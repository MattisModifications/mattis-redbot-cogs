from .mattis_apps import MattisApplications

async def setup(bot):
    await bot.add_cog(MattisApplications(bot))
