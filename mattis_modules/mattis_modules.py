from redbot.core import commands

from .shared_mattis import request_json, require_staff, require_admin, simple_counts_embed


class MattisModules(commands.Cog):
    """Modules/product operations monitoring."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mmodules", invoke_without_command=True)
    async def mmodules(self, ctx):
        if not await require_staff(ctx):
            return
        await self.summary(ctx)

    @mmodules.command(name="summary")
    @mmodules.command(name="catalog")
    @mmodules.command(name="usage")
    async def summary(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/modules/summary")
        await ctx.send(embed=simple_counts_embed("Modules Summary", payload if status == 200 else {}))

    @mmodules.command(name="flags")
    @mmodules.command(name="developer")
    async def admin(self, ctx):
        if not await require_admin(ctx):
            return

        await self.summary(ctx)
