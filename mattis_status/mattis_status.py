from __future__ import annotations

import time
import aiohttp
import discord
from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload, get_core_config


class MattisStatus(commands.Cog):
    """Mattis uptime and health checks."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="mstatus")
    async def mstatus(self, ctx):
        """Quick Mattis status check."""
        await self._health(ctx, full=False)

    @commands.command(name="muptime")
    async def muptime(self, ctx):
        """Alias for Mattis status."""
        await self._health(ctx, full=False)

    @commands.command(name="mhealth")
    async def mhealth(self, ctx):
        """Full Mattis health check."""
        await self._health(ctx, full=True)

    async def _health(self, ctx, *, full: bool):
        cfg = await get_core_config(self.bot)
        api_url = (await cfg.api_url()).rstrip("/")
        if not api_url:
            await ctx.send("Mattis API URL is not configured. Run `mcore apiurl https://api.mattisproductions.com`.")
            return
        start = time.perf_counter()
        e = embed("Mattis CMS Health")
        try:
            status, payload = await request_json(self.bot, "GET", "/health", timeout=10)
            latency = round((time.perf_counter() - start) * 1000)
            ok = status < 500
            e.color = discord.Color.green() if ok else discord.Color.red()
            e.add_field(name="API", value=f"{'✅' if ok else '❌'} HTTP {status} — {latency}ms", inline=False)
            if full:
                e.add_field(name="Response", value=fmt_payload(payload)[:1024], inline=False)
        except Exception as exc:
            e.color = discord.Color.red()
            e.add_field(name="API", value=f"❌ {type(exc).__name__}: {exc}", inline=False)
        # Website ping from APP URL convention
        try:
            web_url = "https://mattisproductions.com"
            wstart = time.perf_counter()
            async with aiohttp.ClientSession() as session:
                async with session.get(web_url, timeout=10) as resp:
                    wlat = round((time.perf_counter() - wstart) * 1000)
                    e.add_field(name="Website", value=f"{'✅' if resp.status < 500 else '❌'} HTTP {resp.status} — {wlat}ms", inline=False)
        except Exception as exc:
            e.add_field(name="Website", value=f"❌ {type(exc).__name__}: {exc}", inline=False)
        await ctx.send(embed=e)
