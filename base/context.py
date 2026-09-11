from __future__ import annotations

import discord
from base.config import *
from base.managers.paginator import *
from discord.ext.commands import HelpCommand, Group
from datetime import datetime
from xxhash import xxh32_hexdigest

from discord.ext.commands import Command, Group


from typing import Union
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Unpack, TypedDict, cast

from discord import (
    AllowedMentions,
    ButtonStyle,
    Color,
    Message,
    MessageReference,
    Embed,
    Role,
    Member,
    SelectOption,
    SeparatorSpacing,
    ui,
)
from discord.ui import View, Button, LayoutView, Container, Section, TextDisplay, Thumbnail, Separator, ActionRow, Select
from discord.ui import button
from discord.ext.commands import Context as BaseContext
from base.config import *

if TYPE_CHECKING:
    from base.Xrypton import Bot


class FieldDict(TypedDict, total=False):
    name: str
    value: str
    inline: bool


class FooterDict(TypedDict, total=False):
    text: Optional[str]
    icon_url: Optional[str]


class AuthorDict(TypedDict, total=False):
    name: Optional[str]
    icon_url: Optional[str]


class ButtonDict(TypedDict, total=False):
    url: Optional[str]
    emoji: Optional[str]
    style: Optional[ButtonStyle]
    label: Optional[str]


class MessageKwargs(TypedDict, total=False):
    content: Optional[str]
    tts: Optional[bool]
    allowed_mentions: Optional[AllowedMentions]
    reference: Optional[MessageReference]
    mention_author: Optional[bool]
    delete_after: Optional[float]

    # Embed Related
    url: Optional[str]
    title: Optional[str]
    color: Optional[Color]
    image: Optional[str]
    description: Optional[str]
    thumbnail: Optional[str]
    footer: Optional[FooterDict]
    author: Optional[AuthorDict]
    fields: Optional[List[FieldDict]]
    timestamp: Optional[datetime]
    view: Optional[View]
    buttons: Optional[List[ButtonDict]]


class Context(BaseContext):
    bot: "Bot"

    @property
    def config(self):
        return self.bot.config

    def is_dangerous(self, role: Role) -> bool:
        permissions = role.permissions

        return any(
            [
                permissions.kick_members,
                permissions.ban_members,
                permissions.administrator,
                permissions.manage_channels,
                permissions.manage_guild,
                permissions.manage_messages,
                permissions.manage_roles,
                permissions.manage_webhooks,
                permissions.manage_emojis_and_stickers,
                permissions.manage_threads,
                permissions.mention_everyone,
                permissions.moderate_members,
            ]
        )

    async def embed(self, **kwargs: Unpack[MessageKwargs]) -> Message:
        return await self.send(**self.create(**kwargs))

    def create(self, **kwargs: Unpack[MessageKwargs]) -> Dict[str, Any]:
        """Create a message with the given keword arguments.

        Returns:
            Dict[str, Any]: The message content, embed, view and delete_after.
        """
        view = View()

        for button in kwargs.get("buttons") or []:
            if not button or not button.get("label"):
                continue

            view.add_item(
                Button(
                    label=button.get("label"),
                    style=button.get("style") or ButtonStyle.secondary,
                    emoji=button.get("emoji"),
                    url=button.get("url"),
                )
            )

        embed = (
            Embed(
                url=kwargs.get("url"),
                description=kwargs.get("description"),
                title=kwargs.get("title"),
                color=kwargs.get("color") or COLORS.neutral,
                timestamp=kwargs.get("timestamp"),
            )
            .set_image(url=kwargs.get("image"))
            .set_thumbnail(url=kwargs.get("thumbnail"))
            .set_footer(
                text=cast(dict, kwargs.get("footer", {})).get("text"),
                icon_url=cast(dict, kwargs.get("footer", {})).get("icon_url"),
            )
            .set_author(
                name=cast(dict, kwargs.get("author", {})).get("name", ""),
                icon_url=cast(dict, kwargs.get("author", {})).get("icon_url", ""),
            )
        )

        for field in kwargs.get("fields") or []:
            if not field:
                continue

            embed.add_field(
                name=field.get("name"),
                value=field.get("value"),
                inline=field.get("inline", False),
            )

        return {
            "content": kwargs.get("content"),
            "embed": embed,
            "view": kwargs.get("view") or view,
            "delete_after": kwargs.get("delete_after"),
        }

    async def approve(self, message: str, **kwargs) -> Message:
        return await self.send(
            embed=Embed(
                color=COLORS.approve,
                description=f"{EMOJIS.APPROVE} {self.author.mention}: {message}",
            ),
            **kwargs,
        )

    async def warn(self, message: str, **kwargs) -> Message:
        return await self.send(
            embed=Embed(
                color=COLORS.warn,
                description=f"{EMOJIS.WARN} {self.author.mention}: {message}",
            ),
            **kwargs,
        )

    async def deny(self, message: str, **kwargs) -> Message:
        return await self.send(
            embed=Embed(
                color=COLORS.deny,
                description=f"{EMOJIS.DENY} {self.author.mention}: {message}",
            ),
            **kwargs,
        )

    async def cooldown(self, message: str, **kwargs) -> Message:
        return await self.send(
            embed=Embed(
                color=0x38A9E1,
                description=f"{EMOJIS.COOLDOWN} {self.author.mention}: {message}",
            )
        )

    async def paginate(self, embeds: List[discord.Embed], **kwargs) -> Message:
        if len(embeds) == 1:
            if isinstance(embeds[0], discord.Embed):
                return await self.send(embed=embeds[0])

        paginator = Paginator(self, embeds)
        message = await self.send(embed=embeds[0], view=paginator, **kwargs)
        paginator.message = message
        return message


