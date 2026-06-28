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
)


class MattisDiscord(commands.Cog):
    """Global Discord integration monitoring for Mattis CMS | Systems."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mdiscord", invoke_without_command=True)
    async def mdiscord(self, ctx):
        if not await require_staff(ctx):
            return
        await self.summary(ctx)

    @mdiscord.command(name="summary")
    async def summary(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/discord/summary")
        await ctx.send(embed=simple_counts_embed("Discord Integration Summary", payload if status == 200 else {}))

    @mdiscord.command(name="broken")
    async def broken(self, ctx):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/discord/broken")
        await ctx.send(embed=embed("Discord Missing Config", line_list(payload.get("missingDiscordGuild", []), workspace_line, empty="No missing Discord guild IDs.")))

    @mdiscord.command(name="workspace", aliases=["routes", "mappings", "syncstatus"])
    async def workspace(self, ctx, *, workspace: str):
        if not await require_staff(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/discord/workspace/{q(workspace)}")

        e = embed(f"Discord Workspace: {workspace}")

        if status == 200:
            e.add_field(name="Routes", value=str(len(payload.get("routes", []))), inline=True)
            e.add_field(name="Mappings", value=str(len(payload.get("mappings", []))), inline=True)
            e.add_field(name="Actions", value=str(len(payload.get("actions", []))), inline=True)
        else:
            e.description = f"HTTP {status}"

        await ctx.send(embed=e)

    @mdiscord.command(name="test")
    async def test(self, ctx, *, workspace: str):
        if not await require_admin(ctx):
            return

        await ctx.send(embed=embed("Discord Test", "Test-message actions will be enabled in the controlled admin-action phase."))
