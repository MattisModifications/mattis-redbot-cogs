from redbot.core import commands

from .shared_mattis import embed, request_json, fmt_payload


class MattisAudit(commands.Cog):
    """Mattis audit operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="maudit")
    @commands.has_permissions(manage_guild=True)
    async def maudit(self, ctx):
        """Mattis audit commands."""
        if ctx.invoked_subcommand is None:
            status, payload = await request_json(self.bot, "GET", "/bot/audit/recent")
            e = embed("Recent Audit Events", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
            await ctx.send(embed=e)

    @maudit.command(name="recent")
    async def recent(self, ctx):
        """Show recent audit events."""
        status, payload = await request_json(self.bot, "GET", "/bot/audit/recent")
        e = embed("Recent Audit Events", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @maudit.command(name="highrisk")
    async def highrisk(self, ctx):
        """Show security risks."""
        status, payload = await request_json(self.bot, "GET", "/bot/security/risks")
        e = embed("High Risk Audit Signals", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @maudit.command(name="search")
    async def search(self, ctx, *, query: str):
        """Search placeholder using recent audit feed."""
        status, payload = await request_json(self.bot, "GET", "/bot/audit/recent")
        e = embed(f"Audit Search: {query}", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)
