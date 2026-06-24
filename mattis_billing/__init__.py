from .mattis_billing import MattisBilling

async def setup(bot):
    await bot.add_cog(MattisBilling(bot))
