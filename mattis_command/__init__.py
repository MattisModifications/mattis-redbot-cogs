from .mattis_command import MattisCommand

async def setup(bot):
    await bot.add_cog(MattisCommand(bot))
