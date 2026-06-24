from .mattis_crm import MattisCRM

async def setup(bot):
    await bot.add_cog(MattisCRM(bot))
