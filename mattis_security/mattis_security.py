from redbot.core import commands

from .shared_mattis import (
    embed,
    request_json,
    require_admin,
    require_security,
    simple_counts_embed,
    line_list,
    user_line,
    q,
    require_security_support,
)


class MattisSecurity(commands.Cog):
    """Mattis security operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="msecurity", invoke_without_command=True)
    async def msecurity(self, ctx):
        if not await require_security_support(ctx):
            return
        await self.risks(ctx)

    @msecurity.command(name="risks")
    async def risks(self, ctx):
        if not await require_security_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/security/risks")
        e = simple_counts_embed("Security Risks", payload.get("signals", {}) if isinstance(payload, dict) else {})

        if isinstance(payload, dict):
            e.description = f"Risk level: **{payload.get('riskLevel', 'unknown')}**"

        await ctx.send(embed=e)

    @msecurity.command(name="sessions")
    async def sessions(self, ctx):
        if not await require_security_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/security/sessions")
        await ctx.send(embed=embed("Auth Sessions", f"Count: {payload.get('count', 0)}"))

    @msecurity.command(name="admins")
    async def admins(self, ctx):
        if not await require_security_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/security/admins")
        await ctx.send(embed=embed("Platform Admin Users", line_list(payload.get("users", []), user_line, empty="No admin users found.")))

    @msecurity.command(name="suspicious")
    async def suspicious(self, ctx):
        if not await require_security_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", "/bot/security/suspicious")
        await ctx.send(embed=embed("Suspicious Activity", f"Events: {payload.get('count', 0)}"))

    @msecurity.command(name="user")
    async def user(self, ctx, *, query: str):
        if not await require_security_support(ctx):
            return

        status, payload = await request_json(self.bot, "GET", f"/bot/crm/search?q={q(query)}")
        await ctx.send(embed=embed(f"Security User Lookup: {query}", line_list(payload.get("users", []), user_line, empty="No users found.")))

    @msecurity.command(name="failedlogins", aliases=["permissions", "tokens", "config"])
    async def placeholder(self, ctx):
        if not await require_security_support(ctx):
            return
        await self.risks(ctx)
