from redbot.core import commands

from .shared_mattis import embed, request_json, require_staff, simple_counts_embed


class MattisApplications(commands.Cog):
    """Application/recruitment monitoring."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mapps", invoke_without_command=True)
    async def mapps(self, ctx):
        if not await require_staff(ctx):
            return
        await self.summary(ctx)

    @mapps.command(name="summary", aliases=["forms", "stats"])
    async def summary(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/applications/summary")
        await ctx.send(embed=simple_counts_embed("Applications Summary", payload if status == 200 else {}))

    @mapps.command(name="recent", aliases=["pending", "submissions"])
    async def recent(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/applications/recent")
        await ctx.send(embed=embed("Recent Applications", f"Submissions: {payload.get('count', 0)}"))
