import asyncio
import json
from contextlib import suppress
from datetime import timedelta
from io import BytesIO
from logging import getLogger
from typing import Any, List, Optional, cast

import discord
from discord import (
    AuditLogEntry,
    Colour,
    DMChannel,
    Embed,
    Emoji,
    File,
    GroupChannel,
    Guild,
    HTTPException,
    Invite,
    Member,
    Message,
    Object,
    PartialMessageable,
    Permissions,
    Role,
    StageChannel,
    TextChannel,
    Thread,
    User,
    VoiceChannel,
    VoiceState,
)
from discord.abc import GuildChannel
from discord.ext.commands import Cog, Greedy, group, has_permissions, BadArgument
from discord.utils import format_dt, utcnow

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import EMOJIS, COLORS

log = getLogger("xrypton/logging")

# ===== LogType Enum =====
class LogType:
    """Bitflags for log event types."""
    MESSAGE = 1 << 0
    MEMBER = 1 << 1
    ROLE = 1 << 2
    CHANNEL = 1 << 3
    MODERATION = 1 << 4
    INVITE = 1 << 5
    VOICE = 1 << 6
    EMOJI = 1 << 7

    @classmethod
    def all(cls) -> List["LogType"]:
        return [cls.MESSAGE, cls.MEMBER, cls.ROLE, cls.CHANNEL,
                cls.MODERATION, cls.INVITE, cls.VOICE, cls.EMOJI]

    @classmethod
    def ALL(cls) -> int:
        return sum(cls.all())

    @classmethod
    def from_value(cls, value: int) -> List["LogType"]:
        return [log_type for log_type in cls.all() if log_type & value]

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> int:
        """Convert a string to a LogType value."""
        try:
            return getattr(cls, argument.upper())
        except AttributeError:
            raise BadArgument(f"Invalid log type: `{argument}`")

    def __str__(self):
        return self.name.lower()

# ===== Helper functions =====
def human_join(items, final='and'):
    items = list(items)
    if not items:
        return ''
    if len(items) == 1:
        return str(items[0])
    if len(items) == 2:
        return f"{items[0]} {final} {items[1]}"
    return f"{', '.join(items[:-1])}, {final} {items[-1]}"

def plural(num, md=''):
    return f"{md}{num}{md}" if md else str(num)

def shorten(text: str, length: int, suffix: str = "...") -> str:
    if not text:
        return ""
    if len(text) <= length:
        return text
    return text[:length - len(suffix)] + suffix

