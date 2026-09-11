import json
import logging
from contextlib import suppress
from typing import Dict, Any, Optional, List

import discord
from discord import (
    Embed,
    Guild,
    Interaction,
    InteractionResponded,
    InviteTarget,
    Member,
    Role,
    SelectOption,
    VoiceChannel,
    WebhookMessage,
)
from discord.ui import Button, Select, View, button
from discord.utils import format_dt
from discord.ext.commands import Cog, group, has_permissions

from base.Xrypton import Bot
from base.context import Context
from base.config import EMOJIS, COLORS

log = logging.getLogger("evict/voicemaster")

# ===== Helpers =====
class Plural:
    """Simple plural helper."""
    def __init__(self, count: int, code: bool = False):
        self.count = count
        self.code = code

    def __str__(self):
        return f"`{self.count}`" if self.code else str(self.count)

    def __format__(self, format_spec: str) -> str:
        return str(self)

# ===== Activity types (from tools.converters.basic) =====
activity_types = [
    {"id": "755600276941176913", "name": "Watch Together", "emoji": "🎥"},
    {"id": "773336526917861400", "name": "Blazing 8s", "emoji": "🎱"},
    {"id": "832025144389533716", "name": "Betrayal.io", "emoji": "🕵️"},
    {"id": "832012586023256104", "name": "Doodle Crew", "emoji": "🎨"},
    {"id": "832012774040141894", "name": "Letter Tile", "emoji": "🔤"},
    {"id": "832012682520428574", "name": "Word Snacks", "emoji": "🍿"},
    {"id": "832025114077298718", "name": "Spyfall", "emoji": "🕵️"},
    {"id": "832025144389533716", "name": "Bomb Party", "emoji": "💣"},
    {"id": "832012813739696159", "name": "Chess", "emoji": "♟️"},
    {"id": "832012894068801546", "name": "Checkers", "emoji": "🏁"},
    {"id": "832012938278932550", "name": "Putt Party", "emoji": "⛳"},
    {"id": "832013108234289153", "name": "Poker Night", "emoji": "🃏"},
    {"id": "832012973912719360", "name": "Scrabble", "emoji": "📝"},
    {"id": "832012963173392404", "name": "Trivia", "emoji": "🧠"},
    {"id": "832012889399689267", "name": "Land.io", "emoji": "🌍"},
    {"id": "832012857923203093", "name": "Sketch Heads", "emoji": "✏️"},
    {"id": "832012924670541834", "name": "Pictionary", "emoji": "🖼️"},
    {"id": "832012869802442762", "name": "Meme Maestro", "emoji": "😂"},
]

# ===== Fallback emojis for interface (if your config doesn't have EMOJIS.INTERFACE) =====
DEFAULT_INTERFACE_EMOJIS = {
    "LOCK": "🔒",
    "UNLOCK": "🔓",
    "GHOST": "👻",
    "REVEAL": "🔎",
    "CLAIM": "📌",
    "DISCONNECT": "🔌",
    "ACTIVITY": "🎮",
    "INFORMATION": "ℹ️",
    "INCREASE": "⬆️",
    "DECREASE": "⬇️",
}

def get_interface_emoji(key: str, custom_emojis: dict = None) -> str:
    """Get emoji from custom or fallback."""
    if custom_emojis and key in custom_emojis:
        return custom_emojis[key]
    return DEFAULT_INTERFACE_EMOJIS.get(key, "❓")

