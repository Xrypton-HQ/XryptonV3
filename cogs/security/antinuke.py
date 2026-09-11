from contextlib import suppress
from datetime import datetime, timedelta, timezone
from logging import getLogger
from time import time
from typing import Annotated, List, Optional, cast
from xxhash import xxh32_hexdigest

import discord
from discord import (
    AuditLogEntry,
    Embed,
    Guild,
    HTTPException,
    Member,
    Object,
    User,
    Message
)
from discord.ext.commands import Cog, FlagConverter, Range, flag, group, hybrid_command, hybrid_group
from discord.utils import utcnow
from humanize import naturaldelta
import json

# Bot imports
from base.Xrypton import Bot
from base.context import Context
from base.config import EMOJIS, CLIENT
from base.managers.paginator import Paginator
from base.managers.EmbedBuilder import EmbedScript

log = getLogger("xrypton/nuke")  # keep name for consistency

# ===== Local implementations of converters/helpers =====

class Duration:
    """Parse duration strings like 1d, 2h, 30m."""
    async def convert(self, ctx: Context, argument: str) -> timedelta:
        if not argument:
            raise ValueError("Missing duration")
        import re
        match = re.match(r'(\d+)([smhd])', argument.lower())
        if not match:
            raise ValueError("Invalid duration format. Use e.g., 1d, 2h, 30m, 60s")
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
            raise ValueError("Invalid duration unit")
        if seconds <= 0:
            raise ValueError("Duration must be positive")
        if seconds > 12 * 3600:  # max 12 hours
            raise ValueError("Duration cannot exceed 12 hours")
        return timedelta(seconds=seconds)

class Status:
    """Convert on/off, true/false, 1/0 to bool."""
    async def convert(self, ctx: Context, argument: str) -> bool:
        lowered = argument.lower()
        if lowered in ('on', 'true', 'yes', '1', 'enable', 'enabled'):
            return True
        if lowered in ('off', 'false', 'no', '0', 'disable', 'disabled'):
            return False
        raise ValueError(f"Invalid status: {argument}")

# ===== Flag converter =====
class Flags(FlagConverter):
    threshold: Range[int, 1, 12] = flag(
        default=3,
        aliases=["limit"],
        description="The threshold for the module to trigger.",
    )
    duration: timedelta = flag(
        aliases=["time", "per"],
        converter=Duration(),
        default=timedelta(minutes=60),
        description="The duration before the threshold resets.",
    )
    punishment: str = flag(
        default="ban",
        description="The punishment to apply (ban/kick/strip)"
    )

# ===== Module settings (Pydantic-like) =====
class Module:
    def __init__(self, threshold: int, duration: int, punishment: str):
        self.threshold = threshold
        self.duration = duration
        self.punishment = punishment

    def to_dict(self):
        return {"threshold": self.threshold, "duration": self.duration, "punishment": self.punishment}

    @classmethod
    def from_dict(cls, data):
        if not data:
            return None
        return cls(data["threshold"], data["duration"], data["punishment"])

