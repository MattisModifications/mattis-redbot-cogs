from __future__ import annotations

from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload


class MattisSecurity(commands.Cog):
    """Security and identity-risk lookups."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="msecurity")
    @commands.has_permissions(manage_guild=True)
    async def msecurity(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Security", "Commands: `msecurity risks`, `msecurity user <query>`, `msecurity failedlogins`"))

    @msecurity.command(name="risks")
    async def risks(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/security/risks")
        await ctx.send(embed=embed(f"Security risks → {status}", fmt_payload(payload)))

    @msecurity.command(name="user")
    async def user(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/security/users/search?q={query}")
        await ctx.send(embed=embed(f"Security user → {status}", fmt_payload(payload)))

    @msecurity.command(name="failedlogins")
    async def failedlogins(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/security/failed-logins")
        await ctx.send(embed=embed(f"Failed logins → {status}", fmt_payload(payload)))
