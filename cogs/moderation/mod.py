import asyncio
import re
import json
import discord
import datetime
import humanize
import aiohttp
from contextlib import suppress
from datetime import timedelta
from io import BytesIO
from logging import getLogger
from time import perf_counter
from typing import Annotated, Callable, List, Literal, Optional, cast, Union
from zipfile import ZipFile

# --- User bot imports ---
from base.Xrypton import Bot
from base.context import Context
from base.config import EMOJIS, COLORS
from base.managers.paginator import Paginator
from base.managers.EmbedBuilder import EmbedScript

# --- discord.py imports ---
import discord
from discord import (
    AuditLogAction,
    Color,
    Embed,
    Emoji,
    File,
    Guild,
    HTTPException,
    Member,
    Message,
    NotFound,
    NotificationLevel,
    Object,
    PartialEmoji,
    RateLimited,
    Role,
    StageChannel,
    TextChannel,
    Thread,
    User,
    VoiceChannel,
    GuildSticker,
)
from discord.abc import GuildChannel
from discord.ext import commands
from discord.ext.commands import (
    hybrid_command,
    hybrid_group,
    BadArgument,
    BucketType,
    Cog,
    CommandError,
    Greedy,
    MaxConcurrency,
    Range,
    check,
    command,
    cooldown,
    group,
    has_permissions as _dpy_has_permissions,
    max_concurrency,
    parameter,
    Converter,
)
from base.managers.predicates import has_permissions
from discord.utils import MISSING, format_dt, get, utcnow
from humanfriendly import format_timespan
from humanize import precisedelta
from xxhash import xxh64_hexdigest

log = getLogger("xrypton/mod")  # kept for compatibility

# ---- Missing utilities (copied / adapted from Xrypton) ----

def codeblock(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"

def human_join(items, final='and'):
    items = list(items)
    if len(items) == 0:
        return ''
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {final} {items[1]}"
    return f"{', '.join(items[:-1])}, {final} {items[-1]}"

def plural(count, md=''):
    return f"{md}{count}{md}" if md else str(count)

def quietly_delete(message):
    """Delete a message quietly."""
    async def delete():
        try:
            await message.delete()
        except:
            pass
    return delete()

def url_to_mime(url: str):
    """Return (mime_type, extension)."""
    if url.endswith('.png'):
        return 'image/png', '.png'
    elif url.endswith('.jpg') or url.endswith('.jpeg'):
        return 'image/jpeg', '.jpg'
    elif url.endswith('.gif'):
        return 'image/gif', '.gif'
    elif url.endswith('.webp'):
        return 'image/webp', '.webp'
    else:
        return 'application/octet-stream', '.bin'

def unicode_emoji(emoji: str):
    """Return (url, name) for a unicode emoji."""
    # We'll just return a dummy url and the emoji itself as name
    # Actually we need a URL to the emoji image. We can use the discord CDN for unicode?
    # For simplicity, we'll return a placeholder.
    return f"https://cdn.discordapp.com/emojis/unicode/{ord(emoji[0]):x}.png", emoji

async def enlarge_emoji(buffer: bytes, suffix: str):
    """Enlarge an emoji (return bytes and new suffix)."""
    # Simple version: return the buffer as is.
    return buffer, suffix

async def convert_image(buffer: bytes, fmt: str):
    """Convert an image to another format using PIL (if available)."""
    # If PIL not installed, just return buffer
    try:
        from PIL import Image
        img = Image.open(BytesIO(buffer))
        out = BytesIO()
        img.save(out, format=fmt.upper())
        return out.getvalue()
    except:
        return buffer

def strip_roles(member, dangerous=True, reason=None):
    """Remove all roles from a member (used in antinuke)."""
    async def strip():
        if dangerous:
            to_remove = [r for r in member.roles if r.is_assignable() and not r.is_premium_subscriber()]
            if to_remove:
                await member.remove_roles(*to_remove, reason=reason or "Strip roles")
    return strip()

# ---- Converters (from Xrypton) ----

class Duration(Converter):
    async def convert(self, ctx: Context, argument: str) -> timedelta:
        if not argument:
            raise BadArgument("Missing duration")
        # Parse duration like "1d", "2h", "30m", "60s"
        match = re.match(r'(\d+)([smhd])', argument.lower())
        if not match:
            raise BadArgument("Invalid duration format. Use e.g., 1d, 2h, 30m, 60s")
        num = int(match.group(1))
        unit = match.group(2)
        if unit == 's':
            seconds = num
        elif unit == 'm':
            seconds = num * 60
        elif unit == 'h':
            seconds = num * 3600
        elif unit == 'd':
            seconds = num * 86400
        else:
            raise BadArgument("Invalid duration unit")
        if seconds <= 0:
            raise BadArgument("Duration must be positive")
        return timedelta(seconds=seconds)

class PartialAttachment(Converter):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> 'PartialAttachment':
        # If it's a URL, we can download it.
        if argument.startswith('http'):
            async with aiohttp.ClientSession() as session:
                async with session.get(argument) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        return cls(data, argument.split('/')[-1])
        # Else treat as attachment in the message
        if ctx.message.attachments:
            att = ctx.message.attachments[0]
            return cls(await att.read(), att.filename)
        # If no attachment, raise
        raise BadArgument("No attachment provided")
    
    def __init__(self, buffer: bytes = b'', filename: str = ''):
        self.buffer = buffer
        self.filename = filename

    @classmethod
    def fallback(cls):
        return cls()

    def is_image(self):
        return self.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))

    def is_gif(self):
        return self.filename.lower().endswith('.gif')

    def is_archive(self):
        return self.filename.lower().endswith('.zip')

    @property
    def url(self):
        # We can't provide a real URL for an attachment, but we keep for compatibility.
        return self.filename if self.filename else ""

    def is_image(self):
        return self.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))

class StrictMember(Converter):
    async def convert(self, ctx: Context, argument: str):
        # Try to resolve as a Member
        member = await commands.MemberConverter().convert(ctx, argument)
        return member

class StrictUser(Converter):
    async def convert(self, ctx: Context, argument: str):
        # Try to resolve as a User
        user = await commands.UserConverter().convert(ctx, argument)
        return user

class StrictRole(Converter):
    def __init__(self, check_integrated=True, allow_default=False):
        self.check_integrated = check_integrated
        self.allow_default = allow_default

    async def convert(self, ctx: Context, argument: str):
        role = await commands.RoleConverter().convert(ctx, argument)
        if not self.allow_default and role == ctx.guild.default_role:
            raise BadArgument("You cannot use the @everyone role.")
        if self.check_integrated and role.is_integrated():
            raise BadArgument("That role is integrated and cannot be managed.")
        # Also check if the bot can manage it
        if not role.is_assignable():
            raise BadArgument("I cannot manage that role (check hierarchy/permissions).")
        return role

class TouchableMember(Converter):
    def __init__(self, allow_author=False):
        self.allow_author = allow_author

    async def convert(self, ctx: Context, argument: str):
        member = await commands.MemberConverter().convert(ctx, argument)
        if member == ctx.author and not self.allow_author:
            raise BadArgument("You cannot target yourself.")
        if member == ctx.guild.me:
            raise BadArgument("You cannot target the bot.")
        # Check hierarchy: only if the target is not the owner (optional)
        # For simplicity, we'll let the command handle further checks.
        return member

class GoodRole(Converter):
    async def convert(self, ctx: Context, argument: str):
        role = await commands.RoleConverter().convert(ctx, argument)
        # Ensure the role is manageable by the bot and below the bot's top role
        if not role.is_assignable():
            raise BadArgument("I cannot assign that role (check hierarchy/permissions).")
        # Ensure the author can manage it (or we'll let the command handle)
        return role

# ---- Helper classes (from Xrypton) ----

class Mod:
    """Class for moderation checks."""
    @staticmethod
    def is_mod_configured():
        async def predicate(ctx: Context):
            if not ctx.command:
                return False
            # Check if the user has permissions for the command
            required_perms = []
            for check in ctx.command.checks:
                if hasattr(check, '__closure__'):
                    for cell in check.__closure__:
                        if isinstance(cell.cell_contents, dict):
                            for perm, value in cell.cell_contents.items():
                                if value:
                                    required_perms.append(perm)
            if required_perms:
                missing = [p for p in required_perms if not getattr(ctx.author.guild_permissions, p)]
                if missing:
                    await ctx.warn(f"You're missing the **{missing[0].replace('_', ' ').title()}** permission!")
                    return False

            # Check if the mod system is enabled
            check = await ctx.bot.pool.fetchrow(
                "SELECT * FROM mod WHERE guild_id = $1", ctx.guild.id
            )
            if not check:
                await ctx.warn(
                    f"Moderation isn't **enabled** in this server. Enable it using `{ctx.clean_prefix}setme` command"
                )
                return False
            return True
        return check(predicate)

