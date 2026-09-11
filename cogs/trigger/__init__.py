from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .trigger import Trigger

    await bot.add_cog(Trigger(bot))