class HelpComponents(LayoutView):
    def __init__(self, categories, total_count):
        super().__init__(timeout=None)
        self.categories = categories
        self.total_count = total_count

        if categories:
            options = [
                SelectOption(label=name, value=name)
                for name in sorted(categories.keys())
            ]

            select = Select(
                custom_id="help_category_select",
                placeholder="Select a category",
                options=options,
            )

            async def on_select(interaction):
                try:
                    category_name = select.values[0]
                    commands = self.categories.get(category_name, [])
                    view = CategoryComponents(
                        category_name=category_name,
                        commands=commands,
                        total_count=self.total_count,
                        categories=self.categories,
                    )
                    await interaction.response.edit_message(view=view)
                except Exception as exc:
                    from loguru import logger as _logger
                    _logger.exception(f"Help category select failed: {exc}")
                    if not interaction.response.is_done():
                        try:
                            await interaction.response.send_message(
                                "An error occurred while loading that category.",
                                ephemeral=True,
                            )
                        except Exception:
                            pass

            select.callback = on_select

            self.container1 = Container(
                Section(
                    TextDisplay(
                        content=f"## `❓` Xrypton Help\nWelcome to **XRYPTON**. free-to use all in one bot.\n> {total_count} commands inside {len(categories)} categories"
                    ),
                    accessory=Thumbnail(
                        media="https://zne.breed.rip/assets/xrypton/avatar.png",
                    ),
                ),
                Separator(visible=True, spacing=SeparatorSpacing.small),
                ActionRow(select),
                accent_colour=Color(16777215),
            )

            self.add_item(self.container1)


