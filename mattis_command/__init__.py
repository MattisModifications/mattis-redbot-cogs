from .mattis_command import MattisSystems

async def setup(bot):
    await bot.add_cog(MattisSystems(bot))
