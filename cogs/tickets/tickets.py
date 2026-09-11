import asyncio
import secrets
import os
import json
import datetime
import logging
from contextlib import suppress
from typing import Annotated, Dict, List, Literal, Optional, TypedDict, cast, overload, Union

import discord
from discord import (
    ActionRow,
    AllowedMentions,
    ButtonStyle,
    CategoryChannel,
    Color,
    Embed,
    Guild,
    HTTPException,
    Interaction,
    Member,
    Message,
    PartialMessage,
    PermissionOverwrite,
    Role,
    TextChannel,
    Emoji,
    ComponentType,
)
from discord.ui import View, Button, button
from discord.utils import find
from discord.components import Button as ButtonComponent
from discord.ext.commands import group, has_permissions, Cog, flag, check, Range, FlagConverter

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import EMOJIS, COLORS
from base.managers.EmbedBuilder import EmbedBuilder

log = logging.getLogger("xrypton/ticket")  # keep original name

# ===== Local Script class (replaces tools.parser.Script) =====
class Script:
    """Simple script processor with placeholders."""
    def __init__(self, template: str, args: list):
        self.template = template
        self.args = args  # [guild, user, channel, ...]

    def _format(self, text: str) -> str:
        guild = self.args[0] if len(self.args) > 0 else None
        user = self.args[1] if len(self.args) > 1 else None
        channel = self.args[2] if len(self.args) > 2 else None

        replacements = {
            "{guild.name}": guild.name if guild else "",
            "{guild.id}": str(guild.id) if guild else "",
            "{guild.member_count}": str(guild.member_count) if guild else "",
            "{guild.vanity}": guild.vanity_url_code if guild and guild.vanity_url_code else "none",
            "{user.mention}": user.mention if user else "",
            "{user.name}": user.name if user else "",
            "{user.id}": str(user.id) if user else "",
            "{user.display_name}": user.display_name if user else "",
            "{channel.mention}": channel.mention if channel else "",
            "{channel.name}": channel.name if channel else "",
            "{channel.id}": str(channel.id) if channel else "",
        }
        for key, value in replacements.items():
            text = text.replace(key, value)
        return text

    @property
    def format(self) -> str:
        return self.template

    async def send(self, destination, **kwargs):
        """Send the formatted script to a destination (Context or TextChannel)."""
        content = self._format(self.template)
        if hasattr(destination, 'send'):
            return await destination.send(content=content, **kwargs)
        else:
            return await destination.send(content=content, **kwargs)

