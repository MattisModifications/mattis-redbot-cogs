from __future__ import annotations

from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload


class MattisSupport(commands.Cog):
    """Internal support ticket bridge."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="msupport")
    async def msupport(self, ctx):
        """Mattis support commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Support", "Commands: `msupport open`, `msupport ticket <id>`, `msupport search <query>`"))

    @msupport.command(name="open")
    @commands.has_permissions(manage_guild=True)
    async def open_tickets(self, ctx):
        status, payload = await request_json(self.bot, "GET", "/bot/office/support/tickets?status=open")
        await ctx.send(embed=embed(f"Open support tickets → {status}", fmt_payload(payload)))

    @msupport.command(name="ticket")
    @commands.has_permissions(manage_guild=True)
    async def ticket(self, ctx, ticket_id: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/support/tickets/{ticket_id}")
        await ctx.send(embed=embed(f"Support ticket {ticket_id} → {status}", fmt_payload(payload)))

    @msupport.command(name="search")
    @commands.has_permissions(manage_guild=True)
    async def search(self, ctx, *, query: str):
        status, payload = await request_json(self.bot, "GET", f"/bot/office/support/tickets/search?q={query}")
        await ctx.send(embed=embed(f"Support search → {status}", fmt_payload(payload)))
