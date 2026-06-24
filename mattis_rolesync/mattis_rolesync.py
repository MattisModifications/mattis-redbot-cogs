from __future__ import annotations

from redbot.core import commands
from .shared_mattis import embed, request_json, fmt_payload


class MattisRoleSync(commands.Cog):
    """Workspace role sync commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mrolesync")
    async def mrolesync(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Role Sync", "Commands: `mrolesync me`, `mrolesync preview`, `mrolesync all`"))

    @mrolesync.command(name="me")
    async def me(self, ctx):
        body = {"guildId": str(ctx.guild.id), "discordUserId": str(ctx.author.id), "apply": True}
        status, payload = await request_json(self.bot, "POST", "/bot/workspace/roles/sync-user", json_body=body)
        await ctx.send(embed=embed(f"Role sync me → {status}", fmt_payload(payload)))

    @mrolesync.command(name="preview")
    async def preview(self, ctx):
        body = {"guildId": str(ctx.guild.id), "discordUserId": str(ctx.author.id), "apply": False}
        status, payload = await request_json(self.bot, "POST", "/bot/workspace/roles/sync-user", json_body=body)
        await ctx.send(embed=embed(f"Role sync preview → {status}", fmt_payload(payload)))

    @mrolesync.command(name="all")
    @commands.has_permissions(manage_roles=True)
    async def all(self, ctx):
        body = {"guildId": str(ctx.guild.id), "apply": True}
        status, payload = await request_json(self.bot, "POST", "/bot/workspace/roles/sync-all", json_body=body)
        await ctx.send(embed=embed(f"Role sync all → {status}", fmt_payload(payload)))
