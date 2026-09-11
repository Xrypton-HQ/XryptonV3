from typing import TYPE_CHECKING
from discord.ext.commands import Cog

if TYPE_CHECKING:
    from base.Xrypton import Bot


class CogMeta(Cog):
    bot: "Bot"

    def __init__(self, bot: "Bot") -> None:
        self.bot = bot
        super().__init__()
