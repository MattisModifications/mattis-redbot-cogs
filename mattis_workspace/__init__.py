from .mattis_workspace import MattisWorkspace

async def setup(bot):
    await bot.add_cog(MattisWorkspace(bot))
