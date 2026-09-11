from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .stats import Statistics

    await bot.add_cog(Statistics(bot))