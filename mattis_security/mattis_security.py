from redbot.core import commands

from .shared_mattis import embed, request_json, fmt_payload


class MattisSecurity(commands.Cog):
    """Mattis security operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="msecurity")
    @commands.has_permissions(manage_guild=True)
    async def msecurity(self, ctx):
        """Mattis security commands."""
        if ctx.invoked_subcommand is None:
            status, payload = await request_json(self.bot, "GET", "/bot/security/risks")
            e = embed("Security Risks", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
            await ctx.send(embed=e)

    @msecurity.command(name="risks")
    async def risks(self, ctx):
        """Show security risk signals."""
        status, payload = await request_json(self.bot, "GET", "/bot/security/risks")
        e = embed("Security Risks", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @msecurity.command(name="user")
    async def user(self, ctx, *, query: str):
        """Search a user via CRM."""
        status, payload = await request_json(self.bot, "GET", f"/bot/crm/search?q={query}")
        e = embed("Security User Search", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @msecurity.command(name="failedlogins")
    async def failedlogins(self, ctx):
        """Show security risk signals."""
        status, payload = await request_json(self.bot, "GET", "/bot/security/risks")
        e = embed("Failed Login Signals", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)
