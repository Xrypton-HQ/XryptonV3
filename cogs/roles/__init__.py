from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .roles import Roles

    await bot.add_cog(Roles(bot))