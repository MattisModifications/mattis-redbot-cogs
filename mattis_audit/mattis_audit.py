from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_admin,
    line_list,
    audit_line,
    q,
)


class MattisAudit(commands.Cog):
    """Mattis audit operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="maudit", invoke_without_command=True)
    async def maudit(self, ctx):
        if not await require_admin(ctx):
            return
        await self.recent(ctx)

    @maudit.command(name="recent")
    async def recent(self, ctx):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/audit/recent")

        e = embed("Recent Audit Events")
        e.add_field(name="Workspace events", value=line_list(payload.get("events", []), audit_line, empty="No workspace events."), inline=False)
        e.add_field(name="Platform events", value=line_list(payload.get("platformEvents", []), audit_line, empty="No platform events."), inline=False)

        await ctx.send(embed=e)

    @maudit.command(name="highrisk")
    async def highrisk(self, ctx):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/audit/highrisk")
        await ctx.send(embed=embed("High-Risk Audit Events", line_list(payload.get("events", []), audit_line, empty="No high-risk events.")))

    @maudit.command(name="search")
    async def search(self, ctx, *, query: str):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/audit/search?q={q(query)}")
        await ctx.send(embed=embed(f"Audit Search: {query}", line_list(payload.get("events", []), audit_line, empty="No matching events.")))

    @maudit.command(name="billing", aliases=["support", "security", "roblox", "discord"])
    async def filtered(self, ctx):
        if not await require_admin(ctx):
            return
        await self.search(ctx, query=ctx.invoked_with)
