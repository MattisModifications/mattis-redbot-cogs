from .mattis_modules import MattisModules

async def setup(bot):
    await bot.add_cog(MattisModules(bot))
