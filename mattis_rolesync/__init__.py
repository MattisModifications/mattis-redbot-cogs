from .mattis_rolesync import MattisRoleSync

async def setup(bot):
    await bot.add_cog(MattisRoleSync(bot))
