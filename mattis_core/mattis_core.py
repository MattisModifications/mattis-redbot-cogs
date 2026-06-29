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


    def b5_now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    async def b5_get_api_config(self, guild):
        api_url = "https://api.mattisproductions.com"
        api_token = None

        if hasattr(self, "doctor_get_api_config_value"):
            try:
                found_url = await self.doctor_get_api_config_value(guild, [
                    "api_url",
                    "api_base_url",
                    "mattis_api_url",
                    "backend_url",
                ])

                found_token = await self.doctor_get_api_config_value(guild, [
                    "api_token",
                    "doctor_api_token",
                    "bot_api_token",
                    "mattis_api_token",
                    "mattis_token",
                    "api_key",
                ])

                if found_url:
                    api_url = str(found_url)

                if found_token:
                    api_token = str(found_token)
            except Exception:
                pass

        return api_url.rstrip("/"), api_token

    async def b5_http_get(self, guild, endpoint: str, timeout_seconds: int = 10) -> dict:
        import aiohttp
        import time

        api_url, api_token = await self.b5_get_api_config(guild)

        endpoint = endpoint if endpoint.startswith("/") else "/" + endpoint
        url = api_url + endpoint

        headers = {}

        if api_token:
            token = str(api_token).strip()
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"

        started = time.perf_counter()

        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    text = await resp.text()
                    elapsed_ms = int((time.perf_counter() - started) * 1000)

                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        payload = None

                    return {
                        "ok": 200 <= resp.status < 300,
                        "status": resp.status,
                        "endpoint": endpoint,
                        "url": url,
                        "elapsed_ms": elapsed_ms,
                        "payload": payload,
                        "text_preview": str(text or "")[:300],
                        "error": "",
                    }

        except Exception as e:
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            return {
                "ok": False,
                "status": 0,
                "endpoint": endpoint,
                "url": url,
                "elapsed_ms": elapsed_ms,
                "payload": None,
                "text_preview": "",
                "error": f"{type(e).__name__}: {e}",
            }

    def b5_core_endpoints(self) -> list[str]:
        return [
            "/bot/support/critical",
            "/bot/support/unassigned",
            "/bot/billing/failed",
            "/bot/billing/pastdue",
            "/bot/audit/highrisk",
            "/bot/security/risks",
            "/bot/security/suspicious",
            "/bot/automation/failed",
            "/bot/discord/broken",
            "/bot/roblox/broken",
            "/bot/incidents",
        ]

    def b5_optional_endpoints(self) -> list[str]:
        return [
            "/health",
            "/api/health",
            "/bot/status",
            "/bot/backups/status",
            "/bot/backup/status",
        ]

    async def b5_check_endpoints(self, guild, include_optional: bool = False) -> list[dict]:
        endpoints = self.b5_core_endpoints()

        if include_optional:
            endpoints = endpoints + self.b5_optional_endpoints()

        results = []

        for endpoint in endpoints:
            results.append(await self.b5_http_get(guild, endpoint, timeout_seconds=10))

        return results

    async def b5_get_prod_state(self, guild) -> dict:
        cfg = await get_core_config(self.bot)
        lifecycle = await cfg.guild(guild).alert_lifecycle()
        lifecycle = lifecycle or {}

        state = lifecycle.get("production_state") or {}

        if not isinstance(state, dict):
            state = {}

        state.setdefault("release_freeze", False)
        state.setdefault("release_freeze_reason", "")
        state.setdefault("release_freeze_by", "")
        state.setdefault("release_freeze_at", "")
        state.setdefault("snapshots", [])

        return state

    async def b5_set_prod_state(self, guild, state: dict):
        cfg = await get_core_config(self.bot)
        lifecycle = await cfg.guild(guild).alert_lifecycle()
        lifecycle = lifecycle or {}
        lifecycle["production_state"] = state
        await cfg.guild(guild).alert_lifecycle.set(lifecycle)

    def b5_endpoint_summary(self, checks: list[dict]) -> dict:
        total = len(checks)
        ok = sum(1 for x in checks if x.get("ok"))
        failed = total - ok

        slow = [x for x in checks if int(x.get("elapsed_ms", 0) or 0) >= 2500]

        return {
            "total": total,
            "ok": ok,
            "failed": failed,
            "slow": len(slow),
            "healthy": failed == 0,
        }

    def b5_score_readiness(self, checks: list[dict], security: dict, prod_state: dict) -> dict:
        summary = self.b5_endpoint_summary(checks)

        score = 100
        blockers = []
        warnings = []

        if summary["failed"]:
            score -= min(60, summary["failed"] * 12)
            blockers.append(f"{summary['failed']} core API endpoint(s) failed.")

        if summary["slow"]:
            score -= min(20, summary["slow"] * 5)
            warnings.append(f"{summary['slow']} endpoint(s) responded slowly.")

        highrisk_count = int(security.get("highrisk_events", 0) or 0)
        secret_events = int(security.get("secret_events", 0) or 0)

        if highrisk_count >= 20:
            score -= 10
            warnings.append(f"{highrisk_count} high-risk audit event(s) currently returned by the API.")

        if secret_events:
            score -= 10
            warnings.append(f"{secret_events} secret/token/webhook related audit event(s) should be reviewed.")

        if prod_state.get("release_freeze"):
            score -= 25
            blockers.append("Release freeze is currently enabled.")

        if score >= 90 and not blockers:
            label = "Production ready"
        elif score >= 75 and not blockers:
            label = "Mostly ready"
        elif score >= 55:
            label = "Needs review"
        else:
            label = "Not ready"

        return {
            "score": max(0, score),
            "label": label,
            "blockers": blockers,
            "warnings": warnings,
        }

    async def b5_security_summary(self, guild) -> dict:
        result = {
            "highrisk_events": 0,
            "secret_events": 0,
            "actors": [],
            "categories": [],
            "top_reasons": [],
        }

        try:
            if not hasattr(self, "b4a_fetch_highrisk_events"):
                return result

            data = await self.b4a_fetch_highrisk_events(guild)
            events = data.get("events") or []

            result["highrisk_events"] = len(events)

            classified = [self.b4a_classify_log_event(e) for e in events]

            secret_events = []

            for item in classified:
                reason = str(item.get("reason", "")).lower()
                category = str(item.get("category", "")).lower()

                if any(x in reason or x in category for x in ["secret", "token", "key", "webhook"]):
                    secret_events.append(item)

            result["secret_events"] = len(secret_events)

            def group(field):
                counts = {}
                for item in classified:
                    key = str(item.get(field) or "Unknown")
                    counts[key] = counts.get(key, 0) + 1
                return sorted(counts.items(), key=lambda x: x[1], reverse=True)

            result["actors"] = group("actor")[:5]
            result["categories"] = group("category")[:8]
            result["top_reasons"] = group("reason")[:8]

        except Exception:
            pass

        return result

    async def b5_build_readiness(self, guild, include_optional: bool = False) -> dict:
        checks = await self.b5_check_endpoints(guild, include_optional=include_optional)
        security = await self.b5_security_summary(guild)
        prod_state = await self.b5_get_prod_state(guild)
        readiness = self.b5_score_readiness(checks, security, prod_state)

        return {
            "created_at": self.b5_now_iso(),
            "checks": checks,
            "security": security,
            "prod_state": prod_state,
            "readiness": readiness,
        }

    def b5_check_lines(self, checks: list[dict]) -> list[str]:
        lines = []

        for check in checks:
            emoji = "✅" if check.get("ok") else "❌"
            status = check.get("status") or 0
            elapsed = check.get("elapsed_ms") or 0
            endpoint = check.get("endpoint")

            if check.get("ok"):
                lines.append(f"{emoji} `{endpoint}` — HTTP `{status}` — `{elapsed}ms`")
            else:
                error = check.get("error") or check.get("text_preview") or "failed"
                lines.append(f"{emoji} `{endpoint}` — HTTP `{status}` — `{elapsed}ms` — {error[:180]}")

        return lines

    def b5_readiness_lines(self, data: dict) -> list[str]:
        readiness = data.get("readiness") or {}
        summary = self.b5_endpoint_summary(data.get("checks") or {})
        security = data.get("security") or {}
        prod_state = data.get("prod_state") or {}

        lines = [
            f"Generated: `{data.get('created_at')}`",
            f"Readiness: `{readiness.get('label')}`",
            f"Score: `{readiness.get('score')}/100`",
            "",
            "**API endpoints:**",
            f"Total: `{summary['total']}`",
            f"Healthy: `{summary['ok']}`",
            f"Failed: `{summary['failed']}`",
            f"Slow: `{summary['slow']}`",
            "",
            "**Release safety:**",
            f"Release freeze: `{'on' if prod_state.get('release_freeze') else 'off'}`",
        ]

        if prod_state.get("release_freeze_reason"):
            lines.append(f"Freeze reason: {prod_state.get('release_freeze_reason')}")

        lines.extend([
            "",
            "**Security/audit feed:**",
            f"High-risk events: `{security.get('highrisk_events', 0)}`",
            f"Secret/token/webhook events: `{security.get('secret_events', 0)}`",
            "",
        ])

        blockers = readiness.get("blockers") or []
        warnings = readiness.get("warnings") or []

        lines.append("**Blockers:**")
        if blockers:
            for blocker in blockers:
                lines.append(f"- 🚫 {blocker}")
        else:
            lines.append("- None")

        lines.extend(["", "**Warnings:**"])
        if warnings:
            for warning in warnings:
                lines.append(f"- ⚠️ {warning}")
        else:
            lines.append("- None")

        lines.extend([
            "",
            "**Core endpoint results:**",
        ])

        lines.extend(self.b5_check_lines(data.get("checks") or []))

        return lines

    async def b5_store_snapshot(self, guild, data: dict):
        state = await self.b5_get_prod_state(guild)
        snapshots = state.get("snapshots") or []

        compact = {
            "created_at": data.get("created_at"),
            "score": data.get("readiness", {}).get("score"),
            "label": data.get("readiness", {}).get("label"),
            "failed": self.b5_endpoint_summary(data.get("checks") or []).get("failed"),
            "highrisk_events": data.get("security", {}).get("highrisk_events"),
            "secret_events": data.get("security", {}).get("secret_events"),
            "release_freeze": data.get("prod_state", {}).get("release_freeze"),
        }

        snapshots.append(compact)
        state["snapshots"] = snapshots[-25:]

        await self.b5_set_prod_state(guild, state)

    def b5_report_text(self, data: dict) -> str:
        lines = [
            "Mattis CMS | Systems",
            "Production Readiness Report",
            "=" * 40,
            "",
        ]

        lines.extend([x.replace("**", "") for x in self.b5_readiness_lines(data)])

        lines.extend([
            "",
            "Recommended Actions",
            "-" * 40,
        ])

        readiness = data.get("readiness") or {}
        blockers = readiness.get("blockers") or []
        warnings = readiness.get("warnings") or []

        if blockers:
            for item in blockers:
                lines.append(f"- Resolve blocker: {item}")

        if warnings:
            for item in warnings:
                lines.append(f"- Review warning: {item}")

        if not blockers and not warnings:
            lines.append("- No immediate readiness blockers detected.")

        lines.extend([
            "- Run `!mcore doctor` and review any failures.",
            "- Run `!mcore alerts ops` and confirm no unresolved critical alerts.",
            "- Run `!mcore logs executive` and confirm high-risk audit events are expected.",
            "- Confirm backups are working outside Discord on the VPS/DB layer.",
        ])

        return "\n".join(lines)

    @mcore.group(name="prod", invoke_without_command=True)
    async def prod(self, ctx):
        """Production readiness and service health control centre."""
        if not await require_admin(ctx):
            return

        lines = [
            "**Mattis Production Control Centre**",
            "",
            "`!mcore prod health` — quick production health check",
            "`!mcore prod api` — API/base configuration and basic reachability",
            "`!mcore prod endpoints` — check all core API bot endpoints",
            "`!mcore prod readiness` — readiness score and blockers",
            "`!mcore prod preflight <release>` — release preflight gate",
            "`!mcore prod freeze on/off/status <reason>` — release freeze control",
            "`!mcore prod backup` — backup readiness/status intelligence",
            "`!mcore prod security` — security/audit readiness summary",
            "`!mcore prod risks` — production risks list",
            "`!mcore prod checklist` — production checklist",
            "`!mcore prod snapshot` — save readiness snapshot",
            "`!mcore prod snapshots` — list saved snapshots",
            "`!mcore prod report` — full report in Discord",
            "`!mcore prod export` — export report as .txt",
        ]

        await self.send_paginated(ctx, "Production Control", lines)

    @prod.command(name="api")
    async def prod_api(self, ctx):
        """Show API configuration and basic reachability."""
        if not await require_admin(ctx):
            return

        api_url, api_token = await self.b5_get_api_config(ctx.guild)
        check = await self.b5_http_get(ctx.guild, "/bot/audit/highrisk")

        lines = [
            f"API URL: `{api_url}`",
            f"API token configured: `{'yes' if api_token else 'no'}`",
            "",
            "**Reachability:**",
        ]

        lines.extend(self.b5_check_lines([check]))

        await self.send_paginated(ctx, "Production API", lines)

    @prod.command(name="health")
    async def prod_health(self, ctx):
        """Run a quick production health check."""
        if not await require_admin(ctx):
            return

        checks = []

        for endpoint in ["/bot/audit/highrisk", "/bot/support/critical", "/bot/billing/failed", "/bot/incidents"]:
            checks.append(await self.b5_http_get(ctx.guild, endpoint))

        summary = self.b5_endpoint_summary(checks)

        lines = [
            f"Healthy: `{summary['healthy']}`",
            f"Checked: `{summary['total']}`",
            f"OK: `{summary['ok']}`",
            f"Failed: `{summary['failed']}`",
            f"Slow: `{summary['slow']}`",
            "",
        ]

        lines.extend(self.b5_check_lines(checks))

        await self.send_paginated(ctx, "Production Health", lines)

    @prod.command(name="endpoints")
    async def prod_endpoints(self, ctx, optional: str = ""):
        """Check all core production API bot endpoints."""
        if not await require_admin(ctx):
            return

        include_optional = str(optional or "").lower() in ["optional", "all", "full", "yes", "true"]
        checks = await self.b5_check_endpoints(ctx.guild, include_optional=include_optional)
        summary = self.b5_endpoint_summary(checks)

        lines = [
            f"Core + optional: `{include_optional}`",
            f"Checked: `{summary['total']}`",
            f"OK: `{summary['ok']}`",
            f"Failed: `{summary['failed']}`",
            f"Slow: `{summary['slow']}`",
            "",
        ]

        lines.extend(self.b5_check_lines(checks))

        await self.send_paginated(ctx, "Production Endpoints", lines)

    @prod.command(name="readiness")
    async def prod_readiness(self, ctx):
        """Show production readiness score."""
        if not await require_admin(ctx):
            return

        data = await self.b5_build_readiness(ctx.guild, include_optional=False)
        lines = self.b5_readiness_lines(data)

        await self.send_paginated(ctx, "Production Readiness", lines)

    @prod.command(name="preflight")
    async def prod_preflight(self, ctx, *, release_name: str = "unnamed release"):
        """Run release preflight gate."""
        if not await require_admin(ctx):
            return

        data = await self.b5_build_readiness(ctx.guild, include_optional=False)
        readiness = data.get("readiness") or {}
        score = int(readiness.get("score", 0) or 0)
        blockers = readiness.get("blockers") or []

        allowed = score >= 75 and not blockers

        lines = [
            f"Release: `{release_name}`",
            f"Preflight result: `{'PASS' if allowed else 'BLOCKED'}`",
            f"Readiness: `{readiness.get('label')}`",
            f"Score: `{score}/100`",
            "",
        ]

        if allowed:
            lines.extend([
                "✅ Release can proceed based on current bot/API checks.",
                "",
                "**Before deploying:**",
                "- Confirm Git status is clean.",
                "- Confirm database backup exists.",
                "- Confirm rollback plan is known.",
                "- Confirm no critical alerts are unresolved.",
            ])
        else:
            lines.append("🚫 Release should not proceed until blockers/warnings are reviewed.")

        lines.extend(["", "**Readiness details:**"])
        lines.extend(self.b5_readiness_lines(data))

        await self.send_paginated(ctx, "Release Preflight", lines)

    @prod.command(name="freeze")
    async def prod_freeze(self, ctx, mode: str = "status", *, reason: str = ""):
        """Control release freeze state."""
        if not await require_admin(ctx):
            return

        state = await self.b5_get_prod_state(ctx.guild)
        mode = str(mode or "status").lower().strip()

        if mode in ["on", "enable", "enabled", "true"]:
            state["release_freeze"] = True
            state["release_freeze_reason"] = reason or "No reason provided."
            state["release_freeze_by"] = str(ctx.author)
            state["release_freeze_at"] = self.b5_now_iso()
            await self.b5_set_prod_state(ctx.guild, state)
            await ctx.send(embed=ok_embed("Release freeze enabled", f"Reason: {state['release_freeze_reason']}"))
            return

        if mode in ["off", "disable", "disabled", "false"]:
            old_reason = state.get("release_freeze_reason") or "No previous reason."
            state["release_freeze"] = False
            state["release_freeze_reason"] = reason or ""
            state["release_freeze_by"] = str(ctx.author)
            state["release_freeze_at"] = self.b5_now_iso()
            await self.b5_set_prod_state(ctx.guild, state)
            await ctx.send(embed=ok_embed("Release freeze disabled", f"Previous reason: {old_reason}"))
            return

        lines = [
            f"Release freeze: `{'on' if state.get('release_freeze') else 'off'}`",
            f"Reason: {state.get('release_freeze_reason') or 'None'}",
            f"Changed by: `{state.get('release_freeze_by') or 'Unknown'}`",
            f"Changed at: `{state.get('release_freeze_at') or 'Unknown'}`",
            "",
            "`!mcore prod freeze on <reason>`",
            "`!mcore prod freeze off <reason>`",
        ]

        await self.send_paginated(ctx, "Release Freeze", lines)

    @prod.command(name="backup")
    async def prod_backup(self, ctx):
        """Check backup readiness/status intelligence."""
        if not await require_admin(ctx):
            return

        backup_endpoints = [
            "/bot/backups/status",
            "/bot/backup/status",
            "/backups/status",
            "/health/backups",
        ]

        checks = []

        for endpoint in backup_endpoints:
            checks.append(await self.b5_http_get(ctx.guild, endpoint, timeout_seconds=8))

        found = [x for x in checks if x.get("ok")]

        lines = [
            "**Backup readiness**",
            "",
            "Discord bot can only verify backup status if the API exposes a backup endpoint.",
            "",
        ]

        if found:
            lines.append("✅ Backup status endpoint found.")
        else:
            lines.append("⚠️ No backup status endpoint responded with HTTP 2xx.")

        lines.extend([
            "",
            "**Endpoint checks:**",
        ])

        lines.extend(self.b5_check_lines(checks))

        lines.extend([
            "",
            "**Manual VPS/DB checks still required:**",
            "- Confirm Postgres backup job exists.",
            "- Confirm latest backup file exists.",
            "- Confirm backup restore has been tested.",
            "- Confirm backups are not stored only on the same server.",
            "- Confirm secrets/env files are backed up securely or reproducible.",
        ])

        await self.send_paginated(ctx, "Backup Readiness", lines)

    @prod.command(name="security")
    async def prod_security(self, ctx):
        """Show production security/audit readiness."""
        if not await require_admin(ctx):
            return

        security = await self.b5_security_summary(ctx.guild)

        lines = [
            f"High-risk audit events: `{security.get('highrisk_events', 0)}`",
            f"Secret/token/webhook events: `{security.get('secret_events', 0)}`",
            "",
            "**Top actors:**",
        ]

        for actor, count in security.get("actors", []):
            lines.append(f"- `{actor}` — `{count}`")

        lines.extend(["", "**Categories:**"])

        for category, count in security.get("categories", []):
            lines.append(f"- `{category}` — `{count}`")

        lines.extend(["", "**Top reasons:**"])

        for reason, count in security.get("top_reasons", []):
            lines.append(f"- {reason} — `{count}`")

        lines.extend([
            "",
            "**Useful commands:**",
            "`!mcore logs executive`",
            "`!mcore logs suspicious`",
            "`!mcore logs packet stripe`",
            "`!mcore alerts ops`",
            "`!mcore alerts investigate audit`",
        ])

        await self.send_paginated(ctx, "Production Security", lines)

    @prod.command(name="risks")
    async def prod_risks(self, ctx):
        """Show current production risks."""
        if not await require_admin(ctx):
            return

        data = await self.b5_build_readiness(ctx.guild, include_optional=False)
        readiness = data.get("readiness") or {}
        security = data.get("security") or {}

        lines = [
            f"Readiness: `{readiness.get('label')}`",
            f"Score: `{readiness.get('score')}/100`",
            "",
            "**Blockers:**",
        ]

        blockers = readiness.get("blockers") or []
        warnings = readiness.get("warnings") or []

        if blockers:
            for item in blockers:
                lines.append(f"- 🚫 {item}")
        else:
            lines.append("- None")

        lines.extend(["", "**Warnings:**"])

        if warnings:
            for item in warnings:
                lines.append(f"- ⚠️ {item}")
        else:
            lines.append("- None")

        lines.extend([
            "",
            "**Operational risk notes:**",
            f"- High-risk audit events: `{security.get('highrisk_events', 0)}`",
            f"- Secret/token/webhook events: `{security.get('secret_events', 0)}`",
            "- Confirm database backup and rollback manually.",
            "- Confirm Discord bot host and VPS/API host are both expected.",
        ])

        await self.send_paginated(ctx, "Production Risks", lines)

    @prod.command(name="checklist")
    async def prod_checklist(self, ctx):
        """Show production readiness checklist."""
        if not await require_admin(ctx):
            return

        lines = [
            "**Production readiness checklist**",
            "",
            "1. ☐ `!mcore prod readiness` score is acceptable.",
            "2. ☐ `!mcore prod endpoints` has no failed core API endpoints.",
            "3. ☐ `!mcore doctor` has no serious failures.",
            "4. ☐ `!mcore alerts ops` has no unresolved critical/high alerts that block release.",
            "5. ☐ `!mcore logs executive` high-risk events are expected and documented.",
            "6. ☐ Database backup exists and restore has been tested.",
            "7. ☐ `.env` secrets are correct and not pasted into Discord/GitHub.",
            "8. ☐ Stripe webhook signing has been checked after billing secret changes.",
            "9. ☐ Roblox OAuth/Open Cloud/webhook flows have been tested.",
            "10. ☐ Discord OAuth and bot command health have been tested.",
            "11. ☐ Rollback steps are known.",
            "12. ☐ Release freeze is off unless intentionally blocking.",
            "",
            "**Useful command flow:**",
            "`!mcore prod preflight release-name`",
            "`!mcore prod snapshot`",
            "`!mcore prod export`",
        ]

        await self.send_paginated(ctx, "Production Checklist", lines)

    @prod.command(name="snapshot")
    async def prod_snapshot(self, ctx):
        """Save a production readiness snapshot."""
        if not await require_admin(ctx):
            return

        data = await self.b5_build_readiness(ctx.guild, include_optional=False)
        await self.b5_store_snapshot(ctx.guild, data)

        readiness = data.get("readiness") or {}

        await ctx.send(embed=ok_embed(
            "Production snapshot saved",
            f"Readiness: `{readiness.get('label')}`\nScore: `{readiness.get('score')}/100`"
        ))

    @prod.command(name="snapshots")
    async def prod_snapshots(self, ctx):
        """List saved production readiness snapshots."""
        if not await require_admin(ctx):
            return

        state = await self.b5_get_prod_state(ctx.guild)
        snapshots = state.get("snapshots") or []

        if not snapshots:
            await ctx.send(embed=info_embed("Production Snapshots", "No readiness snapshots are saved yet."))
            return

        lines = []

        for snap in list(reversed(snapshots))[:25]:
            lines.extend([
                f"**{snap.get('created_at')}**",
                f"Readiness: `{snap.get('label')}` | Score: `{snap.get('score')}/100`",
                f"Failed endpoints: `{snap.get('failed')}` | High-risk: `{snap.get('highrisk_events')}` | Secret events: `{snap.get('secret_events')}`",
                f"Release freeze: `{'on' if snap.get('release_freeze') else 'off'}`",
                "",
            ])

        await self.send_paginated(ctx, "Production Snapshots", lines)

    @prod.command(name="report")
    async def prod_report(self, ctx):
        """Show full production readiness report."""
        if not await require_admin(ctx):
            return

        data = await self.b5_build_readiness(ctx.guild, include_optional=False)
        report = self.b5_report_text(data)
        await self.send_paginated(ctx, "Production Readiness Report", report.splitlines())

    @prod.command(name="export")
    async def prod_export(self, ctx):
        """Export production readiness report as a text file."""
        if not await require_admin(ctx):
            return

        import io
        import discord

        data = await self.b5_build_readiness(ctx.guild, include_optional=False)
        report = self.b5_report_text(data)

        fp = io.BytesIO(report.encode("utf-8"))

        await ctx.send(
            embed=ok_embed("Production report exported", "Exported current production readiness report."),
            file=discord.File(fp, filename="mattis-production-readiness-report.txt")
        )

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
        await self.alert_lifecycle_send(

            ctx.guild if 'ctx' in locals() and ctx else channel.guild,

            rule_name if 'rule_name' in locals() else rule if 'rule' in locals() else name if 'name' in locals() else 'alert',

            channel,

            content=notify_content or None,

            embed=e,

            allowed_mentions=self.notify_allowed_mentions(),

            item=payload if 'payload' in locals() else data if 'data' in locals() else {'count': count if 'count' in locals() else 1},

        ),

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



    @alerts.command(name="enrich")
    async def alerts_enrich(self, ctx):
        """Backfill current lifecycle state with Operations Alert Intelligence fields."""
        if not await require_admin(ctx):
            return

        changed, state = await self.b3a_enrich_all_alert_state(ctx.guild)

        await ctx.send(embed=ok_embed(
            "Alert intelligence enriched",
            f"Enriched `{changed}` tracked alert(s). Use `!mcore alerts ops` to view them."
        ))

    @alerts.command(name="ops")





    async def alerts_ops(self, ctx):
        """Show open operational alerts. Resolved alerts are hidden here."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild)

        open_items = [
            (alert_id, item)
            for alert_id, item in state.items()
            if self.b3d_is_open(item)
        ]

        open_items.sort(key=self.b3d_alert_sort_key)

        if not open_items:
            await ctx.send(embed=ok_embed("Operations Alerts", "No open operational alerts are currently tracked. Use `!mcore alerts resolved` to view resolved alerts."))
            return

        lines = []

        for alert_id, item in open_items[:30]:
            lines.extend(self.b3d_render_alert_lines(alert_id, item))

        await self.send_paginated(ctx, "Open Operations Alerts", lines)

    def b3b_now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def b3b_actor(self, ctx) -> str:
        try:
            return f"{ctx.author} ({ctx.author.id})"
        except Exception:
            return "Unknown"

    def b3b_add_timeline(self, item: dict, action: str, actor: str, details: str = "") -> dict:
        item = item or {}
        timeline = item.get("timeline") or []

        timeline.append({
            "at": self.b3b_now_iso(),
            "action": action,
            "actor": actor,
            "details": details or "",
        })

        item["timeline"] = timeline[-75:]
        return item


    async def b3b_save_alert_item(self, guild, alert_id: str, item: dict):
        cfg = await get_core_config(self.bot)
        lifecycle = await cfg.guild(guild).alert_lifecycle()
        lifecycle = lifecycle or {}

        state = lifecycle.get("b2_state") or lifecycle.get("state") or {}

        canonical = self.b3a_canonical_alert_key(
            alert_id,
            item.get("identity", ""),
            item.get("rule_id", ""),
            item.get("alert_id", ""),
            item.get("title", ""),
            item.get("raw_reference", ""),
        )

        item["alert_id"] = canonical
        item["rule_id"] = canonical
        item["identity"] = canonical
        item["raw_reference"] = canonical

        # Remove old key if it existed.
        if alert_id in state and alert_id != canonical:
            old_item = state.pop(alert_id)
            item = self.b3c_merge_alert_items(old_item, item)

        if canonical in state:
            item = self.b3c_merge_alert_items(state[canonical], item)

        state[canonical] = item
        lifecycle["b2_state"] = state
        lifecycle.pop("state", None)

        await cfg.guild(guild).alert_lifecycle.set(lifecycle)

    async def b3b_find_alert(self, guild, query: str):
        if hasattr(self, "b3a_find_alert_state_item"):
            return await self.b3a_find_alert_state_item(guild, query)

        return None, None

    async def b3b_try_edit_alert_message(self, guild, item: dict):
        try:
            channel_id = item.get("last_channel_id")
            message_id = item.get("last_message_id")

            if not channel_id or not message_id:
                return False

            channel = guild.get_channel(int(channel_id))
            if not channel:
                return False

            message = await channel.fetch_message(int(message_id))
            embed = await self.b3a_render_alert_embed(guild, meta=item)
            await message.edit(embed=embed)
            return True
        except Exception:
            return False

    def b3b_find_role_from_text(self, guild, text: str):
        text = str(text or "").strip()

        if not guild or not text:
            return None

        # Mention ID.
        import re
        m = re.search(r"<@&(\d+)>", text)
        if m:
            role_id = int(m.group(1))
            return guild.get_role(role_id)

        lowered = text.lower().lstrip("@").strip()

        for role in getattr(guild, "roles", []):
            if role.name.lower() == lowered:
                return role

        for role in getattr(guild, "roles", []):
            if lowered in role.name.lower():
                return role

        return None



    def b3c_merge_alert_items(self, base: dict, incoming: dict) -> dict:
        """Merge duplicate alert state safely, preserving action/timeline data."""
        base = dict(base or {})
        incoming = dict(incoming or {})

        # Prefer richer/newer operational intelligence fields.
        for key, value in incoming.items():
            if value is None:
                continue

            if key in {"timeline", "notes"}:
                continue

            if base.get(key) in [None, "", "Unknown", "unknown", 0]:
                base[key] = value
            elif key in {
                "last_seen",
                "last_posted",
                "last_channel_id",
                "last_message_id",
                "status",
                "resolved",
                "owner",
                "assigned_role_id",
                "assigned_role_name",
                "assigned_by",
                "assigned_by_id",
                "assigned_at",
                "acknowledged_by",
                "acknowledged_by_id",
                "acknowledged_at",
                "resolved_by",
                "resolved_by_id",
                "resolved_at",
                "reopened_by",
                "reopened_by_id",
                "reopened_at",
            }:
                base[key] = value

        base["post_count"] = max(int(base.get("post_count", 0) or 0), int(incoming.get("post_count", 0) or 0))
        base["suppressed_count"] = max(int(base.get("suppressed_count", 0) or 0), int(incoming.get("suppressed_count", 0) or 0))

        # Merge timeline by timestamp/action/details.
        timeline = []
        seen = set()

        for source in [base.get("timeline") or [], incoming.get("timeline") or []]:
            for entry in source:
                marker = (
                    str(entry.get("at")),
                    str(entry.get("action")),
                    str(entry.get("actor")),
                    str(entry.get("details")),
                )
                if marker not in seen:
                    seen.add(marker)
                    timeline.append(entry)

        timeline.sort(key=lambda x: str(x.get("at", "")))
        base["timeline"] = timeline[-75:]

        # Merge notes.
        notes = []
        seen_notes = set()

        for source in [base.get("notes") or [], incoming.get("notes") or []]:
            for note in source:
                marker = (
                    str(note.get("at")),
                    str(note.get("author")),
                    str(note.get("note")),
                )
                if marker not in seen_notes:
                    seen_notes.add(marker)
                    notes.append(note)

        notes.sort(key=lambda x: str(x.get("at", "")))
        base["notes"] = notes[-50:]

        return base

    async def b3c_normalize_alert_state(self, guild):
        cfg = await get_core_config(self.bot)
        lifecycle = await cfg.guild(guild).alert_lifecycle()
        lifecycle = lifecycle or {}

        old_state = lifecycle.get("b2_state") or lifecycle.get("state") or {}
        new_state = {}
        changed = 0

        for old_key, item in old_state.items():
            raw = " ".join([
                str(old_key),
                str(item.get("identity", "")),
                str(item.get("rule_id", "")),
                str(item.get("alert_id", "")),
                str(item.get("title", "")),
                str(item.get("raw_reference", "")),
                str(item.get("investigation_route", "")),
                str(item.get("route", "")),
            ])

            canonical = self.b3a_canonical_alert_key(raw)

            enriched = await self.b3a_enrich_existing_alert_item(guild, canonical, item)
            enriched["alert_id"] = canonical
            enriched["rule_id"] = canonical
            enriched["identity"] = canonical
            enriched["raw_reference"] = canonical

            if canonical != old_key:
                changed += 1

            if canonical in new_state:
                new_state[canonical] = self.b3c_merge_alert_items(new_state[canonical], enriched)
            else:
                new_state[canonical] = enriched

        lifecycle["b2_state"] = new_state
        lifecycle.pop("state", None)
        await cfg.guild(guild).alert_lifecycle.set(lifecycle)

        return changed, new_state



    async def b3d_get_alert_state(self, guild):
        """Return normalized alert lifecycle state."""
        if hasattr(self, "b3c_normalize_alert_state"):
            changed, state = await self.b3c_normalize_alert_state(guild)
            return state or {}

        cfg = await get_core_config(self.bot)
        lifecycle = await cfg.guild(guild).alert_lifecycle()
        lifecycle = lifecycle or {}
        return lifecycle.get("b2_state") or lifecycle.get("state") or {}

    def b3d_is_resolved(self, item: dict) -> bool:
        item = item or {}
        status = str(item.get("status") or "").lower()
        return bool(item.get("resolved")) or status == "resolved"

    def b3d_is_open(self, item: dict) -> bool:
        return not self.b3d_is_resolved(item)

    def b3d_status_emoji(self, item: dict) -> str:
        status = str((item or {}).get("status") or "ongoing").lower()
        severity = str((item or {}).get("severity") or "").lower()

        if status == "resolved":
            return "✅"
        if status == "acknowledged":
            return "👀"
        if status == "reopened":
            return "♻️"
        if severity == "critical":
            return "🚨"
        if severity == "high":
            return "⚠️"
        if severity == "medium":
            return "🟡"
        return "ℹ️"

    def b3d_alert_sort_key(self, pair):
        alert_id, item = pair
        sev = str((item or {}).get("severity") or "").lower()
        status = str((item or {}).get("status") or "").lower()

        sev_rank = {
            "critical": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
            "unknown": 4,
        }.get(sev, 4)

        status_rank = {
            "reopened": 0,
            "new": 1,
            "ongoing": 2,
            "acknowledged": 3,
            "updated": 4,
            "resolved": 9,
        }.get(status, 5)

        last_seen = str((item or {}).get("last_seen") or "")
        return (status_rank, sev_rank, last_seen)


    def b3d_render_alert_lines(self, alert_id: str, item: dict, include_action: bool = True, compact: bool = False) -> list[str]:
        item = item or {}

        title = item.get("title") or self.b3a_human_rule_name(alert_id)
        status = self.b3a_status_label(item.get("status", "ongoing")) if hasattr(self, "b3a_status_label") else str(item.get("status", "ongoing")).title()
        severity = str(item.get("severity") or "unknown").title()
        count = item.get("count", "?")
        area = item.get("area", "Unknown")
        owner = item.get("owner", "Unknown")
        route = item.get("investigation_route", item.get("route", "Unknown"))
        posts = item.get("post_count", 0)
        suppressed = item.get("suppressed_count", 0)
        emoji = self.b3d_status_emoji(item)

        sla = None
        if hasattr(self, "b3f_sla_status"):
            try:
                sla = self.b3f_sla_status(item)
            except Exception:
                sla = None

        if compact:
            sla_label = f" / SLA `{sla.get('label')}`" if sla else ""
            return [
                f"{emoji} **{title}** — `{status}` / `{severity}` / Count `{count}` / Owner `{owner}`{sla_label} / `{alert_id}`"
            ]

        lines = [
            f"{emoji} **{title}**",
            f"Alert ID: `{alert_id}`",
            f"Status: `{status}` | Severity: `{severity}` | Count: `{count}`",
            f"Area: `{area}` | Owner: `{owner}` | Route: `{route}`",
            f"Posts: `{posts}` | Suppressed duplicates: `{suppressed}`",
        ]

        if sla:
            lines.append(f"SLA: `{sla.get('label')}` | Age: `{sla.get('age_minutes')} min` | Next: {sla.get('next_step')}")

        if item.get("acknowledged_by"):
            lines.append(f"Acknowledged by: `{item.get('acknowledged_by')}`")

        if item.get("assigned_role_name"):
            lines.append(f"Assigned to: `{item.get('assigned_role_name')}`")

        if item.get("resolved_by"):
            lines.append(f"Resolved by: `{item.get('resolved_by')}`")

        if include_action:
            action = item.get("recommended_action", "Review the routed logs and confirm whether action is required.")
            lines.append(f"Action: {action}")

        lines.extend([
            f"Show: `!mcore alerts show {alert_id}`",
            f"Timeline: `!mcore alerts timeline {alert_id}`",
            "",
        ])

        return lines

    def b3e_redact_payload(self, obj):
        """Redact secret-looking payload values while keeping useful evidence keys/reasons."""
        import re

        secret_key_words = [
            "secret",
            "token",
            "password",
            "private",
            "api_key",
            "apikey",
            "access_key",
            "refresh",
            "session",
        ]

        if isinstance(obj, dict):
            cleaned = {}

            for key, value in obj.items():
                key_s = str(key)
                key_l = key_s.lower()

                if any(word in key_l for word in secret_key_words):
                    # Keep the key name so staff know what changed, but never expose the value.
                    cleaned[key_s] = "<redacted>"
                else:
                    cleaned[key_s] = self.b3e_redact_payload(value)

            return cleaned

        if isinstance(obj, list):
            return [self.b3e_redact_payload(x) for x in obj[:25]]

        if isinstance(obj, str):
            value = obj

            # Do not hide useful reason labels like "Updated billing.stripe_secret_key".
            value = re.sub(r"\b(sk_live|sk_test|pk_live|pk_test|xoxb|ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_\-]{8,}\b", "<secret>", value)
            value = re.sub(r"\b[A-Za-z0-9+/]{90,}={0,2}\b", "<secret>", value)

            return value

        return obj

    def b3e_json_preview(self, obj, limit: int = 1600) -> str:
        import json

        try:
            safe = self.b3e_redact_payload(obj)
            text = json.dumps(safe, indent=2, ensure_ascii=False)
        except Exception:
            text = str(obj)

        if len(text) > limit:
            text = text[:limit] + "\n... <truncated>"

        return text

    def b3e_build_evidence_from_payload(self, payload, *, endpoint: str = "", api_status: str = "", route: str = "", source: str = "api") -> dict:
        """Build a useful evidence summary from API JSON/text/embed payload."""
        import json

        original_payload = payload
        parsed = None

        if isinstance(payload, (dict, list)):
            parsed = payload
        elif isinstance(payload, str):
            text = payload.strip()

            # Remove code fences if present.
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()

            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None

        evidence = {
            "source": source,
            "endpoint": endpoint or "",
            "api_status": api_status or "",
            "route": route or "",
            "issue_count": None,
            "event_count": None,
            "actions": [],
            "risk_levels": [],
            "reasons": [],
            "actor_user_ids": [],
            "newest_event_time": "",
            "sample_events": [],
            "summary": "",
            "payload_preview": "",
        }

        if parsed is None:
            evidence["payload_preview"] = self.b3e_json_preview(str(original_payload), 1200)
            evidence["summary"] = "Evidence payload was not valid JSON, so only a safe text preview is available."
            return evidence

        safe = self.b3e_redact_payload(parsed)
        evidence["payload_preview"] = self.b3e_json_preview(safe, 1800)

        # Common count keys.
        if isinstance(safe, dict):
            for key in ["count", "issueCount", "issue_count", "total", "totalCount", "total_count"]:
                if key in safe:
                    try:
                        evidence["issue_count"] = int(safe.get(key))
                        break
                    except Exception:
                        pass

        # Find event-like arrays.
        events = []

        if isinstance(safe, dict):
            for key in ["events", "items", "results", "data", "alerts", "logs"]:
                value = safe.get(key)
                if isinstance(value, list):
                    events = value
                    break

        elif isinstance(safe, list):
            events = safe

        if evidence["issue_count"] is None:
            evidence["issue_count"] = len(events) if events else 1

        evidence["event_count"] = len(events) if events else evidence["issue_count"]

        actions = []
        risk_levels = []
        reasons = []
        actor_ids = []
        times = []
        sample_events = []

        for event in events[:8]:
            if not isinstance(event, dict):
                continue

            action = event.get("action") or event.get("type") or event.get("event") or ""
            risk = event.get("riskLevel") or event.get("risk") or event.get("severity") or ""
            reason = event.get("reason") or event.get("message") or event.get("description") or ""
            actor = event.get("actorUserId") or event.get("actorId") or event.get("actor") or event.get("userId") or ""
            created = event.get("createdAt") or event.get("timestamp") or event.get("time") or ""

            if action and action not in actions:
                actions.append(str(action))
            if risk and risk not in risk_levels:
                risk_levels.append(str(risk))
            if reason and reason not in reasons:
                reasons.append(str(reason))
            if actor and actor not in actor_ids:
                actor_ids.append(str(actor))
            if created:
                times.append(str(created))

            sample_events.append({
                "id": str(event.get("id", ""))[:80],
                "actor": str(actor)[:80],
                "action": str(action)[:160],
                "risk": str(risk)[:80],
                "reason": str(reason)[:220],
                "createdAt": str(created)[:80],
            })

        if times:
            try:
                evidence["newest_event_time"] = sorted(times, reverse=True)[0]
            except Exception:
                evidence["newest_event_time"] = times[0]

        evidence["actions"] = actions[:8]
        evidence["risk_levels"] = risk_levels[:6]
        evidence["reasons"] = reasons[:8]
        evidence["actor_user_ids"] = actor_ids[:8]
        evidence["sample_events"] = sample_events[:8]

        parts = []

        if evidence.get("endpoint"):
            parts.append(f"Endpoint `{evidence['endpoint']}`")
        if evidence.get("api_status"):
            parts.append(f"API `{evidence['api_status']}`")
        if evidence.get("issue_count") is not None:
            parts.append(f"{evidence['issue_count']} issue(s)")
        if actions:
            parts.append("actions: " + ", ".join(actions[:3]))
        if risk_levels:
            parts.append("risk: " + ", ".join(risk_levels[:3]))
        if reasons:
            parts.append("latest reasons include: " + "; ".join(reasons[:3]))

        evidence["summary"] = " • ".join(parts) if parts else "Evidence captured from the alert payload."

        return evidence

    def b3e_extract_embed_evidence(self, embed=None, content=None) -> dict:
        """Extract evidence fields from the original alert embed."""
        fields = {}
        payload_preview = ""

        if content:
            fields["content"] = str(content)

        if embed is not None:
            try:
                if getattr(embed, "title", None):
                    fields["title"] = str(embed.title)

                if getattr(embed, "description", None):
                    fields["description"] = str(embed.description)

                for field in getattr(embed, "fields", []) or []:
                    name = str(getattr(field, "name", "") or "").strip().lower()
                    value = str(getattr(field, "value", "") or "")

                    fields[name] = value

                    if "payload" in name or "preview" in name:
                        payload_preview = value

            except Exception:
                pass

        endpoint = fields.get("endpoint", "")
        api_status = fields.get("api", "") or fields.get("http", "") or fields.get("status", "")
        route = fields.get("route", "")
        issue_count = fields.get("issue count", "") or fields.get("count", "")

        if payload_preview:
            evidence = self.b3e_build_evidence_from_payload(
                payload_preview,
                endpoint=endpoint,
                api_status=api_status,
                route=route,
                source="alert_embed",
            )
        else:
            evidence = self.b3e_build_evidence_from_payload(
                fields,
                endpoint=endpoint,
                api_status=api_status,
                route=route,
                source="alert_embed_fields",
            )

        if issue_count:
            try:
                evidence["issue_count"] = int(str(issue_count).strip())
            except Exception:
                pass

        if route and not evidence.get("route"):
            evidence["route"] = route

        if endpoint and not evidence.get("endpoint"):
            evidence["endpoint"] = endpoint

        if api_status and not evidence.get("api_status"):
            evidence["api_status"] = api_status

        return evidence

    def b3e_endpoint_for_alert(self, alert_id: str, item: dict = None) -> str:
        item = item or {}
        raw = " ".join([
            str(alert_id or ""),
            str(item.get("identity", "")),
            str(item.get("rule_id", "")),
            str(item.get("title", "")),
            str(item.get("raw_reference", "")),
        ]).lower()

        mapping = [
            (["audit_highrisk", "high_risk_audit_events", "alert:audit_highrisk"], "/bot/audit/highrisk"),
            (["support_critical", "alert:support_critical"], "/bot/support/critical"),
            (["support_unassigned", "alert:support_unassigned"], "/bot/support/unassigned"),
            (["billing_failed", "alert:billing_failed"], "/bot/billing/failed"),
            (["billing_pastdue", "billing_past_due", "alert:billing_pastdue"], "/bot/billing/pastdue"),
            (["security_risks", "alert:security_risks"], "/bot/security/risks"),
            (["security_suspicious", "alert:security_suspicious"], "/bot/security/suspicious"),
            (["automation_failed", "alert:automation_failed"], "/bot/automation/failed"),
            (["discord_broken", "alert:discord_broken"], "/bot/discord/broken"),
            (["roblox_broken", "alert:roblox_broken"], "/bot/roblox/broken"),
            (["incidents", "alert:incidents"], "/bot/incidents"),
        ]

        for needles, endpoint in mapping:
            if any(n in raw for n in needles):
                return endpoint

        return ""

    async def b3e_fetch_api_evidence(self, guild, alert_id: str, item: dict = None):
        """Fetch fresh evidence directly from the Mattis API for a known alert."""
        try:
            import aiohttp
        except Exception:
            return None

        item = item or {}
        endpoint = self.b3e_endpoint_for_alert(alert_id, item)

        if not endpoint:
            return None

        api_url = None
        api_token = None

        if hasattr(self, "doctor_get_api_config_value"):
            try:
                api_url = await self.doctor_get_api_config_value(guild, [
                    "api_url",
                    "api_base_url",
                    "mattis_api_url",
                    "backend_url",
                ])
                api_token = await self.doctor_get_api_config_value(guild, [
                    "api_token",
                    "doctor_api_token",
                    "bot_api_token",
                    "mattis_api_token",
                    "mattis_token",
                    "api_key",
                ])
            except Exception:
                pass

        if not api_url:
            api_url = "https://api.mattisproductions.com"

        api_url = str(api_url).rstrip("/")
        headers = {}

        if api_token:
            token = str(api_token).strip()
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"

        try:
            timeout = aiohttp.ClientTimeout(total=10)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_url + endpoint, headers=headers) as resp:
                    text = await resp.text()

                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        payload = text

                    evidence = self.b3e_build_evidence_from_payload(
                        payload,
                        endpoint=endpoint,
                        api_status=f"HTTP {resp.status}",
                        route=item.get("investigation_route") or item.get("route") or "",
                        source="api_fetch",
                    )

                    return evidence

        except Exception:
            return None

    def b3e_evidence_brief(self, evidence: dict) -> str:
        evidence = evidence or {}

        lines = []

        if evidence.get("summary"):
            lines.append(evidence.get("summary"))

        endpoint = evidence.get("endpoint")
        api_status = evidence.get("api_status")
        route = evidence.get("route")

        if endpoint or api_status or route:
            lines.append(f"Endpoint: `{endpoint or 'Unknown'}` | API: `{api_status or 'Unknown'}` | Route: `{route or 'Unknown'}`")

        if evidence.get("newest_event_time"):
            lines.append(f"Newest event: `{evidence.get('newest_event_time')}`")

        reasons = evidence.get("reasons") or []
        if reasons:
            lines.append("Reasons:")
            for reason in reasons[:4]:
                lines.append(f"- {reason}")

        actions = evidence.get("actions") or []
        if actions:
            lines.append("Actions: " + ", ".join(f"`{x}`" for x in actions[:5]))

        actors = evidence.get("actor_user_ids") or []
        if actors:
            lines.append("Actors: " + ", ".join(f"`{x}`" for x in actors[:5]))

        return "\n".join(lines)[:1024] if lines else ""

    def b3e_evidence_lines(self, evidence: dict) -> list[str]:
        evidence = evidence or {}

        lines = [
            f"Source: `{evidence.get('source', 'unknown')}`",
            f"Endpoint: `{evidence.get('endpoint') or 'Unknown'}`",
            f"API status: `{evidence.get('api_status') or 'Unknown'}`",
            f"Route: `{evidence.get('route') or 'Unknown'}`",
            f"Issue count: `{evidence.get('issue_count', 'Unknown')}`",
            f"Event count: `{evidence.get('event_count', 'Unknown')}`",
            f"Newest event: `{evidence.get('newest_event_time') or 'Unknown'}`",
            "",
            "**Summary:**",
            evidence.get("summary") or "No evidence summary available.",
            "",
        ]

        if evidence.get("risk_levels"):
            lines.append("**Risk levels:**")
            for risk in evidence.get("risk_levels", [])[:8]:
                lines.append(f"- `{risk}`")
            lines.append("")

        if evidence.get("actions"):
            lines.append("**Actions:**")
            for action in evidence.get("actions", [])[:10]:
                lines.append(f"- `{action}`")
            lines.append("")

        if evidence.get("reasons"):
            lines.append("**Reasons:**")
            for reason in evidence.get("reasons", [])[:10]:
                lines.append(f"- {reason}")
            lines.append("")

        if evidence.get("actor_user_ids"):
            lines.append("**Actor user IDs:**")
            for actor in evidence.get("actor_user_ids", [])[:10]:
                lines.append(f"- `{actor}`")
            lines.append("")

        return lines



    def b3f_parse_iso(self, value):
        from datetime import datetime, timezone

        if not value:
            return None

        try:
            value = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def b3f_now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    def b3f_sla_minutes(self, severity: str) -> dict:
        severity = str(severity or "low").lower()

        if severity == "critical":
            return {
                "ack": 5,
                "investigate": 15,
                "update": 30,
            }

        if severity == "high":
            return {
                "ack": 15,
                "investigate": 30,
                "update": 60,
            }

        if severity == "medium":
            return {
                "ack": 60,
                "investigate": 120,
                "update": 240,
            }

        return {
            "ack": 240,
            "investigate": 480,
            "update": 720,
        }

    def b3f_escalation_path_list(self, item: dict) -> list[str]:
        item = item or {}
        raw = str(item.get("escalation") or item.get("owner") or "Management → Founder")

        parts = []

        for token in raw.replace("->", "→").split("→"):
            token = token.strip()
            if token:
                parts.append(token)

        if not parts:
            parts = ["Management", "Founder"]

        return parts

    def b3f_alert_age_minutes(self, item: dict) -> int:
        first_seen = self.b3f_parse_iso(item.get("first_seen") or item.get("last_seen") or item.get("acknowledged_at"))
        if not first_seen:
            return 0

        delta = self.b3f_now() - first_seen
        return max(0, int(delta.total_seconds() // 60))

    def b3f_minutes_since_last_update(self, item: dict) -> int:
        last = (
            item.get("last_seen")
            or item.get("last_posted")
            or item.get("acknowledged_at")
            or item.get("first_seen")
        )

        dt = self.b3f_parse_iso(last)

        if not dt:
            return 0

        delta = self.b3f_now() - dt
        return max(0, int(delta.total_seconds() // 60))

    def b3f_sla_status(self, item: dict) -> dict:
        item = item or {}

        status = str(item.get("status") or "ongoing").lower()
        severity = str(item.get("severity") or "low").lower()
        sla = self.b3f_sla_minutes(severity)
        age = self.b3f_alert_age_minutes(item)
        since_update = self.b3f_minutes_since_last_update(item)

        acknowledged = bool(item.get("acknowledged_at")) or status in ["acknowledged", "resolved", "reopened"]
        resolved = bool(item.get("resolved")) or status == "resolved"

        ack_overdue = False
        investigation_overdue = False
        update_overdue = False

        if not resolved:
            ack_overdue = not acknowledged and age >= sla["ack"]
            investigation_overdue = acknowledged and age >= sla["investigate"] and not item.get("assigned_role_name")
            update_overdue = acknowledged and since_update >= sla["update"]

        overdue = ack_overdue or investigation_overdue or update_overdue

        if resolved:
            label = "Resolved"
        elif overdue:
            label = "Overdue"
        elif acknowledged:
            label = "Within SLA / Acknowledged"
        else:
            label = "Waiting for acknowledgement"

        next_step = "No action required."

        if resolved:
            next_step = "Alert is resolved. Reopen if the API still reports the issue."
        elif ack_overdue:
            next_step = "Acknowledge this alert and assign an owner immediately."
        elif investigation_overdue:
            next_step = "Assign an owner/team and begin investigation."
        elif update_overdue:
            next_step = "Post an update/note or escalate if no progress has been made."
        elif not acknowledged:
            next_step = f"Acknowledge within {sla['ack']} minutes of first seen."
        elif acknowledged and not item.get("assigned_role_name"):
            next_step = "Assign an owner/team if investigation is required."

        return {
            "label": label,
            "severity": severity,
            "age_minutes": age,
            "minutes_since_update": since_update,
            "ack_sla_minutes": sla["ack"],
            "investigate_sla_minutes": sla["investigate"],
            "update_sla_minutes": sla["update"],
            "acknowledged": acknowledged,
            "resolved": resolved,
            "ack_overdue": ack_overdue,
            "investigation_overdue": investigation_overdue,
            "update_overdue": update_overdue,
            "overdue": overdue,
            "next_step": next_step,
        }

    def b3f_sla_brief(self, item: dict) -> str:
        data = self.b3f_sla_status(item)

        return (
            f"Status: `{data['label']}`\n"
            f"Age: `{data['age_minutes']} min`\n"
            f"Ack SLA: `{data['ack_sla_minutes']} min`\n"
            f"Investigation SLA: `{data['investigate_sla_minutes']} min`\n"
            f"Update SLA: `{data['update_sla_minutes']} min`\n"
            f"Next step: {data['next_step']}"
        )

    def b3f_recommended_escalation_target(self, item: dict) -> str:
        path = self.b3f_escalation_path_list(item)

        status = str(item.get("status") or "ongoing").lower()
        assigned = item.get("assigned_role_name")
        acknowledged = bool(item.get("acknowledged_at")) or status in ["acknowledged", "resolved", "reopened"]

        if not acknowledged:
            return path[0] if path else "Management"

        if assigned and len(path) > 1:
            # If already assigned to first team, escalate to the next step where possible.
            assigned_l = str(assigned).lower()
            for idx, step in enumerate(path):
                if step.lower() in assigned_l or assigned_l in step.lower():
                    if idx + 1 < len(path):
                        return path[idx + 1]
            return path[min(1, len(path) - 1)]

        return path[0] if path else "Management"



    def b4a_redact_log_value(self, value):
        import re

        if value is None:
            return ""

        text = str(value)

        text = re.sub(r"\b(sk_live|sk_test|pk_live|pk_test|xoxb|ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_\-]{8,}\b", "<secret>", text)
        text = re.sub(r"\b[A-Za-z0-9+/]{90,}={0,2}\b", "<secret>", text)

        return text[:300]

    def b4a_log_event_time(self, event: dict) -> str:
        return str(
            event.get("createdAt")
            or event.get("timestamp")
            or event.get("time")
            or event.get("at")
            or "Unknown"
        )

    def b4a_log_event_actor(self, event: dict) -> str:
        return self.b4a_redact_log_value(
            event.get("actorUserId")
            or event.get("actorId")
            or event.get("actor")
            or event.get("userId")
            or "Unknown"
        )

    def b4a_log_event_action(self, event: dict) -> str:
        return self.b4a_redact_log_value(
            event.get("action")
            or event.get("type")
            or event.get("event")
            or "Unknown"
        )

    def b4a_log_event_reason(self, event: dict) -> str:
        return self.b4a_redact_log_value(
            event.get("reason")
            or event.get("message")
            or event.get("description")
            or "Unknown"
        )

    def b4a_log_event_risk(self, event: dict) -> str:
        return self.b4a_redact_log_value(
            event.get("riskLevel")
            or event.get("risk")
            or event.get("severity")
            or "unknown"
        ).lower()

    def b4a_classify_log_event(self, event: dict) -> dict:
        action = self.b4a_log_event_action(event).lower()
        reason = self.b4a_log_event_reason(event).lower()
        risk = self.b4a_log_event_risk(event)

        category = "General"
        severity = risk if risk in ["critical", "high", "medium", "low"] else "low"

        if "secret" in reason or "token" in reason or "key" in reason or "webhook" in reason:
            category = "Secrets / Tokens"
            severity = "high"

        elif "billing" in reason or "stripe" in reason or "invoice" in reason:
            category = "Billing / Stripe"
            severity = "high" if severity in ["high", "critical"] else "medium"

        elif "roblox" in reason:
            category = "Roblox Integration"
            severity = "high" if severity in ["high", "critical"] else "medium"

        elif "permission" in reason or "role" in reason or "access" in reason or "capability" in reason:
            category = "Access / Permissions"
            severity = "high"

        elif "route" in reason or "channel" in reason:
            category = "Routes / Discord Logs"
            severity = "medium"

        elif "platform.setting.updated" in action or "setting.updated" in action:
            category = "Platform Settings"
            severity = "high" if severity in ["high", "critical"] else "medium"

        return {
            "category": category,
            "severity": severity,
            "action": self.b4a_log_event_action(event),
            "reason": self.b4a_log_event_reason(event),
            "actor": self.b4a_log_event_actor(event),
            "risk": self.b4a_log_event_risk(event),
            "createdAt": self.b4a_log_event_time(event),
            "id": self.b4a_redact_log_value(event.get("id") or ""),
        }

    async def b4a_get_api_config(self, guild):
        api_url = "https://api.mattisproductions.com"
        api_token = None

        if hasattr(self, "doctor_get_api_config_value"):
            try:
                found_url = await self.doctor_get_api_config_value(guild, [
                    "api_url",
                    "api_base_url",
                    "mattis_api_url",
                    "backend_url",
                ])

                found_token = await self.doctor_get_api_config_value(guild, [
                    "api_token",
                    "doctor_api_token",
                    "bot_api_token",
                    "mattis_api_token",
                    "mattis_token",
                    "api_key",
                ])

                if found_url:
                    api_url = str(found_url)

                if found_token:
                    api_token = str(found_token)
            except Exception:
                pass

        return api_url.rstrip("/"), api_token

    async def b4a_fetch_json(self, guild, endpoint: str):
        import aiohttp

        api_url, api_token = await self.b4a_get_api_config(guild)

        headers = {}

        if api_token:
            token = str(api_token).strip()
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"

        timeout = aiohttp.ClientTimeout(total=12)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(api_url + endpoint, headers=headers) as resp:
                text = await resp.text()

                try:
                    payload = await resp.json(content_type=None)
                except Exception:
                    payload = {"raw": text}

                return {
                    "status": resp.status,
                    "endpoint": endpoint,
                    "payload": payload,
                }

    def b4a_extract_events(self, payload):
        if isinstance(payload, dict):
            for key in ["events", "items", "results", "data", "logs", "alerts"]:
                value = payload.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]

        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]

        return []

    async def b4a_fetch_highrisk_events(self, guild):
        result = await self.b4a_fetch_json(guild, "/bot/audit/highrisk")
        payload = result.get("payload")
        events = self.b4a_extract_events(payload)

        return {
            "status": result.get("status"),
            "endpoint": result.get("endpoint"),
            "events": events,
            "payload": payload,
        }

    def b4a_group_counts(self, items):
        counts = {}

        for item in items:
            key = str(item or "Unknown")
            counts[key] = counts.get(key, 0) + 1

        return sorted(counts.items(), key=lambda x: x[1], reverse=True)

    def b4a_log_summary_lines(self, events: list[dict]) -> list[str]:
        classified = [self.b4a_classify_log_event(e) for e in events]

        categories = self.b4a_group_counts([x["category"] for x in classified])
        actors = self.b4a_group_counts([x["actor"] for x in classified])
        actions = self.b4a_group_counts([x["action"] for x in classified])
        risks = self.b4a_group_counts([x["risk"] for x in classified])
        reasons = self.b4a_group_counts([x["reason"] for x in classified])

        high_count = sum(1 for x in classified if x["severity"] in ["high", "critical"])
        newest = classified[0]["createdAt"] if classified else "Unknown"

        lines = [
            f"Events analysed: `{len(events)}`",
            f"High/critical classified events: `{high_count}`",
            f"Newest event: `{newest}`",
            "",
            "**Categories:**",
        ]

        if categories:
            for name, count in categories[:10]:
                lines.append(f"- `{name}` — `{count}`")
        else:
            lines.append("- None")

        lines.extend(["", "**Risk levels:**"])

        if risks:
            for name, count in risks[:10]:
                lines.append(f"- `{name}` — `{count}`")
        else:
            lines.append("- None")

        lines.extend(["", "**Top actors:**"])

        if actors:
            for actor, count in actors[:10]:
                lines.append(f"- `{actor}` — `{count}`")
        else:
            lines.append("- None")

        lines.extend(["", "**Top actions:**"])

        if actions:
            for action, count in actions[:10]:
                lines.append(f"- `{action}` — `{count}`")
        else:
            lines.append("- None")

        lines.extend(["", "**Top reasons:**"])

        if reasons:
            for reason, count in reasons[:10]:
                lines.append(f"- {reason} — `{count}`")
        else:
            lines.append("- None")

        return lines

    def b4a_event_lines(self, events: list[dict], limit: int = 10) -> list[str]:
        lines = []

        for idx, event in enumerate(events[:limit], start=1):
            c = self.b4a_classify_log_event(event)

            emoji = "🚨" if c["severity"] == "critical" else "⚠️" if c["severity"] == "high" else "🟡" if c["severity"] == "medium" else "ℹ️"

            lines.extend([
                f"{emoji} **Event {idx}**",
                f"ID: `{c['id'] or 'Unknown'}`",
                f"Time: `{c['createdAt']}`",
                f"Actor: `{c['actor']}`",
                f"Action: `{c['action']}`",
                f"Risk: `{c['risk']}`",
                f"Category: `{c['category']}`",
                f"Reason: {c['reason']}",
                "",
            ])

        return lines

    @alerts.command(name="sla")
    async def alerts_sla(self, ctx):
        """Show SLA status for tracked alerts."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild) if hasattr(self, "b3d_get_alert_state") else {}

        if not state:
            await ctx.send(embed=info_embed("Alert SLA", "No lifecycle alerts are currently tracked."))
            return

        items = list(state.items())

        if hasattr(self, "b3d_alert_sort_key"):
            items.sort(key=self.b3d_alert_sort_key)

        lines = []

        for alert_id, item in items[:50]:
            title = item.get("title") or self.b3a_human_rule_name(alert_id)
            sla = self.b3f_sla_status(item)
            target = self.b3f_recommended_escalation_target(item)

            lines.extend([
                f"**{title}**",
                f"Alert ID: `{alert_id}`",
                f"SLA status: `{sla['label']}`",
                f"Severity: `{str(item.get('severity', 'unknown')).title()}` | Age: `{sla['age_minutes']} min` | Since update: `{sla['minutes_since_update']} min`",
                f"Ack SLA: `{sla['ack_sla_minutes']} min` | Investigation SLA: `{sla['investigate_sla_minutes']} min` | Update SLA: `{sla['update_sla_minutes']} min`",
                f"Recommended escalation target: `{target}`",
                f"Next step: {sla['next_step']}",
                "",
            ])

        await self.send_paginated(ctx, "Alert SLA Status", lines)

    @alerts.command(name="overdue")
    async def alerts_overdue(self, ctx):
        """Show alerts that are overdue for acknowledgement, investigation, or update."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild) if hasattr(self, "b3d_get_alert_state") else {}

        overdue = []

        for alert_id, item in state.items():
            sla = self.b3f_sla_status(item)

            if sla.get("overdue"):
                overdue.append((alert_id, item, sla))

        overdue.sort(key=lambda x: (str(x[1].get("severity", "")), -int(x[2].get("age_minutes", 0))))

        if not overdue:
            await ctx.send(embed=ok_embed("Overdue Alerts", "No alerts are currently overdue."))
            return

        lines = []

        for alert_id, item, sla in overdue[:30]:
            title = item.get("title") or self.b3a_human_rule_name(alert_id)
            target = self.b3f_recommended_escalation_target(item)

            reasons = []
            if sla.get("ack_overdue"):
                reasons.append("acknowledgement overdue")
            if sla.get("investigation_overdue"):
                reasons.append("investigation assignment overdue")
            if sla.get("update_overdue"):
                reasons.append("update overdue")

            lines.extend([
                f"🚨 **{title}**",
                f"Alert ID: `{alert_id}`",
                f"Reason: `{', '.join(reasons)}`",
                f"Age: `{sla['age_minutes']} min` | Severity: `{str(item.get('severity', 'unknown')).title()}`",
                f"Owner: `{item.get('owner', 'Unknown')}` | Escalate to: `{target}`",
                f"Next step: {sla['next_step']}",
                f"Escalate: `!mcore alerts escalate {alert_id} <note>`",
                "",
            ])

        await self.send_paginated(ctx, "Overdue Alerts", lines)

    @alerts.command(name="escalation")
    async def alerts_escalation(self, ctx, *, alert_id: str):
        """Show escalation plan for one alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        path = self.b3f_escalation_path_list(item)
        sla = self.b3f_sla_status(item)
        target = self.b3f_recommended_escalation_target(item)

        lines = [
            f"**{item.get('title', key)}**",
            f"Alert ID: `{key}`",
            f"Status: `{self.b3a_status_label(item.get('status', 'ongoing'))}`",
            f"Severity: `{str(item.get('severity', 'unknown')).title()}`",
            "",
            "**SLA:**",
            f"Current SLA status: `{sla['label']}`",
            f"Age: `{sla['age_minutes']} min`",
            f"Ack SLA: `{sla['ack_sla_minutes']} min`",
            f"Investigation SLA: `{sla['investigate_sla_minutes']} min`",
            f"Update SLA: `{sla['update_sla_minutes']} min`",
            f"Next step: {sla['next_step']}",
            "",
            "**Escalation path:**",
        ]

        for idx, step in enumerate(path, start=1):
            marker = "⬅️ recommended next" if step == target else ""
            lines.append(f"{idx}. `{step}` {marker}")

        lines.extend([
            "",
            f"Recommended escalation target: `{target}`",
            f"Escalate command: `!mcore alerts escalate {key} <note>`",
        ])

        await self.send_paginated(ctx, "Alert Escalation Plan", lines)

    @alerts.command(name="escalate")
    async def alerts_escalate(self, ctx, alert_id: str, *, note: str = ""):
        """Escalate an alert to the next team/person in its escalation path."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        target = self.b3f_recommended_escalation_target(item)
        actor = self.b3b_actor(ctx) if hasattr(self, "b3b_actor") else str(ctx.author)

        item["status"] = "reopened" if item.get("resolved") else item.get("status", "ongoing")
        item["escalated_to"] = target
        item["escalated_by"] = str(ctx.author)
        item["escalated_by_id"] = getattr(ctx.author, "id", None)
        item["escalated_at"] = self.b3b_now_iso() if hasattr(self, "b3b_now_iso") else self.b3f_now().isoformat()

        details = f"Escalated to {target}"
        if note:
            details += f" — {note}"

        if hasattr(self, "b3b_add_timeline"):
            item = self.b3b_add_timeline(item, "escalated", actor, details)

        notes = item.get("notes") or []
        if note:
            notes.append({
                "at": item["escalated_at"],
                "author": str(ctx.author),
                "note": f"Escalated to {target}: {note}",
            })
            item["notes"] = notes[-50:]

        await self.b3b_save_alert_item(ctx.guild, key, item)

        if hasattr(self, "b3b_try_edit_alert_message"):
            await self.b3b_try_edit_alert_message(ctx.guild, item)

        await ctx.send(embed=ok_embed(
            "Alert escalated",
            f"`{item.get('title', key)}` escalated to `{target}`."
        ))

    @alerts.command(name="priority")
    async def alerts_priority(self, ctx, alert_id: str, severity: str):
        """Manually set alert priority/severity."""
        if not await require_admin(ctx):
            return

        severity = str(severity or "").lower().strip()

        if severity not in ["critical", "high", "medium", "low"]:
            await ctx.send(embed=error_embed("Invalid priority", "Use one of: `critical`, `high`, `medium`, `low`."))
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        old = item.get("severity", "unknown")
        item["severity"] = severity
        item["severity_reason"] = f"Manually set from `{old}` to `{severity}` by `{ctx.author}`."

        if hasattr(self, "b3b_add_timeline"):
            item = self.b3b_add_timeline(
                item,
                "priority_changed",
                self.b3b_actor(ctx),
                f"Priority changed from {old} to {severity}",
            )

        await self.b3b_save_alert_item(ctx.guild, key, item)

        if hasattr(self, "b3b_try_edit_alert_message"):
            await self.b3b_try_edit_alert_message(ctx.guild, item)

        await ctx.send(embed=ok_embed(
            "Alert priority updated",
            f"`{item.get('title', key)}` priority changed from `{old}` to `{severity}`."
        ))

    @alerts.command(name="evidence")
    async def alerts_evidence(self, ctx, *, alert_id: str):
        """Show evidence behind an alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        evidence = item.get("evidence") or {}

        if not evidence or not evidence.get("summary"):
            fetched = await self.b3e_fetch_api_evidence(ctx.guild, key, item)

            if fetched:
                item["evidence"] = fetched
                await self.b3b_save_alert_item(ctx.guild, key, item)
                evidence = fetched

        if not evidence:
            await ctx.send(embed=info_embed(
                "Alert Evidence",
                "No evidence payload is stored for this alert yet. Run `!mcore alerts check` after cooldown, then try again."
            ))
            return

        lines = [
            f"**{item.get('title', key)}**",
            f"Alert ID: `{key}`",
            "",
        ]

        lines.extend(self.b3e_evidence_lines(evidence))

        await self.send_paginated(ctx, "Alert Evidence", lines)

    @alerts.command(name="events")
    async def alerts_events(self, ctx, *, alert_id: str):
        """Show parsed event samples for an alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        evidence = item.get("evidence") or {}

        if not evidence or not evidence.get("sample_events"):
            fetched = await self.b3e_fetch_api_evidence(ctx.guild, key, item)

            if fetched:
                item["evidence"] = fetched
                await self.b3b_save_alert_item(ctx.guild, key, item)
                evidence = fetched

        events = evidence.get("sample_events") or []

        if not events:
            await ctx.send(embed=info_embed("Alert Events", "No parsed event samples are available for this alert."))
            return

        lines = [
            f"**{item.get('title', key)}**",
            f"Alert ID: `{key}`",
            "",
        ]

        for idx, event in enumerate(events[:10], start=1):
            lines.extend([
                f"**Event {idx}**",
                f"ID: `{event.get('id') or 'Unknown'}`",
                f"Actor: `{event.get('actor') or 'Unknown'}`",
                f"Action: `{event.get('action') or 'Unknown'}`",
                f"Risk: `{event.get('risk') or 'Unknown'}`",
                f"Reason: {event.get('reason') or 'Unknown'}",
                f"Created: `{event.get('createdAt') or 'Unknown'}`",
                "",
            ])

        await self.send_paginated(ctx, "Alert Event Samples", lines)

    @alerts.command(name="payload")
    async def alerts_payload(self, ctx, *, alert_id: str):
        """Show a safe/redacted payload preview for an alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        evidence = item.get("evidence") or {}

        if not evidence or not evidence.get("payload_preview"):
            fetched = await self.b3e_fetch_api_evidence(ctx.guild, key, item)

            if fetched:
                item["evidence"] = fetched
                await self.b3b_save_alert_item(ctx.guild, key, item)
                evidence = fetched

        payload = evidence.get("payload_preview")

        if not payload:
            await ctx.send(embed=info_embed("Alert Payload", "No payload preview is available for this alert."))
            return

        lines = [
            f"**{item.get('title', key)}**",
            f"Alert ID: `{key}`",
            "",
            "```json",
            payload[:1800],
            "```",
        ]

        await self.send_paginated(ctx, "Alert Payload Preview", lines)

    @alerts.command(name="open")
    async def alerts_open(self, ctx):
        """Show open/unresolved alerts."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild)

        items = [
            (alert_id, item)
            for alert_id, item in state.items()
            if self.b3d_is_open(item)
        ]

        items.sort(key=self.b3d_alert_sort_key)

        if not items:
            await ctx.send(embed=ok_embed("Open Alerts", "No open alerts are currently tracked."))
            return

        lines = []

        for alert_id, item in items[:30]:
            lines.extend(self.b3d_render_alert_lines(alert_id, item))

        await self.send_paginated(ctx, "Open Alerts", lines)

    @alerts.command(name="resolved")
    async def alerts_resolved(self, ctx):
        """Show resolved alerts."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild)

        items = [
            (alert_id, item)
            for alert_id, item in state.items()
            if self.b3d_is_resolved(item)
        ]

        items.sort(key=lambda pair: str(pair[1].get("resolved_at") or pair[1].get("last_seen") or ""), reverse=True)

        if not items:
            await ctx.send(embed=info_embed("Resolved Alerts", "No resolved alerts are currently tracked."))
            return

        lines = []

        for alert_id, item in items[:30]:
            lines.extend(self.b3d_render_alert_lines(alert_id, item, include_action=False))

        await self.send_paginated(ctx, "Resolved Alerts", lines)

    @alerts.command(name="all")
    async def alerts_all(self, ctx):
        """Show all tracked alerts."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild)

        if not state:
            await ctx.send(embed=info_embed("All Alerts", "No lifecycle alerts are currently tracked."))
            return

        items = list(state.items())
        items.sort(key=self.b3d_alert_sort_key)

        lines = []

        for alert_id, item in items[:50]:
            lines.extend(self.b3d_render_alert_lines(alert_id, item, include_action=False))

        await self.send_paginated(ctx, "All Tracked Alerts", lines)

    @alerts.command(name="compact")
    async def alerts_compact(self, ctx):
        """Show compact one-line alert overview."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild)

        if not state:
            await ctx.send(embed=info_embed("Compact Alerts", "No lifecycle alerts are currently tracked."))
            return

        items = list(state.items())
        items.sort(key=self.b3d_alert_sort_key)

        lines = []

        for alert_id, item in items[:60]:
            lines.extend(self.b3d_render_alert_lines(alert_id, item, compact=True))

        await self.send_paginated(ctx, "Compact Alerts", lines)

    @alerts.command(name="stats")
    async def alerts_stats(self, ctx):
        """Show alert lifecycle stats."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild)

        total = len(state)
        open_count = 0
        resolved_count = 0
        acknowledged_count = 0
        reopened_count = 0
        critical = 0
        high = 0
        medium = 0
        low = 0
        posts = 0
        suppressed = 0

        by_area = {}

        for item in state.values():
            status = str(item.get("status") or "").lower()
            severity = str(item.get("severity") or "").lower()
            area = item.get("area") or "Unknown"

            if self.b3d_is_resolved(item):
                resolved_count += 1
            else:
                open_count += 1

            if status == "acknowledged":
                acknowledged_count += 1
            if status == "reopened":
                reopened_count += 1

            if severity == "critical":
                critical += 1
            elif severity == "high":
                high += 1
            elif severity == "medium":
                medium += 1
            else:
                low += 1

            posts += int(item.get("post_count", 0) or 0)
            suppressed += int(item.get("suppressed_count", 0) or 0)

            by_area[area] = by_area.get(area, 0) + 1

        lines = [
            f"Total tracked alerts: `{total}`",
            f"Open: `{open_count}`",
            f"Resolved: `{resolved_count}`",
            f"Acknowledged: `{acknowledged_count}`",
            f"Reopened: `{reopened_count}`",
            "",
            "**Severity:**",
            f"Critical: `{critical}`",
            f"High: `{high}`",
            f"Medium: `{medium}`",
            f"Low/Unknown: `{low}`",
            "",
            "**Lifecycle volume:**",
            f"Posts: `{posts}`",
            f"Suppressed duplicates: `{suppressed}`",
            "",
            "**By area:**",
        ]

        if by_area:
            for area, count in sorted(by_area.items(), key=lambda x: x[0]):
                lines.append(f"{area}: `{count}`")
        else:
            lines.append("None")

        await self.send_paginated(ctx, "Alert Stats", lines)

    @alerts.command(name="history")
    async def alerts_history(self, ctx, *, alert_id: str = ""):
        """Show timeline history. Without an ID, shows all recent alert timeline events."""
        if not await require_admin(ctx):
            return

        state = await self.b3d_get_alert_state(ctx.guild)

        if alert_id:
            key, item = await self.b3b_find_alert(ctx.guild, alert_id)

            if not item:
                await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
                return

            await self.alerts_timeline(ctx, alert_id=alert_id)
            return

        events = []

        for key, item in state.items():
            title = item.get("title") or key

            for entry in item.get("timeline") or []:
                events.append({
                    "alert_id": key,
                    "title": title,
                    "at": entry.get("at", ""),
                    "action": entry.get("action", ""),
                    "actor": entry.get("actor", ""),
                    "details": entry.get("details", ""),
                })

        events.sort(key=lambda x: str(x.get("at", "")), reverse=True)

        if not events:
            await ctx.send(embed=info_embed("Alert History", "No timeline history is currently tracked."))
            return

        lines = []

        for event in events[:40]:
            details = event.get("details") or ""
            if details:
                lines.append(f"`{event.get('at')}` — **{event.get('title')}** — `{event.get('action')}` by `{event.get('actor')}` — {details}")
            else:
                lines.append(f"`{event.get('at')}` — **{event.get('title')}** — `{event.get('action')}` by `{event.get('actor')}`")

        await self.send_paginated(ctx, "Recent Alert History", lines)

    @alerts.command(name="normalize")
    async def alerts_normalize(self, ctx):
        """Migrate old hash-based alert state into stable canonical alert IDs."""
        if not await require_admin(ctx):
            return

        changed, state = await self.b3c_normalize_alert_state(ctx.guild)

        await ctx.send(embed=ok_embed(
            "Alert state normalized",
            f"Migrated `{changed}` old alert key(s). Current tracked alert(s): `{len(state)}`."
        ))

    @alerts.command(name="ack")
    async def alerts_ack(self, ctx, alert_id: str, *, note: str = ""):
        """Acknowledge an active alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        actor = self.b3b_actor(ctx)

        item["status"] = "acknowledged"
        item["acknowledged_by"] = str(ctx.author)
        item["acknowledged_by_id"] = getattr(ctx.author, "id", None)
        item["acknowledged_at"] = self.b3b_now_iso()
        item["last_seen"] = item["acknowledged_at"]

        if note:
            notes = item.get("notes") or []
            notes.append({
                "at": self.b3b_now_iso(),
                "author": str(ctx.author),
                "note": note,
            })
            item["notes"] = notes[-50:]

        item = self.b3b_add_timeline(item, "acknowledged", actor, note)
        await self.b3b_save_alert_item(ctx.guild, key, item)
        await self.b3b_try_edit_alert_message(ctx.guild, item)

        await ctx.send(embed=ok_embed("Alert acknowledged", f"`{item.get('title', key)}` has been acknowledged."))

    @alerts.command(name="resolve")
    async def alerts_resolve(self, ctx, alert_id: str, *, note: str = ""):
        """Mark an alert as resolved."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        actor = self.b3b_actor(ctx)

        item["status"] = "resolved"
        item["resolved"] = True
        item["resolved_by"] = str(ctx.author)
        item["resolved_by_id"] = getattr(ctx.author, "id", None)
        item["resolved_at"] = self.b3b_now_iso()
        item["last_seen"] = item["resolved_at"]

        if note:
            notes = item.get("notes") or []
            notes.append({
                "at": self.b3b_now_iso(),
                "author": str(ctx.author),
                "note": note,
            })
            item["notes"] = notes[-50:]

        item = self.b3b_add_timeline(item, "resolved", actor, note)
        await self.b3b_save_alert_item(ctx.guild, key, item)
        await self.b3b_try_edit_alert_message(ctx.guild, item)

        await ctx.send(embed=ok_embed("Alert resolved", f"`{item.get('title', key)}` has been marked resolved."))

    @alerts.command(name="reopen")
    async def alerts_reopen(self, ctx, alert_id: str, *, note: str = ""):
        """Reopen a resolved/acknowledged alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        actor = self.b3b_actor(ctx)

        item["status"] = "reopened"
        item["resolved"] = False
        item["reopened_by"] = str(ctx.author)
        item["reopened_by_id"] = getattr(ctx.author, "id", None)
        item["reopened_at"] = self.b3b_now_iso()
        item["last_seen"] = item["reopened_at"]

        if note:
            notes = item.get("notes") or []
            notes.append({
                "at": self.b3b_now_iso(),
                "author": str(ctx.author),
                "note": note,
            })
            item["notes"] = notes[-50:]

        item = self.b3b_add_timeline(item, "reopened", actor, note)
        await self.b3b_save_alert_item(ctx.guild, key, item)
        await self.b3b_try_edit_alert_message(ctx.guild, item)

        await ctx.send(embed=ok_embed("Alert reopened", f"`{item.get('title', key)}` has been reopened."))

    @alerts.command(name="assign")
    async def alerts_assign(self, ctx, alert_id: str, *, assignment: str):
        """Assign an alert to a Discord role."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        role = self.b3b_find_role_from_text(ctx.guild, assignment)

        if not role:
            await ctx.send(embed=error_embed("Role not found", "Mention a role or type its exact name. Example: `!mcore alerts assign audit @Security Admin`"))
            return

        actor = self.b3b_actor(ctx)

        item["assigned_role_id"] = role.id
        item["assigned_role_name"] = role.name
        item["assigned_by"] = str(ctx.author)
        item["assigned_by_id"] = getattr(ctx.author, "id", None)
        item["assigned_at"] = self.b3b_now_iso()
        item["owner"] = role.name

        item = self.b3b_add_timeline(item, "assigned", actor, f"Assigned to {role.name}")
        await self.b3b_save_alert_item(ctx.guild, key, item)
        await self.b3b_try_edit_alert_message(ctx.guild, item)

        await ctx.send(embed=ok_embed("Alert assigned", f"`{item.get('title', key)}` assigned to `{role.name}`."))

    @alerts.command(name="note")
    async def alerts_note(self, ctx, alert_id: str, *, note: str):
        """Add an internal note to an alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        notes = item.get("notes") or []

        notes.append({
            "at": self.b3b_now_iso(),
            "author": str(ctx.author),
            "note": note,
        })

        item["notes"] = notes[-50:]
        item = self.b3b_add_timeline(item, "note", self.b3b_actor(ctx), note)

        await self.b3b_save_alert_item(ctx.guild, key, item)

        await ctx.send(embed=ok_embed("Alert note added", f"Added note to `{item.get('title', key)}`."))

    @alerts.command(name="timeline")
    async def alerts_timeline(self, ctx, *, alert_id: str):
        """Show the timeline and notes for an alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3b_find_alert(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title."))
            return

        lines = [
            f"**{item.get('title', key)}**",
            f"Status: `{self.b3a_status_label(item.get('status', 'ongoing'))}`",
            f"Severity: `{str(item.get('severity', 'unknown')).title()}`",
            f"Owner: `{item.get('owner', 'Unknown')}`",
            "",
            "**Timeline:**",
        ]

        timeline = item.get("timeline") or []

        if not timeline:
            lines.append("No timeline events yet.")
        else:
            for entry in timeline[-25:]:
                at = entry.get("at", "Unknown time")
                action = entry.get("action", "event")
                actor = entry.get("actor", "Unknown")
                details = entry.get("details", "")

                if details:
                    lines.append(f"- `{at}` — **{action}** by `{actor}` — {details}")
                else:
                    lines.append(f"- `{at}` — **{action}** by `{actor}`")

        notes = item.get("notes") or []

        if notes:
            lines.extend(["", "**Notes:**"])

            for note in notes[-15:]:
                lines.append(f"- `{note.get('at', 'Unknown time')}` — `{note.get('author', 'Unknown')}`: {note.get('note', '')}")

        lines.extend([
            "",
            f"Alert ID: `{str(key)[:180]}`",
        ])

        await self.send_paginated(ctx, "Alert Timeline", lines)

    @alerts.command(name="show")

    async def alerts_show(self, ctx, *, alert_id: str):
        """Show a full operational view of an alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3a_find_alert_state_item(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title. Try `!mcore alerts ops`."))
            return

        embed = await self.b3a_render_alert_embed(ctx.guild, meta=item)
        await ctx.send(embed=embed)

    @alerts.command(name="explain")

    async def alerts_explain(self, ctx, *, alert_id: str):
        """Explain what an alert means and what staff should do."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3a_find_alert_state_item(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title. Try `!mcore alerts ops`."))
            return

        lines = [
            f"**{item.get('title', key)}**",
            "",
            f"**What happened:** {item.get('plain_summary', 'Unknown')}",
            f"**Detailed summary:** {item.get('detailed_summary', 'Unknown')}",
            "",
            f"**Why it matters:** {item.get('why_it_matters', 'Unknown')}",
            "",
            f"**Severity:** {str(item.get('severity', 'unknown')).title()}",
            f"**Severity reason:** {item.get('severity_reason', 'Unknown')}",
            f"**Count:** `{item.get('count', '?')}`",
            f"**Trend:** {item.get('trend', 'Unknown')}",
            "",
            f"**Customer impact:** {item.get('customer_impact', 'Unknown')}",
            f"**Internal impact:** {item.get('internal_impact', 'Unknown')}",
            "",
            f"**Affected area:** {item.get('area', 'Unknown')}",
            f"**Subsystem:** {item.get('subsystem', 'Unknown')}",
            f"**Owner team:** {item.get('owner', 'Unknown')}",
            f"**Escalation path:** {item.get('escalation', 'Unknown')}",
            "",
            f"**Recommended action:** {item.get('recommended_action', 'Unknown')}",
            f"**Investigation route:** {item.get('investigation_route', 'Unknown')}",
            "",
            "**Related commands:**",
        ]

        for cmd in item.get("related_commands", [])[:8]:
            lines.append(f"`{cmd}`")

        lines.extend([
            "",
            "**Lifecycle:**",
            f"Posts: `{item.get('post_count', 0)}`",
            f"Suppressed duplicates: `{item.get('suppressed_count', 0)}`",
            f"Alert ID: `{str(key)[:180]}`",
        ])

        await self.send_paginated(ctx, "Alert Explanation", lines)

    @alerts.command(name="investigate")

    async def alerts_investigate(self, ctx, *, alert_id: str):
        """Show investigation steps for an alert."""
        if not await require_admin(ctx):
            return

        key, item = await self.b3a_find_alert_state_item(ctx.guild, alert_id)

        if not item:
            await ctx.send(embed=error_embed("Alert not found", "I could not find a tracked alert matching that ID/title. Try `!mcore alerts ops`."))
            return

        area = str(item.get("area", "")).lower()

        steps = [
            f"**Alert:** {item.get('title', key)}",
            f"**Status:** {self.b3a_status_label(item.get('status', 'ongoing'))}",
            f"**Severity:** {str(item.get('severity', 'unknown')).title()}",
            f"**Route:** {item.get('investigation_route', 'Unknown')}",
            f"**Owner:** {item.get('owner', 'Unknown')}",
            f"**Escalation:** {item.get('escalation', 'Unknown')}",
            "",
            "**Investigation steps:**",
            "1. Open the investigation route and review the newest related entries.",
            "2. Check what changed immediately before the alert first appeared.",
            "3. Confirm whether the activity was expected, planned, or caused by a staff action.",
            "4. Check whether customer-facing systems, billing, support, or security are affected.",
            "5. If the activity is suspicious, unauthorised, or customer-impacting, escalate using the escalation path.",
            "6. Once confirmed safe/resolved, update the alert lifecycle when resolution commands are added in the next batch.",
            "",
            "**What to look for:**",
        ]

        if "audit" in area or "security" in area:
            steps.extend([
                "- Recent permission, route, capability, token, admin, or staff-access changes.",
                "- Any action performed by an unexpected staff account.",
                "- Any repeated or unusual sensitive event pattern.",
                "- Whether the high-risk audit entry matches something you intentionally changed.",
            ])
        elif "api" in area or "backend" in area:
            steps.extend([
                "- API health and protected endpoint auth.",
                "- Nginx/API upstream errors.",
                "- systemd restarts or process crashes.",
                "- Recent backend deployments or env changes.",
            ])
        elif "billing" in area:
            steps.extend([
                "- Failed payment/invoice customer records.",
                "- Stripe/webhook errors.",
                "- Customer access changes caused by billing state.",
                "- Refund/chargeback evidence.",
            ])
        else:
            steps.extend([
                "- Newest routed logs.",
                "- Recent config changes.",
                "- Related doctor warnings/failures.",
                "- Whether the alert count is increasing.",
            ])

        steps.extend([
            "",
            "**Useful commands:**",
            "`!mcore alerts ops`",
            "`!mcore alerts show <alert_id>`",
            "`!mcore alerts explain <alert_id>`",
            "`!mcore doctor`",
        ])

        if "audit" in area or "security" in area:
            steps.extend([
                "`!mcore doctor capabilities`",
                "`!mcore access matrix`",
            ])
        elif "api" in area or "backend" in area:
            steps.extend([
                "`!mcore doctor api`",
                "`!mcore doctor settings`",
            ])

        await self.send_paginated(ctx, "Alert Investigation", steps)

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
            await self.b2_alert_guarded_send(ctx.guild, channel, 
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


    def b4b_event_search_text(self, event: dict) -> str:
        c = self.b4a_classify_log_event(event)
        return " ".join([
            str(c.get("id", "")),
            str(c.get("actor", "")),
            str(c.get("action", "")),
            str(c.get("reason", "")),
            str(c.get("risk", "")),
            str(c.get("category", "")),
            str(c.get("createdAt", "")),
        ]).lower()

    def b4b_match_event(self, event: dict, query: str) -> bool:
        query = str(query or "").lower().strip()
        if not query:
            return True

        text = self.b4b_event_search_text(event)

        # All words must match somewhere.
        parts = [x for x in query.replace(",", " ").split() if x]

        if not parts:
            return True

        return all(part in text for part in parts)

    def b4b_find_events(self, events: list[dict], query: str, limit: int = 25) -> list[dict]:
        matches = []

        for event in events:
            if self.b4b_match_event(event, query):
                matches.append(event)

            if len(matches) >= limit:
                break

        return matches

    def b4b_find_single_event(self, events: list[dict], query: str):
        query_l = str(query or "").lower().strip()

        if not query_l:
            return None

        # Exact/prefix ID first.
        for event in events:
            event_id = str(event.get("id") or "").lower()
            if event_id and (event_id == query_l or event_id.startswith(query_l) or query_l in event_id):
                return event

        # Then broad match.
        matches = self.b4b_find_events(events, query_l, limit=1)
        return matches[0] if matches else None

    def b4b_event_explanation_lines(self, event: dict) -> list[str]:
        c = self.b4a_classify_log_event(event)

        why = "This event should be reviewed because it appeared in the high-risk audit feed."

        reason_l = str(c.get("reason", "")).lower()
        action_l = str(c.get("action", "")).lower()

        if any(x in reason_l for x in ["secret", "token", "key", "webhook"]):
            why = "This is high risk because it relates to secrets, tokens, keys, or webhook configuration. These can affect authentication, billing, Roblox integrations, or API trust."

        elif "stripe" in reason_l or "billing" in reason_l:
            why = "This is important because billing/Stripe settings can affect payments, subscriptions, invoice handling, and customer access."

        elif "roblox" in reason_l:
            why = "This is important because Roblox integration settings can affect verification, webhooks, customer products, and CMS automation."

        elif any(x in reason_l for x in ["role", "permission", "access", "capability"]):
            why = "This is important because access/permission changes can affect who can manage, view, or control sensitive systems."

        elif "setting.updated" in action_l:
            why = "This is important because platform setting updates can change production behaviour and should be attributable to an expected admin action."

        next_steps = [
            "Confirm the actor is expected and authorised.",
            "Confirm the change matches planned work.",
            "Check whether any secret/token/key value was rotated or exposed.",
            "Check whether customer-facing systems were affected.",
            "If unexpected, escalate through the alert escalation path.",
        ]

        lines = [
            f"ID: `{c['id'] or 'Unknown'}`",
            f"Time: `{c['createdAt']}`",
            f"Actor: `{c['actor']}`",
            f"Action: `{c['action']}`",
            f"Risk: `{c['risk']}`",
            f"Category: `{c['category']}`",
            f"Reason: {c['reason']}",
            "",
            "**Why this matters:**",
            why,
            "",
            "**Recommended checks:**",
        ]

        for step in next_steps:
            lines.append(f"- {step}")

        lines.extend([
            "",
            "**Useful commands:**",
            "`!mcore logs event <event_id>`",
            "`!mcore logs actor <actor_id>`",
            "`!mcore logs reasons`",
            "`!mcore alerts investigate audit`",
            "`!mcore alerts escalate audit <note>`",
        ])

        return lines


    def b4c_parse_time(self, value):
        from datetime import datetime, timezone

        if not value:
            return None

        try:
            value = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt
        except Exception:
            return None

    def b4c_now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    def b4c_sort_events(self, events: list[dict], newest_first: bool = True) -> list[dict]:
        def key(event):
            c = self.b4a_classify_log_event(event)
            dt = self.b4c_parse_time(c.get("createdAt"))
            return dt or self.b4c_parse_time("1970-01-01T00:00:00+00:00")

        return sorted(events, key=key, reverse=newest_first)

    def b4c_minutes_ago(self, event: dict) -> int:
        c = self.b4a_classify_log_event(event)
        dt = self.b4c_parse_time(c.get("createdAt"))

        if not dt:
            return -1

        delta = self.b4c_now() - dt
        return max(0, int(delta.total_seconds() // 60))

    def b4c_timeline_lines(self, events: list[dict], limit: int = 25) -> list[str]:
        events = self.b4c_sort_events(events, newest_first=True)

        lines = []

        for event in events[:limit]:
            c = self.b4a_classify_log_event(event)
            minutes = self.b4c_minutes_ago(event)

            age = f"{minutes} min ago" if minutes >= 0 else "age unknown"

            emoji = "🚨" if c["severity"] == "critical" else "⚠️" if c["severity"] == "high" else "🟡" if c["severity"] == "medium" else "ℹ️"

            lines.append(
                f"{emoji} `{c['createdAt']}` — `{c['actor']}` — `{c['action']}` — **{c['category']}** — {c['reason']} _({age})_"
            )

        return lines

    def b4c_related_score(self, base: dict, other: dict) -> int:
        b = self.b4a_classify_log_event(base)
        o = self.b4a_classify_log_event(other)

        score = 0

        if b["actor"] != "Unknown" and b["actor"] == o["actor"]:
            score += 5

        if b["action"] != "Unknown" and b["action"] == o["action"]:
            score += 3

        if b["category"] == o["category"]:
            score += 3

        if b["risk"] == o["risk"]:
            score += 1

        reason_words = set(str(b["reason"]).lower().replace(".", " ").replace("_", " ").split())
        other_words = set(str(o["reason"]).lower().replace(".", " ").replace("_", " ").split())
        shared = reason_words.intersection(other_words)

        score += min(len(shared), 5)

        bt = self.b4c_parse_time(b["createdAt"])
        ot = self.b4c_parse_time(o["createdAt"])

        if bt and ot:
            minutes = abs(int((bt - ot).total_seconds() // 60))

            if minutes <= 15:
                score += 5
            elif minutes <= 60:
                score += 3
            elif minutes <= 240:
                score += 1

        return score

    def b4c_cluster_events(self, events: list[dict]) -> list[dict]:
        classified = [self.b4a_classify_log_event(e) for e in events]

        clusters = {}

        for event, c in zip(events, classified):
            key = f"{c['actor']}|{c['category']}|{c['action']}"
            bucket = clusters.setdefault(key, {
                "actor": c["actor"],
                "category": c["category"],
                "action": c["action"],
                "risk": c["risk"],
                "severity": c["severity"],
                "events": [],
                "reasons": {},
                "newest": c["createdAt"],
            })

            bucket["events"].append(event)
            bucket["reasons"][c["reason"]] = bucket["reasons"].get(c["reason"], 0) + 1

            if str(c["createdAt"]) > str(bucket["newest"]):
                bucket["newest"] = c["createdAt"]

            if c["severity"] == "critical":
                bucket["severity"] = "critical"
            elif c["severity"] == "high" and bucket["severity"] != "critical":
                bucket["severity"] = "high"
            elif c["severity"] == "medium" and bucket["severity"] not in ["critical", "high"]:
                bucket["severity"] = "medium"

        results = list(clusters.values())
        results.sort(key=lambda x: (len(x["events"]), str(x["newest"])), reverse=True)
        return results

    def b4c_suspicion_score(self, event: dict) -> tuple[int, list[str]]:
        c = self.b4a_classify_log_event(event)
        reason = str(c["reason"]).lower()
        action = str(c["action"]).lower()

        score = 0
        flags = []

        if c["severity"] in ["critical", "high"]:
            score += 3
            flags.append("high-risk severity")

        if any(x in reason for x in ["secret", "token", "key", "webhook"]):
            score += 5
            flags.append("secret/token/webhook related")

        if any(x in reason for x in ["stripe", "billing", "invoice"]):
            score += 4
            flags.append("billing/Stripe related")

        if "roblox" in reason:
            score += 3
            flags.append("Roblox integration related")

        if any(x in reason for x in ["permission", "role", "access", "capability"]):
            score += 4
            flags.append("access/permission related")

        if "platform.setting.updated" in action or "setting.updated" in action:
            score += 2
            flags.append("platform setting update")

        if c["actor"] == "Unknown":
            score += 2
            flags.append("unknown actor")

        return score, flags

    def b4c_suspicious_events(self, events: list[dict]) -> list[tuple[dict, int, list[str]]]:
        ranked = []

        for event in events:
            score, flags = self.b4c_suspicion_score(event)

            if score >= 5:
                ranked.append((event, score, flags))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked


    def b4c_relation_reasons(self, base: dict, other: dict) -> list[str]:
        b = self.b4a_classify_log_event(base)
        o = self.b4a_classify_log_event(other)

        reasons = []

        if b["actor"] != "Unknown" and b["actor"] == o["actor"]:
            reasons.append("same actor")

        if b["action"] != "Unknown" and b["action"] == o["action"]:
            reasons.append("same action")

        if b["category"] == o["category"]:
            reasons.append("same category")

        if b["risk"] == o["risk"]:
            reasons.append("same risk level")

        bt = self.b4c_parse_time(b["createdAt"])
        ot = self.b4c_parse_time(o["createdAt"])

        if bt and ot:
            minutes = abs(int((bt - ot).total_seconds() // 60))

            if minutes <= 240:
                reasons.append(f"within {minutes} minutes")

        reason_words = set(str(b["reason"]).lower().replace(".", " ").replace("_", " ").split())
        other_words = set(str(o["reason"]).lower().replace(".", " ").replace("_", " ").split())

        ignored = {"updated", "platform", "setting", "secret", "key", "client", "webhook", "the", "and", "or"}
        shared = sorted((reason_words.intersection(other_words)) - ignored)

        if shared:
            reasons.append("shared terms: " + ", ".join(shared[:5]))

        return reasons

    def b4c_related_score(self, base: dict, other: dict) -> int:
        b = self.b4a_classify_log_event(base)
        o = self.b4a_classify_log_event(other)

        score = 0

        same_actor = b["actor"] != "Unknown" and b["actor"] == o["actor"]
        same_action = b["action"] != "Unknown" and b["action"] == o["action"]
        same_category = b["category"] == o["category"]

        if same_actor:
            score += 3

        if same_action:
            score += 4

        if same_category:
            score += 5

        if b["risk"] == o["risk"]:
            score += 1

        reason_words = set(str(b["reason"]).lower().replace(".", " ").replace("_", " ").split())
        other_words = set(str(o["reason"]).lower().replace(".", " ").replace("_", " ").split())

        ignored = {"updated", "platform", "setting", "secret", "key", "client", "webhook", "the", "and", "or"}
        shared = (reason_words.intersection(other_words)) - ignored
        score += min(len(shared) * 2, 8)

        bt = self.b4c_parse_time(b["createdAt"])
        ot = self.b4c_parse_time(o["createdAt"])

        if bt and ot:
            minutes = abs(int((bt - ot).total_seconds() // 60))

            if minutes <= 15:
                score += 6
            elif minutes <= 60:
                score += 4
            elif minutes <= 240:
                score += 2

        if same_actor and same_action and same_category and b["category"] == "Secrets / Tokens":
            score += 5

        if same_actor and not same_action and not same_category:
            score -= 4

        return max(score, 0)

    def b4c_is_meaningfully_related(self, base: dict, other: dict, score: int) -> bool:
        b = self.b4a_classify_log_event(base)
        o = self.b4a_classify_log_event(other)

        if score < 10:
            return False

        same_actor = b["actor"] != "Unknown" and b["actor"] == o["actor"]
        same_action = b["action"] != "Unknown" and b["action"] == o["action"]
        same_category = b["category"] == o["category"]

        bt = self.b4c_parse_time(b["createdAt"])
        ot = self.b4c_parse_time(o["createdAt"])
        close_time = False

        if bt and ot:
            minutes = abs(int((bt - ot).total_seconds() // 60))
            close_time = minutes <= 240

        if same_category and same_action:
            return True

        if same_category and same_actor and close_time:
            return True

        if same_action and same_actor and close_time:
            return True

        if "Secrets / Tokens" in [b["category"], o["category"]] and same_actor and close_time:
            return True

        return False


    def b4mega_safe(self, value, limit: int = 400):
        text = str(value or "")
        text = text.replace("`", "'")
        text = text.replace("\n", " ")
        while "  " in text:
            text = text.replace("  ", " ")
        return text.strip()[:limit]

    def b4mega_event_identity(self, event: dict) -> str:
        c = self.b4a_classify_log_event(event)
        return c.get("id") or c.get("reason") or c.get("action") or "unknown"

    def b4mega_event_score(self, event: dict) -> int:
        c = self.b4a_classify_log_event(event)
        score = 0

        severity = str(c.get("severity", "")).lower()
        category = str(c.get("category", "")).lower()
        reason = str(c.get("reason", "")).lower()
        action = str(c.get("action", "")).lower()

        if severity == "critical":
            score += 10
        elif severity == "high":
            score += 7
        elif severity == "medium":
            score += 4
        else:
            score += 1

        if "secrets" in category or "token" in category:
            score += 8

        if any(x in reason for x in ["secret", "token", "key", "webhook"]):
            score += 8

        if any(x in reason for x in ["stripe", "billing", "invoice"]):
            score += 6

        if "roblox" in reason:
            score += 5

        if any(x in reason for x in ["oauth", "discord"]):
            score += 4

        if "setting.updated" in action:
            score += 3

        return score

    def b4mega_overall_severity(self, events: list[dict]) -> str:
        if not events:
            return "low"

        max_score = max(self.b4mega_event_score(e) for e in events)

        if max_score >= 22:
            return "critical"
        if max_score >= 15:
            return "high"
        if max_score >= 8:
            return "medium"
        return "low"

    def b4mega_get_related_events(self, events: list[dict], query: str, limit: int = 20) -> tuple[dict | None, list[dict]]:
        base = None
        query_l = str(query or "").lower().strip()

        scored_bases = []

        for event in events:
            c = self.b4a_classify_log_event(event)
            haystack = " ".join([
                c.get("id", ""),
                c.get("actor", ""),
                c.get("action", ""),
                c.get("category", ""),
                c.get("reason", ""),
                c.get("risk", ""),
            ]).lower()

            if query_l in haystack:
                score = 0

                if query_l in str(c.get("id", "")).lower():
                    score += 50
                if query_l in str(c.get("reason", "")).lower():
                    score += 30
                if query_l in str(c.get("category", "")).lower():
                    score += 15
                if c.get("category") == "Secrets / Tokens":
                    score += 20
                if c.get("action") == "platform.setting.updated":
                    score += 10
                if c.get("category") == "General":
                    score -= 25

                scored_bases.append((score, event))

        if scored_bases:
            scored_bases.sort(key=lambda x: x[0], reverse=True)
            base = scored_bases[0][1]

        if not base and hasattr(self, "b4b_find_single_event"):
            base = self.b4b_find_single_event(events, query)

        if not base:
            return None, []

        related = []

        for event in events:
            if self.b4mega_event_identity(event) == self.b4mega_event_identity(base):
                continue

            score = 0
            meaningful = False

            if hasattr(self, "b4c_related_score"):
                score = self.b4c_related_score(base, event)

            if hasattr(self, "b4c_is_meaningfully_related"):
                meaningful = self.b4c_is_meaningfully_related(base, event, score)
            else:
                meaningful = score >= 10

            if meaningful:
                related.append((score, event))

        related.sort(key=lambda x: x[0], reverse=True)

        return base, [event for score, event in related[:limit]]

    def b4mega_group_by(self, events: list[dict], field: str) -> list[tuple[str, int]]:
        counts = {}

        for event in events:
            c = self.b4a_classify_log_event(event)
            key = c.get(field) or "Unknown"
            counts[key] = counts.get(key, 0) + 1

        return sorted(counts.items(), key=lambda x: x[1], reverse=True)

    def b4mega_affected_areas(self, events: list[dict]) -> list[str]:
        areas = set()

        for event in events:
            c = self.b4a_classify_log_event(event)
            reason = str(c.get("reason", "")).lower()
            category = str(c.get("category", "")).lower()

            if "billing" in reason or "stripe" in reason:
                areas.add("Billing / Stripe")
            if "roblox" in reason:
                areas.add("Roblox Integration")
            if "discord" in reason:
                areas.add("Discord OAuth / Bot Integration")
            if "oauth" in reason:
                areas.add("OAuth Authentication")
            if "secret" in reason or "token" in reason or "key" in reason or "webhook" in reason or "secrets" in category:
                areas.add("Secrets / Tokens / Webhooks")
            if "entitlement" in reason or "entitlement" in str(c.get("action", "")).lower():
                areas.add("Entitlements / Access Matrix")
            if "role" in reason or "permission" in reason or "access" in reason:
                areas.add("Roles / Permissions / Access Control")
            if "setting" in str(c.get("action", "")).lower():
                areas.add("Platform Settings")

        return sorted(areas) or ["General Operations"]

    def b4mega_recommendations_for_events(self, events: list[dict]) -> list[str]:
        areas = self.b4mega_affected_areas(events)
        recs = []

        if "Secrets / Tokens / Webhooks" in areas:
            recs.extend([
                "Confirm every secret/token/webhook change was intentional and performed by an authorised admin.",
                "Verify no secret values were posted in Discord, logs, screenshots, commits, or support channels.",
                "Confirm old webhook/token values are no longer active if a rotation occurred.",
            ])

        if "Billing / Stripe" in areas:
            recs.extend([
                "Check Stripe dashboard/webhook delivery for failed or suspicious events after the secret/key update.",
                "Confirm billing webhooks are signing correctly and invoice/subscription events are still reaching the API.",
                "Run a safe billing test flow if available.",
            ])

        if "Roblox Integration" in areas:
            recs.extend([
                "Confirm Roblox OAuth, Open Cloud API, and webhook signing flows still work after the changes.",
                "Run a verification/sync test from the CMS side and check API logs.",
            ])

        if "Discord OAuth / Bot Integration" in areas:
            recs.extend([
                "Confirm Discord OAuth callback/login still works.",
                "Confirm the bot still has expected permissions and command responses are healthy.",
            ])

        if "Entitlements / Access Matrix" in areas:
            recs.extend([
                "Review entitlement matrix changes against the intended role/access design.",
                "Check no customer, staff, or executive access was granted incorrectly.",
            ])

        if "Roles / Permissions / Access Control" in areas:
            recs.extend([
                "Run capability/access matrix checks and confirm sensitive commands remain restricted.",
                "Review staff roles for accidental broad permissions.",
            ])

        recs.extend([
            "Add an alert note documenting whether this was expected maintenance, migration, setup, or suspicious activity.",
            "Escalate if the actor, timing, or reason does not match expected work.",
        ])

        # Dedupe while preserving order.
        seen = set()
        unique = []

        for rec in recs:
            if rec not in seen:
                seen.add(rec)
                unique.append(rec)

        return unique

    def b4mega_runbook_for_events(self, events: list[dict]) -> list[str]:
        areas = self.b4mega_affected_areas(events)

        lines = [
            "**Immediate triage:**",
            "1. Confirm whether the activity was expected, planned, or part of a setup/migration.",
            "2. Confirm the actor account is authorised.",
            "3. Check the related alert timeline and notes.",
            "4. Confirm whether customer-facing systems are impacted.",
            "",
            "**Area-specific checks:**",
        ]

        if "Billing / Stripe" in areas:
            lines.extend([
                "",
                "**Billing / Stripe:**",
                "- Check Stripe webhook endpoint status.",
                "- Confirm webhook signing secret matches the API environment.",
                "- Check recent billing event delivery attempts.",
                "- Confirm subscriptions, invoices, and customer access were not disrupted.",
            ])

        if "Roblox Integration" in areas:
            lines.extend([
                "",
                "**Roblox:**",
                "- Confirm Roblox OAuth client ID/secret are correct.",
                "- Confirm Open Cloud API key is active and scoped properly.",
                "- Confirm Roblox webhook signing secret matches CMS/API config.",
                "- Run a test verification/sync workflow.",
            ])

        if "Discord OAuth / Bot Integration" in areas:
            lines.extend([
                "",
                "**Discord:**",
                "- Confirm Discord OAuth client ID/secret are correct.",
                "- Confirm callback URL is correct.",
                "- Confirm the bot is online and responding.",
                "- Check command permission failures.",
            ])

        if "Secrets / Tokens / Webhooks" in areas:
            lines.extend([
                "",
                "**Secrets / Tokens / Webhooks:**",
                "- Do not paste actual secrets into Discord.",
                "- Confirm whether this was a rotation.",
                "- Disable old values where appropriate.",
                "- Check GitHub commits and screenshots for accidental exposure.",
            ])

        if "Entitlements / Access Matrix" in areas:
            lines.extend([
                "",
                "**Entitlements / Access Matrix:**",
                "- Review entitlement matrix changes.",
                "- Confirm who gained or lost access.",
                "- Run access/capability matrix checks.",
                "- Confirm customer-facing access rules still match policy.",
            ])

        lines.extend([
            "",
            "**Close-out:**",
            "- Add a note to the alert.",
            "- Resolve the alert only once the API evidence is expected/accepted.",
            "- Export an investigation report if this needs keeping.",
        ])

        return lines

    def b4mega_incident_classification(self, events: list[dict]) -> dict:
        severity = self.b4mega_overall_severity(events)
        areas = self.b4mega_affected_areas(events)

        customer_impact = "No confirmed customer impact from audit evidence alone."
        internal_impact = "Internal operational review required."

        if "Billing / Stripe" in areas:
            customer_impact = "Possible customer impact if billing webhooks, subscriptions, invoice handling, or customer access were disrupted."

        if "Roblox Integration" in areas:
            customer_impact = "Possible customer impact if Roblox verification, sync, or customer product integrations were disrupted."

        if "Secrets / Tokens / Webhooks" in areas:
            internal_impact = "Sensitive secret/token/webhook configuration changed. This requires confirmation, rotation review, and access validation."

        if "Entitlements / Access Matrix" in areas:
            internal_impact = "Access or entitlement configuration changed. Staff/customer permissions should be reviewed."

        return {
            "severity": severity,
            "areas": areas,
            "customer_impact": customer_impact,
            "internal_impact": internal_impact,
        }

    def b4mega_packet_lines(self, base: dict, related: list[dict], query: str) -> list[str]:
        events = [base] + list(related or [])
        classification = self.b4mega_incident_classification(events)
        base_c = self.b4a_classify_log_event(base)

        actors = self.b4mega_group_by(events, "actor")
        actions = self.b4mega_group_by(events, "action")
        categories = self.b4mega_group_by(events, "category")
        reasons = self.b4mega_group_by(events, "reason")
        recommendations = self.b4mega_recommendations_for_events(events)

        lines = [
            f"**Investigation Packet for `{query}`**",
            "",
            "**Base event:**",
            f"ID: `{base_c['id'] or 'Unknown'}`",
            f"Time: `{base_c['createdAt']}`",
            f"Actor: `{base_c['actor']}`",
            f"Action: `{base_c['action']}`",
            f"Category: `{base_c['category']}`",
            f"Risk: `{base_c['risk']}`",
            f"Reason: {base_c['reason']}",
            "",
            "**Incident classification:**",
            f"Severity: `{classification['severity'].title()}`",
            f"Affected areas: `{', '.join(classification['areas'])}`",
            f"Customer impact: {classification['customer_impact']}",
            f"Internal impact: {classification['internal_impact']}",
            "",
            f"Related events: `{len(related or [])}`",
            f"Events in packet: `{len(events)}`",
            "",
            "**Actors:**",
        ]

        for actor, count in actors[:10]:
            lines.append(f"- `{actor}` — `{count}`")

        lines.extend(["", "**Actions:**"])

        for action, count in actions[:10]:
            lines.append(f"- `{action}` — `{count}`")

        lines.extend(["", "**Categories:**"])

        for category, count in categories[:10]:
            lines.append(f"- `{category}` — `{count}`")

        lines.extend(["", "**Top reasons:**"])

        for reason, count in reasons[:10]:
            lines.append(f"- {reason} — `{count}`")

        lines.extend(["", "**Recommended actions:**"])

        for rec in recommendations[:12]:
            lines.append(f"- {rec}")

        lines.extend([
            "",
            "**Useful commands:**",
            f"`!mcore logs report {query}`",
            f"`!mcore logs export {query}`",
            f"`!mcore logs runbook {query}`",
            f"`!mcore alerts note audit <note>`",
            f"`!mcore alerts escalate audit <note>`",
            f"`!mcore alerts resolve audit <note>`",
        ])

        return lines

    def b4mega_report_text(self, base: dict, related: list[dict], query: str) -> str:
        events = [base] + list(related or [])
        classification = self.b4mega_incident_classification(events)
        lines = []

        lines.extend([
            "Mattis CMS | Systems",
            "Operations Investigation Report",
            "=" * 40,
            "",
            f"Query: {query}",
            f"Severity: {classification['severity'].title()}",
            f"Affected areas: {', '.join(classification['areas'])}",
            "",
            f"Customer impact: {classification['customer_impact']}",
            f"Internal impact: {classification['internal_impact']}",
            "",
            f"Total events in report: {len(events)}",
            "",
            "Events",
            "-" * 40,
        ])

        for idx, event in enumerate(events, start=1):
            c = self.b4a_classify_log_event(event)

            lines.extend([
                f"{idx}. {c['id'] or 'Unknown'}",
                f"   Time: {c['createdAt']}",
                f"   Actor: {c['actor']}",
                f"   Action: {c['action']}",
                f"   Category: {c['category']}",
                f"   Risk: {c['risk']}",
                f"   Reason: {c['reason']}",
                "",
            ])

        lines.extend([
            "",
            "Recommendations",
            "-" * 40,
        ])

        for rec in self.b4mega_recommendations_for_events(events):
            lines.append(f"- {rec}")

        lines.extend([
            "",
            "Runbook",
            "-" * 40,
        ])

        for line in self.b4mega_runbook_for_events(events):
            # Remove Discord markdown noise for export.
            lines.append(line.replace("**", ""))

        return "\n".join(lines)

    @mcore.group(name="logs", invoke_without_command=True)

    async def logs(self, ctx):
        """Mattis log forwarding + operations log intelligence."""
        if not await require_admin(ctx):
            return

        lines = [
            "**Mattis Log Forwarding Engine**",
            "",
            "`!mcore logs list` — forwarding rules",
            "`!mcore logs preview` — preview forwarding",
            "`!mcore logs check` — run forwarding check",
            "`!mcore logs force` — force forwarding",
            "`!mcore logs enable` — enable forwarding",
            "`!mcore logs disable` — disable forwarding",
            "",
            "**Operations Log Intelligence**",
            "",
            "`!mcore logs highrisk` — show newest high-risk audit entries",
            "`!mcore logs audit` — high-risk audit summary",
            "`!mcore logs actors` — group high-risk logs by actor",
            "`!mcore logs actions` — group high-risk logs by action",
            "`!mcore logs reasons` — group high-risk logs by reason",
            "`!mcore logs secrets` — secret/token/key/webhook related entries",
            "`!mcore logs summary` — full operations log summary",
        ]

        await self.send_paginated(ctx, "Mattis Logs", lines)






    @logs.command(name="brief")
    async def logs_brief(self, ctx, *, query: str):
        """Create a short investigation brief for a log query/event."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        base, related = self.b4mega_get_related_events(events, query, limit=10)

        if not base:
            await ctx.send(embed=info_embed("Investigation Brief", f"No event matched `{query}`."))
            return

        packet_events = [base] + related
        classification = self.b4mega_incident_classification(packet_events)
        base_c = self.b4a_classify_log_event(base)

        lines = [
            f"**Brief for `{query}`**",
            "",
            f"Base event: `{base_c['id'] or 'Unknown'}`",
            f"Reason: {base_c['reason']}",
            f"Severity: `{classification['severity'].title()}`",
            f"Affected areas: `{', '.join(classification['areas'])}`",
            f"Related events: `{len(related)}`",
            "",
            f"Customer impact: {classification['customer_impact']}",
            f"Internal impact: {classification['internal_impact']}",
            "",
            "**Top recommendations:**",
        ]

        for rec in self.b4mega_recommendations_for_events(packet_events)[:6]:
            lines.append(f"- {rec}")

        await self.send_paginated(ctx, "Investigation Brief", lines)

    @logs.command(name="packet")
    async def logs_packet(self, ctx, *, query: str):
        """Create a full investigation packet for a log query/event."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        base, related = self.b4mega_get_related_events(events, query, limit=20)

        if not base:
            await ctx.send(embed=info_embed("Investigation Packet", f"No event matched `{query}`."))
            return

        lines = self.b4mega_packet_lines(base, related, query)
        await self.send_paginated(ctx, "Investigation Packet", lines)

    @logs.command(name="incident")
    async def logs_incident(self, ctx, *, query: str):
        """Create an incident-style summary for a log query/event."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        base, related = self.b4mega_get_related_events(events, query, limit=20)

        if not base:
            await ctx.send(embed=info_embed("Incident Summary", f"No event matched `{query}`."))
            return

        packet_events = [base] + related
        classification = self.b4mega_incident_classification(packet_events)
        base_c = self.b4a_classify_log_event(base)

        lines = [
            "**Incident-style summary**",
            "",
            f"Trigger: `{base_c['id'] or 'Unknown'}`",
            f"Trigger reason: {base_c['reason']}",
            f"Severity: `{classification['severity'].title()}`",
            f"Affected areas: `{', '.join(classification['areas'])}`",
            f"Related evidence events: `{len(related)}`",
            "",
            "**Impact:**",
            f"- Customer: {classification['customer_impact']}",
            f"- Internal: {classification['internal_impact']}",
            "",
            "**Likely scenario:**",
            "This appears to be a related group of high-risk platform changes. Review whether it was planned setup, migration, secret rotation, OAuth configuration, or suspicious account activity.",
            "",
            "**Immediate response:**",
        ]

        for rec in self.b4mega_recommendations_for_events(packet_events)[:8]:
            lines.append(f"- {rec}")

        lines.extend([
            "",
            "**Close-out requirement:**",
            "- Add a note to the alert documenting whether this was expected.",
            "- Resolve only after the API evidence is accepted as safe or expected.",
        ])

        await self.send_paginated(ctx, "Incident Summary", lines)

    @logs.command(name="executive")
    async def logs_executive(self, ctx):
        """Create an executive summary of the current high-risk audit feed."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        if not events:
            await ctx.send(embed=ok_embed("Executive Log Summary", "No high-risk audit events returned by the API."))
            return

        classification = self.b4mega_incident_classification(events)
        actors = self.b4mega_group_by(events, "actor")
        categories = self.b4mega_group_by(events, "category")
        actions = self.b4mega_group_by(events, "action")

        lines = [
            "**Executive Summary — High-Risk Audit Feed**",
            "",
            f"Events analysed: `{len(events)}`",
            f"Overall severity: `{classification['severity'].title()}`",
            f"Affected areas: `{', '.join(classification['areas'])}`",
            "",
            f"Customer impact: {classification['customer_impact']}",
            f"Internal impact: {classification['internal_impact']}",
            "",
            "**Main actors:**",
        ]

        for actor, count in actors[:5]:
            lines.append(f"- `{actor}` — `{count}` event(s)")

        lines.extend(["", "**Main categories:**"])

        for category, count in categories[:8]:
            lines.append(f"- `{category}` — `{count}` event(s)")

        lines.extend(["", "**Main actions:**"])

        for action, count in actions[:8]:
            lines.append(f"- `{action}` — `{count}` event(s)")

        lines.extend(["", "**Recommended next actions:**"])

        for rec in self.b4mega_recommendations_for_events(events)[:10]:
            lines.append(f"- {rec}")

        await self.send_paginated(ctx, "Executive Log Summary", lines)

    @logs.command(name="runbook")
    async def logs_runbook(self, ctx, *, query: str):
        """Show the response runbook for a log query/event."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        base, related = self.b4mega_get_related_events(events, query, limit=20)

        if not base:
            await ctx.send(embed=info_embed("Investigation Runbook", f"No event matched `{query}`."))
            return

        packet_events = [base] + related
        lines = [
            f"**Runbook for `{query}`**",
            "",
        ]

        lines.extend(self.b4mega_runbook_for_events(packet_events))

        await self.send_paginated(ctx, "Investigation Runbook", lines)

    @logs.command(name="recommendations")
    async def logs_recommendations(self, ctx):
        """Show recommendations for the current high-risk audit feed."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        if not events:
            await ctx.send(embed=ok_embed("Log Recommendations", "No high-risk audit events returned by the API."))
            return

        lines = [
            f"Events analysed: `{len(events)}`",
            "",
            "**Recommended actions:**",
        ]

        for rec in self.b4mega_recommendations_for_events(events):
            lines.append(f"- {rec}")

        await self.send_paginated(ctx, "Log Recommendations", lines)

    @logs.command(name="checklist")
    async def logs_checklist(self, ctx, *, query: str):
        """Create a practical investigation checklist for a log query/event."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        base, related = self.b4mega_get_related_events(events, query, limit=20)

        if not base:
            await ctx.send(embed=info_embed("Investigation Checklist", f"No event matched `{query}`."))
            return

        packet_events = [base] + related
        areas = self.b4mega_affected_areas(packet_events)

        checks = [
            "Confirm actor is expected and authorised.",
            "Confirm this was planned work, migration, setup, or rotation.",
            "Confirm there is no customer impact.",
            "Confirm no secret values were exposed in Discord, GitHub, screenshots, or logs.",
            "Add a timeline note to the alert.",
        ]

        if "Billing / Stripe" in areas:
            checks.extend([
                "Check Stripe webhook signing and delivery.",
                "Check recent invoice/subscription events.",
                "Confirm customer access was not disrupted by billing config changes.",
            ])

        if "Roblox Integration" in areas:
            checks.extend([
                "Test Roblox OAuth/login/verification.",
                "Test Roblox Open Cloud/API integration.",
                "Confirm Roblox webhook signing still validates.",
            ])

        if "Discord OAuth / Bot Integration" in areas:
            checks.extend([
                "Test Discord OAuth login.",
                "Confirm Discord bot command health.",
                "Confirm Discord callback URL and client secret are correct.",
            ])

        lines = [
            f"**Checklist for `{query}`**",
            "",
            f"Affected areas: `{', '.join(areas)}`",
            "",
        ]

        for idx, check in enumerate(checks, start=1):
            lines.append(f"{idx}. ☐ {check}")

        await self.send_paginated(ctx, "Investigation Checklist", lines)

    @logs.command(name="affected")
    async def logs_affected(self, ctx, *, query: str = ""):
        """Show affected areas for a query, or the whole high-risk feed."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        selected = events

        if query:
            base, related = self.b4mega_get_related_events(events, query, limit=20)

            if not base:
                await ctx.send(embed=info_embed("Affected Areas", f"No event matched `{query}`."))
                return

            selected = [base] + related

        areas = self.b4mega_affected_areas(selected)
        classification = self.b4mega_incident_classification(selected)

        lines = [
            f"Query: `{query or 'all high-risk events'}`",
            f"Events analysed: `{len(selected)}`",
            f"Overall severity: `{classification['severity'].title()}`",
            "",
            "**Affected areas:**",
        ]

        for area in areas:
            lines.append(f"- `{area}`")

        lines.extend([
            "",
            f"Customer impact: {classification['customer_impact']}",
            f"Internal impact: {classification['internal_impact']}",
        ])

        await self.send_paginated(ctx, "Affected Areas", lines)

    @logs.command(name="report")
    async def logs_report(self, ctx, *, query: str):
        """Show a full investigation report in Discord."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        base, related = self.b4mega_get_related_events(events, query, limit=20)

        if not base:
            await ctx.send(embed=info_embed("Investigation Report", f"No event matched `{query}`."))
            return

        report = self.b4mega_report_text(base, related, query)
        chunks = report.splitlines()

        await self.send_paginated(ctx, "Investigation Report", chunks)

    @logs.command(name="export")
    async def logs_export(self, ctx, *, query: str):
        """Export an investigation report as a text file."""
        if not await require_admin(ctx):
            return

        import io
        import discord

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        base, related = self.b4mega_get_related_events(events, query, limit=20)

        if not base:
            await ctx.send(embed=info_embed("Investigation Export", f"No event matched `{query}`."))
            return

        report = self.b4mega_report_text(base, related, query)
        safe_name = "".join(ch for ch in str(query).lower() if ch.isalnum() or ch in ["-", "_"])[:40] or "investigation"
        fp = io.BytesIO(report.encode("utf-8"))

        await ctx.send(
            embed=ok_embed("Investigation report exported", f"Exported report for `{query}`."),
            file=discord.File(fp, filename=f"mattis-investigation-{safe_name}.txt")
        )

    @logs.command(name="correlate")
    async def logs_correlate(self, ctx, *, query: str):
        """Find events meaningfully related to one event ID or query."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        if not events:
            await ctx.send(embed=info_embed("Log Correlation", "No high-risk audit events returned by the API."))
            return

        query_l = str(query or "").lower().strip()

        base = None

        # Prefer direct search matches that are NOT generic entitlement records.
        scored_bases = []

        for event in events:
            c = self.b4a_classify_log_event(event)
            haystack = " ".join([
                c.get("id", ""),
                c.get("actor", ""),
                c.get("action", ""),
                c.get("category", ""),
                c.get("reason", ""),
                c.get("risk", ""),
            ]).lower()

            if query_l in haystack:
                score = 0

                if query_l in str(c.get("id", "")).lower():
                    score += 50

                if query_l in str(c.get("reason", "")).lower():
                    score += 30

                if query_l in str(c.get("category", "")).lower():
                    score += 15

                if c.get("category") == "Secrets / Tokens":
                    score += 20

                if c.get("action") == "platform.setting.updated":
                    score += 10

                if c.get("category") == "General":
                    score -= 25

                scored_bases.append((score, event))

        if scored_bases:
            scored_bases.sort(key=lambda x: x[0], reverse=True)
            base = scored_bases[0][1]

        if not base and hasattr(self, "b4b_find_single_event"):
            base = self.b4b_find_single_event(events, query)

        if not base:
            await ctx.send(embed=info_embed("Log Correlation", f"No base event matched `{query}`."))
            return

        base_c = self.b4a_classify_log_event(base)

        related = []

        for event in events:
            c = self.b4a_classify_log_event(event)

            if c.get("id") == base_c.get("id"):
                continue

            score = self.b4c_related_score(base, event)

            if self.b4c_is_meaningfully_related(base, event, score):
                related.append((event, score))

        related.sort(key=lambda x: x[1], reverse=True)

        lines = [
            "**Base event:**",
            f"ID: `{base_c['id'] or 'Unknown'}`",
            f"Time: `{base_c['createdAt']}`",
            f"Actor: `{base_c['actor']}`",
            f"Action: `{base_c['action']}`",
            f"Category: `{base_c['category']}`",
            f"Reason: {base_c['reason']}",
            "",
            f"Meaningfully related events found: `{len(related)}`",
            "",
        ]

        if not related:
            lines.append("No strongly related events found.")
        else:
            for idx, (event, score) in enumerate(related[:15], start=1):
                c = self.b4a_classify_log_event(event)
                reasons = self.b4c_relation_reasons(base, event)

                lines.extend([
                    f"**Related Event {idx}** — score `{score}`",
                    f"ID: `{c['id'] or 'Unknown'}`",
                    f"Time: `{c['createdAt']}`",
                    f"Actor: `{c['actor']}`",
                    f"Action: `{c['action']}`",
                    f"Category: `{c['category']}`",
                    f"Reason: {c['reason']}",
                    f"Relation: {', '.join(reasons) if reasons else 'strong similarity'}",
                    "",
                ])

        await self.send_paginated(ctx, "Log Correlation", lines)

    @logs.command(name="timeline")
    async def logs_timeline(self, ctx, limit: int = 25):
        """Show high-risk audit events as a timeline."""
        if not await require_admin(ctx):
            return

        limit = max(5, min(int(limit or 25), 50))

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        if not events:
            await ctx.send(embed=info_embed("Audit Timeline", "No high-risk audit events returned by the API."))
            return

        lines = [
            f"Endpoint: `{data.get('endpoint')}`",
            f"API: `HTTP {data.get('status')}`",
            f"Events returned: `{len(events)}`",
            f"Showing: `{min(limit, len(events))}`",
            "",
        ]

        lines.extend(self.b4c_timeline_lines(events, limit=limit))

        await self.send_paginated(ctx, "High-Risk Audit Timeline", lines)

    @logs.command(name="window")
    async def logs_window(self, ctx, minutes: int = 60):
        """Show high-risk audit events from the last X minutes."""
        if not await require_admin(ctx):
            return

        minutes = max(1, min(int(minutes or 60), 10080))

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        matches = []

        for event in events:
            age = self.b4c_minutes_ago(event)

            if age >= 0 and age <= minutes:
                matches.append(event)

        if not matches:
            await ctx.send(embed=info_embed("Audit Time Window", f"No high-risk audit events found in the last `{minutes}` minutes."))
            return

        lines = [
            f"Window: last `{minutes}` minutes",
            f"Matching events: `{len(matches)}`",
            "",
        ]

        lines.extend(self.b4c_timeline_lines(matches, limit=50))

        await self.send_paginated(ctx, "Audit Time Window", lines)

    @logs.command(name="actor-timeline")
    async def logs_actor_timeline(self, ctx, *, actor: str):
        """Show timeline for one actor/user ID."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        actor_l = str(actor or "").lower().strip()

        matches = [
            event for event in events
            if actor_l in self.b4a_log_event_actor(event).lower()
        ]

        if not matches:
            await ctx.send(embed=info_embed("Actor Timeline", f"No high-risk audit timeline events found for `{actor}`."))
            return

        lines = [
            f"Actor query: `{actor}`",
            f"Matching events: `{len(matches)}`",
            "",
        ]

        lines.extend(self.b4c_timeline_lines(matches, limit=50))

        await self.send_paginated(ctx, "Actor Audit Timeline", lines)

    @logs.command(name="clusters")
    async def logs_clusters(self, ctx):
        """Cluster high-risk audit events by actor/category/action."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        clusters = self.b4c_cluster_events(events)

        if not clusters:
            await ctx.send(embed=info_embed("Audit Clusters", "No high-risk audit events available to cluster."))
            return

        lines = [
            f"Events analysed: `{len(events)}`",
            f"Clusters: `{len(clusters)}`",
            "",
        ]

        for idx, cluster in enumerate(clusters[:25], start=1):
            reasons = sorted(cluster["reasons"].items(), key=lambda x: x[1], reverse=True)

            lines.extend([
                f"**Cluster {idx}**",
                f"Actor: `{cluster['actor']}`",
                f"Category: `{cluster['category']}`",
                f"Action: `{cluster['action']}`",
                f"Severity: `{cluster['severity']}` | Events: `{len(cluster['events'])}`",
                f"Newest: `{cluster['newest']}`",
                "Top reasons:",
            ])

            for reason, count in reasons[:5]:
                lines.append(f"- {reason} — `{count}`")

            lines.append("")

        await self.send_paginated(ctx, "Audit Event Clusters", lines)

    @logs.command(name="suspicious")
    async def logs_suspicious(self, ctx):
        """Rank high-risk audit events by suspiciousness."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        ranked = self.b4c_suspicious_events(events)

        if not ranked:
            await ctx.send(embed=ok_embed("Suspicious Audit Events", "No unusually suspicious high-risk events were found beyond the normal high-risk feed."))
            return

        lines = [
            f"Events analysed: `{len(events)}`",
            f"Suspicious matches: `{len(ranked)}`",
            "",
        ]

        for idx, (event, score, flags) in enumerate(ranked[:20], start=1):
            c = self.b4a_classify_log_event(event)

            lines.extend([
                f"🚨 **Suspicious Event {idx}**",
                f"Score: `{score}`",
                f"ID: `{c['id'] or 'Unknown'}`",
                f"Time: `{c['createdAt']}`",
                f"Actor: `{c['actor']}`",
                f"Action: `{c['action']}`",
                f"Category: `{c['category']}`",
                f"Reason: {c['reason']}",
                f"Flags: {', '.join(flags)}",
                f"Explain: `!mcore logs explain {c['id'] or c['reason'][:40]}`",
                "",
            ])

        await self.send_paginated(ctx, "Suspicious Audit Events", lines)


    @logs.command(name="find")
    async def logs_find(self, ctx, *, query: str):
        """Search high-risk audit logs by keyword."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []
        matches = self.b4b_find_events(events, query, limit=20)

        if not matches:
            await ctx.send(embed=info_embed("Log Search", f"No high-risk audit events matched `{query}`."))
            return

        lines = [
            f"Query: `{query}`",
            f"Matches: `{len(matches)}`",
            "",
        ]

        lines.extend(self.b4a_event_lines(matches, limit=20))
        await self.send_paginated(ctx, "Log Search Results", lines)

    @logs.command(name="actor")
    async def logs_actor(self, ctx, *, actor: str):
        """Show high-risk logs for one actor/user ID."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        matches = []

        actor_l = actor.lower().strip()

        for event in events:
            event_actor = self.b4a_log_event_actor(event).lower()
            if actor_l in event_actor:
                matches.append(event)

        if not matches:
            await ctx.send(embed=info_embed("Actor Logs", f"No high-risk audit events found for `{actor}`."))
            return

        lines = [
            f"Actor query: `{actor}`",
            f"Matching events: `{len(matches)}`",
            "",
        ]

        lines.extend(self.b4a_event_lines(matches, limit=20))
        await self.send_paginated(ctx, "Actor Audit Logs", lines)

    @logs.command(name="action")
    async def logs_action(self, ctx, *, action: str):
        """Show high-risk logs matching an action."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        action_l = action.lower().strip()
        matches = [
            event for event in events
            if action_l in self.b4a_log_event_action(event).lower()
        ]

        if not matches:
            await ctx.send(embed=info_embed("Action Logs", f"No high-risk audit events found for action `{action}`."))
            return

        lines = [
            f"Action query: `{action}`",
            f"Matching events: `{len(matches)}`",
            "",
        ]

        lines.extend(self.b4a_event_lines(matches, limit=20))
        await self.send_paginated(ctx, "Action Audit Logs", lines)

    @logs.command(name="risk")
    async def logs_risk(self, ctx, risk: str = "high"):
        """Show high-risk logs by risk level."""
        if not await require_admin(ctx):
            return

        risk = str(risk or "high").lower().strip()

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        matches = [
            event for event in events
            if self.b4a_log_event_risk(event).lower() == risk
            or self.b4a_classify_log_event(event).get("severity") == risk
        ]

        if not matches:
            await ctx.send(embed=info_embed("Risk Logs", f"No audit events found for risk `{risk}`."))
            return

        lines = [
            f"Risk: `{risk}`",
            f"Matching events: `{len(matches)}`",
            "",
        ]

        lines.extend(self.b4a_event_lines(matches, limit=20))
        await self.send_paginated(ctx, "Risk Audit Logs", lines)

    @logs.command(name="event")
    async def logs_event(self, ctx, *, event_id: str):
        """Show one high-risk audit event by ID or search query."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        event = self.b4b_find_single_event(events, event_id)

        if not event:
            await ctx.send(embed=info_embed("Audit Event", f"No event matched `{event_id}`."))
            return

        lines = self.b4a_event_lines([event], limit=1)
        await self.send_paginated(ctx, "Audit Event Detail", lines)

    @logs.command(name="explain")
    async def logs_explain(self, ctx, *, query: str):
        """Explain one high-risk audit event by ID or search query."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        event = self.b4b_find_single_event(events, query)

        if not event:
            await ctx.send(embed=info_embed("Explain Log Event", f"No event matched `{query}`."))
            return

        lines = self.b4b_event_explanation_lines(event)
        await self.send_paginated(ctx, "Audit Event Explanation", lines)

    @logs.command(name="highrisk")
    async def logs_highrisk(self, ctx):
        """Show newest high-risk audit log entries."""
        if not await require_admin(ctx):
            return

        try:
            data = await self.b4a_fetch_highrisk_events(ctx.guild)
        except Exception as e:
            await ctx.send(embed=error_embed("High-risk logs failed", f"Could not fetch `/bot/audit/highrisk`.\n`{type(e).__name__}: {e}`"))
            return

        events = data.get("events") or []

        if not events:
            await ctx.send(embed=ok_embed("High-Risk Audit Logs", "No high-risk audit events returned by the API."))
            return

        lines = [
            f"Endpoint: `{data.get('endpoint')}`",
            f"API: `HTTP {data.get('status')}`",
            f"Events returned: `{len(events)}`",
            "",
        ]

        lines.extend(self.b4a_event_lines(events, limit=12))

        await self.send_paginated(ctx, "High-Risk Audit Logs", lines)

    @logs.command(name="audit")
    async def logs_audit(self, ctx):
        """Show high-risk audit summary."""
        if not await require_admin(ctx):
            return

        try:
            data = await self.b4a_fetch_highrisk_events(ctx.guild)
        except Exception as e:
            await ctx.send(embed=error_embed("Audit summary failed", f"Could not fetch audit logs.\n`{type(e).__name__}: {e}`"))
            return

        events = data.get("events") or []

        lines = [
            f"Endpoint: `{data.get('endpoint')}`",
            f"API: `HTTP {data.get('status')}`",
            "",
        ]

        lines.extend(self.b4a_log_summary_lines(events))

        await self.send_paginated(ctx, "Audit Log Summary", lines)

    @logs.command(name="summary")
    async def logs_summary(self, ctx):
        """Show operations log summary."""
        if not await require_admin(ctx):
            return

        await self.logs_audit(ctx)

    @logs.command(name="actors")
    async def logs_actors(self, ctx):
        """Group high-risk logs by actor."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []
        classified = [self.b4a_classify_log_event(e) for e in events]
        grouped = self.b4a_group_counts([x["actor"] for x in classified])

        lines = [
            f"Events analysed: `{len(events)}`",
            "",
            "**Actors:**",
        ]

        for actor, count in grouped[:25]:
            lines.append(f"- `{actor}` — `{count}` event(s)")

        if not grouped:
            lines.append("- None")

        await self.send_paginated(ctx, "Audit Log Actors", lines)

    @logs.command(name="actions")
    async def logs_actions(self, ctx):
        """Group high-risk logs by action."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []
        classified = [self.b4a_classify_log_event(e) for e in events]
        grouped = self.b4a_group_counts([x["action"] for x in classified])

        lines = [
            f"Events analysed: `{len(events)}`",
            "",
            "**Actions:**",
        ]

        for action, count in grouped[:25]:
            lines.append(f"- `{action}` — `{count}` event(s)")

        if not grouped:
            lines.append("- None")

        await self.send_paginated(ctx, "Audit Log Actions", lines)

    @logs.command(name="reasons")
    async def logs_reasons(self, ctx):
        """Group high-risk logs by reason."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []
        classified = [self.b4a_classify_log_event(e) for e in events]
        grouped = self.b4a_group_counts([x["reason"] for x in classified])

        lines = [
            f"Events analysed: `{len(events)}`",
            "",
            "**Reasons:**",
        ]

        for reason, count in grouped[:30]:
            lines.append(f"- {reason} — `{count}` event(s)")

        if not grouped:
            lines.append("- None")

        await self.send_paginated(ctx, "Audit Log Reasons", lines)

    @logs.command(name="secrets")
    async def logs_secrets(self, ctx):
        """Show secret/token/key/webhook related high-risk logs."""
        if not await require_admin(ctx):
            return

        data = await self.b4a_fetch_highrisk_events(ctx.guild)
        events = data.get("events") or []

        matches = []

        for event in events:
            reason = self.b4a_log_event_reason(event).lower()
            action = self.b4a_log_event_action(event).lower()

            if any(word in reason or word in action for word in ["secret", "token", "key", "webhook", "stripe", "roblox"]):
                matches.append(event)

        if not matches:
            await ctx.send(embed=ok_embed("Secret/Token Audit Logs", "No secret/token/key/webhook related high-risk events were found."))
            return

        lines = [
            f"Matching events: `{len(matches)}`",
            "",
        ]

        lines.extend(self.b4a_event_lines(matches, limit=15))

        await self.send_paginated(ctx, "Secret/Token Audit Logs", lines)

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

        await self.alert_lifecycle_send(


            ctx.guild if 'ctx' in locals() and ctx else channel.guild,


            rule_name if 'rule_name' in locals() else rule if 'rule' in locals() else name if 'name' in locals() else 'alert',


            channel,


            content=content or None,


            embed=e,


            allowed_mentions=self.notify_allowed_mentions(),


            item=payload if 'payload' in locals() else data if 'data' in locals() else {'count': count if 'count' in locals() else 1},


        ),

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

        await self.b2_alert_guarded_send(ctx.guild, channel, 
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



    def capability_defaults(self) -> dict:
        return {
            "core_admin": ["founder", "administrator"],

            "management_view": ["founder", "director", "executive"],
            "finance_view": ["director", "executive"],

            "general_support": ["support_agent", "support_lead"],
            "support_lead": ["support_lead"],
            "billing_support": ["billing_support"],
            "technical_support": ["technical_support"],
            "security_support": ["security_support"],

            "moderation": ["moderator", "senior_moderator"],
            "incident_response": ["incident_response"],
            "audit_review": ["audit_reviewer"],

            "security_admin": ["security_admin"],
            "infrastructure_admin": ["infrastructure_admin"],

            "development_read": ["developer", "lead_developer", "qa_tester", "release_manager"],
            "backend_access": ["developer", "lead_developer"],
            "production_access": ["lead_developer", "infrastructure_admin"],
            "release_manager": ["release_manager"],
            "qa_testing": ["qa_tester"],
            "design_access": ["designer"],

            "discord_systems": ["developer", "lead_developer", "infrastructure_admin"],
            "roblox_systems": ["developer", "lead_developer", "infrastructure_admin"],
            "automation_access": ["lead_developer", "infrastructure_admin"],
        }

    def capability_keyword_match(self, role_name: str, keyword: str) -> bool:
        role_slug = self.route_slug(role_name)
        key_slug = self.route_slug(keyword)

        return key_slug.replace("_", "") in role_slug.replace("_", "")

    def build_default_capabilities(self, guild: discord.Guild) -> dict[str, list[int]]:
        caps = {key: [] for key in self.capability_defaults().keys()}

        for role in guild.roles:
            if role == guild.default_role or role.managed:
                continue

            for cap, keywords in self.capability_defaults().items():
                for keyword in keywords:
                    if self.capability_keyword_match(role.name, keyword):
                        if role.id not in caps[cap]:
                            caps[cap].append(role.id)

        return caps

    async def saved_capabilities(self, guild: discord.Guild) -> dict[str, list[int]]:
        cfg = await get_core_config(self.bot)
        caps = await cfg.guild(guild).capabilities()
        caps = caps or {}

        if not caps:
            caps = self.build_default_capabilities(guild)

        clean = {}

        for key, ids in caps.items():
            clean[self.route_slug(key)] = [int(x) for x in ids or []]

        for key in self.capability_defaults().keys():
            clean.setdefault(key, [])

        return clean

    async def save_capabilities(self, guild: discord.Guild, caps: dict[str, list[int]]):
        cfg = await get_core_config(self.bot)
        clean = {}

        for key, ids in caps.items():
            clean[self.route_slug(key)] = list(dict.fromkeys(int(x) for x in ids or []))

        await cfg.guild(guild).capabilities.set(clean)

    def capability_role_lines(self, guild: discord.Guild, caps: dict[str, list[int]], capability: str) -> list[str]:
        capability = self.route_slug(capability)
        lines = []

        for rid in caps.get(capability, []):
            role = guild.get_role(int(rid))
            lines.append(f"• {role.mention if role else f'`missing:{rid}`'}")

        return lines or ["No roles mapped."]

    @mcore.group(name="capabilities", aliases=["capability"], invoke_without_command=True)
    async def capabilities(self, ctx):
        """Least-privilege role capability management."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)

        lines = [
            f"Saved capabilities: `{len(caps)}`",
            "",
            "**Commands:**",
            "`!mcore capabilities defaults`",
            "`!mcore capabilities applydefaults`",
            "`!mcore capabilities list`",
            "`!mcore capabilities matrix`",
            "`!mcore capabilities check @Role`",
            "`!mcore capabilities roles release_manager`",
            "`!mcore capabilities add release_manager @Role`",
            "`!mcore capabilities remove release_manager @Role`",
            "`!mcore capabilities clear release_manager`",
            "",
            "Default is deny. Only explicit capability mappings give access.",
        ]

        await self.send_paginated(ctx, "Mattis Capability System", lines)

    @capabilities.command(name="defaults")
    async def capabilities_defaults(self, ctx):
        """Preview default capability mapping without saving."""
        if not await require_admin(ctx):
            return

        caps = self.build_default_capabilities(ctx.guild)
        lines = []

        for cap in sorted(caps.keys()):
            lines.append(f"**{cap}**")
            lines.extend(self.capability_role_lines(ctx.guild, caps, cap))
            lines.append("")

        await self.send_paginated(ctx, "Default Capability Preview", lines)

    @capabilities.command(name="applydefaults")
    async def capabilities_applydefaults(self, ctx):
        """Save default capability mapping."""
        if not await require_admin(ctx):
            return

        caps = self.build_default_capabilities(ctx.guild)
        await self.save_capabilities(ctx.guild, caps)

        lines = [f"Saved `{len(caps)}` capabilities from current Discord roles.", ""]

        for cap in sorted(caps.keys()):
            lines.append(f"`{cap}` → `{len(caps[cap])}` roles")

        await self.send_paginated(ctx, "Default Capabilities Applied", lines, color=discord.Color.green())

    @capabilities.command(name="list")
    async def capabilities_list(self, ctx):
        """List saved capabilities."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)
        lines = []

        for cap in sorted(caps.keys()):
            lines.append(f"`{cap}` → `{len(caps.get(cap, []))}` roles")

        await self.send_paginated(ctx, "Saved Capabilities", lines)

    @capabilities.command(name="roles")
    async def capabilities_roles(self, ctx, capability: str):
        """Show roles mapped to one capability."""
        if not await require_admin(ctx):
            return

        capability = self.route_slug(capability)
        caps = await self.saved_capabilities(ctx.guild)

        if capability not in caps:
            await ctx.send(embed=error_embed("Unknown capability", f"`{capability}` is not saved."))
            return

        lines = [f"Capability: `{capability}`", ""]
        lines.extend(self.capability_role_lines(ctx.guild, caps, capability))

        await self.send_paginated(ctx, f"Capability Roles: {capability}", lines)

    @capabilities.command(name="add")
    async def capabilities_add(self, ctx, capability: str, role: discord.Role):
        """Add a role to a capability."""
        if not await require_admin(ctx):
            return

        if role == ctx.guild.default_role or role.managed or self.is_separator_role(role):
            await ctx.send(embed=error_embed("Unsafe role", "I will not map @everyone, managed roles, or separator/header roles."))
            return

        capability = self.route_slug(capability)
        caps = await self.saved_capabilities(ctx.guild)
        caps.setdefault(capability, [])

        if role.id not in caps[capability]:
            caps[capability].append(role.id)

        await self.save_capabilities(ctx.guild, caps)

        await ctx.send(embed=ok_embed("Capability role added", f"{role.mention} added to `{capability}`."))

    @capabilities.command(name="remove")
    async def capabilities_remove(self, ctx, capability: str, role: discord.Role):
        """Remove a role from a capability."""
        if not await require_admin(ctx):
            return

        capability = self.route_slug(capability)
        caps = await self.saved_capabilities(ctx.guild)
        caps.setdefault(capability, [])
        caps[capability] = [rid for rid in caps[capability] if int(rid) != role.id]

        await self.save_capabilities(ctx.guild, caps)

        await ctx.send(embed=ok_embed("Capability role removed", f"{role.mention} removed from `{capability}`."))

    @capabilities.command(name="clear")
    async def capabilities_clear(self, ctx, capability: str):
        """Clear all roles from one capability."""
        if not await require_admin(ctx):
            return

        capability = self.route_slug(capability)
        caps = await self.saved_capabilities(ctx.guild)
        caps[capability] = []

        await self.save_capabilities(ctx.guild, caps)

        await ctx.send(embed=ok_embed("Capability cleared", f"`{capability}` now has no roles."))

    @capabilities.command(name="check")
    async def capabilities_check(self, ctx, role: discord.Role):
        """Check what capabilities a role has."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)

        lines = [
            f"Role: {role.mention}",
            f"Position: `{role.position}`",
            f"Discord Administrator: `{'yes' if role.permissions.administrator else 'no'}`",
            "",
            "**Capabilities:**",
        ]

        found = False

        for cap in sorted(caps.keys()):
            if role.id in [int(x) for x in caps.get(cap, [])]:
                lines.append(f"✅ `{cap}`")
                found = True

        if not found:
            lines.append("No capabilities mapped.")

        await self.send_paginated(ctx, "Role Capability Check", lines)

    @capabilities.command(name="matrix")
    async def capabilities_matrix(self, ctx):
        """Show every role and its exact capabilities."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)
        lines = []

        for role in sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True):
            if role == ctx.guild.default_role or role.managed:
                continue

            role_caps = []

            for cap in sorted(caps.keys()):
                if role.id in [int(x) for x in caps.get(cap, [])]:
                    role_caps.append(cap)

            if role_caps:
                lines.append(f"{role.mention} → `{', '.join(role_caps)}`")
            else:
                lines.append(f"{role.mention} → `no bot access`")

        await self.send_paginated(ctx, "Capability Matrix", lines)


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



    def doctor_compact_slug(self, value: str) -> str:
        return self.route_slug(value).replace("_", "")

    def doctor_find_role(self, guild: discord.Guild, role_name: str):
        wanted = self.doctor_compact_slug(role_name)

        for role in guild.roles:
            if role == guild.default_role or role.managed:
                continue

            if self.doctor_compact_slug(role.name) == wanted:
                return role

        for role in guild.roles:
            if role == guild.default_role or role.managed:
                continue

            if wanted in self.doctor_compact_slug(role.name):
                return role

        return None

    def doctor_role_caps(self, role: discord.Role, caps: dict[str, list[int]]) -> set[str]:
        found = set()

        for cap, ids in caps.items():
            if role and role.id in [int(x) for x in ids or []]:
                found.add(cap)

        return found

    def doctor_command_rules(self) -> dict:
        return {
            "core": {
                "label": "!mcore routecheck / config / dangerous settings",
                "capabilities": ["core_admin"],
            },
            "billing": {
                "label": "!mbilling summary / invoices / payments / refunds",
                "capabilities": ["billing_support", "finance_view"],
            },
            "support": {
                "label": "!msupport / !mcrm general support",
                "capabilities": ["general_support", "support_lead"],
            },
            "technical_support": {
                "label": "technical support / troubleshooting",
                "capabilities": ["technical_support", "support_lead"],
            },
            "security": {
                "label": "!msecurity risks / suspicious activity / account compromise",
                "capabilities": ["security_support", "security_admin", "incident_response"],
            },
            "audit": {
                "label": "!maudit recent / high-risk audit review",
                "capabilities": ["audit_review", "security_admin", "management_view"],
            },
            "incident": {
                "label": "!mincident active / incident checks",
                "capabilities": ["incident_response", "security_admin", "production_access", "management_view"],
            },
            "status_safe": {
                "label": "!mstatus safe/system visibility",
                "capabilities": ["technical_support", "development_read", "production_access", "infrastructure_admin"],
            },
            "backend": {
                "label": "!mmodules / backend/API/module checks",
                "capabilities": ["backend_access"],
            },
            "production": {
                "label": "production diagnostics / production status",
                "capabilities": ["production_access", "release_manager", "infrastructure_admin"],
            },
            "release": {
                "label": "release manager / staging / production release commands",
                "capabilities": ["release_manager"],
            },
            "qa": {
                "label": "QA/testing/staging validation",
                "capabilities": ["qa_testing"],
            },
            "design": {
                "label": "design/assets/UI checks",
                "capabilities": ["design_access"],
            },
            "discord_systems": {
                "label": "!mdiscord integration/system checks",
                "capabilities": ["discord_systems", "infrastructure_admin"],
            },
            "roblox_systems": {
                "label": "!mroblox integration/system checks",
                "capabilities": ["roblox_systems", "infrastructure_admin"],
            },
            "automation": {
                "label": "!mautomation workers / failed jobs",
                "capabilities": ["automation_access", "infrastructure_admin"],
            },
            "workspace": {
                "label": "!mworkspace / safe staff workspace",
                "capabilities": [
                    "general_support",
                    "support_lead",
                    "billing_support",
                    "technical_support",
                    "security_support",
                    "moderation",
                    "incident_response",
                    "audit_review",
                    "development_read",
                    "backend_access",
                    "production_access",
                    "release_manager",
                    "qa_testing",
                    "design_access",
                    "management_view",
                    "finance_view",
                    "infrastructure_admin",
                    "security_admin",
                ],
            },
        }

    def doctor_can_use_rule(self, role: discord.Role, caps: dict[str, list[int]], rule_key: str) -> bool:
        if not role:
            return False

        role_caps = self.doctor_role_caps(role, caps)

        if "core_admin" in role_caps:
            return True

        rules = self.doctor_command_rules()
        rule = rules.get(rule_key)

        if not rule:
            return False

        return bool(role_caps.intersection(set(rule["capabilities"])))

    def doctor_expected_role_tests(self) -> list[dict]:
        return [
            {
                "role": "Billing Support",
                "allow": ["billing", "workspace"],
                "deny": ["core", "support", "technical_support", "security", "audit", "incident", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Technical Support",
                "allow": ["technical_support", "status_safe", "workspace"],
                "deny": ["core", "billing", "support", "security", "audit", "incident", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Security Support",
                "allow": ["security", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "audit", "incident", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Support Agent",
                "allow": ["support", "workspace"],
                "deny": ["core", "billing", "technical_support", "security", "audit", "incident", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Support Lead",
                "allow": ["support", "technical_support", "workspace"],
                "deny": ["core", "billing", "security", "audit", "incident", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Release Manager",
                "allow": ["release", "status_safe", "production", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "backend", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Designer",
                "allow": ["design", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "status_safe", "backend", "production", "release", "qa", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "QA Tester",
                "allow": ["qa", "status_safe", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "backend", "production", "release", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Developer",
                "allow": ["status_safe", "backend", "discord_systems", "roblox_systems", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "production", "release", "qa", "design", "automation"],
            },
            {
                "role": "Lead Developer",
                "allow": ["status_safe", "backend", "production", "incident", "discord_systems", "roblox_systems", "automation", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "release", "qa", "design"],
            },
            {
                "role": "Infrastructure Admin",
                "allow": ["status_safe", "production", "incident", "discord_systems", "roblox_systems", "automation", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "backend", "release", "qa", "design"],
            },
            {
                "role": "Security Admin",
                "allow": ["security", "audit", "incident", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Audit Reviewer",
                "allow": ["audit", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "incident", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Incident Response",
                "allow": ["security", "incident", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "audit", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Director",
                "allow": ["billing", "audit", "incident"],
                "deny": ["core", "support", "technical_support", "security", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Executive",
                "allow": ["billing", "audit", "incident"],
                "deny": ["core", "support", "technical_support", "security", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
        ]

    def doctor_simulate_role_tests(self, guild: discord.Guild, caps: dict[str, list[int]]) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        rules = self.doctor_command_rules()

        for test in self.doctor_expected_role_tests():
            role = self.doctor_find_role(guild, test["role"])

            if not role:
                lines.append(f"⚠️ `{test['role']}` role not found.")
                warnings += 1
                continue

            role_caps = sorted(self.doctor_role_caps(role, caps))
            role_lines = [f"**{role.mention}**", f"Capabilities: `{', '.join(role_caps) if role_caps else 'none'}`"]

            for allow_key in test["allow"]:
                allowed = self.doctor_can_use_rule(role, caps, allow_key)
                label = rules[allow_key]["label"]

                if allowed:
                    role_lines.append(f"✅ should allow `{allow_key}` — {label}")
                else:
                    role_lines.append(f"❌ FAIL should allow `{allow_key}` — {label}")
                    failures += 1

            for deny_key in test["deny"]:
                allowed = self.doctor_can_use_rule(role, caps, deny_key)
                label = rules[deny_key]["label"]

                if allowed:
                    role_lines.append(f"❌ FAIL should deny `{deny_key}` — {label}")
                    failures += 1
                else:
                    role_lines.append(f"✅ denied `{deny_key}` — {label}")

            lines.append("\n".join(role_lines))

        return lines, failures, warnings

    def doctor_unsafe_capability_mappings(self, guild: discord.Guild, caps: dict[str, list[int]]) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        for capability, role_ids in sorted(caps.items()):
            for rid in role_ids or []:
                role = guild.get_role(int(rid))

                if not role:
                    lines.append(f"⚠️ `{capability}` has missing role id `{rid}`.")
                    warnings += 1
                    continue

                if role == guild.default_role:
                    lines.append(f"❌ `{capability}` is mapped to @everyone.")
                    failures += 1
                    continue

                if role.managed:
                    lines.append(f"❌ `{capability}` is mapped to managed role {role.mention}.")
                    failures += 1
                    continue

                if self.is_separator_role(role):
                    lines.append(f"❌ `{capability}` is mapped to separator/header role {role.mention}.")
                    failures += 1
                    continue

                if role.name.lower().strip() in ["bots", "bot"]:
                    lines.append(f"❌ `{capability}` is mapped to bot role {role.mention}.")
                    failures += 1
                    continue

        if not lines:
            lines.append("✅ No unsafe capability role mappings found.")

        return lines, failures, warnings

    def doctor_source_gate_audit(self) -> tuple[list[str], int, int]:
        from pathlib import Path

        lines = []
        failures = 0
        warnings = 0

        bad_gates = [
            "require_staff(ctx)",
            "require_development(ctx)",
            "require_security(ctx)",
            "require_support(ctx)",
        ]

        try:
            root = Path(__file__).resolve().parents[1]

            for path in sorted(root.glob("mattis_*/*.py")):
                if path.name == "shared_mattis.py":
                    continue

                if path.parent.name == "mattis_core":
                    continue

                text = path.read_text(errors="ignore")
                found = []

                for line_no, line in enumerate(text.splitlines(), start=1):
                    for gate in bad_gates:
                        if gate in line:
                            found.append(f"L{line_no}: `{line.strip()}`")

                if found:
                    failures += len(found)
                    lines.append(f"❌ **{path.parent.name}/{path.name}** still has broad gates:\n" + "\n".join(found))

        except Exception as exc:
            warnings += 1
            lines.append(f"⚠️ Could not scan command source gates: `{type(exc).__name__}: {exc}`")

        if not lines:
            lines.append("✅ No broad command gates found in non-core cogs.")

        return lines, failures, warnings


    async def doctor_get_routes_map(self, guild: discord.Guild) -> dict:
        import inspect

        # First try live helper methods already on MattisCore.
        for method_name in [
            "saved_routes",
            "get_saved_routes",
            "load_routes",
            "get_route_map",
            "saved_channel_routes",
            "channel_routes",
        ]:
            method = getattr(self, method_name, None)

            if not method or not callable(method):
                continue

            try:
                sig = inspect.signature(method)
                params = list(sig.parameters)

                if len(params) == 0:
                    value = await method()
                else:
                    value = await method(guild)

                if isinstance(value, dict) and value:
                    return value
            except Exception:
                pass

        cfg = await get_core_config(self.bot)

        # Then try known config names.
        names = [
            "routes",
            "channel_routes",
            "route_map",
            "routing",
            "saved_routes",
            "channel_map",
            "route_settings",
            "routes_map",
            "route_data",
        ]

        for name in names:
            try:
                value = await getattr(cfg.guild(guild), name)()
                if isinstance(value, dict) and value:
                    return value
            except Exception:
                pass

        try:
            all_data = await cfg.guild(guild).all()
        except Exception:
            all_data = {}

        for name in names:
            value = all_data.get(name)
            if isinstance(value, dict) and value:
                return value

        def value_channel_id(value):
            if isinstance(value, dict):
                return value.get("channel_id") or value.get("id")
            return value

        best = {}
        best_score = 0

        def score_dict(obj: dict) -> int:
            score = 0

            for key, value in obj.items():
                key_text = str(key).lower()
                channel_id = value_channel_id(value)

                try:
                    if guild.get_channel(int(channel_id)):
                        score += 5
                except Exception:
                    pass

                if any(word in key_text for word in [
                    "billing",
                    "support",
                    "security",
                    "development",
                    "management",
                    "observatory",
                    "logs",
                    "route",
                    "channel",
                    "ticket",
                    "incident",
                    "audit",
                    "message",
                    "member",
                ]):
                    score += 2

            return score

        def scan(obj):
            nonlocal best, best_score

            if not isinstance(obj, dict):
                return

            score = score_dict(obj)

            if score > best_score and len(obj) >= 3:
                best = obj
                best_score = score

            for value in obj.values():
                if isinstance(value, dict):
                    scan(value)

        scan(all_data)

        return best or {}

    async def doctor_route_audit(self, guild: discord.Guild) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        try:
            cfg = await get_core_config(self.bot)
            routes = await cfg.guild(guild).routes()
            routes = routes or {}

            missing = []
            custom = []

            for key, value in routes.items():
                channel_id = value

                if isinstance(value, dict):
                    channel_id = value.get("channel_id") or value.get("id")

                try:
                    channel = guild.get_channel(int(channel_id))
                except Exception:
                    channel = None

                if not channel:
                    missing.append(f"`{key}` → missing `{channel_id}`")

                if key == "support":
                    custom.append(f"`{key}` → custom fallback route")

            lines.append(f"Saved routes: `{len(routes)}`")

            if missing:
                failures += len(missing)
                lines.append("❌ Missing channels:\n" + "\n".join(missing[:25]))
            else:
                lines.append("✅ Missing channels: `0`")

            if custom:
                lines.append("ℹ️ Custom fallback routes:\n" + "\n".join(custom))

        except Exception as exc:
            warnings += 1
            lines.append(f"⚠️ Could not audit routes: `{type(exc).__name__}: {exc}`")

        return lines, failures, warnings

    async def doctor_engine_settings(self, guild: discord.Guild) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        try:
            cfg = await get_core_config(self.bot)

            checks = [
                ("log_settings", "Logs"),
                ("alert_settings", "Alerts"),
                ("notify_settings", "Notify"),
                ("eventlog_settings", "Event logs"),
            ]

            for attr, label in checks:
                try:
                    data = await getattr(cfg.guild(guild), attr)()
                    data = data or {}

                    enabled = data.get("enabled")
                    if enabled is None:
                        enabled = data.get("global_enabled")

                    if enabled is True:
                        lines.append(f"✅ {label}: enabled")
                    elif enabled is False:
                        lines.append(f"⚠️ {label}: disabled")
                        warnings += 1
                    else:
                        lines.append(f"ℹ️ {label}: settings found")
                except Exception as exc:
                    lines.append(f"⚠️ {label}: could not read settings `{type(exc).__name__}`")
                    warnings += 1

        except Exception as exc:
            warnings += 1
            lines.append(f"⚠️ Could not audit engine settings: `{type(exc).__name__}: {exc}`")

        return lines, failures, warnings


    def doctor_compact_slug(self, value: str) -> str:
        return self.route_slug(value).replace("_", "")

    def doctor_find_role(self, guild: discord.Guild, role_name: str):
        wanted = self.doctor_compact_slug(role_name)

        for role in guild.roles:
            if role == guild.default_role or role.managed:
                continue

            if self.doctor_compact_slug(role.name) == wanted:
                return role

        for role in guild.roles:
            if role == guild.default_role or role.managed:
                continue

            if wanted in self.doctor_compact_slug(role.name):
                return role

        return None

    def doctor_role_caps(self, role: discord.Role, caps: dict[str, list[int]]) -> set[str]:
        found = set()

        for cap, ids in caps.items():
            if role and role.id in [int(x) for x in ids or []]:
                found.add(cap)

        return found

    def doctor_command_rules(self) -> dict:
        return {
            "core": {
                "label": "!mcore config/routes/capabilities/logs/alerts/eventlogs",
                "capabilities": ["core_admin"],
            },
            "billing": {
                "label": "!mbilling invoices/payments/refunds/chargebacks",
                "capabilities": ["billing_support", "finance_view"],
            },
            "support": {
                "label": "!msupport / !mcrm general support",
                "capabilities": ["general_support", "support_lead"],
            },
            "technical_support": {
                "label": "technical support / troubleshooting",
                "capabilities": ["technical_support", "support_lead"],
            },
            "security": {
                "label": "!msecurity risks/suspicious/account compromise",
                "capabilities": ["security_support", "security_admin", "incident_response"],
            },
            "audit": {
                "label": "!maudit high-risk audit review",
                "capabilities": ["audit_review", "security_admin", "management_view"],
            },
            "incident": {
                "label": "!mincident active/critical incidents",
                "capabilities": ["incident_response", "security_admin", "production_access", "management_view"],
            },
            "status_safe": {
                "label": "!mstatus safe/system visibility",
                "capabilities": ["technical_support", "development_read", "production_access", "infrastructure_admin"],
            },
            "backend": {
                "label": "!mmodules backend/API/module checks",
                "capabilities": ["backend_access"],
            },
            "production": {
                "label": "production diagnostics / system health",
                "capabilities": ["production_access", "release_manager", "infrastructure_admin"],
            },
            "release": {
                "label": "release manager / staging / production releases",
                "capabilities": ["release_manager"],
            },
            "qa": {
                "label": "QA/testing/staging validation",
                "capabilities": ["qa_testing"],
            },
            "design": {
                "label": "design/assets/UI checks",
                "capabilities": ["design_access"],
            },
            "discord_systems": {
                "label": "!mdiscord integration/system checks",
                "capabilities": ["discord_systems", "infrastructure_admin"],
            },
            "roblox_systems": {
                "label": "!mroblox integration/system checks",
                "capabilities": ["roblox_systems", "infrastructure_admin"],
            },
            "automation": {
                "label": "!mautomation workers/failed jobs",
                "capabilities": ["automation_access", "infrastructure_admin"],
            },
            "workspace": {
                "label": "!mworkspace safe staff workspace",
                "capabilities": [
                    "general_support",
                    "support_lead",
                    "billing_support",
                    "technical_support",
                    "security_support",
                    "moderation",
                    "incident_response",
                    "audit_review",
                    "development_read",
                    "backend_access",
                    "production_access",
                    "release_manager",
                    "qa_testing",
                    "design_access",
                    "management_view",
                    "finance_view",
                    "infrastructure_admin",
                    "security_admin",
                ],
            },
        }

    def doctor_can_use_rule(self, role: discord.Role, caps: dict[str, list[int]], rule_key: str) -> bool:
        if not role:
            return False

        role_caps = self.doctor_role_caps(role, caps)

        if "core_admin" in role_caps:
            return True

        rule = self.doctor_command_rules().get(rule_key)
        if not rule:
            return False

        return bool(role_caps.intersection(set(rule["capabilities"])))

    def doctor_expected_role_tests(self) -> list[dict]:
        return [
            {
                "role": "Billing Support",
                "allow": ["billing", "workspace"],
                "deny": ["core", "support", "technical_support", "security", "audit", "incident", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Technical Support",
                "allow": ["technical_support", "status_safe", "workspace"],
                "deny": ["core", "billing", "support", "security", "audit", "incident", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Security Support",
                "allow": ["security", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "audit", "incident", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Support Agent",
                "allow": ["support", "workspace"],
                "deny": ["core", "billing", "technical_support", "security", "audit", "incident", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Support Lead",
                "allow": ["support", "technical_support", "workspace"],
                "deny": ["core", "billing", "security", "audit", "incident", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Release Manager",
                "allow": ["release", "status_safe", "production", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "backend", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Designer",
                "allow": ["design", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "status_safe", "backend", "production", "release", "qa", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "QA Tester",
                "allow": ["qa", "status_safe", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "backend", "production", "release", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Developer",
                "allow": ["status_safe", "backend", "discord_systems", "roblox_systems", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "incident", "production", "release", "qa", "design", "automation"],
            },
            {
                "role": "Lead Developer",
                "allow": ["status_safe", "backend", "production", "incident", "discord_systems", "roblox_systems", "automation", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "release", "qa", "design"],
            },
            {
                "role": "Infrastructure Admin",
                "allow": ["status_safe", "production", "incident", "discord_systems", "roblox_systems", "automation", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "audit", "backend", "release", "qa", "design"],
            },
            {
                "role": "Security Admin",
                "allow": ["security", "audit", "incident", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Audit Reviewer",
                "allow": ["audit", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "security", "incident", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Incident Response",
                "allow": ["security", "incident", "workspace"],
                "deny": ["core", "billing", "support", "technical_support", "audit", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Director",
                "allow": ["billing", "audit", "incident"],
                "deny": ["core", "support", "technical_support", "security", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
            {
                "role": "Executive",
                "allow": ["billing", "audit", "incident"],
                "deny": ["core", "support", "technical_support", "security", "status_safe", "backend", "production", "release", "qa", "design", "discord_systems", "roblox_systems", "automation"],
            },
        ]

    def doctor_simulate_role_tests(self, guild: discord.Guild, caps: dict[str, list[int]]) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0
        rules = self.doctor_command_rules()

        for test in self.doctor_expected_role_tests():
            role = self.doctor_find_role(guild, test["role"])

            if not role:
                lines.append(f"⚠️ `{test['role']}` role not found.")
                warnings += 1
                continue

            role_caps = sorted(self.doctor_role_caps(role, caps))
            role_lines = [
                f"**{role.mention}**",
                f"Capabilities: `{', '.join(role_caps) if role_caps else 'none'}`",
            ]

            for allow_key in test["allow"]:
                allowed = self.doctor_can_use_rule(role, caps, allow_key)
                label = rules[allow_key]["label"]

                if allowed:
                    role_lines.append(f"✅ allow `{allow_key}` — {label}")
                else:
                    role_lines.append(f"❌ FAIL should allow `{allow_key}` — {label}")
                    failures += 1

            for deny_key in test["deny"]:
                allowed = self.doctor_can_use_rule(role, caps, deny_key)
                label = rules[deny_key]["label"]

                if allowed:
                    role_lines.append(f"❌ FAIL should deny `{deny_key}` — {label}")
                    failures += 1
                else:
                    role_lines.append(f"✅ deny `{deny_key}` — {label}")

            lines.append("\n".join(role_lines))

        return lines, failures, warnings

    def doctor_unsafe_capability_mappings(self, guild: discord.Guild, caps: dict[str, list[int]]) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        for capability, role_ids in sorted(caps.items()):
            for rid in role_ids or []:
                role = guild.get_role(int(rid))

                if not role:
                    lines.append(f"⚠️ `{capability}` has missing role id `{rid}`.")
                    warnings += 1
                    continue

                if role == guild.default_role:
                    lines.append(f"❌ `{capability}` is mapped to @everyone.")
                    failures += 1

                if role.managed:
                    lines.append(f"❌ `{capability}` is mapped to managed role {role.mention}.")
                    failures += 1

                if self.is_separator_role(role):
                    lines.append(f"❌ `{capability}` is mapped to separator/header role {role.mention}.")
                    failures += 1

                if role.name.lower().strip() in ["bots", "bot"]:
                    lines.append(f"❌ `{capability}` is mapped to bot role {role.mention}.")
                    failures += 1

        if not lines:
            lines.append("✅ No unsafe capability role mappings found.")

        return lines, failures, warnings

    def doctor_source_gate_audit(self) -> tuple[list[str], int, int]:
        from pathlib import Path

        lines = []
        failures = 0
        warnings = 0

        bad_gates = [
            "require_staff(ctx)",
            "require_development(ctx)",
            "require_security(ctx)",
            "require_support(ctx)",
        ]

        try:
            root = Path(__file__).resolve().parents[1]

            for path in sorted(root.glob("mattis_*/*.py")):
                if path.name == "shared_mattis.py":
                    continue

                if path.parent.name == "mattis_core":
                    continue

                text = path.read_text(errors="ignore")
                found = []

                for line_no, line in enumerate(text.splitlines(), start=1):
                    for gate in bad_gates:
                        if gate in line:
                            found.append(f"L{line_no}: `{line.strip()}`")

                if found:
                    failures += len(found)
                    lines.append(f"❌ **{path.parent.name}/{path.name}** still has broad gates:\n" + "\n".join(found[:20]))

        except Exception as exc:
            warnings += 1
            lines.append(f"⚠️ Could not scan command source gates: `{type(exc).__name__}: {exc}`")

        if not lines:
            lines.append("✅ No broad command gates found in non-core cogs.")

        return lines, failures, warnings

    def doctor_critical_route_keys(self) -> dict[str, list[str]]:
        return {
            "billing": [
                "billing_support_billing_help",
                "billing_support_invoices",
                "billing_support_payments",
                "billing_support_refunds",
                "billing_support_chargebacks",
            ],
            "security": [
                "observatory_logs_security_log",
                "security_support_security_help",
                "security_support_account_compromise",
                "security_support_report_exploit",
                "security_support_suspicious_activity",
            ],
            "support": [
                "support",
                "support_support_center",
                "support_center",
                "ticket_support",
                "observatory_logs_ticket_log",
            ],
            "development": [
                "development_backend",
                "development_deployments",
                "development_bot_systems",
                "development_dev_chat",
            ],
            "management": [
                "management_board_room",
                "management_finance",
                "management_analytics",
            ],
            "logs": [
                "observatory_logs_api_log",
                "observatory_logs_audit_log",
                "observatory_logs_bot_log",
                "observatory_logs_incident_log",
                "observatory_logs_member_log",
                "observatory_logs_message_log",
                "observatory_logs_system_log",
                "observatory_logs_delete_log",
                "observatory_logs_edit_log",
            ],
        }

    def doctor_route_value_id(self, value):
        if isinstance(value, dict):
            return value.get("channel_id") or value.get("id")

        return value

    def doctor_channel_perm_status(self, channel: discord.abc.GuildChannel) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        me = channel.guild.me
        perms = channel.permissions_for(me)

        required = [
            ("view_channel", "View Channel"),
            ("send_messages", "Send Messages"),
            ("embed_links", "Embed Links"),
            ("read_message_history", "Read Message History"),
        ]

        for attr, label in required:
            if getattr(perms, attr, False):
                lines.append(f"✅ {label}")
            else:
                lines.append(f"❌ Missing {label}")
                failures += 1

        if hasattr(perms, "attach_files") and not perms.attach_files:
            lines.append("⚠️ Missing Attach Files")
            warnings += 1

        return lines, failures, warnings


    async def doctor_get_routes_map(self, guild: discord.Guild) -> dict:
        import inspect

        # First try live helper methods already on MattisCore.
        for method_name in [
            "saved_routes",
            "get_saved_routes",
            "load_routes",
            "get_route_map",
            "saved_channel_routes",
            "channel_routes",
        ]:
            method = getattr(self, method_name, None)

            if not method or not callable(method):
                continue

            try:
                sig = inspect.signature(method)
                params = list(sig.parameters)

                if len(params) == 0:
                    value = await method()
                else:
                    value = await method(guild)

                if isinstance(value, dict) and value:
                    return value
            except Exception:
                pass

        cfg = await get_core_config(self.bot)

        # Then try known config names.
        names = [
            "routes",
            "channel_routes",
            "route_map",
            "routing",
            "saved_routes",
            "channel_map",
            "route_settings",
            "routes_map",
            "route_data",
        ]

        for name in names:
            try:
                value = await getattr(cfg.guild(guild), name)()
                if isinstance(value, dict) and value:
                    return value
            except Exception:
                pass

        try:
            all_data = await cfg.guild(guild).all()
        except Exception:
            all_data = {}

        for name in names:
            value = all_data.get(name)
            if isinstance(value, dict) and value:
                return value

        def value_channel_id(value):
            if isinstance(value, dict):
                return value.get("channel_id") or value.get("id")
            return value

        best = {}
        best_score = 0

        def score_dict(obj: dict) -> int:
            score = 0

            for key, value in obj.items():
                key_text = str(key).lower()
                channel_id = value_channel_id(value)

                try:
                    if guild.get_channel(int(channel_id)):
                        score += 5
                except Exception:
                    pass

                if any(word in key_text for word in [
                    "billing",
                    "support",
                    "security",
                    "development",
                    "management",
                    "observatory",
                    "logs",
                    "route",
                    "channel",
                    "ticket",
                    "incident",
                    "audit",
                    "message",
                    "member",
                ]):
                    score += 2

            return score

        def scan(obj):
            nonlocal best, best_score

            if not isinstance(obj, dict):
                return

            score = score_dict(obj)

            if score > best_score and len(obj) >= 3:
                best = obj
                best_score = score

            for value in obj.values():
                if isinstance(value, dict):
                    scan(value)

        scan(all_data)

        return best or {}

    async def doctor_route_audit(self, guild: discord.Guild) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        try:
            routes = await self.doctor_get_routes_map(guild)
            routes = routes or {}

            lines.append(f"Saved routes: `{len(routes)}`")

            missing_channels = []

            for key, value in sorted(routes.items()):
                channel_id = self.doctor_route_value_id(value)

                try:
                    channel = guild.get_channel(int(channel_id))
                except Exception:
                    channel = None

                if not channel:
                    missing_channels.append(f"`{key}` → missing `{channel_id}`")

            if missing_channels:
                failures += len(missing_channels)
                lines.append("❌ Missing saved route channels:\n" + "\n".join(missing_channels[:30]))
            else:
                lines.append("✅ Missing saved route channels: `0`")

            if "support" in routes:
                lines.append("ℹ️ `support` fallback route is present.")
            else:
                warnings += 1
                lines.append("⚠️ `support` fallback route is not present.")

            lines.append("")
            lines.append("**Critical route coverage**")

            critical = self.doctor_critical_route_keys()

            for group, keys in critical.items():
                present = []
                missing = []

                for key in keys:
                    if key in routes:
                        present.append(key)
                    else:
                        missing.append(key)

                if present:
                    lines.append(f"✅ `{group}` has `{len(present)}` critical routes.")
                else:
                    failures += 1
                    lines.append(f"❌ `{group}` has no critical routes present.")

                if missing:
                    warnings += len(missing)
                    lines.append(f"⚠️ `{group}` missing optional/expected keys: `{', '.join(missing[:8])}`")

            lines.append("")
            lines.append("**Critical channel permissions**")

            checked_ids = set()

            for group, keys in critical.items():
                for key in keys:
                    if key not in routes:
                        continue

                    channel_id = self.doctor_route_value_id(routes[key])

                    try:
                        channel_id_int = int(channel_id)
                    except Exception:
                        failures += 1
                        lines.append(f"❌ `{key}` has invalid channel id `{channel_id}`")
                        continue

                    if channel_id_int in checked_ids:
                        continue

                    checked_ids.add(channel_id_int)
                    channel = guild.get_channel(channel_id_int)

                    if not channel:
                        failures += 1
                        lines.append(f"❌ `{key}` route channel missing.")
                        continue

                    perm_lines, f, w = self.doctor_channel_perm_status(channel)
                    failures += f
                    warnings += w

                    if f:
                        lines.append(f"❌ {channel.mention} permission problem:\n" + "\n".join(perm_lines))
                    else:
                        lines.append(f"✅ {channel.mention} permissions OK")

        except Exception as exc:
            warnings += 1
            lines.append(f"⚠️ Could not audit routes: `{type(exc).__name__}: {exc}`")

        return lines, failures, warnings

    async def doctor_engine_settings(self, guild: discord.Guild) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        try:
            cfg = await get_core_config(self.bot)

            checks = [
                ("log_settings", "Logs", True),
                ("alert_settings", "Alerts", True),
                ("notify_settings", "Notify", True),
                ("eventlog_settings", "Event logs", True),
            ]

            for attr, label, should_be_enabled in checks:
                try:
                    data = await getattr(cfg.guild(guild), attr)()
                    data = data or {}

                    enabled = data.get("enabled")

                    if enabled is None:
                        enabled = data.get("global_enabled")

                    if enabled is True:
                        lines.append(f"✅ {label}: enabled")
                    elif enabled is False:
                        if should_be_enabled:
                            warnings += 1
                            lines.append(f"⚠️ {label}: disabled")
                        else:
                            lines.append(f"✅ {label}: disabled as expected")
                    else:
                        warnings += 1
                        lines.append(f"⚠️ {label}: enabled state unclear")

                    if attr == "notify_settings":
                        alert_mentions = data.get("alert_mentions")
                        log_mentions = data.get("log_mentions")
                        manual_mentions = data.get("manual_mentions")
                        dispatch_mentions = data.get("dispatch_mentions")

                        if alert_mentions is True:
                            lines.append("✅ Notify alert mentions: on")
                        else:
                            warnings += 1
                            lines.append("⚠️ Notify alert mentions: not on")

                        if log_mentions is False:
                            lines.append("✅ Notify log mentions: off")
                        else:
                            warnings += 1
                            lines.append("⚠️ Notify log mentions should be off to avoid spam")

                        if manual_mentions:
                            warnings += 1
                            lines.append("⚠️ Manual post mentions are on")
                        else:
                            lines.append("✅ Manual post mentions: off")

                        if dispatch_mentions:
                            warnings += 1
                            lines.append("⚠️ Dispatch mentions are on")
                        else:
                            lines.append("✅ Dispatch mentions: off")

                except Exception as exc:
                    warnings += 1
                    lines.append(f"⚠️ {label}: could not read settings `{type(exc).__name__}`")

        except Exception as exc:
            warnings += 1
            lines.append(f"⚠️ Could not audit engine settings: `{type(exc).__name__}: {exc}`")

        return lines, failures, warnings



    async def doctor_get_api_config_value(self, guild: discord.Guild, names: list[str]):
        cfg = await get_core_config(self.bot)

        # Doctor-specific settings first. This is where setapi/settoken saves values.
        try:
            doctor_settings = await cfg.guild(guild).doctor_settings()
            doctor_settings = doctor_settings or {}

            for name in names:
                value = doctor_settings.get(name)
                if value:
                    return value
        except Exception:
            pass

        # Then try direct registered config keys, if any exist.
        for name in names:
            try:
                value = await getattr(cfg.guild(guild), name)()
                if value:
                    return value
            except Exception:
                pass

        # Then scan all guild config.
        try:
            full = await cfg.guild(guild).all()
        except Exception:
            full = {}

        wanted = {str(x).lower() for x in names}

        def scan(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    key_l = str(key).lower()

                    if key_l in wanted and value:
                        return value

                    found = scan(value)
                    if found:
                        return found

            if isinstance(obj, list):
                for item in obj:
                    found = scan(item)
                    if found:
                        return found

            return None

        return scan(full)


    async def doctor_api_audit(self, guild: discord.Guild) -> tuple[list[str], int, int]:
        lines = []
        failures = 0
        warnings = 0

        try:
            import aiohttp

            api_url = await self.doctor_get_api_config_value(guild, [
                "api_url",
                "api_base_url",
                "base_api_url",
                "base_url",
                "mattis_api_url",
                "backend_url",
                "backend_base_url",
                "api_endpoint",
                "endpoint",
            ])

            api_token = await self.doctor_get_api_config_value(guild, [
                "api_token",
                "doctor_api_token",
                "bot_api_token",
                "bot_token",
                "mattis_token",
                "mattis_api_token",
                "token",
                "api_key",
                "secret",
            ])

            if not api_url:
                api_url = "https://api.mattisproductions.com"
                warnings += 1
                lines.append("⚠️ API URL not saved. Using default `https://api.mattisproductions.com`.")

            api_url = str(api_url).rstrip("/")
            lines.append(f"✅ API URL: `{api_url}`")

            headers = {}

            if api_token:
                token = str(api_token).strip()

                if token.lower().startswith("bearer "):
                    headers["Authorization"] = token
                else:
                    headers["Authorization"] = f"Bearer {token}"

                lines.append("✅ API token: configured")
            else:
                warnings += 1
                lines.append("⚠️ API token: not configured. Protected bot endpoints may return 401.")

            protected_endpoints = [
                "/bot/status",
                "/bot/command/overview",
            ]

            public_fallback_endpoints = [
                "/health",
                "/status",
                "/api/health",
                "/",
            ]

            timeout = aiohttp.ClientTimeout(total=10)
            protected_passed = False
            protected_auth_required = []
            protected_failed = []

            async with aiohttp.ClientSession(timeout=timeout) as session:
                for endpoint in protected_endpoints:
                    url = api_url + endpoint

                    try:
                        async with session.get(url, headers=headers) as resp:
                            text = await resp.text()
                            sample = text[:140].replace("`", "'").replace("\n", " ")

                            if 200 <= resp.status < 300:
                                protected_passed = True
                                lines.append(f"✅ Protected API endpoint passed: `{endpoint}` returned `{resp.status}`")
                            elif resp.status in [401, 403]:
                                protected_auth_required.append(endpoint)
                                lines.append(f"❌ Protected API endpoint `{endpoint}` returned `{resp.status}` auth-required.")
                            else:
                                protected_failed.append(endpoint)
                                lines.append(f"⚠️ Protected API endpoint `{endpoint}` returned `{resp.status}`: `{sample}`")

                    except Exception as exc:
                        protected_failed.append(endpoint)
                        lines.append(f"⚠️ Protected API endpoint `{endpoint}` failed: `{type(exc).__name__}`")

                # If protected endpoints failed, still prove API reachability with public fallbacks.
                public_passed = False

                if not protected_passed:
                    for endpoint in public_fallback_endpoints:
                        url = api_url + endpoint

                        try:
                            async with session.get(url) as resp:
                                text = await resp.text()
                                sample = text[:140].replace("`", "'").replace("\n", " ")

                                if 200 <= resp.status < 300:
                                    public_passed = True
                                    lines.append(f"✅ Public API reachability passed: `{endpoint}` returned `{resp.status}`")
                                    break

                                if resp.status < 500:
                                    lines.append(f"ℹ️ Public endpoint `{endpoint}` returned `{resp.status}`: `{sample}`")
                                else:
                                    lines.append(f"⚠️ Public endpoint `{endpoint}` returned `{resp.status}`: `{sample}`")

                        except Exception as exc:
                            lines.append(f"ℹ️ Public endpoint `{endpoint}` failed: `{type(exc).__name__}`")

                if protected_passed:
                    lines.append("✅ API auth readiness: protected bot endpoint access working.")
                else:
                    failures += 1

                    if protected_auth_required:
                        lines.append("❌ API auth readiness failed: protected endpoints require a valid token.")
                    elif protected_failed:
                        lines.append("❌ API auth readiness failed: protected endpoints did not return success.")
                    else:
                        lines.append("❌ API auth readiness failed: no protected endpoint passed.")

                    if public_passed:
                        lines.append("ℹ️ API is reachable, but protected bot auth is not passing.")

        except Exception as exc:
            failures += 1
            lines.append(f"❌ API audit failed: `{type(exc).__name__}: {exc}`")

        return lines, failures, warnings

    @mcore.group(name="doctor", invoke_without_command=True)
    async def doctor(self, ctx):
        """Full Mattis Systems live-readiness doctor."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)

        sections = []
        failures = 0
        warnings = 0

        async def add_section(title, result):
            nonlocal failures, warnings
            lines, f, w = result
            failures += f
            warnings += w
            sections.append(f"**{title}**\n" + "\n".join(lines))

        await add_section("1. API live health", await self.doctor_api_audit(ctx.guild))
        await add_section("2. Routes + channel permissions", await self.doctor_route_audit(ctx.guild))
        await add_section("3. Engine settings", await self.doctor_engine_settings(ctx.guild))
        await add_section("4. Unsafe capability mappings", self.doctor_unsafe_capability_mappings(ctx.guild, caps))
        await add_section("5. Source command-gate audit", self.doctor_source_gate_audit())
        await add_section("6. Simulated role permission tests", self.doctor_simulate_role_tests(ctx.guild, caps))

        summary = [
            f"Failures: `{failures}`",
            f"Warnings: `{warnings}`",
            "",
        ]

        if failures:
            title = f"Mattis Doctor: FAIL — {failures} failures, {warnings} warnings"
            color = discord.Color.red()
        elif warnings:
            title = f"Mattis Doctor: WARN — {warnings} warnings"
            color = discord.Color.orange()
        else:
            title = "Mattis Doctor: PASS — Live Ready"
            color = discord.Color.green()

        await self.send_paginated(ctx, title, summary + sections, color=color)



    async def doctor_setapi(self, ctx, api_url: str):
        """Set the API URL used by doctor readiness checks."""
        if not await require_admin(ctx):
            return

        if not api_url.startswith("http://") and not api_url.startswith("https://"):
            await ctx.send(embed=error_embed("Invalid API URL", "Use a full URL starting with http:// or https://"))
            return

        cfg = await get_core_config(self.bot)
        data = await cfg.guild(ctx.guild).doctor_settings()
        data = data or {}
        data["api_url"] = api_url.rstrip("/")
        await cfg.guild(ctx.guild).doctor_settings.set(data)

        await ctx.send(embed=ok_embed("Doctor API URL saved", f"Doctor will test `{data['api_url']}`."))


    async def doctor_clearapi(self, ctx):
        """Clear the doctor API URL override."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        data = await cfg.guild(ctx.guild).doctor_settings()
        data = data or {}
        data.pop("api_url", None)
        await cfg.guild(ctx.guild).doctor_settings.set(data)

        await ctx.send(embed=ok_embed("Doctor API URL cleared", "Doctor will use detected/default API URL."))


    @doctor.command(name="setapi")
    async def doctor_setapi(self, ctx, api_url: str):
        """Set the API URL used by doctor readiness checks."""
        if not await require_admin(ctx):
            return

        if not api_url.startswith("http://") and not api_url.startswith("https://"):
            await ctx.send(embed=error_embed("Invalid API URL", "Use a full URL starting with `http://` or `https://`."))
            return

        cfg = await get_core_config(self.bot)
        data = await cfg.guild(ctx.guild).doctor_settings()
        data = data or {}
        data["api_url"] = api_url.rstrip("/")
        await cfg.guild(ctx.guild).doctor_settings.set(data)

        await ctx.send(embed=ok_embed("Doctor API URL saved", f"Doctor will test `{data['api_url']}`."))

    @doctor.command(name="clearapi")
    async def doctor_clearapi(self, ctx):
        """Clear the doctor API URL override."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        data = await cfg.guild(ctx.guild).doctor_settings()
        data = data or {}
        data.pop("api_url", None)
        await cfg.guild(ctx.guild).doctor_settings.set(data)

        await ctx.send(embed=ok_embed("Doctor API URL cleared", "Doctor will use detected/default API URL."))

    @doctor.command(name="settoken")
    async def doctor_settoken(self, ctx, *, token: str):
        """Set the protected API token used by doctor readiness checks."""
        if not await require_admin(ctx):
            return

        token = str(token or "").strip()

        if not token:
            await ctx.send(embed=error_embed("Missing token", "Usage: `!mcore doctor settoken <token>`"))
            return

        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        cfg = await get_core_config(self.bot)
        data = await cfg.guild(ctx.guild).doctor_settings()
        data = data or {}
        data["api_token"] = token
        await cfg.guild(ctx.guild).doctor_settings.set(data)

        deleted = False

        try:
            await ctx.message.delete()
            deleted = True
        except Exception:
            deleted = False

        msg = "Token saved. I also deleted the command message containing it." if deleted else "Token saved. I could not delete the command message, so delete it manually."

        try:
            await ctx.author.send("Mattis doctor API token saved. I will never display it back.")
        except Exception:
            pass

        await ctx.send(embed=ok_embed("Doctor API token saved", msg))

    @doctor.command(name="cleartoken")
    async def doctor_cleartoken(self, ctx):
        """Clear the protected API token used by doctor readiness checks."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        data = await cfg.guild(ctx.guild).doctor_settings()
        data = data or {}
        data.pop("api_token", None)
        await cfg.guild(ctx.guild).doctor_settings.set(data)

        await ctx.send(embed=ok_embed("Doctor API token cleared", "Protected API doctor checks will no longer send a token."))

    @doctor.command(name="auth")
    async def doctor_auth(self, ctx):
        """Show whether doctor API auth is configured without revealing secrets."""
        if not await require_admin(ctx):
            return

        api_url = await self.doctor_get_api_config_value(ctx.guild, ["api_url"])
        api_token = await self.doctor_get_api_config_value(ctx.guild, ["api_token", "doctor_api_token", "bot_api_token"])

        lines = [
            f"API URL configured: `{'yes' if api_url else 'no'}`",
            f"API token configured: `{'yes' if api_token else 'no'}`",
            "",
            "**Commands:**",
            "`!mcore doctor setapi https://api.mattisproductions.com`",
            "`!mcore doctor settoken <token>`",
            "`!mcore doctor cleartoken`",
            "`!mcore doctor api`",
        ]

        await self.send_paginated(ctx, "Doctor API Auth", lines)

    @doctor.command(name="api")
    async def doctor_api(self, ctx):
        """Run only the API live health audit."""
        if not await require_admin(ctx):
            return

        lines, failures, warnings = await self.doctor_api_audit(ctx.guild)
        color = discord.Color.red() if failures else discord.Color.orange() if warnings else discord.Color.green()
        title = f"Doctor API Audit: {'FAIL' if failures else 'WARN' if warnings else 'PASS'}"
        await self.send_paginated(ctx, title, lines, color=color)

    @doctor.command(name="routes")
    async def doctor_routes(self, ctx):
        """Run only route/channel permission checks."""
        if not await require_admin(ctx):
            return

        lines, failures, warnings = await self.doctor_route_audit(ctx.guild)
        color = discord.Color.red() if failures else discord.Color.orange() if warnings else discord.Color.green()
        title = f"Doctor Route Audit: {'FAIL' if failures else 'WARN' if warnings else 'PASS'}"
        await self.send_paginated(ctx, title, lines, color=color)

    @doctor.command(name="settings")
    async def doctor_settings(self, ctx):
        """Run only engine setting checks."""
        if not await require_admin(ctx):
            return

        lines, failures, warnings = await self.doctor_engine_settings(ctx.guild)
        color = discord.Color.red() if failures else discord.Color.orange() if warnings else discord.Color.green()
        title = f"Doctor Engine Settings: {'FAIL' if failures else 'WARN' if warnings else 'PASS'}"
        await self.send_paginated(ctx, title, lines, color=color)

    @doctor.command(name="roles")
    async def doctor_roles(self, ctx):
        """Run only simulated role permission tests."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)
        lines, failures, warnings = self.doctor_simulate_role_tests(ctx.guild, caps)
        color = discord.Color.red() if failures else discord.Color.orange() if warnings else discord.Color.green()
        title = f"Doctor Role Tests: {'FAIL' if failures else 'WARN' if warnings else 'PASS'}"
        await self.send_paginated(ctx, title, lines, color=color)

    @doctor.command(name="gates")
    async def doctor_gates(self, ctx):
        """Run only source command-gate audit."""
        if not await require_admin(ctx):
            return

        lines, failures, warnings = self.doctor_source_gate_audit()
        color = discord.Color.red() if failures else discord.Color.orange() if warnings else discord.Color.green()
        title = f"Doctor Gate Audit: {'FAIL' if failures else 'WARN' if warnings else 'PASS'}"
        await self.send_paginated(ctx, title, lines, color=color)

    @doctor.command(name="capabilities")
    async def doctor_capabilities(self, ctx):
        """Run only unsafe capability mapping checks."""
        if not await require_admin(ctx):
            return

        caps = await self.saved_capabilities(ctx.guild)
        lines, failures, warnings = self.doctor_unsafe_capability_mappings(ctx.guild, caps)
        color = discord.Color.red() if failures else discord.Color.orange() if warnings else discord.Color.green()
        title = f"Doctor Capability Audit: {'FAIL' if failures else 'WARN' if warnings else 'PASS'}"
        await self.send_paginated(ctx, title, lines, color=color)


    def alert_lifecycle_now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def alert_lifecycle_norm(self, value) -> str:
        import re

        if value is None:
            return ""

        value = str(value).lower().strip()
        value = re.sub(r"[^a-z0-9]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        return value

    def alert_lifecycle_extract_count(self, item) -> int:
        if isinstance(item, int):
            return item

        if isinstance(item, float):
            return int(item)

        if isinstance(item, dict):
            for key in [
                "count",
                "total",
                "active",
                "open",
                "open_count",
                "critical",
                "critical_count",
                "failed",
                "failed_count",
                "risk_count",
                "incident_count",
            ]:
                value = item.get(key)

                if isinstance(value, int):
                    return value

                if isinstance(value, str) and value.isdigit():
                    return int(value)

        return 1

    def alert_lifecycle_extract_status(self, item, count: int) -> str:
        if isinstance(item, dict):
            for key in ["status", "state", "incident_status", "alert_status", "resolution_status"]:
                value = item.get(key)

                if value:
                    status = self.alert_lifecycle_norm(value)

                    if status in ["resolved", "closed", "complete", "completed", "fixed", "cleared"]:
                        return "resolved"

                    if status in ["reopened", "open_again"]:
                        return "reopened"

                    if status in ["open", "active", "ongoing", "investigating", "degraded", "critical", "failing"]:
                        return "ongoing"

        return "ongoing" if count > 0 else "resolved"


    def alert_lifecycle_extract_severity(self, *args, **kwargs):
        """Extract useful severity instead of unknown."""
        values = list(args) + list(kwargs.values())
        raw = " ".join(str(v or "") for v in values).lower()

        try:
            count = self.b3a_extract_count(raw, {})
        except Exception:
            count = 1

        if any(x in raw for x in ["audit_highrisk", "high_risk_audit_events", "bot_audit_highrisk", "/bot/audit/highrisk", "highrisk"]):
            return "high"
        if any(x in raw for x in ["security", "suspicious", "exploit", "compromise"]):
            return "high"
        if any(x in raw for x in ["billing_failed", "failed invoice", "chargeback"]):
            return "high" if count >= 5 else "medium"
        if count >= 20:
            return "high"
        if count >= 5:
            return "medium"

        return "low"

    def alert_lifecycle_extract_identity(self, *args, **kwargs):
        """Canonical lifecycle identity. Prevents same alert being reposted with new hash IDs."""
        values = list(args) + list(kwargs.values())
        return self.b3a_canonical_alert_key(*values)


    def alert_lifecycle_fingerprint(self, *args, **kwargs):
        """Stable lifecycle fingerprint. Ignores volatile hash/timestamp noise."""
        values = list(args) + list(kwargs.values())
        return self.b3a_stable_alert_fingerprint(*values)

    async def alert_lifecycle_get(self) -> dict:
        cfg = await get_core_config(self.bot)

        try:
            data = await cfg.alert_lifecycle()
        except Exception:
            try:
                data = await cfg.guild_from_id(0).alert_lifecycle()
            except Exception:
                data = {}

        return data or {}

    async def alert_lifecycle_get_guild(self, guild: discord.Guild) -> dict:
        cfg = await get_core_config(self.bot)

        try:
            data = await cfg.guild(guild).alert_lifecycle()
        except Exception:
            data = {}

        return data or {}

    async def alert_lifecycle_set_guild(self, guild: discord.Guild, data: dict):
        cfg = await get_core_config(self.bot)
        await cfg.guild(guild).alert_lifecycle.set(data or {})

    async def alert_lifecycle_settings(self, guild: discord.Guild) -> dict:
        data = await self.alert_lifecycle_get_guild(guild)

        settings = data.get("_settings") or {}
        settings.setdefault("enabled", True)
        settings.setdefault("post_resolved", True)
        settings.setdefault("post_updates", True)
        settings.setdefault("edit_original", True)
        settings.setdefault("stale_resolve_minutes", 0)

        return settings

    async def alert_lifecycle_state(self, guild: discord.Guild) -> dict:
        data = await self.alert_lifecycle_get_guild(guild)
        return data.get("state") or {}

    async def alert_lifecycle_save_state(self, guild: discord.Guild, state: dict):
        data = await self.alert_lifecycle_get_guild(guild)
        data["state"] = state or {}
        await self.alert_lifecycle_set_guild(guild, data)

    async def alert_lifecycle_decide(self, guild: discord.Guild, rule_name: str, item) -> tuple[str, dict, dict]:
        settings = await self.alert_lifecycle_settings(guild)

        if not settings.get("enabled", True):
            return "post", {}, {}

        state = await self.alert_lifecycle_state(guild)

        rule_name = self.alert_lifecycle_norm(rule_name) or "unknown_rule"
        count = self.alert_lifecycle_extract_count(item)
        status = self.alert_lifecycle_extract_status(item, count)
        severity = self.alert_lifecycle_extract_severity(item)
        identity = self.alert_lifecycle_extract_identity(rule_name, item)
        fingerprint = self.alert_lifecycle_fingerprint(rule_name, item, count, status, severity)
        now = self.alert_lifecycle_now()

        previous = state.get(identity) or {}
        previous_status = previous.get("status")
        previous_fingerprint = previous.get("fingerprint")
        previous_count = previous.get("count")
        previous_severity = previous.get("severity")

        record = dict(previous)
        record.update({
            "id": identity,
            "rule": rule_name,
            "status": status,
            "severity": severity,
            "count": count,
            "fingerprint": fingerprint,
            "last_seen": now,
        })

        if not record.get("first_seen"):
            record["first_seen"] = now

        action = "skip"

        if not previous:
            action = "new" if status != "resolved" else "skip"
        elif previous_status == "resolved" and status != "resolved":
            action = "reopened"
        elif status == "resolved" and previous_status != "resolved":
            action = "resolved" if settings.get("post_resolved", True) else "skip"
            record["resolved_at"] = now
        elif fingerprint != previous_fingerprint or count != previous_count or severity != previous_severity:
            action = "updated" if settings.get("post_updates", True) else "skip"
        else:
            action = "skip"

        if action in ["new", "reopened", "updated", "resolved", "post"]:
            record["last_posted"] = now
            record["last_action"] = action

        state[identity] = record
        await self.alert_lifecycle_save_state(guild, state)

        return action, record, previous

    async def alert_lifecycle_mark_message(self, guild: discord.Guild, record_id: str, channel_id: int | None = None, message_id: int | None = None):
        state = await self.alert_lifecycle_state(guild)

        if record_id not in state:
            return

        if channel_id:
            state[record_id]["channel_id"] = int(channel_id)

        if message_id:
            state[record_id]["message_id"] = int(message_id)

        await self.alert_lifecycle_save_state(guild, state)

    def alert_lifecycle_status_label(self, action: str, record: dict) -> str:
        status = record.get("status", "ongoing")
        severity = record.get("severity", "unknown")
        count = record.get("count", 1)

        if action == "new":
            prefix = "🚨 NEW"
        elif action == "updated":
            prefix = "🔄 UPDATED"
        elif action == "resolved":
            prefix = "✅ RESOLVED"
        elif action == "reopened":
            prefix = "♻️ REOPENED"
        else:
            prefix = "🚨 ALERT"

        return f"{prefix} · Status: `{status}` · Severity: `{severity}` · Count: `{count}`"






    def b3a_canonical_alert_key(self, *values) -> str:
        """Return a stable alert ID. Same rule/endpoint = same alert, no random hash."""
        import re
        import hashlib

        raw = " ".join(str(v or "") for v in values)
        low = raw.lower()

        mapping = [
            (["audit_highrisk", "high_risk_audit_events", "bot_audit_highrisk", "/bot/audit/highrisk", "observatory_logs_audit_log"], "alert:audit_highrisk"),
            (["support_critical", "/bot/support/critical"], "alert:support_critical"),
            (["support_unassigned", "/bot/support/unassigned"], "alert:support_unassigned"),
            (["billing_failed", "/bot/billing/failed", "failed_invoices"], "alert:billing_failed"),
            (["billing_pastdue", "billing_past_due", "/bot/billing/pastdue", "past_due"], "alert:billing_pastdue"),
            (["security_risks", "/bot/security/risks"], "alert:security_risks"),
            (["security_suspicious", "/bot/security/suspicious"], "alert:security_suspicious"),
            (["automation_failed", "/bot/automation/failed"], "alert:automation_failed"),
            (["discord_broken", "/bot/discord/broken"], "alert:discord_broken"),
            (["roblox_broken", "/bot/roblox/broken"], "alert:roblox_broken"),
            (["incidents", "/bot/incidents"], "alert:incidents"),
            (["api_down", "/health", "/bot/status"], "alert:api_health"),
        ]

        for needles, key in mapping:
            if any(n in low for n in needles):
                return key

        # Fallback: remove volatile bits before hashing.
        stable = low
        stable = re.sub(r":hash:[a-f0-9]+", "", stable)
        stable = re.sub(r"\bhash[:=\s]+[a-f0-9]{8,}\b", "", stable)
        stable = re.sub(r"\b(first seen|last seen|last posted)[:\s]+[^\n]+", "", stable)
        stable = re.sub(r"\b20\d\d-\d\d-\d\d[t ][0-9:.+\-z]+\b", "<time>", stable)
        stable = re.sub(r"<t:\d+:[a-z]>", "<time>", stable)
        stable = re.sub(r"\bcm[a-z0-9]{12,}\b", "<id>", stable)
        stable = re.sub(r"\s+", " ", stable).strip()

        digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
        return f"alert:auto:{digest}"

    def b3a_stable_alert_fingerprint(self, *values) -> str:
        """Fingerprint only meaningful changes, not timestamps/hash noise."""
        import re
        import hashlib

        raw = " ".join(str(v or "") for v in values)
        low = raw.lower()

        key = self.b3a_canonical_alert_key(raw)

        count = 1
        try:
            count = self.b3a_extract_count(raw, {})
        except Exception:
            count = 1

        severity = "unknown"
        try:
            if hasattr(self, "b3a_alert_profile"):
                severity = self.b3a_alert_profile(raw, count).get("severity", "unknown")
        except Exception:
            severity = "unknown"

        # Keep event/action/reason shape, but remove volatile lifecycle IDs/timestamps.
        stable = low
        stable = re.sub(r":hash:[a-f0-9]+", "", stable)
        stable = re.sub(r"\balert id\s+[^\n]+", "", stable)
        stable = re.sub(r"\b(first seen|last seen|last posted)[:\s]+[^\n]+", "", stable)
        stable = re.sub(r"\b20\d\d-\d\d-\d\d[t ][0-9:.+\-z]+\b", "<time>", stable)
        stable = re.sub(r"<t:\d+:[a-z]>", "<time>", stable)
        stable = re.sub(r"\s+", " ", stable).strip()

        base = f"{key}|count:{count}|severity:{severity}|{stable[:1500]}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def b3a_clean_alert_text(self, value: str) -> str:
        import re

        value = str(value or "")
        value = value.replace("`", "'")
        value = value.replace("<Redacted>", "").replace("<redacted>", "")

        # Keep lifecycle/rule IDs readable. Only redact obvious secret-looking tokens.
        value = re.sub(r"\b(sk_live|sk_test|pk_live|pk_test|xoxb|ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_\-]{10,}\b", "<secret>", value)
        value = re.sub(r"\b[A-Za-z0-9+/]{80,}={0,2}\b", "<secret>", value)
        value = re.sub(r"\s+", " ", value).strip()

        return value


    def b3a_extract_path(self, text: str) -> str:
        import re

        text = str(text or "")

        # Handles: _path_bot_audit_highrisk_purpose_audit_log
        m = re.search(r"_path_([a-zA-Z0-9_]+?)(?:_purpose_|:hash:|$)", text, re.I)
        if m:
            return m.group(1).replace("_", "-")

        for pat in [
            r"route[:=\s]+#?([a-zA-Z0-9_\-]+)",
            r"channel[:=\s]+#?([a-zA-Z0-9_\-]+)",
            r"path[:=\s]+#?([a-zA-Z0-9_\-]+)",
        ]:
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1).replace("_", "-")

        return ""


    def b3a_human_rule_name(self, raw: str) -> str:
        import re

        raw = str(raw or "")
        low = raw.lower()

        known = [
            ("support_critical", "Critical Support Tickets"),
            ("support_unassigned", "Unassigned Support Tickets"),
            ("billing_failed", "Failed Billing / Failed Invoices"),
            ("billing_pastdue", "Past Due Billing"),
            ("audit_highrisk", "High Risk Audit Events"),
            ("high_risk_audit_events", "High Risk Audit Events"),
            ("bot_audit_highrisk", "High Risk Audit Events"),
            ("observatory_logs_audit_log", "High Risk Audit Events"),
            ("security_risks", "Security Risk Events"),
            ("security_suspicious", "Suspicious Security Activity"),
            ("automation_failed", "Automation Failures"),
            ("discord_broken", "Discord Integration Issues"),
            ("roblox_broken", "Roblox Integration Issues"),
            ("incidents", "Active Incidents"),
            ("api_down", "API Availability Issue"),
            ("api_errors", "API Error Spike"),
            ("web_down", "Website Availability Issue"),
            ("postgres", "Postgres Database Issue"),
            ("redis", "Redis Cache / Session Issue"),
        ]

        for key, title in known:
            if key in low:
                return title

        text = raw
        text = re.sub(r"^title_", "", text, flags=re.I)
        text = re.sub(r":hash:[a-f0-9]+$", "", text, flags=re.I)
        text = re.sub(r"_path_.*$", "", text, flags=re.I)
        text = re.sub(r"_purpose_.*$", "", text, flags=re.I)
        text = text.replace("_", " ").replace("-", " ")
        text = re.sub(r"\s+", " ", text).strip()

        if not text:
            return "Mattis CMS Alert"

        return text.title()[:120]



    def b3a_alert_profile(self, raw: str, count: int = 0) -> dict:
        raw = str(raw or "")
        low = raw.lower()

        def profile(
            title,
            area,
            subsystem,
            owner,
            escalation,
            customer_impact,
            internal_impact,
            why,
            action,
            commands,
            severity="low",
            severity_reason="Low severity because no urgent impact was detected.",
        ):
            return {
                "title": title,
                "area": area,
                "subsystem": subsystem,
                "owner": owner,
                "escalation": escalation,
                "customer_impact": customer_impact,
                "internal_impact": internal_impact,
                "why_it_matters": why,
                "recommended_action": action,
                "related_commands": commands,
                "severity": severity,
                "severity_reason": severity_reason,
            }

        if any(x in low for x in [
            "audit_highrisk",
            "high_risk_audit_events",
            "bot_audit_highrisk",
            "highrisk",
            "high risk audit",
            "observatory_logs_audit_log",
        ]):
            severity = "high" if int(count or 0) >= 5 else "medium"
            return profile(
                "High Risk Audit Events",
                "Audit / Security",
                "High-Risk Audit Trail",
                "Audit Reviewer + Security Admin",
                "Audit Reviewer → Security Admin → Incident Response → Founder",
                "No direct customer impact detected from this alert alone. Customer impact becomes possible if the audit entries involve customer accounts, billing, access, or production data.",
                "Sensitive internal staff/system activity requires review. This may include permissions, route changes, admin actions, token/config changes, or unusual internal behaviour.",
                "High-risk audit events matter because they can reveal sensitive operational changes, staff actions, permission changes, or suspicious internal activity before they become a larger incident.",
                "Open the high-risk audit route, review the newest entries first, confirm whether they match expected staff/admin actions, and escalate anything unauthorised or unusual.",
                [
                    "!mcore alerts ops",
                    "!mcore alerts show audit",
                    "!mcore alerts explain audit",
                    "!mcore alerts investigate audit",
                    "!mcore doctor capabilities",
                    "!mcore access matrix",
                ],
                severity,
                f"{severity.title()} severity because `{count}` high-risk audit event(s) are active and require review.",
            )

        if any(x in low for x in [
            "billing_failed",
            "failed invoice",
            "failed_invoices",
            "payment_fail",
            "stripe",
        ]):
            severity = "high" if int(count or 0) >= 5 else "medium"
            return profile(
                "Failed Billing / Failed Invoices",
                "Billing",
                "Payments / Invoices",
                "Billing Support",
                "Billing Support → Director → Founder",
                "Customers may lose access, fail renewal, or need payment support if invoices are failing.",
                "Billing Support may need to review customer accounts, failed payments, invoice status, Stripe/webhook events, and access state.",
                "Failed billing matters because it can affect customer access, revenue collection, subscriptions, and trust.",
                "Review failed invoice/payment records, confirm whether failures are isolated or increasing, and contact or escalate affected customers.",
                ["!mcore alerts ops", "!mcore doctor api"],
                severity,
                f"{severity.title()} severity because failed billing can affect customer access/revenue and count is `{count}`.",
            )

        if any(x in low for x in ["billing_pastdue", "past_due", "pastdue"]):
            return profile(
                "Past Due Billing",
                "Billing",
                "Invoices / Account Status",
                "Billing Support",
                "Billing Support → Director → Founder",
                "Customers may be overdue and at risk of losing access depending on billing rules.",
                "Billing team should review overdue accounts and decide whether reminders/escalation are needed.",
                "Past-due billing matters because unresolved invoices can become access issues, support tickets, or revenue loss.",
                "Review past-due invoices, check customer history, and follow billing support process.",
                ["!mcore alerts ops", "!mcore doctor api"],
                "medium",
                f"Medium severity because `{count}` past-due billing item(s) require review.",
            )

        if "support_critical" in low:
            return profile(
                "Critical Support Tickets",
                "Support",
                "Critical Tickets",
                "Support Lead",
                "Support Agent → Support Lead → Management",
                "Customers may be blocked, unhappy, or waiting for urgent help.",
                "Support Lead should make sure urgent tickets have ownership and escalation.",
                "Critical support tickets matter because they can quickly become customer-impacting incidents.",
                "Open the support route, assign an owner, respond to urgent customers, and escalate unresolved critical issues.",
                ["!mcore alerts ops", "!mcore doctor"],
                "high",
                f"High severity because `{count}` critical support ticket(s) may require urgent action.",
            )

        if "support_unassigned" in low:
            return profile(
                "Unassigned Support Tickets",
                "Support",
                "Ticket Assignment",
                "Support Lead",
                "Support Agent → Support Lead",
                "Customers may be waiting without a clear owner.",
                "Support Lead should assign tickets and monitor response delays.",
                "Unassigned tickets matter because nobody owns the customer response until they are assigned.",
                "Review unassigned tickets, assign owners, and escalate anything urgent.",
                ["!mcore alerts ops", "!mcore doctor"],
                "medium",
                f"Medium severity because `{count}` unassigned support ticket(s) may be waiting for ownership.",
            )

        if any(x in low for x in [
            "security_risks",
            "security_suspicious",
            "suspicious",
            "exploit",
            "compromise",
        ]):
            return profile(
                "Security Risk Events",
                "Security",
                "Security Monitoring",
                "Security Support + Security Admin",
                "Security Support → Security Admin → Incident Response → Founder",
                "Possible customer impact if accounts, sessions, billing, or production access are involved.",
                "Security staff must verify whether this is expected, suspicious, or actively harmful.",
                "Security alerts matter because they can indicate suspicious activity, account risk, abuse, or unauthorised access attempts.",
                "Investigate the source, affected account/session/system, confirm legitimacy, and escalate if suspicious.",
                ["!mcore alerts ops", "!mcore doctor capabilities", "!mcore access matrix"],
                "high",
                f"High severity because security-risk activity needs review. Active count: `{count}`.",
            )

        if "automation_failed" in low:
            return profile(
                "Automation Failures",
                "Automation",
                "Internal Automation Jobs",
                "Infrastructure Admin + Lead Developer",
                "Infrastructure Admin → Lead Developer → Founder",
                "Customer impact depends on which automation failed.",
                "Internal workflows, sync jobs, alerts, or scheduled checks may be failing.",
                "Automation failures matter because background workflows can silently stop doing important work.",
                "Review the failed automation route/logs and confirm which job failed and whether it needs rerunning.",
                ["!mcore alerts ops", "!mcore doctor"],
                "medium",
                f"Medium severity because `{count}` automation failure(s) may require review.",
            )

        if "discord_broken" in low:
            return profile(
                "Discord Integration Issues",
                "Discord Bot",
                "Redbot / Discord Integration",
                "Infrastructure Admin + Lead Developer",
                "Infrastructure Admin → Lead Developer → Founder",
                "Usually internal impact unless customers rely on Discord workflows.",
                "Staff commands, alerts, logs, support flows, or role sync may be affected.",
                "Discord integration issues matter because the bot is used for internal operations and support workflows.",
                "Check Redbot process, loaded cogs, bot permissions, command errors, and recent reloads.",
                ["!mcore doctor", "!mcore doctor routes", "!mcore doctor gates"],
                "medium",
                f"Medium severity because Discord/bot integration health needs review. Count: `{count}`.",
            )

        if "roblox_broken" in low:
            return profile(
                "Roblox Integration Issues",
                "Roblox Integration",
                "Roblox OAuth / API / Sync",
                "Developer + Roblox Systems",
                "Developer → Lead Developer → Founder",
                "Customers using Roblox-linked CMS features may be affected.",
                "Verification, marketplace, sync, or Roblox-related workflows may need review.",
                "Roblox integration issues matter because Mattis CMS is Roblox-focused and depends on Roblox workflows.",
                "Check Roblox API/OAuth/webhook configuration and related API logs.",
                ["!mcore alerts ops", "!mcore doctor api"],
                "medium",
                f"Medium severity because Roblox integration issues can affect customer workflows. Count: `{count}`.",
            )

        if "incidents" in low:
            return profile(
                "Active Incidents",
                "Incident Response",
                "Incident Management",
                "Incident Response + Management",
                "Incident Response → Management → Founder",
                "Customer impact depends on the incident type and affected service.",
                "Incident responders need to confirm ownership, status, impact, and resolution progress.",
                "Incidents matter because they represent active operational problems that need tracking until resolved.",
                "Review active incidents, confirm status/owner, update timeline, and escalate unresolved high-impact incidents.",
                ["!mcore alerts ops", "!mcore doctor"],
                "high",
                f"High severity because active incident tracking indicates `{count}` incident-related item(s).",
            )

        severity = "medium" if int(count or 0) >= 5 else "low"

        return profile(
            self.b3a_human_rule_name(raw),
            "Mattis CMS | Systems",
            "General Operations",
            "Management",
            "Management → Founder",
            "No direct customer impact detected from this alert alone.",
            "Internal review may be required.",
            "This alert may indicate something that needs review inside Mattis CMS | Systems.",
            "Review the routed logs and confirm whether action is required.",
            ["!mcore alerts ops", "!mcore doctor"],
            severity,
            f"{severity.title()} severity based on count `{count}` and no stronger classification match.",
        )

    def b3a_classify_alert(self, raw: str, count: int = 0) -> dict:
        return self.b3a_alert_profile(raw, count)

    def b3a_extract_count(self, raw: str, existing=None) -> int:
        raw = str(raw or "")
        existing = existing or {}

        for key in ["count", "total", "events", "failures"]:
            if existing.get(key) is not None:
                try:
                    return int(existing.get(key))
                except Exception:
                    pass

        patterns = [
            r"\bcount[:\s]+(\d+)\b",
            r"\btotal[:\s]+(\d+)\b",
            r"\b(\d+)\s+events?\b",
            r"\b(\d+)\s+failures?\b",
            r"\b(\d+)\s+alerts?\b",
        ]

        for pat in patterns:
            m = re.search(pat, raw, re.I)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    pass

        return 1

    def b3a_alert_icon(self, severity: str, status: str = "") -> str:
        severity = str(severity or "").lower()
        status = str(status or "").lower()

        if status == "resolved":
            return "✅"
        if status == "updated":
            return "🔁"
        if status == "reopened":
            return "♻️"
        if severity == "critical":
            return "🚨"
        if severity == "high":
            return "⚠️"
        if severity == "medium":
            return "🟡"
        return "ℹ️"

    def b3a_status_label(self, status: str) -> str:
        status = str(status or "ongoing").lower()
        return {
            "new": "New",
            "ongoing": "Ongoing",
            "updated": "Updated",
            "resolved": "Resolved",
            "reopened": "Reopened",
            "acknowledged": "Acknowledged",
        }.get(status, status.title())

    def b3a_change_summary(self, existing: dict, new_meta: dict) -> str:
        existing = existing or {}
        changes = []

        checks = [
            ("count", "Count"),
            ("severity", "Severity"),
            ("status", "Status"),
            ("area", "Affected Area"),
            ("owner", "Owner Team"),
        ]

        for key, label in checks:
            old = existing.get(key)
            new = new_meta.get(key)

            if old is not None and new is not None and str(old) != str(new):
                changes.append(f"{label} changed from `{old}` to `{new}`.")

        if not changes:
            return "No material field changes detected. This post exists because the alert content fingerprint changed."

        return "\n".join(changes[:6])





    async def b3a_enrich_existing_alert_item(self, guild, alert_id: str, item: dict) -> dict:
        """Rebuild Operations Alert Intelligence from the stable alert key, while preserving action state."""
        item = item or {}

        safe_raw = " ".join([
            str(alert_id or ""),
            str(item.get("identity") or ""),
            str(item.get("rule_id") or ""),
            str(item.get("raw_reference") or ""),
            str(item.get("investigation_route") or ""),
            str(item.get("route") or ""),
        ])

        meta = await self.b3a_build_alert_intelligence(
            guild,
            embed=None,
            content=safe_raw,
            identity=alert_id or item.get("identity") or item.get("rule_id") or "unknown",
            fingerprint=item.get("fingerprint", ""),
            existing=item,
            status=item.get("status") or "ongoing",
        )

        merged = dict(item)
        merged.update(meta)

        preserve_keys = [
            "first_seen",
            "last_seen",
            "last_posted",
            "last_channel_id",
            "last_message_id",
            "post_count",
            "suppressed_count",
            "fingerprint",
            "identity",
            "status",
            "resolved",
            "acknowledged_by",
            "acknowledged_by_id",
            "acknowledged_at",
            "resolved_by",
            "resolved_by_id",
            "resolved_at",
            "reopened_by",
            "reopened_by_id",
            "reopened_at",
            "assigned_role_id",
            "assigned_role_name",
            "assigned_by",
            "assigned_by_id",
            "assigned_at",
            "notes",
            "timeline",
        ]

        for key in preserve_keys:
            if item.get(key) is not None:
                merged[key] = item.get(key)

        if item.get("assigned_role_name"):
            merged["owner"] = item.get("assigned_role_name")

        if item.get("resolved"):
            merged["status"] = "resolved"

        merged["alert_id"] = alert_id
        merged["rule_id"] = alert_id
        merged["identity"] = item.get("identity") or alert_id

        return merged


    async def b3a_find_alert_state_item(self, guild, query: str):
        cfg = await get_core_config(self.bot)
        lifecycle = await cfg.guild(guild).alert_lifecycle()
        lifecycle = lifecycle or {}
        state = lifecycle.get("b2_state") or lifecycle.get("state") or {}

        q = str(query or "").lower().strip()

        # Prefer canonical query matching first.
        canonical_query = self.b3a_canonical_alert_key(q)

        for key, item in state.items():
            if q == str(key).lower() or canonical_query == key:
                enriched = await self.b3a_enrich_existing_alert_item(guild, key, item)
                state[key] = enriched
                lifecycle["b2_state"] = state
                await cfg.guild(guild).alert_lifecycle.set(lifecycle)
                return key, enriched

        # Fuzzy matching.
        for key, item in state.items():
            haystack = " ".join([
                str(key),
                str(item.get("identity", "")),
                str(item.get("alert_id", "")),
                str(item.get("rule_id", "")),
                str(item.get("title", "")),
                str(item.get("area", "")),
            ]).lower()

            if q in haystack:
                enriched = await self.b3a_enrich_existing_alert_item(guild, key, item)
                state[key] = enriched
                lifecycle["b2_state"] = state
                await cfg.guild(guild).alert_lifecycle.set(lifecycle)
                return key, enriched

        return None, None

    async def b3a_enrich_all_alert_state(self, guild):
        cfg = await get_core_config(self.bot)
        lifecycle = await cfg.guild(guild).alert_lifecycle()
        lifecycle = lifecycle or {}
        state = lifecycle.get("b2_state") or lifecycle.get("state") or {}

        changed = 0
        new_state = {}

        for key, item in state.items():
            enriched = await self.b3a_enrich_existing_alert_item(guild, key, item)
            new_state[key] = enriched
            changed += 1

        lifecycle["b2_state"] = new_state
        await cfg.guild(guild).alert_lifecycle.set(lifecycle)

        return changed, new_state




    async def b3a_build_alert_intelligence(self, guild, embed=None, content=None, identity=None, fingerprint=None, existing=None, status=None) -> dict:
        existing = existing or {}

        raw_parts = [
            str(identity or ""),
            str(content or ""),
            str(existing.get("identity") or ""),
            str(existing.get("rule_id") or ""),
            str(existing.get("alert_id") or ""),
            str(existing.get("raw_reference") or ""),
            str(existing.get("investigation_route") or ""),
            str(existing.get("route") or ""),
        ]

        if embed is not None:
            try:
                raw_parts.append(str(getattr(embed, "title", "") or ""))
                raw_parts.append(str(getattr(embed, "description", "") or ""))

                for field in getattr(embed, "fields", []) or []:
                    raw_parts.append(str(getattr(field, "name", "") or ""))
                    raw_parts.append(str(getattr(field, "value", "") or ""))
            except Exception:
                pass

        raw = self.b3a_clean_alert_text(" ".join(raw_parts))
        count = self.b3a_extract_count(raw, existing)

        profile = self.b3a_alert_profile(raw, count)

        path = self.b3a_extract_path(raw)
        route = "Unknown"

        if existing.get("last_channel_id"):
            try:
                ch = guild.get_channel(int(existing.get("last_channel_id")))
                if ch:
                    route = f"#{ch.name}"
            except Exception:
                pass

        if route == "Unknown" and path:
            route = "#" + path

        if route == "Unknown" and any(x in raw.lower() for x in ["audit_highrisk", "high_risk_audit_events", "bot_audit_highrisk", "observatory_logs_audit_log"]):
            route = "#observatory-logs-audit-log / #bot-audit-highrisk"

        status = status or existing.get("status") or "new"

        evidence = existing.get("evidence")

        if not evidence and hasattr(self, "b3e_extract_embed_evidence"):
            try:
                evidence = self.b3e_extract_embed_evidence(embed=embed, content=content)
            except Exception:
                evidence = None

        if evidence and evidence.get("issue_count") is not None:
            try:
                count = int(evidence.get("issue_count"))
            except Exception:
                pass

        meta = {
            "alert_id": identity or existing.get("alert_id") or "unknown",
            "fingerprint": fingerprint or existing.get("fingerprint") or "",
            "rule_id": identity or existing.get("rule_id") or "unknown",
            "title": profile["title"],
            "plain_summary": f"{profile['title']} detected. `{count}` matching item(s) are currently active.",
            "detailed_summary": f"The Mattis alert engine detected `{count}` active item(s) matching this rule. This is tracked through the alert lifecycle so repeated unchanged checks are suppressed instead of spammed.",
            "status": status,
            "severity": profile["severity"],
            "severity_reason": profile["severity_reason"],
            "area": profile["area"],
            "subsystem": profile["subsystem"],
            "owner": profile["owner"],
            "escalation": profile["escalation"],
            "customer_impact": profile["customer_impact"],
            "internal_impact": profile["internal_impact"],
            "count": count,
            "threshold": "Critical keywords override count. Count ≥ 20 = high-volume review. Count ≥ 5 = medium/high review depending on alert type.",
            "trend": "New alert." if not existing else "Ongoing alert. Duplicate unchanged posts are being suppressed.",
            "why_it_matters": profile["why_it_matters"],
            "recommended_action": profile["recommended_action"],
            "investigation_route": route,
            "related_commands": profile["related_commands"],
            "source": "Mattis CMS | Systems alert engine",
            "raw_reference": str(identity or existing.get("rule_id") or "")[:220],
            "post_count": int(existing.get("post_count", 0)),
            "suppressed_count": int(existing.get("suppressed_count", 0)),
            "last_change": "",
            "evidence": evidence or {},
        }

        if existing:
            meta["last_change"] = self.b3a_change_summary(existing, meta)

        return meta


    async def b3a_render_alert_embed(self, guild, embed=None, content=None, meta=None):
        import discord

        meta = meta or {}

        icon = self.b3a_alert_icon(meta.get("severity"), meta.get("status"))
        title = f"{icon} {meta.get('title', 'Mattis CMS Alert')}"
        title = title[:250]

        description = (
            f"**What happened:** {meta.get('plain_summary', 'Unknown')}\n\n"
            f"**Why it matters:** {meta.get('why_it_matters', 'Unknown')}\n\n"
            f"**Recommended next action:** {meta.get('recommended_action', 'Unknown')}"
        )[:3900]

        new_embed = discord.Embed(title=title, description=description)

        try:
            sev = str(meta.get("severity", "")).lower()
            status = str(meta.get("status", "")).lower()

            if status == "resolved":
                new_embed.colour = discord.Colour.green()
            elif sev == "critical":
                new_embed.colour = discord.Colour.red()
            elif sev == "high":
                new_embed.colour = discord.Colour.orange()
            elif sev == "medium":
                new_embed.colour = discord.Colour.gold()
            else:
                new_embed.colour = discord.Colour.blue()
        except Exception:
            pass

        def add(name, value, inline=False):
            value = self.b3a_clean_alert_text(value)

            if not value or str(value).lower() == "none":
                value = "Unknown"

            new_embed.add_field(name=name, value=str(value)[:1024], inline=inline)

        add("Status", self.b3a_status_label(meta.get("status")), True)
        add("Severity", str(meta.get("severity", "unknown")).title(), True)
        add("Count", str(meta.get("count", "?")), True)

        add("Affected Area", meta.get("area"), True)
        add("Subsystem", meta.get("subsystem"), True)
        add("Owner Team", meta.get("owner"), True)

        evidence = meta.get("evidence") or {}
        evidence_brief = ""

        if hasattr(self, "b3e_evidence_brief"):
            try:
                evidence_brief = self.b3e_evidence_brief(evidence)
            except Exception:
                evidence_brief = ""

        if evidence_brief:
            add("Evidence Summary", evidence_brief, False)

        add("Customer Impact", meta.get("customer_impact"), False)
        add("Internal Impact", meta.get("internal_impact"), False)
        add("Severity Reason", meta.get("severity_reason"), False)
        add("Escalation Path", meta.get("escalation"), False)

        add("Investigation Route", meta.get("investigation_route"), True)
        add("Trend", meta.get("trend"), True)

        related = meta.get("related_commands") or []

        if evidence:
            related = list(related) + [
                f"!mcore alerts evidence {meta.get('alert_id', 'audit')}",
                f"!mcore alerts events {meta.get('alert_id', 'audit')}",
                f"!mcore alerts payload {meta.get('alert_id', 'audit')}",
            ]

        if related:
            add("Related Commands", "\n".join(f"`{x}`" for x in related[:10]), False)

        lifecycle = (
            f"Posts: `{meta.get('post_count', 0)}`\n"
            f"Suppressed duplicates: `{meta.get('suppressed_count', 0)}`"
        )
        add("Lifecycle", lifecycle, True)

        add("Alert ID", f"`{str(meta.get('alert_id', 'unknown'))[:240]}`", False)

        try:
            new_embed.set_footer(text="Mattis CMS | Systems • Operations Alert Intelligence")
        except Exception:
            pass

        return new_embed

    async def b2_alert_embed_signature(self, embed=None, content=None):
        """Build a stable alert signature from content/embed without leaking secrets."""
        import re
        import json
        import hashlib

        def clean(value):
            value = "" if value is None else str(value)
            value = re.sub(r"`[^`]{24,}`", "`<redacted>`", value)
            value = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "<redacted>", value)
            value = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+\-Z]+\b", "<time>", value)
            value = re.sub(r"<t:\d+:[A-Za-z]>", "<time>", value)
            value = re.sub(r"\s+", " ", value).strip()
            return value

        data = {
            "content": clean(content),
            "title": "",
            "description": "",
            "fields": [],
            "footer": "",
        }

        if embed is not None:
            try:
                data["title"] = clean(getattr(embed, "title", "") or "")
                data["description"] = clean(getattr(embed, "description", "") or "")

                for field in getattr(embed, "fields", []) or []:
                    name = clean(getattr(field, "name", "") or "")
                    value = clean(getattr(field, "value", "") or "")
                    data["fields"].append([name, value])

                footer = getattr(embed, "footer", None)
                data["footer"] = clean(getattr(footer, "text", "") or "")
            except Exception:
                data["embed"] = clean(repr(embed))

        raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest(), data


    async def b2_alert_identity(self, embed=None, content=None):
        """Build the alert identity. Same rule/endpoint always returns the same ID."""
        parts = [str(content or "")]

        if embed is not None:
            try:
                parts.append(str(getattr(embed, "title", "") or ""))
                parts.append(str(getattr(embed, "description", "") or ""))

                for field in getattr(embed, "fields", []) or []:
                    parts.append(str(getattr(field, "name", "") or ""))
                    parts.append(str(getattr(field, "value", "") or ""))

                footer = getattr(embed, "footer", None)
                parts.append(str(getattr(footer, "text", "") or ""))
            except Exception:
                parts.append(repr(embed))

        return self.b3a_canonical_alert_key(*parts)

    async def b2_alert_guarded_send(self, guild, channel, *args, **kwargs):
        """
        Alert lifecycle hard gate with B3A Operations Alert Intelligence.

        - first alert posts rich operational intelligence
        - exact duplicate suppresses silently
        - changed alert posts once as UPDATED with change summary
        """
        if channel is None:
            return None

        content = kwargs.get("content", None)
        embed = kwargs.get("embed", None)

        if args and content is None and isinstance(args[0], str):
            content = args[0]

        cfg = await get_core_config(self.bot)

        try:
            lifecycle = await cfg.guild(guild).alert_lifecycle()
        except Exception:
            lifecycle = {}

        lifecycle = lifecycle or {}
        settings = lifecycle.get("settings", {})
        enabled = settings.get("enabled", True)

        if not enabled:
            return await channel.send(*args, **kwargs)

        fingerprint, data = await self.b2_alert_embed_signature(embed=embed, content=content)
        identity = await self.b2_alert_identity(embed=embed, content=content)

        state = lifecycle.get("b2_state", {})
        existing = state.get(identity)

        import time
        now = int(time.time())

        # Exact same alert = suppress, but update stats.
        if existing and existing.get("fingerprint") == fingerprint and not existing.get("resolved"):
            existing["last_seen"] = now
            existing["suppressed_count"] = int(existing.get("suppressed_count", 0)) + 1
            existing["status"] = existing.get("status") or "ongoing"
            state[identity] = existing
            lifecycle["b2_state"] = state
            await cfg.guild(guild).alert_lifecycle.set(lifecycle)
            return None

        if existing and existing.get("resolved"):
            status = "reopened"
        elif existing and existing.get("fingerprint") != fingerprint:
            status = "updated"
        else:
            status = "new"

        meta = await self.b3a_build_alert_intelligence(
            guild,
            embed=embed,
            content=content,
            identity=identity,
            fingerprint=fingerprint,
            existing=existing or {},
            status=status,
        )

        # include existing lifecycle counts in the embed preview
        meta["post_count"] = int(existing.get("post_count", 0)) + 1 if existing else 1
        meta["suppressed_count"] = int(existing.get("suppressed_count", 0)) if existing else 0

        rich_embed = await self.b3a_render_alert_embed(
            guild,
            embed=embed,
            content=content,
            meta=meta,
        )

        # Replace noisy content with rich embed.
        kwargs["embed"] = rich_embed
        kwargs["content"] = None

        sent = await channel.send(*args, **kwargs)

        saved = dict(meta)
        saved.update({
            "identity": identity,
            "fingerprint": fingerprint,
            "status": "ongoing" if status == "new" else status,
            "first_seen": existing.get("first_seen", now) if existing else now,
            "last_seen": now,
            "last_posted": now,
            "last_channel_id": getattr(channel, "id", None),
            "last_message_id": getattr(sent, "id", None),
            "post_count": int(existing.get("post_count", 0)) + 1 if existing else 1,
            "suppressed_count": int(existing.get("suppressed_count", 0)) if existing else 0,
        })

        state[identity] = saved
        lifecycle["b2_state"] = state
        await cfg.guild(guild).alert_lifecycle.set(lifecycle)

        return sent

    async def alert_lifecycle_send(self, guild: discord.Guild, rule_name: str, channel, *, content=None, embed=None, allowed_mentions=None, item=None):
        action, record, previous = await self.alert_lifecycle_decide(guild, rule_name, item if item is not None else {"rule": rule_name})

        if action == "skip":
            return None

        status_line = self.alert_lifecycle_status_label(action, record)

        final_content = status_line

        if content:
            final_content = f"{content}\n{status_line}"

        if embed:
            try:
                embed.add_field(name="Alert Status", value=status_line, inline=False)
                embed.add_field(name="Alert ID", value=f"`{record.get('id')}`", inline=False)

                if record.get("first_seen"):
                    embed.add_field(name="First Seen", value=f"`{record.get('first_seen')}`", inline=True)

                if record.get("last_seen"):
                    embed.add_field(name="Last Seen", value=f"`{record.get('last_seen')}`", inline=True)

                if record.get("resolved_at"):
                    embed.add_field(name="Resolved At", value=f"`{record.get('resolved_at')}`", inline=True)
            except Exception:
                pass

        # If resolving and original message exists, try editing it first.
        settings = await self.alert_lifecycle_settings(guild)

        if action in ["updated", "resolved"] and settings.get("edit_original", True):
            old_channel_id = record.get("channel_id") or previous.get("channel_id")
            old_message_id = record.get("message_id") or previous.get("message_id")

            if old_channel_id and old_message_id:
                try:
                    old_channel = guild.get_channel(int(old_channel_id))

                    if old_channel:
                        old_message = await old_channel.fetch_message(int(old_message_id))
                        await old_message.edit(content=final_content, embed=embed, allowed_mentions=allowed_mentions)
                        await self.alert_lifecycle_mark_message(guild, record.get("id"), old_channel_id, old_message_id)
                        return old_message
                except Exception:
                    pass

        msg = await channel.send(content=final_content, embed=embed, allowed_mentions=allowed_mentions)
        await self.alert_lifecycle_mark_message(guild, record.get("id"), channel.id, msg.id)
        return msg

    @alerts.group(name="lifecycle", invoke_without_command=True)
    async def alerts_lifecycle(self, ctx):
        """Show alert lifecycle status."""
        if not await require_admin(ctx):
            return

        settings = await self.alert_lifecycle_settings(ctx.guild)
        state = await self.alert_lifecycle_state(ctx.guild)

        ongoing = 0
        resolved = 0

        for record in state.values():
            if record.get("status") == "resolved":
                resolved += 1
            else:
                ongoing += 1

        lines = [
            f"Enabled: `{'yes' if settings.get('enabled', True) else 'no'}`",
            f"Post updates: `{'yes' if settings.get('post_updates', True) else 'no'}`",
            f"Post resolved: `{'yes' if settings.get('post_resolved', True) else 'no'}`",
            f"Edit original message: `{'yes' if settings.get('edit_original', True) else 'no'}`",
            f"Tracked alerts: `{len(state)}`",
            f"Ongoing: `{ongoing}`",
            f"Resolved: `{resolved}`",
            "",
            "**Commands:**",
            "`!mcore alerts lifecycle on`",
            "`!mcore alerts lifecycle off`",
            "`!mcore alerts lifecycle state`",
            "`!mcore alerts lifecycle reset`",
            "`!mcore alerts lifecycle resolved on/off`",
            "`!mcore alerts lifecycle updates on/off`",
            "`!mcore alerts lifecycle edit on/off`",
        ]

        await self.send_paginated(ctx, "Alert Lifecycle", lines)

    @alerts_lifecycle.command(name="on")
    async def alerts_lifecycle_on(self, ctx):
        if not await require_admin(ctx):
            return

        data = await self.alert_lifecycle_get_guild(ctx.guild)
        settings = data.get("_settings") or {}
        settings["enabled"] = True
        data["_settings"] = settings
        await self.alert_lifecycle_set_guild(ctx.guild, data)

        await ctx.send(embed=ok_embed("Alert lifecycle enabled", "Duplicate ongoing alerts will now be suppressed."))

    @alerts_lifecycle.command(name="off")
    async def alerts_lifecycle_off(self, ctx):
        if not await require_admin(ctx):
            return

        data = await self.alert_lifecycle_get_guild(ctx.guild)
        settings = data.get("_settings") or {}
        settings["enabled"] = False
        data["_settings"] = settings
        await self.alert_lifecycle_set_guild(ctx.guild, data)

        await ctx.send(embed=ok_embed("Alert lifecycle disabled", "Alerts will post normally again."))

    @alerts_lifecycle.command(name="resolved")
    async def alerts_lifecycle_resolved(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = mode.lower() in ["on", "yes", "true", "1", "enable", "enabled"]
        data = await self.alert_lifecycle_get_guild(ctx.guild)
        settings = data.get("_settings") or {}
        settings["post_resolved"] = enabled
        data["_settings"] = settings
        await self.alert_lifecycle_set_guild(ctx.guild, data)

        await ctx.send(embed=ok_embed("Resolved alert posting updated", f"Resolved posts are now `{'on' if enabled else 'off'}`."))

    @alerts_lifecycle.command(name="updates")
    async def alerts_lifecycle_updates(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = mode.lower() in ["on", "yes", "true", "1", "enable", "enabled"]
        data = await self.alert_lifecycle_get_guild(ctx.guild)
        settings = data.get("_settings") or {}
        settings["post_updates"] = enabled
        data["_settings"] = settings
        await self.alert_lifecycle_set_guild(ctx.guild, data)

        await ctx.send(embed=ok_embed("Alert update posting updated", f"Update posts are now `{'on' if enabled else 'off'}`."))

    @alerts_lifecycle.command(name="edit")
    async def alerts_lifecycle_edit(self, ctx, mode: str):
        if not await require_admin(ctx):
            return

        enabled = mode.lower() in ["on", "yes", "true", "1", "enable", "enabled"]
        data = await self.alert_lifecycle_get_guild(ctx.guild)
        settings = data.get("_settings") or {}
        settings["edit_original"] = enabled
        data["_settings"] = settings
        await self.alert_lifecycle_set_guild(ctx.guild, data)

        await ctx.send(embed=ok_embed("Alert message editing updated", f"Original alert editing is now `{'on' if enabled else 'off'}`."))

    @alerts_lifecycle.command(name="state")
    async def alerts_lifecycle_state_cmd(self, ctx):
        if not await require_admin(ctx):
            return

        state = await self.alert_lifecycle_state(ctx.guild)
        lines = []

        for key, record in sorted(state.items()):
            lines.append(
                f"**{key}**\n"
                f"Rule: `{record.get('rule')}`\n"
                f"Status: `{record.get('status')}`\n"
                f"Severity: `{record.get('severity')}`\n"
                f"Count: `{record.get('count')}`\n"
                f"First seen: `{record.get('first_seen')}`\n"
                f"Last seen: `{record.get('last_seen')}`\n"
                f"Last posted: `{record.get('last_posted')}`\n"
                f"Last action: `{record.get('last_action')}`"
            )

        await self.send_paginated(ctx, "Alert Lifecycle State", lines, empty="No alert lifecycle state saved.")

    @alerts_lifecycle.command(name="reset")
    async def alerts_lifecycle_reset(self, ctx):
        if not await require_admin(ctx):
            return

        data = await self.alert_lifecycle_get_guild(ctx.guild)
        data["state"] = {}
        await self.alert_lifecycle_set_guild(ctx.guild, data)

        await ctx.send(embed=ok_embed("Alert lifecycle reset", "Tracked alert state has been cleared."))


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
