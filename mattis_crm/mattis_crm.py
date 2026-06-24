from __future__ import annotations

from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload


class MattisCRM(commands.Cog):
    """CRM lookups."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mcrm")
    @commands.has_permissions(manage_guild=True)
    async def mcrm(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis CRM", "Commands: `mcrm lookup <query>`, `mcrm health`, `mcrm onboarding`"))

    @mcrm.command(name="lookup")
    async def lookup(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/crm/search?q={query}")
        await ctx.send(embed=embed(f"CRM lookup → {status}", fmt_payload(payload)))

    @mcrm.command(name="health")
    async def health(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/crm/health")
        await ctx.send(embed=embed(f"CRM health → {status}", fmt_payload(payload)))

    @mcrm.command(name="onboarding")
    async def onboarding(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/crm/onboarding")
        await ctx.send(embed=embed(f"CRM onboarding → {status}", fmt_payload(payload)))
