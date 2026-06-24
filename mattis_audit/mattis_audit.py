from __future__ import annotations

from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload


class MattisAudit(commands.Cog):
    """Audit log lookups."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="maudit")
    @commands.has_permissions(manage_guild=True)
    async def maudit(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Audit", "Commands: `maudit recent`, `maudit highrisk`, `maudit search <query>`"))

    @maudit.command(name="recent")
    async def recent(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/audit/recent")
        await ctx.send(embed=embed(f"Recent audit → {status}", fmt_payload(payload)))

    @maudit.command(name="highrisk")
    async def highrisk(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/audit/recent?risk=high")
        await ctx.send(embed=embed(f"High-risk audit → {status}", fmt_payload(payload)))

    @maudit.command(name="search")
    async def search(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/audit/search?q={query}")
        await ctx.send(embed=embed(f"Audit search → {status}", fmt_payload(payload)))
