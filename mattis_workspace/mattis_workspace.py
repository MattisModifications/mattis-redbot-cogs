from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_staff,
    require_admin,
    line_list,
    workspace_line,
    q,
    add_fields,
)


class MattisWorkspace(commands.Cog):
    """Mattis workspace monitoring commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mworkspace", aliases=["mws"], invoke_without_command=True)
    async def mworkspace(self, ctx):
        if not await require_staff(ctx):
            return
        await self.list(ctx)

    @mworkspace.command(name="list")
    async def list(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/workspaces")
        await ctx.send(embed=embed("Workspaces", line_list(payload.get("workspaces", []), workspace_line, empty="No workspaces found.", limit=15)))

    @mworkspace.command(name="status")
    async def status(self, ctx, *, workspace: str = ""):
        if not await require_staff(ctx):
            return

        path = f"/bot/workspaces/{q(workspace)}" if workspace else "/bot/workspaces"
        status, payload = await request_json(self.bot, "GET", path)

        if workspace and status == 200:
            w = payload.get("workspace", {})
            e = embed(f"Workspace: {w.get('name', workspace)}")
            add_fields(e, w, inline=True, max_fields=18)
            await ctx.send(embed=e)
        else:
            await self.list(ctx)

    @mworkspace.command(name="config", aliases=["health"])
    async def config(self, ctx, *, workspace: str = ""):
        if not await require_staff(ctx):
            return

        if not workspace:
            await self.list(ctx)
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/workspaces/{q(workspace)}")
        w = payload.get("workspace", {}) if isinstance(payload, dict) else {}

        e = embed(f"Workspace Config: {w.get('name', workspace)}")
        add_fields(e, w, inline=True, max_fields=18)

        await ctx.send(embed=e)

    @mworkspace.command(name="integrations", aliases=["modules"])
    async def integrations(self, ctx, *, workspace: str):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/workspaces/{q(workspace)}/integrations")

        e = embed(f"Workspace Integrations: {workspace}")

        if status == 200:
            e.add_field(name="Integrations", value=str(len(payload.get("integrations", []))), inline=True)
            e.add_field(name="Modules", value=str(len(payload.get("modules", []))), inline=True)
            e.add_field(name="Discord routes", value=str(len(payload.get("discord", {}).get("routes", []))), inline=True)
            e.add_field(name="Discord mappings", value=str(len(payload.get("discord", {}).get("mappings", []))), inline=True)
            e.add_field(name="Roblox keys", value=str(len(payload.get("roblox", {}).get("telemetryKeys", []))), inline=True)
            e.add_field(name="Roblox policies", value=str(len(payload.get("roblox", {}).get("rankPolicies", []))), inline=True)
        else:
            e.description = f"HTTP {status}"

        await ctx.send(embed=e)

    @mworkspace.command(name="audit")
    async def audit(self, ctx, *, workspace: str):
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/audit/search?q={q(workspace)}")
        await ctx.send(embed=embed(f"Workspace Audit: {workspace}", f"Events: {len(payload.get('events', []))}"))
