from redbot.core import commands

from .shared_mattis import (
    request_json,
    require_staff,
    require_admin,
    require_development,
    simple_counts_embed,
    require_backend_access,
)


class MattisModules(commands.Cog):
    """Modules/product operations monitoring."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mmodules", invoke_without_command=True)
    async def mmodules(self, ctx):
        if not await require_backend_access(ctx):
            return
        await self.summary(ctx)

    @mmodules.command(name="summary", aliases=["catalog", "usage"])
    async def summary(self, ctx):
        if not await require_backend_access(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/modules/summary")
        await ctx.send(embed=simple_counts_embed("Modules Summary", payload if status == 200 else {}))

    @mmodules.command(name="flags", aliases=["developer"])
    async def admin(self, ctx):
        if not await require_backend_access(ctx):
            return

        await self.summary(ctx)
