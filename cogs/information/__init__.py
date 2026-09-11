from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .info import Information

    await bot.add_cog(Information(bot))