class CategoryComponents(LayoutView):
    def __init__(self, category_name, commands, total_count, categories, page: int = 0):
        super().__init__(timeout=None)
        self.category_name = category_name
        self.commands = commands
        self.total_count = total_count
        self.categories = categories

        # Build raw command lines
        command_lines = []

        def add_command(cmd, depth=0):
            if cmd.hidden:
                return
            indent = "    " * depth
            aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            command_lines.append(
                f"{indent}• `{cmd.qualified_name}`{aliases} - {cmd.description or 'No description'}"
            )
            if isinstance(cmd, Group):
                for subcmd in sorted(cmd.commands, key=lambda c: c.qualified_name):
                    add_command(subcmd, depth + 1)

        for cmd in sorted(commands, key=lambda c: c.qualified_name):
            add_command(cmd)

        full_text = "\n".join(command_lines)

        # Split into pages. Discord caps TOTAL TextDisplay content inside a
        # single Container at 4000 chars (header + body combined).
        # Budget the body against (4000 - header - codeblock wrapper overhead).
        self.pages = []
        body_limit = 4000 - 120  # leaves room for header + codeblock wrapper
        if full_text:
            lines = full_text.splitlines()
            current_page: List[str] = []
            current_len = 0
            for line in lines:
                line_cost = len(line) + 1  # +1 for newline
                if current_len + line_cost > body_limit and current_page:
                    self.pages.append("\n".join(current_page))
                    current_page = [line]
                    current_len = line_cost
                else:
                    current_page.append(line)
                    current_len += line_cost
            if current_page:
                self.pages.append("\n".join(current_page))
        else:
            self.pages = [""]

        self.total_pages = len(self.pages)
        self.current_page = max(0, min(page, self.total_pages - 1))

        # Build UI
        self.container = ui.Container(accent_colour=Color(16777215))
        self._build_container()
        self.add_item(self.container)

    def _build_container(self):
        self.container.clear_items()

        # Header
        header = ui.TextDisplay(
            content=f"📚 {self.category_name} Commands\n"
                    f"Page {self.current_page + 1}/{self.total_pages} • Total Commands: {self.total_count}"
        )
        self.container.add_item(header)

        # Command list for current page
        command_content = self.pages[self.current_page] or "No commands in this category."
        cmd_display = ui.TextDisplay(content=f"\n{command_content}\n")
        self.container.add_item(cmd_display)

        # Navigation buttons (only if more than one page)
        if self.total_pages > 1:
            prev_btn = ui.Button(
                emoji=EMOJIS.PREVIOUS,
                style=ButtonStyle.blurple,
                custom_id="cat_prev",
                disabled=(self.current_page == 0)
            )
            next_btn = ui.Button(
                emoji=EMOJIS.NEXT,
                style=ButtonStyle.blurple,
                custom_id="cat_next",
                disabled=(self.current_page == self.total_pages - 1)
            )

            async def prev_callback(interaction: discord.Interaction):
                self.current_page = max(0, self.current_page - 1)
                self._build_container()
                await interaction.response.edit_message(view=self)

            async def next_callback(interaction: discord.Interaction):
                self.current_page = min(self.total_pages - 1, self.current_page + 1)
                self._build_container()
                await interaction.response.edit_message(view=self)

            prev_btn.callback = prev_callback
            next_btn.callback = next_callback

            nav_row = ui.ActionRow(prev_btn, next_btn)
            self.container.add_item(nav_row)

        # Category selection dropdown
        options = [
            SelectOption(label=name, value=name)
            for name in sorted(self.categories.keys())
        ]
        select = ui.Select(
            custom_id="help_category_select",
            placeholder="Select a category",
            options=options,
        )

        async def on_select(interaction):
            try:
                category_name = select.values[0]
                commands = self.categories.get(category_name, [])
                # Reset to page 0 when switching categories
                view = CategoryComponents(
                    category_name=category_name,
                    commands=commands,
                    total_count=self.total_count,
                    categories=self.categories,
                    page=0
                )
                await interaction.response.edit_message(view=view)
            except Exception as exc:
                from loguru import logger as _logger
                _logger.exception(f"Category select failed: {exc}")
                if not interaction.response.is_done():
                    try:
                        await interaction.response.send_message(
                            "An error occurred while loading that category.",
                            ephemeral=True,
                        )
                    except Exception:
                        pass

        select.callback = on_select
        self.container.add_item(ui.ActionRow(select))


