from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_staff,
    require_admin,
    line_list,
    workspace_line,
    user_line,
    q,
    simple_counts_embed,
)


class MattisCRM(commands.Cog):
    """Mattis CRM commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mcrm", invoke_without_command=True)
    async def mcrm(self, ctx):
        if not await require_staff(ctx):
            return
        await ctx.send_help(ctx.command)

    @mcrm.command(name="lookup")
    async def lookup(self, ctx, *, query: str):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/crm/search?q={q(query)}")

        e = embed(f"CRM Lookup: {query}")
        e.add_field(name="Workspaces", value=line_list(payload.get("workspaces", []), workspace_line, empty="No workspaces found."), inline=False)
        e.add_field(name="Users", value=line_list(payload.get("users", []), user_line, empty="No users found."), inline=False)

        await ctx.send(embed=e)

    @mcrm.command(name="health")
    async def health(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/crm/health")
        await ctx.send(embed=simple_counts_embed("CRM Health", payload if status == 200 else {}))

    @mcrm.command(name="onboarding")
    async def onboarding(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/crm/onboarding")
        await ctx.send(embed=embed("Onboarding Workspaces", line_list(payload.get("workspaces", []), workspace_line, empty="No onboarding workspaces.")))

    @mcrm.command(name="trials")
    async def trials(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/crm/trials")
        await ctx.send(embed=embed("Trial Customers", line_list(payload.get("workspaces", []), workspace_line, empty="No trial customers.")))

    @mcrm.command(name="atrisk")
    async def atrisk(self, ctx):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/crm/atrisk")
        await ctx.send(embed=embed("At-Risk Customers", line_list(payload.get("workspaces", []), workspace_line, empty="No high-risk customers.")))

    @mcrm.command(name="suspended")
    async def suspended(self, ctx):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/crm/suspended")
        await ctx.send(embed=embed("Suspended Customers", line_list(payload.get("workspaces", []), workspace_line, empty="No suspended workspaces.")))

    @mcrm.command(name="frozen")
    async def frozen(self, ctx):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/crm/frozen")
        await ctx.send(embed=embed("Frozen Customers", line_list(payload.get("workspaces", []), workspace_line, empty="No frozen workspaces.")))
