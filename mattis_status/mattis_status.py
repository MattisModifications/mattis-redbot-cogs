from __future__ import annotations

import time
import aiohttp
import discord
from redbot.core import commands

from .shared_mattis import (
    embed,
    require_staff,
    require_admin,
    require_development,
    request_json,
    get_core_config,
    fmt_payload,
    simple_counts_embed,
)


class MattisStatus(commands.Cog):
    """Mattis CMS | Systems status checks."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mstatus", invoke_without_command=True)
    async def mstatus(self, ctx):
        """Mattis CMS | Systems health summary."""
        if not await require_staff(ctx):
            return
        await self.overview(ctx)

    @mstatus.command(name="overview")
    async def overview(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/status")

        e = simple_counts_embed("Mattis CMS | Systems Status", payload if status == 200 else {})
        e.color = discord.Color.green() if status == 200 else discord.Color.red()
        e.add_field(name="API", value=f"HTTP {status}", inline=False)

        await ctx.send(embed=e)

    @mstatus.command(name="api")
    async def api(self, ctx):
        if not await require_staff(ctx):
            return

        cfg = await get_core_config(self.bot)
        api_url = (await cfg.api_url()).rstrip("/")

        started = time.perf_counter()
        status, payload = await request_json(self.bot, "GET", "/health", timeout=10)
        latency = round((time.perf_counter() - started) * 1000)

        e = embed("API Health")
        e.add_field(name="Status", value=f"HTTP {status}", inline=True)
        e.add_field(name="Latency", value=f"{latency}ms", inline=True)
        e.add_field(name="URL", value=api_url, inline=False)
        e.add_field(name="Response", value=fmt_payload(payload)[:1024], inline=False)

        await ctx.send(embed=e)

    @mstatus.command(name="web")
    async def web(self, ctx):
        if not await require_staff(ctx):
            return

        web_url = "https://mattisproductions.com"
        started = time.perf_counter()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(web_url, timeout=10) as resp:
                    latency = round((time.perf_counter() - started) * 1000)

                    e = embed("Website Health")
                    e.add_field(name="Status", value=f"HTTP {resp.status}", inline=True)
                    e.add_field(name="Latency", value=f"{latency}ms", inline=True)
                    e.add_field(name="URL", value=web_url, inline=False)

                    await ctx.send(embed=e)

        except Exception as exc:
            await ctx.send(embed=embed("Website Health", f"{type(exc).__name__}: {exc}", color=discord.Color.red()))

    @mstatus.command(name="database", aliases=["redis", "workers", "pm2"])
    async def admin_snapshot(self, ctx):
        if not await require_development(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/systems/counts")
        await ctx.send(embed=simple_counts_embed("Systems Service Snapshot", payload if status == 200 else {}))

    @commands.command(name="muptime")
    async def muptime(self, ctx):
        await self.overview(ctx)

    @commands.command(name="mhealth")
    async def mhealth(self, ctx):
        await self.overview(ctx)
