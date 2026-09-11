from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .mod import Moderation
    from .sticky import Sticky

    await bot.add_cog(Sticky(bot))
    await bot.add_cog(Moderation(bot))