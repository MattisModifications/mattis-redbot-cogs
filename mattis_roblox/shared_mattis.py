from __future__ import annotations

import json
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
    cfg.register_guild(systems_channels={}, staff_roles=[], admin_roles=[])
    return cfg


def q(value: str) -> str:
    return quote(str(value), safe="")


def trim(value: Any, limit: int = 1000) -> str:
    text = str(value) if value is not None else "—"
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
            value = ", ".join(f"{k}: {v}" for k, v in value.items())
        elif isinstance(value, list):
            value = ", ".join(map(str, value[:8])) if value else "—"

        e.add_field(
            name=str(key).replace("_", " ").title(),
            value=trim(value, 1024),
            inline=inline,
        )
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
        "User-Agent": "Mattis-CMS-Systems/2.0",
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


async def api_embed(
    bot,
    title: str,
    method: str,
    path: str,
    *,
    payload_formatter=None,
    json_body: Optional[dict[str, Any]] = None,
) -> discord.Embed:
    try:
        started = time.perf_counter()
        status, payload = await request_json(bot, method, path, json_body=json_body)
        latency = round((time.perf_counter() - started) * 1000)

        if status >= 400:
            return error_embed(f"{title} → HTTP {status}", fmt_payload(payload))

        if payload_formatter:
            e = payload_formatter(payload)
        else:
            e = embed(title, fmt_payload(payload))

        e.add_field(name="API", value=f"HTTP {status} · {latency}ms", inline=False)
        return e

    except Exception as exc:
        return error_embed(title, f"{type(exc).__name__}: {exc}")


async def is_admin(ctx) -> bool:
    if await ctx.bot.is_owner(ctx.author):
        return True

    if not ctx.guild:
        return False

    if ctx.author.guild_permissions.administrator:
        return True

    cfg = await get_core_config(ctx.bot)
    admin_roles = await cfg.guild(ctx.guild).admin_roles()
    author_roles = {role.id for role in getattr(ctx.author, "roles", [])}

    return bool(author_roles.intersection(set(admin_roles or [])))


async def is_staff(ctx) -> bool:
    if await is_admin(ctx):
        return True

    if not ctx.guild:
        return False

    if ctx.author.guild_permissions.manage_guild:
        return True

    cfg = await get_core_config(ctx.bot)
    staff_roles = await cfg.guild(ctx.guild).staff_roles()
    author_roles = {role.id for role in getattr(ctx.author, "roles", [])}

    return bool(author_roles.intersection(set(staff_roles or [])))


async def require_staff(ctx) -> bool:
    if await is_staff(ctx):
        return True

    await ctx.send(embed=error_embed(
        "Permission denied",
        "You need a Mattis Systems staff role or Manage Server permission.",
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
