from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .voicemaster import VoiceMaster

    await bot.add_cog(VoiceMaster(bot))