from redbot.core import commands

from .shared_mattis import embed, request_json, fmt_payload


class MattisBilling(commands.Cog):
    """Mattis billing operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mbilling")
    @commands.has_permissions(manage_guild=True)
    async def mbilling(self, ctx):
        """Mattis billing commands."""
        if ctx.invoked_subcommand is None:
            status, payload = await request_json(self.bot, "GET", "/bot/billing/summary")
            e = embed("Billing Summary", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
            await ctx.send(embed=e)

    @mbilling.command(name="summary")
    async def summary(self, ctx):
        """Show billing summary."""
        status, payload = await request_json(self.bot, "GET", "/bot/billing/summary")
        e = embed("Billing Summary", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mbilling.command(name="customer")
    async def customer(self, ctx, *, query: str):
        """Search billing customer via CRM."""
        status, payload = await request_json(self.bot, "GET", f"/bot/crm/search?q={query}")
        e = embed("Billing Customer Search", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mbilling.command(name="invoices")
    async def invoices(self, ctx, *, query: str = ""):
        """Show invoice summary."""
        status, payload = await request_json(self.bot, "GET", "/bot/billing/summary")
        e = embed("Invoices", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mbilling.command(name="failed")
    async def failed(self, ctx):
        """Show failed invoice summary."""
        status, payload = await request_json(self.bot, "GET", "/bot/billing/summary")
        e = embed("Failed Billing", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)
