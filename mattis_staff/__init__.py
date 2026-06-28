from .mattis_staff import MattisStaff

async def setup(bot):
    await bot.add_cog(MattisStaff(bot))
