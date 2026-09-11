from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .rp import Roleplay

    await bot.add_cog(Roleplay(bot))