# ===== Main Cog =====
class Logging(Cog):
    """Server logging for various events."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS logging (
                guild_id BIGINT,
                channel_id BIGINT,
                events INTEGER,
                PRIMARY KEY (guild_id, channel_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS logging_history (
                guild_id BIGINT,
                channel_id BIGINT,
                event_type TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS logging_ignore (
                guild_id BIGINT,
                entity_id BIGINT,
                PRIMARY KEY (guild_id, entity_id)
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    def get_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    # ===== Logging Commands =====
    @group(aliases=["log"], invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def logging(self, ctx: Context) -> Message:
        """Base command for managing server logs."""
        return await ctx.send_help(ctx.command)

    @logging.command(name="enable", aliases=["create", "add"], example="#logs message")
    @has_permissions(manage_guild=True)
    async def logging_enable(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        events: Greedy[LogType],
    ) -> Message:
        """Set events to be logged in a channel."""
        if not events:
            events = LogType.all()

        value = 0
        for event in events:
            value |= event

        await self.bot.pool.execute(
            """
            INSERT INTO logging (guild_id, channel_id, events)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, channel_id)
            DO UPDATE SET events = EXCLUDED.events
            """,
            ctx.guild.id, channel.id, value
        )

        if value == LogType.ALL():
            return await ctx.approve(f"Now logging all events in {channel.mention}")
        human_events = human_join([f"`{event.name.lower()}`" for event in events], final="and")
        return await ctx.approve(f"Now logging {human_events} events in {channel.mention}")

    @logging.command(name="events")
    @has_permissions(manage_guild=True)
    async def logging_events(self, ctx: Context) -> Message:
        """View all available logging events."""
        entries = [f"`{event.name.lower()}`" for event in LogType.all()]
        embed = Embed(title="Logging Events", description="\n".join(entries))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    @logging.command(name="disable", aliases=["remove", "delete", "del", "rm"], example="#logs")
    @has_permissions(manage_guild=True)
    async def logging_disable(self, ctx: Context, *, channel: TextChannel | Thread) -> Message:
        """Remove an existing logging channel."""
        result = await self.bot.pool.execute(
            "DELETE FROM logging WHERE guild_id = $1 AND channel_id = $2",
            ctx.guild.id, channel.id
        )
        if result == "DELETE 0":
            return await ctx.warn(f"Logging in {channel.mention} doesn't exist!")
        return await ctx.approve(f"No longer logging in {channel.mention}")

    @logging.command(name="list", aliases=["ls"])
    @has_permissions(manage_guild=True)
    async def logging_list(self, ctx: Context) -> Message:
        """List all logging channels."""
        records = await self.bot.pool.fetch(
            "SELECT channel_id, events FROM logging WHERE guild_id = $1", ctx.guild.id
        )
        channels = []
        for record in records:
            channel = ctx.guild.get_channel_or_thread(record["channel_id"])
            if not channel:
                continue
            events = LogType.from_value(record["events"])
            if not events:
                continue
            event_names = [f"`{e.name.lower()}`" for e in events]
            channels.append(f"{channel.mention} (`{channel.id}`) - {human_join(event_names, final='and')}")
        if not channels:
            return await ctx.warn("No logging channels exist for this server!")
        embed = Embed(title="Logging Channels", description="\n".join(channels))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    @logging.group(name="ignore", aliases=["exempt"], invoke_without_command=True, example="(#channel or @role)")
    @has_permissions(manage_guild=True)
    async def logging_ignore(self, ctx: Context, *, target: GuildChannel | Member) -> Message:
        """Ignore a channel or user from being unintentionally logged."""
        # Check if already ignored
        existing = await self.bot.pool.fetchval(
            "SELECT 1 FROM logging_ignore WHERE guild_id = $1 AND entity_id = $2",
            ctx.guild.id, target.id
        )
        if existing:
            return await ctx.warn(f"{target.mention} is already ignored!")
        await self.bot.pool.execute(
            "INSERT INTO logging_ignore (guild_id, entity_id) VALUES ($1, $2)",
            ctx.guild.id, target.id
        )
        return await ctx.approve(f"Now ignoring {target.mention} from logging")

    @logging_ignore.command(name="remove", aliases=["delete", "del", "rm"], example="(#channel or @role)")
    @has_permissions(manage_guild=True)
    async def logging_ignore_remove(self, ctx: Context, *, target: GuildChannel | Member) -> Message:
        """Remove a channel or user from being ignored."""
        result = await self.bot.pool.execute(
            "DELETE FROM logging_ignore WHERE guild_id = $1 AND entity_id = $2",
            ctx.guild.id, target.id
        )
        if result == "DELETE 0":
            return await ctx.warn(f"{target.mention} isn't ignored!")
        return await ctx.approve(f"No longer ignoring {target.mention} from logging")

    @logging_ignore.command(name="list", aliases=["ls"])
    @has_permissions(manage_channels=True)
    async def logging_ignore_list(self, ctx: Context) -> Message:
        """View all ignored channels and users."""
        records = await self.bot.pool.fetch(
            "SELECT entity_id FROM logging_ignore WHERE guild_id = $1", ctx.guild.id
        )
        if not records:
            return await ctx.warn("No channels or users are being ignored!")
        entries = []
        for record in records:
            target = ctx.guild.get_channel(record["entity_id"]) or ctx.guild.get_member(record["entity_id"])
            if target:
                entries.append(f"{target.mention} (`{target.id}`)")
        if not entries:
            return await ctx.warn("No valid channels or users are being ignored!")
        embed = Embed(title="Logging Exemption", description="\n".join(entries))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    # ===== Core Logging Method =====
    async def log(
        self,
        guild: Guild,
        event: int,
        embed: Embed,
        *,
        from_channel: Optional[Object | Any] = None,
        files: List[File] | None = None,
        user: Optional[Member | User | Object] = None,
    ) -> Optional[Message]:
        """Send a log to the appropriate channel."""
        if files is None:
            files = []

        # Find the channel for this event
        channel_id = await self.bot.pool.fetchval(
            """
            SELECT channel_id
            FROM logging
            WHERE guild_id = $1 AND (events & $2) = $2
            """,
            guild.id, event
        )
        if not channel_id:
            return None

        channel = cast(Optional[TextChannel | Thread], guild.get_channel_or_thread(channel_id))
        if not channel:
            log.warning("Logging channel %s doesn't exist in %s (%s)", channel_id, guild, guild.id)
            return None

        # Check permissions
        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.embed_links):
            return None

        # Check if the target or from_channel is ignored
        targets: List[int] = []
        if user and hasattr(user, "id"):
            targets.append(user.id)
        if from_channel and hasattr(from_channel, "id"):
            targets.append(from_channel.id)
        if targets:
            # Check if any of the targets are ignored
            ignored = await self.bot.pool.fetchval(
                """
                SELECT 1 FROM logging_ignore
                WHERE guild_id = $1 AND entity_id = ANY($2::BIGINT[])
                """,
                guild.id, targets
            )
            if ignored:
                return None

        # Set author and footer
        if user:
            if not embed.author:
                embed.set_author(
                    name=str(user),
                    icon_url=user.display_avatar.url if hasattr(user, "display_avatar") else None,
                )
            if not embed.footer:
                embed.set_footer(text=f"{user.__class__.__name__} ID: {user.id}")

        if not embed.timestamp:
            embed.timestamp = utcnow()

        # Use a lock to prevent concurrent logging flooding
        lock = self.get_lock(guild.id)
        async with lock:
            with suppress(HTTPException):
                return await channel.send(embed=embed, files=files, silent=True)

    # ===== Listeners =====
    @Cog.listener("on_member_join")
    async def log_member_join(self, member: Member) -> None:
        embed = Embed(title="Member Joined")
        if member.created_at > utcnow() - timedelta(days=1):
            embed.description = "⚠ Account is less than 1 day old!"
        embed.add_field(
            name="Creation",
            value=f"{format_dt(member.created_at, 'F')} ({format_dt(member.created_at, 'R')})"
        )
        await self.log(member.guild, LogType.MEMBER, embed, user=member)

    @Cog.listener("on_member_remove")
    async def log_member_remove(self, member: Member) -> None:
        embed = Embed(title="Member Left")
        embed.add_field(
            name="Creation",
            value=f"{format_dt(member.created_at, 'F')} ({format_dt(member.created_at, 'R')})",
            inline=False
        )
        if member.joined_at:
            embed.add_field(
                name="Joined",
                value=f"{format_dt(member.joined_at, 'F')} ({format_dt(member.joined_at, 'R')})",
                inline=False
            )
        await self.log(member.guild, LogType.MEMBER, embed, user=member)

    @Cog.listener("on_member_update")
    async def log_member_update(self, before: Member, after: Member) -> None:
        if before.nick == after.nick:
            return
        embed = Embed(title="Member Updated")
        embed.add_field(
            name="Nickname",
            value=f"**{before.nick or before.name}** -> **{after.nick or after.name}**",
            inline=False
        )
        await self.log(after.guild, LogType.MEMBER, embed, user=after)

    @Cog.listener("on_voice_state_update")
    async def log_voice_state_update(self, member: Member, before: VoiceState, after: VoiceState) -> None:
        embed = Embed(title="Voice State Updated")
        action_type = None

        if before.channel == after.channel and after.channel:
            if before.self_mute != after.self_mute:
                embed.description = f"{member.mention} {'muted' if after.self_mute else 'unmuted'} themselves"
            elif before.self_deaf != after.self_deaf:
                embed.description = f"{member.mention} {'deafened' if after.self_deaf else 'undeafened'} themselves"
            elif before.self_stream != after.self_stream:
                embed.description = f"{member.mention} {'started' if after.self_stream else 'stopped'} streaming"
            elif before.self_video != after.self_video:
                embed.description = f"{member.mention} {'started' if after.self_video else 'stopped'} video"
            else:
                return
        elif not before.channel and after.channel:
            embed.description = f"{member.mention} joined **{after.channel}**"
        elif before.channel and not after.channel:
            embed.description = f"{member.mention} left **{before.channel}**"
        elif before.channel and after.channel:
            embed.description = f"{member.mention} moved from **{before.channel}** to **{after.channel}**"
        else:
            return

        await self.log(
            member.guild,
            LogType.VOICE,
            embed,
            user=member,
            from_channel=before.channel or after.channel,
        )

    @Cog.listener("on_bulk_message_delete")
    async def log_bulk_message_delete(self, messages: List[Message]) -> None:
        if not messages or not messages[0].guild:
            return
        guild = messages[0].guild
        channel = messages[0].channel
        if isinstance(channel, (GroupChannel, DMChannel, PartialMessageable)):
            return

        members = list({msg.author for msg in messages})
        embed = Embed(
            title="Messages Deleted",
            description=(
                f"**{len(messages)} messages** deleted in {channel.mention}\n"
                f"> They were sent between {format_dt(messages[0].created_at, 't')} and {format_dt(messages[-1].created_at, 't')}"
            )
        )
        embed.add_field(
            name=f"**{plural(members):member}**",
            value="\n".join(
                [f"> {m.mention} (`{m.id}`)" for m in members[:10]]
                + ([f"> ... and {len(members) - 10} more"] if len(members) > 10 else [])
            )
        )

        # Build a text file with message content
        content_lines = []
        for msg in messages:
            if msg.system_content:
                content_lines.append(
                    f"[{msg.created_at:%d/%m/%Y - %H:%M}] {msg.author} ({msg.author.id}): {msg.system_content}"
                )
        files = []
        if content_lines:
            files.append(
                File(
                    BytesIO("\n".join(content_lines).encode()),
                    filename=f"messages_{utcnow().timestamp()}.txt",
                )
            )

        await self.log(guild, LogType.MESSAGE, embed, from_channel=channel, files=files)

    @Cog.listener("on_message_delete")
    async def log_message_delete(self, message: Message) -> None:
        if not message.guild or message.author.bot or isinstance(message.channel, (GroupChannel, DMChannel, PartialMessageable)):
            return

        embed = Embed(
            title="Message Deleted",
            description=f"Message from {message.author.mention} deleted in {message.channel.mention}"
        )
        if message.system_content:
            embed.add_field(name="Message Content", value=shorten(message.system_content, 1024), inline=False)
        if message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join([f"[{a.filename}]({a.url})" for a in message.attachments]),
                inline=False
            )
        elif message.stickers:
            embed.set_image(url=message.stickers[0].url)
        for emb in message.embeds:
            if emb.image:
                embed.set_image(url=emb.image.url)
                break

        # Attempt to include attachments as files
        files = []
        for att in message.attachments[:5]:  # limit to 5
            try:
                files.append(await att.to_file())
            except:
                pass

        await self.log(message.guild, LogType.MESSAGE, embed, from_channel=message.channel, files=files, user=message.author)

    @Cog.listener("on_message_edit")
    async def log_message_edit(self, before: Message, after: Message) -> None:
        if not after.guild or after.author.bot or isinstance(after.channel, (GroupChannel, DMChannel, PartialMessageable)):
            return
        if after.embeds or after.content == before.content:
            return

        embed = Embed(
            title="Message Edited",
            description=f"Message from {after.author.mention} edited\n> [Jump to Message]({after.jump_url})"
        )
        embed.add_field(name="Before", value=shorten(before.system_content or "", 1024), inline=False)
        embed.add_field(name="After", value=shorten(after.system_content or "", 1024), inline=False)

        files = []
        if before.attachments and not after.attachments:
            embed.add_field(
                name="Attachments Removed",
                value="\n".join([f"[{a.filename}]({a.url})" for a in before.attachments]),
                inline=False
            )
            for att in before.attachments[:5]:
                try:
                    files.append(await att.to_file())
                except:
                    pass

        await self.log(after.guild, LogType.MESSAGE, embed, from_channel=after.channel, files=files, user=after.author)

    @Cog.listener("on_audit_log_entry_role_create")
    async def log_role_creation(self, entry: AuditLogEntry) -> None:
        role = cast(Role, entry.target)
        embed = Embed(
            title="Role Created",
            description=f"Role {role.mention} created by {entry.user.mention}" if entry.user else f"Role {role.mention} created",
            color=role.color if role.color != Colour.default() else Colour.dark_embed()
        )
        embed.set_thumbnail(url=role.display_icon)
        embed.add_field(name="Name", value=role.name)
        embed.add_field(name="Color", value=str(role.color))
        await self.log(entry.guild, LogType.ROLE, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_role_update")
    async def log_role_updated(self, entry: AuditLogEntry) -> None:
        role = cast(Role, entry.target)
        embed = Embed(
            title="Role Updated",
            description=f"Role {role.mention} updated by {entry.user.mention}" if entry.user else f"Role {role.mention} updated",
            color=role.color if role.color != Colour.default() else Colour.dark_embed()
        )
        embed.set_thumbnail(url=role.display_icon)

        changed = False
        if hasattr(entry.before, "name") and hasattr(entry.after, "name") and entry.before.name != entry.after.name:
            embed.add_field(name="Name", value=f"**{entry.before.name}** -> **{entry.after.name}**", inline=False)
            changed = True
        if hasattr(entry.before, "color") and hasattr(entry.after, "color") and entry.before.color != entry.after.color:
            embed.add_field(name="Color", value=f"`{entry.before.color}` -> `{entry.after.color}`", inline=False)
            changed = True
        if hasattr(entry.before, "permissions") and hasattr(entry.after, "permissions") and entry.before.permissions != entry.after.permissions:
            before_perms = cast(Permissions, entry.before.permissions)
            after_perms = cast(Permissions, entry.after.permissions)
            changes = []
            for perm, status in after_perms:
                if getattr(before_perms, perm) != status:
                    changes.append(f"`{'✅' if status else '❌'}` **{perm.replace('_', ' ').title()}**")
            if changes:
                embed.add_field(name="Permissions Modified", value="\n".join(changes), inline=False)
                changed = True
        if not changed:
            return
        await self.log(entry.guild, LogType.ROLE, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_role_delete")
    async def log_role_deletion(self, entry: AuditLogEntry) -> None:
        embed = Embed(
            title="Role Deleted",
            description=f"Role **{entry.target}** deleted by {entry.user.mention}" if entry.user else f"Role **{entry.target}** deleted"
        )
        await self.log(entry.guild, LogType.ROLE, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_member_role_update")
    async def log_member_role_update(self, entry: AuditLogEntry) -> None:
        if not isinstance(entry.target, Member):
            return
        roles_granted = [r for r in entry.after.roles if r not in entry.before.roles]
        roles_removed = [r for r in entry.before.roles if r not in entry.after.roles]
        if not roles_granted and not roles_removed:
            return
        embed = Embed(
            title="Member Roles Updated",
            description=f"{entry.target.mention} was "
                        f"{'granted' if roles_granted else 'removed from'} "
                        f"{human_join([r.mention for r in (roles_granted or roles_removed)], final='and')}"
        )
        if entry.user and entry.target != entry.user:
            embed.add_field(name="Moderator", value=f"{entry.user.mention} (`{entry.user.id}`)")
        await self.log(entry.guild, LogType.ROLE, embed, user=entry.target)

    @Cog.listener("on_audit_log_entry_channel_create")
    async def log_channel_creation(self, entry: AuditLogEntry) -> None:
        channel = cast(GuildChannel, entry.target)
        if isinstance(channel, (Object, VoiceChannel, GroupChannel, DMChannel, PartialMessageable)):
            return
        embed = Embed(
            title="Channel Created",
            description=f"{channel.type.name.replace('_', ' ').title()} channel {channel.mention} created by {entry.user.mention}" if entry.user else f"{channel.type.name.replace('_', ' ').title()} channel {channel.mention} created"
        )
        embed.add_field(name="ID", value=f"`{channel.id}`")
        await self.log(entry.guild, LogType.CHANNEL, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_channel_update")
    async def log_channel_updated(self, entry: AuditLogEntry) -> None:
        channel = cast(GuildChannel, entry.target)
        if isinstance(channel, (Object, GroupChannel, DMChannel, PartialMessageable)):
            return
        if not entry.user:
            return

        embed = Embed(
            title="Channel Updated",
            description=f"{channel.type.name.replace('_', ' ').title()} channel {channel.mention} updated by {entry.user.mention}"
        )
        changed = False

        if hasattr(entry.before, "name") and hasattr(entry.after, "name") and entry.before.name != entry.after.name:
            embed.add_field(name="Name", value=f"**{entry.before.name}** -> **{entry.after.name}**", inline=False)
            changed = True
        if hasattr(entry.before, "topic") and hasattr(entry.after, "topic") and entry.before.topic != entry.after.topic:
            embed.add_field(name="Topic", value=f"**{shorten(entry.before.topic or 'No topic')}** -> **{shorten(entry.after.topic or 'No topic')}**", inline=False)
            changed = True
        if hasattr(entry.before, "nsfw") and hasattr(entry.after, "nsfw") and entry.before.nsfw != entry.after.nsfw:
            embed.add_field(name="NSFW", value=f"`{entry.before.nsfw}` -> `{entry.after.nsfw}`", inline=False)
            changed = True
        if hasattr(entry.before, "bitrate") and hasattr(entry.after, "bitrate") and entry.before.bitrate != entry.after.bitrate:
            embed.add_field(name="Bitrate", value=f"`{entry.before.bitrate / 1000}kbps` -> `{entry.after.bitrate / 1000}kbps`", inline=False)
            changed = True
        if hasattr(entry.before, "user_limit") and hasattr(entry.after, "user_limit") and entry.before.user_limit != entry.after.user_limit:
            embed.add_field(name="User Limit", value=f"`{entry.before.user_limit}` -> `{entry.after.user_limit}`", inline=False)
            changed = True
        if hasattr(entry.before, "slowmode_delay") and hasattr(entry.after, "slowmode_delay") and entry.before.slowmode_delay != entry.after.slowmode_delay:
            embed.add_field(name="Slowmode Delay", value=f"`{entry.before.slowmode_delay}s` -> `{entry.after.slowmode_delay}s`", inline=False)
            changed = True

        if not changed:
            return

        await self.log(entry.guild, LogType.CHANNEL, embed, from_channel=channel, user=entry.user)

    @Cog.listener("on_audit_log_entry_channel_delete")
    async def log_channel_deletion(self, entry: AuditLogEntry) -> None:
        channel = cast(GuildChannel, entry.before)
        embed = Embed(
            title="Channel Deleted",
            description=f"{channel.type.name.replace('_', ' ').title()} channel **{channel.name}** deleted by {entry.user.mention}" if entry.user else f"{channel.type.name.replace('_', ' ').title()} channel **{channel.name}** deleted"
        )
        embed.add_field(name="Creation", value=f"{format_dt(channel.created_at, 'F')} ({format_dt(channel.created_at, 'R')})")
        await self.log(entry.guild, LogType.CHANNEL, embed, from_channel=channel, user=entry.user)

    @Cog.listener("on_audit_log_entry_invite_create")
    async def log_invite_creation(self, entry: AuditLogEntry) -> None:
        invite = cast(Invite, entry.target)
        embed = Embed(
            title="Invite Created",
            description=f"{'Temporary ' if invite.temporary else ''}Invite [`{invite.code}`]({invite.url}) created by {entry.user.mention}" if entry.user else f"{'Temporary ' if invite.temporary else ''}Invite [`{invite.code}`]({invite.url}) created"
        )
        if invite.max_uses:
            embed.add_field(name="Max Uses", value=f"`{invite.max_uses}`")
        await self.log(entry.guild, LogType.INVITE, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_invite_delete")
    async def log_invite_deletion(self, entry: AuditLogEntry) -> None:
        invite = cast(Invite, entry.target)
        embed = Embed(
            title="Invite Deleted",
            description=f"{'Temporary ' if invite.temporary else ''}Invite [`{invite.code}`]({invite.url}) deleted by {entry.user.mention}" if entry.user else f"{'Temporary ' if invite.temporary else ''}Invite [`{invite.code}`]({invite.url}) deleted"
        )
        if invite.uses:
            embed.add_field(name="Uses", value=f"`{invite.uses}`/`{invite.max_uses or '∞'}`")
        if invite.inviter and invite.inviter != entry.user:
            embed.add_field(name="Inviter", value=f"{invite.inviter} (`{invite.inviter.id}`)")
        await self.log(entry.guild, LogType.INVITE, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_emoji_create")
    async def log_emoji_creation(self, entry: AuditLogEntry) -> None:
        emoji = cast(Emoji, entry.target)
        embed = Embed(title="Emoji Created", description=f"Emoji created by {entry.user.mention}" if entry.user else "")
        embed.set_thumbnail(url=emoji.url)
        embed.add_field(name="Name", value=emoji.name)
        await self.log(entry.guild, LogType.EMOJI, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_emoji_update")
    async def log_emoji_updated(self, entry: AuditLogEntry) -> None:
        emoji = cast(Emoji, entry.target)
        if not hasattr(entry.before, "name") or not hasattr(entry.after, "name") or entry.before.name == entry.after.name:
            return
        embed = Embed(title="Emoji Updated", description=f"Emoji updated by {entry.user.mention}" if entry.user else "")
        embed.set_thumbnail(url=emoji.url)
        embed.add_field(name="Name", value=f"**{entry.before.name}** -> **{entry.after.name}**", inline=False)
        await self.log(entry.guild, LogType.EMOJI, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_emoji_delete")
    async def log_emoji_deletion(self, entry: AuditLogEntry) -> None:
        embed = Embed(
            title="Emoji Deleted",
            description=f"Emoji **{entry.before.name}** deleted by {entry.user.mention}" if entry.user else f"Emoji {entry.before.name} deleted"
        )
        await self.log(entry.guild, LogType.EMOJI, embed, user=entry.user)