from __future__ import annotations

from redbot.core import commands
from .shared_mattis import embed, request_json, fmt_payload


class MattisCommand(commands.Cog):
    """Internal Mattis command commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mcommand")
    async def mcommand(self, ctx):
        """Mattis command command centre."""
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Command", "Commands: `mcommand links`, `mcommand customer <query>`"))

    @mcommand.command(name="links")
    async def links(self, ctx):
        await ctx.send(embed=embed("Mattis Command Links", "Portal: https://mattisproductions.com\nAPI: https://api.mattisproductions.com/health"))

    @mcommand.command(name="customer")
    @commands.has_permissions(manage_guild=True)
    async def customer(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/command/customers/search?q={query}")
        await ctx.send(embed=embed(f"Customer search → {status}", fmt_payload(payload)))
