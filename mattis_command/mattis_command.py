from redbot.core import commands

from .shared_mattis import embed, request_json, fmt_payload


class MattisCommand(commands.Cog):
    """Mattis Command operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mcommand")
    async def mcommand(self, ctx):
        """Mattis Command centre."""
        if ctx.invoked_subcommand is None:
            status, payload = await request_json(self.bot, "GET", "/bot/command/overview")
            e = embed("Mattis Command", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
            await ctx.send(embed=e)

    @mcommand.command(name="links")
    async def links(self, ctx):
        """Show Mattis Command links."""
        status, payload = await request_json(self.bot, "GET", "/bot/command/links")
        e = embed("Mattis Command Links", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mcommand.command(name="overview")
    async def overview(self, ctx):
        """Show Mattis Command overview."""
        status, payload = await request_json(self.bot, "GET", "/bot/command/overview")
        e = embed("Mattis Command Overview", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mcommand.command(name="customer")
    @commands.has_permissions(manage_guild=True)
    async def customer(self, ctx, *, query: str):
        """Search CRM users/workspaces."""
        status, payload = await request_json(self.bot, "GET", f"/bot/crm/search?q={query}")
        e = embed("Mattis Customer Search", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)
