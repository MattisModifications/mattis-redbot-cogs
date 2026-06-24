from __future__ import annotations

from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload


class MattisOffice(commands.Cog):
    """Internal Mattis office commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="moffice")
    async def moffice(self, ctx):
        """Mattis office command centre."""
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Office", "Commands: `moffice links`, `moffice customer <query>`"))

    @moffice.command(name="links")
    async def links(self, ctx):
        await ctx.send(embed=embed("Mattis Office Links", "Portal: https://mattisproductions.com\nAPI: https://api.mattisproductions.com/health"))

    @moffice.command(name="customer")
    @commands.has_permissions(manage_guild=True)
    async def customer(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/customers/search?q={query}")
        await ctx.send(embed=embed(f"Customer search → {status}", fmt_payload(payload)))
