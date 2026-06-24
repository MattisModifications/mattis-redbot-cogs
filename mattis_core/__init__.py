from .mattis_core import MattisCore

async def setup(bot):
    await bot.add_cog(MattisCore(bot))
