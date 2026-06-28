from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_staff,
    require_admin,
    simple_counts_embed,
    line_list,
    workspace_line,
    q,
)


class MattisSystems(commands.Cog):
    """Mattis CMS | Systems overview commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="msystems", invoke_without_command=True)
    async def msystems(self, ctx):
        """Mattis CMS | Systems dashboard."""
        if not await require_staff(ctx):
            return
        await self.overview(ctx)

    @msystems.command(name="overview")
    async def overview(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/systems/overview")
        e = simple_counts_embed("Mattis CMS | Systems Overview", payload if status == 200 else {})

        if isinstance(payload, dict):
            e.description = payload.get("summary")

        await ctx.send(embed=e)

    @msystems.command(name="links")
    async def links(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/systems/links")
        links = payload.get("links", []) if isinstance(payload, dict) else []

        desc = line_list(
            links,
            lambda l: f"**{l.get('label')}**\n{l.get('url')}",
            empty="No links configured.",
        )

        await ctx.send(embed=embed("Mattis Systems Links", desc))

    @msystems.command(name="counts")
    async def counts(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/systems/counts")
        await ctx.send(embed=simple_counts_embed("Mattis Systems Counts", payload if status == 200 else {}))

    @msystems.command(name="alerts")
    async def alerts(self, ctx):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/systems/alerts")
        e = embed("Mattis Systems Alerts")

        if status != 200:
            e.description = f"HTTP {status}"
        else:
            e.add_field(name="Suspended", value=str(len(payload.get("suspendedWorkspaces", []))), inline=True)
            e.add_field(name="Frozen", value=str(len(payload.get("frozenWorkspaces", []))), inline=True)
            e.add_field(name="Critical tickets", value=str(len(payload.get("criticalTickets", []))), inline=True)
            e.add_field(name="Failed invoices", value=str(len(payload.get("failedInvoices", []))), inline=True)

        await ctx.send(embed=e)

    @msystems.command(name="modules")
    async def modules(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/modules/summary")
        await ctx.send(embed=simple_counts_embed("Systems Modules", payload if status == 200 else {}))

    @msystems.command(name="customer")
    async def customer(self, ctx, *, query: str):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/crm/search?q={q(query)}")
        workspaces = payload.get("workspaces", []) if isinstance(payload, dict) else []
        users = payload.get("users", []) if isinstance(payload, dict) else []

        e = embed(f"Systems Search: {query}")
        e.add_field(name="Workspaces", value=line_list(workspaces, workspace_line, empty="No workspaces."), inline=False)
        e.add_field(name="Users", value=str(len(users)), inline=True)

        await ctx.send(embed=e)