# ===== Settings class =====
class Settings:
    def __init__(self, guild: Guild, whitelist: List[int], trusted_admins: List[int],
                 bot: bool, ban: Optional[Module], kick: Optional[Module],
                 role: Optional[Module], channel: Optional[Module],
                 webhook: Optional[Module], emoji: Optional[Module]):
        self.guild = guild
        self.whitelist = whitelist or []
        self.trusted_admins = trusted_admins or []
        self.bot = bot
        self.ban = ban
        self.kick = kick
        self.role = role
        self.channel = channel
        self.webhook = webhook
        self.emoji = emoji

    def __bool__(self) -> bool:
        return any(getattr(self, module) for module in ("ban", "kick", "role", "channel", "webhook", "emoji"))

    def is_trusted(self, member: Member) -> bool:
        return member.id in {self.guild.owner_id, *self.trusted_admins, *CLIENT.OWNER}

    def is_whitelisted(self, member: Member) -> bool:
        return member.id in {self.guild.owner_id, self.guild.me.id, *self.whitelist, *self.trusted_admins, *CLIENT.OWNER}

    @classmethod
    async def fetch(cls, bot: Bot, guild: Guild) -> "Settings":
        """Fetch settings from DB, insert default if missing."""
        record = await bot.pool.fetchrow(
            "SELECT * FROM antinuke WHERE guild_id = $1", guild.id
        )
        if not record:
            # Insert default
            await bot.pool.execute(
                "INSERT INTO antinuke (guild_id) VALUES ($1)", guild.id
            )
            record = await bot.pool.fetchrow(
                "SELECT * FROM antinuke WHERE guild_id = $1", guild.id
            )

        # Parse JSON fields
        def parse_module(col):
            return Module.from_dict(json.loads(col)) if col else None

        return cls(
            guild=guild,
            whitelist=json.loads(record["whitelist"]) if record.get("whitelist") else [],
            trusted_admins=json.loads(record["trusted_admins"]) if record.get("trusted_admins") else [],
            bot=record.get("bot", False),
            ban=parse_module(record.get("ban")),
            kick=parse_module(record.get("kick")),
            role=parse_module(record.get("role")),
            channel=parse_module(record.get("channel")),
            webhook=parse_module(record.get("webhook")),
            emoji=parse_module(record.get("emoji")),
        )

    async def check_threshold(self, bot: Bot, member: Member, module: str) -> bool:
        """Check if member exceeds threshold for module using SQLite."""
        if self.is_whitelisted(member):
            return False

        config: Optional[Module] = getattr(self, module)
        if not config:
            return False

        # Get current count for this (guild, user, module) within the duration window
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=config.duration)

        # Use a transaction to increment and check
        # We'll do: get current count, if exists and timestamp > cutoff, increment else reset.
        # Using SQLite with row-level locking via INSERT ... ON CONFLICT DO UPDATE.
        # We'll use a single query to increment and return count.
        # We'll use the existing row or insert new.
        # We'll do a SELECT first, then UPDATE or INSERT.
        # But to avoid race conditions, we can use a transaction.

        # For simplicity, we'll do a SELECT then UPDATE/INSERT inside a transaction.
        async with bot.pool.acquire() as conn:  # using the connection context
            # Check existing
            row = await conn.fetchrow(
                "SELECT count, timestamp FROM antinuke_counts WHERE guild_id = $1 AND user_id = $2 AND module = $3",
                self.guild.id, member.id, module
            )
            if row:
                # If timestamp is older than duration, reset to 1
                if (now - row["timestamp"]).total_seconds() >= config.duration:
                    await conn.execute(
                        "UPDATE antinuke_counts SET count = 1, timestamp = $4 WHERE guild_id = $1 AND user_id = $2 AND module = $3",
                        self.guild.id, member.id, module, now
                    )
                    return False  # reset, not exceeding threshold
                else:
                    # Increment
                    new_count = row["count"] + 1
                    await conn.execute(
                        "UPDATE antinuke_counts SET count = $4 WHERE guild_id = $1 AND user_id = $2 AND module = $3",
                        self.guild.id, member.id, module, new_count
                    )
                    if new_count >= config.threshold:
                        return True
                    return False
            else:
                # Insert new
                await conn.execute(
                    "INSERT INTO antinuke_counts (guild_id, user_id, module, count, timestamp) VALUES ($1, $2, $3, 1, $4)",
                    self.guild.id, member.id, module, now
                )
                return False

    async def dispatch_log(self, bot: Bot, perpetrator: Member, *, module: str,
                           elapsed: float, failure: bool = False, details: Optional[str] = None,
                           description: Optional[str] = None, **kwargs):
        """Log antinuke event."""
        # Send log using the same logic as ModConfig.sendlogs
        await send_log(bot, self.guild, perpetrator, module, elapsed, failure, details, description, **kwargs)

