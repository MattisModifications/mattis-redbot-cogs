from __future__ import annotations

import json
from typing import Any, Optional

import aiohttp
import discord
from redbot.core import Config


BRAND = discord.Color.from_rgb(28, 45, 74)


async def get_core_config(bot) -> Config:
    return Config.get_conf(bot, identifier=912406121210, force_registration=True)


async def request_json(bot, method: str, path: str, *, json_body: Optional[dict[str, Any]] = None, timeout: int = 15) -> tuple[int, Any]:
    cfg = await get_core_config(bot)
    api_url = (await cfg.api_url()).rstrip("/")
    token = await cfg.api_token()
    if not api_url:
        raise RuntimeError("Mattis API URL is not configured. Run: mcore apiurl https://api.mattisproductions.com")
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mattis-Redbot-Cogs/0.1.0",
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


def embed(title: str, description: str | None = None, *, color: discord.Color = BRAND) -> discord.Embed:
    e = discord.Embed(title=title, description=description or None, color=color)
    e.set_footer(text="Mattis CMS")
    return e


def fmt_payload(payload: Any) -> str:
    if payload is None:
        return "No response body."
    if isinstance(payload, str):
        return payload[:1800]
    return f"```json\n{json.dumps(payload, indent=2, ensure_ascii=False)[:1800]}\n```"
