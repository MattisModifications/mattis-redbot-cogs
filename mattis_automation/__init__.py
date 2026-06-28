from .mattis_automation import MattisAutomation

async def setup(bot):
    await bot.add_cog(MattisAutomation(bot))
