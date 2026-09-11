from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .invites import Invites

    await bot.add_cog(Invites(bot))