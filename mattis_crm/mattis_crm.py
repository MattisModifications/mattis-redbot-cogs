from redbot.core import commands

from .shared_mattis import embed, request_json, fmt_payload


class MattisCRM(commands.Cog):
    """Mattis CRM operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mcrm")
    @commands.has_permissions(manage_guild=True)
    async def mcrm(self, ctx):
        """Mattis CRM commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @mcrm.command(name="lookup")
    async def lookup(self, ctx, *, query: str):
        """Search CRM users/workspaces."""
        status, payload = await request_json(self.bot, "GET", f"/bot/crm/search?q={query}")
        e = embed("CRM Search", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mcrm.command(name="health")
    async def health(self, ctx):
        """Show CRM health via Command overview."""
        status, payload = await request_json(self.bot, "GET", "/bot/command/overview")
        e = embed("CRM Health", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mcrm.command(name="onboarding")
    async def onboarding(self, ctx):
        """Show workspace onboarding snapshot."""
        status, payload = await request_json(self.bot, "GET", "/bot/workspaces")
        e = embed("Workspace Onboarding", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)
