from .mattis_incidents import MattisIncidents

async def setup(bot):
    await bot.add_cog(MattisIncidents(bot))
