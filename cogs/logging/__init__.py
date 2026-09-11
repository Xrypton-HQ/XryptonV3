from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .logging import Logging

    await bot.add_cog(Logging(bot))