# ===== Disconnect Members Select =====
class DisconnectMembers(Select):
    def __init__(self, member: Member):
        self.member: Member = member
        self.guild: Guild = member.guild
        self.channel: VoiceChannel = member.voice.channel
        super().__init__(
            placeholder="Choose members...",
            min_values=1,
            max_values=len(self.channel.members),
            options=[
                SelectOption(
                    value=str(member.id),
                    label=f"{member} ({member.id})",
                    emoji="👤",
                )
                for member in self.channel.members
            ],
        )

    async def callback(self, interaction: Interaction) -> WebhookMessage:
        await interaction.response.defer()

        disconnected, failed = 0, 0

        for member_id in self.values:
            if member := self.guild.get_member(int(member_id)):
                if member == self.member:
                    failed += 1
                elif not member.voice or member.voice.channel != self.channel:
                    failed += 1
                else:
                    try:
                        await member.move_to(None)
                    except:
                        failed += 1
                    else:
                        disconnected += 1

        return await interaction.approve(
            f"Successfully **disconnected** {Plural(disconnected, code=True)} member(s) (`{failed}` failed)"
        )

# ===== Activity Selection =====
class ActivitySelection(Select):
    def __init__(self, member: Member):
        self.member: Member = member
        self.guild: Guild = member.guild
        self.channel: VoiceChannel = member.voice.channel
        super().__init__(
            placeholder="Choose an activity...",
            min_values=1,
            max_values=1,
            options=[
                SelectOption(
                    value=activity["id"],
                    label=activity["name"],
                    emoji=activity["emoji"],
                )
                for activity in activity_types
            ],
        )

    async def callback(self, interaction: Interaction) -> WebhookMessage:
        await interaction.response.defer()

        try:
            invite = await self.channel.create_invite(
                max_age=0,
                target_type=InviteTarget.embedded_application,
                target_application_id=int(self.values[0]),
                reason=f"VoiceMaster: {self.member} started an activity",
            )
        except Exception:
            return await interaction.warn(
                "Failed to create an **invite** for the selected **activity**!"
            )

        return await interaction.followup.send(
            f"[Click here to join the activity!]({invite})",
            ephemeral=True,
        )

# ===== Voicemaster Dropdown =====
class VoicemasterDropdown(Select):
    def __init__(self, interface: 'Interface'):
        self.interface = interface
        super().__init__(
            placeholder="Select task...",
            min_values=1,
            max_values=1,
            options=[]
        )

    async def setup_options(self, guild_id: int):
        """Setup dropdown options with proper emojis"""
        custom_emojis = await self.interface.get_custom_emojis(guild_id)
        
        options = [
            ("LOCK", "Lock", "the voice channel"),
            ("UNLOCK", "Unlock", "the voice channel"),
            ("GHOST", "Hide", "the voice channel"),
            ("REVEAL", "Reveal", "the voice channel"),
            ("CLAIM", "Claim", "the voice channel"),
            ("DISCONNECT", "Disconnect", "a member"),
            ("ACTIVITY", "Start", "a new voice channel activity"),
            ("INFORMATION", "View", "channel information"),
            ("INCREASE", "Increase", "the user limit"),
            ("DECREASE", "Decrease", "the user limit")
        ]
        
        for button_type, label, description in options:
            emoji = get_interface_emoji(button_type, custom_emojis)
            
            # Attempt to convert to PartialEmoji if it's a custom emoji string
            if isinstance(emoji, str) and emoji.startswith('<') and emoji.endswith('>'):
                try:
                    emoji_id = int(emoji.split(':')[-1][:-1])
                    emoji_name = emoji.split(':')[1]
                    emoji = discord.PartialEmoji(name=emoji_name, id=emoji_id, animated=False)
                except (ValueError, IndexError):
                    emoji = DEFAULT_INTERFACE_EMOJIS.get(button_type, "❓")
            
            self.options.append(
                SelectOption(
                    label=f"{label} {description}",
                    value=button_type.lower(),
                    emoji=emoji
                )
            )

    async def callback(self, interaction: Interaction):
        if not await self.interface.interaction_check(interaction):
            return
            
        action = self.values[0].lower()
        
        method = getattr(self.interface.__class__, action)
        await method(self.interface, interaction, None)
            
        new_dropdown = VoicemasterDropdown(self.interface)
        await new_dropdown.setup_options(interaction.guild_id)
        new_view = View(timeout=None)
        new_view.add_item(new_dropdown)
        
        await interaction.message.edit(view=new_view)

