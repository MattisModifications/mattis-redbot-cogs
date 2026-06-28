from .mattis_roblox import MattisRoblox

async def setup(bot):
    await bot.add_cog(MattisRoblox(bot))
