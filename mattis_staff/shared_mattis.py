from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from urllib.parse import quote

import aiohttp
import discord
from redbot.core import Config


IDENTIFIER = 912406121210
BRAND = discord.Color.from_rgb(28, 45, 74)
ERROR = discord.Color.red()
OK = discord.Color.green()
WARN = discord.Color.gold()


async def get_core_config(bot) -> Config:
    cfg = Config.get_conf(bot, identifier=IDENTIFIER, force_registration=True)
    cfg.register_global(api_url="", api_token="")
    cfg.register_guild(
        systems_channels={},
        staff_roles=[],
        admin_roles=[],
        role_groups={},
        role_sections={},
        route_backups=[],
        alert_settings={},
        alert_state={},
        log_settings={},
        log_state={},
        notify_settings={},
        eventlog_settings={},
    )
    return cfg


def q(value: str) -> str:
    return quote(str(value), safe="")


def trim(value: Any, limit: int = 1000) -> str:
    text = str(value) if value is not None else "—"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def pretty_key(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?<!^)(?=[A-Z])", " ", text)
    text = text.replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in text.split())


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def embed(title: str, description: str | None = None, *, color: discord.Color = BRAND) -> discord.Embed:
    e = discord.Embed(title=title, description=description or None, color=color)
    e.set_footer(text="Mattis CMS | Systems")
    return e


def error_embed(title: str, message: str) -> discord.Embed:
    return embed(title, message, color=ERROR)


def ok_embed(title: str, message: str | None = None) -> discord.Embed:
    return embed(title, message, color=OK)


def add_fields(e: discord.Embed, data: dict[str, Any], *, inline: bool = True, max_fields: int = 20) -> discord.Embed:
    added = 0
    for key, value in data.items():
        if added >= max_fields:
            break

        if isinstance(value, dict):
            value = ", ".join(f"{pretty_key(k)}: {v}" for k, v in value.items())
        elif isinstance(value, list):
            value = ", ".join(map(str, value[:8])) if value else "—"

        e.add_field(name=pretty_key(key), value=trim(value, 1024), inline=inline)
        added += 1

    return e


def line_list(items: list[Any], formatter, *, empty: str = "Nothing found.", limit: int = 10) -> str:
    if not items:
        return empty

    lines = []
    for item in items[:limit]:
        try:
            lines.append(formatter(item))
        except Exception:
            lines.append(trim(item, 180))

    if len(items) > limit:
        lines.append(f"…and {len(items) - limit} more.")

    return "\n".join(lines)


def fmt_payload(payload: Any) -> str:
    if payload is None:
        return "No response body."

    if isinstance(payload, str):
        return trim(payload, 1800)

    return f"```json\n{json.dumps(payload, indent=2, ensure_ascii=False)[:1800]}\n```"


async def request_json(
    bot,
    method: str,
    path: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    timeout: int = 20,
) -> tuple[int, Any]:
    cfg = await get_core_config(bot)
    api_url = (await cfg.api_url()).rstrip("/")
    token = await cfg.api_token()

    if not api_url:
        raise RuntimeError("Mattis API URL is not configured. Run: !mcore apiurl https://api.mattisproductions.com")

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mattis-CMS-Systems/3.0",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{api_url}{path if path.startswith('/') else '/' + path}"

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.request(method.upper(), url, json=json_body, timeout=timeout) as resp:
            text = await resp.text()
            try:
                return resp.status, json.loads(text) if text else None
            except json.JSONDecodeError:
                return resp.status, text


async def get_role_sections(ctx) -> dict[str, list[int]]:
    cfg = await get_core_config(ctx.bot)
    sections = await cfg.guild(ctx.guild).role_sections()
    sections = sections or {}

    old_groups = await cfg.guild(ctx.guild).role_groups()
    old_groups = old_groups or {}

    legacy_map = {
        "support": "Support Team",
        "moderation": "Moderation & Support",
        "staff": "Staff",
        "management": "Management",
        "admin": "Administration Team",
        "development": "Development Team",
    }

    for key, section_name in legacy_map.items():
        for rid in old_groups.get(key, []) or []:
            sections.setdefault(section_name, [])
            if rid not in sections[section_name]:
                sections[section_name].append(rid)

    old_staff = await cfg.guild(ctx.guild).staff_roles()
    old_admin = await cfg.guild(ctx.guild).admin_roles()

    for rid in old_staff or []:
        sections.setdefault("Staff", [])
        if rid not in sections["Staff"]:
            sections["Staff"].append(rid)

    for rid in old_admin or []:
        sections.setdefault("Administration Team", [])
        if rid not in sections["Administration Team"]:
            sections["Administration Team"].append(rid)

    return sections


def member_role_ids(ctx) -> set[int]:
    return {role.id for role in getattr(ctx.author, "roles", [])}


def member_role_names(ctx) -> list[str]:
    return [role.name for role in getattr(ctx.author, "roles", [])]


async def member_in_sections(ctx, section_keywords: list[str]) -> bool:
    if not ctx.guild:
        return False

    sections = await get_role_sections(ctx)
    author_roles = member_role_ids(ctx)
    wanted_keywords = [norm(k) for k in section_keywords]

    for section_name, role_ids in sections.items():
        section_norm = norm(section_name)
        if any(key in section_norm for key in wanted_keywords):
            if author_roles.intersection(set(role_ids or [])):
                return True

    return False


async def member_has_role_keywords(ctx, role_keywords: list[str]) -> bool:
    wanted = [norm(k) for k in role_keywords]

    for role_name in member_role_names(ctx):
        role_norm = norm(role_name)
        if any(key in role_norm for key in wanted):
            return True

    return False


