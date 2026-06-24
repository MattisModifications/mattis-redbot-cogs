from __future__ import annotations

from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload


class MattisBilling(commands.Cog):
    """Billing and invoice lookups."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mbilling")
    @commands.has_permissions(manage_guild=True)
    async def mbilling(self, ctx):
        """Mattis billing commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Billing", "Commands: `mbilling customer <query>`, `mbilling invoices <query>`, `mbilling failed`"))

    @mbilling.command(name="customer")
    async def customer(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/billing/customers/search?q={query}")
        await ctx.send(embed=embed(f"Billing customer → {status}", fmt_payload(payload)))

    @mbilling.command(name="invoices")
    async def invoices(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/billing/invoices?q={query}")
        await ctx.send(embed=embed(f"Invoices → {status}", fmt_payload(payload)))

    @mbilling.command(name="failed")
    async def failed(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/billing/invoices?status=failed")
        await ctx.send(embed=embed(f"Failed invoices → {status}", fmt_payload(payload)))
