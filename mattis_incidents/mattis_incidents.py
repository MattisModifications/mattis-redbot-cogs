from redbot.core import commands

from .shared_mattis import (
    request_json,
    require_staff,
    simple_counts_embed,
    require_development,
    require_incident_response,
)


class MattisIncidents(commands.Cog):
    """Incident and monitoring summary."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mincident", invoke_without_command=True)
    async def mincident(self, ctx):
        if not await require_incident_response(ctx):
            return
        await self.summary(ctx)

    @mincident.command(name="summary", aliases=["current", "recent", "errors", "uptime", "latency", "ssl", "domains"])
    async def summary(self, ctx):
        if not await require_incident_response(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/incidents/summary")
        await ctx.send(embed=simple_counts_embed("Incident Summary", payload if status == 200 else {}))
