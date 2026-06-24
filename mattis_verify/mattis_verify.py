from __future__ import annotations

from redbot.core import commands
from shared_mattis import embed, request_json, fmt_payload


class MattisVerify(commands.Cog):
    """Customer verification commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="mverify")
    async def mverify(self, ctx):
        """Start Mattis Discord verification."""
        body = {"guildId": str(ctx.guild.id), "discordUserId": str(ctx.author.id)}
        status, payload = await request_json(self.bot, "POST", "/bot/workspace/verify/start", json_body=body)
        if status < 400 and isinstance(payload, dict) and payload.get("url"):
            await ctx.author.send(embed=embed("Mattis Verification", f"Verify here: {payload['url']}"))
            await ctx.reply("I sent you a verification link in DMs.", mention_author=False)
        else:
            await ctx.send(embed=embed(f"Verification start → {status}", fmt_payload(payload)))

    @commands.command(name="mwhoami")
    async def mwhoami(self, ctx):
        status, payload = await request_json(self.bot, "GET", f"/bot/workspace/verify/whoami?guildId={ctx.guild.id}&discordUserId={ctx.author.id}")
        await ctx.send(embed=embed(f"Mattis account → {status}", fmt_payload(payload)))