# ===== Helper functions =====
def codeblock(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"

def vowel(text: str) -> str:
    if text and text[0].lower() in 'aeiou':
        return 'an'
    return 'a'

def parse(template: str, args: list) -> str:
    """Parse a template with placeholders (used for channel names)."""
    script = Script(template, args)
    return script._format(template)

# ===== Flag converter for buttons =====
class ButtonFlags(FlagConverter):
    style: Literal["blurple", "grey", "gray", "green", "red"] = flag(
        default="green",
        aliases=["color"],
    )
    emoji: Optional[str] = flag(
        aliases=["emote"],
    )

# ===== Types =====
class TicketConfig(TypedDict):
    guild_id: int
    channel_id: int
    message_id: int
    staff_ids: list[int]
    blacklisted_ids: list[int]
    channel_name: Optional[str]

class TicketButton(TypedDict):
    identifier: str
    guild_id: int
    template: Optional[str]
    category_id: Optional[int]
    topic: Optional[str]

class TicketChannel(TypedDict):
    identifier: str
    guild_id: int
    channel_id: int
    user_id: int

# ===== Checks =====
def in_ticket():
    async def predicate(ctx: Context):
        if not ctx.guild:
            return False
        record = await ctx.bot.pool.fetchrow(
            """
            SELECT * FROM ticket_open
            WHERE guild_id = $1 AND channel_id = $2
            """,
            ctx.guild.id, ctx.channel.id
        )
        return bool(record)
    return check(predicate)

def can_close_ticket():
    async def predicate(ctx: Context):
        if ctx.author.guild_permissions.manage_channels:
            return True
        config = await ctx.bot.pool.fetchrow(
            "SELECT staff_ids FROM ticket_config WHERE guild_id = $1",
            ctx.guild.id
        )
        if not config:
            await ctx.warn("You're missing the `manage_channels` permission!")
            return False
        staff_ids = json.loads(config["staff_ids"]) if config["staff_ids"] else []
        has_staff_role = any(role.id in staff_ids for role in ctx.author.roles)
        if not has_staff_role:
            await ctx.warn("You're missing the `manage_channels` permission!")
            return False
        return True
    return check(predicate)

# ===== Close Ticket Button View =====
class DeleteTicket(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(
        label="Close Ticket",
        emoji="🗑️",  # fallback, as EMOJIS.TICKETS.TRASH may not exist
        style=ButtonStyle.red,
        custom_id="ticket:close",
    )
    async def close(self, interaction: Interaction, button: Button):
        await interaction.response.send_message(
            "Use `;ticket close` to close this ticket.", ephemeral=True
        )

# ===== Main Cog =====
class Ticket(Cog):
    """Create tickets for users to contact the staff."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT,
                message_id BIGINT,
                staff_ids TEXT DEFAULT '[]',
                blacklisted_ids TEXT DEFAULT '[]',
                channel_name TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS ticket_button (
                identifier TEXT,
                guild_id BIGINT,
                template TEXT,
                category_id BIGINT,
                topic TEXT,
                PRIMARY KEY (identifier, guild_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS ticket_open (
                identifier TEXT,
                guild_id BIGINT,
                channel_id BIGINT,
                user_id BIGINT,
                PRIMARY KEY (identifier, guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS ticket_logs (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    # ---------- Helper methods ----------
    @staticmethod
    def sanitize_data(data):
        if isinstance(data, dict):
            return {k: Ticket.sanitize_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [Ticket.sanitize_data(item) for item in data]
        elif isinstance(data, discord.PartialEmoji):
            return str(data)
        elif isinstance(data, discord.Emoji):
            return str(data)
        elif hasattr(data, 'id'):
            return str(data.id)
        elif hasattr(data, '__dict__'):
            return str(data)
        return data

    async def get_buttons_data(self, channel):
        messages = [message async for message in channel.history(limit=50)]
        buttons_data = []
        for message in messages:
            if message.components:
                for row in message.components:
                    for component in row.children:
                        if component.type == ComponentType.button:
                            label = component.label if component.label else ""
                            emoji_url = component.emoji.url if component.emoji else None
                            buttons_data.append({
                                "label": label or emoji_url,
                                "type": "button",
                                "style": str(component.style),
                                "custom_id": component.custom_id,
                                "url": component.url if component.style == ButtonStyle.link else None,
                            })
        return buttons_data

    async def create_ticket_transcript(self, channel: TextChannel, reason: str = "No reason provided") -> dict:
        transcript = {
            "channel": {
                "created_at": channel.created_at.isoformat(),
                "id": str(channel.id),
                "name": channel.name,
                "type": str(channel.type),
            },
            "embeds": [],
            "mentions": [],
            "messages": [],
            "reactions": [],
            "buttons": [],
            "ticket": {
                "channel_id": channel.id,
                "closed_at": None,
                "closed_by_id": None,
                "guild_id": channel.guild.id,
                "opened_by_id": None,
                "reason": reason,
            },
            "users": [],
            "attachments": [],
        }

        messages = [msg async for msg in channel.history(limit=None, oldest_first=True)]
        users_dict = {}

        for message in messages:
            message_data = {
                "id": str(message.id),
                "author_id": str(message.author.id),
                "channel_id": str(message.channel.id),
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "edited_timestamp": message.edited_at.isoformat() if message.edited_at else None,
                "pinned": message.pinned,
                "timestamp": message.created_at.isoformat(),
            }
            transcript["messages"].append(message_data)

            if message.author.id not in users_dict:
                users_dict[message.author.id] = {
                    "accent_color": str(message.author.accent_color) if message.author.accent_color else None,
                    "author_id": str(message.author.id),
                    "avatar": str(message.author.avatar.url) if message.author.avatar else None,
                    "banner": str(message.author.banner.url) if message.author.banner else None,
                    "bot": message.author.bot,
                    "channel_id": str(message.channel.id),
                    "content": "",
                    "created_at": message.author.created_at.isoformat(),
                    "discriminator": message.author.discriminator,
                    "edited_timestamp": None,
                    "global_name": message.author.global_name,
                    "id": str(message.author.id),
                    "pinned": False,
                    "system": message.author.system,
                    "timestamp": message.created_at.isoformat(),
                    "username": message.author.name,
                }

            for embed in message.embeds:
                embed_data = {
                    "title": embed.title,
                    "description": embed.description,
                    "url": embed.url,
                    "color": str(embed.color.value) if embed.color else None,
                    "message_id": str(message.id),
                    "author": {
                        "name": embed.author.name if embed.author else None,
                        "icon_url": str(embed.author.icon_url) if embed.author and embed.author.icon_url else None,
                        "url": str(embed.author.url) if embed.author and embed.author.url else None,
                    },
                    "footer": {
                        "text": embed.footer.text if embed.footer else None,
                        "icon_url": str(embed.footer.icon_url) if embed.footer and embed.footer.icon_url else None,
                    },
                    "timestamp": embed.timestamp.isoformat() if embed.timestamp else None,
                    "thumbnail": {"url": str(embed.thumbnail.url) if embed.thumbnail else None},
                    "image": {"url": str(embed.image.url) if embed.image else None},
                    "fields": [
                        {"name": field.name, "value": field.value, "inline": field.inline}
                        for field in embed.fields
                    ],
                }
                transcript["embeds"].append(embed_data)

            for attachment in message.attachments:
                attachment_data = {
                    "file_size": attachment.size,
                    "filename": attachment.filename,
                    "message_id": str(message.id),
                    "url": attachment.url,
                }
                transcript["attachments"].append(attachment_data)

            for reaction in message.reactions:
                reaction_data = {
                    "message_id": str(message.id),
                    "name": str(reaction.emoji),
                    "count": reaction.count,
                    "image": str(reaction.emoji.url) if hasattr(reaction.emoji, 'url') else None,
                    "active": True,
                }
                transcript["reactions"].append(reaction_data)

        transcript["users"] = list(users_dict.values())

        # Get buttons from the latest message that has components
        for msg in reversed(messages):
            if msg.components:
                for row in msg.components:
                    for component in row.children:
                        if component.type == ComponentType.button:
                            button_data = {
                                "label": component.label or "",
                                "type": "button",
                                "style": str(component.style),
                                "custom_id": component.custom_id,
                                "url": component.url if component.style == ButtonStyle.link else None,
                            }
                            transcript["buttons"].append(button_data)
                break

        return transcript

    async def get_channel_member_ids(self, channel: TextChannel) -> dict:
        member_ids = set()
        for member in channel.guild.members:
            if channel.permissions_for(member).read_messages:
                member_ids.add(member.id)
        return {"ids": list(member_ids)}

    async def write_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    @overload
    async def get_ticket_message(
        self,
        guild: Guild,
        record: dict,
        partial: Literal[True],
    ) -> PartialMessage: ...

    @overload
    async def get_ticket_message(
        self,
        guild: Guild,
        record: dict,
        partial: Literal[False] = False,
    ) -> Optional[Message]: ...

    async def get_ticket_message(
        self,
        guild: Guild,
        record: dict,
        partial: bool = False,
    ) -> Optional[Message | PartialMessage]:
        channel = guild.get_channel_or_thread(record["channel_id"])
        if not channel:
            return None
        if partial:
            return channel.get_partial_message(record["message_id"])
        try:
            return await channel.fetch_message(record["message_id"])
        except HTTPException:
            return None

    # ---------- Listeners ----------
    @Cog.listener("on_guild_channel_delete")
    async def ticket_channel_delete(self, channel: TextChannel):
        if not isinstance(channel, TextChannel):
            return
        await self.bot.pool.execute(
            "DELETE FROM ticket_open WHERE guild_id = $1 AND channel_id = $2",
            channel.guild.id, channel.id
        )

    @Cog.listener("on_interaction")
    async def ticket_create(self, interaction: Interaction):
        if (
            not interaction.data
            or not interaction.guild
            or not isinstance(interaction.user, Member)
        ):
            return

        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.endswith("ticket_create"):
            return

        guild = interaction.guild
        member = interaction.user
        identifier = custom_id.split(":", 1)[0]

        config = await self.bot.pool.fetchrow(
            "SELECT * FROM ticket_config WHERE guild_id = $1", guild.id
        )
        record = await self.bot.pool.fetchrow(
            "SELECT * FROM ticket_button WHERE guild_id = $1 AND identifier = $2",
            guild.id, identifier
        )
        if not config or not record:
            return await interaction.response.send_message(
                embed=Embed(description="This button shouldn't exist anymore! \n> Please contact a staff member about this"),
                ephemeral=True
            )

        blacklisted_ids = json.loads(config["blacklisted_ids"]) if config["blacklisted_ids"] else []
        if member.id in blacklisted_ids or any(role.id in blacklisted_ids for role in member.roles):
            return await interaction.response.send_message(
                embed=Embed(description="You're not allowed to create tickets!"),
                ephemeral=True
            )

        ticket = await self.bot.pool.fetchrow(
            "SELECT * FROM ticket_open WHERE guild_id = $1 AND user_id = $2 AND identifier = $3",
            guild.id, member.id, identifier
        )
        if ticket:
            channel = guild.get_channel(ticket["channel_id"])
            if channel:
                return await interaction.response.send_message(
                    embed=Embed(description=f"You already have an open ticket - {channel.mention}"),
                    ephemeral=True
                )

        await interaction.response.defer(ephemeral=True, thinking=True)
        category = guild.get_channel(record["category_id"] or 0)
        if not isinstance(category, CategoryChannel):
            category = None

        staff_ids = json.loads(config["staff_ids"]) if config["staff_ids"] else []
        overwrites: Dict[Role | Member, PermissionOverwrite] = {
            guild.default_role: PermissionOverwrite(view_channel=False, read_messages=False),
        }
        for target in (member, *[role for role_id in staff_ids if (role := guild.get_role(role_id))]):
            overwrites[target] = PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                read_message_history=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                mention_everyone=False,
            )

        channel_name_template = config["channel_name"] or f"ticket-{member.name}"
        channel_name = parse(channel_name_template, [guild, member])[:100]

        topic_template = record["topic"] or ""
        topic = parse(topic_template, [guild, member])

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                topic=topic,
                overwrites=overwrites,
                reason=f"Ticket opened by {member} ({member.id})"
            )
        except HTTPException as exc:
            return await interaction.followup.send(
                embed=Embed(description=f"Failed to create a ticket channel!\n> {codeblock(exc.text)}"),
                ephemeral=True
            )

        await self.bot.pool.execute(
            """
            INSERT INTO ticket_open (identifier, guild_id, channel_id, user_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (identifier, guild_id, user_id)
            DO UPDATE SET channel_id = EXCLUDED.channel_id
            """,
            identifier, guild.id, channel.id, member.id
        )

        await interaction.followup.send(
            embed=Embed(description=f"Created a new ticket - {channel.mention}"),
            ephemeral=True
        )

        if record["template"]:
            script = Script(record["template"], [guild, member, channel])
            with suppress(HTTPException):
                message = await script.send(channel, allowed_mentions=AllowedMentions.all())
                await message.pin()
        else:
            view = DeleteTicket()
            embed = Embed(
                description="Be patient and staff will be right with you.",
                timestamp=datetime.datetime.now()
            )
            embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            embed.set_footer(text="xrypton.vercel.app", icon_url=interaction.client.user.avatar.url if interaction.client.user.avatar else None)
            message = await channel.send(
                embed=embed, content=f"Welcome {interaction.user.mention}", view=view
            )
            await message.pin()

    # ---------- Commands ----------
    @group(aliases=["tickets"], invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def ticket(self, ctx: Context) -> Message:
        """Manage support tickets."""
        return await ctx.send_help(ctx.command)

    @ticket.group(name="panel", aliases=["message", "link"], invoke_without_command=True, example="1234567890")
    @has_permissions(manage_channels=True)
    async def ticket_panel(self, ctx: Context, message: Message) -> Message:
        """Set the ticket panel message."""
        if message.guild != ctx.guild:
            return await ctx.warn("The message must be in this server!")
        if message.author != ctx.guild.me:
            return await ctx.warn("The message must be from me!")

        await self.bot.pool.execute(
            """
            INSERT INTO ticket_config (guild_id, channel_id, message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id)
            DO UPDATE SET channel_id = EXCLUDED.channel_id, message_id = EXCLUDED.message_id
            """,
            ctx.guild.id, message.channel.id, message.id
        )
        return await ctx.approve(
            f"Successfully set that [`message`]({message.jump_url}) as a ticket panel. Use `{ctx.clean_prefix}ticket button` to attach buttons"
        )

    @ticket_panel.command(name="remove", aliases=["delete", "del", "rm"])
    @has_permissions(manage_channels=True)
    async def ticket_panel_remove(self, ctx: Context) -> Message:
        """Remove the ticket panel message."""
        record = await self.bot.pool.fetchrow(
            "DELETE FROM ticket_config WHERE guild_id = $1 RETURNING *",
            ctx.guild.id
        )
        if not record:
            return await ctx.warn("The ticket panel hasn't been set yet!")
        message = await self.get_ticket_message(ctx.guild, record, partial=True)
        if message:
            with suppress(HTTPException):
                await message.delete()
        return await ctx.approve("Successfully removed the ticket panel")

    @ticket.group(name="button", aliases=["option"], invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def ticket_button(self, ctx: Context) -> Message:
        """Control the buttons on the panel."""
        return await ctx.send_help(ctx.command)

    @ticket_button.command(name="add", aliases=["button"], example="Support")
    @has_permissions(manage_channels=True)
    async def ticket_button_add(self, ctx: Context, *, label: str) -> Message:
        """Add a button to the ticket panel."""
        # Parse label and flags
        # We'll use a simple approach: split label and flags
        # The original uses ButtonFlags().find(ctx, label) but we'll implement inline
        # Since we have the flags class, we can use it.
        # We'll assume the flags are at the end of the string after the label.
        # Simpler: we'll just use the flags as given.
        # The original used: label, flags = await ButtonFlags().find(ctx, label)
        # We'll replicate by parsing manually or using the converter.
        # We'll use the converter from discord.py; it should work.
        try:
            # We'll use a simple hack: the command signature has flags as a parameter, but here it's a single string.
            # Actually, in the original, the command is: ticket button add, then label is a string that contains the label and flags.
            # So we need to extract the flags from the label string. We'll use the same approach as the original.
            # But since we have the FlagConverter, we can call its find method if available.
            # We'll just use the parameters as is and assume flags are passed as --key value.
            # For simplicity, we'll not implement flag parsing here; the original had a custom find.
            # We'll just use the label as is and ignore flags for now, or we can implement a simple parser.
            # However, the user expects the same functionality, so we'll parse.
            # We'll split by -- and parse.
            # Actually, we'll use the Flags class's conversion? It's easier to just use the command with flags as separate options.
            # But the original command uses a single string for label and flags.
            # I'll implement a simple parser that extracts --style, --emoji.
            # For now, I'll copy the parsing logic from the original's find method if I had it.
            # Let's implement a basic parser.
            style = "green"
            emoji = None
            # remove flags from label
            import re
            # Find flags like --style blurple or --emoji 👍
            style_match = re.search(r'--style\s+(\w+)', label)
            if style_match:
                style = style_match.group(1)
                label = label.replace(style_match.group(0), '').strip()
            emoji_match = re.search(r'--emoji\s+([^\s]+)', label)
            if emoji_match:
                emoji = emoji_match.group(1)
                label = label.replace(emoji_match.group(0), '').strip()
        except Exception:
            style = "green"
            emoji = None

        if not label:
            return await ctx.warn("You must provide a button label!")

        config = await self.bot.pool.fetchrow("SELECT * FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                "The ticket panel hasn't been set yet!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )

        message = await self.get_ticket_message(ctx.guild, config)
        if not message:
            return await ctx.warn(
                "The ticket panel no longer exists!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )

        view = View()
        if message.components and isinstance(message.components[0], ActionRow):
            for child in message.components[0].children:
                if isinstance(child, ButtonComponent):
                    view.add_item(
                        Button(
                            style=child.style,
                            label=child.label,
                            disabled=child.disabled,
                            custom_id=child.custom_id,
                            emoji=child.emoji,
                        )
                    )

        identifier = secrets.token_urlsafe(13)
        button_style = getattr(ButtonStyle, style, ButtonStyle.green)
        view.add_item(
            Button(
                style=button_style,
                label=label,
                emoji=emoji,
                custom_id=f"{identifier}:ticket_create",
            )
        )

        try:
            await message.edit(view=view)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your **button**!",
                codeblock(exc.text)
            )

        await self.bot.pool.execute(
            """
            INSERT INTO ticket_button (identifier, guild_id)
            VALUES ($1, $2)
            ON CONFLICT (identifier, guild_id) DO NOTHING
            """,
            identifier, ctx.guild.id
        )
        return await ctx.approve(
            f"Added button **{emoji or ''} {label}** with identifier [`{identifier}`]({message.jump_url}). Use the identifier above to change what this button does."
        )

    @ticket_button.command(name="remove", aliases=["delete", "del", "rm"], example="1234567890")
    @has_permissions(manage_channels=True)
    async def ticket_button_remove(self, ctx: Context, identifier: str) -> Message:
        """Remove a button from the ticket panel."""
        config = await self.bot.pool.fetchrow("SELECT * FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                "The ticket panel hasn't been set yet!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )

        message = await self.get_ticket_message(ctx.guild, config)
        if not message:
            return await ctx.warn(
                "The ticket panel no longer exists!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )

        result = await self.bot.pool.execute(
            "DELETE FROM ticket_button WHERE guild_id = $1 AND identifier = $2",
            ctx.guild.id, identifier
        )
        if result == "DELETE 0":
            return await ctx.warn(
                f"A button with identifier [`{identifier}`]({message.jump_url}) doesn't exist!"
            )

        view = View()
        if message.components and isinstance(message.components[0], ActionRow):
            for child in message.components[0].children:
                if isinstance(child, ButtonComponent):
                    if child.custom_id and child.custom_id.split(":", 1)[0] == identifier:
                        continue
                    view.add_item(
                        Button(
                            style=child.style,
                            label=child.label,
                            disabled=child.disabled,
                            custom_id=child.custom_id,
                            emoji=child.emoji,
                        )
                    )

        try:
            await message.edit(view=view)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your **panel**!",
                codeblock(exc.text)
            )

        return await ctx.approve(
            f"Removed button with identifier [`{identifier}`]({message.jump_url})"
        )

    @ticket_button.command(name="list", aliases=["ls"])
    @has_permissions(manage_channels=True)
    async def ticket_button_list(self, ctx: Context) -> Message:
        """View all button identifiers."""
        config = await self.bot.pool.fetchrow("SELECT * FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                "The ticket panel hasn't been set yet!",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )

        message = await self.get_ticket_message(ctx.guild, config)
        if not message:
            return await ctx.warn(
                f"The ticket panel no longer exists! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )

        if not message.components or not isinstance(message.components[0], ActionRow):
            return await ctx.warn("No buttons have been added yet!")

        children = message.components[0].children
        records = await self.bot.pool.fetch(
            "SELECT identifier FROM ticket_button WHERE guild_id = $1", ctx.guild.id
        )
        buttons = []
        for record in records:
            button = find(
                lambda b: b.custom_id and b.custom_id.startswith(record["identifier"]),
                children
            )
            if button and isinstance(button, ButtonComponent):
                buttons.append(f"**{button.emoji or ''} {button.label}** (`{record['identifier']}`)")

        if not buttons:
            return await ctx.warn("No buttons have been added yet!")

        # Create paginator with embed
        embed = Embed(title="Ticket Buttons", description="\n".join(buttons))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    @ticket.command(name="open", aliases=["welcome", "opening"], example="{user.mention} welcome!")
    @has_permissions(manage_channels=True)
    async def ticket_welcome(self, ctx: Context, identifier: str, *, script: Script) -> Message:
        """Set the opening message for a ticket."""
        record = await self.bot.pool.fetchrow(
            "SELECT * FROM ticket_button WHERE guild_id = $1 AND identifier = $2",
            ctx.guild.id, identifier
        )
        if not record:
            return await ctx.warn(
                f"A button with identifier `{identifier}` doesn't exist! Use `{ctx.clean_prefix}ticket button list` to view all identifiers"
            )

        await self.bot.pool.execute(
            "UPDATE ticket_button SET template = $3 WHERE guild_id = $1 AND identifier = $2",
            ctx.guild.id, identifier, script.template
        )
        return await ctx.approve(
            f"Now sending {vowel(script.format)} message to tickets from button `{identifier}`"
        )

    @ticket.group(name="category", aliases=["redirect"], invoke_without_command=True, example="1234567890 tickets")
    @has_permissions(manage_channels=True)
    async def ticket_category(self, ctx: Context, identifier: str, *, channel: CategoryChannel) -> Message:
        """Set the category for a ticket button."""
        record = await self.bot.pool.fetchrow(
            "SELECT * FROM ticket_button WHERE guild_id = $1 AND identifier = $2",
            ctx.guild.id, identifier
        )
        if not record:
            return await ctx.warn(
                f"A button with identifier `{identifier}` doesn't exist! Use `{ctx.clean_prefix}ticket button list` to view all identifiers"
            )

        await self.bot.pool.execute(
            "UPDATE ticket_button SET category_id = $3 WHERE guild_id = $1 AND identifier = $2",
            ctx.guild.id, identifier, channel.id
        )
        return await ctx.approve(
            f"Now redirecting tickets from button `{identifier}` to [`{channel.name}`]({channel.jump_url})"
        )

    @ticket.command(name="name", aliases=["channel"], example="example")
    @has_permissions(manage_channels=True)
    async def ticket_name(self, ctx: Context, *, name: Range[str, 1, 100]) -> Message:
        """Set the name for new ticket channels."""
        result = await self.bot.pool.execute(
            "UPDATE ticket_config SET channel_name = $2 WHERE guild_id = $1",
            ctx.guild.id, name
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"The ticket panel hasn't been set yet! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )
        return await ctx.approve(
            f"Now using `{name}` for ticket channel names. It will appear as **{parse(name, [ctx.guild, ctx.author])}**"
        )

    @ticket_category.command(name="remove", aliases=["delete", "del", "rm"], example="1234567890")
    @has_permissions(manage_channels=True)
    async def ticket_category_remove(self, ctx: Context, identifier: str) -> Message:
        """Remove the category for a ticket button."""
        record = await self.bot.pool.fetchrow(
            "SELECT * FROM ticket_button WHERE guild_id = $1 AND identifier = $2",
            ctx.guild.id, identifier
        )
        if not record:
            return await ctx.warn(
                f"A button with identifier `{identifier}` doesn't exist! Use `{ctx.clean_prefix}ticket button list` to view all identifiers!"
            )

        await self.bot.pool.execute(
            "UPDATE ticket_button SET category_id = NULL WHERE guild_id = $1 AND identifier = $2",
            ctx.guild.id, identifier
        )
        return await ctx.approve(
            f"No longer redirecting tickets from button `{identifier}`"
        )

    @ticket.group(name="staff", invoke_without_command=True, example="@mod")
    @has_permissions(manage_channels=True)
    async def ticket_staff(self, ctx: Context, *, role: Role) -> Message:
        """Allow a role to see new tickets."""
        # Get current staff_ids
        config = await self.bot.pool.fetchrow("SELECT staff_ids FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                f"The ticket panel hasn't been set yet! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )
        staff_ids = json.loads(config["staff_ids"]) if config["staff_ids"] else []
        if role.id in staff_ids:
            return await ctx.warn(f"{role.mention} is already allowed!")
        staff_ids.append(role.id)
        await self.bot.pool.execute(
            "UPDATE ticket_config SET staff_ids = $2 WHERE guild_id = $1",
            ctx.guild.id, json.dumps(staff_ids)
        )
        return await ctx.approve(f"Now allowing {role.mention} to see new tickets")

    @ticket_staff.command(name="remove", aliases=["delete", "del", "rm"], example="@mod")
    @has_permissions(manage_channels=True)
    async def ticket_staff_remove(self, ctx: Context, *, role: Role) -> Message:
        """Disallow a role from seeing tickets."""
        config = await self.bot.pool.fetchrow("SELECT staff_ids FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                f"The ticket panel hasn't been set yet! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )
        staff_ids = json.loads(config["staff_ids"]) if config["staff_ids"] else []
        if role.id not in staff_ids:
            return await ctx.warn(f"{role.mention} already isn't allowed!")
        staff_ids.remove(role.id)
        await self.bot.pool.execute(
            "UPDATE ticket_config SET staff_ids = $2 WHERE guild_id = $1",
            ctx.guild.id, json.dumps(staff_ids)
        )
        return await ctx.approve(f"No longer allowing {role.mention} to see new tickets")

    @ticket_staff.command(name="list", aliases=["ls"])
    @has_permissions(manage_channels=True)
    async def ticket_staff_list(self, ctx: Context) -> Message:
        """View all roles which can see tickets."""
        config = await self.bot.pool.fetchrow("SELECT staff_ids FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                f"The ticket panel hasn't been set yet! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )
        staff_ids = json.loads(config["staff_ids"]) if config["staff_ids"] else []
        roles = []
        for rid in staff_ids:
            role = ctx.guild.get_role(rid)
            if role:
                roles.append(f"{role.mention} (`{role.id}`)")
        if not roles:
            return await ctx.warn("No roles have been allowed yet!")
        embed = Embed(title="Ticket Staff", description="\n".join(roles))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    @ticket.group(name="ignore", aliases=["blacklist"], invoke_without_command=True, example="@ignore")
    @has_permissions(manage_channels=True)
    async def ticket_ignore(self, ctx: Context, *, target: Union[Role, Member]) -> Message:
        """Prevent a role or member from creating tickets."""
        config = await self.bot.pool.fetchrow("SELECT blacklisted_ids FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                f"The ticket panel hasn't been set yet! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )
        blacklisted = json.loads(config["blacklisted_ids"]) if config["blacklisted_ids"] else []
        if target.id in blacklisted:
            return await ctx.warn(f"{target.mention} is already blacklisted!")
        blacklisted.append(target.id)
        await self.bot.pool.execute(
            "UPDATE ticket_config SET blacklisted_ids = $2 WHERE guild_id = $1",
            ctx.guild.id, json.dumps(blacklisted)
        )
        return await ctx.approve(f"No longer allowing {target.mention} to create tickets")

    @ticket_ignore.command(name="remove", aliases=["delete", "del", "rm"], example="@ignore")
    @has_permissions(manage_channels=True)
    async def ticket_ignore_remove(self, ctx: Context, *, target: Union[Role, Member]) -> Message:
        """Allow an entity to make tickets again."""
        config = await self.bot.pool.fetchrow("SELECT blacklisted_ids FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                f"The ticket panel hasn't been set yet! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )
        blacklisted = json.loads(config["blacklisted_ids"]) if config["blacklisted_ids"] else []
        if target.id not in blacklisted:
            return await ctx.warn(f"{target.mention} isn't blacklisted!")
        blacklisted.remove(target.id)
        await self.bot.pool.execute(
            "UPDATE ticket_config SET blacklisted_ids = $2 WHERE guild_id = $1",
            ctx.guild.id, json.dumps(blacklisted)
        )
        return await ctx.approve(f"Now allowing {target.mention} to create tickets")

    @ticket_ignore.command(name="list", aliases=["ls"])
    @has_permissions(manage_channels=True)
    async def ticket_ignore_list(self, ctx: Context) -> Message:
        """View all blacklisted entities."""
        config = await self.bot.pool.fetchrow("SELECT blacklisted_ids FROM ticket_config WHERE guild_id = $1", ctx.guild.id)
        if not config:
            return await ctx.warn(
                f"The ticket panel hasn't been set yet! Use `{ctx.clean_prefix}ticket panel <message>` to set it"
            )
        blacklisted = json.loads(config["blacklisted_ids"]) if config["blacklisted_ids"] else []
        targets = []
        for tid in blacklisted:
            target = ctx.guild.get_member(tid) or ctx.guild.get_role(tid)
            if target:
                targets.append(f"{target.mention} (`{target.id}`)")
        if not targets:
            return await ctx.warn("No members have been blacklisted yet!")
        embed = Embed(title="Ticket Blacklisted", description="\n".join(targets))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    @ticket.command(name="add", aliases=["allow"], example="@x")
    @in_ticket()
    @has_permissions(manage_channels=True)
    async def ticket_add(self, ctx: Context, target: Union[Member, Role]) -> Message:
        """Add a role or member to the ticket."""
        await ctx.channel.set_permissions(
            target,
            view_channel=True,
            read_messages=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            mention_everyone=False,
            reason=f"Granted access by {ctx.author} ({ctx.author.id})."
        )
        return await ctx.approve(f"Now allowing {target.mention} to see this ticket")

    @ticket.command(name="deny", aliases=["block"], example="@x")
    @in_ticket()
    @has_permissions(manage_channels=True)
    async def ticket_deny(self, ctx: Context, target: Union[Member, Role]) -> Message:
        """Remove a role or member's access to the ticket."""
        await ctx.channel.set_permissions(
            target,
            view_channel=False,
            read_messages=False,
            read_message_history=False,
            send_messages=False,
            attach_files=False,
            embed_links=False,
            mention_everyone=False,
            reason=f"Denied access by {ctx.author} ({ctx.author.id})."
        )
        return await ctx.approve(f"Now denying {target.mention} access to this ticket")

    @ticket.command(name="remove", aliases=["hide"], example="@x")
    @in_ticket()
    @has_permissions(manage_channels=True)
    async def ticket_remove(self, ctx: Context, target: Union[Role, Member]) -> Message:
        """Remove a role or member from the ticket."""
        await ctx.channel.set_permissions(
            target,
            overwrite=None,
            reason=f"Denied access by {ctx.author} ({ctx.author.id})."
        )
        return await ctx.approve(f"No longer allowing {target.mention} to see this ticket")

    @ticket.command(name="close", aliases=["end"], example="Resolved", brief="manage channels")
    @in_ticket()
    @can_close_ticket()
    async def ticket_close(self, ctx: Context, reason: str = "No reason provided.") -> None:
        """Close an open ticket and forward the transcript."""
        transcript = await self.create_ticket_transcript(ctx.channel, reason)
        member_ids = await self.get_channel_member_ids(ctx.channel)
        channel_config = await self.bot.pool.fetchrow(
            "SELECT * FROM ticket_logs WHERE guild_id = $1", ctx.guild.id
        )

        if not channel_config:
            return await ctx.warn(f"Ticket logs haven't been set, run ``{ctx.clean_prefix}ticket logs`` to set it.")

        log_id = secrets.token_hex(8)
        logs_directory = "./tickets"  # change to your preferred path
        os.makedirs(logs_directory, exist_ok=True)
        file_path = f"{logs_directory}/{log_id}.json"
        member_ids_file_path = f"{logs_directory}/{log_id}_ids.json"

        logging_channel_id = channel_config["channel_id"]
        logging_channel = self.bot.get_channel(logging_channel_id)

        await asyncio.gather(
            self.write_json(file_path, transcript),
            self.write_json(member_ids_file_path, member_ids)
        )

        await ctx.approve(
            f"Your logs can be found here: https://xrypton.vercel.app/tickets/{log_id}"  # adjust URL if needed
        )
        await asyncio.sleep(5)
        await self.bot.pool.execute(
            "DELETE FROM ticket_open WHERE guild_id = $1 AND channel_id = $2",
            ctx.guild.id, ctx.channel.id
        )

        embed = Embed(
            title="Ticket Closed",
            description=f"Your logs can be found here: https://xrypton.vercel.app/tickets/{log_id}\n\n"
                        f"⚠️ Ticket logging is currently in beta, please report any bugs you come across.",
            timestamp=datetime.datetime.now()
        )

        await ctx.channel.delete(
            reason=f"Closed by {ctx.author} ({ctx.author.id}) - {reason}"
        )
        if logging_channel:
            await logging_channel.send(embed=embed)

    @ticket.command(name="setup")
    @has_permissions(manage_channels=True)
    async def ticket_setup(self, ctx: Context):
        """Automatically create a panel, ticket message, and category channel for your tickets."""
        identifier = secrets.token_urlsafe(13)

        embed = Embed(
            description="Click on the button below this message to create a ticket.",
            title="Create a ticket",
        )
        embed.set_author(
            name=ctx.guild.name,
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )

        category = await ctx.guild.create_category(name="Tickets")
        channel = await category.create_text_channel(name="tickets")

        view = View()
        create_ticket_button = Button(
            style=ButtonStyle.grey,
            label="Create Ticket",
            custom_id=f"{identifier}:ticket_create",
            emoji="🎟️",
        )
        view.add_item(create_ticket_button)

        try:
            message = await channel.send(embed=embed, view=view)
        except HTTPException as exc:
            return await ctx.warn(f"Something went wrong with sending the message: {exc.text}")

        await self.bot.pool.execute(
            """
            INSERT INTO ticket_config (guild_id, channel_id, message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id)
            DO UPDATE SET channel_id = EXCLUDED.channel_id, message_id = EXCLUDED.message_id
            """,
            ctx.guild.id, channel.id, message.id
        )

        await self.bot.pool.execute(
            """
            INSERT INTO ticket_button (guild_id, identifier, category_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (identifier, guild_id) DO NOTHING
            """,
            ctx.guild.id, identifier, category.id
        )

        await ctx.approve(
            f"Automatically set up ticket button with identifier `{identifier}`, ticket panel has been sent to {channel.mention}, and category has been set to `{category.name}`."
        )

    @ticket.command(name="logs", example="#logs")
    @has_permissions(manage_channels=True)
    async def ticket_logs(self, ctx: Context, channel: TextChannel):
        """Set the channel in which ticket logs will be sent to."""
        await self.bot.pool.execute(
            """
            INSERT INTO ticket_logs (guild_id, channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET channel_id = EXCLUDED.channel_id
            """,
            ctx.guild.id, channel.id
        )
        await ctx.approve(f"Ticket logs have been set to {channel.mention}!")