import discord
from discord import Message

from base.config import *


class Paginator(discord.ui.View):
    def __init__(
        self,
        ctx,
        pages: list[discord.Embed],
        current: int = 0,
        timeout: float = None,  # type: ignore
    ):
        self.ctx = ctx
        self.pages = pages
        self.current = current
        self.message: Message = None  # type: ignore
        super().__init__(timeout=180)

        self.add_item(
            PaginatorButton(
                style=discord.ButtonStyle.blurple,
                custom_id="previous",
                emoji=EMOJIS.PREVIOUS,
            )
        )
        self.add_item(
            PaginatorButton(
                style=discord.ButtonStyle.blurple,
                custom_id="next",
                emoji=EMOJIS.NEXT,
            )
        )
        self.add_item(
            PaginatorButton(
                style=discord.ButtonStyle.grey,
                custom_id="pages",
                emoji=EMOJIS.NAVIGATE,
            )
        )
        self.add_item(
            PaginatorButton(
                style=discord.ButtonStyle.danger,
                custom_id="cancel",
                emoji=EMOJIS.CANCEL,
            )
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def interaction_check(
        self, interaction: discord.Interaction[discord.Client]
    ) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.warn("You're not the **author** of this embed!")
            return False
        return True


class PaginatorButton(discord.ui.Button):
    def __init__(self, style: discord.ButtonStyle, emoji: str, custom_id: str = None):
        super().__init__(style=style, custom_id=custom_id, emoji=emoji)
        self.disabled: bool = False

    async def callback(self, interaction: discord.Interaction):
        if self.custom_id == "previous":
            return await self.previous(interaction)
        if self.custom_id == "next":
            return await self.next(interaction)
        if self.custom_id == "pages":
            return await self.pages(interaction)
        if self.custom_id == "cancel":
            return await self.cancel(interaction)

    async def previous(self, interaction: discord.Interaction):
        if self.view.current == 0:
            self.view.current = len(self.view.pages) - 1
        else:
            self.view.current -= 1
        return await interaction.response.edit_message(
            embed=self.view.pages[self.view.current]
        )

    async def next(self, interaction: discord.Interaction):
        if self.view.current == len(self.view.pages) - 1:
            self.view.current = 0
        else:
            self.view.current += 1
        return await interaction.response.edit_message(
            embed=self.view.pages[self.view.current]
        )

    async def cancel(self, interaction: discord.Interaction):
        self.view.stop()
        return await interaction.message.delete()

    async def pages(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(PagesModal(self.view))


class PagesModal(discord.ui.Modal, title="Select Page"):
    def __init__(self, view: Paginator):
        super().__init__()
        self.view = view
        self.selector = discord.ui.TextInput(
            label="Page",
            placeholder="5",
            custom_id="PAGINATOR:PAGES",
            style=discord.TextStyle.short,
            min_length=1,
            max_length=3,
            required=True,
            row=0,
        )
        self.add_item(self.selector)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.selector.value)
        except ValueError:
            return await interaction.warn("Please provide a valid page number.")
        if page < 1 or page > len(self.view.pages):
            return await interaction.warn("Please provide a valid page number.")
        self.view.current = page - 1
        return await interaction.response.edit_message(
            embed=self.view.pages[self.view.current]
        )
