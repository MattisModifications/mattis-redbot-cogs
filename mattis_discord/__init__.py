from .mattis_discord import MattisDiscord

async def setup(bot):
    await bot.add_cog(MattisDiscord(bot))