async def is_admin(ctx) -> bool:
    if await ctx.bot.is_owner(ctx.author):
        return True

    if not ctx.guild:
        return False

    if ctx.author.guild_permissions.administrator:
        return True

    if await member_has_role_keywords(ctx, ["founder", "owner"]):
        return True

    if await member_in_sections(ctx, ["administration"]):
        return True

    return False


async def is_management(ctx) -> bool:
    if await is_admin(ctx):
        return True

    if await member_in_sections(ctx, ["management"]):
        return True

    return False


async def is_administration(ctx) -> bool:
    if await is_management(ctx):
        return True

    if await member_in_sections(ctx, ["administration"]):
        return True

    if await member_has_role_keywords(ctx, ["administrator", "infrastructureadmin", "securityadmin"]):
        return True

    return False


async def is_development(ctx) -> bool:
    if await is_administration(ctx):
        return True

    if await member_in_sections(ctx, ["development"]):
        return True

    if await member_has_role_keywords(ctx, ["developer", "qtester", "qatester", "releasemanager", "designer"]):
        return True

    return False


async def is_support(ctx) -> bool:
    if await is_management(ctx):
        return True

    if await member_in_sections(ctx, ["support", "moderation"]):
        return True

    if await member_has_role_keywords(ctx, ["support", "moderator", "incidentresponse", "auditreviewer"]):
        return True

    return False


async def is_billing(ctx) -> bool:
    if await is_management(ctx):
        return True

    if await member_has_role_keywords(ctx, ["billingsupport", "billing"]):
        return True

    return False


async def is_security(ctx) -> bool:
    if await is_administration(ctx):
        return True

    if await member_has_role_keywords(ctx, ["security", "incidentresponse", "auditreviewer", "seniormoderator"]):
        return True

    if await member_in_sections(ctx, ["moderation"]):
        return True

    return False


async def is_staff(ctx) -> bool:
    if await is_management(ctx) or await is_development(ctx) or await is_support(ctx):
        return True

    if not ctx.guild:
        return False

    if ctx.author.guild_permissions.manage_guild:
        return True

    return False


async def require_staff(ctx) -> bool:
    if await is_staff(ctx):
        return True

    await ctx.send(embed=error_embed(
        "Permission denied",
        "You need a Mattis Systems staff, support, moderation, development, management, or admin role.",
    ))
    return False


async def require_management(ctx) -> bool:
    if await is_management(ctx):
        return True

    await ctx.send(embed=error_embed(
        "Permission denied",
        "You need a Mattis Systems management/admin role.",
    ))
    return False


async def require_admin(ctx) -> bool:
    if await is_admin(ctx):
        return True

    await ctx.send(embed=error_embed(
        "Permission denied",
        "You need a Mattis Systems admin role or Administrator permission.",
    ))
    return False


async def require_development(ctx) -> bool:
    if await is_development(ctx):
        return True

    await ctx.send(embed=error_embed(
        "Permission denied",
        "You need Development Team, Administration Team, Management, or Admin access.",
    ))
    return False


async def require_billing(ctx) -> bool:
    if await is_billing(ctx):
        return True

    await ctx.send(embed=error_embed(
        "Permission denied",
        "You need Billing Support, Management, or Admin access.",
    ))
    return False


async def require_security(ctx) -> bool:
    if await is_security(ctx):
        return True

    await ctx.send(embed=error_embed(
        "Permission denied",
        "You need Security/Moderation/Audit/Incident, Administration, Management, or Admin access.",
    ))
    return False


def workspace_line(w: dict[str, Any]) -> str:
    status = w.get("subscriptionStatus") or w.get("customerStatus") or "unknown"
    risk = w.get("customerRisk", "unknown")
    flags = []

    if w.get("suspended"):
        flags.append("SUSPENDED")

    if w.get("frozen"):
        flags.append("FROZEN")

    suffix = f" · {' · '.join(flags)}" if flags else ""

    return f"**{w.get('name', 'Unknown')}** (`{w.get('slug', w.get('id', '—'))}`) — {status} · risk: {risk}{suffix}"


def user_line(u: dict[str, Any]) -> str:
    label = u.get("email") or u.get("discordUsername") or u.get("robloxUsername") or u.get("id")
    return f"**{label}** — role: {u.get('platformRole', 'user')} · MFA: {'yes' if u.get('mfaEnabled') else 'no'}"


def ticket_line(t: dict[str, Any]) -> str:
    return f"**#{t.get('ticketNumber', t.get('id'))}** · {t.get('priority', 'medium')} · {t.get('status', 'unknown')} — {trim(t.get('subject', 'No subject'), 90)}"


def invoice_line(i: dict[str, Any]) -> str:
    amount = i.get("amountDue") or i.get("amountPaid") or "—"
    currency = i.get("currency") or ""
    return f"**{i.get('status', 'unknown')}** · {amount} {currency} · `{i.get('id', '—')}`"


def audit_line(a: dict[str, Any]) -> str:
    risk = f" · risk: {a.get('riskLevel')}" if a.get("riskLevel") else ""
    return f"**{a.get('action', 'event')}** · {a.get('targetType', 'target')}{risk}"


def staff_line(s: dict[str, Any]) -> str:
    return f"**{s.get('displayName', 'Unknown')}** — {s.get('department') or 'No department'} · {s.get('rankName') or 'No rank'} · {s.get('status', 'unknown')}"


def simple_counts_embed(title: str, payload: dict[str, Any]) -> discord.Embed:
    e = embed(title)
    counts = payload.get("counts", payload) if isinstance(payload, dict) else {}

    if isinstance(counts, dict):
        add_fields(e, counts)

    return e
