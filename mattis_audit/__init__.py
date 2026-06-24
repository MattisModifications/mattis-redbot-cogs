from .mattis_audit import MattisAudit

async def setup(bot):
    await bot.add_cog(MattisAudit(bot))
