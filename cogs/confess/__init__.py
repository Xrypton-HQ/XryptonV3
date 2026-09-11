from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .confess import Confessions

    await bot.add_cog(Confessions(bot))