# ===== Main Interface View =====
class Interface(View):
    def __init__(self, bot: Bot):
        self.bot: Bot = bot
        super().__init__(timeout=None)

    async def get_layout(self, guild_id: int) -> str:
        """Get interface layout preference"""
        try:
            layout = await self.bot.pool.fetchval(
                """
                SELECT interface_layout 
                FROM voicemaster_configuration 
                WHERE guild_id = $1
                """,
                guild_id
            ) or 'default'
            return layout
        except Exception:
            return 'default'

    def create_dropdown_embed(self, guild):
        """Create the dropdown style interface embed"""
        embed = Embed(
            title="Voicemaster Control Menu",
            description="Control your voice channel using the dropdown below",
            color=0x2B2D31
        )
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar)
        return embed

    async def send(self, **kwargs) -> discord.Message:
        """Send the interface and store the message"""
        if not hasattr(self, 'channel'):
            raise ValueError("Channel not created. Call create_channel() first")

        layout = await self.get_layout(self.channel.guild.id)
        custom_emojis = await self.get_custom_emojis(self.channel.guild.id)
        
        if 'embed' in kwargs and kwargs['embed'].description:
            emoji_map = {
                'lock': get_interface_emoji('LOCK', custom_emojis),
                'unlock': get_interface_emoji('UNLOCK', custom_emojis),
                'ghost': get_interface_emoji('GHOST', custom_emojis),
                'reveal': get_interface_emoji('REVEAL', custom_emojis),
                'claim': get_interface_emoji('CLAIM', custom_emojis),
                'disconnect': get_interface_emoji('DISCONNECT', custom_emojis),
                'activity': get_interface_emoji('ACTIVITY', custom_emojis),
                'information': get_interface_emoji('INFORMATION', custom_emojis),
                'increase': get_interface_emoji('INCREASE', custom_emojis),
                'decrease': get_interface_emoji('DECREASE', custom_emojis),
            }
            
            description = kwargs['embed'].description
            import re
            for key, emoji in emoji_map.items():
                pattern = r'\{' + re.escape(key) + r'\}?'
                description = re.sub(pattern, str(emoji), description)
                
            kwargs['embed'].description = description
        
        if layout == 'dropdown':
            if 'embed' not in kwargs:
                kwargs['embed'] = self.create_dropdown_embed(self.channel.guild)
            
            dropdown = VoicemasterDropdown(self)
            await dropdown.setup_options(self.channel.guild.id)
            
            view = View(timeout=None)
            view.add_item(dropdown)
            kwargs['view'] = view
        else:
            kwargs['view'] = self
        
        self.message = await self.channel.send(**kwargs)
        return self.message

    async def get_custom_emojis(self, guild_id: int) -> dict:
        """Get custom emojis from database"""
        try:
            emojis_json = await self.bot.pool.fetchval(
                """
                SELECT interface_emojis 
                FROM voicemaster_configuration 
                WHERE guild_id = $1
                """,
                guild_id
            ) or '{}'
            return json.loads(emojis_json)
        except Exception:
            return {}

    async def setup_buttons(self, guild_id: int):
        """Setup button emojis with custom ones where available"""
        custom_emojis = await self.get_custom_emojis(guild_id)
        
        for child in self.children:
            if isinstance(child, Button):
                button_type = child.custom_id.split(":")[1].upper()
                emoji = get_interface_emoji(button_type, custom_emojis)
                
                if isinstance(emoji, str) and emoji.startswith('<') and emoji.endswith('>'):
                    try:
                        emoji_id = int(emoji.split(':')[-1][:-1])
                        emoji_name = emoji.split(':')[1]
                        child.emoji = discord.PartialEmoji(name=emoji_name, id=emoji_id, animated=False)
                    except (ValueError, IndexError):
                        child.emoji = DEFAULT_INTERFACE_EMOJIS.get(button_type, "❓")
                else:
                    child.emoji = emoji

    async def create_channel(self, category, author):
        """Create the interface channel"""
        self.channel = await category.create_text_channel(
            "interface",
            reason=f"{author} setup VoiceMaster"
        )
        return self.channel

    def create_embed(self, guild):
        """Create the interface embed"""
        embed = Embed(
            title="VoiceMaster Interface",
            description="Click the buttons below to control your voice channel",
        )
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon,
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar)

        embed.add_field(
            name="**Button Usage**",
            value=(
                f"{DEFAULT_INTERFACE_EMOJIS['LOCK']} — [`Lock`] the voice channel\n"
                f"{DEFAULT_INTERFACE_EMOJIS['UNLOCK']} — [`Unlock`] the voice channel\n"
                f"{DEFAULT_INTERFACE_EMOJIS['GHOST']} — [`Ghost`] the voice channel\n"
                f"{DEFAULT_INTERFACE_EMOJIS['REVEAL']} — [`Reveal`] the voice channel\n"
                f"{DEFAULT_INTERFACE_EMOJIS['CLAIM']} — [`Claim`] the voice channel\n"
                f"{DEFAULT_INTERFACE_EMOJIS['DISCONNECT']} — [`Disconnect`] a member\n"
                f"{DEFAULT_INTERFACE_EMOJIS['ACTIVITY']} — [`Start`] a new voice channel activity\n"
                f"{DEFAULT_INTERFACE_EMOJIS['INFORMATION']} — [`View`] channel information\n"
                f"{DEFAULT_INTERFACE_EMOJIS['INCREASE']} — [`Increase`] the user limit\n"
                f"{DEFAULT_INTERFACE_EMOJIS['DECREASE']} — [`Decrease`] the user limit\n"
            ),
        )
        return embed

    async def set_permissions(self, default_role: discord.Role, author: discord.Member):
        """Set default permissions for the interface channel"""
        if hasattr(self, 'channel'):
            await self.channel.set_permissions(
                default_role,
                send_messages=False,
                add_reactions=False,
                reason=f"{author} setup VoiceMaster"
            )

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Check if the user can use the interface"""
        try:
            custom_emojis = await self.get_custom_emojis(interaction.guild_id)
            
            for child in self.children:
                if isinstance(child, Button):
                    button_type = child.custom_id.split(":")[1].upper()
                    emoji = get_interface_emoji(button_type, custom_emojis)
                    
                    if isinstance(emoji, str) and emoji.startswith('<') and emoji.endswith('>'):
                        try:
                            emoji_id = int(emoji.split(':')[-1][:-1])
                            emoji_name = emoji.split(':')[1]
                            child.emoji = discord.PartialEmoji(name=emoji_name, id=emoji_id, animated=False)
                        except (ValueError, IndexError):
                            child.emoji = DEFAULT_INTERFACE_EMOJIS.get(button_type, "❓")
                    else:
                        child.emoji = emoji
                    
        except Exception:
            return False

        if not interaction.user.voice:
            await interaction.warn("You're not connected to a **voice channel**")
            return False

        owner_id = await self.bot.pool.fetchval(
            """
            SELECT owner_id FROM voicemaster_channels
            WHERE channel_id = $1
            """,
            interaction.user.voice.channel.id
        )
        if not owner_id:
            await interaction.warn("You're not in a **VoiceMaster** channel!")
            return False

        if interaction.data["custom_id"] == "voicemaster:claim":
            if interaction.user.id == owner_id:
                await interaction.warn(
                    "You already have **ownership** of this voice channel!"
                )
                return False

            if owner_id in (member.id for member in interaction.user.voice.channel.members):
                await interaction.warn(
                    "You can't claim this **voice channel**, the owner is still active here."
                )
                return False

            return True

        if interaction.user.id != owner_id:
            await interaction.warn("You don't own a **voice channel**!")
            return False

        return True

    @button(
        custom_id="voicemaster:lock",
    )
    async def lock(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Lock your voice channel"""
        await interaction.user.voice.channel.set_permissions(
            interaction.guild.default_role,
            connect=False,
            reason=f"VoiceMaster: {interaction.user} locked voice channel",
        )
        return await interaction.warn("Your **voice channel** has been locked", emoji="🔒")

    @button(
        custom_id="voicemaster:unlock",
    )
    async def unlock(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Unlock your voice channel"""
        await interaction.user.voice.channel.set_permissions(
            interaction.guild.default_role,
            connect=None,
            reason=f"VoiceMaster: {interaction.user} unlocked voice channel",
        )
        return await interaction.warn("Your **voice channel** has been unlocked", emoji="🔓")

    @button(
        custom_id="voicemaster:ghost",
    )
    async def ghost(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Hide your voice channel"""
        await interaction.user.voice.channel.set_permissions(
            interaction.guild.default_role,
            view_channel=False,
            reason=f"VoiceMaster: {interaction.user} made voice channel hidden",
        )
        return await interaction.approve("Your **voice channel** has been hidden")

    @button(
        custom_id="voicemaster:reveal",
    )
    async def reveal(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Reveal your voice channel"""
        await interaction.user.voice.channel.set_permissions(
            interaction.guild.default_role,
            view_channel=None,
            reason=f"VoiceMaster: {interaction.user} revealed voice channel",
        )
        return await interaction.approve("Your **voice channel** has been revealed")

    @button(
        custom_id="voicemaster:claim",
    )
    async def claim(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Claim an inactive voice channel"""
        await self.bot.pool.execute(
            """
            UPDATE voicemaster_channels
            SET owner_id = $2
            WHERE channel_id = $1
            """,
            interaction.user.voice.channel.id,
            interaction.user.id,
        )
        if interaction.user.voice.channel.name.endswith("channel"):
            try:
                await interaction.user.voice.channel.edit(
                    name=f"{interaction.user.display_name}'s channel"
                )
            except Exception:
                pass
        return await interaction.approve("You are now the owner of this **channel**!")

    @button(
        custom_id="voicemaster:disconnect",
    )
    async def disconnect(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Reject a member or role from joining your VC"""
        view = View(timeout=None)
        view.add_item(DisconnectMembers(interaction.user))
        return await interaction.neutral(
            "Select members from the **dropdown** to disconnect.",
            emoji="🔨",
            view=view,
        )

    @button(
        custom_id="voicemaster:activity",
    )
    async def activity(self, interaction: Interaction, button: Button):
        """Start an activity in your voice channel"""
        view = View(timeout=None)
        view.add_item(ActivitySelection(interaction.user))
        return await interaction.neutral(
            "Select an activity from the **dropdown** to start!",
            emoji="🎮",
            view=view,
        )

    @button(
        custom_id="voicemaster:information",
    )
    async def information(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """See current configuration for current voice channel"""
        with suppress(InteractionResponded):
            await interaction.response.defer(ephemeral=True)
        channel = interaction.user.voice.channel
        embed = Embed(
            title=channel.name,
            description=(
                f"**Owner:** {interaction.user} (`{interaction.user.id}`)"
                + "\n**Locked:** "
                + (
                    EMOJIS.APPROVE
                    if channel.permissions_for(interaction.guild.default_role).connect is False
                    else EMOJIS.DENY
                )
                + "\n**Created:** "
                + format_dt(channel.created_at, style="R")
                + f"\n**Bitrate:** {int(channel.bitrate / 1000)}kbps"
                + f"\n**Connected:** `{len(channel.members)}`"
                + (f"/`{channel.user_limit}`" if channel.user_limit else "")
            ),
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar)
        if roles_permitted := [
            target for target, overwrite in channel.overwrites.items()
            if overwrite.connect is True and isinstance(target, Role)
        ]:
            embed.add_field(
                name="**Role Permitted**",
                value=", ".join(role.mention for role in roles_permitted),
                inline=False,
            )
        if members_permitted := [
            target for target, overwrite in channel.overwrites.items()
            if overwrite.connect is True and isinstance(target, Member)
            and target != interaction.user
        ]:
            embed.add_field(
                name="**Member Permitted**",
                value=", ".join(member.mention for member in members_permitted),
                inline=False,
            )
        return await interaction.followup.send(embed=embed, ephemeral=True)

    @button(
        custom_id="voicemaster:increase",
    )
    async def increase(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Increase the user limit of your voice channel"""
        limit = interaction.user.voice.channel.user_limit or 0
        if limit == 99:
            return await interaction.warn("Channel member limit cannot be more than **99 members**!")
        await interaction.user.voice.channel.edit(
            user_limit=limit + 1,
            reason=f"VoiceMaster: {interaction.user} increased voice channel user limit",
        )
        return await interaction.approve(f"Your **voice channel**'s limit has been updated to `{limit + 1}`")

    @button(
        custom_id="voicemaster:decrease",
    )
    async def decrease(self, interaction: Interaction, button: Button) -> WebhookMessage:
        """Decrease the user limit of your voice channel"""
        limit = interaction.user.voice.channel.user_limit or 0
        if limit == 0:
            return await interaction.warn("Channel member limit must be greater than **0 members**")
        await interaction.user.voice.channel.edit(
            user_limit=limit - 1,
            reason=f"VoiceMaster: {interaction.user} decreased voice channel user limit",
        )
        return await interaction.approve(
            "Your **voice channel**'s limit has been **removed**"
            if (limit - 1) == 0
            else f"Your **voice channel**'s limit has been updated to `{limit - 1}`"
        )

    async def get_custom_embed(self, guild_id: int) -> str:
        """Get custom embed code from database"""
        try:
            embed_code = await self.bot.pool.fetchval(
                """
                SELECT interface_embed 
                FROM voicemaster_configuration 
                WHERE guild_id = $1
                """,
                guild_id
            )
            return embed_code
        except Exception:
            return None

    def create_default_embed(self, guild):
        """Create the default interface embed"""
        embed = Embed(
            title="VoiceMaster Interface",
            description="Click the buttons below to control your voice channel",
        )
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon,
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar)
        return embed

class VoiceMaster(Cog):
    """VoiceMaster - create and manage voice channels with a control interface."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.interface = Interface(bot)

    async def cog_load(self) -> None:
        """Create tables and add the persistent view."""
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS voicemaster_configuration (
                guild_id BIGINT PRIMARY KEY,
                interface_layout TEXT DEFAULT 'default',
                interface_emojis TEXT,
                interface_embed TEXT
            )
            """
        )
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS voicemaster_channels (
                channel_id BIGINT PRIMARY KEY,
                owner_id BIGINT,
                guild_id BIGINT
            )
            """
        )
        # Add the persistent view
        self.bot.add_view(self.interface)

    @group(name="voicemaster", aliases=["vm"], invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def voicemaster(self, ctx: Context):
        """Base command for VoiceMaster management."""
        await ctx.send_help(ctx.command)

    @voicemaster.command(name="setup")
    @has_permissions(manage_guild=True)
    async def vm_setup(self, ctx: Context):
        """Set up the VoiceMaster interface in a new category."""
        # Check if already set up
        config = await self.bot.pool.fetchrow(
            "SELECT guild_id FROM voicemaster_configuration WHERE guild_id = $1",
            ctx.guild.id
        )
        if config:
            return await ctx.warn("VoiceMaster is already set up in this server.")

        # Create category and channel
        category = await ctx.guild.create_category("VoiceMaster")
        interface = Interface(self.bot)
        channel = await interface.create_channel(category, ctx.author)
        await interface.set_permissions(ctx.guild.default_role, ctx.author)

        # Send the interface message
        embed = interface.create_embed(ctx.guild)
        await interface.send(embed=embed)

        # Save configuration
        await self.bot.pool.execute(
            """
            INSERT INTO voicemaster_configuration (guild_id, interface_layout)
            VALUES ($1, $2)
            """,
            ctx.guild.id, 'default'
        )

        await ctx.approve(f"VoiceMaster set up in {channel.mention}!")

    @voicemaster.command(name="layout")
    @has_permissions(manage_guild=True)
    async def vm_layout(self, ctx: Context, layout: str):
        """Set the interface layout: default or dropdown."""
        if layout.lower() not in ('default', 'dropdown'):
            return await ctx.warn("Layout must be `default` or `dropdown`.")
        await self.bot.pool.execute(
            """
            INSERT INTO voicemaster_configuration (guild_id, interface_layout)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET interface_layout = EXCLUDED.interface_layout
            """,
            ctx.guild.id, layout.lower()
        )
        await ctx.approve(f"Interface layout set to **{layout}**.")

    @voicemaster.command(name="emojis")
    @has_permissions(manage_guild=True)
    async def vm_emojis(self, ctx: Context, action: str, button: str, emoji: str = None):
        """Manage custom emojis for interface buttons."""
        if action not in ('set', 'remove'):
            return await ctx.warn("Action must be `set` or `remove`.")

        valid_buttons = ['lock', 'unlock', 'ghost', 'reveal', 'claim', 'disconnect', 'activity', 'information', 'increase', 'decrease']
        if button.lower() not in valid_buttons:
            return await ctx.warn(f"Invalid button. Valid: {', '.join(valid_buttons)}")

        # Get current emojis
        current = await self.bot.pool.fetchval(
            "SELECT interface_emojis FROM voicemaster_configuration WHERE guild_id = $1",
            ctx.guild.id
        ) or '{}'
        custom = json.loads(current)

        if action == 'set':
            if not emoji:
                return await ctx.warn("You must provide an emoji.")
            custom[button.upper()] = emoji
            await ctx.approve(f"Custom emoji for `{button}` set to {emoji}.")
        else:  # remove
            if button.upper() in custom:
                del custom[button.upper()]
                await ctx.approve(f"Removed custom emoji for `{button}`.")
            else:
                return await ctx.warn(f"No custom emoji set for `{button}`.")

        # Update database
        await self.bot.pool.execute(
            """
            INSERT INTO voicemaster_configuration (guild_id, interface_emojis)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET interface_emojis = EXCLUDED.interface_emojis
            """,
            ctx.guild.id, json.dumps(custom)
        )

    @voicemaster.command(name="settings")
    @has_permissions(manage_guild=True)
    async def vm_settings(self, ctx: Context):
        """View current VoiceMaster settings."""
        config = await self.bot.pool.fetchrow(
            "SELECT interface_layout, interface_emojis, interface_embed FROM voicemaster_configuration WHERE guild_id = $1",
            ctx.guild.id
        )
        if not config:
            return await ctx.warn("VoiceMaster is not set up in this server.")

        embed = Embed(title="VoiceMaster Settings", color=COLORS.neutral)
        embed.add_field(name="Layout", value=config['interface_layout'] or 'default')
        custom_emojis = json.loads(config['interface_emojis'] or '{}')
        if custom_emojis:
            embed.add_field(
                name="Custom Emojis",
                value="\n".join(f"**{k}**: {v}" for k, v in custom_emojis.items()),
                inline=False
            )
        else:
            embed.add_field(name="Custom Emojis", value="None set.")
        await ctx.send(embed=embed)

    @Cog.listener()
    async def on_voice_state_update(self, member: Member, before, after):
        """Handle channel creation/deletion and ownership transfer."""
        pass