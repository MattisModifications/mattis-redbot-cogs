from .mattis_security import MattisSecurity

async def setup(bot):
    await bot.add_cog(MattisSecurity(bot))
