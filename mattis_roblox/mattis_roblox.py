from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_staff,
    require_admin,
    simple_counts_embed,
    line_list,
    workspace_line,
    q,
    require_development,
)


class MattisRoblox(commands.Cog):
    """Global Roblox integration monitoring for Mattis CMS | Systems."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mroblox", invoke_without_command=True)
    async def mroblox(self, ctx):
        if not await require_staff(ctx):
            return
        await self.summary(ctx)

    @mroblox.command(name="summary")
    async def summary(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/roblox/summary")
        await ctx.send(embed=simple_counts_embed("Roblox Integration Summary", payload if status == 200 else {}))

    @mroblox.command(name="broken")
    async def broken(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/roblox/broken")
        await ctx.send(embed=embed("Roblox Missing Config", line_list(payload.get("missingRobloxGroup", []), workspace_line, empty="No missing Roblox group IDs.")))

    @mroblox.command(name="workspace", aliases=["group", "ranks", "policies", "telemetry", "keys", "syncstatus"])
    async def workspace(self, ctx, *, workspace: str):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/roblox/workspace/{q(workspace)}")

        e = embed(f"Roblox Workspace: {workspace}")

        if status == 200:
            e.add_field(name="Telemetry keys", value=str(len(payload.get("telemetryKeys", []))), inline=True)
            e.add_field(name="Policies", value=str(len(payload.get("policies", []))), inline=True)
            e.add_field(name="Rank requests", value=str(len(payload.get("requests", []))), inline=True)
            e.add_field(name="Mappings", value=str(len(payload.get("mappings", []))), inline=True)
            e.add_field(name="Drift", value=str(len(payload.get("drift", []))), inline=True)
        else:
            e.description = f"HTTP {status}"

        await ctx.send(embed=e)

    @mroblox.command(name="forcesync")
    async def forcesync(self, ctx, *, workspace: str):
        if not await require_development(ctx):
            return

        await ctx.send(embed=embed("Roblox Force Sync", "Force-sync actions will be enabled in the controlled admin-action phase."))
