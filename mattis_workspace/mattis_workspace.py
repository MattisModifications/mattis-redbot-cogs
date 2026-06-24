from __future__ import annotations

from redbot.core import commands
from .shared_mattis import embed, request_json, fmt_payload


class MattisWorkspace(commands.Cog):
    """Customer workspace integration commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mworkspace", aliases=["mws"])
    async def mworkspace(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=embed("Mattis Workspace", "Commands: `mws status`, `mws config`, `mws health`"))

    @mworkspace.command(name="status")
    async def status(self, ctx):
        status, payload = await request_json(self.bot, "GET", f"/bot/workspace/discord/guilds/{ctx.guild.id}/status")
        await ctx.send(embed=embed(f"Workspace status → {status}", fmt_payload(payload)))

    @mworkspace.command(name="health")
    async def health(self, ctx):
        status, payload = await request_json(self.bot, "GET", f"/bot/workspace/discord/guilds/{ctx.guild.id}/health")
        await ctx.send(embed=embed(f"Workspace health → {status}", fmt_payload(payload)))

    @mworkspace.command(name="config")
    @commands.has_permissions(manage_guild=True)
    async def config(self, ctx):
        status, payload = await request_json(self.bot, "GET", f"/bot/workspace/discord/guilds/{ctx.guild.id}/config")
        await ctx.send(embed=embed(f"Workspace config → {status}", fmt_payload(payload)))
