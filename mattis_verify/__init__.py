from .mattis_verify import MattisVerify

async def setup(bot):
    await bot.add_cog(MattisVerify(bot))
