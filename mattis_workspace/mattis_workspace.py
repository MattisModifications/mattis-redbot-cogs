from redbot.core import commands

from .shared_mattis import embed, request_json, fmt_payload


class MattisWorkspace(commands.Cog):
    """Mattis workspace operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mworkspace", aliases=["mws"])
    async def mworkspace(self, ctx):
        """Mattis workspace commands."""
        if ctx.invoked_subcommand is None:
            status, payload = await request_json(self.bot, "GET", "/bot/workspaces")
            e = embed("Workspaces", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
            await ctx.send(embed=e)

    @mworkspace.command(name="status")
    async def status(self, ctx):
        """Show workspace status."""
        status, payload = await request_json(self.bot, "GET", "/bot/workspaces")
        e = embed("Workspace Status", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mworkspace.command(name="health")
    async def health(self, ctx):
        """Show workspace health."""
        status, payload = await request_json(self.bot, "GET", "/bot/command/overview")
        e = embed("Workspace Health", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)

    @mworkspace.command(name="config")
    @commands.has_permissions(manage_guild=True)
    async def config(self, ctx):
        """Show Discord workspace integration summary."""
        status, payload = await request_json(self.bot, "GET", "/bot/discord/summary")
        e = embed("Workspace Discord Config", fmt_payload(payload) if status == 200 else f"API error {status}: {payload}")
        await ctx.send(embed=e)
