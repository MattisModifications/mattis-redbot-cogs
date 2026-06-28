from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_staff,
    require_admin,
    simple_counts_embed,
    line_list,
    staff_line,
    q,
)


class MattisStaff(commands.Cog):
    """Staff/workforce monitoring."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mstaff", invoke_without_command=True)
    async def mstaff(self, ctx):
        if not await require_staff(ctx):
            return
        await self.summary(ctx)

    @mstaff.command(name="summary")
    async def summary(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/staff/summary")
        await ctx.send(embed=simple_counts_embed("Staff Summary", payload if status == 200 else {}))

    @mstaff.command(name="lookup")
    async def lookup(self, ctx, *, query: str):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/staff/lookup?q={q(query)}")
        await ctx.send(embed=embed(f"Staff Lookup: {query}", line_list(payload.get("staff", []), staff_line, empty="No staff found.")))

    @mstaff.command(name="permissions")
    async def permissions(self, ctx, *, query: str = ""):
        if not await require_admin(ctx):
            return

        await self.lookup(ctx, query=query or " ")
