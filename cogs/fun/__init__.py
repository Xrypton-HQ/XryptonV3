from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .fun import Fun
    from .starboard import Starboard
    
    await bot.add_cog(Starboard(bot))
    await bot.add_cog(Fun(bot))