from __future__ import annotations

import discord
from redbot.core import commands, Config
from .shared_mattis import embed, request_json, fmt_payload


class MattisCore(commands.Cog):
    """Core config for Mattis CMS cogs."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(bot, identifier=912406121210, force_registration=True)
        self.config.register_global(api_url="", api_token="", office_channels={})

    @commands.group(name="mcore")
    @commands.is_owner()
    async def mcore(self, ctx):
        """Configure the Mattis API bridge."""
        if ctx.invoked_subcommand is None:
            api_url = await self.config.api_url()
            channels = await self.config.office_channels()
            e = embed("Mattis Core Config")
            e.add_field(name="API URL", value=api_url or "Not set", inline=False)
            e.add_field(name="API token", value="Set" if await self.config.api_token() else "Not set", inline=True)
            e.add_field(name="Office channels", value=str(len(channels)), inline=True)
            await ctx.send(embed=e)

    @mcore.command(name="apiurl")
    async def apiurl(self, ctx, url: str):
        """Set the Mattis API URL."""
        await self.config.api_url.set(url.rstrip("/"))
        await ctx.tick()

    @mcore.command(name="token")
    async def token(self, ctx, *, token: str):
        """Set the private Mattis bot API token."""
        await self.config.api_token.set(token.strip())
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ctx.send("Mattis API token saved. I deleted your command message if I had permission.", delete_after=10)

    @mcore.command(name="cleartoken")
    async def cleartoken(self, ctx):
        """Clear the private Mattis bot API token."""
        await self.config.api_token.set("")
        await ctx.tick()

    @mcore.command(name="officechannel")
    async def officechannel(self, ctx, key: str, channel: discord.TextChannel):
        """Map an office channel, e.g. support #cms-support."""
        channels = await self.config.office_channels()
        channels[key.lower()] = channel.id
        await self.config.office_channels.set(channels)
        await ctx.send(f"Mapped `{key.lower()}` to {channel.mention}.")

    @mcore.command(name="channels")
    async def channels(self, ctx):
        channels = await self.config.office_channels()
        if not channels:
            await ctx.send("No office channels mapped yet.")
            return
        lines = []
        for key, cid in channels.items():
            ch = ctx.guild.get_channel(cid) if ctx.guild else None
            lines.append(f"`{key}` → {ch.mention if ch else cid}")
        await ctx.send("\n".join(lines))

    @mcore.command(name="apiget")
    async def apiget(self, ctx, path: str):
        """Owner test: GET an API path."""
        status, payload = await request_json(self.bot, "GET", path)
        await ctx.send(embed=embed(f"GET {path} → {status}", fmt_payload(payload)))
