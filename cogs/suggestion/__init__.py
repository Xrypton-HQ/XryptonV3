from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .suggest import Suggest

    await bot.add_cog(Suggest(bot))