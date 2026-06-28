from __future__ import annotations

import re
import time
import discord
from redbot.core import commands

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