class ComponentPaginator(LayoutView):
    """Paginates a list of components v2 containers, mirroring the behaviour
    of :class:`Paginator` but replacing embeds with ``ui.Container`` pages.

    The pagination action-row buttons are nested *inside* the container so the
    whole message renders as a single cohesive components v2 block.
    """

    def __init__(self, ctx: Context, pages: List[List[Any]], timeout: float = 180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.pages = pages
        self.current = 0
        self.message: Message = None  # type: ignore

        self.prev_button = ComponentPaginatorPrev(self, emoji=EMOJIS.PREVIOUS)
        self.next_button = ComponentPaginatorNext(self, emoji=EMOJIS.NEXT)
        self.pages_button = ComponentPaginatorPages(self, emoji=EMOJIS.NAVIGATE)
        self.cancel_button = ComponentPaginatorCancel(self, emoji=EMOJIS.CANCEL)

        self.action_row = ui.ActionRow(
            self.prev_button,
            self.next_button,
            self.pages_button,
            self.cancel_button,
        )

        self.container = ui.Container(
            accent_color=COLORS.neutral,
        )
        self._rebuild_container()
        self.add_item(self.container)

    def _refresh(self):
        self.prev_button.disabled = self.current == 0
        self.next_button.disabled = self.current == len(self.pages) - 1

    def _rebuild_container(self):
        self._refresh()
        self.container.clear_items()
        for item in self.pages[self.current]:
            self.container.add_item(item)
        self.container.add_item(self.action_row)

    def current_page(self) -> List[Any]:
        return self.pages[self.current]

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.warn("You're not the **author** of this message!")
            return False
        return True

    async def _edit(self, interaction: discord.Interaction):
        self._rebuild_container()
        await interaction.response.edit_message(view=self)


class _ComponentPaginatorButton(ui.Button):
    def __init__(self, paginator: ComponentPaginator, emoji: str, style: ButtonStyle, custom_id: str):
        super().__init__(emoji=emoji, style=style, custom_id=custom_id)
        self.paginator = paginator


class ComponentPaginatorPrev(_ComponentPaginatorButton):
    def __init__(self, paginator: ComponentPaginator, emoji: str):
        super().__init__(paginator, emoji, ButtonStyle.blurple, "previous")

    async def callback(self, interaction: discord.Interaction):
        if self.paginator.current == 0:
            self.paginator.current = len(self.paginator.pages) - 1
        else:
            self.paginator.current -= 1
        return await self.paginator._edit(interaction)


class ComponentPaginatorNext(_ComponentPaginatorButton):
    def __init__(self, paginator: ComponentPaginator, emoji: str):
        super().__init__(paginator, emoji, ButtonStyle.blurple, "next")

    async def callback(self, interaction: discord.Interaction):
        if self.paginator.current == len(self.paginator.pages) - 1:
            self.paginator.current = 0
        else:
            self.paginator.current += 1
        return await self.paginator._edit(interaction)


class ComponentPaginatorPages(_ComponentPaginatorButton):
    def __init__(self, paginator: ComponentPaginator, emoji: str):
        super().__init__(paginator, emoji, ButtonStyle.grey, "pages")

    async def callback(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(ComponentPagesModal(self.paginator))


class ComponentPaginatorCancel(_ComponentPaginatorButton):
    def __init__(self, paginator: ComponentPaginator, emoji: str):
        super().__init__(paginator, emoji, ButtonStyle.danger, "cancel")

    async def callback(self, interaction: discord.Interaction):
        self.paginator.stop()
        return await interaction.message.delete()


class ComponentPagesModal(discord.ui.Modal, title="Select Page"):
    def __init__(self, paginator: ComponentPaginator):
        super().__init__()
        self.paginator = paginator
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
        if page < 1 or page > len(self.paginator.pages):
            return await interaction.warn("Please provide a valid page number.")
        self.paginator.current = page - 1
        return await self.paginator._edit(interaction)


class XryptonHelp(HelpCommand):
    context: "Context"

    def __init__(self, **options):
        super().__init__(
            command_attrs={"aliases": ["h", "cmds", "commands"], "hidden": True},
            verify_checks=False,
            **options,
        )

    async def send_bot_help(self, mapping):
        categories = {}

        for cog, command_list in mapping.items():
            commands = [cmd for cmd in command_list if not cmd.hidden]
            if commands:
                cog_name = cog.qualified_name if cog else "Uncategorized"
                if cog_name not in ["Jishaku", "Syncing"]:
                    categories[cog_name] = commands

        total_count = len([c for c in self.context.bot.walk_commands() if c.cog_name != "Jishaku"])

        if categories:
            view = HelpComponents(categories, total_count)
            await self.context.send(view=view)
        else:
            embed = Embed(
                title="XRYPTON Help",
                description="No commands available.",
                color=COLORS.neutral,
            )
            await self.context.send(embed=embed)


    def _get_syntax(self, command: Union[Command, Group]) -> str:
        params = " ".join(f"[{name}]" for name in command.clean_params)
        return f",{command.qualified_name} {params}"

    async def send_command_help(self, command: Command):
        try:
            permissions = command.permissions  # type: ignore
        except (AttributeError, TypeError):
            permissions = []

        aliases = ", ".join(command.aliases) if command.aliases else "N/A"
        params = ", ".join(command.clean_params) if command.clean_params else "N/A"
        perms = f"{EMOJIS.WARN} {', '.join(permissions)}" if permissions else f"{EMOJIS.WARN} N/A"
        syntax = self._get_syntax(command)
        example = next(
            (
                v for v in (
                    getattr(command, "_example", None),
                    getattr(getattr(command, "callback", None), "_example", None),
                )
                if v
            ),
            "N/A",
        )
        module = command.cog_name.lower() if command.cog_name else "N/A"

        container = ui.Container(
            ui.TextDisplay(
                f"## Command: {command.qualified_name}\n"
                f"{command.description or 'No description provided'}\n\n"
                f"**Aliases**: {aliases}\n"
                f"**Parameters**: {params}\n"
                f"**Permissions**: {perms}",
            ),
            ui.Separator(visible=False),
            ui.TextDisplay(
                f"**Usage**\n```py\nSyntax: {syntax}\nExample: {example}\n```",
            ),
            ui.TextDisplay(
                f"-# Page 1/1 • Module: {module}",
            ),
            accent_color=COLORS.neutral,
        )

        view = ui.LayoutView(timeout=None)
        view.add_item(container)
        return await self.context.send(view=view)

    def _walk_subcommands(self, group: Group) -> List[Command]:
        """Recursively collect all subcommands, including those nested in subgroups."""
        result: List[Command] = []
        for command in sorted(group.commands, key=lambda c: c.qualified_name):
            result.append(command)
            if isinstance(command, Group):
                result.extend(self._walk_subcommands(command))
        return result

    async def send_group_help(self, group: Group):
        subcommands = self._walk_subcommands(group)

        group_permissions = set()
        for cmd in subcommands:
            try:
                if hasattr(cmd, "permissions") and cmd.permissions:  # type: ignore
                    group_permissions.update(cmd.permissions)  # type: ignore
            except (AttributeError, TypeError):
                continue

        group_aliases = ", ".join(group.aliases) if group.aliases else "N/A"
        group_params = ", ".join(group.clean_params) if group.clean_params else "N/A"
        group_perms = f"{EMOJIS.WARN} {', '.join(group_permissions)}" if group_permissions else f"{EMOJIS.WARN} N/A"
        group_syntax = self._get_syntax(group)
        group_example = next(
            (
                v for v in (
                    getattr(group, "_example", None),
                    getattr(getattr(group, "callback", None), "_example", None),
                )
                if v
            ),
            "N/A",
        )
        module = group.cog_name.lower() if group.cog_name else "N/A"
        total = len(subcommands) + 1

        pages = []

        group_content = [
            ui.TextDisplay(
                f"## Command Group: {group.name}\n"
                f"{group.help or group.description or 'No description provided'}\n\n"
                f"**Aliases**: {group_aliases}\n"
                f"**Parameters**: {group_params}\n"
                f"**Permissions**: {group_perms}",
            ),
            ui.Separator(visible=False),
            ui.TextDisplay(
                f"**Usage**\n```py\nSyntax: {group_syntax}\nExample: {group_example}\n```",
            ),
            ui.TextDisplay(
                f"-# Page 1/{total} • Module: {module}",
            ),
        ]
        pages.append(group_content)

        for i, command in enumerate(subcommands):
            try:
                permissions = command.permissions  # type: ignore
            except (AttributeError, TypeError):
                permissions = []

            cmd_aliases = ", ".join(command.aliases) if command.aliases else "N/A"
            cmd_params = ", ".join(command.clean_params) if command.clean_params else "N/A"
            cmd_perms = f"{EMOJIS.WARN} {', '.join(permissions)}" if permissions else f"{EMOJIS.WARN} N/A"
            cmd_module = command.cog_name.lower() if command.cog_name else "N/A"

            cmd_content = [
                ui.TextDisplay(
                    f"## Command: {command.qualified_name}\n"
                    f"{command.help or command.description or 'No description provided'}\n\n"
                    f"**Aliases**: {cmd_aliases}\n"
                    f"**Parameters**: {cmd_params}\n"
                    f"**Permissions**: {cmd_perms}",
                ),
                ui.Separator(visible=False),
                ui.TextDisplay(
                    f"**Usage**\n```py\nSyntax: {self._get_syntax(command)}\n```",
                ),
                ui.TextDisplay(
                    f"-# Page {i + 2}/{total} • Module: {cmd_module}",
                ),
            ]
            pages.append(cmd_content)

        paginator = ComponentPaginator(self.context, pages)
        return await self.context.send(view=paginator)



class Confirmation(View):
    def __init__(self, ctx: Context, user: Member, reason: str, action: str):
        super().__init__()
        self.ctx = ctx
        self.user = user
        self.reason = reason
        self.action = action
        self.message = None

    async def send_confirmation(self):
        embed = Embed(
            title="",
            description=f"Are you sure you want to {self.action} {self.user.mention if self.user else ''}?",
            color=COLORS.neutral,
        )
        self.message = await self.ctx.send(embed=embed, view=self)

    @ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes_button(self, button: Button, interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(
                "You cannot confirm this action.", ephemeral=True
            )
            return
        if self.action == "ban" and self.user:
            await self.user.ban(reason=self.reason)
            await self.ctx.approve(f"{self.user.mention} has been **banned**.")
        elif self.action == "kick" and self.user:
            await self.user.kick(reason=self.reason)
            await self.ctx.approve(f"{self.user.mention} has been **kicked**.")

        if self.message:
            await self.message.delete()
        self.stop()

    @ui.button(label="No", style=discord.ButtonStyle.red)
    async def no_button(self, button: Button, interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(
                "You cannot confirm cancel action.", ephemeral=True
            )
            return
        await self.ctx.approve(
            f"{self.action.capitalize()} action has been **cancelled**."
        )
        if self.message:
            await self.message.delete()
        self.stop()