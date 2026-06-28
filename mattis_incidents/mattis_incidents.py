from redbot.core import commands

from .shared_mattis import request_json, require_staff, simple_counts_embed


class MattisIncidents(commands.Cog):
    """Incident and monitoring summary."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mincident", invoke_without_command=True)
    async def mincident(self, ctx):
        if not await require_staff(ctx):
            return
        await self.summary(ctx)

    @mincident.command(name="summary")
    @mincident.command(name="current")
    @mincident.command(name="recent")
    @mincident.command(name="errors")
    @mincident.command(name="uptime")
    @mincident.command(name="latency")
    @mincident.command(name="ssl")
    @mincident.command(name="domains")
    async def summary(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/incidents/summary")
        await ctx.send(embed=simple_counts_embed("Incident Summary", payload if status == 200 else {}))
