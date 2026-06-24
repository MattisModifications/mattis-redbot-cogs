from .mattis_status import MattisStatus

async def setup(bot):
    await bot.add_cog(MattisStatus(bot))
