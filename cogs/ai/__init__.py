from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .ai import AI

    await bot.add_cog(AI(bot))