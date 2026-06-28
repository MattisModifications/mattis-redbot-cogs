from __future__ import annotations

import discord
from redbot.core import commands

from .shared_mattis import (
    embed,
    ok_embed,
    get_core_config,
    request_json,
    fmt_payload,
    require_admin,
)


class MattisCore(commands.Cog):
    """Core configuration for Mattis CMS | Systems."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="mcore", invoke_without_command=True)
    async def mcore(self, ctx):
        """Configure the Mattis API bridge."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        api_url = await cfg.api_url()
        channels = await cfg.guild(ctx.guild).systems_channels() if ctx.guild else {}
        staff_roles = await cfg.guild(ctx.guild).staff_roles() if ctx.guild else []
        admin_roles = await cfg.guild(ctx.guild).admin_roles() if ctx.guild else []

        e = embed("Mattis Core Config")
        e.add_field(name="API URL", value=api_url or "Not set", inline=False)
        e.add_field(name="API token", value="Set" if await cfg.api_token() else "Not set", inline=True)
        e.add_field(name="Systems channels", value=str(len(channels or {})), inline=True)
        e.add_field(name="Staff roles", value=str(len(staff_roles or [])), inline=True)
        e.add_field(name="Admin roles", value=str(len(admin_roles or [])), inline=True)
        await ctx.send(embed=e)

    @mcore.command(name="apiurl")
    @commands.is_owner()
    async def apiurl(self, ctx, url: str):
        """Set the Mattis API URL."""
        cfg = await get_core_config(self.bot)
        await cfg.api_url.set(url.rstrip("/"))
        await ctx.send(embed=ok_embed("API URL saved", url.rstrip("/")))

    @mcore.command(name="token")
    @commands.is_owner()
    async def token(self, ctx, *, token: str):
        """Set the private Mattis bot API token."""
        cfg = await get_core_config(self.bot)
        await cfg.api_token.set(token.strip())

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        await ctx.send(
            embed=ok_embed("Token saved", "Private token saved. Delete any visible token messages."),
            delete_after=10,
        )

    @mcore.command(name="cleartoken")
    @commands.is_owner()
    async def cleartoken(self, ctx):
        """Clear the private Mattis bot API token."""
        cfg = await get_core_config(self.bot)
        await cfg.api_token.set("")
        await ctx.send(embed=ok_embed("Token cleared"))

    @mcore.command(name="systemchannel")
    async def systemchannel(self, ctx, key: str, channel: discord.TextChannel):
        """Map a Mattis Systems channel, e.g. support #cms-support."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()
        channels[key.lower()] = channel.id
        await cfg.guild(ctx.guild).systems_channels.set(channels)

        await ctx.send(embed=ok_embed("Systems channel mapped", f"`{key.lower()}` → {channel.mention}"))

    @mcore.command(name="channels")
    async def channels(self, ctx):
        """Show mapped Mattis Systems channels."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        channels = await cfg.guild(ctx.guild).systems_channels()

        if not channels:
            await ctx.send(embed=embed("Systems channels", "No systems channels mapped yet."))
            return

        lines = []
        for key, cid in channels.items():
            ch = ctx.guild.get_channel(cid) if ctx.guild else None
            lines.append(f"`{key}` → {ch.mention if ch else cid}")

        await ctx.send(embed=embed("Systems channels", "\n".join(lines)))

    @mcore.command(name="staffrole")
    async def staffrole(self, ctx, role: discord.Role):
        """Add a Mattis Systems staff role."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        roles = await cfg.guild(ctx.guild).staff_roles()

        if role.id not in roles:
            roles.append(role.id)

        await cfg.guild(ctx.guild).staff_roles.set(roles)
        await ctx.send(embed=ok_embed("Staff role added", role.mention))

    @mcore.command(name="adminrole")
    async def adminrole(self, ctx, role: discord.Role):
        """Add a Mattis Systems admin role."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        roles = await cfg.guild(ctx.guild).admin_roles()

        if role.id not in roles:
            roles.append(role.id)

        await cfg.guild(ctx.guild).admin_roles.set(roles)
        await ctx.send(embed=ok_embed("Admin role added", role.mention))

    @mcore.command(name="permissions")
    async def permissions(self, ctx):
        """Show Mattis Systems permission gates."""
        if not await require_admin(ctx):
            return

        cfg = await get_core_config(self.bot)
        staff_roles = await cfg.guild(ctx.guild).staff_roles()
        admin_roles = await cfg.guild(ctx.guild).admin_roles()

        def fmt_roles(ids):
            parts = []
            for rid in ids:
                role = ctx.guild.get_role(rid)
                parts.append(role.mention if role else str(rid))
            return "\n".join(parts) or "None configured"

        e = embed("Mattis Systems Permissions")
        e.add_field(name="Staff roles", value=fmt_roles(staff_roles), inline=False)
        e.add_field(name="Admin roles", value=fmt_roles(admin_roles), inline=False)
        e.add_field(name="Fallbacks", value="Administrator = admin\nManage Server = staff", inline=False)

        await ctx.send(embed=e)

    @mcore.command(name="diagnostics")
    async def diagnostics(self, ctx):
        """Run Mattis Systems diagnostics."""
        if not await require_admin(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/status")

        e = embed("Mattis Systems Diagnostics")
        e.add_field(name="Bot latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        e.add_field(name="API", value=f"HTTP {status}", inline=True)
        e.add_field(name="Response", value=fmt_payload(payload)[:1024], inline=False)

        await ctx.send(embed=e)

    @mcore.command(name="apiget")
    @commands.is_owner()
    async def apiget(self, ctx, path: str):
        """Owner-only raw API GET test."""
        status, payload = await request_json(self.bot, "GET", path)
        await ctx.send(embed=embed(f"GET {path} → {status}", fmt_payload(payload)))
