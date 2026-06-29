from __future__ import annotations

import re
import time
import json
import hashlib
import discord
from redbot.core import commands
from discord.ext import tasks

from .shared_mattis import (
    embed,
    ok_embed,
    error_embed,
    get_core_config,
    request_json,
    fmt_payload,
    require_admin,
    trim,
    norm,
)



class PagedEmbedView(discord.ui.View):
    def __init__(self, ctx, *, title: str, pages: list[str], color: discord.Color | None = None, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.title = title
        self.pages = pages or ["Nothing found."]
        self.color = color
        self.index = 0
        self.sync_buttons()

    def sync_buttons(self):
        self.previous_page.disabled = self.index <= 0
        self.next_page.disabled = self.index >= len(self.pages) - 1

    def current_embed(self) -> discord.Embed:
        e = embed(self.title, self.pages[self.index], color=self.color or discord.Color.from_rgb(28, 45, 74))
        e.set_footer(text=f"Mattis CMS | Systems • Page {self.index + 1}/{len(self.pages)}")
        return e

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these buttons.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self.sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self.sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


class MattisCore(commands.Cog):
    """Core configuration for Mattis CMS | Systems."""

    def __init__(self, bot):
        self.bot = bot
        if not self.alert_loop.is_running():
            self.alert_loop.start()
        if not self.log_loop.is_running():
            self.log_loop.start()

    def cog_unload(self):
        self.alert_loop.cancel()
        self.log_loop.cancel()

    def build_pages(self, lines: list[str], *, empty: str = "Nothing found.", max_chars: int = 3200) -> list[str]:
        if not lines:
            return [empty]

        pages: list[str] = []
        current: list[str] = []
        current_len = 0

        for raw_line in lines:
            line = str(raw_line)

            if len(line) > max_chars:
                if current:
                    pages.append("\n".join(current))
                    current = []
                    current_len = 0

                for i in range(0, len(line), max_chars):
                    pages.append(line[i:i + max_chars])

                continue

            extra = len(line) + 1

            if current and current_len + extra > max_chars:
                pages.append("\n".join(current))
                current = [line]
                current_len = extra
            else:
                current.append(line)
                current_len += extra

        if current:
            pages.append("\n".join(current))

        return pages or [empty]

    async def send_paginated(
        self,
        ctx,
        title: str,
        lines: list[str],
        *,
        empty: str = "Nothing found.",
        color: discord.Color | None = None,
    ):
        pages = self.build_pages(lines, empty=empty)
        view = PagedEmbedView(ctx, title=title, pages=pages, color=color)
        await ctx.send(embed=view.current_embed(), view=view if len(pages) > 1 else None)


    def is_separator_role(self, role: discord.Role) -> bool:
        name = role.name
        dash_count = sum(1 for ch in name if ch in "-–—")
        cleaned = self.clean_separator_name(name)
        return dash_count >= 6 and len(cleaned) >= 3

    def clean_separator_name(self, name: str) -> str:
        cleaned = re.sub(r"[-–—_]+", " ", name)
        cleaned = re.sub(r"[^\w &/+]+", " ", cleaned)
        cleaned = " ".join(cleaned.split())
        return cleaned.strip()

    def parse_role_sections(self, guild: discord.Guild) -> dict[str, list[int]]:
        sections: dict[str, list[int]] = {}
        current_section: str | None = None

        roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)

        for role in roles:
            if role == guild.default_role:
                continue

            if self.is_separator_role(role):
                current_section = self.clean_separator_name(role.name)
                sections.setdefault(current_section, [])
                continue

            if current_section is None:
                continue

            if role.managed:
                continue

            sections.setdefault(current_section, [])

            if role.id not in sections[current_section]:
                sections[current_section].append(role.id)

        return sections

    def role_mentions(self, guild: discord.Guild, ids: list[int]) -> str:
        lines = []

        for rid in ids:
            role = guild.get_role(rid)
            lines.append(role.mention if role else f"`missing:{rid}`")

        return "\n".join(lines) or "None"

    def section_summary(self, guild: discord.Guild, sections: dict[str, list[int]], *, limit: int = 12) -> str:
        lines = []

        for name, ids in sections.items():
            lines.append(f"**{name}** — `{len(ids)}` roles")
            for rid in ids[:limit]:
                role = guild.get_role(rid)
                lines.append(f"• {role.mention if role else f'`missing:{rid}`'}")
            lines.append("")

        return trim("\n".join(lines).strip() or "No role groups found.", 3900)

    def channel_label(self, channel: discord.abc.GuildChannel) -> str:
        category = getattr(channel, "category", None)
        category_name = category.name if category else "No category"
        synced = getattr(channel, "permissions_synced", None)

        if synced is True:
            sync_text = "synced"
        elif synced is False:
            sync_text = "not synced"
        else:
            sync_text = "n/a"

        mention = channel.mention if hasattr(channel, "mention") else channel.name
        return f"{mention} · `{category_name}` · {sync_text}"

    def bot_perm_line(self, channel: discord.abc.GuildChannel) -> str:
        me = channel.guild.me
        perms = channel.permissions_for(me)

        checks = [
            ("View", perms.view_channel),
            ("Send", getattr(perms, "send_messages", False)),
            ("Embed", getattr(perms, "embed_links", False)),
            ("History", getattr(perms, "read_message_history", False)),
            ("Manage", getattr(perms, "manage_channels", False)),
        ]

        return " · ".join(f"{'✅' if ok else '❌'} {name}" for name, ok in checks)

    def role_risk_line(self, role: discord.Role, guild: discord.Guild) -> str:
        flags = []

        if role == guild.default_role:
            flags.append("@everyone")

        if role.managed:
            flags.append("managed")

        if role.permissions.administrator:
            flags.append("administrator")

        if role.permissions.manage_guild:
            flags.append("manage server")

        if role.permissions.manage_roles:
            flags.append("manage roles")

        if guild.me and role >= guild.me.top_role:
            flags.append("above/equal bot")

        suffix = f" · {', '.join(flags)}" if flags else ""

        return f"{role.mention} · pos `{role.position}`{suffix}"


    def route_slug(self, value: str) -> str:
        """Turn Discord category/channel names into safe route keys."""
        text = str(value or "").lower()
        text = re.sub(r"[^a-z0-9]+", "_", text)
        text = "_".join(part for part in text.split("_") if part)
        return text.strip("_") or "unnamed"

    def exact_route_key(self, channel: discord.TextChannel) -> str:
        category = channel.category.name if channel.category else "uncategorised"
        category_slug = self.route_slug(category)
        channel_slug = self.route_slug(channel.name)
        return f"{category_slug}_{channel_slug}"

    def route_aliases_for(self, category_slug: str, channel_slug: str, exact_key: str) -> list[str]:
        """Optional short aliases. These are only saved if you apply with aliases."""
        aliases: list[str] = []

        def add(value: str):
            value = self.route_slug(value)
            if value and value != exact_key and value not in aliases:
                aliases.append(value)

        # Category-specific aliases.
        if category_slug == "billing_support":
            add(f"billing_{channel_slug}")
            add(channel_slug)

        if category_slug == "tech_support":
            add(f"tech_{channel_slug}")
            add(f"support_{channel_slug}")

        if category_slug == "security_support":
            add(f"security_{channel_slug}")
            add(channel_slug)

        if category_slug == "support_hub":
            add(f"support_{channel_slug}")
            add(channel_slug)

        if category_slug == "development":
            add(f"dev_{channel_slug}")
            add(channel_slug)

        if category_slug == "release_engine":
            add(f"release_{channel_slug}")
            add(channel_slug)

        if category_slug == "observatory_logs":
            add(channel_slug)
            if channel_slug.endswith("_log"):
                add(channel_slug[:-4])

        if category_slug == "operations":
            add(f"operations_{channel_slug}")
            add(channel_slug)

        if category_slug == "management":
            add(f"management_{channel_slug}")

        if category_slug == "company_hub":
            add(channel_slug)

        return aliases

    def route_perms(self, channel: discord.TextChannel) -> tuple[bool, list[str]]:
        perms = channel.permissions_for(channel.guild.me)
        missing = []

        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.embed_links:
            missing.append("Embed Links")
        if not perms.read_message_history:
            missing.append("Read Message History")

        return len(missing) == 0, missing

    def route_meta(self, channel: discord.TextChannel) -> dict:
        category = channel.category.name if channel.category else "Uncategorised"
        category_slug = self.route_slug(category)
        channel_slug = self.route_slug(channel.name)
        exact_key = f"{category_slug}_{channel_slug}"
        ok, missing = self.route_perms(channel)

        return {
            "key": exact_key,
            "category": category,
            "category_slug": category_slug,
            "channel_slug": channel_slug,
            "channel": channel,
            "channel_id": channel.id,
            "ok": ok,
            "missing": missing,
            "aliases": self.route_aliases_for(category_slug, channel_slug, exact_key),
        }

    def build_exact_routes(self, guild: discord.Guild) -> tuple[dict[str, dict], dict[str, list[dict]]]:
        routes: dict[str, dict] = {}
        duplicates: dict[str, list[dict]] = {}

        for channel in sorted(guild.text_channels, key=lambda c: (c.category.position if c.category else 999, c.position)):
            meta = self.route_meta(channel)
            key = meta["key"]

            if key in routes:
                duplicates.setdefault(key, [routes[key]])
                duplicates[key].append(meta)
                continue

            routes[key] = meta

        return routes, duplicates

    def route_preview_line(self, meta: dict, *, show_aliases: bool = False) -> str:
        channel = meta["channel"]
        status = "✅ usable" if meta["ok"] else f"⚠️ missing {', '.join(meta['missing'])}"
        base = f"`{meta['key']}` → {channel.mention} · `{meta['category']}`\\n{status}"

        if show_aliases and meta["aliases"]:
            base += f"\\nAliases: `{', '.join(meta['aliases'][:8])}`"

        return base

    async def backup_routes(self, guild: discord.Guild, reason: str):
        cfg = await get_core_config(self.bot)
        current = await cfg.guild(guild).systems_channels()
        current = current or {}

        backups = await cfg.guild(guild).route_backups()
        backups = backups or []

        backups.append({
            "created_at": int(time.time()),
            "reason": reason,
            "routes": current,
        })

        # Keep the last 10 backups only.
        backups = backups[-10:]

        await cfg.guild(guild).route_backups.set(backups)

    async def saved_routes(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)
        routes = await cfg.guild(guild).systems_channels()
        return routes or {}

    async def saved_sections(self, guild: discord.Guild) -> dict[str, list[int]]:
        cfg = await get_core_config(self.bot)
        sections = await cfg.guild(guild).role_sections()
        return sections or {}

    async def save_sections(self, guild: discord.Guild, sections: dict[str, list[int]]):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).role_sections.set(sections)

        # Compatibility with the older gates.
        staff_ids = []
        admin_ids = []

        for name, ids in sections.items():
            n = norm(name)
            if "support" in n or "moderation" in n or "development" in n or "staff" in n:
                staff_ids.extend(ids)
            if "administration" in n:
                admin_ids.extend(ids)

        await cfg.guild(guild).staff_roles.set(list(dict.fromkeys(staff_ids)))
        await cfg.guild(guild).admin_roles.set(list(dict.fromkeys(admin_ids)))

    @commands.group(name="mcore", invoke_without_command=True)
    async def mcore(self, ctx):
        """Configure the Mattis API bridge."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        api_url = await cfg.api_url()
        channels = await cfg.guild(ctx.guild).systems_channels() if ctx.guild else {}
        sections = await self.saved_sections(ctx.guild) if ctx.guild else {}

        e = embed("Mattis Core Config")
        e.add_field(name="API URL", value=api_url or "Not set", inline=False)
        e.add_field(name="API token", value="Set" if await cfg.api_token() else "Not set", inline=True)
        e.add_field(name="Systems channels", value=str(len(channels or {})), inline=True)
        e.add_field(name="Role groups", value=str(len(sections or {})), inline=True)

        await ctx.send(embed=e)

    @mcore.command(name="apiurl")
    @commands.is_owner()
    async def apiurl(self, ctx, url: str):
        cfg = await get_core_config(self.bot)
        await cfg.api_url.set(url.rstrip("/"))
        await ctx.send(embed=ok_embed("API URL saved", url.rstrip("/")))

    @mcore.command(name="token")
    @commands.is_owner()
    async def token(self, ctx, *, token: str):
        cfg = await get_core_config(self.bot)
        await cfg.api_token.set(token.strip())

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        await ctx.send(embed=ok_embed("Token saved", "Private token saved. Delete any visible token messages."), delete_after=10)

    @mcore.command(name="cleartoken")
    @commands.is_owner()
    async def cleartoken(self, ctx):
        cfg = await get_core_config(self.bot)
        await cfg.api_token.set("")
        await ctx.send(embed=ok_embed("Token cleared"))

    @mcore.command(name="maprole")
    async def maprole(self, ctx, role: discord.Role, *, group: str):
        """Manually add a role to a saved role group. Usage: !mcore maprole @Role Support Team"""
        if not await require_admin(ctx):
            return

        if role == ctx.guild.default_role:
            await ctx.send(embed=error_embed("Unsafe role", "I will not map @everyone."))
            return

        if role.managed:
            await ctx.send(embed=error_embed("Unsafe role", "I will not map bot/integration-managed roles."))
            return

        group = " ".join(group.split()).strip()
        sections = await self.saved_sections(ctx.guild)
        sections.setdefault(group, [])

        if role.id not in sections[group]:
            sections[group].append(role.id)

        await self.save_sections(ctx.guild, sections)
        await ctx.send(embed=ok_embed("Role mapped", f"{role.mention} added to **{group}**."))

    @mcore.command(name="unmaprole")
    async def unmaprole(self, ctx, role: discord.Role, *, group: str):
        """Remove a role from a saved role group. Usage: !mcore unmaprole @Role Support Team"""
        if not await require_admin(ctx):
            return

        group = " ".join(group.split()).strip()
        sections = await self.saved_sections(ctx.guild)

        if group in sections:
            sections[group] = [rid for rid in sections[group] if rid != role.id]

        await self.save_sections(ctx.guild, sections)
        await ctx.send(embed=ok_embed("Role unmapped", f"{role.mention} removed from **{group}**."))

    @mcore.command(name="groups")
    async def groups(self, ctx):
        """Show saved role groups."""
        if not await require_admin(ctx):
            return

        sections = await self.saved_sections(ctx.guild)
        await ctx.send(embed=embed("Saved Role Groups", self.section_summary(ctx.guild, sections)))

    @mcore.command(name="group")
    async def group(self, ctx, *, group: str):
        """Show one saved role group."""
        if not await require_admin(ctx):
            return

        sections = await self.saved_sections(ctx.guild)
        wanted = norm(group)

        for name, ids in sections.items():
            if norm(name) == wanted or wanted in norm(name):
                e = embed(f"Role Group: {name}", self.role_mentions(ctx.guild, ids))
                e.add_field(name="Role count", value=str(len(ids)), inline=True)
                await ctx.send(embed=e)
                return

        await ctx.send(embed=error_embed("Role group not found", group))

    @mcore.command(name="staffrole")
    async def staffrole(self, ctx, role: discord.Role):
        """Compatibility shortcut."""
        await self.maprole(ctx, role, group="Staff")

    @mcore.command(name="adminrole")
    async def adminrole(self, ctx, role: discord.Role):
        """Compatibility shortcut."""
        await self.maprole(ctx, role, group="Administration Team")

    @mcore.command(name="mapchannel", aliases=["systemchannel"])
    async def mapchannel(self, ctx, key: str, channel: discord.TextChannel):
        """Map a Mattis Systems channel."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()
        channels[key.lower().strip()] = channel.id
        await cfg.guild(ctx.guild).systems_channels.set(channels)

        await ctx.send(embed=ok_embed("Systems channel mapped", f"`{key.lower().strip()}` → {channel.mention}"))

    @mcore.command(name="unmapchannel")
    async def unmapchannel(self, ctx, key: str):
        """Remove a Mattis Systems channel mapping."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()
        channels.pop(key.lower().strip(), None)
        await cfg.guild(ctx.guild).systems_channels.set(channels)

        await ctx.send(embed=ok_embed("Systems channel unmapped", f"`{key}` removed."))

    @mcore.command(name="channels")
    async def channels(self, ctx):
        """Show mapped Mattis Systems channels."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()

        if not channels:
            await ctx.send(embed=embed("Systems channels", "No systems channels mapped yet."))
            return

        lines = []
        for key, cid in channels.items():
            ch = ctx.guild.get_channel(cid)
            lines.append(f"`{key}` → {ch.mention if ch else f'missing:{cid}'}")

        await ctx.send(embed=embed("Systems channels", "\n".join(lines)))

    @mcore.command(name="mappings")
    async def mappings(self, ctx):
        """Show role and channel mappings."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()
        sections = await self.saved_sections(ctx.guild)

        e = embed("Mattis Systems Mappings")
        e.add_field(name="Role groups", value=self.section_summary(ctx.guild, sections, limit=5), inline=False)

        channel_lines = []
        for key, cid in channels.items():
            ch = ctx.guild.get_channel(cid)
            channel_lines.append(f"`{key}` → {ch.mention if ch else f'missing:{cid}'}")

        e.add_field(name="Channels", value="\n".join(channel_lines) if channel_lines else "None configured", inline=False)

        await ctx.send(embed=e)

    @mcore.command(name="permissions")
    async def permissions(self, ctx):
        """Show Mattis Systems permission mappings."""
        if not await require_admin(ctx):
            return
        await self.mappings(ctx)

    @mcore.group(name="scan", invoke_without_command=True)
    async def scan(self, ctx):
        """Read-only Discord server scan."""
        if not await require_admin(ctx):
            return

        e = embed("Discord Discovery Scan")
        e.add_field(name="Roles", value=str(len(ctx.guild.roles)), inline=True)
        e.add_field(name="Categories", value=str(len(ctx.guild.categories)), inline=True)
        e.add_field(name="Text channels", value=str(len(ctx.guild.text_channels)), inline=True)
        e.add_field(name="Safety", value="Read-only scan. No roles, channels, or permissions are edited.", inline=False)
        e.add_field(name="Next", value="Run `!mcore scan rolegroups`, `!mcore scan channels`, `!mcore scan permissions`, then `!mcore importgroups preview`.", inline=False)

        await ctx.send(embed=e)

    @scan.command(name="roles")
    async def scan_roles(self, ctx):
        """Scan all visible roles with pages."""
        if not await require_admin(ctx):
            return

        roles = sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True)
        lines = []

        for role in roles:
            if role == ctx.guild.default_role:
                continue

            if self.is_separator_role(role):
                lines.append(f"**GROUP HEADER:** `{self.clean_separator_name(role.name)}`")
            else:
                lines.append(self.role_risk_line(role, ctx.guild))

        await self.send_paginated(ctx, "Role Scan", lines, empty="No roles found.")
    @scan.command(name="rolegroups")
    async def scan_rolegroups(self, ctx):
        """Scan dashed role headers and child roles."""
        if not await require_admin(ctx):
            return

        sections = self.parse_role_sections(ctx.guild)
        await ctx.send(embed=embed("Detected Role Groups", self.section_summary(ctx.guild, sections)))

    @scan.command(name="categories")
    async def scan_categories(self, ctx):
        """Scan all categories and bot permissions with pages."""
        if not await require_admin(ctx):
            return

        lines = []

        for category in sorted(ctx.guild.categories, key=lambda c: c.position):
            perms = self.bot_perm_line(category)
            lines.append(f"**{category.name}** · pos `{category.position}` · channels `{len(category.channels)}`\n{perms}")

        await self.send_paginated(ctx, "Category Scan", lines, empty="No categories found.")
    @scan.command(name="channels")
    async def scan_channels(self, ctx):
        """Scan all text channels, categories, sync state, and bot permissions with pages."""
        if not await require_admin(ctx):
            return

        lines = []

        for channel in sorted(ctx.guild.text_channels, key=lambda c: (c.category.position if c.category else 999, c.position)):
            lines.append(f"{self.channel_label(channel)}\n{self.bot_perm_line(channel)}")

        await self.send_paginated(ctx, "Channel Scan", lines, empty="No text channels found.")
    @scan.command(name="permissions")
    async def scan_permissions(self, ctx, mode: str = "issues"):
        """Scan channel permissions. Use: !mcore scan permissions all"""
        if not await require_admin(ctx):
            return

        mode = mode.lower().strip()
        show_all = mode in ["all", "full", "everything", "list"]

        issue_lines = []
        all_lines = []

        for channel in sorted(ctx.guild.text_channels, key=lambda c: (c.category.position if c.category else 999, c.position)):
            perms = channel.permissions_for(ctx.guild.me)
            missing = []

            if not perms.view_channel:
                missing.append("View Channel")
            if not perms.send_messages:
                missing.append("Send Messages")
            if not perms.embed_links:
                missing.append("Embed Links")
            if not perms.read_message_history:
                missing.append("Read Message History")

            category = channel.category.name if channel.category else "No category"
            status = "✅ OK" if not missing else f"⚠️ Missing: {', '.join(missing)}"

            line = f"{channel.mention} · `{category}`\\n{self.bot_perm_line(channel)}\\n{status}"

            all_lines.append(line)

            if missing:
                issue_lines.append(line)

        if show_all:
            await self.send_paginated(
                ctx,
                "Full Permission Scan",
                all_lines,
                empty="No text channels found.",
            )
            return

        if not issue_lines:
            await ctx.send(embed=ok_embed(
                "Permission Scan",
                "No obvious channel permission issues found.\\n\\nUse `!mcore scan permissions all` to view every channel.",
            ))
            return

        await self.send_paginated(
            ctx,
            "Permission Issues",
            issue_lines,
            empty="No permission issues found.",
            color=discord.Color.gold(),
        )

    @mcore.group(name="importgroups", invoke_without_command=True)
    async def importgroups(self, ctx):
        """Preview/apply dashed role group import."""
        if not await require_admin(ctx):
            return

        await ctx.send(embed=embed("Import Role Groups", "Use `!mcore importgroups preview` first, then `!mcore importgroups apply`."))

    @importgroups.command(name="preview")
    async def importgroups_preview(self, ctx):
        """Preview detected role groups without saving."""
        if not await require_admin(ctx):
            return

        sections = self.parse_role_sections(ctx.guild)
        e = embed("Role Group Import Preview", self.section_summary(ctx.guild, sections))
        e.add_field(name="Safety", value="Nothing has been saved yet. Run `!mcore importgroups apply` to save these role IDs.", inline=False)

        await ctx.send(embed=e)

    @importgroups.command(name="apply")
    async def importgroups_apply(self, ctx):
        """Save detected role groups by role ID."""
        if not await require_admin(ctx):
            return

        sections = self.parse_role_sections(ctx.guild)

        if not sections:
            await ctx.send(embed=error_embed("No role groups found", "No dashed role headers were detected."))
            return

        await self.save_sections(ctx.guild, sections)

        e = ok_embed("Role groups imported", f"Saved `{len(sections)}` role groups by role ID.")
        e.add_field(name="Saved groups", value="\n".join(f"• {name} — {len(ids)} roles" for name, ids in sections.items()), inline=False)

        await ctx.send(embed=e)


    @scan.command(name="routing", aliases=["routes"])
    async def scan_routing(self, ctx, mode: str = "exact"):
        """Preview automatic channel routing. Use: !mcore scan routing aliases"""
        if not await require_admin(ctx):
            return

        mode = mode.lower().strip()
        show_aliases = mode in ["alias", "aliases", "all", "full"]

        routes, duplicates = self.build_exact_routes(ctx.guild)

        lines = []
        current_category = None

        for key, meta in routes.items():
            if meta["category"] != current_category:
                current_category = meta["category"]
                lines.append(f"\\n**{current_category}**")

            lines.append(self.route_preview_line(meta, show_aliases=show_aliases))

        if duplicates:
            lines.append("\\n**Duplicate exact route keys detected**")
            for key, metas in duplicates.items():
                lines.append(f"`{key}` has `{len(metas)}` possible channels:")
                for meta in metas:
                    lines.append(f"• {meta['channel'].mention} · `{meta['category']}`")

        await self.send_paginated(
            ctx,
            "Automatic Route Scan",
            lines,
            empty="No text channels found.",
        )

    @mcore.group(name="autoroute", invoke_without_command=True)
    async def autoroute(self, ctx):
        """Safe automatic route preview/apply tools."""
        if not await require_admin(ctx):
            return

        await ctx.send(embed=embed(
            "Safe Automatic Routing",
            "This only reads Discord and saves channel IDs into bot config.\\n\\n"
            "**Safe flow:**\\n"
            "`!mcore autoroute preview`\\n"
            "`!mcore autoroute apply`\\n\\n"
            "**Optional:**\\n"
            "`!mcore autoroute preview aliases`\\n"
            "`!mcore autoroute apply aliases`\\n"
            "`!mcore autoroute apply overwrite`\\n"
            "`!mcore autoroute restore latest`"
        ))

    @autoroute.command(name="preview")
    async def autoroute_preview(self, ctx, mode: str = "exact"):
        """Preview routes without saving anything."""
        if not await require_admin(ctx):
            return

        mode = mode.lower().strip()
        show_aliases = mode in ["alias", "aliases", "all", "full"]

        routes, duplicates = self.build_exact_routes(ctx.guild)
        saved = await self.saved_routes(ctx.guild)

        lines = [
            f"Detected exact routes: `{len(routes)}`",
            f"Already saved routes: `{len(saved)}`",
            f"Aliases shown: `{'yes' if show_aliases else 'no'}`",
            "",
            "**Nothing has been saved yet.**",
            "",
        ]

        current_category = None

        for key, meta in routes.items():
            if meta["category"] != current_category:
                current_category = meta["category"]
                lines.append(f"\\n**{current_category}**")

            existing = saved.get(key)
            saved_state = "new"

            if existing == meta["channel_id"]:
                saved_state = "already saved"
            elif existing:
                saved_state = "would conflict"

            lines.append(f"{self.route_preview_line(meta, show_aliases=show_aliases)}\\nState: `{saved_state}`")

        if duplicates:
            lines.append("\\n**Duplicate exact route keys detected**")
            for key, metas in duplicates.items():
                lines.append(f"`{key}` has `{len(metas)}` possible channels:")
                for meta in metas:
                    lines.append(f"• {meta['channel'].mention} · `{meta['category']}`")

        lines.append("\\n**Apply commands**")
        lines.append("`!mcore autoroute apply` = save missing exact routes only")
        lines.append("`!mcore autoroute apply overwrite` = replace existing exact routes")
        lines.append("`!mcore autoroute apply aliases` = save exact routes plus shortcut aliases")

        await self.send_paginated(
            ctx,
            "Auto-route Preview",
            lines,
            empty="No routes detected.",
        )

    @autoroute.command(name="apply")
    async def autoroute_apply(self, ctx, *options: str):
        """Save detected routes. Safe default: merge exact routes only."""
        if not await require_admin(ctx):
            return

        opts = {self.route_slug(o) for o in options}
        overwrite = "overwrite" in opts or "replace" in opts
        include_aliases = "aliases" in opts or "alias" in opts
        mode = "overwrite" if overwrite else "merge"

        routes, duplicates = self.build_exact_routes(ctx.guild)

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()
        channels = channels or {}

        await self.backup_routes(ctx.guild, f"before autoroute apply mode={mode} aliases={include_aliases}")

        saved = []
        skipped = []
        conflicts = []

        def save_key(key: str, channel_id: int, mention: str):
            existing = channels.get(key)

            if existing and existing != channel_id and not overwrite:
                conflicts.append(f"`{key}` already points elsewhere, skipped. Use overwrite if correct.")
                return

            if existing == channel_id:
                skipped.append(f"`{key}` already saved.")
                return

            channels[key] = channel_id
            saved.append(f"`{key}` → {mention}")

        for key, meta in routes.items():
            save_key(key, meta["channel_id"], meta["channel"].mention)

            if include_aliases:
                for alias in meta["aliases"]:
                    save_key(alias, meta["channel_id"], meta["channel"].mention)

        await cfg.guild(ctx.guild).systems_channels.set(channels)

        lines = [
            f"Mode: `{mode}`",
            f"Aliases saved: `{'yes' if include_aliases else 'no'}`",
            f"Saved/updated: `{len(saved)}`",
            f"Already existed/skipped: `{len(skipped)}`",
            f"Conflicts skipped: `{len(conflicts)}`",
            "",
            "A backup was created before saving.",
            "",
        ]

        if saved:
            lines.append("**Saved routes**")
            lines.extend(saved)

        if conflicts:
            lines.append("\\n**Conflicts skipped**")
            lines.extend(conflicts)

        if duplicates:
            lines.append("\\n**Duplicate exact keys noticed**")
            for key, metas in duplicates.items():
                lines.append(f"`{key}` had `{len(metas)}` candidates. First route was used.")

        await self.send_paginated(
            ctx,
            "Auto-route Applied",
            lines,
            empty="No route changes made.",
            color=discord.Color.green(),
        )

    @autoroute.command(name="backups")
    async def autoroute_backups(self, ctx):
        """Show route backups."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        backups = await cfg.guild(ctx.guild).route_backups()
        backups = backups or []

        if not backups:
            await ctx.send(embed=embed("Route Backups", "No route backups found."))
            return

        lines = []

        for i, backup in enumerate(reversed(backups), start=1):
            created = int(backup.get("created_at", 0))
            reason = backup.get("reason", "No reason")
            count = len(backup.get("routes", {}) or {})
            lines.append(f"`{i}` · routes `{count}` · <t:{created}:R>\\n{reason}")

        await self.send_paginated(ctx, "Route Backups", lines)

    @autoroute.command(name="restore")
    async def autoroute_restore(self, ctx, which: str = "latest"):
        """Restore the latest route backup."""
        if not await require_admin(ctx):
            return

        if which.lower().strip() != "latest":
            await ctx.send(embed=error_embed("Unsupported restore", "Use `!mcore autoroute restore latest`."))
            return

        cfg = await get_core_config(self.bot)
        backups = await cfg.guild(ctx.guild).route_backups()
        backups = backups or []

        if not backups:
            await ctx.send(embed=error_embed("No backups", "No route backups found."))
            return

        latest = backups[-1]
        routes = latest.get("routes", {}) or {}

        await self.backup_routes(ctx.guild, "before restoring latest backup")
        await cfg.guild(ctx.guild).systems_channels.set(routes)

        await ctx.send(embed=ok_embed(
            "Routes restored",
            f"Restored latest backup with `{len(routes)}` routes."
        ))

    @mcore.command(name="routes")
    async def routes(self, ctx, *, query: str = ""):
        """Show saved routes. Optional: !mcore routes billing"""
        if not await require_admin(ctx):
            return

        routes = await self.saved_routes(ctx.guild)
        query_slug = self.route_slug(query) if query else ""

        lines = []

        for key in sorted(routes.keys()):
            if query_slug and query_slug not in self.route_slug(key):
                continue

            cid = routes[key]
            ch = ctx.guild.get_channel(cid)
            lines.append(f"`{key}` → {ch.mention if ch else f'missing:{cid}'}")

        await self.send_paginated(
            ctx,
            "Saved Channel Routes",
            lines,
            empty="No saved routes matched.",
        )

    @mcore.command(name="route")
    async def route(self, ctx, key: str, channel: discord.TextChannel):
        """Manually save one route. Example: !mcore route billing_invoices #invoices"""
        if not await require_admin(ctx):
            return

        key = self.route_slug(key)

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()
        channels = channels or {}

        await self.backup_routes(ctx.guild, f"before manual route {key}")
        channels[key] = channel.id

        await cfg.guild(ctx.guild).systems_channels.set(channels)

        await ctx.send(embed=ok_embed("Route saved", f"`{key}` → {channel.mention}"))

    @mcore.command(name="unroute")
    async def unroute(self, ctx, key: str):
        """Remove one saved route key."""
        if not await require_admin(ctx):
            return

        key = self.route_slug(key)

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()
        channels = channels or {}

        if key not in channels:
            await ctx.send(embed=error_embed("Route not found", f"`{key}` is not saved."))
            return

        await self.backup_routes(ctx.guild, f"before unroute {key}")
        channels.pop(key, None)
        await cfg.guild(ctx.guild).systems_channels.set(channels)

        await ctx.send(embed=ok_embed("Route removed", f"`{key}` removed."))


    @mcore.command(name="routeinfo")
    async def routeinfo(self, ctx, *, key: str):
        """Show where a saved route points."""
        if not await require_admin(ctx):
            return

        clean_key = self.route_slug(key)
        routes = await self.saved_routes(ctx.guild)
        channel_id = routes.get(clean_key)

        if not channel_id:
            await ctx.send(embed=error_embed(
                "Route not found",
                f"`{clean_key}` is not saved.\n\nTry `!mcore routes {clean_key}` or `!mcore routes`."
            ))
            return

        channel = ctx.guild.get_channel(channel_id)

        if not channel:
            await ctx.send(embed=error_embed(
                "Route channel missing",
                f"`{clean_key}` points to missing channel ID `{channel_id}`."
            ))
            return

        category = channel.category.name if channel.category else "No category"
        exact_key = self.exact_route_key(channel) if isinstance(channel, discord.TextChannel) else "not_text_channel"
        is_exact = clean_key == exact_key

        ok, missing = self.route_perms(channel) if isinstance(channel, discord.TextChannel) else (False, ["Not a text channel"])

        e = embed("Route Info")
        e.add_field(name="Route key", value=f"`{clean_key}`", inline=False)
        e.add_field(name="Channel", value=channel.mention, inline=True)
        e.add_field(name="Category", value=f"`{category}`", inline=True)
        e.add_field(name="Exact category route", value="✅ yes" if is_exact else f"ℹ️ no\nExact would be `{exact_key}`", inline=False)
        e.add_field(name="Bot permissions", value="✅ usable" if ok else f"⚠️ Missing: {', '.join(missing)}", inline=False)

        await ctx.send(embed=e)

    @mcore.command(name="routetest", aliases=["testroute", "routeping"])
    async def routetest(self, ctx, key: str, *, message: str = "Mattis Systems route test."):
        """Send a test embed to a saved route."""
        if not await require_admin(ctx):
            return

        clean_key = self.route_slug(key)
        routes = await self.saved_routes(ctx.guild)
        channel_id = routes.get(clean_key)

        if not channel_id:
            await ctx.send(embed=error_embed(
                "Route not found",
                f"`{clean_key}` is not saved.\n\nUse `!mcore routes {clean_key}` to search saved routes."
            ))
            return

        channel = ctx.guild.get_channel(channel_id)

        if not channel or not isinstance(channel, discord.TextChannel):
            await ctx.send(embed=error_embed(
                "Route channel invalid",
                f"`{clean_key}` points to a missing or non-text channel."
            ))
            return

        ok, missing = self.route_perms(channel)

        if not ok:
            await ctx.send(embed=error_embed(
                "Route permission issue",
                f"I cannot safely send to {channel.mention}.\nMissing: {', '.join(missing)}"
            ))
            return

        test = embed("Mattis Systems Route Test", message, color=discord.Color.green())
        test.add_field(name="Route key", value=f"`{clean_key}`", inline=True)
        test.add_field(name="Sent from", value=ctx.channel.mention, inline=True)
        test.add_field(name="Target", value=channel.mention, inline=True)

        await channel.send(embed=test)
        await ctx.send(embed=ok_embed("Route test sent", f"`{clean_key}` → {channel.mention}"))

    @mcore.command(name="routetestmany")
    async def routetestmany(self, ctx, *keys: str):
        """Test multiple saved routes at once. Example: !mcore routetestmany billing_support_invoices development_deployments"""
        if not await require_admin(ctx):
            return

        if not keys:
            await ctx.send(embed=error_embed(
                "No routes provided",
                "Example: `!mcore routetestmany billing_support_invoices development_deployments`"
            ))
            return

        routes = await self.saved_routes(ctx.guild)
        results = []

        for raw_key in keys[:10]:
            clean_key = self.route_slug(raw_key)
            channel_id = routes.get(clean_key)

            if not channel_id:
                results.append(f"❌ `{clean_key}` not found")
                continue

            channel = ctx.guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                results.append(f"❌ `{clean_key}` invalid/missing channel")
                continue

            ok, missing = self.route_perms(channel)

            if not ok:
                results.append(f"⚠️ `{clean_key}` → {channel.mention} missing {', '.join(missing)}")
                continue

            test = embed("Mattis Systems Route Test", f"Bulk route test for `{clean_key}`.", color=discord.Color.green())
            test.add_field(name="Route key", value=f"`{clean_key}`", inline=True)
            test.add_field(name="Sent from", value=ctx.channel.mention, inline=True)

            await channel.send(embed=test)
            results.append(f"✅ `{clean_key}` → {channel.mention}")

        await self.send_paginated(
            ctx,
            "Bulk Route Test Results",
            results,
            empty="No route tests ran.",
            color=discord.Color.green(),
        )



    def dispatch_routes_for(self, purpose: str) -> list[str]:
        """Purpose/event name -> ordered route keys."""
        p = self.route_slug(purpose)

        route_map = {
            # Billing
            "billing": ["billing_support_billing_help", "billing_support_invoices", "support"],
            "billing_help": ["billing_support_billing_help", "billing_support_invoices", "support"],
            "invoice": ["billing_support_invoices", "billing_support_billing_help", "support"],
            "invoices": ["billing_support_invoices", "billing_support_billing_help", "support"],
            "payment": ["billing_support_payments", "billing_support_billing_help", "support"],
            "payments": ["billing_support_payments", "billing_support_billing_help", "support"],
            "refund": ["billing_support_refunds", "billing_support_billing_help", "support"],
            "refunds": ["billing_support_refunds", "billing_support_billing_help", "support"],
            "chargeback": ["billing_support_chargebacks", "billing_support_billing_help", "support"],
            "chargebacks": ["billing_support_chargebacks", "billing_support_billing_help", "support"],

            # Support
            "support": ["support_hub_support_center", "support_hub_create_ticket", "support"],
            "ticket": ["support_hub_create_ticket", "support_hub_support_center", "support"],
            "tickets": ["support_hub_create_ticket", "support_hub_support_center", "support"],
            "customer_support": ["support_hub_support_center", "customers_priority_support", "support"],
            "priority_support": ["customers_priority_support", "support_hub_support_center", "support"],

            # Tech support
            "tech": ["tech_support_tech_support", "support_hub_support_center", "support"],
            "tech_support": ["tech_support_tech_support", "support_hub_support_center", "support"],
            "api_help": ["tech_support_api_help", "mattis_cms_api_reference", "support"],
            "system_error": ["tech_support_system_errors", "observatory_logs_system_log", "support"],
            "system_errors": ["tech_support_system_errors", "observatory_logs_system_log", "support"],
            "installation": ["tech_support_installation_help", "tech_support_tech_support", "support"],
            "troubleshooting": ["tech_support_troubleshooting", "tech_support_tech_support", "support"],

            # Security
            "security": ["security_support_security_help", "observatory_logs_security_log", "support"],
            "security_help": ["security_support_security_help", "observatory_logs_security_log", "support"],
            "exploit": ["security_support_report_exploit", "observatory_logs_security_log", "operations_incidents"],
            "report_exploit": ["security_support_report_exploit", "observatory_logs_security_log", "operations_incidents"],
            "account_compromise": ["security_support_account_compromise", "observatory_logs_security_log", "operations_incidents"],
            "suspicious_activity": ["security_support_suspicious_activity", "observatory_logs_security_log", "operations_incidents"],

            # Bug reporting
            "bug": ["bug_reporting_bug_reports", "mattis_cms_cms_bugs", "development_backend"],
            "bugs": ["bug_reporting_bug_reports", "mattis_cms_cms_bugs", "development_backend"],
            "known_issue": ["bug_reporting_known_issues", "company_hub_status", "development_backend"],
            "known_issues": ["bug_reporting_known_issues", "company_hub_status", "development_backend"],

            # Development / production
            "development": ["development_dev_chat", "development_backend", "development_deployments"],
            "dev": ["development_dev_chat", "development_backend", "development_deployments"],
            "deployment": ["development_deployments", "release_engine_staging", "observatory_logs_system_log"],
            "deployments": ["development_deployments", "release_engine_staging", "observatory_logs_system_log"],
            "backend": ["development_backend", "development_dev_chat", "observatory_logs_api_log"],
            "frontend": ["development_dev_chat", "brand_design_ui_design", "development_backend"],
            "bot": ["development_bot_systems", "observatory_logs_bot_log", "development_dev_chat"],
            "bot_systems": ["development_bot_systems", "observatory_logs_bot_log", "development_dev_chat"],
            "qa": ["development_sprints", "release_engine_release_testing", "labs_beta_testing"],
            "testing": ["release_engine_release_testing", "labs_beta_testing", "development_sprints"],

            # Release engine
            "release": ["release_engine_production_releases", "company_hub_releases", "development_deployments"],
            "releases": ["release_engine_production_releases", "company_hub_releases", "development_deployments"],
            "production_release": ["release_engine_production_releases", "company_hub_releases", "development_deployments"],
            "staging": ["release_engine_staging", "release_engine_release_testing", "development_deployments"],
            "release_notes": ["release_engine_release_notes", "company_hub_changelog", "company_hub_releases"],

            # Incidents / operations
            "incident": ["operations_incidents", "observatory_logs_incident_log", "company_hub_status"],
            "incidents": ["operations_incidents", "observatory_logs_incident_log", "company_hub_status"],
            "moderation": ["operations_moderation", "cms_staff_mod_log_review", "observatory_logs_member_log"],
            "report": ["operations_reports", "operations_investigations", "management_board_room"],
            "reports": ["operations_reports", "operations_investigations", "management_board_room"],
            "investigation": ["operations_investigations", "operations_reports", "management_board_room"],

            # Logs
            "api_log": ["observatory_logs_api_log", "development_backend", "tech_support_api_help"],
            "audit_log": ["observatory_logs_audit_log", "observatory_logs_security_log", "management_analytics"],
            "security_log": ["observatory_logs_security_log", "security_support_security_help"],
            "incident_log": ["observatory_logs_incident_log", "operations_incidents"],
            "ticket_log": ["observatory_logs_ticket_log", "support_hub_support_center"],
            "payment_log": ["observatory_logs_payment_log", "billing_support_payments"],
            "system_log": ["observatory_logs_system_log", "tech_support_system_errors"],
            "bot_log": ["observatory_logs_bot_log", "development_bot_systems"],
            "member_log": ["observatory_logs_member_log", "cms_staff_user_actions"],
            "message_log": ["observatory_logs_message_log", "cms_staff_mod_log_review"],

            # Management / business
            "management": ["management_board_room", "management_strategy", "management_analytics"],
            "strategy": ["management_strategy", "management_board_room"],
            "finance": ["management_finance", "billing_support_payments"],
            "legal": ["management_legal", "arrival_rules_and_legal"],
            "analytics": ["management_analytics", "observatory_logs_analytics_log"],
            "sales": ["sales_business_sales", "sales_business_contact_sales"],
            "enterprise": ["sales_business_enterprise_plans", "sales_business_contact_sales"],
            "pricing": ["sales_business_pricing", "sales_business_sales"],

            # CMS / company info
            "status": ["company_hub_status", "company_hub_maintenance", "observatory_logs_system_log"],
            "maintenance": ["company_hub_maintenance", "company_hub_status"],
            "news": ["company_hub_news", "customers_customer_news"],
            "roadmap": ["company_hub_roadmap", "mattis_cms_cms_features"],
            "documentation": ["mattis_cms_documentation", "mattis_cms_api_reference"],
            "api_reference": ["mattis_cms_api_reference", "tech_support_api_help"],
            "downloads": ["mattis_cms_downloads", "customers_downloads"],
            "cms_bug": ["mattis_cms_cms_bugs", "bug_reporting_bug_reports"],
            "cms_feature": ["mattis_cms_cms_features", "products_services_beta_features"],
        }

        if p in route_map:
            return route_map[p]

        return [p]

    async def resolve_dispatch_route(self, guild: discord.Guild, purpose: str):
        routes = await self.saved_routes(guild)
        candidates = self.dispatch_routes_for(purpose)

        for key in candidates:
            clean_key = self.route_slug(key)
            channel_id = routes.get(clean_key)

            if not channel_id:
                continue

            channel = guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            ok, missing = self.route_perms(channel)

            if ok:
                return clean_key, channel, candidates

        return None, None, candidates

    @mcore.command(name="dispatchmap")
    async def dispatchmap(self, ctx, *, purpose: str = ""):
        """Show dispatch purpose mapping. Example: !mcore dispatchmap invoice"""
        if not await require_admin(ctx):
            return

        if not purpose:
            examples = [
                "`invoice` → billing invoices",
                "`payment` → billing payments",
                "`refund` → billing refunds",
                "`chargeback` → billing chargebacks",
                "`exploit` → security exploit reports",
                "`deployment` → development deployments",
                "`release` → production releases",
                "`incident` → operations incidents",
                "`api_log` → API logs",
                "`system_log` → system logs",
                "`management` → board/management",
            ]
            await ctx.send(embed=embed("Dispatch Map Examples", "\n".join(examples)))
            return

        key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, purpose)

        lines = [f"Purpose: `{self.route_slug(purpose)}`", "", "**Candidate routes:**"]

        saved = await self.saved_routes(ctx.guild)

        for candidate in candidates:
            clean = self.route_slug(candidate)
            channel_id = saved.get(clean)
            ch = ctx.guild.get_channel(channel_id) if channel_id else None
            marker = "✅ selected" if clean == key else "•"
            lines.append(f"{marker} `{clean}` → {ch.mention if ch else 'not saved'}")

        await self.send_paginated(ctx, "Dispatch Route Map", lines)

    @mcore.command(name="dispatch")
    async def dispatch(self, ctx, purpose: str, *, message: str):
        """Send a test dispatch to the correct route by purpose."""
        if not await require_admin(ctx):
            return

        selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, purpose)

        if not channel:
            lines = [
                f"No usable route found for `{self.route_slug(purpose)}`.",
                "",
                "**Tried:**",
            ]
            lines.extend(f"• `{self.route_slug(c)}`" for c in candidates)
            lines.append("")
            lines.append("Use `!mcore routes <keyword>` to check saved routes.")
            await self.send_paginated(ctx, "Dispatch Failed", lines, color=discord.Color.red())
            return

        e = embed("Mattis Systems Dispatch", message, color=discord.Color.green())
        e.add_field(name="Purpose", value=f"`{self.route_slug(purpose)}`", inline=True)
        e.add_field(name="Route", value=f"`{selected_key}`", inline=True)
        e.add_field(name="Triggered from", value=ctx.channel.mention, inline=True)

        await channel.send(embed=e)
        await ctx.send(embed=ok_embed("Dispatch sent", f"`{self.route_slug(purpose)}` → `{selected_key}` → {channel.mention}"))

    @mcore.command(name="dispatchmany")
    async def dispatchmany(self, ctx, *purposes: str):
        """Bulk test dispatch purposes."""
        if not await require_admin(ctx):
            return

        if not purposes:
            await ctx.send(embed=error_embed(
                "No purposes provided",
                "Example: `!mcore dispatchmany invoice payment exploit deployment api_log`"
            ))
            return

        results = []

        for purpose in purposes[:15]:
            selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, purpose)

            if not channel:
                results.append(f"❌ `{self.route_slug(purpose)}` no usable route")
                continue

            e = embed("Mattis Systems Dispatch Test", f"Bulk dispatch test for `{self.route_slug(purpose)}`.", color=discord.Color.green())
            e.add_field(name="Purpose", value=f"`{self.route_slug(purpose)}`", inline=True)
            e.add_field(name="Route", value=f"`{selected_key}`", inline=True)
            e.add_field(name="Triggered from", value=ctx.channel.mention, inline=True)

            await channel.send(embed=e)
            results.append(f"✅ `{self.route_slug(purpose)}` → `{selected_key}` → {channel.mention}")

        await self.send_paginated(
            ctx,
            "Bulk Dispatch Results",
            results,
            empty="No dispatch tests ran.",
            color=discord.Color.green(),
        )



    def post_targets(self) -> dict:
        return {
            "status": {
                "title": "Mattis Systems Status",
                "path": "/bot/status",
                "purpose": "status",
            },
            "overview": {
                "title": "Mattis Systems Overview",
                "path": "/bot/systems/overview",
                "purpose": "management",
            },
            "support": {
                "title": "Support Summary",
                "path": "/bot/support/stats",
                "purpose": "support",
            },
            "tickets": {
                "title": "Open Support Tickets",
                "path": "/bot/support/open",
                "purpose": "ticket",
            },
            "billing": {
                "title": "Billing Summary",
                "path": "/bot/billing/summary",
                "purpose": "billing",
            },
            "failed_invoices": {
                "title": "Failed Billing Items",
                "path": "/bot/billing/failed",
                "purpose": "invoice",
            },
            "trials": {
                "title": "Trial Subscriptions",
                "path": "/bot/billing/trials",
                "purpose": "billing",
            },
            "pastdue": {
                "title": "Past Due Billing",
                "path": "/bot/billing/pastdue",
                "purpose": "payment",
            },
            "security": {
                "title": "Security Risks",
                "path": "/bot/security/risks",
                "purpose": "security",
            },
            "sessions": {
                "title": "Security Sessions",
                "path": "/bot/security/sessions",
                "purpose": "security_log",
            },
            "incidents": {
                "title": "Incident Summary",
                "path": "/bot/incidents/summary",
                "purpose": "incident",
            },
            "audit": {
                "title": "Recent Audit Events",
                "path": "/bot/audit/recent",
                "purpose": "audit_log",
            },
            "highrisk": {
                "title": "High Risk Audit Events",
                "path": "/bot/audit/highrisk",
                "purpose": "audit_log",
            },
            "development": {
                "title": "Development / Modules Summary",
                "path": "/bot/modules/summary",
                "purpose": "development",
            },
            "automation": {
                "title": "Automation Summary",
                "path": "/bot/automation/summary",
                "purpose": "system_log",
            },
            "automation_failed": {
                "title": "Failed Automation",
                "path": "/bot/automation/failed",
                "purpose": "system_error",
            },
            "discord": {
                "title": "Discord Integration Summary",
                "path": "/bot/discord/summary",
                "purpose": "bot_log",
            },
            "roblox": {
                "title": "Roblox Integration Summary",
                "path": "/bot/roblox/summary",
                "purpose": "system_log",
            },
            "applications": {
                "title": "Applications Summary",
                "path": "/bot/applications/summary",
                "purpose": "support",
            },
            "staff": {
                "title": "Staff Summary",
                "path": "/bot/staff/summary",
                "purpose": "staff",
            },
        }

    def build_post_embed(self, target_key: str, target: dict, status: int, payload):
        colour = discord.Color.green() if 200 <= int(status) < 400 else discord.Color.red()
        e = embed(target["title"], color=colour)

        e.add_field(name="Target", value=f"`{target_key}`", inline=True)
        e.add_field(name="API", value=f"`HTTP {status}`", inline=True)
        e.add_field(name="Endpoint", value=f"`{target['path']}`", inline=False)

        if isinstance(payload, dict):
            counts = payload.get("counts")

            if isinstance(counts, dict):
                for key, value in list(counts.items())[:12]:
                    e.add_field(name=str(key).replace("_", " ").title(), value=str(value), inline=True)

            else:
                added = 0

                for key, value in payload.items():
                    if added >= 12:
                        break

                    if isinstance(value, (str, int, float, bool)) or value is None:
                        e.add_field(name=str(key).replace("_", " ").title(), value=str(value), inline=True)
                        added += 1
                    elif isinstance(value, list):
                        e.add_field(name=str(key).replace("_", " ").title(), value=f"{len(value)} items", inline=True)
                        added += 1
                    elif isinstance(value, dict):
                        e.add_field(name=str(key).replace("_", " ").title(), value=f"{len(value)} fields", inline=True)
                        added += 1

        preview = fmt_payload(payload)
        e.add_field(name="Payload preview", value=preview[:1000], inline=False)

        return e

    @mcore.group(name="post", invoke_without_command=True)
    async def post(self, ctx):
        """Post Mattis API summaries into routed channels."""
        if not await require_admin(ctx):
            return

        await ctx.send(embed=embed(
            "Manual API Posting",
            "Use:\n"
            "`!mcore post list`\n"
            "`!mcore post preview billing`\n"
            "`!mcore post send billing`\n\n"
            "This only posts when you manually run it."
        ))

    @post.command(name="list")
    async def post_list(self, ctx):
        """List available manual post targets."""
        if not await require_admin(ctx):
            return

        targets = self.post_targets()
        lines = []

        for key, target in sorted(targets.items()):
            selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, target["purpose"])
            lines.append(
                f"`{key}` → purpose `{target['purpose']}` → "
                f"{channel.mention if channel else 'no usable route'}"
            )

        await self.send_paginated(
            ctx,
            "Manual Post Targets",
            lines,
            empty="No post targets configured.",
        )

    @post.command(name="preview")
    async def post_preview(self, ctx, target_key: str):
        """Preview where a post target will go without sending API data."""
        if not await require_admin(ctx):
            return

        targets = self.post_targets()
        clean_key = self.route_slug(target_key)
        target = targets.get(clean_key)

        if not target:
            await ctx.send(embed=error_embed(
                "Unknown post target",
                f"`{clean_key}` is not valid. Run `!mcore post list`."
            ))
            return

        selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, target["purpose"])

        lines = [
            f"Target: `{clean_key}`",
            f"Title: **{target['title']}**",
            f"Endpoint: `{target['path']}`",
            f"Purpose: `{target['purpose']}`",
            "",
            "**Candidate routes:**",
        ]

        saved = await self.saved_routes(ctx.guild)

        for candidate in candidates:
            c = self.route_slug(candidate)
            cid = saved.get(c)
            ch = ctx.guild.get_channel(cid) if cid else None
            marker = "✅ selected" if c == selected_key else "•"
            lines.append(f"{marker} `{c}` → {ch.mention if ch else 'not saved'}")

        await self.send_paginated(ctx, "Manual Post Preview", lines)

    @post.command(name="send")
    async def post_send(self, ctx, target_key: str):
        """Fetch Mattis API data and post it to the correct routed channel."""
        if not await require_admin(ctx):
            return

        targets = self.post_targets()
        clean_key = self.route_slug(target_key)
        target = targets.get(clean_key)

        if not target:
            await ctx.send(embed=error_embed(
                "Unknown post target",
                f"`{clean_key}` is not valid. Run `!mcore post list`."
            ))
            return

        selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, target["purpose"])

        if not channel:
            lines = [
                f"No usable route found for `{target['purpose']}`.",
                "",
                "**Tried:**",
            ]
            lines.extend(f"• `{self.route_slug(c)}`" for c in candidates)
            await self.send_paginated(ctx, "Post Failed", lines, color=discord.Color.red())
            return

        status, payload = await request_json(self.bot, "GET", target["path"])
        e = self.build_post_embed(clean_key, target, status, payload)
        e.add_field(name="Route", value=f"`{selected_key}` → {channel.mention}", inline=False)
        e.add_field(name="Triggered from", value=ctx.channel.mention, inline=True)

        notify_content = await self.notify_content_for(ctx.guild, target["purpose"], source="manual")
        await channel.send(
            content=notify_content or None,
            embed=e,
            allowed_mentions=self.notify_allowed_mentions(),
        )
        await ctx.send(embed=ok_embed(
            "Post sent",
            f"`{clean_key}` → `{selected_key}` → {channel.mention}"
        ))

    @post.command(name="sendmany")
    async def post_sendmany(self, ctx, *target_keys: str):
        """Send multiple manual posts."""
        if not await require_admin(ctx):
            return

        if not target_keys:
            await ctx.send(embed=error_embed(
                "No targets provided",
                "Example: `!mcore post sendmany status billing security incidents`"
            ))
            return

        targets = self.post_targets()
        results = []

        for raw_key in target_keys[:10]:
            clean_key = self.route_slug(raw_key)
            target = targets.get(clean_key)

            if not target:
                results.append(f"❌ `{clean_key}` unknown target")
                continue

            selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, target["purpose"])

            if not channel:
                results.append(f"❌ `{clean_key}` no usable route")
                continue

            status, payload = await request_json(self.bot, "GET", target["path"])
            e = self.build_post_embed(clean_key, target, status, payload)
            e.add_field(name="Route", value=f"`{selected_key}` → {channel.mention}", inline=False)
            e.add_field(name="Triggered from", value=ctx.channel.mention, inline=True)

            notify_content = await self.notify_content_for(ctx.guild, target["purpose"], source="manual")
            await channel.send(
                content=notify_content or None,
                embed=e,
                allowed_mentions=self.notify_allowed_mentions(),
            )
            results.append(f"✅ `{clean_key}` → `{selected_key}` → {channel.mention}")

        await self.send_paginated(
            ctx,
            "Manual Post Results",
            results,
            empty="No posts sent.",
            color=discord.Color.green(),
        )



    def alert_rules(self) -> dict:
        return {
            "support_critical": {
                "title": "Critical Support Tickets",
                "path": "/bot/support/critical",
                "purpose": "incident",
            },
            "support_unassigned": {
                "title": "Unassigned Support Tickets",
                "path": "/bot/support/unassigned",
                "purpose": "support",
            },
            "billing_failed": {
                "title": "Failed Billing Items",
                "path": "/bot/billing/failed",
                "purpose": "invoice",
            },
            "billing_pastdue": {
                "title": "Past Due Billing",
                "path": "/bot/billing/pastdue",
                "purpose": "payment",
            },
            "audit_highrisk": {
                "title": "High Risk Audit Events",
                "path": "/bot/audit/highrisk",
                "purpose": "audit_log",
            },
            "security_risks": {
                "title": "Security Risks",
                "path": "/bot/security/risks",
                "purpose": "security",
            },
            "security_suspicious": {
                "title": "Suspicious Security Activity",
                "path": "/bot/security/suspicious",
                "purpose": "security_log",
            },
            "automation_failed": {
                "title": "Failed Automation",
                "path": "/bot/automation/failed",
                "purpose": "system_error",
            },
            "discord_broken": {
                "title": "Broken Discord Integrations",
                "path": "/bot/discord/broken",
                "purpose": "bot_log",
            },
            "roblox_broken": {
                "title": "Broken Roblox Integrations",
                "path": "/bot/roblox/broken",
                "purpose": "system_log",
            },
            "incidents": {
                "title": "Incident Summary",
                "path": "/bot/incidents/summary",
                "purpose": "incident",
            },
        }

    async def get_alert_settings(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)
        settings = await cfg.guild(guild).alert_settings()
        settings = settings or {}

        settings.setdefault("enabled", False)
        settings.setdefault("cooldown_minutes", 30)
        settings.setdefault("interval_minutes", 10)
        settings.setdefault("rules_enabled", {})

        return settings

    async def save_alert_settings(self, guild: discord.Guild, settings: dict):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).alert_settings.set(settings)

    async def get_alert_state(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)
        state = await cfg.guild(guild).alert_state()
        state = state or {}
        state.setdefault("rules", {})
        return state

    async def save_alert_state(self, guild: discord.Guild, state: dict):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).alert_state.set(state)

    def is_alert_rule_enabled(self, settings: dict, rule_key: str) -> bool:
        rules_enabled = settings.get("rules_enabled", {}) or {}
        return bool(rules_enabled.get(rule_key, True))

    def payload_signature(self, payload) -> str:
        try:
            raw = json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            raw = str(payload)

        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def alert_issue_count(self, payload) -> int:
        if payload is None:
            return 0

        if isinstance(payload, list):
            return len(payload)

        if not isinstance(payload, dict):
            return 1 if payload else 0

        if payload.get("ok") is False:
            return 1

        list_keys = [
            "items",
            "results",
            "data",
            "tickets",
            "invoices",
            "risks",
            "events",
            "sessions",
            "workflows",
            "runs",
            "broken",
            "failed",
            "records",
        ]

        for key in list_keys:
            value = payload.get(key)
            if isinstance(value, list) and len(value) > 0:
                return len(value)

        numeric_keys = [
            "count",
            "total",
            "failed",
            "critical",
            "unassigned",
            "pastDue",
            "past_due",
            "highRisk",
            "high_risk",
            "broken",
            "suspicious",
            "risks",
            "open",
        ]

        for key in numeric_keys:
            value = payload.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)

        counts = payload.get("counts")
        if isinstance(counts, dict):
            total = 0
            for value in counts.values():
                if isinstance(value, (int, float)) and value > 0:
                    total += int(value)
            return total

        return 0


    def count_value(self, value) -> int:
        if value is None:
            return 0

        if isinstance(value, list):
            return len(value)

        if isinstance(value, dict):
            if "items" in value and isinstance(value["items"], list):
                return len(value["items"])
            if "results" in value and isinstance(value["results"], list):
                return len(value["results"])
            return 0

        if isinstance(value, bool):
            return 1 if value else 0

        if isinstance(value, (int, float)):
            return int(value) if value > 0 else 0

        return 0

    def first_count(self, payload, keys: list[str]) -> int:
        if not isinstance(payload, dict):
            return self.count_value(payload)

        places = [payload]

        counts = payload.get("counts")
        if isinstance(counts, dict):
            places.append(counts)

        summary = payload.get("summary")
        if isinstance(summary, dict):
            places.append(summary)

        meta = payload.get("meta")
        if isinstance(meta, dict):
            places.append(meta)

        for place in places:
            for key in keys:
                if key in place:
                    count = self.count_value(place.get(key))
                    if count > 0:
                        return count

        for key in ["items", "results", "data", "records"]:
            if key in payload:
                count = self.count_value(payload.get(key))
                if count > 0:
                    return count

        return 0

    def alert_issue_count_for_rule(self, rule_key: str, payload, status: int) -> int:
        if status >= 400:
            return 1

        rule_key = self.route_slug(rule_key)

        rule_keys = {
            "support_critical": [
                "critical",
                "criticalTickets",
                "critical_tickets",
                "urgent",
                "priority",
                "items",
                "tickets",
            ],
            "support_unassigned": [
                "unassigned",
                "unassignedTickets",
                "unassigned_tickets",
                "items",
                "tickets",
            ],
            "billing_failed": [
                "failed",
                "failedInvoices",
                "failed_invoices",
                "failedPayments",
                "failed_payments",
                "items",
                "invoices",
            ],
            "billing_pastdue": [
                "pastDue",
                "past_due",
                "pastdue",
                "overdue",
                "items",
                "invoices",
            ],
            "audit_highrisk": [
                "highRisk",
                "high_risk",
                "highRiskEvents",
                "high_risk_events",
                "events",
                "items",
            ],
            "security_risks": [
                "risks",
                "criticalRisks",
                "critical_risks",
                "highRisk",
                "high_risk",
                "items",
            ],
            "security_suspicious": [
                "suspicious",
                "suspiciousActivity",
                "suspicious_activity",
                "items",
                "sessions",
            ],
            "automation_failed": [
                "failed",
                "failedRuns",
                "failed_runs",
                "runs",
                "items",
            ],
            "discord_broken": [
                "broken",
                "brokenRoutes",
                "broken_routes",
                "brokenMappings",
                "broken_mappings",
                "items",
            ],
            "roblox_broken": [
                "broken",
                "brokenPolicies",
                "broken_policies",
                "drift",
                "items",
            ],
            "incidents": [
                "active",
                "activeIncidents",
                "active_incidents",
                "open",
                "openIncidents",
                "open_incidents",
                "critical",
                "major",
                "unresolved",
                "ongoing",
                "items",
            ],
        }

        keys = rule_keys.get(rule_key)

        if keys:
            return self.first_count(payload, keys)

        return self.alert_issue_count(payload)

    def build_alert_embed(self, rule_key: str, rule: dict, status: int, payload, issue_count: int, route_key: str):
        colour = discord.Color.red() if issue_count > 0 or status >= 400 else discord.Color.green()
        e = embed(rule["title"], color=colour)
        e.add_field(name="Alert rule", value=f"`{rule_key}`", inline=True)
        e.add_field(name="API", value=f"`HTTP {status}`", inline=True)
        e.add_field(name="Issue count", value=f"`{issue_count}`", inline=True)
        e.add_field(name="Endpoint", value=f"`{rule['path']}`", inline=False)
        e.add_field(name="Route", value=f"`{route_key}`", inline=False)
        e.add_field(name="Payload preview", value=fmt_payload(payload)[:1000], inline=False)
        return e

    async def run_one_alert_rule(self, guild: discord.Guild, rule_key: str, *, dry_run: bool = False, force: bool = False) -> dict:
        settings = await self.get_alert_settings(guild)
        state = await self.get_alert_state(guild)

        if not self.is_alert_rule_enabled(settings, rule_key):
            return {
                "rule": rule_key,
                "triggered": False,
                "sent": False,
                "status": "disabled",
            }

        rules = self.alert_rules()
        rule = rules.get(rule_key)

        if not rule:
            return {
                "rule": rule_key,
                "triggered": False,
                "sent": False,
                "status": "unknown_rule",
            }

        status, payload = await request_json(self.bot, "GET", rule["path"])
        issue_count = self.alert_issue_count_for_rule(rule_key, payload, status)

        if status >= 400:
            issue_count = max(issue_count, 1)

        if issue_count <= 0:
            return {
                "rule": rule_key,
                "triggered": False,
                "sent": False,
                "status": f"clear_http_{status}",
            }

        selected_key, channel, candidates = await self.resolve_dispatch_route(guild, rule["purpose"])

        if not channel:
            return {
                "rule": rule_key,
                "triggered": True,
                "sent": False,
                "status": "no_route",
                "candidates": candidates,
            }

        now = int(time.time())
        cooldown_seconds = int(settings.get("cooldown_minutes", 30)) * 60
        signature = self.payload_signature(payload)

        rule_state = state.setdefault("rules", {}).setdefault(rule_key, {})
        last_sent = int(rule_state.get("last_sent", 0))
        last_sig = rule_state.get("signature")

        if not force and not dry_run:
            if last_sig == signature and now - last_sent < cooldown_seconds:
                return {
                    "rule": rule_key,
                    "triggered": True,
                    "sent": False,
                    "status": "cooldown_duplicate",
                    "route": selected_key,
                }

            if now - last_sent < cooldown_seconds:
                return {
                    "rule": rule_key,
                    "triggered": True,
                    "sent": False,
                    "status": "cooldown",
                    "route": selected_key,
                }

        if dry_run:
            return {
                "rule": rule_key,
                "triggered": True,
                "sent": False,
                "status": "dry_run",
                "route": selected_key,
                "issue_count": issue_count,
            }

        e = self.build_alert_embed(rule_key, rule, status, payload, issue_count, selected_key)
        notify_content = await self.notify_content_for(guild, rule["purpose"], source="alerts")
        await channel.send(
            content=notify_content or None,
            embed=e,
            allowed_mentions=self.notify_allowed_mentions(),
        )

        rule_state["last_sent"] = now
        rule_state["signature"] = signature
        rule_state["issue_count"] = issue_count
        rule_state["route"] = selected_key

        await self.save_alert_state(guild, state)

        return {
            "rule": rule_key,
            "triggered": True,
            "sent": True,
            "status": "sent",
            "route": selected_key,
            "issue_count": issue_count,
        }

    async def run_alert_checks(self, guild: discord.Guild, *, dry_run: bool = False, force: bool = False) -> list[dict]:
        results = []

        for rule_key in self.alert_rules().keys():
            try:
                result = await self.run_one_alert_rule(guild, rule_key, dry_run=dry_run, force=force)
            except Exception as exc:
                result = {
                    "rule": rule_key,
                    "triggered": False,
                    "sent": False,
                    "status": f"error: {type(exc).__name__}: {exc}",
                }

            results.append(result)

        return results

    @tasks.loop(minutes=5)
    async def alert_loop(self):
        if not self.bot.is_ready():
            return

        for guild in list(self.bot.guilds):
            settings = await self.get_alert_settings(guild)

            if not settings.get("enabled", False):
                continue

            state = await self.get_alert_state(guild)
            now = int(time.time())
            interval_seconds = max(5, int(settings.get("interval_minutes", 10))) * 60
            last_run = int(state.get("_last_run", 0))

            if now - last_run < interval_seconds:
                continue

            state["_last_run"] = now
            await self.save_alert_state(guild, state)

            await self.run_alert_checks(guild, dry_run=False, force=False)

    @mcore.group(name="alerts", invoke_without_command=True)
    async def alerts(self, ctx):
        """Automatic routed alerts. Disabled by default."""
        if not await require_admin(ctx):
            return

        settings = await self.get_alert_settings(ctx.guild)
        state = await self.get_alert_state(ctx.guild)

        e = embed("Mattis Alert Engine")
        e.add_field(name="Enabled", value="✅ yes" if settings.get("enabled") else "❌ no", inline=True)
        e.add_field(name="Interval", value=f"`{settings.get('interval_minutes', 10)} min`", inline=True)
        e.add_field(name="Cooldown", value=f"`{settings.get('cooldown_minutes', 30)} min`", inline=True)
        e.add_field(name="Rules", value=f"`{len(self.alert_rules())}`", inline=True)
        e.add_field(name="Last run", value=f"<t:{int(state.get('_last_run', 0))}:R>" if state.get("_last_run") else "Never", inline=True)
        e.add_field(
            name="Commands",
            value="`!mcore alerts list`\n`!mcore alerts preview`\n`!mcore alerts check`\n`!mcore alerts enable`\n`!mcore alerts disable`",
            inline=False,
        )

        await ctx.send(embed=e)

    @alerts.command(name="list")
    async def alerts_list(self, ctx):
        """List alert rules and their routes."""
        if not await require_admin(ctx):
            return

        settings = await self.get_alert_settings(ctx.guild)
        lines = []

        for rule_key, rule in self.alert_rules().items():
            enabled = self.is_alert_rule_enabled(settings, rule_key)
            selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, rule["purpose"])
            lines.append(
                f"{'✅' if enabled else '❌'} `{rule_key}` → purpose `{rule['purpose']}` → "
                f"{channel.mention if channel else 'no usable route'}"
            )

        await self.send_paginated(ctx, "Alert Rules", lines)

    @alerts.command(name="preview")
    async def alerts_preview(self, ctx):
        """Check alert rules without sending anything."""
        if not await require_admin(ctx):
            return

        results = await self.run_alert_checks(ctx.guild, dry_run=True, force=True)
        lines = []

        for result in results:
            marker = "⚠️" if result.get("triggered") else "✅"
            lines.append(
                f"{marker} `{result['rule']}` · `{result.get('status')}`"
                + (f" · route `{result.get('route')}`" if result.get("route") else "")
                + (f" · count `{result.get('issue_count')}`" if result.get("issue_count") is not None else "")
            )

        await self.send_paginated(ctx, "Alert Preview", lines)

    @alerts.command(name="check")
    async def alerts_check(self, ctx):
        """Run alert checks now and send triggered alerts, respecting cooldown."""
        if not await require_admin(ctx):
            return

        results = await self.run_alert_checks(ctx.guild, dry_run=False, force=False)
        lines = []

        for result in results:
            if result.get("sent"):
                marker = "📨"
            elif result.get("triggered"):
                marker = "⚠️"
            else:
                marker = "✅"

            lines.append(
                f"{marker} `{result['rule']}` · `{result.get('status')}`"
                + (f" · route `{result.get('route')}`" if result.get("route") else "")
            )

        await self.send_paginated(ctx, "Alert Check Results", lines)

    @alerts.command(name="force")
    async def alerts_force(self, ctx):
        """Force-send triggered alerts, bypassing cooldown."""
        if not await require_admin(ctx):
            return

        results = await self.run_alert_checks(ctx.guild, dry_run=False, force=True)
        lines = []

        for result in results:
            marker = "📨" if result.get("sent") else ("⚠️" if result.get("triggered") else "✅")
            lines.append(
                f"{marker} `{result['rule']}` · `{result.get('status')}`"
                + (f" · route `{result.get('route')}`" if result.get("route") else "")
            )

        await self.send_paginated(ctx, "Forced Alert Results", lines)

    @alerts.command(name="enable")
    async def alerts_enable(self, ctx):
        """Enable background alerts."""
        if not await require_admin(ctx):
            return

        settings = await self.get_alert_settings(ctx.guild)
        settings["enabled"] = True
        await self.save_alert_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed(
            "Alerts enabled",
            "Background alerts are now enabled. Use `!mcore alerts disable` to stop them."
        ))

    @alerts.command(name="disable")
    async def alerts_disable(self, ctx):
        """Disable background alerts."""
        if not await require_admin(ctx):
            return

        settings = await self.get_alert_settings(ctx.guild)
        settings["enabled"] = False
        await self.save_alert_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Alerts disabled", "Background alerts are now disabled."))

    @alerts.command(name="cooldown")
    async def alerts_cooldown(self, ctx, minutes: int):
        """Set alert cooldown minutes."""
        if not await require_admin(ctx):
            return

        minutes = max(5, min(int(minutes), 1440))
        settings = await self.get_alert_settings(ctx.guild)
        settings["cooldown_minutes"] = minutes
        await self.save_alert_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Alert cooldown saved", f"Cooldown is now `{minutes}` minutes."))

    @alerts.command(name="interval")
    async def alerts_interval(self, ctx, minutes: int):
        """Set background check interval minutes."""
        if not await require_admin(ctx):
            return

        minutes = max(5, min(int(minutes), 1440))
        settings = await self.get_alert_settings(ctx.guild)
        settings["interval_minutes"] = minutes
        await self.save_alert_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Alert interval saved", f"Background check interval is now `{minutes}` minutes."))

    @alerts.command(name="ruleoff")
    async def alerts_ruleoff(self, ctx, rule_key: str):
        """Disable one alert rule."""
        if not await require_admin(ctx):
            return

        rule_key = self.route_slug(rule_key)
        if rule_key not in self.alert_rules():
            await ctx.send(embed=error_embed("Unknown alert rule", f"`{rule_key}` is not a valid rule."))
            return

        settings = await self.get_alert_settings(ctx.guild)
        rules_enabled = settings.setdefault("rules_enabled", {})
        rules_enabled[rule_key] = False
        await self.save_alert_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Alert rule disabled", f"`{rule_key}` disabled."))

    @alerts.command(name="ruleon")
    async def alerts_ruleon(self, ctx, rule_key: str):
        """Enable one alert rule."""
        if not await require_admin(ctx):
            return

        rule_key = self.route_slug(rule_key)
        if rule_key not in self.alert_rules():
            await ctx.send(embed=error_embed("Unknown alert rule", f"`{rule_key}` is not a valid rule."))
            return

        settings = await self.get_alert_settings(ctx.guild)
        rules_enabled = settings.setdefault("rules_enabled", {})
        rules_enabled[rule_key] = True
        await self.save_alert_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Alert rule enabled", f"`{rule_key}` enabled."))

    @alerts.command(name="reset")
    async def alerts_reset(self, ctx):
        """Clear alert cooldown/dedupe state."""
        if not await require_admin(ctx):
            return

        await self.save_alert_state(ctx.guild, {"rules": {}})
        await ctx.send(embed=ok_embed("Alert state reset", "Cooldown and duplicate state cleared."))



    def log_rules(self) -> dict:
        return {
            "audit_recent": {
                "title": "Audit Log",
                "path": "/bot/audit/recent",
                "purpose": "audit_log",
            },
            "audit_highrisk": {
                "title": "High Risk Audit Log",
                "path": "/bot/audit/highrisk",
                "purpose": "audit_log",
            },
            "support_all": {
                "title": "Support Ticket Log",
                "path": "/bot/support/all",
                "purpose": "ticket_log",
            },
            "support_open": {
                "title": "Open Ticket Log",
                "path": "/bot/support/open",
                "purpose": "ticket_log",
            },
            "support_critical": {
                "title": "Critical Ticket Log",
                "path": "/bot/support/critical",
                "purpose": "incident_log",
            },
            "billing_failed": {
                "title": "Failed Billing Log",
                "path": "/bot/billing/failed",
                "purpose": "payment_log",
            },
            "billing_pastdue": {
                "title": "Past Due Billing Log",
                "path": "/bot/billing/pastdue",
                "purpose": "payment_log",
            },
            "billing_trials": {
                "title": "Trial Billing Log",
                "path": "/bot/billing/trials",
                "purpose": "payment_log",
            },
            "security_risks": {
                "title": "Security Risk Log",
                "path": "/bot/security/risks",
                "purpose": "security_log",
            },
            "security_sessions": {
                "title": "Security Session Log",
                "path": "/bot/security/sessions",
                "purpose": "security_log",
            },
            "security_suspicious": {
                "title": "Suspicious Activity Log",
                "path": "/bot/security/suspicious",
                "purpose": "security_log",
            },
            "discord_summary": {
                "title": "Discord Integration Log",
                "path": "/bot/discord/summary",
                "purpose": "bot_log",
            },
            "discord_broken": {
                "title": "Broken Discord Integration Log",
                "path": "/bot/discord/broken",
                "purpose": "bot_log",
            },
            "roblox_summary": {
                "title": "Roblox Integration Log",
                "path": "/bot/roblox/summary",
                "purpose": "system_log",
            },
            "roblox_broken": {
                "title": "Broken Roblox Integration Log",
                "path": "/bot/roblox/broken",
                "purpose": "system_log",
            },
            "automation_failed": {
                "title": "Failed Automation Log",
                "path": "/bot/automation/failed",
                "purpose": "system_log",
            },
            "incidents": {
                "title": "Incident Log",
                "path": "/bot/incidents/summary",
                "purpose": "incident_log",
            },
            "modules": {
                "title": "Module Log",
                "path": "/bot/modules/summary",
                "purpose": "system_log",
            },
            "applications_recent": {
                "title": "Application Log",
                "path": "/bot/applications/recent",
                "purpose": "ticket_log",
            },
            "staff_summary": {
                "title": "Staff Log",
                "path": "/bot/staff/summary",
                "purpose": "member_log",
            },
        }

    async def get_log_settings(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)
        settings = await cfg.guild(guild).log_settings()
        settings = settings or {}
        settings.setdefault("enabled", False)
        settings.setdefault("interval_minutes", 5)
        settings.setdefault("rules_enabled", {})
        settings.setdefault("post_summaries", True)
        settings.setdefault("post_items", True)
        settings.setdefault("max_items_per_rule", 25)
        return settings

    async def save_log_settings(self, guild: discord.Guild, settings: dict):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).log_settings.set(settings)

    async def get_log_state(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)
        state = await cfg.guild(guild).log_state()
        state = state or {}
        state.setdefault("rules", {})
        return state

    async def save_log_state(self, guild: discord.Guild, state: dict):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).log_state.set(state)

    def is_log_rule_enabled(self, settings: dict, rule_key: str) -> bool:
        rules_enabled = settings.get("rules_enabled", {}) or {}
        return bool(rules_enabled.get(rule_key, True))

    def extract_log_items(self, payload) -> list:
        if payload is None:
            return []

        if isinstance(payload, list):
            return payload

        if not isinstance(payload, dict):
            return [{"value": payload}]

        keys = [
            "items",
            "results",
            "data",
            "records",
            "events",
            "logs",
            "tickets",
            "invoices",
            "sessions",
            "risks",
            "runs",
            "workflows",
            "applications",
            "submissions",
            "broken",
            "failed",
        ]

        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value

        # If there is no item list, log the whole summary as one record.
        return [payload]

    def log_item_id(self, rule_key: str, item) -> str:
        if isinstance(item, dict):
            for key in ["id", "eventId", "event_id", "ticketId", "ticket_id", "invoiceId", "invoice_id", "sessionId", "session_id"]:
                value = item.get(key)
                if value:
                    return f"{rule_key}:{value}"

        try:
            raw = json.dumps(item, sort_keys=True, default=str)
        except Exception:
            raw = str(item)

        return f"{rule_key}:{hashlib.sha256(raw.encode('utf-8', errors='ignore')).hexdigest()}"

    def log_item_title(self, rule_key: str, rule: dict, item) -> str:
        if isinstance(item, dict):
            for key in ["title", "subject", "action", "type", "event", "name", "status"]:
                value = item.get(key)
                if value:
                    return f"{rule['title']} · {str(value)[:80]}"

        return rule["title"]

    def build_log_embed(self, rule_key: str, rule: dict, item, route_key: str, index: int, total: int):
        e = embed(self.log_item_title(rule_key, rule, item), color=discord.Color.blue())
        e.add_field(name="Log rule", value=f"`{rule_key}`", inline=True)
        e.add_field(name="Route", value=f"`{route_key}`", inline=True)
        e.add_field(name="Item", value=f"`{index}/{total}`", inline=True)
        e.add_field(name="Endpoint", value=f"`{rule['path']}`", inline=False)

        if isinstance(item, dict):
            added = 0
            for key, value in item.items():
                if added >= 10:
                    break

                if isinstance(value, (str, int, float, bool)) or value is None:
                    e.add_field(name=str(key).replace("_", " ").title(), value=trim(value, 1000), inline=True)
                    added += 1

            e.add_field(name="Raw preview", value=fmt_payload(item)[:900], inline=False)
        else:
            e.add_field(name="Raw preview", value=trim(item, 1000), inline=False)

        return e

    async def run_one_log_rule(self, guild: discord.Guild, rule_key: str, *, dry_run: bool = False, force: bool = False) -> dict:
        settings = await self.get_log_settings(guild)

        if not self.is_log_rule_enabled(settings, rule_key):
            return {"rule": rule_key, "status": "disabled", "sent": 0, "seen": 0}

        rules = self.log_rules()
        rule = rules.get(rule_key)

        if not rule:
            return {"rule": rule_key, "status": "unknown_rule", "sent": 0, "seen": 0}

        selected_key, channel, candidates = await self.resolve_dispatch_route(guild, rule["purpose"])

        if not channel:
            return {"rule": rule_key, "status": "no_route", "sent": 0, "seen": 0, "candidates": candidates}

        status, payload = await request_json(self.bot, "GET", rule["path"])

        if status >= 400:
            item = {"api_status": status, "error_payload": payload}
            items = [item]
        else:
            items = self.extract_log_items(payload)

        max_items = max(1, int(settings.get("max_items_per_rule", 25)))
        items = items[:max_items]

        state = await self.get_log_state(guild)
        rule_state = state.setdefault("rules", {}).setdefault(rule_key, {})
        seen_ids = set(rule_state.get("seen_ids", []))

        sent = 0
        skipped = 0
        new_seen = list(seen_ids)

        total = len(items)

        for index, item in enumerate(items, start=1):
            item_id = self.log_item_id(rule_key, item)

            if not force and item_id in seen_ids:
                skipped += 1
                continue

            if dry_run:
                sent += 1
                continue

            e = self.build_log_embed(rule_key, rule, item, selected_key, index, total)
            notify_content = await self.notify_content_for(guild, rule["purpose"], source="logs")
            await channel.send(
                content=notify_content or None,
                embed=e,
                allowed_mentions=self.notify_allowed_mentions(),
            )

            sent += 1

            if item_id not in new_seen:
                new_seen.append(item_id)

        # Keep enough seen IDs to prevent duplicates, without growing forever.
        rule_state["seen_ids"] = new_seen[-1000:]
        rule_state["last_run"] = int(time.time())
        rule_state["last_status"] = status
        rule_state["last_route"] = selected_key

        if not dry_run:
            await self.save_log_state(guild, state)

        return {
            "rule": rule_key,
            "status": f"http_{status}",
            "sent": sent,
            "skipped": skipped,
            "seen": total,
            "route": selected_key,
        }

    async def run_log_checks(self, guild: discord.Guild, *, dry_run: bool = False, force: bool = False) -> list[dict]:
        results = []

        for rule_key in self.log_rules().keys():
            try:
                result = await self.run_one_log_rule(guild, rule_key, dry_run=dry_run, force=force)
            except Exception as exc:
                result = {
                    "rule": rule_key,
                    "status": f"error: {type(exc).__name__}: {exc}",
                    "sent": 0,
                    "seen": 0,
                }

            results.append(result)

        return results

    @tasks.loop(minutes=5)
    async def log_loop(self):
        if not self.bot.is_ready():
            return

        for guild in list(self.bot.guilds):
            settings = await self.get_log_settings(guild)

            if not settings.get("enabled", False):
                continue

            state = await self.get_log_state(guild)
            now = int(time.time())
            interval_seconds = max(1, int(settings.get("interval_minutes", 5))) * 60
            last_run = int(state.get("_last_run", 0))

            if now - last_run < interval_seconds:
                continue

            state["_last_run"] = now
            await self.save_log_state(guild, state)

            await self.run_log_checks(guild, dry_run=False, force=False)

    @mcore.group(name="logs", invoke_without_command=True)
    async def logs(self, ctx):
        """Full log forwarding engine."""
        if not await require_admin(ctx):
            return

        settings = await self.get_log_settings(ctx.guild)
        state = await self.get_log_state(ctx.guild)

        e = embed("Mattis Log Forwarding Engine")
        e.add_field(name="Enabled", value="✅ yes" if settings.get("enabled") else "❌ no", inline=True)
        e.add_field(name="Interval", value=f"`{settings.get('interval_minutes', 5)} min`", inline=True)
        e.add_field(name="Max items/rule", value=f"`{settings.get('max_items_per_rule', 25)}`", inline=True)
        e.add_field(name="Rules", value=f"`{len(self.log_rules())}`", inline=True)
        e.add_field(name="Last run", value=f"<t:{int(state.get('_last_run', 0))}:R>" if state.get("_last_run") else "Never", inline=True)
        e.add_field(
            name="Commands",
            value="`!mcore logs list`\n`!mcore logs preview`\n`!mcore logs check`\n`!mcore logs force`\n`!mcore logs enable`\n`!mcore logs disable`",
            inline=False,
        )

        await ctx.send(embed=e)

    @logs.command(name="list")
    async def logs_list(self, ctx):
        """List log forwarding rules."""
        if not await require_admin(ctx):
            return

        settings = await self.get_log_settings(ctx.guild)
        lines = []

        for rule_key, rule in self.log_rules().items():
            enabled = self.is_log_rule_enabled(settings, rule_key)
            selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, rule["purpose"])
            lines.append(
                f"{'✅' if enabled else '❌'} `{rule_key}` → purpose `{rule['purpose']}` → "
                f"{channel.mention if channel else 'no usable route'}"
            )

        await self.send_paginated(ctx, "Log Forwarding Rules", lines)

    @logs.command(name="preview")
    async def logs_preview(self, ctx, *rule_keys: str):
        """Preview which logs would be posted without sending. Optional: !mcore logs preview audit_recent"""
        if not await require_admin(ctx):
            return

        started = await ctx.send(embed=embed(
            "Log Preview Started",
            "Checking log rules now. This may take a moment if the API has lots of records."
        ))

        if rule_keys:
            results = []
            for raw_key in rule_keys[:10]:
                key = self.route_slug(raw_key)
                result = await self.run_one_log_rule(ctx.guild, key, dry_run=True, force=False)
                results.append(result)
        else:
            results = await self.run_log_checks(ctx.guild, dry_run=True, force=False)

        lines = []

        for r in results:
            lines.append(
                f"`{r['rule']}` · `{r.get('status')}` · seen `{r.get('seen', 0)}` · would-send `{r.get('sent', 0)}`"
                + (f" · route `{r.get('route')}`" if r.get("route") else "")
            )

        pages = self.build_pages(lines, empty="No log rules checked.")
        view = PagedEmbedView(ctx, title="Log Preview", pages=pages, color=None)

        await started.edit(embed=view.current_embed(), view=view if len(pages) > 1 else None)

    @logs.command(name="check")
    async def logs_check(self, ctx, *rule_keys: str):
        """Forward new unseen logs now. Optional: !mcore logs check audit_recent"""
        if not await require_admin(ctx):
            return

        started = await ctx.send(embed=embed(
            "Log Forwarding Started",
            "Forwarding new unseen logs now. I will edit this message when finished."
        ))

        if rule_keys:
            results = []
            for raw_key in rule_keys[:10]:
                key = self.route_slug(raw_key)
                result = await self.run_one_log_rule(ctx.guild, key, dry_run=False, force=False)
                results.append(result)
        else:
            results = await self.run_log_checks(ctx.guild, dry_run=False, force=False)

        lines = []

        for r in results:
            lines.append(
                f"`{r['rule']}` · `{r.get('status')}` · seen `{r.get('seen', 0)}` · sent `{r.get('sent', 0)}` · skipped `{r.get('skipped', 0)}`"
                + (f" · route `{r.get('route')}`" if r.get("route") else "")
            )

        pages = self.build_pages(lines, empty="No log forwarding results.")
        view = PagedEmbedView(ctx, title="Log Forwarding Check", pages=pages, color=discord.Color.green())

        await started.edit(embed=view.current_embed(), view=view if len(pages) > 1 else None)

    @logs.command(name="force")
    async def logs_force(self, ctx, *rule_keys: str):
        """Force post current log payloads even if already seen. Optional: !mcore logs force audit_recent"""
        if not await require_admin(ctx):
            return

        started = await ctx.send(embed=embed(
            "Forced Log Forwarding Started",
            "Force-posting current logs now. This can take a moment."
        ))

        if rule_keys:
            results = []
            for raw_key in rule_keys[:10]:
                key = self.route_slug(raw_key)
                result = await self.run_one_log_rule(ctx.guild, key, dry_run=False, force=True)
                results.append(result)
        else:
            results = await self.run_log_checks(ctx.guild, dry_run=False, force=True)

        lines = []

        for r in results:
            lines.append(
                f"`{r['rule']}` · `{r.get('status')}` · seen `{r.get('seen', 0)}` · force-sent `{r.get('sent', 0)}`"
                + (f" · route `{r.get('route')}`" if r.get("route") else "")
            )

        pages = self.build_pages(lines, empty="No forced log results.")
        view = PagedEmbedView(ctx, title="Forced Log Forwarding", pages=pages, color=discord.Color.green())

        await started.edit(embed=view.current_embed(), view=view if len(pages) > 1 else None)


    @logs.command(name="enable")
    async def logs_enable(self, ctx):
        """Enable background log forwarding."""
        if not await require_admin(ctx):
            return

        settings = await self.get_log_settings(ctx.guild)
        settings["enabled"] = True
        await self.save_log_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Log forwarding enabled", "Every new log item will be forwarded to routed log channels."))

    @logs.command(name="disable")
    async def logs_disable(self, ctx):
        """Disable background log forwarding."""
        if not await require_admin(ctx):
            return

        settings = await self.get_log_settings(ctx.guild)
        settings["enabled"] = False
        await self.save_log_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Log forwarding disabled"))

    @logs.command(name="interval")
    async def logs_interval(self, ctx, minutes: int):
        """Set log forwarding interval."""
        if not await require_admin(ctx):
            return

        minutes = max(1, min(int(minutes), 1440))
        settings = await self.get_log_settings(ctx.guild)
        settings["interval_minutes"] = minutes
        await self.save_log_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Log interval saved", f"Log forwarding interval is now `{minutes}` minutes."))

    @logs.command(name="limit")
    async def logs_limit(self, ctx, count: int):
        """Set max items per rule per check."""
        if not await require_admin(ctx):
            return

        count = max(1, min(int(count), 100))
        settings = await self.get_log_settings(ctx.guild)
        settings["max_items_per_rule"] = count
        await self.save_log_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Log limit saved", f"Max items per rule is now `{count}`."))

    @logs.command(name="ruleoff")
    async def logs_ruleoff(self, ctx, rule_key: str):
        """Disable one log rule."""
        if not await require_admin(ctx):
            return

        rule_key = self.route_slug(rule_key)

        if rule_key not in self.log_rules():
            await ctx.send(embed=error_embed("Unknown log rule", f"`{rule_key}` is not valid."))
            return

        settings = await self.get_log_settings(ctx.guild)
        rules_enabled = settings.setdefault("rules_enabled", {})
        rules_enabled[rule_key] = False
        await self.save_log_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Log rule disabled", f"`{rule_key}` disabled."))

    @logs.command(name="ruleon")
    async def logs_ruleon(self, ctx, rule_key: str):
        """Enable one log rule."""
        if not await require_admin(ctx):
            return

        rule_key = self.route_slug(rule_key)

        if rule_key not in self.log_rules():
            await ctx.send(embed=error_embed("Unknown log rule", f"`{rule_key}` is not valid."))
            return

        settings = await self.get_log_settings(ctx.guild)
        rules_enabled = settings.setdefault("rules_enabled", {})
        rules_enabled[rule_key] = True
        await self.save_log_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Log rule enabled", f"`{rule_key}` enabled."))

    @logs.command(name="reset")
    async def logs_reset(self, ctx):
        """Clear seen-log dedupe state."""
        if not await require_admin(ctx):
            return

        await self.save_log_state(ctx.guild, {"rules": {}})
        await ctx.send(embed=ok_embed("Log state reset", "Seen-log state cleared. Next check can post current logs again."))



    def notify_allowed_mentions(self):
        return discord.AllowedMentions(roles=True, users=False, everyone=False)

    async def get_notify_settings(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)
        settings = await cfg.guild(guild).notify_settings()
        settings = settings or {}

        settings.setdefault("enabled", False)
        settings.setdefault("alert_mentions", True)
        settings.setdefault("log_mentions", False)
        settings.setdefault("manual_mentions", False)
        settings.setdefault("dispatch_mentions", False)
        settings.setdefault("purpose_roles", {})

        return settings

    async def save_notify_settings(self, guild: discord.Guild, settings: dict):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).notify_settings.set(settings)

    def notify_source_enabled(self, settings: dict, source: str) -> bool:
        source = self.route_slug(source)

        if not settings.get("enabled", False):
            return False

        if source == "alerts":
            return bool(settings.get("alert_mentions", True))

        if source == "logs":
            return bool(settings.get("log_mentions", False))

        if source == "manual":
            return bool(settings.get("manual_mentions", False))

        if source == "dispatch":
            return bool(settings.get("dispatch_mentions", False))

        return False

    def notify_spec_for_purpose(self, purpose: str) -> dict:
        p = self.route_slug(purpose)

        # Default targeted pings. Custom role mappings can be added on top.
        if any(x in p for x in ["invoice", "payment", "refund", "chargeback", "billing", "pastdue", "finance"]):
            return {
                "sections": ["management"],
                "role_keywords": ["billing_support", "support_lead"],
            }

        if any(x in p for x in ["exploit", "security", "account_compromise", "suspicious", "security_log"]):
            return {
                "sections": ["management"],
                "role_keywords": ["security_admin", "security_support", "incident_response", "senior_moderator"],
            }

        if any(x in p for x in ["incident", "critical", "outage", "downtime", "incident_log"]):
            return {
                "sections": ["management", "administration", "development"],
                "role_keywords": ["incident_response", "security_admin", "senior_moderator", "release_manager"],
            }

        if any(x in p for x in ["deployment", "deploy", "release", "production", "staging"]):
            return {
                "sections": ["development", "administration"],
                "role_keywords": ["lead_developer", "release_manager", "infrastructure_admin"],
            }

        if any(x in p for x in ["api_log", "system_log", "bot_log", "system_error", "backend", "frontend", "development", "automation"]):
            return {
                "sections": ["development"],
                "role_keywords": ["lead_developer", "developer", "release_manager", "infrastructure_admin"],
            }

        if any(x in p for x in ["audit", "audit_log", "highrisk", "high_risk"]):
            return {
                "sections": ["management"],
                "role_keywords": ["audit_reviewer", "security_admin", "senior_moderator"],
            }

        if any(x in p for x in ["support", "ticket", "tickets", "customer", "priority_support"]):
            return {
                "sections": ["support", "moderation"],
                "role_keywords": ["support_lead", "support_agent", "technical_support"],
            }

        if any(x in p for x in ["staff", "member", "member_log"]):
            return {
                "sections": ["management", "administration"],
                "role_keywords": [],
            }

        if any(x in p for x in ["management", "strategy", "legal", "analytics"]):
            return {
                "sections": ["management"],
                "role_keywords": [],
            }

        return {
            "sections": ["management"],
            "role_keywords": [],
        }

    async def notify_role_ids_for(self, guild: discord.Guild, purpose: str) -> list[int]:
        settings = await self.get_notify_settings(guild)
        sections = await self.saved_sections(guild)
        spec = self.notify_spec_for_purpose(purpose)
        role_ids: set[int] = set()

        purpose_key = self.route_slug(purpose)
        custom_roles = settings.get("purpose_roles", {}) or {}

        for rid in custom_roles.get(purpose_key, []) or []:
            role_ids.add(int(rid))

        section_keywords = [self.route_slug(x) for x in spec.get("sections", [])]
        role_keywords = [self.route_slug(x) for x in spec.get("role_keywords", [])]

        for section_name, ids in sections.items():
            section_slug = self.route_slug(section_name)

            if any(keyword in section_slug for keyword in section_keywords):
                for rid in ids or []:
                    role_ids.add(int(rid))

        for role in guild.roles:
            if role == guild.default_role or role.managed:
                continue

            role_slug = self.route_slug(role.name)

            if any(keyword in role_slug for keyword in role_keywords):
                role_ids.add(role.id)

        live_roles = []

        for rid in role_ids:
            role = guild.get_role(int(rid))
            if role:
                live_roles.append(role)

        live_roles = sorted(live_roles, key=lambda r: r.position, reverse=True)

        return [role.id for role in live_roles]

    async def notify_content_for(self, guild: discord.Guild, purpose: str, *, source: str = "alerts", force: bool = False) -> str:
        settings = await self.get_notify_settings(guild)

        if not force and not self.notify_source_enabled(settings, source):
            return ""

        role_ids = await self.notify_role_ids_for(guild, purpose)

        mentions = []

        for rid in role_ids:
            role = guild.get_role(int(rid))
            if role:
                mentions.append(role.mention)

        return " ".join(mentions)

    @mcore.group(name="notify", invoke_without_command=True)
    async def notify(self, ctx):
        """Mention/ping routing for alerts and logs."""
        if not await require_admin(ctx):
            return

        settings = await self.get_notify_settings(ctx.guild)

        e = embed("Mattis Notify Engine")
        e.add_field(name="Global", value="✅ enabled" if settings.get("enabled") else "❌ disabled", inline=True)
        e.add_field(name="Alert mentions", value="✅ on" if settings.get("alert_mentions") else "❌ off", inline=True)
        e.add_field(name="Log mentions", value="✅ on" if settings.get("log_mentions") else "❌ off", inline=True)
        e.add_field(name="Manual post mentions", value="✅ on" if settings.get("manual_mentions") else "❌ off", inline=True)
        e.add_field(name="Dispatch mentions", value="✅ on" if settings.get("dispatch_mentions") else "❌ off", inline=True)
        e.add_field(
            name="Commands",
            value="`!mcore notify preview invoice`\n"
                  "`!mcore notify test invoice`\n"
                  "`!mcore notify enable`\n"
                  "`!mcore notify disable`\n"
                  "`!mcore notify alerts on/off`\n"
                  "`!mcore notify logs on/off`",
            inline=False,
        )

        await ctx.send(embed=e)

    @notify.command(name="enable")
    async def notify_enable(self, ctx):
        if not await require_admin(ctx):
            return

        settings = await self.get_notify_settings(ctx.guild)
        settings["enabled"] = True
        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Notify enabled", "Alert role pings are now enabled."))

    @notify.command(name="disable")
    async def notify_disable(self, ctx):
        if not await require_admin(ctx):
            return

        settings = await self.get_notify_settings(ctx.guild)
        settings["enabled"] = False
        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Notify disabled", "Role pings are now disabled."))

    def parse_on_off(self, value: str) -> bool | None:
        value = self.route_slug(value)

        if value in ["on", "enable", "enabled", "yes", "true", "1"]:
            return True

        if value in ["off", "disable", "disabled", "no", "false", "0"]:
            return False

        return None

    @notify.command(name="alerts")
    async def notify_alerts(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = self.parse_on_off(mode)

        if enabled is None:
            await ctx.send(embed=error_embed("Invalid mode", "Use `on` or `off`."))
            return

        settings = await self.get_notify_settings(ctx.guild)
        settings["alert_mentions"] = enabled
        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Alert mentions updated", f"Alert mentions are now `{'on' if enabled else 'off'}`."))

    @notify.command(name="logs")
    async def notify_logs(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = self.parse_on_off(mode)

        if enabled is None:
            await ctx.send(embed=error_embed("Invalid mode", "Use `on` or `off`."))
            return

        settings = await self.get_notify_settings(ctx.guild)
        settings["log_mentions"] = enabled
        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Log mentions updated", f"Log mentions are now `{'on' if enabled else 'off'}`."))

    @notify.command(name="manual")
    async def notify_manual(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = self.parse_on_off(mode)

        if enabled is None:
            await ctx.send(embed=error_embed("Invalid mode", "Use `on` or `off`."))
            return

        settings = await self.get_notify_settings(ctx.guild)
        settings["manual_mentions"] = enabled
        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Manual post mentions updated", f"Manual post mentions are now `{'on' if enabled else 'off'}`."))

    @notify.command(name="dispatch")
    async def notify_dispatch(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = self.parse_on_off(mode)

        if enabled is None:
            await ctx.send(embed=error_embed("Invalid mode", "Use `on` or `off`."))
            return

        settings = await self.get_notify_settings(ctx.guild)
        settings["dispatch_mentions"] = enabled
        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Dispatch mentions updated", f"Dispatch mentions are now `{'on' if enabled else 'off'}`."))

    @notify.command(name="preview")
    async def notify_preview(self, ctx, *, purpose: str):
        """Preview roles that would be pinged for a purpose."""
        if not await require_admin(ctx):
            return

        purpose_key = self.route_slug(purpose)
        role_ids = await self.notify_role_ids_for(ctx.guild, purpose_key)
        spec = self.notify_spec_for_purpose(purpose_key)

        lines = [
            f"Purpose: `{purpose_key}`",
            "",
            "**Default section keywords:**",
            ", ".join(f"`{x}`" for x in spec.get("sections", [])) or "None",
            "",
            "**Default role keywords:**",
            ", ".join(f"`{x}`" for x in spec.get("role_keywords", [])) or "None",
            "",
            "**Roles that would be mentioned:**",
        ]

        if role_ids:
            for rid in role_ids:
                role = ctx.guild.get_role(int(rid))
                lines.append(f"• {role.mention if role else f'`missing:{rid}`'}")
        else:
            lines.append("No matching roles found.")

        await self.send_paginated(ctx, "Notify Preview", lines)

    @notify.command(name="test")
    async def notify_test(self, ctx, purpose: str, *, message: str = "Mattis notify test."):
        """Send a routed test message with role mentions."""
        if not await require_admin(ctx):
            return

        purpose_key = self.route_slug(purpose)
        selected_key, channel, candidates = await self.resolve_dispatch_route(ctx.guild, purpose_key)

        if not channel:
            await ctx.send(embed=error_embed(
                "No route found",
                f"No usable route found for `{purpose_key}`."
            ))
            return

        content = await self.notify_content_for(ctx.guild, purpose_key, source="alerts", force=True)

        e = embed("Mattis Notify Test", message, color=discord.Color.gold())
        e.add_field(name="Purpose", value=f"`{purpose_key}`", inline=True)
        e.add_field(name="Route", value=f"`{selected_key}`", inline=True)
        e.add_field(name="Target", value=channel.mention, inline=True)
        e.add_field(name="Mention content", value=content or "No roles matched.", inline=False)

        await channel.send(
            content=content or None,
            embed=e,
            allowed_mentions=self.notify_allowed_mentions(),
        )

        await ctx.send(embed=ok_embed("Notify test sent", f"`{purpose_key}` → `{selected_key}` → {channel.mention}"))

    @notify.command(name="addrole")
    async def notify_addrole(self, ctx, purpose: str, role: discord.Role):
        """Add an exact role mention for a purpose."""
        if not await require_admin(ctx):
            return

        if role == ctx.guild.default_role:
            await ctx.send(embed=error_embed("Unsafe role", "I will not notify @everyone."))
            return

        purpose_key = self.route_slug(purpose)
        settings = await self.get_notify_settings(ctx.guild)
        purpose_roles = settings.setdefault("purpose_roles", {})
        purpose_roles.setdefault(purpose_key, [])

        if role.id not in purpose_roles[purpose_key]:
            purpose_roles[purpose_key].append(role.id)

        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Notify role added", f"`{purpose_key}` will also mention {role.mention}."))

    @notify.command(name="removerole")
    async def notify_removerole(self, ctx, purpose: str, role: discord.Role):
        """Remove an exact role mention for a purpose."""
        if not await require_admin(ctx):
            return

        purpose_key = self.route_slug(purpose)
        settings = await self.get_notify_settings(ctx.guild)
        purpose_roles = settings.setdefault("purpose_roles", {})
        purpose_roles[purpose_key] = [rid for rid in purpose_roles.get(purpose_key, []) if int(rid) != role.id]

        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Notify role removed", f"{role.mention} removed from `{purpose_key}`."))

    @notify.command(name="clear")
    async def notify_clear(self, ctx, *, purpose: str):
        """Clear custom exact role mentions for a purpose."""
        if not await require_admin(ctx):
            return

        purpose_key = self.route_slug(purpose)
        settings = await self.get_notify_settings(ctx.guild)
        purpose_roles = settings.setdefault("purpose_roles", {})
        purpose_roles.pop(purpose_key, None)

        await self.save_notify_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Notify custom roles cleared", f"Custom roles cleared for `{purpose_key}`."))

    @notify.command(name="list")
    async def notify_list(self, ctx):
        """Show useful notify purposes."""
        if not await require_admin(ctx):
            return

        purposes = [
            "invoice",
            "payment",
            "refund",
            "chargeback",
            "support",
            "ticket",
            "security",
            "exploit",
            "account_compromise",
            "suspicious_activity",
            "incident",
            "deployment",
            "release",
            "api_log",
            "system_log",
            "bot_log",
            "audit_log",
            "management",
        ]

        lines = []

        for purpose in purposes:
            role_ids = await self.notify_role_ids_for(ctx.guild, purpose)
            mentions = []
            for rid in role_ids[:8]:
                role = ctx.guild.get_role(int(rid))
                if role:
                    mentions.append(role.mention)

            lines.append(f"`{purpose}` → {' '.join(mentions) if mentions else 'No roles matched'}")

        await self.send_paginated(ctx, "Notify Purpose List", lines)


    async def get_eventlog_settings(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)
        settings = await cfg.guild(guild).eventlog_settings()
        settings = settings or {}

        settings.setdefault("enabled", False)
        settings.setdefault("message_content", False)
        settings.setdefault("include_bots", True)
        settings.setdefault("voice_events", False)
        settings.setdefault("ignored_channel_ids", [])

        return settings

    async def save_eventlog_settings(self, guild: discord.Guild, settings: dict):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).eventlog_settings.set(settings)

    def event_bool(self, value: str) -> bool | None:
        value = self.route_slug(value)

        if value in ["on", "enable", "enabled", "yes", "true", "1"]:
            return True

        if value in ["off", "disable", "disabled", "no", "false", "0"]:
            return False

        return None

    def channel_display(self, channel) -> str:
        if not channel:
            return "None"

        mention = getattr(channel, "mention", None)
        name = getattr(channel, "name", "unknown")

        return mention or f"`{name}`"

    async def should_log_event(self, guild: discord.Guild, *, channel=None, user=None, voice: bool = False) -> bool:
        if not guild:
            return False

        settings = await self.get_eventlog_settings(guild)

        if not settings.get("enabled", False):
            return False

        if voice and not settings.get("voice_events", False):
            return False

        if channel and getattr(channel, "id", None) in settings.get("ignored_channel_ids", []):
            return False

        if user and self.bot.user and user.id == self.bot.user.id:
            return False

        if user and getattr(user, "bot", False) and not settings.get("include_bots", True):
            return False

        return True

    async def send_event_log(
        self,
        guild: discord.Guild,
        purpose: str,
        title: str,
        description: str,
        *,
        fields: dict | None = None,
        color: discord.Color | None = None,
    ):
        selected_key, channel, candidates = await self.resolve_dispatch_route(guild, purpose)

        if not channel:
            return

        e = embed(title, trim(description, 4000), color=color or discord.Color.blue())
        e.add_field(name="Event route", value=f"`{selected_key}`", inline=True)

        for key, value in (fields or {}).items():
            e.add_field(name=str(key), value=trim(value, 1024), inline=False)

        notify_content = await self.notify_content_for(guild, purpose, source="logs")

        await channel.send(
            content=notify_content or None,
            embed=e,
            allowed_mentions=self.notify_allowed_mentions(),
        )

    @mcore.group(name="eventlogs", aliases=["events"], invoke_without_command=True)
    async def eventlogs(self, ctx):
        """Discord-native event logging."""
        if not await require_admin(ctx):
            return

        settings = await self.get_eventlog_settings(ctx.guild)

        e = embed("Discord Event Logger")
        e.add_field(name="Enabled", value="✅ yes" if settings.get("enabled") else "❌ no", inline=True)
        e.add_field(name="Message content", value="✅ yes" if settings.get("message_content") else "❌ no", inline=True)
        e.add_field(name="Include bots", value="✅ yes" if settings.get("include_bots") else "❌ no", inline=True)
        e.add_field(name="Voice events", value="✅ yes" if settings.get("voice_events") else "❌ no", inline=True)
        e.add_field(name="Ignored channels", value=f"`{len(settings.get('ignored_channel_ids', []))}`", inline=True)
        e.add_field(
            name="Commands",
            value="`!mcore eventlogs enable`\n"
                  "`!mcore eventlogs disable`\n"
                  "`!mcore eventlogs content on/off`\n"
                  "`!mcore eventlogs voice on/off`\n"
                  "`!mcore eventlogs bots on/off`\n"
                  "`!mcore eventlogs test`",
            inline=False,
        )

        await ctx.send(embed=e)

    @eventlogs.command(name="enable")
    async def eventlogs_enable(self, ctx):
        if not await require_admin(ctx):
            return

        settings = await self.get_eventlog_settings(ctx.guild)
        settings["enabled"] = True
        await self.save_eventlog_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Discord event logging enabled"))

    @eventlogs.command(name="disable")
    async def eventlogs_disable(self, ctx):
        if not await require_admin(ctx):
            return

        settings = await self.get_eventlog_settings(ctx.guild)
        settings["enabled"] = False
        await self.save_eventlog_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Discord event logging disabled"))

    @eventlogs.command(name="content")
    async def eventlogs_content(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = self.event_bool(mode)

        if enabled is None:
            await ctx.send(embed=error_embed("Invalid mode", "Use `on` or `off`."))
            return

        settings = await self.get_eventlog_settings(ctx.guild)
        settings["message_content"] = enabled
        await self.save_eventlog_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Message content logging updated", f"Message content logging is now `{'on' if enabled else 'off'}`."))

    @eventlogs.command(name="voice")
    async def eventlogs_voice(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = self.event_bool(mode)

        if enabled is None:
            await ctx.send(embed=error_embed("Invalid mode", "Use `on` or `off`."))
            return

        settings = await self.get_eventlog_settings(ctx.guild)
        settings["voice_events"] = enabled
        await self.save_eventlog_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Voice event logging updated", f"Voice event logging is now `{'on' if enabled else 'off'}`."))

    @eventlogs.command(name="bots")
    async def eventlogs_bots(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = self.event_bool(mode)

        if enabled is None:
            await ctx.send(embed=error_embed("Invalid mode", "Use `on` or `off`."))
            return

        settings = await self.get_eventlog_settings(ctx.guild)
        settings["include_bots"] = enabled
        await self.save_eventlog_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Bot event logging updated", f"Bot events are now `{'included' if enabled else 'ignored'}`."))

    @eventlogs.command(name="ignore")
    async def eventlogs_ignore(self, ctx, channel: discord.TextChannel | None = None):
        if not await require_admin(ctx):
            return

        channel = channel or ctx.channel
        settings = await self.get_eventlog_settings(ctx.guild)
        ignored = settings.setdefault("ignored_channel_ids", [])

        if channel.id not in ignored:
            ignored.append(channel.id)

        await self.save_eventlog_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Channel ignored", f"{channel.mention} will be ignored by event logs."))

    @eventlogs.command(name="unignore")
    async def eventlogs_unignore(self, ctx, channel: discord.TextChannel | None = None):
        if not await require_admin(ctx):
            return

        channel = channel or ctx.channel
        settings = await self.get_eventlog_settings(ctx.guild)
        ignored = settings.setdefault("ignored_channel_ids", [])
        settings["ignored_channel_ids"] = [cid for cid in ignored if int(cid) != channel.id]

        await self.save_eventlog_settings(ctx.guild, settings)

        await ctx.send(embed=ok_embed("Channel unignored", f"{channel.mention} will now be logged."))

    @eventlogs.command(name="test")
    async def eventlogs_test(self, ctx):
        if not await require_admin(ctx):
            return

        await self.send_event_log(
            ctx.guild,
            "audit_log",
            "Discord Event Logger Test",
            "This is a test Discord-native event log.",
            fields={
                "Triggered from": ctx.channel.mention,
                "Triggered by": ctx.author.mention,
            },
            color=discord.Color.green(),
        )

        await ctx.send(embed=ok_embed("Event log test sent"))

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if not getattr(message, "guild", None):
            return

        if not await self.should_log_event(message.guild, channel=message.channel, user=message.author):
            return

        settings = await self.get_eventlog_settings(message.guild)
        fields = {
            "Channel": self.channel_display(message.channel),
            "Author": f"{message.author} (`{message.author.id}`)",
            "Message ID": str(message.id),
        }

        if settings.get("message_content", False) and getattr(message, "content", None):
            fields["Content"] = message.content

        await self.send_event_log(
            message.guild,
            "message_log",
            "Message Deleted",
            f"A message was deleted in {self.channel_display(message.channel)}.",
            fields=fields,
            color=discord.Color.orange(),
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):
        if not messages:
            return

        guild = getattr(messages[0], "guild", None)
        channel = getattr(messages[0], "channel", None)

        if not guild:
            return

        if not await self.should_log_event(guild, channel=channel):
            return

        await self.send_event_log(
            guild,
            "message_log",
            "Bulk Message Delete",
            f"`{len(messages)}` messages were bulk deleted.",
            fields={
                "Channel": self.channel_display(channel),
                "Message count": str(len(messages)),
            },
            color=discord.Color.orange(),
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not getattr(before, "guild", None):
            return

        if before.content == after.content:
            return

        if not await self.should_log_event(before.guild, channel=before.channel, user=before.author):
            return

        settings = await self.get_eventlog_settings(before.guild)
        fields = {
            "Channel": self.channel_display(before.channel),
            "Author": f"{before.author} (`{before.author.id}`)",
            "Message ID": str(before.id),
            "Jump": getattr(after, "jump_url", "Unavailable"),
        }

        if settings.get("message_content", False):
            fields["Before"] = before.content or "Empty"
            fields["After"] = after.content or "Empty"

        await self.send_event_log(
            before.guild,
            "message_log",
            "Message Edited",
            f"A message was edited in {self.channel_display(before.channel)}.",
            fields=fields,
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not await self.should_log_event(member.guild, user=member):
            return

        await self.send_event_log(
            member.guild,
            "member_log",
            "Member Joined",
            f"{member.mention} joined the server.",
            fields={
                "User": f"{member} (`{member.id}`)",
                "Account created": f"<t:{int(member.created_at.timestamp())}:R>",
            },
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if not await self.should_log_event(member.guild, user=member):
            return

        await self.send_event_log(
            member.guild,
            "member_log",
            "Member Left",
            f"{member} left the server.",
            fields={
                "User": f"{member} (`{member.id}`)",
            },
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if not await self.should_log_event(after.guild, user=after):
            return

        changes = {}

        if before.nick != after.nick:
            changes["Nickname"] = f"`{before.nick}` → `{after.nick}`"

        before_roles = {role.id for role in before.roles}
        after_roles = {role.id for role in after.roles}

        added = [role.mention for role in after.roles if role.id not in before_roles and role != after.guild.default_role]
        removed = [role.mention for role in before.roles if role.id not in after_roles and role != after.guild.default_role]

        if added:
            changes["Roles added"] = " ".join(added)

        if removed:
            changes["Roles removed"] = " ".join(removed)

        if not changes:
            return

        changes["Member"] = f"{after.mention} (`{after.id}`)"

        await self.send_event_log(
            after.guild,
            "member_log",
            "Member Updated",
            f"{after.mention} was updated.",
            fields=changes,
            color=discord.Color.blue(),
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        if not await self.should_log_event(guild, user=user):
            return

        await self.send_event_log(
            guild,
            "security_log",
            "Member Banned",
            f"{user} was banned.",
            fields={"User": f"{user} (`{user.id}`)"},
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        if not await self.should_log_event(guild, user=user):
            return

        await self.send_event_log(
            guild,
            "security_log",
            "Member Unbanned",
            f"{user} was unbanned.",
            fields={"User": f"{user} (`{user.id}`)"},
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        if not await self.should_log_event(role.guild):
            return

        await self.send_event_log(
            role.guild,
            "audit_log",
            "Role Created",
            f"Role {role.mention} was created.",
            fields={"Role ID": str(role.id), "Permissions": str(role.permissions.value)},
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        if not await self.should_log_event(role.guild):
            return

        await self.send_event_log(
            role.guild,
            "audit_log",
            "Role Deleted",
            f"Role `{role.name}` was deleted.",
            fields={"Role ID": str(role.id)},
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        if not await self.should_log_event(after.guild):
            return

        changes = {}

        if before.name != after.name:
            changes["Name"] = f"`{before.name}` → `{after.name}`"

        if before.permissions.value != after.permissions.value:
            changes["Permissions"] = f"`{before.permissions.value}` → `{after.permissions.value}`"

        if before.position != after.position:
            changes["Position"] = f"`{before.position}` → `{after.position}`"

        if not changes:
            return

        await self.send_event_log(
            after.guild,
            "audit_log",
            "Role Updated",
            f"Role {after.mention} was updated.",
            fields=changes,
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if not await self.should_log_event(channel.guild, channel=channel):
            return

        await self.send_event_log(
            channel.guild,
            "audit_log",
            "Channel Created",
            f"{self.channel_display(channel)} was created.",
            fields={
                "Name": getattr(channel, "name", "Unknown"),
                "Type": str(getattr(channel, "type", "unknown")),
                "Category": getattr(getattr(channel, "category", None), "name", "No category"),
            },
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not await self.should_log_event(channel.guild, channel=channel):
            return

        await self.send_event_log(
            channel.guild,
            "audit_log",
            "Channel Deleted",
            f"`{getattr(channel, 'name', 'unknown')}` was deleted.",
            fields={
                "Type": str(getattr(channel, "type", "unknown")),
                "Category": getattr(getattr(channel, "category", None), "name", "No category"),
            },
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if not await self.should_log_event(after.guild, channel=after):
            return

        changes = {}

        if before.name != after.name:
            changes["Name"] = f"`{before.name}` → `{after.name}`"

        before_cat = getattr(getattr(before, "category", None), "name", "No category")
        after_cat = getattr(getattr(after, "category", None), "name", "No category")

        if before_cat != after_cat:
            changes["Category"] = f"`{before_cat}` → `{after_cat}`"

        if getattr(before, "position", None) != getattr(after, "position", None):
            changes["Position"] = f"`{getattr(before, 'position', None)}` → `{getattr(after, 'position', None)}`"

        if len(getattr(before, "overwrites", {})) != len(getattr(after, "overwrites", {})):
            changes["Permission overwrites"] = f"`{len(before.overwrites)}` → `{len(after.overwrites)}`"

        if not changes:
            return

        await self.send_event_log(
            after.guild,
            "audit_log",
            "Channel Updated",
            f"{self.channel_display(after)} was updated.",
            fields=changes,
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        if not await self.should_log_event(thread.guild, channel=thread):
            return

        await self.send_event_log(
            thread.guild,
            "audit_log",
            "Thread Created",
            f"Thread `{thread.name}` was created.",
            fields={"Parent": self.channel_display(getattr(thread, "parent", None))},
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_thread_delete(self, thread):
        if not await self.should_log_event(thread.guild, channel=thread):
            return

        await self.send_event_log(
            thread.guild,
            "audit_log",
            "Thread Deleted",
            f"Thread `{thread.name}` was deleted.",
            fields={"Parent": self.channel_display(getattr(thread, "parent", None))},
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_thread_update(self, before, after):
        if not await self.should_log_event(after.guild, channel=after):
            return

        changes = {}

        if before.name != after.name:
            changes["Name"] = f"`{before.name}` → `{after.name}`"

        if getattr(before, "archived", None) != getattr(after, "archived", None):
            changes["Archived"] = f"`{before.archived}` → `{after.archived}`"

        if not changes:
            return

        await self.send_event_log(
            after.guild,
            "audit_log",
            "Thread Updated",
            f"Thread `{after.name}` was updated.",
            fields=changes,
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        guild = invite.guild
        if not guild:
            return

        if not await self.should_log_event(guild, channel=invite.channel, user=invite.inviter):
            return

        await self.send_event_log(
            guild,
            "security_log",
            "Invite Created",
            f"Invite `{invite.code}` was created.",
            fields={
                "Channel": self.channel_display(invite.channel),
                "Inviter": str(invite.inviter) if invite.inviter else "Unknown",
                "Max uses": str(invite.max_uses),
            },
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        guild = invite.guild
        if not guild:
            return

        if not await self.should_log_event(guild, channel=invite.channel):
            return

        await self.send_event_log(
            guild,
            "security_log",
            "Invite Deleted",
            f"Invite `{invite.code}` was deleted.",
            fields={"Channel": self.channel_display(invite.channel)},
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        if not await self.should_log_event(guild):
            return

        if len(before) == len(after):
            return

        await self.send_event_log(
            guild,
            "audit_log",
            "Emoji List Updated",
            "The server emoji list changed.",
            fields={
                "Before": str(len(before)),
                "After": str(len(after)),
            },
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not await self.should_log_event(member.guild, user=member, voice=True):
            return

        before_channel = before.channel
        after_channel = after.channel

        if before_channel == after_channel:
            return

        if before_channel is None and after_channel is not None:
            action = "Voice Joined"
            desc = f"{member.mention} joined {self.channel_display(after_channel)}."
        elif before_channel is not None and after_channel is None:
            action = "Voice Left"
            desc = f"{member.mention} left {self.channel_display(before_channel)}."
        else:
            action = "Voice Moved"
            desc = f"{member.mention} moved voice channels."

        await self.send_event_log(
            member.guild,
            "member_log",
            action,
            desc,
            fields={
                "Member": f"{member} (`{member.id}`)",
                "Before": self.channel_display(before_channel),
                "After": self.channel_display(after_channel),
            },
            color=discord.Color.blue(),
        )


    def access_model(self) -> list[dict]:
        return [
            {
                "family": "core",
                "name": "Core Config / Dangerous Settings",
                "commands": "!mcore config, token, routes, autoroute, logs, alerts, notify, eventlogs, capabilities",
                "capabilities": ["core_admin"],
                "notes": "Full bot configuration. Founder/Administrator only.",
            },
            {
                "family": "management",
                "name": "Management Overview",
                "commands": "management summaries, customer health, business overview",
                "capabilities": ["management_view"],
                "notes": "Directors/executives/founder only.",
            },
            {
                "family": "finance",
                "name": "Finance View",
                "commands": "finance summaries, financial overview",
                "capabilities": ["finance_view"],
                "notes": "Director/executive finance visibility.",
            },
            {
                "family": "support",
                "name": "General Support",
                "commands": "general support tickets/customer help",
                "capabilities": ["general_support", "support_lead"],
                "notes": "Support Agent and Support Lead only.",
            },
            {
                "family": "support_lead",
                "name": "Support Lead / Escalations",
                "commands": "support escalations, assignment overview",
                "capabilities": ["support_lead"],
                "notes": "Support Lead only.",
            },
            {
                "family": "billing",
                "name": "Billing Support",
                "commands": "billing, invoices, payments, refunds, chargebacks",
                "capabilities": ["billing_support", "finance_view"],
                "notes": "Billing Support or finance/management visibility.",
            },
            {
                "family": "technical_support",
                "name": "Technical Support",
                "commands": "API help, install help, troubleshooting, system errors support",
                "capabilities": ["technical_support", "support_lead"],
                "notes": "Technical Support and Support Lead only.",
            },
            {
                "family": "security_support",
                "name": "Security Support",
                "commands": "security reports, suspicious activity, account compromise",
                "capabilities": ["security_support", "security_admin"],
                "notes": "Security Support or Security Admin only.",
            },
            {
                "family": "security_admin",
                "name": "Security Admin",
                "commands": "security admin checks and high-risk security visibility",
                "capabilities": ["security_admin"],
                "notes": "Security Admin only.",
            },
            {
                "family": "incident",
                "name": "Incident Response",
                "commands": "incident checks, urgent security/production issues",
                "capabilities": ["incident_response", "security_admin", "production_access", "management_view"],
                "notes": "Incident Response, Security Admin, production owners, or management.",
            },
            {
                "family": "audit",
                "name": "Audit Review",
                "commands": "audit logs, high-risk audit review",
                "capabilities": ["audit_review", "security_admin", "management_view"],
                "notes": "Audit Reviewer, Security Admin, or management.",
            },
            {
                "family": "development_read",
                "name": "Development Read",
                "commands": "general development/status visibility",
                "capabilities": ["development_read"],
                "notes": "Developer, Lead Developer, QA Tester, Release Manager.",
            },
            {
                "family": "backend",
                "name": "Backend Access",
                "commands": "backend/API/module checks",
                "capabilities": ["backend_access"],
                "notes": "Developer and Lead Developer.",
            },
            {
                "family": "production",
                "name": "Production Access",
                "commands": "production diagnostics, system health, infrastructure",
                "capabilities": ["production_access", "infrastructure_admin"],
                "notes": "Lead Developer and Infrastructure Admin.",
            },
            {
                "family": "release",
                "name": "Release Manager",
                "commands": "release engine, staging, release notes, production releases",
                "capabilities": ["release_manager"],
                "notes": "Release Manager only.",
            },
            {
                "family": "qa",
                "name": "QA / Testing",
                "commands": "testing, QA, bug validation, staging checks",
                "capabilities": ["qa_testing"],
                "notes": "QA Tester only.",
            },
            {
                "family": "design",
                "name": "Design / Assets",
                "commands": "design/assets/UI related checks",
                "capabilities": ["design_access"],
                "notes": "Designer only.",
            },
            {
                "family": "discord_systems",
                "name": "Discord Systems",
                "commands": "Discord integration/system checks",
                "capabilities": ["discord_systems", "infrastructure_admin"],
                "notes": "Developer/Lead Developer/Infrastructure Admin.",
            },
            {
                "family": "roblox_systems",
                "name": "Roblox Systems",
                "commands": "Roblox integration/system checks",
                "capabilities": ["roblox_systems", "infrastructure_admin"],
                "notes": "Developer/Lead Developer/Infrastructure Admin.",
            },
            {
                "family": "automation",
                "name": "Automation / Workers",
                "commands": "automation, workers, failed runs",
                "capabilities": ["automation_access", "infrastructure_admin"],
                "notes": "Lead Developer or Infrastructure Admin.",
            },
        ]

    def role_is_a_real_access_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        if role == guild.default_role:
            return False

        if role.managed:
            return False

        if role.name.lower().strip() in ["bots", "bot"]:
            return False

        if self.is_separator_role(role):
            return False

        return True

    def role_has_capability(self, role: discord.Role, caps: dict[str, list[int]], capability: str) -> bool:
        return role.id in [int(x) for x in caps.get(self.route_slug(capability), [])]

    def role_capabilities(self, role: discord.Role, caps: dict[str, list[int]]) -> list[str]:
        found = []

        for cap in sorted(caps.keys()):
            if self.role_has_capability(role, caps, cap):
                found.append(cap)

        return found

    def role_can_access_family(self, role: discord.Role, family_row: dict, caps: dict[str, list[int]]) -> bool:
        # Explicit core_admin capability is the only full bot override.
        if self.role_has_capability(role, caps, "core_admin"):
            return True

        for capability in family_row.get("capabilities", []):
            if self.role_has_capability(role, caps, capability):
                return True

        return False

    @mcore.group(name="access", invoke_without_command=True)
    async def access(self, ctx):
        """Audit Mattis Systems command access."""
        if not await require_admin(ctx):
            return

        lines = []

        for row in self.access_model():
            lines.append(
                f"**{row['name']}**\n"
                f"Family: `{row['family']}`\n"
                f"Commands: `{row['commands']}`\n"
                f"Required capabilities: `{', '.join(row['capabilities'])}`\n"
                f"Notes: {row['notes']}\n"
            )

        await self.send_paginated(ctx, "Mattis Capability Access Model", lines)

    @access.command(name="matrix")
    async def access_matrix(self, ctx):
        """Show role access based only on saved capabilities."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)
        lines = []

        for role in sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True):
            if not self.role_is_a_real_access_role(ctx.guild, role):
                continue

            families = []

            for row in self.access_model():
                if self.role_can_access_family(role, row, caps):
                    families.append(row["family"])

            if families:
                lines.append(f"{role.mention} → `{', '.join(families)}`")
            else:
                lines.append(f"{role.mention} → `no bot access`")

        await self.send_paginated(
            ctx,
            "Capability Access Matrix",
            lines,
            empty="No access roles detected.",
        )

    @access.command(name="families")
    async def access_families(self, ctx):
        """List access families."""
        if not await require_admin(ctx):
            return

        lines = []

        for row in self.access_model():
            lines.append(
                f"`{row['family']}` — **{row['name']}**\n"
                f"Requires: `{', '.join(row['capabilities'])}`"
            )

        await self.send_paginated(ctx, "Access Families", lines)

    @access.command(name="family")
    async def access_family(self, ctx, family: str):
        """Show who can access one command family."""
        if not await require_admin(ctx):
            return

        family = self.route_slug(family)
        rows = {row["family"]: row for row in self.access_model()}

        if family not in rows:
            await ctx.send(embed=error_embed(
                "Unknown family",
                f"Use one of: `{', '.join(rows.keys())}`"
            ))
            return

        caps = await self.saved_capabilities(ctx.guild)
        row = rows[family]

        lines = [
            f"Family: `{family}`",
            f"Name: **{row['name']}**",
            f"Commands: `{row['commands']}`",
            f"Required capabilities: `{', '.join(row['capabilities'])}`",
            "",
            "**Roles with access:**",
        ]

        found = False

        for role in sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True):
            if not self.role_is_a_real_access_role(ctx.guild, role):
                continue

            if self.role_can_access_family(role, row, caps):
                lines.append(f"• {role.mention}")
                found = True

        if not found:
            lines.append("No roles detected.")

        await self.send_paginated(ctx, f"Access Family: {family}", lines)

    @access.command(name="check")
    async def access_check(self, ctx, role: discord.Role):
        """Check what a role can access."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)
        role_caps = self.role_capabilities(role, caps)

        lines = [
            f"Role: {role.mention}",
            f"Position: `{role.position}`",
            f"Separator/header role: `{'yes' if self.is_separator_role(role) else 'no'}`",
            "",
            "**Capabilities:**",
            "`" + (", ".join(role_caps) if role_caps else "none") + "`",
            "",
            "**Access families:**",
        ]

        for row in self.access_model():
            allowed = self.role_can_access_family(role, row, caps)
            lines.append(f"{'✅' if allowed else '❌'} `{row['family']}` — {row['name']}")

        await self.send_paginated(ctx, "Role Access Check", lines)


    @mcore.command(name="routecheck")
    async def routecheck(self, ctx):
        """Check saved routes against current Discord channels."""
        if not await require_admin(ctx):
            return

        saved = await self.saved_routes(ctx.guild)
        live_routes, _ = self.build_exact_routes(ctx.guild)

        lines = []

        missing_channels = 0
        exact_missing = 0

        for key in sorted(saved.keys()):
            cid = saved[key]
            channel = ctx.guild.get_channel(cid)

            if not channel:
                missing_channels += 1
                lines.append(f"❌ `{key}` → missing channel `{cid}`")
                continue

            if key not in live_routes:
                exact_missing += 1
                lines.append(f"ℹ️ `{key}` → {channel.mention} saved, but not an exact category route key")

        lines.insert(0, f"Saved routes: `{len(saved)}`")
        lines.insert(1, f"Missing channels: `{missing_channels}`")
        lines.insert(2, f"Non-exact/custom keys: `{exact_missing}`")
        lines.insert(3, "")

        if len(lines) == 4:
            lines.append("✅ Saved routes look healthy.")

        await self.send_paginated(
            ctx,
            "Route Check",
            lines,
            empty="No saved routes found.",
        )

    @mcore.command(name="suggest")
    async def suggest(self, ctx):
        """Suggest next safe setup steps."""
        if not await require_admin(ctx):
            return

        sections = self.parse_role_sections(ctx.guild)
        saved = await self.saved_sections(ctx.guild)

        e = embed("Suggested Systems Setup")
        e.description = "Read-only suggestions. Nothing is changed automatically."

        e.add_field(
            name="Detected role groups",
            value="\n".join(f"• {name} — {len(ids)} roles" for name, ids in sections.items()) or "No role groups detected.",
            inline=False,
        )
        e.add_field(
            name="Saved role groups",
            value="\n".join(f"• {name} — {len(ids)} roles" for name, ids in saved.items()) or "No role groups saved yet.",
            inline=False,
        )
        e.add_field(
            name="Next",
            value="Run `!mcore importgroups preview`, then `!mcore importgroups apply` if it looks correct.",
            inline=False,
        )

        await ctx.send(embed=e)

    @mcore.command(name="diagnostics")
    async def diagnostics(self, ctx):
        """Run Mattis Systems diagnostics."""
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/status")

        e = embed("Mattis Systems Diagnostics")
        e.add_field(name="Bot latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        e.add_field(name="API", value=f"HTTP {status}", inline=True)
        e.add_field(name="Response", value=fmt_payload(payload)[:1024], inline=False)

        await ctx.send(embed=e)

    @mcore.command(name="apiget")
    @commands.is_owner()
    async def apiget(self, ctx, path: str):
        """Owner-only raw API GET test."""
        status, payload = await request_json(self.bot, "GET", path)
        await ctx.send(embed=embed(f"GET {path} → {status}", fmt_payload(payload)))
