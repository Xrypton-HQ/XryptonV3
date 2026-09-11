from base.Xrypton import Bot


async def setup(bot: Bot) -> None:
    from .tickets import Ticket

    await bot.add_cog(Ticket(bot))