# ===== Logging function (replaces ModConfig.sendlogs) =====
async def send_log(bot: Bot, guild: Guild, perpetrator: Member, module: str,
                   elapsed: float, failure: bool, details: Optional[str], description: Optional[str],
                   **kwargs):
    """Send log embed to mod channel."""
    # Fetch mod settings
    mod = await bot.pool.fetchrow("SELECT * FROM mod WHERE guild_id = $1", guild.id)
    if not mod or not mod.get("channel_id"):
        return

    channel = guild.get_channel(mod["channel_id"])
    if not channel:
        return

    config = await Settings.fetch(bot, guild)
    module_config = getattr(config, module)
    if not module_config:
        return

    action = f"antinuke {module_config.punishment}"
    reason = description or f"Mass {module} attempt detected"
    if details:
        reason += f" | {details}"

    # Get case number
    res = await bot.pool.fetchrow("SELECT count FROM cases WHERE guild_id = $1", guild.id)
    if not res:
        await bot.pool.execute("INSERT INTO cases (guild_id, count) VALUES ($1, $2)", guild.id, 0)
        case = 1
    else:
        case = int(res["count"]) + 1
    await bot.pool.execute("UPDATE cases SET count = $1 WHERE guild_id = $2", case, guild.id)

    # Insert into history.moderation
    await bot.pool.execute(
        """
        INSERT INTO history.moderation 
        (guild_id, case_id, user_id, moderator_id, action, reason)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        guild.id, case, perpetrator.id, guild.me.id, action, reason
    )

    # Send embed
    embed = Embed(
        timestamp=datetime.utcnow(),
        color=discord.Color.red() if not failure else discord.Color.orange(),
    )
    embed.set_author(name="Antinuke Protection", icon_url=guild.me.display_avatar.url)
    embed.add_field(
        name="Action",
        value=f"{action}\n**Module**: {module}\n**Failure**: {failure}",
        inline=False
    )
    embed.add_field(
        name="Perpetrator",
        value=f"{perpetrator.mention} (`{perpetrator.id}`)",
        inline=True
    )
    embed.add_field(
        name="Elapsed",
        value=f"{elapsed:.2f}s",
        inline=True
    )
    if details:
        embed.add_field(name="Details", value=details, inline=False)

    with suppress(HTTPException):
        await channel.send(embed=embed)

class AntiNuke(Cog):
    """Protect your server from malicious administrators."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS antinuke (
                guild_id BIGINT PRIMARY KEY,
                whitelist TEXT DEFAULT '[]',
                trusted_admins TEXT DEFAULT '[]',
                bot BOOL DEFAULT FALSE,
                ban TEXT,
                kick TEXT,
                role TEXT,
                channel TEXT,
                webhook TEXT,
                emoji TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS antinuke_counts (
                guild_id BIGINT,
                user_id BIGINT,
                module TEXT,
                count INT DEFAULT 1,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id, module)
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    async def is_trusted_admin(self, ctx: Context) -> bool:
        """Check if author is trusted to manage antinuke."""
        if ctx.author.id in (ctx.guild.owner_id, *self.bot.owner_ids):
            return True
        config = await Settings.fetch(self.bot, ctx.guild)
        if config.is_trusted(ctx.author):
            return True
        await ctx.warn("You must be a **trusted administrator** to use this command!")
        return False

    # ===== Commands =====

    @hybrid_group(
        aliases=["antiwizz", "an", "aw"],
        invoke_without_command=True,
        with_app_command=False,
        brief="antinuke admin or guild owner",
    )
    async def antinuke(self, ctx: Context) -> Message:
        """Protect your server from malicious administrators."""
        return await ctx.send_help(ctx.command)

    @antinuke.command(name="settings", aliases=["config"], brief="antinuke admin or guild owner")
    async def antinuke_settings(self, ctx: Context) -> Message:
        """View the antinuke settings."""
        config = await Settings.fetch(self.bot, ctx.guild)
        if not config:
            return await ctx.warn("The **antinuke system** hasn't been configured yet!")

        embed = Embed(title="Anti-Nuke Settings")
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        def format_module(module: Optional[Module]) -> str:
            if not module:
                return "Disabled"
            threshold = module.threshold
            duration = naturaldelta(timedelta(seconds=module.duration))
            return f"> Threshold: {threshold}\n> Duration: {duration}"

        protection_modules = [
            ("Bot", config.bot),
            ("Ban", config.ban),
            ("Kick", config.kick),
            ("Role", config.role),
            ("Channel", config.channel),
            ("Webhook", config.webhook),
            ("Emoji", config.emoji),
        ]

        for name, module in protection_modules:
            emoji = EMOJIS.APPROVE if module else EMOJIS.DENY
            embed.add_field(
                name=f"{emoji} {name}",
                value=format_module(module) if not isinstance(module, bool) else ("Enabled" if module else "Disabled"),
                inline=True
            )

        # Whitelist
        whitelist_str = "None"
        if config.whitelist:
            members = [ctx.guild.get_member(mid) for mid in config.whitelist]
            valid = [f"{m.mention} (`{m.id}`)" for m in members if m]
            if valid:
                whitelist_str = "\n".join(valid[:3])
                if len(valid) > 3:
                    whitelist_str += f"\n... and {len(valid)-3} more"
        embed.add_field(name="Whitelisted Members", value=whitelist_str, inline=True)

        # Trusted Admins
        trusted_str = "None"
        if config.trusted_admins:
            admins = [ctx.guild.get_member(mid) for mid in config.trusted_admins]
            valid = [f"{a.mention} (`{a.id}`)" for a in admins if a]
            if valid:
                trusted_str = "\n".join(valid[:3])
                if len(valid) > 3:
                    trusted_str += f"\n... and {len(valid)-3} more"
        embed.add_field(name="Trusted Admins", value=trusted_str, inline=True)

        return await ctx.send(embed=embed)

    @antinuke.group(name="whitelist", aliases=["wl"], invoke_without_command=True,
                    brief="antinuke admin or guild owner", example="@x")
    async def antinuke_whitelist(self, ctx: Context, *, member: User) -> Message:
        """Whitelist a member from the antinuke system."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if member.id in config.whitelist:
            await ctx.prompt(
                f"**{member}** is already whitelisted from the **antinuke system**!",
                "Would you like to remove them from the whitelist?",
            )
            config.whitelist.remove(member.id)
        else:
            config.whitelist.append(member.id)

        await self.bot.pool.execute(
            "UPDATE antinuke SET whitelist = $1 WHERE guild_id = $2",
            json.dumps(config.whitelist), ctx.guild.id
        )
        return await ctx.approve(
            f"Now exempting **{member}** from being punished"
            if member.id in config.whitelist
            else f"Now punishing **{member}** for malicious actions"
        )

    @antinuke_whitelist.command(name="list", aliases=["ls"], brief="antinuke admin or guild owner")
    async def antinuke_whitelist_list(self, ctx: Context) -> Message:
        """View all whitelisted members."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        members = []
        for mid in config.whitelist:
            m = ctx.guild.get_member(mid)
            if m:
                members.append(f"**{m}** (`{m.id}`)")
        if not members:
            return await ctx.warn("No members are exempt from the **antinuke system**!")
        paginator = Paginator(ctx, [Embed(title="Whitelisted Members", description="\n".join(members))])
        return await paginator.start()

    @antinuke.group(name="trust", aliases=["admin"], invoke_without_command=True,
                    brief="antinuke admin or guild owner", example="@x")
    async def antinuke_trust(self, ctx: Context, *, member: User) -> Message:
        """Allow a member to manage the antinuke system."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if member.id in config.trusted_admins:
            await ctx.prompt(
                f"**{member}** is already trusted with the **antinuke system**!",
                "Would you like to remove them from the trusted list?",
            )
            config.trusted_admins.remove(member.id)
        else:
            config.trusted_admins.append(member.id)

        await self.bot.pool.execute(
            "UPDATE antinuke SET trusted_admins = $1 WHERE guild_id = $2",
            json.dumps(config.trusted_admins), ctx.guild.id
        )
        return await ctx.approve(
            f"{'Now' if member.id in config.trusted_admins else 'No longer'} allowing **{member}** to manage the **antinuke system**"
        )

    @antinuke_trust.command(name="list", aliases=["ls"], brief="antinuke admin or guild owner")
    async def antinuke_trust_list(self, ctx: Context) -> Message:
        """View all trusted admins."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        admins = []
        for mid in config.trusted_admins:
            m = ctx.guild.get_member(mid)
            if m:
                admins.append(f"**{m}** (`{m.id}`)")
        if not admins:
            return await ctx.warn("No members are allowed to manage the **antinuke system**!")
        paginator = Paginator(ctx, [Embed(title="Trusted Admins", description="\n".join(admins))])
        return await paginator.start()

    @antinuke.command(name="bot", aliases=["bots", "botadd"], brief="antinuke admin or guild owner", example="on")
    async def antinuke_bot(self, ctx: Context, status: Annotated[bool, Status]) -> Message:
        """Prevent bots from being added to the server."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if config.bot == status:
            return await ctx.warn(f"Protection against bots is already **{'enabled' if status else 'disabled'}**!")
        await self.bot.pool.execute(
            "UPDATE antinuke SET bot = $1 WHERE guild_id = $2",
            status, ctx.guild.id
        )
        return await ctx.approve(f"{'Now' if status else 'No longer'} preventing bots from being added to the server")

    @antinuke.command(name="ban", aliases=["bans"], brief="antinuke admin or guild owner",
                      example="(on/off) (--threshold 5) (--time 1h) (--punishment ban)")
    async def antinuke_ban(self, ctx: Context, status: Annotated[bool, Status], *, flags: Flags) -> Message:
        """Prevent members from being banned."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if not status and not config.ban:
            return await ctx.warn("The **antinuke system** isn't preventing members from being banned!")

        punishment = flags.punishment.lower()
        if punishment not in ["ban", "kick", "strip"]:
            return await ctx.warn("Punishment must be `ban`, `kick`, or `strip`")

        if status:
            module = Module(flags.threshold, int(flags.duration.total_seconds()), punishment)
            await self.bot.pool.execute(
                "UPDATE antinuke SET ban = $1 WHERE guild_id = $2",
                json.dumps(module.to_dict()), ctx.guild.id
            )
            return await ctx.approve(
                f"Now applying **{punishment}** punishment if {plural(flags.threshold, md='`')} members are banned within **{naturaldelta(flags.duration)}**"
            )
        else:
            await self.bot.pool.execute("UPDATE antinuke SET ban = NULL WHERE guild_id = $1", ctx.guild.id)
            return await ctx.approve("No longer preventing members from being banned")

    @antinuke.command(name="kick", aliases=["kicks"], brief="antinuke admin or guild owner",
                      example="(on/off) (--threshold 5) (--time 1h) (--punishment ban)")
    async def antinuke_kick(self, ctx: Context, status: Annotated[bool, Status], *, flags: Flags) -> Message:
        """Prevent members from being kicked."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if not status and not config.kick:
            return await ctx.warn("The **antinuke system** isn't preventing members from being kicked!")

        punishment = flags.punishment.lower()
        if punishment not in ["ban", "kick", "strip"]:
            return await ctx.warn("Punishment must be `ban`, `kick`, or `strip`")

        if status:
            module = Module(flags.threshold, int(flags.duration.total_seconds()), punishment)
            await self.bot.pool.execute(
                "UPDATE antinuke SET kick = $1 WHERE guild_id = $2",
                json.dumps(module.to_dict()), ctx.guild.id
            )
            return await ctx.approve(
                f"Now punishing the perpetrator if {plural(flags.threshold, md='`')} members are kicked within **{naturaldelta(flags.duration)}**"
            )
        else:
            await self.bot.pool.execute("UPDATE antinuke SET kick = NULL WHERE guild_id = $1", ctx.guild.id)
            return await ctx.approve("No longer monitoring members from being kicked")

    @antinuke.command(name="role", aliases=["roles"], brief="antinuke admin or guild owner",
                      example="(on/off) (--threshold 5) (--time 1h) (--punishment ban)")
    async def antinuke_role(self, ctx: Context, status: Annotated[bool, Status], *, flags: Flags) -> Message:
        """Prevent roles from being created, deleted, or modified."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if not status and not config.role:
            return await ctx.warn("The **antinuke system** isn't preventing roles from being modified!")

        punishment = flags.punishment.lower()
        if punishment not in ["ban", "kick", "strip"]:
            return await ctx.warn("Punishment must be `ban`, `kick`, or `strip`")

        if status:
            module = Module(flags.threshold, int(flags.duration.total_seconds()), punishment)
            await self.bot.pool.execute(
                "UPDATE antinuke SET role = $1 WHERE guild_id = $2",
                json.dumps(module.to_dict()), ctx.guild.id
            )
            return await ctx.approve(
                f"Now punishing the perpetrator if {plural(flags.threshold, md='`')} roles are modified within **{naturaldelta(flags.duration)}**"
            )
        else:
            await self.bot.pool.execute("UPDATE antinuke SET role = NULL WHERE guild_id = $1", ctx.guild.id)
            return await ctx.approve("No longer monitoring role modifications")

    @antinuke.command(name="channel", aliases=["channels"], brief="antinuke admin or guild owner",
                      example="(on/off) (--threshold 5) (--time 1h) (--punishment ban)")
    async def antinuke_channel(self, ctx: Context, status: Annotated[bool, Status], *, flags: Flags) -> Message:
        """Prevent channels from being created, deleted, or modified."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if not status and not config.channel:
            return await ctx.warn("The **antinuke system** isn't preventing channels from being modified!")

        punishment = flags.punishment.lower()
        if punishment not in ["ban", "kick", "strip"]:
            return await ctx.warn("Punishment must be `ban`, `kick`, or `strip`")

        if status:
            module = Module(flags.threshold, int(flags.duration.total_seconds()), punishment)
            await self.bot.pool.execute(
                "UPDATE antinuke SET channel = $1 WHERE guild_id = $2",
                json.dumps(module.to_dict()), ctx.guild.id
            )
            return await ctx.approve(
                f"Now punishing the perpetrator if {plural(flags.threshold, md='`')} channels are modified within **{naturaldelta(flags.duration)}**"
            )
        else:
            await self.bot.pool.execute("UPDATE antinuke SET channel = NULL WHERE guild_id = $1", ctx.guild.id)
            return await ctx.approve("No longer monitoring channel modifications")

    @antinuke.command(name="webhook", aliases=["webhooks", "hook", "hooks", "wh"],
                      brief="antinuke admin or guild owner",
                      example="(on/off) (--threshold 5) (--time 1h) (--punishment ban)")
    async def antinuke_webhook(self, ctx: Context, status: Annotated[bool, Status], *, flags: Flags) -> Message:
        """Prevent webhooks from being created, deleted, or modified."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if not status and not config.webhook:
            return await ctx.warn("The **antinuke system** isn't preventing webhooks from being modified!")

        punishment = flags.punishment.lower()
        if punishment not in ["ban", "kick", "strip"]:
            return await ctx.warn("Punishment must be `ban`, `kick`, or `strip`")

        if status:
            module = Module(flags.threshold, int(flags.duration.total_seconds()), punishment)
            await self.bot.pool.execute(
                "UPDATE antinuke SET webhook = $1 WHERE guild_id = $2",
                json.dumps(module.to_dict()), ctx.guild.id
            )
            return await ctx.approve(
                f"Now punishing the perpetrator if {plural(flags.threshold, md='`')} webhooks are modified within **{naturaldelta(flags.duration)}**"
            )
        else:
            await self.bot.pool.execute("UPDATE antinuke SET webhook = NULL WHERE guild_id = $1", ctx.guild.id)
            return await ctx.approve("No longer monitoring webhook modifications")

    @antinuke.command(name="emoji", aliases=["emojis", "emote", "emotes", "em"],
                      brief="antinuke admin or guild owner",
                      example="(on/off) (--threshold 5) (--time 1h) (--punishment ban)")
    async def antinuke_emoji(self, ctx: Context, status: Annotated[bool, Status], *, flags: Flags) -> Message:
        """Prevent emojis from being created, deleted, or modified."""
        if not await self.is_trusted_admin(ctx):
            return
        config = await Settings.fetch(self.bot, ctx.guild)
        if not status and not config.emoji:
            return await ctx.warn("The **antinuke system** isn't preventing emojis from being modified!")

        punishment = flags.punishment.lower()
        if punishment not in ["ban", "kick", "strip"]:
            return await ctx.warn("Punishment must be `ban`, `kick`, or `strip`")

        if status:
            module = Module(flags.threshold, int(flags.duration.total_seconds()), punishment)
            await self.bot.pool.execute(
                "UPDATE antinuke SET emoji = $1 WHERE guild_id = $2",
                json.dumps(module.to_dict()), ctx.guild.id
            )
            return await ctx.approve(
                f"Now punishing the perpetrator if {plural(flags.threshold, md='`')} emojis are modified within **{naturaldelta(flags.duration)}**"
            )
        else:
            await self.bot.pool.execute("UPDATE antinuke SET emoji = NULL WHERE guild_id = $1", ctx.guild.id)
            return await ctx.approve("No longer monitoring emoji modifications")

    @antinuke.command(name="reset", brief="antinuke admin or guild owner")
    async def antinuke_reset(self, ctx: Context) -> Message:
        """Reset the antinuke settings."""
        if not await self.is_trusted_admin(ctx):
            return
        await ctx.prompt(
            "Are you sure you want to reset the **antinuke settings**?",
            "This will remove all whitelisted members and trusted admins!",
        )
        await self.bot.pool.execute("DELETE FROM antinuke WHERE guild_id = $1", ctx.guild.id)
        await self.bot.pool.execute("DELETE FROM antinuke_counts WHERE guild_id = $1", ctx.guild.id)
        return await ctx.approve("Successfully reset the **antinuke settings**")

    # ===== Listeners =====

    @Cog.listener("on_audit_log_entry_bot_add")
    async def antinuke_monitor_bot(self, entry: AuditLogEntry):
        start = time()
        guild = entry.guild
        perpetrator = entry.user
        application = cast(Member | User | Object, entry.target)
        if not isinstance(perpetrator, Member):
            return

        config = await Settings.fetch(self.bot, guild)
        if not config or not config.bot or config.is_whitelisted(perpetrator):
            return

        elapsed = time() - start
        try:
            await guild.ban(application, reason="Bot addition attempt detected!")
            await guild.ban(perpetrator, reason="Bot addition attempt detected!")
        except HTTPException as exc:
            await config.dispatch_log(
                self.bot, perpetrator, module="bot", elapsed=elapsed,
                failure=True, details=exc.text, application=application
            )
            return

        await config.dispatch_log(
            self.bot, perpetrator, module="bot", elapsed=elapsed, application=application
        )

    @Cog.listener("on_audit_log_entry_ban")
    async def antinuke_monitor_ban(self, entry: AuditLogEntry):
        start = time()
        guild = entry.guild
        perpetrator = entry.user
        if not isinstance(perpetrator, Member):
            return

        config = await Settings.fetch(self.bot, guild)
        if not config or not config.ban or config.is_whitelisted(perpetrator):
            return

        if not await config.check_threshold(self.bot, perpetrator, "ban"):
            return

        elapsed = time() - start
        try:
            if config.ban.punishment == "ban":
                await guild.ban(perpetrator, reason="Mass ban attempt detected")
            elif config.ban.punishment == "kick":
                await guild.kick(perpetrator, reason="Mass ban attempt detected")
            elif config.ban.punishment == "strip":
                await perpetrator.edit(roles=[], reason="Mass ban attempt detected")
        except HTTPException as exc:
            await config.dispatch_log(
                self.bot, perpetrator, module="ban", elapsed=elapsed,
                failure=True, details=exc.text
            )
            return

        await config.dispatch_log(self.bot, perpetrator, module="ban", elapsed=elapsed)

    @Cog.listener("on_audit_log_entry_kick")
    async def antinuke_monitor_kick(self, entry: AuditLogEntry):
        start = time()
        guild = entry.guild
        perpetrator = entry.user
        if not isinstance(perpetrator, Member):
            return

        config = await Settings.fetch(self.bot, guild)
        if not config or not config.kick or config.is_whitelisted(perpetrator):
            return

        if not await config.check_threshold(self.bot, perpetrator, "kick"):
            return

        elapsed = time() - start
        try:
            if config.kick.punishment == "ban":
                await guild.ban(perpetrator, reason="Mass kick attempt detected")
            elif config.kick.punishment == "kick":
                await guild.kick(perpetrator, reason="Mass kick attempt detected")
            elif config.kick.punishment == "strip":
                await perpetrator.edit(roles=[], reason="Mass kick attempt detected")
        except HTTPException as exc:
            await config.dispatch_log(
                self.bot, perpetrator, module="kick", elapsed=elapsed,
                failure=True, details=exc.text
            )
            return

        await config.dispatch_log(self.bot, perpetrator, module="kick", elapsed=elapsed)

    @Cog.listener("on_audit_log_entry_role_create")
    @Cog.listener("on_audit_log_entry_role_delete")
    @Cog.listener("on_audit_log_entry_role_update")
    async def antinuke_monitor_role(self, entry: AuditLogEntry):
        start = time()
        guild = entry.guild
        perpetrator = entry.user
        if not isinstance(perpetrator, Member):
            return

        config = await Settings.fetch(self.bot, guild)
        if not config or not config.role or config.is_whitelisted(perpetrator):
            return

        if not await config.check_threshold(self.bot, perpetrator, "role"):
            return

        elapsed = time() - start
        verb = "created" if entry.action == "role_create" else "deleted" if entry.action == "role_delete" else "updated"
        try:
            if config.role.punishment == "ban":
                await guild.ban(perpetrator, reason=f"Mass role {verb} attempt detected")
            elif config.role.punishment == "kick":
                await guild.kick(perpetrator, reason=f"Mass role {verb} attempt detected")
            elif config.role.punishment == "strip":
                await perpetrator.edit(roles=[], reason=f"Mass role {verb} attempt detected")
        except HTTPException as exc:
            await config.dispatch_log(
                self.bot, perpetrator, module="role", elapsed=elapsed,
                failure=True, details=exc.text,
                description=f"Role {verb} attempt has been detected!"
            )
            return

        await config.dispatch_log(
            self.bot, perpetrator, module="role", elapsed=elapsed,
            description=f"Role {verb} attempt has been detected!"
        )

    @Cog.listener("on_audit_log_entry_channel_create")
    @Cog.listener("on_audit_log_entry_channel_delete")
    @Cog.listener("on_audit_log_entry_channel_update")
    async def antinuke_monitor_channel(self, entry: AuditLogEntry):
        start = time()
        guild = entry.guild
        perpetrator = entry.user
        if not isinstance(perpetrator, Member):
            return

        config = await Settings.fetch(self.bot, guild)
        if not config or not config.channel or config.is_whitelisted(perpetrator):
            return

        if not await config.check_threshold(self.bot, perpetrator, "channel"):
            return

        elapsed = time() - start
        verb = "created" if entry.action == "channel_create" else "deleted" if entry.action == "channel_delete" else "updated"
        try:
            if config.channel.punishment == "ban":
                await guild.ban(perpetrator, reason=f"Mass channel {verb} attempt detected")
            elif config.channel.punishment == "kick":
                await guild.kick(perpetrator, reason=f"Mass channel {verb} attempt detected")
            elif config.channel.punishment == "strip":
                await perpetrator.edit(roles=[], reason=f"Mass channel {verb} attempt detected")
        except HTTPException as exc:
            await config.dispatch_log(
                self.bot, perpetrator, module="channel", elapsed=elapsed,
                failure=True, details=exc.text,
                description=f"Channel {verb} attempt has been detected!"
            )
            return

        await config.dispatch_log(
            self.bot, perpetrator, module="channel", elapsed=elapsed,
            description=f"Channel {verb} attempt has been detected!"
        )

    @Cog.listener("on_audit_log_entry_webhook_create")
    @Cog.listener("on_audit_log_entry_webhook_delete")
    @Cog.listener("on_audit_log_entry_webhook_update")
    async def antinuke_monitor_webhook(self, entry: AuditLogEntry):
        start = time()
        guild = entry.guild
        perpetrator = entry.user
        if not isinstance(perpetrator, Member):
            return

        config = await Settings.fetch(self.bot, guild)
        if not config or not config.webhook or config.is_whitelisted(perpetrator):
            return

        if not await config.check_threshold(self.bot, perpetrator, "webhook"):
            return

        elapsed = time() - start
        verb = "created" if entry.action == "webhook_create" else "deleted" if entry.action == "webhook_delete" else "updated"
        try:
            if config.webhook.punishment == "ban":
                await guild.ban(perpetrator, reason=f"Mass webhook {verb} attempt detected")
            elif config.webhook.punishment == "kick":
                await guild.kick(perpetrator, reason=f"Mass webhook {verb} attempt detected")
            elif config.webhook.punishment == "strip":
                await perpetrator.edit(roles=[], reason=f"Mass webhook {verb} attempt detected")
        except HTTPException as exc:
            await config.dispatch_log(
                self.bot, perpetrator, module="webhook", elapsed=elapsed,
                failure=True, details=exc.text,
                description=f"Webhook {verb} attempt has been detected!"
            )
            return

        await config.dispatch_log(
            self.bot, perpetrator, module="webhook", elapsed=elapsed,
            description=f"Webhook {verb} attempt has been detected!"
        )

    @Cog.listener("on_audit_log_entry_emoji_create")
    @Cog.listener("on_audit_log_entry_emoji_delete")
    @Cog.listener("on_audit_log_entry_emoji_update")
    async def antinuke_monitor_emoji(self, entry: AuditLogEntry):
        start = time()
        guild = entry.guild
        perpetrator = entry.user
        if not isinstance(perpetrator, Member):
            return

        config = await Settings.fetch(self.bot, guild)
        if not config or not config.emoji or config.is_whitelisted(perpetrator):
            return

        if not await config.check_threshold(self.bot, perpetrator, "emoji"):
            return

        elapsed = time() - start
        verb = "created" if entry.action == "emoji_create" else "deleted" if entry.action == "emoji_delete" else "updated"
        try:
            if config.emoji.punishment == "ban":
                await guild.ban(perpetrator, reason=f"Mass emoji {verb} attempt detected")
            elif config.emoji.punishment == "kick":
                await guild.kick(perpetrator, reason=f"Mass emoji {verb} attempt detected")
            elif config.emoji.punishment == "strip":
                await perpetrator.edit(roles=[], reason=f"Mass emoji {verb} attempt detected")
        except HTTPException as exc:
            await config.dispatch_log(
                self.bot, perpetrator, module="emoji", elapsed=elapsed,
                failure=True, details=exc.text,
                description=f"Emoji {verb} attempt has been detected!"
            )
            return

        await config.dispatch_log(
            self.bot, perpetrator, module="emoji", elapsed=elapsed,
            description=f"Emoji {verb} attempt has been detected!"
        )

# ===== Helper for plural (since we don't have tools.formatter) =====
def plural(num, md=''):
    return f"{md}{num}{md}" if md else str(num)