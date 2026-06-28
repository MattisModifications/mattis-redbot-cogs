from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_admin,
    require_billing,
    simple_counts_embed,
    line_list,
    invoice_line,
    workspace_line,
    q,
)


class MattisBilling(commands.Cog):
    """Mattis billing operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mbilling", invoke_without_command=True)
    async def mbilling(self, ctx):
        if not await require_billing(ctx):
            return
        await self.summary(ctx)

    @mbilling.command(name="summary")
    async def summary(self, ctx):
        if not await require_billing(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/billing/summary")
        e = simple_counts_embed("Billing Summary", payload if status == 200 else {})

        recent = payload.get("recent", []) if isinstance(payload, dict) else []
        e.add_field(name="Recent invoices", value=line_list(recent, invoice_line, empty="No recent invoices."), inline=False)

        await ctx.send(embed=e)

    @mbilling.command(name="failed")
    async def failed(self, ctx):
        if not await require_billing(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/billing/failed")
        await ctx.send(embed=embed("Failed Billing", line_list(payload.get("invoices", []), invoice_line, empty="No failed invoices.")))

    @mbilling.command(name="trials")
    async def trials(self, ctx):
        if not await require_billing(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/billing/trials")
        await ctx.send(embed=embed("Trial Subscriptions", f"Count: {payload.get('count', 0)}"))

    @mbilling.command(name="pastdue")
    async def pastdue(self, ctx):
        if not await require_billing(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/billing/pastdue")
        await ctx.send(embed=embed("Past-Due Subscriptions", f"Count: {payload.get('count', 0)}"))

    @mbilling.command(name="customer")
    async def customer(self, ctx, *, query: str):
        if not await require_billing(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/billing/customer?q={q(query)}")

        e = embed(f"Customer Billing: {query}")

        if status == 200:
            e.add_field(name="Workspace", value=workspace_line(payload.get("workspace", {})), inline=False)
            e.add_field(name="Invoices", value=line_list(payload.get("invoices", []), invoice_line, empty="No invoices."), inline=False)
        else:
            e.description = f"HTTP {status}"

        await ctx.send(embed=e)

    @mbilling.command(name="invoices")
    async def invoices(self, ctx, *, query: str = ""):
        if not await require_billing(ctx):
            return

        if query:
            await self.customer(ctx, query=query)
        else:
            await self.summary(ctx)
