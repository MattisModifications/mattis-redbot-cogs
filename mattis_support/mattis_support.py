from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_staff,
    line_list,
    ticket_line,
    q,
    simple_counts_embed,
    require_support,
    require_general_support,
)


class MattisSupport(commands.Cog):
    """Mattis support operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="msupport", invoke_without_command=True)
    async def msupport(self, ctx):
        if not await require_general_support(ctx):
            return
        await self.open_tickets(ctx)

    @msupport.command(name="stats")
    async def stats(self, ctx):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/support/stats")
        await ctx.send(embed=simple_counts_embed("Support Stats", payload if status == 200 else {}))

    @msupport.command(name="open")
    async def open_tickets(self, ctx):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/support/open")
        await ctx.send(embed=embed("Open Support Tickets", line_list(payload.get("tickets", []), ticket_line, empty="No open tickets.")))

    @msupport.command(name="all")
    async def all_tickets(self, ctx):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/support/all")
        await ctx.send(embed=embed("Recent Support Tickets", line_list(payload.get("tickets", []), ticket_line, empty="No tickets.")))

    @msupport.command(name="critical")
    async def critical(self, ctx):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/support/critical")
        await ctx.send(embed=embed("Critical Support Tickets", line_list(payload.get("tickets", []), ticket_line, empty="No critical tickets.")))

    @msupport.command(name="unassigned")
    async def unassigned(self, ctx):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/support/unassigned")
        await ctx.send(embed=embed("Unassigned Support Tickets", line_list(payload.get("tickets", []), ticket_line, empty="No unassigned tickets.")))

    @msupport.command(name="search")
    async def search(self, ctx, *, query: str):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/support/search?q={q(query)}")
        await ctx.send(embed=embed(f"Support Search: {query}", line_list(payload.get("tickets", []), ticket_line, empty="No matching tickets.")))

    @msupport.command(name="ticket")
    async def ticket(self, ctx, ticket_id: str):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/support/ticket/{q(ticket_id)}")
        ticket = payload.get("ticket", {}) if isinstance(payload, dict) else {}

        await ctx.send(embed=embed(f"Support Ticket #{ticket.get('ticketNumber', ticket_id)}", ticket_line(ticket) if ticket else f"HTTP {status}"))

    @msupport.command(name="categories")
    async def categories(self, ctx):
        if not await require_general_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/support/categories")
        cats = payload.get("categories", []) if isinstance(payload, dict) else []

        await ctx.send(embed=embed(
            "Support Categories",
            line_list(
                cats,
                lambda c: f"**{c.get('name')}** (`{c.get('key')}`) · {'enabled' if c.get('enabled') else 'disabled'}",
                empty="No categories.",
            ),
        ))