class ModConfig:
    @staticmethod
    async def sendlogs(
        bot: Bot,
        action: str,
        author: Member,
        victim: Union[Member, User],
        reason: str,
        duration: Union[timedelta, int, None] = None,
        role: discord.Role = None
    ):
        """Log moderation actions to the database and channel."""
        try:
            settings = await bot.pool.fetchrow(
                "SELECT * FROM mod WHERE guild_id = $1",
                author.guild.id
            )
            if not settings:
                return

            # Get case number
            res = await bot.pool.fetchrow(
                "SELECT count FROM cases WHERE guild_id = $1", author.guild.id
            )
            if not res:
                await bot.pool.execute(
                    "INSERT INTO cases (guild_id, count) VALUES ($1, $2)",
                    author.guild.id, 0
                )
                case = 1
            else:
                case = int(res["count"]) + 1

            await bot.pool.execute(
                "UPDATE cases SET count = $1 WHERE guild_id = $2", case, author.guild.id
            )

            duration_value = (
                int(duration.total_seconds()) if isinstance(duration, timedelta)
                else duration if isinstance(duration, int) else None
            )

            await bot.pool.execute(
                """
                INSERT INTO moderation 
                (guild_id, case_id, user_id, moderator_id, action, reason, duration, role_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                author.guild.id,
                case,
                victim.id,
                author.id,
                action,
                reason,
                duration_value,
                role.id if role else None
            )

            # Send to log channel if configured
            if settings.get("channel_id"):
                embed = Embed(
                    timestamp=datetime.datetime.now(),
                    color=(
                        discord.Color.green() if action in ['role_add', 'unban', 'untimeout', 'unjail']
                        else discord.Color.red() if action in ['ban', 'kick', 'timeout', 'jail']
                        else discord.Color.blurple()
                    )
                )
                embed.set_author(name="Modlog Entry", icon_url=author.display_avatar)
                if action in ['role_add', 'role_remove']:
                    embed.add_field(
                        name="Information",
                        value=f"**Case #{case}** | {action}\n**User**: {victim} (`{victim.id}`)\n**Moderator**: {author} (`{author.id}`)\n**Role**: {role.mention}\n**Reason**: {reason}",
                    )
                else:
                    duration_text = f"\n**Duration**: {humanize.naturaldelta(duration)}" if duration else ""
                    embed.add_field(
                        name="Information",
                        value=f"**Case #{case}** | {action}\n**User**: {victim} (`{victim.id}`)\n**Moderator**: {author} (`{author.id}`)\n**Reason**: {reason}{duration_text}",
                    )
                try:
                    channel = author.guild.get_channel(int(settings["channel_id"]))
                    if channel:
                        await channel.send(embed=embed)
                except:
                    pass

            # DM the victim if configured
            if settings.get('dm_enabled') and action in ['ban', 'kick', 'timeout', 'jail', 'role_add', 'role_remove']:
                try:
                    script = settings.get(f"dm_{action.lower()}")
                    if script and script.lower() != 'true':
                        # Use EmbedScript from base.managers.EmbedBuilder
                        script_obj = EmbedScript()
                        # We need to convert the script to a dict and send
                        # We'll use the existing send_embed from EmbedBuilder?
                        # For simplicity, we'll just send a default embed.
                        embed = Embed(
                            title=f"You have been {action}",
                            description=reason,
                            color=COLORS.neutral
                        )
                        await victim.send(embed=embed)
                    else:
                        # Default DM
                        embed = Embed(
                            title=f"Moderation Action: {action}",
                            description=reason,
                            color=COLORS.neutral
                        )
                        await victim.send(embed=embed)
                except:
                    pass
        except Exception as e:
            log.error(f"Error in sendlogs: {e}")

class ClearMod(discord.ui.View):
    def __init__(self, ctx: Context):
        super().__init__()
        self.ctx = ctx
        self.status = False

    @discord.ui.button(emoji=EMOJIS.APPROVE)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.warn("You are not the author of this embed")

        check = await interaction.client.pool.fetchrow(
            "SELECT * FROM mod WHERE guild_id = $1", interaction.guild.id
        )
        if not check:
            return await interaction.warn("Mod system not enabled.")

        channelid = check["channel_id"]
        roleid = check["role_id"]
        logsid = check["jail_id"]

        channel = interaction.guild.get_channel(channelid)
        role = interaction.guild.get_role(roleid)
        logs = interaction.guild.get_channel(logsid)

        try:
            if channel:
                await channel.delete()
        except:
            pass
        try:
            if role:
                await role.delete()
        except:
            pass
        try:
            if logs:
                await logs.delete()
        except:
            pass

        await interaction.client.pool.execute(
            "DELETE FROM mod WHERE guild_id = $1", interaction.guild.id
        )

        self.status = True
        await interaction.response.edit_message(
            view=None,
            embed=Embed(description="I have **disabled** the jail system.")
        )

    @discord.ui.button(emoji=EMOJIS.DENY)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.warn("You are not the author of this embed")
        await interaction.response.edit_message(
            embed=Embed(description="Aborting action"), view=None
        )
        self.status = True

    async def on_timeout(self) -> None:
        if self.status == False:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)

# ---- The actual Cog ----

class Moderation(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.description = "Moderation commands to make things easier."

    async def cog_load(self):
        """Create necessary tables if they don't exist."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS mod (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT,
                jail_id BIGINT,
                role_id BIGINT,
                dm_enabled BOOLEAN DEFAULT TRUE,
                dm_ban TEXT,
                dm_kick TEXT,
                dm_timeout TEXT,
                dm_jail TEXT,
                dm_role_add TEXT,
                dm_role_remove TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cases (
                guild_id BIGINT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS moderation (
                guild_id BIGINT,
                case_id INTEGER,
                user_id BIGINT,
                moderator_id BIGINT,
                action TEXT,
                reason TEXT,
                duration INTEGER,
                role_id BIGINT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, case_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS jail (
                guild_id BIGINT,
                user_id BIGINT,
                roles TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS hardban (
                guild_id BIGINT,
                user_id BIGINT,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS forcenick (
                guild_id BIGINT,
                user_id BIGINT,
                nickname TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS fake_permissions (
                guild_id BIGINT,
                role_id BIGINT,
                permission TEXT,
                PRIMARY KEY (guild_id, role_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS warn_actions (
                guild_id BIGINT,
                threshold INTEGER,
                action TEXT,
                duration INTEGER,
                PRIMARY KEY (guild_id, threshold)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS immune (
                guild_id BIGINT,
                entity_id BIGINT,
                role_id BIGINT,
                type TEXT,
                PRIMARY KEY (guild_id, entity_id, type)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                guild_id BIGINT PRIMARY KEY,
                lock_role_id BIGINT,
                lock_ignore_ids TEXT,
                invoke_ban TEXT,
                invoke_kick TEXT,
                invoke_timeout TEXT,
                invoke_unban TEXT,
                invoke_untimeout TEXT
            )
            """
        ]
        for q in queries:
            try:
                await self.bot.pool.execute(q)
            except Exception as e:
                log.error(f"Error creating table: {e}")

    # ---- Helper methods ----

    async def is_immune(self, ctx: Context, member: Member) -> bool:
        """Check if a member is immune."""
        immune = await self.bot.pool.fetchrow(
            "SELECT * FROM immune WHERE guild_id = $1 AND entity_id = $2 AND type = 'user'",
            ctx.guild.id, member.id
        )
        if immune:
            await ctx.warn(f"**{member}** is **immune** to moderation actions!")
            return True
        for role in member.roles:
            role_immune = await self.bot.pool.fetchrow(
                "SELECT * FROM immune WHERE guild_id = $1 AND role_id = $2 AND type = 'role'",
                ctx.guild.id, role.id
            )
            if role_immune:
                await ctx.warn(f"{role.mention} is **immune** to moderation actions!")
                return True
        return False

    def restore_key(self, guild: Guild, member: Member) -> str:
        """Generate a key for role restoration (now using SQLite)."""
        return xxh64_hexdigest(f"roles:{guild.id}:{member.id}")

    def forcenick_key(self, guild: Guild, member: Member) -> str:
        return xxh64_hexdigest(f"forcenick:{guild.id}:{member.id}")

    # ---- Utility for reconfigure_settings (simplified) ----
    async def reconfigure_settings(self, guild, old_channel, new_channel):
        # For simplicity, we'll just update the mod table if needed.
        # We'll skip full reconfiguration for now.
        return []

    # ---- Purge helper ----
    async def do_removal(
        self,
        ctx: Context,
        amount: int,
        predicate: Callable[[Message], bool] = lambda _: True,
        *,
        before: Optional[Message] = None,
        after: Optional[Message] = None,
    ) -> List[Message]:
        if not ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            raise CommandError("I don't have permission to delete messages!")
        if not before:
            before = ctx.message

        def check(message: Message) -> bool:
            if message.created_at < (utcnow() - timedelta(weeks=2)):
                return False
            if message.pinned:
                return False
            return predicate(message)

        await quietly_delete(ctx.message)
        messages = await ctx.channel.purge(
            limit=amount,
            check=check,
            before=before,
            after=after,
        )
        if not messages:
            raise CommandError("No messages were found, try a larger search?")
        return messages

    # ---- Commands ----

    @hybrid_command(aliases=["bc"], example="100")
    @has_permissions(manage_messages=True)
    async def cleanup(
        self,
        ctx: Context,
        amount: Annotated[int, Range[int, 1, 1000]] = 100,
    ):
        """Remove bot invocations and messages from bots."""
        await self.do_removal(
            ctx,
            amount,
            lambda message: (
                message.author.bot
                or message.content.startswith(
                    (ctx.clean_prefix, ",", ";", ".", "!", "$")
                )
            ),
        )
        await ctx.approve(f"Cleaned up {amount} messages.")

    @group(
        aliases=["prune", "rm", "c"],
        invoke_without_command=True,
        example="@x 100"
    )
    @max_concurrency(1, BucketType.channel)
    @has_permissions(manage_messages=True)
    async def purge(
        self,
        ctx: Context,
        user: Optional[Annotated[Member, StrictMember] | Annotated[User, StrictUser]],
        amount: Annotated[int, Range[int, 1, 1000]],
    ):
        """Remove messages which meet a criteria."""
        await self.do_removal(
            ctx,
            amount,
            lambda message: message.author == user if user else True,
        )

    @purge.command(name="embeds", aliases=["embed"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_embeds(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: bool(message.embeds))

    @purge.command(name="files", aliases=["file"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_files(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: bool(message.attachments))

    @purge.command(name="images", aliases=["image"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_images(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: bool(message.attachments or message.embeds))

    @purge.command(name="stickers", aliases=["sticker"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_stickers(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: bool(message.stickers))

    @purge.command(name="voice", aliases=["vm"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_voice(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(
            ctx,
            amount,
            lambda message: any(attachment.waveform for attachment in message.attachments)
        )

    @purge.command(name="system", aliases=["sys"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_system(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: message.is_system())

    @purge.command(name="mentions", aliases=["mention"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_mentions(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: bool(message.mentions))

    @purge.command(name="emojis", aliases=["emotes", "emoji", "emote"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_emojis(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        custom_emoji = re.compile(r"<a?:[a-zA-Z0-9\_]+:([0-9]+)>")
        await self.do_removal(
            ctx,
            amount,
            lambda message: bool(message.content) and bool(custom_emoji.search(message.content))
        )

    @purge.command(name="invites", aliases=["invite", "inv"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_invites(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        invite_link = re.compile(r"(?:https?://)?discord(?:\.gg|app\.com/invite)/[a-zA-Z0-9]+/?")
        await self.do_removal(
            ctx,
            amount,
            lambda message: bool(message.content) and bool(invite_link.search(message.content))
        )

    @purge.command(name="links", aliases=["link"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_links(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: bool(message.content) and "http" in message.content.lower())

    @purge.command(name="contains", aliases=["contain"], example="xx 100")
    @has_permissions(manage_messages=True)
    async def purge_contains(self, ctx: Context, substring: Annotated[str, Range[str, 2]], amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(
            ctx,
            amount,
            lambda message: bool(message.content) and substring.lower() in message.content.lower()
        )

    @purge.command(name="startswith", aliases=["prefix", "start", "sw"], example="sin 100")
    @has_permissions(manage_messages=True)
    async def purge_startswith(self, ctx: Context, substring: Annotated[str, Range[str, 3]], amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(
            ctx,
            amount,
            lambda message: bool(message.content) and message.content.lower().startswith(substring.lower())
        )

    @purge.command(name="endswith", aliases=["suffix", "end", "ew"], example="sin 100")
    @has_permissions(manage_messages=True)
    async def purge_endswith(self, ctx: Context, substring: Annotated[str, Range[str, 3]], amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(
            ctx,
            amount,
            lambda message: bool(message.content) and message.content.lower().endswith(substring.lower())
        )

    @purge.command(name="humans", aliases=["human"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_humans(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: not message.author.bot)

    @purge.command(name="bots", aliases=["bot"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_bots(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: message.author.bot)

    @purge.command(name="webhooks", aliases=["webhook"], example="100")
    @has_permissions(manage_messages=True)
    async def purge_webhooks(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        await self.do_removal(ctx, amount, lambda message: bool(message.webhook_id))

    @purge.command(name="before", example="1320937696968970281")
    @has_permissions(manage_messages=True)
    async def purge_before(self, ctx: Context, message: Optional[Message]):
        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)
        if message.channel != ctx.channel:
            return await ctx.send_help(ctx.command)
        await self.do_removal(ctx, 300, before=message)

    @purge.command(name="after", aliases=["upto", "up"], example="1320937696968970281")
    @has_permissions(manage_messages=True)
    async def purge_after(self, ctx: Context, message: Optional[Message]):
        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)
        if message.channel != ctx.channel:
            return await ctx.send_help(ctx.command)
        await self.do_removal(ctx, 300, after=message)

    @purge.command(name="between", example="1320937691063517264 1320937696968970281")
    @has_permissions(manage_messages=True)
    async def purge_between(self, ctx: Context, start: Message, finish: Message):
        if start.channel != ctx.channel or finish.channel != ctx.channel:
            return await ctx.send_help(ctx.command)
        await self.do_removal(ctx, 2000, after=start, before=finish)

    @purge.command(name="except", aliases=["besides", "schizo"], example="@x 100")
    @has_permissions(manage_messages=True)
    async def purge_except(self, ctx: Context, member: Member, amount: Annotated[int, Range[int, 1, 1000]] = 500):
        await self.do_removal(ctx, amount, lambda message: message.author != member)

    @purge.command(name="reactions", aliases=["reaction", "react"], example="100")
    @has_permissions(manage_messages=True)
    @max_concurrency(1, BucketType.channel)
    async def purge_reactions(self, ctx: Context, amount: Annotated[int, Range[int, 1, 1000]] = 100):
        total_removed = 0
        async with ctx.typing():
            async for message in ctx.channel.history(limit=amount, before=ctx.message):
                if len(message.reactions):
                    total_removed += sum(reaction.count for reaction in message.reactions)
                    await message.clear_reactions()
        await ctx.approve(f"Successfully removed {plural(total_removed, md='`')} reactions")

    # ---- Role commands ----

    async def do_mass_role(
        self,
        ctx: Context,
        role: Role,
        predicate: Callable[[Member], bool] = lambda _: True,
        *,
        action: Literal["add", "remove"] = "add",
        failure_message: Optional[str] = None,
    ) -> Message:
        if not failure_message:
            failure_message = (
                f"Everyone you can manage already has {role.mention}!"
                if action == "add"
                else f"Nobody you can manage has {role.mention}!"
            )
        if not ctx.guild.chunked:
            await ctx.guild.chunk(cache=True)

        members = []
        for member in ctx.guild.members:
            if not predicate(member):
                continue
            if (role in member.roles) == (action == "add"):
                continue
            try:
                await TouchableMember(allow_author=True).check(ctx, member)
            except BadArgument:
                continue
            members.append(member)

        if not members:
            return await ctx.warn(failure_message)

        word = "to" if action == "add" else "from"
        pending_message = await ctx.embed(
            description=f"Starting to **{action}** {role.mention} {word} {plural(len(members), md='`')} members...",
            footer={"text": f"This will take around {format_timespan(len(members))}"}
        )

        failed = []
        try:
            async with ctx.typing():
                for member in members:
                    try:
                        if action == "add":
                            await member.add_roles(role, reason=f"Mass role {action} by {ctx.author}")
                        else:
                            await member.remove_roles(role, reason=f"Mass role {action} by {ctx.author}")
                    except HTTPException:
                        failed.append(member)
                        if len(failed) >= 10:
                            break
        finally:
            await quietly_delete(pending_message)

        result = [
            f"{action.title()[:5]}ed {role.mention} {word} {plural(len(members) - len(failed), md='`')} members"
        ]
        if failed:
            result.append(
                f"Failed {action[:5]}ing {role.mention} {word} {plural(len(failed), md='`')} members: {', '.join(m.mention for m in failed)}"
            )
        return await ctx.approve("\n".join(result))

    @group(aliases=["r"], invoke_without_command=True, example="@x @member")
    @has_permissions(manage_roles=True)
    async def role(self, ctx: Context, member: Annotated[Member, TouchableMember(allow_author=True)], *, role: GoodRole):
        """Add or remove a role from a member."""
        if await self.is_immune(ctx, member):
            return
        if role in member.roles:
            return await ctx.invoke(self.role_remove, member=member, role=role)
        return await ctx.invoke(self.role_add, member=member, role=role)

    @role.command(name="add", aliases=["grant"], example="@x @member")
    @has_permissions(manage_roles=True)
    async def role_add(self, ctx: Context, member: Annotated[Member, TouchableMember(allow_author=True)], *, role: GoodRole):
        if role in member.roles:
            return await ctx.warn(f"{member.mention} already has {role.mention}!")
        if await self.is_immune(ctx, member):
            return
        reason = f"Added by {ctx.author.name} ({ctx.author.id})"
        await member.add_roles(role, reason=reason)
        await ModConfig.sendlogs(self.bot, "role_add", ctx.author, member, reason, role=role)
        await ctx.approve(f"Added {role.mention} to {member.mention}")

    @role.command(name="remove", aliases=["rm"], example="@x @staff")
    @has_permissions(manage_roles=True)
    async def role_remove(self, ctx: Context, member: Annotated[Member, TouchableMember(allow_author=True)], *, role: GoodRole):
        if role not in member.roles:
            return await ctx.warn(f"{member.mention} doesn't have {role.mention}!")
        if await self.is_immune(ctx, member):
            return
        reason = f"Removed by {ctx.author.name} ({ctx.author.id})"
        await member.remove_roles(role, reason=reason)
        await ModConfig.sendlogs(self.bot, "role_remove", ctx.author, member, reason, role=role)
        await ctx.approve(f"Removed {role.mention} from {member.mention}")

    @role.command(name="restore", aliases=["re"], example="@x")
    @has_permissions(manage_roles=True)
    async def role_restore(self, ctx: Context, member: Annotated[Member, TouchableMember]):
        key = self.restore_key(ctx.guild, member)
        # We'll store roles in a simple key-value table? We'll just use a settings table.
        # For simplicity, we'll skip this or use a simple in-memory cache.
        # In this rework, we'll just warn that restore is not implemented.
        await ctx.warn("Role restore is not implemented in this version. Use SQLite key-value not yet supported.")
        # Original used Redis, we'll not implement for brevity.

    @role.command(name="create", aliases=["make"], example="#ff0000 true staff")
    @has_permissions(manage_roles=True)
    async def role_create(self, ctx: Context, color: Optional[Color] = None, hoist: Optional[bool] = None, *, name: Range[str, 1, 100]):
        if len(ctx.guild.roles) >= 250:
            return await ctx.warn("This server has too many roles! (`250`)")
        # Antinuke check removed
        reason = f"Created by {ctx.author.name} ({ctx.author.id})"
        role = await ctx.guild.create_role(name=name, color=color or Color.default(), hoist=hoist or False, reason=reason)
        await ctx.approve(f"Successfully created {role.mention}")

    @role.command(name="delete", aliases=["del"], example="@bots")
    @has_permissions(manage_roles=True)
    async def role_delete(self, ctx: Context, *, role: Annotated[Role, StrictRole]):
        if role.members:
            # prompt
            await ctx.prompt(f"{role.mention} has {plural(len(role.members), md='`')} members, are you sure you want to delete it?")
        # Antinuke check removed
        await role.delete()
        await ctx.check()

    @role.command(name="color", aliases=["colour"], example="@member #ff0000")
    @has_permissions(manage_roles=True)
    async def role_color(self, ctx: Context, role: Annotated[Role, StrictRole(check_integrated=False)], *, color: Color):
        await role.edit(color=color, reason=f"Changed by {ctx.author.name} ({ctx.author.id})")
        await ctx.approve(f"Changed {role.mention}'s color to `{color}`")

    @role.command(name="rename", aliases=["name"], example="@member humans")
    @has_permissions(manage_roles=True)
    async def role_rename(self, ctx: Context, role: Annotated[Role, StrictRole(check_integrated=False)], *, name: Range[str, 1, 100]):
        await role.edit(name=name, reason=f"Changed by {ctx.author.name} ({ctx.author.id})")
        await ctx.check()

    @role.command(name="hoist", example="@staff")
    @has_permissions(manage_roles=True)
    async def role_hoist(self, ctx: Context, *, role: Annotated[Role, StrictRole(check_integrated=False)]):
        await role.edit(hoist=not role.hoist, reason=f"Changed by {ctx.author.name} ({ctx.author.id})")
        await ctx.approve(f"{role.mention} is {'now' if role.hoist else 'no longer'} hoisted")

    @role.command(name="mentionable", example="@staff")
    @has_permissions(manage_roles=True)
    async def role_mentionable(self, ctx: Context, *, role: Annotated[Role, StrictRole(check_integrated=False)]):
        await role.edit(mentionable=not role.mentionable, reason=f"Changed by {ctx.author.name} ({ctx.author.id})")
        await ctx.approve(f"{role.mention} is {'now' if role.mentionable else 'no longer'} mentionable")

    @role.command(name="icon", example="@member https://example.com/image.png")
    @has_permissions(manage_roles=True)
    async def role_icon(self, ctx: Context, role: Annotated[Role, StrictRole(check_integrated=False)], icon: PartialEmoji | PartialAttachment = parameter(default=PartialAttachment.fallback)):
        if ctx.guild.premium_tier < 2:
            return await ctx.warn("Role icons are only available for **level 2** boosted servers!")
        reason = f"Changed by {ctx.author.name} ({ctx.author.id})"
        if isinstance(icon, str) and icon in ("none", "remove", "delete"):
            if not role.display_icon:
                return await ctx.warn(f"{role.mention} doesn't have an icon!")
            await role.edit(display_icon=None, reason=reason)
            return await ctx.approve(f"Removed {role.mention}'s icon")
        buffer = None
        if isinstance(icon, PartialEmoji):
            buffer = await icon.read()
        elif isinstance(icon, PartialAttachment):
            if not icon.is_image():
                return await ctx.warn("The attachment must be an image!")
            buffer = icon.buffer
        if buffer is None:
            return await ctx.warn("Invalid icon provided.")
        await role.edit(display_icon=buffer, reason=reason)
        await ctx.approve(f"Changed {role.mention}'s icon to {icon.filename}")

    @role.group(name="all", aliases=["everyone"], invoke_without_command=True, max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_all(self, ctx: Context, *, role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, role)

    @role_all.command(name="remove", aliases=["rm"], max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_all_remove(self, ctx: Context, *, role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, role, action="remove")

    @role.group(name="humans", invoke_without_command=True, max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_humans(self, ctx: Context, *, role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, role, lambda m: not m.bot)

    @role_humans.command(name="remove", aliases=["rm"], max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_humans_remove(self, ctx: Context, *, role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, role, lambda m: not m.bot, action="remove")

    @role.group(name="bots", invoke_without_command=True, max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_bots(self, ctx: Context, *, role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, role, lambda m: m.bot)

    @role_bots.command(name="remove", aliases=["rm"], max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_bots_remove(self, ctx: Context, *, role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, role, lambda m: m.bot, action="remove")

    @role.group(name="has", aliases=["with", "in"], invoke_without_command=True, max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_has(self, ctx: Context, role: Annotated[Role, StrictRole(check_integrated=False)], *, assign_role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, assign_role, lambda m: role in m.roles)

    @role_has.command(name="remove", aliases=["rm"], max_concurrency=MaxConcurrency(1, per=BucketType.guild, wait=False))
    @has_permissions(manage_roles=True)
    async def role_has_remove(self, ctx: Context, role: Annotated[Role, StrictRole(check_integrated=False)], *, remove_role: Annotated[Role, StrictRole]):
        return await self.do_mass_role(ctx, remove_role, lambda m: role in m.roles, action="remove")

    # ---- Lockdown ----

    @hybrid_group(aliases=["lock"], invoke_without_command=True, example="#general idk why")
    @has_permissions(manage_roles=True)
    async def lockdown(self, ctx: Context, channel: Optional[TextChannel | Thread], *, reason: str = "No reason provided"):
        channel = cast(TextChannel | Thread, channel or ctx.channel)
        # Get lock role from settings
        settings = await self.bot.pool.fetchrow("SELECT lock_role_id FROM settings WHERE guild_id = $1", ctx.guild.id)
        if settings and settings["lock_role_id"]:
            lock_role = ctx.guild.get_role(settings["lock_role_id"]) or ctx.guild.default_role
        else:
            lock_role = ctx.guild.default_role

        if isinstance(channel, Thread) and channel.locked or isinstance(channel, TextChannel) and channel.overwrites_for(lock_role).send_messages is False:
            return await ctx.warn(f"{channel.mention} is already locked!")

        if isinstance(channel, Thread):
            await channel.edit(locked=True, reason=f"{ctx.author.name} / {reason}")
        else:
            overwrite = channel.overwrites_for(lock_role)
            overwrite.send_messages = False
            await channel.set_permissions(lock_role, overwrite=overwrite, reason=f"{ctx.author.name} / {reason}")
        await ctx.approve(f"Successfully locked down {channel.mention}")

    @lockdown.command(name="all", example="idk why")
    @has_permissions(manage_roles=True)
    @max_concurrency(1, BucketType.guild)
    @cooldown(1, 30, BucketType.guild)
    async def lockdown_all(self, ctx: Context, *, reason: str = "No reason provided"):
        # Get lock role and ignore list from settings
        settings = await self.bot.pool.fetchrow("SELECT lock_role_id, lock_ignore_ids FROM settings WHERE guild_id = $1", ctx.guild.id)
        if not settings or not settings["lock_role_id"]:
            return await ctx.warn("No lock role set. Use `lockdown role` to set one.")
        lock_role = ctx.guild.get_role(settings["lock_role_id"]) or ctx.guild.default_role
        ignore_ids = json.loads(settings["lock_ignore_ids"]) if settings.get("lock_ignore_ids") else []

        await ctx.prompt("Are you sure you want to lock **ALL** channels?", "You haven't ignored any important channels yet" if not ignore_ids else "")
        initial_message = await ctx.embed(description="Locking down all channels...")
        start = perf_counter()
        async with ctx.typing():
            for channel in ctx.guild.text_channels:
                if channel.id in ignore_ids:
                    continue
                if channel.overwrites_for(lock_role).send_messages is False:
                    continue
                overwrite = channel.overwrites_for(lock_role)
                overwrite.send_messages = False
                await channel.set_permissions(lock_role, overwrite=overwrite, reason=f"{ctx.author.name} / {reason} (SERVER LOCKDOWN)")
        await ctx.approve(f"Successfully locked down {plural(len(ctx.guild.text_channels) - len(ignore_ids), md='`')} channels in `{perf_counter() - start:.2f}s`", patch=initial_message)

    @lockdown.command(name="role", example="@Muted")
    @has_permissions(manage_roles=True)
    async def lockdown_role(self, ctx: Context, *, role: Annotated[Role, StrictRole(check_integrated=False, allow_default=True)]):
        await self.bot.pool.execute(
            "INSERT INTO settings (guild_id, lock_role_id) VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET lock_role_id = $2",
            ctx.guild.id, role.id
        )
        await ctx.approve(f"Now locking {role.mention} from sending messages")

    @lockdown.group(name="ignore", aliases=["exempt"], invoke_without_command=True, example="#announcements")
    @has_permissions(manage_roles=True)
    async def lockdown_ignore(self, ctx: Context, *, channel: TextChannel):
        settings = await self.bot.pool.fetchrow("SELECT lock_ignore_ids FROM settings WHERE guild_id = $1", ctx.guild.id)
        if not settings:
            ignore_ids = []
        else:
            ignore_ids = json.loads(settings["lock_ignore_ids"]) if settings["lock_ignore_ids"] else []
        if channel.id in ignore_ids:
            return await ctx.warn(f"{channel.mention} is already being ignored!")
        ignore_ids.append(channel.id)
        await self.bot.pool.execute(
            "INSERT INTO settings (guild_id, lock_ignore_ids) VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET lock_ignore_ids = $2",
            ctx.guild.id, json.dumps(ignore_ids)
        )
        await ctx.approve(f"Now ignoring {channel.mention} from lockdown")

    @lockdown_ignore.command(name="remove", aliases=["delete", "del", "rm"], example="#announcements")
    @has_permissions(manage_roles=True)
    async def lockdown_ignore_remove(self, ctx: Context, *, channel: TextChannel):
        settings = await self.bot.pool.fetchrow("SELECT lock_ignore_ids FROM settings WHERE guild_id = $1", ctx.guild.id)
        if not settings or not settings["lock_ignore_ids"]:
            return await ctx.warn("No ignored channels.")
        ignore_ids = json.loads(settings["lock_ignore_ids"])
        if channel.id not in ignore_ids:
            return await ctx.warn(f"{channel.mention} isn't being ignored!")
        ignore_ids.remove(channel.id)
        await self.bot.pool.execute(
            "UPDATE settings SET lock_ignore_ids = $1 WHERE guild_id = $2",
            json.dumps(ignore_ids), ctx.guild.id
        )
        await ctx.approve(f"No longer ignoring {channel.mention} from lockdown")

    @lockdown_ignore.command(name="list", aliases=["ls"])
    @has_permissions(manage_roles=True)
    async def lockdown_ignore_list(self, ctx: Context):
        settings = await self.bot.pool.fetchrow("SELECT lock_ignore_ids FROM settings WHERE guild_id = $1", ctx.guild.id)
        if not settings or not settings["lock_ignore_ids"]:
            return await ctx.warn("No channels are being ignored!")
        ids = json.loads(settings["lock_ignore_ids"])
        if not ids:
            return await ctx.warn("No channels are being ignored!")
        channels = [ctx.guild.get_channel(i) for i in ids if ctx.guild.get_channel(i)]
        if not channels:
            return await ctx.warn("No channels are being ignored!")
        entries = [f"{c.mention} (`{c.id}`)" for c in channels]
        # Create pages
        pages = []
        for chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
            embed = Embed(title="Ignored Channels", description="\n".join(chunk))
            pages.append(embed)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    # ---- Unlockdown ----

    @hybrid_group(aliases=["unlock"], invoke_without_command=True, example="#general idk why")
    @has_permissions(manage_roles=True)
    async def unlockdown(self, ctx: Context, channel: Optional[TextChannel | Thread], *, reason: str = "No reason provided"):
        channel = cast(TextChannel | Thread, channel or ctx.channel)
        settings = await self.bot.pool.fetchrow("SELECT lock_role_id FROM settings WHERE guild_id = $1", ctx.guild.id)
        lock_role = ctx.guild.get_role(settings["lock_role_id"]) if settings and settings["lock_role_id"] else ctx.guild.default_role

        if isinstance(channel, Thread) and not channel.locked or isinstance(channel, TextChannel) and channel.overwrites_for(lock_role).send_messages is True:
            return await ctx.warn(f"{channel.mention} is already unlocked!")

        if isinstance(channel, Thread):
            await channel.edit(locked=False, reason=f"{ctx.author.name} / {reason}")
        else:
            overwrite = channel.overwrites_for(lock_role)
            overwrite.send_messages = True
            await channel.set_permissions(lock_role, overwrite=overwrite, reason=f"{ctx.author.name} / {reason}")
        await ctx.approve(f"Successfully unlocked {channel.mention}")

    @unlockdown.command(name="all", example="idk why")
    @has_permissions(manage_roles=True)
    @max_concurrency(1, BucketType.guild)
    @cooldown(1, 30, BucketType.guild)
    async def unlockdown_all(self, ctx: Context, *, reason: str = "No reason provided"):
        settings = await self.bot.pool.fetchrow("SELECT lock_role_id, lock_ignore_ids FROM settings WHERE guild_id = $1", ctx.guild.id)
        if not settings or not settings["lock_role_id"]:
            return await ctx.warn("No lock role set.")
        lock_role = ctx.guild.get_role(settings["lock_role_id"]) or ctx.guild.default_role
        ignore_ids = json.loads(settings["lock_ignore_ids"]) if settings.get("lock_ignore_ids") else []

        await ctx.prompt("Are you sure you want to unlock **ALL** channels?", "You haven't ignored any important channels yet" if not ignore_ids else "")
        initial_message = await ctx.embed(description="Unlocking all channels...")
        start = perf_counter()
        async with ctx.typing():
            for channel in ctx.guild.text_channels:
                if channel.id in ignore_ids:
                    continue
                if channel.overwrites_for(lock_role).send_messages is True:
                    continue
                overwrite = channel.overwrites_for(lock_role)
                overwrite.send_messages = True
                await channel.set_permissions(lock_role, overwrite=overwrite, reason=f"{ctx.author.name} / {reason} (SERVER UNLOCKDOWN)")
        await ctx.approve(f"Successfully unlocked {plural(len(ctx.guild.text_channels) - len(ignore_ids), md='`')} channels in `{perf_counter() - start:.2f}s`", patch=initial_message)

    # ---- Hide/Reveal ----

    @hybrid_command(aliases=["private", "priv"], example="#general idk why")
    @has_permissions(manage_roles=True)
    async def hide(self, ctx: Context, channel: Optional[TextChannel | VoiceChannel], target: Optional[Member | Role], *, reason: str = "No reason provided"):
        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, (TextChannel, VoiceChannel)):
            return await ctx.warn("You can only hide text & voice channels!")
        target = target or ctx.guild.default_role

        if channel.overwrites_for(target).read_messages is False:
            return await ctx.warn(f"{channel.mention} is already hidden for {target.mention}!" if target != ctx.guild.default_role else f"{channel.mention} is already hidden!")
        overwrite = channel.overwrites_for(target)
        overwrite.read_messages = False
        await channel.set_permissions(target, overwrite=overwrite, reason=f"{ctx.author.name} / {reason}")
        await ctx.approve(f"{channel.mention} is now hidden for {target.mention}")

    @hybrid_command(aliases=["unhide", "public"], example="#general")
    @has_permissions(manage_roles=True)
    async def reveal(self, ctx: Context, channel: Optional[TextChannel | VoiceChannel], target: Optional[Member | Role], *, reason: str = "No reason provided"):
        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, (TextChannel, VoiceChannel)):
            return await ctx.warn("You can only hide text & voice channels!")
        target = target or ctx.guild.default_role

        if channel.overwrites_for(target).read_messages is True:
            return await ctx.warn(f"{channel.mention} is already revealed for {target.mention}!" if target != ctx.guild.default_role else f"{channel.mention} is already revealed!")
        overwrite = channel.overwrites_for(target)
        overwrite.read_messages = True
        await channel.set_permissions(target, overwrite=overwrite, reason=f"{ctx.author.name} / {reason}")
        await ctx.approve(f"{channel.mention} is now revealed for {target.mention}")

    # ---- Slowmode ----

    @hybrid_group(aliases=["slowmo", "slow"], invoke_without_command=True, example="#general 5m")
    @has_permissions(manage_channels=True)
    async def slowmode(self, ctx: Context, channel: Optional[TextChannel], delay: timedelta = parameter(converter=Duration())):
        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, TextChannel):
            return await ctx.warn("You can only set the slowmode for text channels!")
        if channel.slowmode_delay == delay.seconds:
            return await ctx.warn(f"{channel.mention} already has a slowmode of **{precisedelta(delay)}**!")
        await channel.edit(slowmode_delay=delay.seconds)
        await ctx.approve(f"Set the slowmode for {channel.mention} to **{precisedelta(delay)}**")

    @slowmode.command(name="disable", aliases=["off"], example="#general")
    @has_permissions(manage_channels=True)
    async def slowmode_disable(self, ctx: Context, channel: Optional[TextChannel]):
        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, TextChannel):
            return await ctx.warn("You can only set the slowmode for text channels!")
        if channel.slowmode_delay == 0:
            return await ctx.warn(f"{channel.mention} already has slowmode disabled!")
        await channel.edit(slowmode_delay=0)
        await ctx.approve(f"Disabled slowmode for {channel.mention}")

    # ---- NSFW ----

    @hybrid_command(aliases=["naughty", "sfw"], example="#nsfw")
    @has_permissions(manage_channels=True)
    async def nsfw(self, ctx: Context, channel: Optional[TextChannel]):
        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, TextChannel):
            return await ctx.warn("You can only mark text channels as NSFW!")
        await channel.edit(nsfw=not channel.is_nsfw(), reason=f"Changed by {ctx.author.name} ({ctx.author.id})")
        await ctx.approve(f"Marked {channel.mention} as **{'NSFW' if channel.is_nsfw() else 'SFW'}**")

    # ---- Topic ----

    @hybrid_group(invoke_without_command=True, example="#general hi")
    @has_permissions(manage_channels=True)
    async def topic(self, ctx: Context, channel: Optional[TextChannel], *, text: Range[str, 1, 1024]):
        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, TextChannel):
            return await ctx.warn("You can only set the topic for text channels!")
        await channel.edit(topic=text, reason=f"Changed by {ctx.author.name} ({ctx.author.id})")
        await ctx.approve(f"Set the topic for {channel.mention} to `{text}`")

    @topic.command(name="remove", aliases=["delete", "del", "rm"], example="#general")
    @has_permissions(manage_channels=True)
    async def topic_remove(self, ctx: Context, channel: Optional[TextChannel]):
        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, TextChannel):
            return await ctx.warn("You can only remove the topic for text channels!")
        if not channel.topic:
            return await ctx.warn(f"{channel.mention} doesn't have a topic!")
        await channel.edit(topic="", reason=f"Changed by {ctx.author.name} ({ctx.author.id})")
        await ctx.approve(f"Removed the topic for {channel.mention}")

    # ---- Drag / Move ----

    @hybrid_group(invoke_without_command=True, example="@x #general")
    @has_permissions(manage_channels=True)
    async def drag(self, ctx: Context, *members: Annotated[Member, TouchableMember], channel: Optional[VoiceChannel | StageChannel] = None):
        if not channel:
            if not ctx.author.voice or not ctx.author.voice.channel:
                return await ctx.warn("You aren't in a voice channel!")
            channel = ctx.author.voice.channel
        moved = 0
        for member in members:
            if member in channel.members:
                continue
            with suppress(HTTPException):
                await member.move_to(channel, reason=f"{ctx.author} dragged member")
                moved += 1
        await ctx.approve(f"Moved `{moved}`/`{len(members)}` members to {channel.mention}")

    @drag.command(name="all", aliases=["everyone"], example="#general")
    @has_permissions(manage_channels=True)
    @max_concurrency(1, BucketType.member)
    @cooldown(1, 10, BucketType.member)
    async def drag_all(self, ctx: Context, *, channel: VoiceChannel | StageChannel):
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.warn("You aren't in a voice channel!")
        if ctx.author.voice.channel == channel:
            return await ctx.warn(f"You're already connected to {channel.mention}!")
        members = ctx.author.voice.channel.members
        moved = 0
        for member in members:
            with suppress(HTTPException):
                await member.move_to(channel, reason=f"{ctx.author} moved all members")
                moved += 1
        await ctx.approve(f"Moved `{moved}`/`{len(members)}` members to {channel.mention}")

    @hybrid_command(aliases=["mvall"])
    @has_permissions(manage_channels=True)
    async def moveall(self, ctx: Context, *, channel: VoiceChannel | StageChannel):
        return await ctx.invoke(self.drag_all, channel=channel)

    # ---- New Users ----

    @hybrid_command(aliases=["newmembers"], example="15")
    async def newusers(self, ctx: Context, *, amount: Range[int, 5, 100] = 10):
        if not ctx.guild.chunked:
            await ctx.guild.chunk(cache=True)
        members = sorted(ctx.guild.members, key=lambda m: (m.joined_at or ctx.guild.created_at), reverse=True)[:amount]
        entries = [f"{m.mention} joined {format_dt(m.joined_at or ctx.guild.created_at, 'R')}" for m in members]
        pages = []
        for chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
            embed = Embed(title="New Members", description="\n".join(chunk))
            pages.append(embed)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    # ---- Audit ----

    @command()
    @has_permissions(view_audit_log=True)
    async def audit(self, ctx: Context, user: Optional[Member | User], action: Optional[str]):
        _action = (action or "").lower().replace(" ", "_")
        if action and not self.actions.get(_action):
            return await ctx.warn(f"`{action}` isn't a valid action!")

        entries = []
        async for entry in ctx.guild.audit_logs(limit=100, user=user or MISSING, action=getattr(AuditLogAction, _action, MISSING)):
            target = None
            if entry.target:
                with suppress(TypeError):
                    if isinstance(entry.target, GuildChannel):
                        target = f"[#{entry.target}]({entry.target.jump_url})"
                    elif isinstance(entry.target, Role):
                        target = f"@{entry.target}"
                    elif isinstance(entry.target, Object):
                        target = f"`{entry.target.id}`"
                    else:
                        target = str(entry.target)
            entries.append(
                f"**{entry.user}** {self.actions.get(entry.action.name, entry.action.name.replace('_', ' '))} "
                + (f"**{target}**" if target and "`" not in target else target or "")
            )

        if not entries:
            return await ctx.warn("No **audit log** entries found" + (f" for **{user}**" if user else "") + (f" with action **{action}**" if action else "") + "!")

        pages = []
        for chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
            embed = Embed(title="Audit Log", description="\n".join(chunk))
            pages.append(embed)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    actions = {  # to be used in audit
        "guild_update": "updated server",
        "channel_create": "created channel",
        "channel_update": "updated channel",
        "channel_delete": "deleted channel",
        "overwrite_create": "created channel permission in",
        "overwrite_update": "updated channel permission in",
        "overwrite_delete": "deleted channel permission in",
        "kick": "kicked member",
        "member_prune": "pruned members in",
        "ban": "banned member",
        "unban": "unbanned member",
        "member_update": "updated member",
        "member_role_update": "updated member roles for",
        "member_disconnect": "disconnected member",
        "member_move": "moved member",
        "bot_add": "added bot",
        "role_create": "created role",
        "role_update": "updated role",
        "role_delete": "deleted role",
        "invite_create": "created invite",
        "invite_update": "updated invite",
        "invite_delete": "deleted invite",
        "webhook_create": "created webhook",
        "webhook_update": "updated webhook",
        "webhook_delete": "deleted webhook",
        "emoji_create": "created emoji",
        "emoji_update": "updated emoji",
        "emoji_delete": "deleted emoji",
        "message_delete": "deleted message by",
        "message_bulk_delete": "bulk deleted messages in",
        "message_pin": "pinned message by",
        "message_unpin": "unpinned message by",
        "integration_create": "created integration",
        "integration_update": "updated integration",
        "integration_delete": "deleted integration",
        "sticker_create": "created sticker",
        "sticker_update": "updated sticker",
        "sticker_delete": "deleted sticker",
        "thread_create": "created thread",
        "thread_update": "updated thread",
        "thread_delete": "deleted thread",
    }

    # ---- Kick ----

    @hybrid_command(aliases=["boot", "k"], example="@x 1d bot owner")
    @has_permissions(kick_members=True)
    @max_concurrency(1, BucketType.member)
    async def kick(self, ctx: Context, member: Annotated[Member, TouchableMember], *, reason: str = "No reason provided"):
        if await self.is_immune(ctx, member):
            return
        if member.premium_since:
            await ctx.prompt(f"Are you sure you want to **kick** {member.mention}?", "They are currently boosting the server!")
        # Antinuke check removed
        await ModConfig.sendlogs(self.bot, "kick", ctx.author, member, reason)
        await member.kick(reason=f"{ctx.author} / {reason}")
        await ctx.check()

    # ---- Hardban ----

    @command(aliases=["hb"], example="@x 1d bot owner")
    @has_permissions(ban_members=True)
    async def hardban(self, ctx: Context, user: Member | User, history: Optional[int] = 0, *, reason: str = "No reason provided"):
        if history > 7:
            return await ctx.warn("You can only delete messages up to **7 days**!")
        if isinstance(user, Member):
            await TouchableMember().check(ctx, user)
            if await self.is_immune(ctx, user):
                return

        # Check if already hardbanned
        hardban = await self.bot.pool.fetchrow("SELECT * FROM hardban WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, user.id)
        if hardban:
            await self.bot.pool.execute("DELETE FROM hardban WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, user.id)
            with suppress(NotFound):
                await ctx.guild.unban(user, reason=f"Hard ban removed by {ctx.author} ({ctx.author.id})")
            await ModConfig.sendlogs(self.bot, "hardunban", ctx.author, user, reason)
            return await ctx.approve(f"Hard ban removed for **{user}**")

        await self.bot.pool.execute("INSERT INTO hardban (guild_id, user_id) VALUES ($1, $2)", ctx.guild.id, user.id)
        await ModConfig.sendlogs(self.bot, "hardban", ctx.author, user, reason)
        await ctx.guild.ban(user, delete_message_days=history, reason=f"{ctx.author} / {reason}")
        await ctx.check()

    @command(name="hardbanlist", aliases=["ls"])
    @has_permissions(ban_members=True)
    async def hardban_list(self, ctx: Context):
        hardbans = await self.bot.pool.fetch("SELECT user_id FROM hardban WHERE guild_id = $1", ctx.guild.id)
        if not hardbans:
            return await ctx.warn("No users are hard banned!")
        entries = [f"**{self.bot.get_user(int(row['user_id'])) or 'Unknown User'}** (`{row['user_id']}`)" for row in hardbans]
        pages = []
        for chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
            embed = Embed(title="Hard Banned Users", description="\n".join(chunk))
            pages.append(embed)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    # ---- Massban ----

    @command(aliases=["massb"], example="@x 1d bot owner")
    @has_permissions(ban_members=True)
    async def massban(self, ctx: Context, users: Greedy[Member | User], history: Optional[Range[int, 0, 7]] = None, *, reason: str = "No reason provided"):
        for user in users:
            if isinstance(user, Member) and await self.is_immune(ctx, user):
                return
        if not users:
            return await ctx.warn("You need to provide at least one user!")
        if len(users) > 150:
            return await ctx.warn("You can only ban up to **150 users** at a time!")
        if len(users) > 5:
            await ctx.prompt(f"Are you sure you want to **ban** `{len(users)}` users?")

        async with ctx.typing():
            for user in users:
                if isinstance(user, Member):
                    await TouchableMember().check(ctx, user)
                await ctx.guild.ban(user, delete_message_days=history or 0, reason=f"{ctx.author} / {reason} (MASS BAN)")
        await ModConfig.sendlogs(self.bot, "massban", ctx.author, users, reason)
        await ctx.check()

    # ---- Forcenick ----

    @command(example="@x bot owner", name="fn")
    @has_permissions(manage_nicknames=True)
    async def fn(self, ctx: Context, member: Annotated[Member, TouchableMember], *, nickname: Range[str, 1, 32]):
        if await self.is_immune(ctx, member):
            return
        await self.bot.pool.execute(
            "INSERT INTO forcenick (guild_id, user_id, nickname) VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET nickname = $3",
            ctx.guild.id, member.id, nickname
        )
        await member.edit(nick=nickname, reason=f"{ctx.author} ({ctx.author.id})")
        await ModConfig.sendlogs(self.bot, "forcenick", ctx.author, member, "No reason provided")
        await ctx.check()

    # ---- Ban ----

    @hybrid_command(aliases=["deport", "b"], example="@x 1d bot owner")
    @has_permissions(ban_members=True)
    @max_concurrency(1, BucketType.member)
    async def ban(self, ctx: Context, user: Member | User, history: Optional[Range[int, 0, 7]] = None, *, reason: str = "No reason provided"):
        if isinstance(user, Member):
            await TouchableMember().check(ctx, user)
            if await self.is_immune(ctx, user):
                return
            if user.premium_since:
                await ctx.prompt(f"Are you sure you want to **ban** {user.mention}?", "They are currently boosting the server!")
        # Antinuke check removed
        await ctx.guild.ban(user, delete_message_days=history or 0, reason=f"{ctx.author} / {reason}")
        await ModConfig.sendlogs(self.bot, "ban", ctx.author, user, reason)
        await ctx.check()

    # ---- Softban ----

    @hybrid_command(example="@x 1d bot owner")
    @has_permissions(ban_members=True)
    @max_concurrency(1, BucketType.member)
    async def softban(self, ctx: Context, member: Annotated[Member, TouchableMember], history: Optional[Range[int, 1, 7]] = None, *, reason: str = "No reason provided"):
        if await self.is_immune(ctx, member):
            return
        if member.premium_since:
            await ctx.prompt(f"Are you sure you want to **ban** {member.mention}?", "They are currently boosting the server!")
        # Antinuke check removed
        await ModConfig.sendlogs(self.bot, "ban", ctx.author, member, reason)
        await ctx.guild.ban(member, delete_message_days=history or 0, reason=f"{ctx.author} / {reason}")
        await ctx.guild.unban(member)
        await ctx.check()

    # ---- Unban ----

    @hybrid_group(example="@x 1d bot owner", aliases=["pardon", "unb"], invoke_without_command=True)
    @has_permissions(ban_members=True)
    async def unban(self, ctx: Context, user: User, *, reason: str = "No reason provided"):
        hardban = await self.bot.pool.fetchrow("SELECT * FROM hardban WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, user.id)
        # Hardban bypass check removed (no trusted admin concept)
        await ctx.guild.unban(user, reason=f"{ctx.author} / {reason}")
        await ModConfig.sendlogs(self.bot, "unban", ctx.author, user, reason)
        await ctx.check()

    @unban.command(name="all")
    @has_permissions(ban_members=True)
    @max_concurrency(1, BucketType.guild)
    async def unban_all(self, ctx: Context):
        hardban_ids = await self.bot.pool.fetch("SELECT user_id FROM hardban WHERE guild_id = $1", ctx.guild.id)
        hardban_set = {row['user_id'] for row in hardban_ids}
        users = [entry.user async for entry in ctx.guild.bans() if entry.user.id not in hardban_set]
        if not users:
            return await ctx.warn("There are no banned users!")
        await ctx.prompt(f"Are you sure you want to unban {plural(users, md='`')} users?")
        async with ctx.typing():
            for user in users:
                with suppress(HTTPException):
                    await ctx.guild.unban(user, reason=f"{ctx.author} ({ctx.author.id}) / UNBAN ALL")
        await ctx.check()

    # ---- Nickname ----

    @hybrid_group(example="@x bot owner", aliases=["nick", "n"], invoke_without_command=True)
    @has_permissions(manage_nicknames=True)
    async def nickname(self, ctx: Context, member: Annotated[Member, TouchableMember(allow_author=True)], *, nickname: Range[str, 1, 32]):
        if await self.is_immune(ctx, member):
            return
        # Check if force nickname exists
        forcenick = await self.bot.pool.fetchval("SELECT 1 FROM forcenick WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, member.id)
        if forcenick:
            return await ctx.warn(f"{member.mention} has a forced nickname! Use `{ctx.prefix}nickname remove {member}` to reset it")
        await ModConfig.sendlogs(self.bot, "nickname", ctx.author, member, "No reason provided")
        await member.edit(nick=nickname, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.check()

    @nickname.command(name="remove", aliases=["reset", "rm"], example="@x")
    @has_permissions(manage_nicknames=True)
    async def nickname_remove(self, ctx: Context, member: Annotated[Member, TouchableMember]):
        if await self.is_immune(ctx, member):
            return
        await self.bot.pool.execute("DELETE FROM forcenick WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, member.id)
        await member.edit(nick=None, reason=f"{ctx.author} ({ctx.author.id})")
        await ModConfig.sendlogs(self.bot, "nickname remove", ctx.author, member, "None")
        await ctx.check()

    @nickname.group(name="force", aliases=["lock"], invoke_without_command=True, example="@x bot owner")
    @has_permissions(manage_nicknames=True)
    async def nickname_force(self, ctx: Context, member: Annotated[Member, TouchableMember], *, nickname: Range[str, 1, 32]):
        if await self.is_immune(ctx, member):
            return
        await self.bot.pool.execute(
            "INSERT INTO forcenick (guild_id, user_id, nickname) VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET nickname = $3",
            ctx.guild.id, member.id, nickname
        )
        await member.edit(nick=nickname, reason=f"{ctx.author} ({ctx.author.id})")
        await ModConfig.sendlogs(self.bot, "forcenick", ctx.author, member, "No reason provided")
        await ctx.check()

    @nickname_force.command(name="cancel", aliases=["stop"], example="@x")
    @has_permissions(manage_nicknames=True)
    async def nickname_force_cancel(self, ctx: Context, member: Annotated[Member, TouchableMember]):
        await self.bot.pool.execute("DELETE FROM forcenick WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, member.id)
        await member.edit(nick=None, reason=f"{ctx.author} ({ctx.author.id})")
        await ModConfig.sendlogs(self.bot, "forcenick remove", ctx.author, member, "No reason provided")
        await ctx.check()

    # ---- Timeout ----

    @hybrid_group(example="@x 1d bot owner", aliases=["mute", "tmo", "to"], invoke_without_command=True)
    @has_permissions(moderate_members=True)
    async def timeout(self, ctx: Context, member: Annotated[Member, TouchableMember], duration: timedelta = parameter(converter=Duration(), default=timedelta(minutes=5)), *, reason: str = "No reason provided"):
        if await self.is_immune(ctx, member):
            return
        await member.timeout(duration, reason=f"{ctx.author} / {reason}")
        await ModConfig.sendlogs(self.bot, "timeout", ctx.author, member, reason, duration=duration)
        await ctx.check()

    @timeout.command(name="list", aliases=["ls"])
    @has_permissions(moderate_members=True)
    async def timeout_list(self, ctx: Context):
        members = [m for m in ctx.guild.members if m.is_timed_out()]
        if not members:
            return await ctx.warn("No members are currently timed out!")
        entries = [f"{m.mention} - expires {format_dt(m.timed_out_until or utcnow(), 'R')}" for m in sorted(members, key=lambda m: m.timed_out_until or utcnow())]
        pages = []
        for chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
            embed = Embed(title="Timed Out Members", description="\n".join(chunk))
            pages.append(embed)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    # ---- Untimeout ----

    @hybrid_group(example="@x bot owner", aliases=["unmute", "untmo", "unto", "utmo", "uto"], invoke_without_command=True)
    @has_permissions(moderate_members=True)
    async def untimeout(self, ctx: Context, member: Annotated[Member, TouchableMember], *, reason: str = "No reason provided"):
        if not member.is_timed_out():
            return await ctx.warn("That member isn't timed out!")
        await member.timeout(None, reason=f"{ctx.author} / {reason}")
        await ModConfig.sendlogs(self.bot, "untimeout", ctx.author, member, reason)
        await ctx.check()

    @untimeout.command(name="all")
    @max_concurrency(1, BucketType.guild)
    @has_permissions(moderate_members=True)
    async def untimeout_all(self, ctx: Context):
        members = [m for m in ctx.guild.members if m.is_timed_out()]
        if not members:
            return await ctx.warn("No members are currently timed out!")
        async with ctx.typing():
            for member in members:
                with suppress(HTTPException):
                    await member.timeout(None, reason=f"{ctx.author} ({ctx.author.id}) lifted all timeouts")
        await ctx.check()

    # ---- Emoji commands ----

    @group(aliases=["emote", "e", "jumbo"], invoke_without_command=True)
    @has_permissions(manage_expressions=True)
    async def emoji(self, ctx: Context, emoji: PartialEmoji | str):
        if isinstance(emoji, str):
            url, name = unicode_emoji(emoji)
        else:
            url, name = emoji.url, emoji.name
        response = await self.bot.session.get(url)
        if not response.ok:
            return await ctx.send_help(ctx.command)
        buffer = await response.read()
        _, suffix = url_to_mime(url)
        image, suffix = await enlarge_emoji(buffer, suffix[1:])
        if not image:
            return await ctx.warn("There was an issue downloading that emoji!")
        try:
            await ctx.send(file=File(BytesIO(image), filename=f"{name}.{suffix}"))
        except HTTPException:
            await ctx.warn("The enlarged emoji was too large to send!")

    @emoji.command(name="information", aliases=["i", "info", "details"])
    async def emoji_information(self, ctx: Context, emoji: PartialEmoji):
        button = discord.ui.Button(label="Emoji URL", style=discord.ButtonStyle.gray, emoji=emoji, url=emoji.url)
        embed = Embed(title=f"{emoji.name}")
        embed.set_thumbnail(url=emoji.url)
        embed.add_field(name="ID", value=emoji.id, inline=False)
        embed.add_field(name="Animated", value=emoji.animated, inline=False)
        embed.add_field(name="Code", value=f"`{emoji}`", inline=False)
        embed.add_field(name="Created At", value=format_dt(emoji.created_at, "R"), inline=False)
        view = discord.ui.View()
        view.add_item(button)
        await ctx.send(embed=embed, view=view)

    @emoji.command(name="sticker")
    @has_permissions(manage_expressions=True)
    async def emoji_sticker(self, ctx: Context, name: Optional[Range[str, 2, 32]], emoji: PartialEmoji):
        if len(ctx.guild.stickers) == ctx.guild.sticker_limit:
            return await ctx.warn("The server is at the **maximum** amount of stickers!")
        try:
            sticker = await ctx.guild.create_sticker(
                name=name or emoji.name,
                description="ya",
                emoji=str(emoji),
                file=File(BytesIO(await emoji.read())),
                reason=f"Created by {ctx.author} ({ctx.author.id})",
            )
        except RateLimited as exc:
            retry_after = timedelta(seconds=exc.retry_after)
            return await ctx.warn(f"The server is currently ratelimited, try again in **{precisedelta(retry_after)}**!")
        except HTTPException as exc:
            return await ctx.warn("Failed to create the sticker!", codeblock(exc.text))
        await ctx.approve(f"Created sticker [{sticker.name}]({sticker.url})")

    @emoji.group(name="add", aliases=["create", "upload", "steal"], invoke_without_command=True)
    @has_permissions(manage_expressions=True)
    async def emoji_add(self, ctx: Context, image: PartialEmoji | PartialAttachment = parameter(default=PartialAttachment.fallback), *, name: Optional[Range[str, 2, 32]]):
        if not image.url:
            return await ctx.send_help(ctx.command)
        if len(ctx.guild.emojis) == ctx.guild.emoji_limit:
            return await ctx.warn("The server is at the **maximum** amount of emojis!")
        try:
            await ctx.guild.create_custom_emoji(
                name=name or (image.name if isinstance(image, PartialEmoji) else image.filename),
                image=(await image.read() if isinstance(image, PartialEmoji) else image.buffer),
                reason=f"Created by {ctx.author} ({ctx.author.id})",
            )
        except RateLimited as exc:
            retry_after = timedelta(seconds=exc.retry_after)
            return await ctx.warn(f"The server is currently ratelimited, try again in **{precisedelta(retry_after)}**!")
        except HTTPException as exc:
            return await ctx.warn("Failed to create the emoji!", codeblock(exc.text))
        await ctx.check()

    @emoji_add.command(name="reactions", aliases=["reaction", "reacts", "react"], example="324325345436")
    @has_permissions(manage_expressions=True)
    async def emoji_add_reactions(self, ctx: Context, message: Optional[Message]):
        message = message or ctx.replied_message
        if not message:
            async for _message in ctx.channel.history(limit=25, before=ctx.message):
                if _message.reactions:
                    message = _message
                    break
        if not message:
            return await ctx.send_help(ctx.command)
        if not message.reactions:
            return await ctx.warn("That message doesn't have any reactions!")
        added_emojis = []
        async with ctx.typing():
            for reaction in message.reactions:
                if not reaction.is_custom_emoji():
                    continue
                emoji = reaction.emoji
                if isinstance(emoji, str):
                    continue
                try:
                    emoji = await ctx.guild.create_custom_emoji(
                        name=emoji.name,
                        image=await emoji.read(),
                        reason=f"Created by {ctx.author} ({ctx.author.id})",
                    )
                except RateLimited as exc:
                    return await ctx.warn(
                        f"Ratelimited after {plural(added_emojis, md='`')} emojis!",
                        f"Please wait **{format_timespan(int(exc.retry_after))}** before trying again",
                        patch=ctx.response,
                    )
                except HTTPException:
                    if len(ctx.guild.emojis) + len(added_emojis) > ctx.guild.emoji_limit:
                        return await ctx.warn("The maximum amount of emojis has been reached!", patch=ctx.response)
                    break
                added_emojis.append(emoji)
        await ctx.approve(
            f"Added {plural(added_emojis, md='`')} emojis to the server"
            + (f" (`{len(message.reactions) - len(added_emojis)}` failed)" if len(added_emojis) < len(message.reactions) else "")
        )

    @emoji_add.command(name="many", aliases=["bulk", "batch"])
    @has_permissions(manage_expressions=True)
    async def emoji_add_many(self, ctx: Context, *emojis: PartialEmoji):
        if not emojis:
            return await ctx.send_help(ctx.command)
        if len(emojis) > 50:
            return await ctx.warn("You can only add up to **50 emojis** at a time!")
        if len(ctx.guild.emojis) + len(emojis) > ctx.guild.emoji_limit:
            return await ctx.warn("The server doesn't have enough space for all the emojis!")
        added_emojis = []
        async with ctx.typing():
            for emoji in emojis:
                try:
                    emoji = await ctx.guild.create_custom_emoji(
                        name=emoji.name,
                        image=await emoji.read(),
                        reason=f"Created by {ctx.author} ({ctx.author.id})",
                    )
                except RateLimited as exc:
                    return await ctx.warn(
                        f"Ratelimited after {plural(added_emojis, md='`')} emojis!",
                        f"Please wait **{format_timespan(int(exc.retry_after))}** before trying again",
                        patch=ctx.response,
                    )
                except HTTPException:
                    if len(ctx.guild.emojis) + len(emojis) > ctx.guild.emoji_limit:
                        return await ctx.warn("The maximum amount of emojis has been reached!", patch=ctx.response)
                    break
                added_emojis.append(emoji)
        await ctx.approve(
            f"Added {plural(added_emojis, md='`')} emojis to the server"
            + (f" (`{len(emojis) - len(added_emojis)}` failed)" if len(added_emojis) < len(emojis) else "")
        )

    @emoji.command(name="rename", aliases=["name"], example="hi")
    @has_permissions(manage_expressions=True)
    async def emoji_rename(self, ctx: Context, emoji: Emoji, *, name: str):
        if emoji.guild_id != ctx.guild.id:
            return await ctx.warn("That emoji is not in this server!")
        if len(name) < 2:
            return await ctx.warn("The emoji name must be at least **2 characters** long!")
        name = name[:32].replace(" ", "_")
        await emoji.edit(name=name, reason=f"Updated by {ctx.author} ({ctx.author.id})")
        await ctx.approve(f"Renamed the emoji to **{name}**")

    @emoji.command(name="delete", aliases=["remove", "del"])
    @has_permissions(manage_expressions=True)
    async def emoji_delete(self, ctx: Context, emoji: Emoji):
        if emoji.guild_id != ctx.guild.id:
            return await ctx.warn("That emoji is not in this server!")
        await emoji.delete(reason=f"Deleted by {ctx.author} ({ctx.author.id})")
        await ctx.check()

    @emoji.group(name="archive", aliases=["zip"], invoke_without_command=True)
    @has_permissions(manage_expressions=True)
    @cooldown(1, 30, BucketType.guild)
    async def emoji_archive(self, ctx: Context):
        if ctx.guild.premium_tier < 2:
            return await ctx.warn("The server must have at least Level 2 to use this command!")
        await ctx.embed(description="Starting the archival process...")
        async with ctx.typing():
            buffer = BytesIO()
            with ZipFile(buffer, "w") as zip:
                for index, emoji in enumerate(ctx.guild.emojis):
                    name = f"{emoji.name}.{emoji.animated and 'gif' or 'png'}"
                    if name in zip.namelist():
                        name = f"{emoji.name}_{index}.{emoji.animated and 'gif' or 'png'}"
                    __buffer = await emoji.read()
                    zip.writestr(name, __buffer)
            buffer.seek(0)
        if ctx.response:
            with suppress(HTTPException):
                await ctx.response.delete()
        await ctx.send(file=File(buffer, filename=f"{ctx.guild.name}_emojis.zip"))

    @emoji_archive.command(name="restore", aliases=["load"])
    @has_permissions(manage_expressions=True)
    @max_concurrency(1, BucketType.guild)
    async def emoji_archive_restore(self, ctx: Context, attachment: PartialAttachment = parameter(default=PartialAttachment.fallback)):
        if not attachment.is_archive():
            return await ctx.warn("The attachment must be a zip archive!")
        await ctx.embed(description="Starting the restoration process...")
        emojis = []
        buffer = BytesIO(attachment.buffer)
        with ZipFile(buffer, "r") as zip:
            if len(zip.namelist()) > (ctx.guild.emoji_limit - len(ctx.guild.emojis)):
                return await ctx.warn("The server doesn't have enough space for all the emojis in the archive!", patch=ctx.response)
            for name in zip.namelist():
                if not name.endswith((".png", ".gif")):
                    continue
                name = name[:-4]
                if get(ctx.guild.emojis, name=name):
                    continue
                try:
                    emoji = await ctx.guild.create_custom_emoji(
                        name=name[:-4],
                        image=zip.read(name),
                        reason=f"Archive loaded by {ctx.author} ({ctx.author.id})",
                    )
                except RateLimited as exc:
                    return await ctx.warn(f"Ratelimited after {plural(emojis, md='`')} emojis! Please wait **{format_timespan(int(exc.retry_after))}** before trying again", patch=ctx.response)
                except HTTPException:
                    if len(ctx.guild.emojis) == ctx.guild.emoji_limit:
                        return await ctx.warn("The maximum amount of emojis has been reached!", patch=ctx.response)
                    break
                emojis.append(emoji)
        if ctx.response:
            await quietly_delete(ctx.response)
        await ctx.approve(f"Restored {plural(emojis, md='`')} emojis from [`{attachment.filename}`]({attachment.url})")

    # ---- Sticker commands ----

    @group(name="sticker", invoke_without_command=True)
    @has_permissions(manage_expressions=True)
    async def sticker(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @sticker.command(name="add", aliases=["create", "upload"], example="new sticker")
    @has_permissions(manage_expressions=True)
    async def sticker_add(self, ctx: Context, name: Optional[Range[str, 2, 32]]):
        if not ctx.message.stickers or not (sticker := ctx.message.stickers[0]):
            return await ctx.send_help(ctx.command)
        if len(ctx.guild.stickers) == ctx.guild.sticker_limit:
            return await ctx.warn("The server is at the **maximum** amount of stickers!")
        sticker = await sticker.fetch()
        if not isinstance(sticker, GuildSticker):
            return await ctx.warn("Stickers cannot be default stickers!")
        try:
            await ctx.guild.create_sticker(
                name=name or sticker.name,
                description=sticker.description,
                emoji=sticker.emoji,
                file=File(BytesIO(await sticker.read())),
                reason=f"Created by {ctx.author} ({ctx.author.id})",
            )
        except RateLimited as exc:
            retry_after = timedelta(seconds=exc.retry_after)
            return await ctx.warn(f"The server is currently ratelimited, try again in **{precisedelta(retry_after)}**!")
        except HTTPException as exc:
            return await ctx.warn("Failed to create the sticker!", codeblock(exc.text))
        await ctx.check()

    @sticker.command(name="tag")
    @has_permissions(manage_expressions=True)
    async def sticker_tag(self, ctx: Context):
        if not ctx.guild.vanity_url_code:
            return await ctx.warn("The server doesn't have a vanity URL!")
        async with ctx.typing():
            for sticker in ctx.guild.stickers:
                if ctx.guild.vanity_url_code in sticker.name:
                    continue
                try:
                    await sticker.edit(name=f"{sticker.name} /{ctx.guild.vanity_url_code}", reason=f"Updated by {ctx.author} ({ctx.author.id})")
                except HTTPException as e:
                    return await ctx.warn(f"Failed to rename all stickers!\n{e}")
            await ctx.check()

    @sticker.command(name="steal", aliases=["grab"], example="new sticker")
    @has_permissions(manage_expressions=True)
    async def sticker_steal(self, ctx: Context, name: Optional[Range[str, 2, 32]] = None):
        message = ctx.replied_message
        if not message:
            async for _message in ctx.channel.history(limit=25, before=ctx.message):
                if _message.stickers:
                    message = _message
                    break
        if not message:
            return await ctx.warn("I couldn't find a message with a sticker in the past 25 messages!")
        if not message.stickers:
            return await ctx.warn("That message doesn't have any stickers!")
        if len(ctx.guild.stickers) == ctx.guild.sticker_limit:
            return await ctx.warn("The server is at the **maximum** amount of stickers!")
        sticker = await message.stickers[0].fetch()
        if not isinstance(sticker, GuildSticker):
            return await ctx.warn("Stickers cannot be default stickers!")
        if sticker.guild_id == ctx.guild.id:
            return await ctx.warn("That sticker is already in this server!")
        try:
            await ctx.guild.create_sticker(
                name=name or sticker.name,
                description=sticker.description,
                emoji=sticker.emoji,
                file=File(BytesIO(await sticker.read())),
                reason=f"Created by {ctx.author} ({ctx.author.id})",
            )
        except RateLimited as exc:
            retry_after = timedelta(seconds=exc.retry_after)
            return await ctx.warn(f"The server is currently ratelimited, try again in **{precisedelta(retry_after)}**!")
        except HTTPException as exc:
            return await ctx.warn("Failed to create the sticker!", codeblock(exc.text))
        await ctx.check()

    @sticker.command(name="rename", aliases=["name"], example="new sticker")
    @has_permissions(manage_expressions=True)
    async def sticker_rename(self, ctx: Context, *, name: str):
        sticker = None
        if ctx.message.stickers:
            sticker = ctx.message.stickers[0]
        elif ctx.replied_message and ctx.replied_message.stickers:
            sticker = ctx.replied_message.stickers[0]
        if not sticker:
            return await ctx.send_help(ctx.command)
        sticker = await sticker.fetch()
        if not isinstance(sticker, GuildSticker):
            return await ctx.warn("Stickers cannot be default stickers!")
        if sticker.guild_id != ctx.guild.id:
            return await ctx.warn("That sticker is not in this server!")
        if len(name) < 2:
            return await ctx.warn("The sticker name must be at least **2 characters** long!")
        name = name[:32]
        await sticker.edit(name=name, reason=f"Updated by {ctx.author} ({ctx.author.id})")
        await ctx.approve(f"Renamed the sticker to **{name}**")

    @sticker.command(name="delete", aliases=["remove", "del"])
    @has_permissions(manage_expressions=True)
    async def sticker_delete(self, ctx: Context):
        if not (sticker := ctx.message.stickers[0]):
            return await ctx.send_help(ctx.command)
        sticker = await sticker.fetch()
        if not isinstance(sticker, GuildSticker):
            return await ctx.warn("Stickers cannot be default stickers!")
        if sticker.guild_id != ctx.guild.id:
            return await ctx.warn("That sticker is not in this server!")
        await sticker.delete(reason=f"Deleted by {ctx.author} ({ctx.author.id})")
        await ctx.check()

    @sticker.command(name="archive", aliases=["zip"])
    @has_permissions(manage_expressions=True)
    @cooldown(1, 30, BucketType.guild)
    async def sticker_archive(self, ctx: Context):
        if ctx.guild.premium_tier < 2:
            return await ctx.warn("The server must have at least Level 2 to use this command!")
        await ctx.embed(description="Starting the archival process...")
        async with ctx.typing():
            buffer = BytesIO()
            with ZipFile(buffer, "w") as zip:
                for index, sticker in enumerate(ctx.guild.stickers):
                    name = f"{sticker.name}.{sticker.format}"
                    if name in zip.namelist():
                        name = f"{sticker.name}_{index}.{sticker.format}"
                    __buffer = await sticker.read()
                    zip.writestr(name, __buffer)
            buffer.seek(0)
        if ctx.response:
            with suppress(HTTPException):
                await ctx.response.delete()
        await ctx.send(file=File(buffer, filename=f"{ctx.guild.name}_stickers.zip"))

    # ---- Guild set commands ----

    @group(name="set", aliases=["editserver"], invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def guild_set(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @guild_set.command(name="name", aliases=["n"], example="new server")
    @has_permissions(manage_guild=True)
    async def guild_set_name(self, ctx: Context, *, name: Range[str, 1, 100]):
        try:
            await ctx.guild.edit(name=name, reason=f"{ctx.author} ({ctx.author.id})")
        except HTTPException:
            return await ctx.warn("Failed to change the server's name!")
        await ctx.check()

    @guild_set.command(name="icon", aliases=["pfp", "i"], example="https://example.com/icon.png")
    @has_permissions(manage_guild=True)
    async def guild_set_icon(self, ctx: Context, attachment: PartialAttachment = parameter(default=PartialAttachment.fallback)):
        if not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")
        await ctx.guild.edit(icon=attachment.buffer, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.check()

    @guild_set.command(name="splash", aliases=["background", "bg"], example="https://example.com/splash.png")
    @has_permissions(manage_guild=True)
    async def guild_set_splash(self, ctx: Context, attachment: PartialAttachment = parameter(default=PartialAttachment.fallback)):
        if not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")
        await ctx.guild.edit(splash=attachment.buffer, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.check()

    @guild_set.command(name="banner", aliases=["b"], example="https://example.com/banner.png")
    @has_permissions(manage_guild=True)
    async def guild_set_banner(self, ctx: Context, attachment: PartialAttachment = parameter(default=PartialAttachment.fallback)):
        if ctx.guild.premium_tier < 2:
            return await ctx.warn("Server must be at least Level 2 to set a banner.")
        if not attachment.is_image():
            return await ctx.warn("The attachment must be an image!")
        await ctx.guild.edit(banner=attachment.buffer, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.check()

    @guild_set.group(name="system", aliases=["sys"], invoke_without_command=True, example="#staff")
    @has_permissions(manage_guild=True)
    async def guild_set_system(self, ctx: Context, *, channel: TextChannel):
        await ctx.guild.edit(system_channel=channel, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.check()

    @guild_set_system.group(name="welcome", aliases=["welc"], invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def guild_set_system_welcome(self, ctx: Context):
        flags = ctx.guild.system_channel_flags
        flags.join_notifications = not flags.join_notifications
        await ctx.guild.edit(system_channel_flags=flags, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.approve(f"{'Now' if flags.join_notifications else 'No longer'} sending integrated **welcome messages**")

    @guild_set_system_welcome.command(name="sticker", aliases=["stickers", "wave"])
    @has_permissions(manage_guild=True)
    async def guild_set_system_welcome_sticker(self, ctx: Context):
        flags = ctx.guild.system_channel_flags
        flags.join_notification_replies = not flags.join_notification_replies
        await ctx.guild.edit(system_channel_flags=flags, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.approve(f"{'Now' if flags.join_notification_replies else 'No longer'} adding a **welcome sticker**")

    @guild_set_system.command(name="boost", aliases=["boosts"])
    @has_permissions(manage_guild=True)
    async def guild_set_system_boost(self, ctx: Context):
        flags = ctx.guild.system_channel_flags
        flags.premium_subscriptions = not flags.premium_subscriptions
        await ctx.guild.edit(system_channel_flags=flags, reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.approve(f"{'Now' if flags.premium_subscriptions else 'No longer'} sending integrated **boost messages**")

    @guild_set.command(name="notifications", aliases=["notis", "noti"], example="all")
    @has_permissions(manage_guild=True)
    async def guild_set_notifications(self, ctx: Context, option: Literal["all", "mentions"]):
        await ctx.guild.edit(
            default_notifications=NotificationLevel.all_messages if option == "all" else NotificationLevel.only_mentions,
            reason=f"{ctx.author} ({ctx.author.id})"
        )
        await ctx.check()

    # ---- Nuke ----

    @command()
    @has_permissions(manage_channels=True)
    async def nuke(self, ctx: Context):
        channel = ctx.channel
        if not isinstance(channel, TextChannel):
            return await ctx.warn("You can only nuke text channels!")
        await ctx.prompt("Are you sure you want to **nuke** this channel?", "This action is **irreversable** and will delete the channel!")
        new_channel = await channel.clone(reason=f"Nuked by {ctx.author} ({ctx.author.id})")
        reconfigured = await self.reconfigure_settings(ctx.guild, channel, new_channel)
        await asyncio.gather(
            new_channel.edit(position=channel.position),
            channel.delete(reason=f"Nuked by {ctx.author} ({ctx.author.id})"),
        )
        embed = Embed(title="Channel Nuked", description=f"This channel has been nuked by {ctx.author.mention}")
        if reconfigured:
            embed.add_field(name="**Reconfigured Settings**", value="\n".join(reconfigured))
        await new_channel.send(embed=embed)
        await new_channel.send("first")

    # ---- Pin / Unpin ----

    @command(example="3423346")
    @has_permissions(manage_messages=True)
    async def pin(self, ctx: Context, message: Optional[Message]):
        message = message or ctx.replied_message
        if not message:
            async for m in ctx.channel.history(limit=1, before=ctx.message):
                message = m
                break
        if not message:
            return await ctx.send_help(ctx.command)
        if message.guild != ctx.guild:
            return await ctx.warn("The message must be in this server!")
        if message.pinned:
            return await ctx.warn(f"That [`message`]({message.jump_url}) is already pinned!")
        await message.pin(reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.check()

    @command(example="232133425")
    @has_permissions(manage_messages=True)
    async def unpin(self, ctx: Context, message: Optional[Message]):
        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)
        if message.guild != ctx.guild:
            return await ctx.warn("The message must be in this server!")
        if not message.pinned:
            return await ctx.warn(f"That [`message`]({message.jump_url}) is not pinned!")
        await message.unpin(reason=f"{ctx.author} ({ctx.author.id})")
        await ctx.check()

    # ---- Strip ----

    @hybrid_command()
    @has_permissions(administrator=True)
    async def strip(self, ctx: Context, user: Member, *, reason: str = "No reason provided"):
        await TouchableMember().check(ctx, user)
        if await self.is_immune(ctx, user):
            return
        dangerous_permissions = [
            'administrator', 'manage_guild', 'manage_roles', 'manage_channels',
            'manage_webhooks', 'manage_nicknames', 'manage_emojis',
            'kick_members', 'ban_members', 'mention_everyone'
        ]
        roles_to_remove = []
        for role in user.roles[1:]:
            for perm, value in role.permissions:
                if perm in dangerous_permissions and value:
                    roles_to_remove.append(role)
                    break
        if not roles_to_remove:
            return await ctx.warn(f"{user.mention} has no dangerous permissions to strip!")
        # store roles (restore not implemented)
        await user.remove_roles(*roles_to_remove, reason=f"Stripped by {ctx.author} ({ctx.author.id}): {reason}")
        await ModConfig.sendlogs(self.bot, "strip", ctx.author, user, reason)
        await ctx.check()

    # ---- Jail / Unjail ----

    @Mod.is_mod_configured()
    @hybrid_command()
    @has_permissions(manage_channels=True)
    async def jail(self, ctx: Context, user: Member, *, reason: str = "No reason provided"):
        await TouchableMember().check(ctx, user)
        if await self.is_immune(ctx, user):
            return
        check = await self.bot.pool.fetchrow("SELECT * FROM jail WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, user.id)
        if check:
            return await ctx.warn(f"**{user.mention}** is already jailed!")
        roles = [r.id for r in user.roles if r.name != "@everyone" and r.is_assignable() and not r.is_premium_subscriber()]
        sql_as_text = json.dumps(roles)
        await self.bot.pool.execute(
            "INSERT INTO jail (guild_id, user_id, roles) VALUES ($1, $2, $3)",
            ctx.guild.id, user.id, sql_as_text
        )
        mod = await self.bot.pool.fetchrow("SELECT * FROM mod WHERE guild_id = $1", ctx.guild.id)
        if not mod:
            return await ctx.warn("Moderation not configured. Run `setme` first.")
        jail_role = ctx.guild.get_role(mod["role_id"])
        if not jail_role:
            return await ctx.warn("Jail role not found. Re-run `setme`.")
        new_roles = [r for r in user.roles if not r.is_assignable()]
        new_roles.append(jail_role)
        await user.edit(roles=new_roles, reason=f"Jailed by {ctx.author} - {reason}")
        await ctx.approve(f"**{user}** got jailed - {reason}")
        await ModConfig.sendlogs(self.bot, "jail", ctx.author, user, reason)
        jail_channel = ctx.guild.get_channel(mod["jail_id"])
        if jail_channel:
            await jail_channel.send(f"{user.mention}, you have been jailed! Wait for a staff member to unjail you.")

    @Mod.is_mod_configured()
    @hybrid_command()
    @has_permissions(manage_channels=True)
    async def unjail(self, ctx: Context, member: discord.Member, *, reason: str = "No reason provided"):
        jail_entry = await self.bot.pool.fetchrow("SELECT * FROM jail WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, member.id)
        if not jail_entry:
            return await ctx.warn(f"**{member.mention}** is not jailed!")
        mod = await self.bot.pool.fetchrow("SELECT * FROM mod WHERE guild_id = $1", ctx.guild.id)
        if not mod:
            return await ctx.warn("Moderation not configured.")
        jail_role = ctx.guild.get_role(mod["role_id"])
        if not jail_role:
            return await ctx.warn("Jail role not found.")
        roles_to_add = [ctx.guild.get_role(rid) for rid in json.loads(jail_entry["roles"]) if ctx.guild.get_role(rid) and ctx.guild.get_role(rid).is_assignable()]
        if roles_to_add:
            await member.edit(roles=roles_to_add, reason=f"Unjailed by {ctx.author} - {reason}")
        await member.remove_roles(jail_role, reason=f"Unjailed by {ctx.author}")
        await self.bot.pool.execute("DELETE FROM jail WHERE user_id = $1 AND guild_id = $2", member.id, ctx.guild.id)
        await ctx.approve(f"Unjailed **{member.mention}**")
        await ModConfig.sendlogs(self.bot, "unjail", ctx.author, member, reason)

    # ---- Setme / Unsetme ----

    @hybrid_command()
    @has_permissions(manage_guild=True, manage_channels=True, manage_roles=True, view_channel=True)
    async def setme(self, ctx: Context):
        if not (ctx.guild.me.guild_permissions.manage_roles and ctx.guild.me.guild_permissions.manage_channels):
            return await ctx.warn("I need **Manage Roles** and **Manage Channels** permissions!")
        check = await self.bot.pool.fetchrow("SELECT * FROM mod WHERE guild_id = $1", ctx.guild.id)
        if check:
            return await ctx.warn("The jail system is **already** enabled in this server!")

        await ctx.typing()
        try:
            category = await ctx.guild.create_category(name="xrypton mod")
            role = await ctx.guild.create_role(name="xrypton-jail")

            for channel in ctx.guild.channels:
                try:
                    await channel.set_permissions(role, view_channel=False)
                except discord.Forbidden:
                    continue

            overwrite = {
                role: discord.PermissionOverwrite(view_channel=True),
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                ctx.guild.me: discord.PermissionOverwrite(view_channel=True)
            }
            text = await ctx.guild.create_text_channel(name="mod-logs", overwrites=overwrite, category=category)
            jai = await ctx.guild.create_text_channel(name="jail", overwrites=overwrite, category=category)
        except discord.Forbidden:
            return await ctx.warn("I don't have permission to create channels!")
        except discord.HTTPException as e:
            return await ctx.warn(f"Failed to create channels: {str(e)}")

        await self.bot.pool.execute(
            "INSERT INTO mod (guild_id, channel_id, jail_id, role_id) VALUES ($1, $2, $3, $4)",
            ctx.guild.id, text.id, jai.id, role.id
        )
        await self.bot.pool.execute("INSERT INTO cases (guild_id, count) VALUES ($1, $2)", ctx.guild.id, 0)
        await ctx.approve("I have **enabled** the jail system!")

    @Mod.is_mod_configured()
    @hybrid_command()
    @has_permissions(manage_guild=True, manage_channels=True, manage_roles=True, view_channel=True)
    async def unsetme(self, ctx: Context):
        check = await self.bot.pool.fetchrow("SELECT * FROM mod WHERE guild_id = $1", ctx.guild.id)
        if not check:
            return await ctx.warn("The jail system is **not** enabled in this server!")
        view = ClearMod(ctx)
        view.message = await ctx.send(view=view, embed=Embed(description="> Are you sure you want to disable jail?"))

    # ---- Fake Permissions ----

    @has_permissions(guild_owner=True)
    @group(invoke_without_command=True, aliases=["fp"])
    async def fakepermissions(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @has_permissions(guild_owner=True)
    @fakepermissions.command(name="grant", aliases=["add"], example="@admin administrator")
    async def fakepermissions_grant(self, ctx: Context, role: Role, *, permissions: str):
        perms_list = [p.strip().lower() for p in permissions.split(",")]
        valid_perms = [
            "administrator", "ban_members", "kick_members", "manage_guild",
            "manage_channels", "manage_roles", "manage_messages", "view_audit_log",
            "manage_webhooks", "manage_expressions", "mute_members", "deafen_members",
            "move_members", "manage_nicknames", "mention_everyone", "view_guild_insights",
            "external_emojis", "moderate_members"
        ]
        invalid = [p for p in perms_list if p not in valid_perms]
        if invalid:
            return await ctx.warn(f"Invalid permissions. Run `{ctx.clean_prefix}fakepermissions permissions` to see valid ones.")
        check = await self.bot.pool.fetchrow("SELECT permission FROM fake_permissions WHERE guild_id = $1 AND role_id = $2", ctx.guild.id, role.id)
        if check:
            perms = json.loads(check["permission"])
            already = [p for p in perms_list if p in perms]
            if already:
                return await ctx.warn(f"{role.mention} already has fake permissions: {', '.join(f'`{p}`' for p in already)}!")
            perms.extend(perms_list)
            await self.bot.pool.execute(
                "UPDATE fake_permissions SET permission = $1 WHERE guild_id = $2 AND role_id = $3",
                json.dumps(perms), ctx.guild.id, role.id
            )
        else:
            await self.bot.pool.execute(
                "INSERT INTO fake_permissions (guild_id, role_id, permission) VALUES ($1, $2, $3)",
                ctx.guild.id, role.id, json.dumps(perms_list)
            )
        await ctx.approve(f"Added {', '.join(f'`{p}`' for p in perms_list)} to {role.mention}'s fake permissions.")

    @has_permissions(guild_owner=True)
    @fakepermissions.command(name="revoke", aliases=["remove"], example="@mod administrator")
    async def fakepermissions_revoke(self, ctx: Context, role: Role, *, permissions: str):
        perms_list = [p.strip().lower() for p in permissions.split(",")]
        valid_perms = ["administrator", "ban_members", "kick_members", "manage_guild", "manage_channels", "manage_roles", "manage_messages", "view_audit_log", "manage_webhooks", "manage_expressions", "mute_members", "deafen_members", "move_members", "manage_nicknames", "mention_everyone", "view_guild_insights", "external_emojis", "moderate_members"]
        invalid = [p for p in perms_list if p not in valid_perms]
        if invalid:
            return await ctx.warn(f"Invalid permissions. Run `{ctx.clean_prefix}fakepermissions permissions` to see valid ones.")
        check = await self.bot.pool.fetchrow("SELECT permission FROM fake_permissions WHERE guild_id = $1 AND role_id = $2", ctx.guild.id, role.id)
        if not check:
            return await ctx.warn(f"{role.mention} doesn't have any fake permissions!")
        perms = json.loads(check["permission"])
        removed = [p for p in perms_list if p in perms]
        if not removed:
            return await ctx.warn(f"{role.mention} doesn't have the specified fake permissions!")
        perms = [p for p in perms if p not in removed]
        if perms:
            await self.bot.pool.execute(
                "UPDATE fake_permissions SET permission = $1 WHERE guild_id = $2 AND role_id = $3",
                json.dumps(perms), ctx.guild.id, role.id
            )
        else:
            await self.bot.pool.execute(
                "DELETE FROM fake_permissions WHERE guild_id = $1 AND role_id = $2",
                ctx.guild.id, role.id
            )
        await ctx.approve(f"Removed {', '.join(f'`{p}`' for p in removed)} from {role.mention}'s fake permissions.")

    @fakepermissions.command(name="list", aliases=["ls"])
    @has_permissions(guild_owner=True)
    async def fakepermissions_list(self, ctx: Context):
        records = await self.bot.pool.fetch("SELECT role_id, permission FROM fake_permissions WHERE guild_id = $1", ctx.guild.id)
        if not records:
            return await ctx.warn("No fake permissions exist for this server!")
        entries = []
        for record in records:
            role = ctx.guild.get_role(record["role_id"])
            if role:
                perms = json.loads(record["permission"])
                for p in perms:
                    entries.append(f"{role.mention} (`{p}`)")
        if not entries:
            return await ctx.warn("No valid fake permissions found.")
        pages = []
        for chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
            embed = Embed(title="Fake Permissions", description="\n".join(chunk))
            pages.append(embed)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    @fakepermissions.command(name="permissions", aliases=["perms"])
    @has_permissions(guild_owner=True)
    async def fakepermissions_permissions(self, ctx: Context):
        valid = ["administrator", "ban_members", "kick_members", "manage_guild", "manage_channels", "manage_roles", "manage_messages", "view_audit_log", "manage_webhooks", "manage_expressions", "mute_members", "deafen_members", "move_members", "manage_nicknames", "mention_everyone", "view_guild_insights", "external_emojis", "moderate_members"]
        embed = Embed(title="Valid Fake Permissions", description="\n".join(f"• `{p}`" for p in valid))
        await ctx.send(embed=embed)

    # ---- Mod History ----

    @command(example="@x")
    @has_permissions(manage_guild=True)
    async def modhistory(self, ctx: Context, moderator: Member = None):
        moderator = moderator or ctx.author
        cases = await self.bot.pool.fetch(
            "SELECT * FROM moderation WHERE moderator_id = $1 AND guild_id = $2 ORDER BY case_id DESC",
            moderator.id, ctx.guild.id
        )
        if not cases:
            return await ctx.warn(f"No moderation history found for {moderator.mention}")
        entries = []
        for case in cases:
            duration_str = f"\nDuration: {humanize.naturaldelta(case['duration'])}" if case['duration'] else ""
            ts = format_dt(case['timestamp'], 'F')
            entries.append(
                f"**Case #{case['case_id']}**\nAction: {case['action']}\nUser: `{case['user_id']}`\nDate: {ts}\nReason: {case['reason']}{duration_str}"
            )
        embed = Embed(title=f"Mod history for {moderator}", footer={"text": f"{len(cases)} total cases"})
        pages = []
        for chunk in [entries[i:i+3] for i in range(0, len(entries), 3)]:
            e = embed.copy()
            e.description = "\n\n".join(chunk)
            pages.append(e)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    @command(example="@x")
    @has_permissions(manage_guild=True)
    async def history(self, ctx: Context, user: Member | User = None):
        user = user or ctx.author
        cases = await self.bot.pool.fetch(
            "SELECT * FROM moderation WHERE user_id = $1 AND guild_id = $2 ORDER BY case_id DESC",
            user.id, ctx.guild.id
        )
        if not cases:
            return await ctx.warn(f"No moderation history found for {user.mention}")
        entries = []
        for case in cases:
            duration_str = f"\nDuration: {humanize.naturaldelta(case['duration'])}" if case['duration'] else ""
            ts = format_dt(case['timestamp'], 'F')
            entries.append(
                f"**Case #{case['case_id']}**\nAction: {case['action']}\nModerator: `{case['moderator_id']}`\nDate: {ts}\nReason: {case['reason']}{duration_str}"
            )
        embed = Embed(title=f"History for {user}", footer={"text": f"{len(cases)} total cases"})
        pages = []
        for chunk in [entries[i:i+3] for i in range(0, len(entries), 3)]:
            e = embed.copy()
            e.description = "\n\n".join(chunk)
            pages.append(e)
        paginator = Paginator(ctx, pages)
        await paginator.start()

    # ---- Picperms ----

    @command(aliases=["pic", "pictureperms", "picture"], example="#general @x")
    @has_permissions(manage_roles=True)
    async def picperms(self, ctx: Context, channel: Optional[TextChannel], user: Member):
        if channel is None:
            channel = ctx.channel
        await TouchableMember().check(ctx, user)
        perms = channel.permissions_for(user)
        pic_perms = perms.attach_files and perms.embed_links
        if pic_perms:
            await channel.set_permissions(user, attach_files=False, embed_links=False)
            await ctx.approve(f"Revoked picture permissions from {user.mention} in {channel.mention}!")
        else:
            await channel.set_permissions(user, attach_files=True, embed_links=True)
            await ctx.approve(f"Granted picture permissions to {user.mention} in {channel.mention}!")

    # ---- Warn ----

    @group(name="warn", invoke_without_command=True, example="@x annoying")
    @has_permissions(moderate_members=True)
    @Mod.is_mod_configured()
    async def warn(self, ctx: Context, member: Member, *, reason: str = "No reason provided"):
        await TouchableMember().check(ctx, member)
        if await self.is_immune(ctx, member):
            return
        warn_count = await self.bot.pool.fetchval(
            "SELECT COUNT(*) FROM moderation WHERE guild_id = $1 AND user_id = $2 AND action = 'warn'",
            ctx.guild.id, member.id
        )
        action = await self.bot.pool.fetchrow(
            "SELECT action, threshold, duration FROM warn_actions WHERE guild_id = $1 AND threshold = $2",
            ctx.guild.id, warn_count + 1
        )
        await ModConfig.sendlogs(self.bot, "warn", ctx.author, member, reason)
        if action:
            dur = timedelta(seconds=action['duration']) if action['duration'] else None
            if action['action'] == 'timeout':
                await member.timeout(dur, reason="Warn threshold reached")
            elif action['action'] == 'ban':
                await member.ban(reason="Warn threshold reached", delete_message_days=0)
            elif action['action'] == 'softban':
                await member.ban(reason="Warn threshold reached", delete_message_days=7)
                await member.unban(reason="Softban complete")
            elif action['action'] == 'kick':
                await member.kick(reason="Warn threshold reached")
            # jail not implemented here
            await ModConfig.sendlogs(self.bot, action['action'], ctx.guild.me, member, "Warn threshold reached", dur)
            return await ctx.approve(f"Warned {member.mention} (Threshold reached: {action['action']})")
        await ctx.approve(f"Warned {member.mention}")

    @warn.group(name="action", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def warn_action(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @warn_action.command(name="add", example="ban --threshold 3")
    @has_permissions(manage_guild=True)
    async def warn_action_add(self, ctx: Context, action: str, *, flags: dict):
        # Since flag parsing is not fully implemented, we'll use a simple parser
        # For simplicity, we'll just accept threshold and duration in the string
        # Better to use a converter but we'll keep it simple.
        # We'll expect flags like "threshold=3 duration=1d"
        match = re.search(r'threshold=(\d+)', flags)
        threshold = int(match.group(1)) if match else 3
        dur_match = re.search(r'duration=([smhd]+)', flags)  # not used
        # For real use, we'd implement proper flag parsing, but for now:
        await ctx.warn("Flag parsing is simplified. Use `threshold=3 duration=1d` format.")
        # But we'll just store with default duration None
        valid = ["timeout", "jail", "ban", "softban", "kick"]
        if action not in valid:
            return await ctx.warn(f"Invalid action. Valid: {', '.join(valid)}")
        await self.bot.pool.execute(
            "INSERT INTO warn_actions (guild_id, threshold, action, duration) VALUES ($1, $2, $3, $4) ON CONFLICT (guild_id, threshold) DO UPDATE SET action = $3, duration = $4",
            ctx.guild.id, threshold, action, None
        )
        await ctx.approve(f"Set warn threshold {threshold} to {action}")

    @warn_action.command(name="remove", example="3")
    @has_permissions(manage_guild=True)
    async def warn_action_remove(self, ctx: Context, threshold: int):
        deleted = await self.bot.pool.execute("DELETE FROM warn_actions WHERE guild_id = $1 AND threshold = $2", ctx.guild.id, threshold)
        if deleted == "DELETE 0":
            return await ctx.warn(f"No action configured for threshold {threshold}")
        await ctx.approve(f"Removed action for threshold {threshold}")

    @warn_action.command(name="list")
    @has_permissions(manage_guild=True)
    async def warn_action_list(self, ctx: Context):
        actions = await self.bot.pool.fetch("SELECT threshold, action, duration FROM warn_actions WHERE guild_id = $1 ORDER BY threshold", ctx.guild.id)
        if not actions:
            return await ctx.warn("No warn actions configured")
        embed = Embed(title="Warn Actions")
        for a in actions:
            dur = timedelta(seconds=a['duration']) if a['duration'] else None
            embed.add_field(name=f"Threshold: {a['threshold']}", value=f"Action: {a['action']}\n" + (f"Duration: {humanize.naturaldelta(dur)}" if dur else ""), inline=True)
        await ctx.send(embed=embed)

    @warn.command(name="remove", aliases=["delete", "del"], example="1 Resolved")
    @has_permissions(moderate_members=True)
    async def warn_remove(self, ctx: Context, case_id: Range[int, 1, None], *, reason: str = "No reason provided"):
        warn = await self.bot.pool.fetchrow(
            "DELETE FROM moderation WHERE guild_id = $1 AND case_id = $2 AND action = 'warn' RETURNING user_id",
            ctx.guild.id, case_id
        )
        if not warn:
            return await ctx.warn(f"No warning found with case ID #{case_id}")
        try:
            user = await self.bot.fetch_user(warn['user_id'])
            user_text = f"{user.mention} (`{user.id}`)"
        except:
            user_text = f"`{warn['user_id']}`"
        await ModConfig.sendlogs(self.bot, "warn remove", ctx.author, user, f"#{case_id} removed. {reason}")
        await ctx.approve(f"Removed warning case #{case_id} from {user_text}")

    # ---- Chunkban ----

    @group(name="chunkban", aliases=["cb"], example="10")
    @has_permissions(ban_members=True)
    async def chunkban(self, ctx: Context, amount: Annotated[int, Range[int, 2, 100]] = 10):
        # ratelimit check using SQLite (we'll use a simple settings table)
        if not ctx.guild.chunked:
            await ctx.guild.chunk(cache=True)

        members = sorted(ctx.guild.members, key=lambda m: (m.joined_at or ctx.guild.created_at), reverse=True)
        banned = members[:amount]

        if not banned:
            return await ctx.warn("No members found to ban!")

        await ctx.prompt(f"Are you sure you want to ban the newest {amount} members?")

        banned_count = 0
        async with ctx.typing():
            for member in banned:
                if member.bot:
                    continue
                try:
                    await member.ban(reason=f"{ctx.author} / Chunkban")
                    await asyncio.sleep(2)
                    banned_count += 1
                except HTTPException:
                    continue
        await ctx.approve(f"Banned {banned_count} out of {len(banned)} members.")

    @chunkban.command(name="avatars", aliases=["defaultavatars"])
    @has_permissions(ban_members=True)
    async def chunkban_avatars(self, ctx: Context):
        # ratelimit using SQLite
        if not ctx.guild.chunked:
            await ctx.guild.chunk(cache=True)

        members = [m for m in ctx.guild.members if m.default_avatar]
        if not members:
            return await ctx.warn("No members found with default avatars!")

        await ctx.prompt(f"Are you sure you want to ban members with default avatars?")
        async with ctx.typing():
            for member in members:
                try:
                    await member.ban(reason=f"{ctx.author} / Chunkban: Default Avatar")
                    await asyncio.sleep(2)
                except HTTPException:
                    continue
        await ctx.approve(f"Banned {plural(len(members), md='**')} members with default avatars.")

    # ---- Immune ----

    @group(name="immune", invoke_without_command=True)
    async def immune(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @immune.group(name="add", invoke_without_command=True)
    async def immune_add(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @immune_add.command(name="user", aliases=["member"])
    @has_permissions(manage_guild=True)
    async def immune_add_user(self, ctx: Context, member: Member):
        check = await self.bot.pool.fetchrow("SELECT * FROM immune WHERE guild_id = $1 AND entity_id = $2 AND type = 'user'", ctx.guild.id, member.id)
        if check:
            return await ctx.warn(f"**{member.name}** is **already** immune!")
        await self.bot.pool.execute("INSERT INTO immune (guild_id, entity_id, type) VALUES ($1, $2, 'user')", ctx.guild.id, member.id)
        await ctx.approve(f"Added **{member.name}** to immune list!")

    @immune_add.command(name="role")
    @has_permissions(manage_guild=True)
    async def immune_add_role(self, ctx: Context, role: Role):
        check = await self.bot.pool.fetchrow("SELECT * FROM immune WHERE guild_id = $1 AND role_id = $2 AND type = 'role'", ctx.guild.id, role.id)
        if check:
            return await ctx.warn(f"**{role.name}** is **already** immune!")
        await self.bot.pool.execute("INSERT INTO immune (guild_id, entity_id, role_id, type) VALUES ($1, $2, $2, 'role')", ctx.guild.id, role.id)
        await ctx.approve(f"Added {role.mention} to immune list!")

    @immune.group(name="remove", invoke_without_command=True)
    async def immune_remove(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @immune_remove.command(name="user", aliases=["member"])
    @has_permissions(manage_guild=True)
    async def immune_remove_user(self, ctx: Context, member: Member):
        check = await self.bot.pool.fetchrow("SELECT * FROM immune WHERE guild_id = $1 AND entity_id = $2 AND type = 'user'", ctx.guild.id, member.id)
        if not check:
            return await ctx.warn(f"**{member.name}** is **not** immune!")
        await self.bot.pool.execute("DELETE FROM immune WHERE guild_id = $1 AND entity_id = $2 AND type = 'user'", ctx.guild.id, member.id)
        await ctx.approve(f"Removed {member.mention} from immune list!")

    @immune_remove.command(name="role")
    @has_permissions(manage_guild=True)
    async def immune_remove_role(self, ctx: Context, role: Role):
        check = await self.bot.pool.fetchrow("SELECT * FROM immune WHERE guild_id = $1 AND role_id = $2 AND type = 'role'", ctx.guild.id, role.id)
        if not check:
            return await ctx.warn(f"**{role.name}** is **not** immune!")
        await self.bot.pool.execute("DELETE FROM immune WHERE guild_id = $1 AND role_id = $2 AND type = 'role'", ctx.guild.id, role.id)
        await ctx.approve(f"Removed {role.mention} from immune list!")

    @immune.command(name="list")
    @has_permissions(manage_guild=True)
    async def immune_list(self, ctx: Context):
        rows = await self.bot.pool.fetch("SELECT entity_id, role_id, type FROM immune WHERE guild_id = $1", ctx.guild.id)
        if not rows:
            return await ctx.warn("No immune users or roles found!")
        entries = []
        for row in rows:
            if row['type'] == 'user':
                entries.append(f"<@{row['entity_id']}>")
            elif row['type'] == 'role' and row['role_id']:
                entries.append(f"<@&{row['role_id']}>")
        if not entries:
            return await ctx.warn("No immune users or roles found!")
        pages = []
        for chunk in [entries[i:i+10] for i in range(0, len(entries), 10)]:
            embed = Embed(title="Immune Users and Roles", description="\n".join(chunk))
            pages.append(embed)
        paginator = Paginator(ctx, pages)
        await paginator.start()