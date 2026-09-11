from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .levels import Level

    await bot.add_cog(Level(bot))