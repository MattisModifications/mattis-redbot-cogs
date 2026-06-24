from redbot.core import commands

from .shared_mattis import embed, request_json, fmt_payload


class MattisSupport(commands.Cog):
    """Mattis support operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="msupport")
    async def msupport(self, ctx):
        """Mattis support commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @msupport.command(name="open")
    @commands.has_permissions(manage_guild=True)
    async def open_tickets(self, ctx):
        """Show open support tickets."""
        status, payload = await request_json(self.bot, "GET", "/bot/support/open")
        e = embed("Open Support Tickets", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @msupport.command(name="ticket")
    @commands.has_permissions(manage_guild=True)
    async def ticket(self, ctx, ticket_id: str):
        """Ticket lookup placeholder."""
        status, payload = await request_json(self.bot, "GET", "/bot/support/open")
        e = embed(f"Support Ticket {ticket_id}", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @msupport.command(name="search")
    @commands.has_permissions(manage_guild=True)
    async def search(self, ctx, *, query: str):
        """Search support placeholder."""
        status, payload = await request_json(self.bot, "GET", "/bot/support/open")
        e = embed("Support Search", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)
