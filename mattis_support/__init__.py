from .mattis_support import MattisSupport

async def setup(bot):
    await bot.add_cog(MattisSupport(bot))
