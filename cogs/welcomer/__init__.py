from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .welcomer_leaver import WL as WelcomerLeaver

    await bot.add_cog(WelcomerLeaver(bot))