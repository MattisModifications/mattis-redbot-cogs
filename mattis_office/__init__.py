from .mattis_office import MattisOffice

async def setup(bot):
    await bot.add_cog(MattisOffice(bot))
