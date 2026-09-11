from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .giveaway import Giveaway

    await bot.add_cog(Giveaway(bot))