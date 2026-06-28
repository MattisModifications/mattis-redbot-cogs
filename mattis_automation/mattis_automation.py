from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_staff,
    require_admin,
    simple_counts_embed,
    require_development,
)


class MattisAutomation(commands.Cog):
    """Automation/workflow monitoring."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mautomation", invoke_without_command=True)
    async def mautomation(self, ctx):
        if not await require_staff(ctx):
            return
        await self.summary(ctx)

    @mautomation.command(name="summary", aliases=["workflows", "workers", "datatransfers"])
    async def summary(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/automation/summary")
        await ctx.send(embed=simple_counts_embed("Automation Summary", payload if status == 200 else {}))

    @mautomation.command(name="failed", aliases=["recent"])
    async def failed(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/automation/failed")

        e = embed("Failed Automation")

        if status == 200:
            e.add_field(name="Workflow runs", value=str(len(payload.get("workflowRuns", []))), inline=True)
            e.add_field(name="Automation runs", value=str(len(payload.get("automationRuns", []))), inline=True)
            e.add_field(name="Data transfers", value=str(len(payload.get("dataTransferJobs", []))), inline=True)

        await ctx.send(embed=e)

    @mautomation.command(name="run")
    async def run(self, ctx, *, workflow: str):
        if not await require_development(ctx):
            return

        await ctx.send(embed=embed("Run Workflow", "Workflow execution will be enabled in the controlled admin-action phase."))
