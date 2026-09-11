from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .antinuke import AntiNuke
    from .antiraid import AntiRaid

    await bot.add_cog(AntiRaid(bot))
    await bot.add_cog(AntiNuke(bot))