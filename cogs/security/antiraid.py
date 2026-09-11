from asyncio import sleep
from contextlib import suppress
from datetime import timedelta, datetime, timezone, UTC
from logging import getLogger
from typing import Annotated, Dict, List, Literal, Optional, Tuple, TypedDict, cast, Union
import json
import discord
from discord import (
    Asset, Embed, Guild, HTTPException, Member, Message, Role, 
    AutoModTrigger, AutoModRuleTriggerType, AutoModRuleEventType, 
    AutoModRuleAction, AutoModRuleActionType,
    Status as DiscordStatus, User
)
from discord.ext.commands import (
    Cog, Range, UserInputError, flag, group, has_permissions, 
    hybrid_command, hybrid_group
)
from discord.http import Route
from discord.utils import utcnow
from xxhash import xxh32_hexdigest

# Bot imports
from base.Xrypton import Bot
from base.context import Context
from base.config import EMOJIS, CLIENT
from base.managers.EmbedBuilder import EmbedScript, EmbedBuilder

log = getLogger("xrypton/raid")  # keep same name

# ===== Local helpers =====

def plural(num, md=''):
    return f"{md}{num}{md}" if md else str(num)

class Status:
    """Convert on/off, true/false, 1/0 to bool."""
    async def convert(self, ctx: Context, argument: str) -> bool:
        lowered = argument.lower()
        if lowered in ('on', 'true', 'yes', '1', 'enable', 'enabled'):
            return True
        if lowered in ('off', 'false', 'no', '0', 'disable', 'disabled'):
            return False
        raise ValueError(f"Invalid status: {argument}")

class Flags(TypedDict):
    punishment: str

class AmountFlags(TypedDict):
    punishment: str
    amount: int

# We'll define flag converters as classes with methods to parse.

class _BaseFlags:
    @classmethod
    def from_kwargs(cls, kwargs):
        return cls(**kwargs)

class _AmountFlags(_BaseFlags):
    def __init__(self, punishment: str, amount: int):
        self.punishment = punishment
        self.amount = amount

class _Flags(_BaseFlags):
    def __init__(self, punishment: str):
        self.punishment = punishment

# For commands, we'll use a simple parsing function since we can't use the original FlagConverter.
# We'll replicate the behavior by accepting `*, flags: str` and parsing.

# Instead of complex flag parsing, we'll use the same pattern as antinuke: use `*, flags: Flags`
# where Flags is a class with attributes. We'll use a custom decorator or parser.

# To keep it simple, we'll define commands with individual parameters for threshold and punishment.

# However, the original uses @flag to define flags. Since we don't have that, we'll adapt the commands
# to take explicit parameters: threshold, punishment, etc. But the user expects the same interface.

# We'll implement a flag parser that looks for --threshold, --punishment, etc.

def parse_flags(argument: str) -> dict:
    """Parse --key value pairs."""
    import re
    pattern = r'--(\w+)\s+([^\s]+(?:\s+[^\s]+)*?)(?=\s+--|$)'
    matches = re.findall(pattern, argument)
    flags = {}
    for key, value in matches:
        flags[key.lower()] = value.strip()
    return flags

# But it's easier to use the `@flag` from discord.py's FlagConverter, but we can't because we don't have the tools.
# Actually, we can use `discord.ext.commands.flag` and `FlagConverter` from the library? The user's bot uses discord.py 2.7, so we can import from discord.ext.commands.
# In the original, they imported from core.client import FlagConverter, which is a custom one. We'll just use the built-in one.

from discord.ext.commands import FlagConverter, flag

class AmountFlags(FlagConverter):
    punishment: Literal["ban", "kick", "timeout", "strip"] = flag(
        description="The punishment the member will receive.",
        aliases=["action", "punish", "do"],
        default="ban",
    )
    amount: Range[int, 3] = flag(
        description="The threshold before activation.",
        aliases=["count", "threshold"],
        default=5,
    )

class Flags(FlagConverter):
    punishment: Literal["ban", "kick", "timeout", "strip"] = flag(
        description="The punishment the member will receive.",
        aliases=["action", "punish", "do"],
        default="ban",
    )

class AutomodFlags(FlagConverter):
    punishment: Literal["delete", "timeout"] = flag(
        description="The punishment the member will receive when they violate automod.",
        aliases=["action", "punish", "do"],
        default="delete",
    )

# ===== Default avatar hashes =====
DEFAULT_AVATAR_HASHES = [
    "157e517cdbf371a47aaead44675714a3",
    "1628fc11e7961d85181295493426b775",
    "5445ffd7ffb201a98393cbdf684ea4b1",
    "79ee349b6511e2000af8a32fb8a6974e",
    "8569adcbd36c70a7578c017bf5604ea5",
    "f7f2e9361e8a54ce6e72580ac7b967af",
    "6c5996770c985bcd6e5b68131ff2ba04",
    "c82b3fa769ed6e6ffdea579381ed5f5c",
]

# ===== Logging function =====
async def send_log(bot: Bot, guild: Guild, perpetrator: Member, action: str, reason: str, duration: Optional[timedelta] = None):
    """Log to mod channel and history.moderation."""
    mod = await bot.pool.fetchrow("SELECT * FROM mod WHERE guild_id = $1", guild.id)
    if not mod or not mod.get("channel_id"):
        return

    # Get case number
    res = await bot.pool.fetchrow("SELECT count FROM cases WHERE guild_id = $1", guild.id)
    if not res:
        await bot.pool.execute("INSERT INTO cases (guild_id, count) VALUES ($1, $2)", guild.id, 0)
        case = 1
    else:
        case = int(res["count"]) + 1
    await bot.pool.execute("UPDATE cases SET count = $1 WHERE guild_id = $2", case, guild.id)

    # Insert into history
    await bot.pool.execute(
        """
        INSERT INTO history.moderation 
        (guild_id, case_id, user_id, moderator_id, action, reason, duration)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        guild.id, case, perpetrator.id, guild.me.id, action, reason,
        int(duration.total_seconds()) if duration else None
    )

    # Send embed
    channel = guild.get_channel(mod["channel_id"])
    if not channel:
        return

    embed = Embed(
        title="Anti-Raid Action",
        description=reason,
        timestamp=utcnow(),
        color=discord.Color.red()
    )
    embed.add_field(name="Perpetrator", value=f"{perpetrator.mention} (`{perpetrator.id}`)", inline=True)
    embed.add_field(name="Action", value=action, inline=True)
    if duration:
        embed.add_field(name="Duration", value=str(duration), inline=True)
    embed.set_footer(text=f"Case #{case}")

    with suppress(HTTPException):
        await channel.send(embed=embed)

# ===== Main Cog =====
class AntiRaid(Cog):
    """
    Protect your server from flood attacks.
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self.error_cooldown = {}

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS antiraid (
                guild_id BIGINT PRIMARY KEY,
                joins TEXT,
                mentions TEXT,
                avatar TEXT,
                browser TEXT,
                locked BOOLEAN DEFAULT FALSE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS antiraid_joins (
                guild_id BIGINT,
                user_id BIGINT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    def is_default(self, avatar: Optional[Asset]) -> bool:
        return not avatar or avatar.key in DEFAULT_AVATAR_HASHES

    # ===== Helper methods =====

    async def do_punishment(
        self,
        guild: Guild,
        member: Member | User,
        *,
        punishment: str,
        reason: str,
    ) -> bool:
        """Attempt to punish the member."""
        bot_member = guild.get_member(self.bot.user.id)
        if not bot_member:
            return False

        try:
            if punishment == "ban":
                await guild.ban(
                    member,
                    delete_message_days=7,
                    reason=reason,
                )
                await send_log(self.bot, guild, member, "antiraid_ban", reason)
                return True

            elif isinstance(member, User):
                return False

            elif punishment == "kick":
                await member.kick(reason=reason)
                await send_log(self.bot, guild, member, "antiraid_kick", reason)
                return True

            elif punishment == "timeout":
                duration = timedelta(days=27)
                await member.timeout(duration=duration, reason=reason)
                await send_log(self.bot, guild, member, "antiraid_timeout", reason, duration)
                return True

            elif punishment == "strip":
                await member.edit(roles=[], reason=reason)
                await send_log(self.bot, guild, member, "antiraid_strip", reason)
                return True

            return False

        except discord.HTTPException:
            return False

    async def notify_perpetrator(
        self,
        guild: Guild,
        member: Member,
        punishment: str,
        reason: str,
    ) -> None:
        """Send DM to perpetrator and create case."""
        await send_log(self.bot, guild, member, f"antiraid_{punishment}", reason)

        settings = await self.bot.pool.fetchrow(
            "SELECT * FROM mod WHERE guild_id = $1", guild.id
        )
        if settings and settings.get("dm_enabled"):
            try:
                script = settings.get("dm_antiraid")
                if script:
                    # Try to send using EmbedScript
                    try:
                        # script is a string with tags
                        processed = EmbedBuilder.embed_replacement(member, script) or script
                        content, embed, view = await EmbedBuilder.to_object(processed)
                        await member.send(content=content, embed=embed, view=view)
                    except Exception:
                        # Fallback to default
                        embed = Embed(
                            title="Anti-Raid Protection",
                            description=f"You were {punishment}ed due to raid detection",
                            color=discord.Color.red(),
                            timestamp=utcnow()
                        )
                        embed.add_field(name="Server", value=guild.name, inline=True)
                        embed.add_field(name="Reason", value=reason, inline=True)
                        await member.send(embed=embed)
                else:
                    embed = Embed(
                        title="Anti-Raid Protection",
                        description=f"You were {punishment}ed due to raid detection",
                        color=discord.Color.red(),
                        timestamp=utcnow()
                    )
                    embed.add_field(name="Server", value=guild.name, inline=True)
                    embed.add_field(name="Reason", value=reason, inline=True)
                    await member.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def submit_incident(
        self,
        guild: Guild,
        members: List[Member],
        punishment: str,
    ) -> None:
        """Secure the server during a raid incident."""
        current_time = datetime.now(timezone.utc)
        error_key = f"antiraid_incident_{guild.id}"
        
        if error_key in self.error_cooldown:
            if (current_time - self.error_cooldown[error_key]).total_seconds() < 300:
                return

        # (Analytics removed)

        await self.bot.pool.execute(
            "UPDATE antiraid SET locked = TRUE WHERE guild_id = $1", guild.id
        )
        ends_at = utcnow() + timedelta(hours=1)

        route = Route(
            "PUT",
            "/guilds/{guild_id}/incident-actions",
            guild_id=guild.id,
        )
        await self.bot.http.request(
            route,
            json={
                "invites_disabled_until": ends_at.isoformat(),
                "dms_disabled_until": ends_at.isoformat(),
            },
        )

        with suppress(HTTPException):
            embed = Embed(
                title="Raid Detected",
                description=f"Detected {len(members)} simultaneous joins",
            )
            embed.add_field(
                name="**Action**",
                value=(
                    "New members & DMs "
                    "have been temporarily restricted for an **hour**"
                ),
                inline=True,
            )
            embed.set_footer(text="The mitigation task has been initialized")
            await self.bot.notify(
                guild,
                content=f"<@{guild.owner_id}>",
                embed=embed,
            )

        await sleep(5)
        await self.bot.pool.execute(
            "UPDATE antiraid SET locked = FALSE WHERE guild_id = $1", guild.id
        )

        settings = await self.bot.pool.fetchrow(
            "SELECT * FROM mod WHERE guild_id = $1", guild.id
        )
        if settings and settings.get("dm_enabled"):
            for member in members:
                try:
                    await self.notify_perpetrator(guild, member, punishment, f"Detected {len(members)} simultaneous joins")
                except:
                    pass

    # ===== Commands =====

    @group(invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def antiraid(self, ctx: Context) -> Message:
        """The base command for managing raid security."""
        return await ctx.send_help(ctx.command)

    @antiraid.command(name="joins", aliases=["massjoin"], example="(on/off) (--threshold 5) (--punishment ban)")
    @has_permissions(manage_guild=True)
    async def antiraid_joins(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: AmountFlags,
    ) -> Message:
        """
        Security against accounts which join simultaneously.

        If multiple members join within the `threshold` then the
        members will automatically be punished.
        The `threshold` must be greater than 3.
        """
        if status is False:
            await self.bot.pool.execute(
                "UPDATE antiraid SET joins = NULL WHERE guild_id = $1", ctx.guild.id
            )
            return await ctx.approve("Join protection has been disabled")

        await self.bot.pool.execute(
            """
            INSERT INTO antiraid (guild_id, joins)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET joins = EXCLUDED.joins
            """,
            ctx.guild.id,
            json.dumps({"punishment": flags.punishment, "amount": flags.amount}),
        )
        return await ctx.approve(
            "Join protection has been enabled.",
            f"Threshold set as `{flags.amount}` with punishment: **{flags.punishment}**",
        )

    @antiraid.command(name="mentions", example="(on/off) (--threshold 5) (--punishment ban)")
    @has_permissions(manage_guild=True)
    async def antiraid_mentions(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: AmountFlags,
    ) -> Message:
        """
        Security against accounts that spam excessive mentions.

        If a message contains `threshold` or more mentions then the
        member will be automatically be punished.
        The `threshold` must be greater than 3.

        This only applies for user mentions. Everyone or Role
        mentions are not included.
        """
        if status is False:
            await self.bot.pool.execute(
                "UPDATE antiraid SET mentions = NULL WHERE guild_id = $1", ctx.guild.id
            )
            return await ctx.approve("Mention spam protection has been disabled")

        await self.bot.pool.execute(
            """
            INSERT INTO antiraid (guild_id, mentions)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET mentions = EXCLUDED.mentions
            """,
            ctx.guild.id,
            json.dumps({"punishment": flags.punishment, "amount": flags.amount}),
        )
        return await ctx.approve(
            "Mention spam protection has been enabled.",
            f"Threshold set as `{flags.amount}` with punishment: **{flags.punishment}**",
        )

    @antiraid.command(name="avatar", aliases=["pfp"], example="(on/off) (--punishment ban)")
    @has_permissions(manage_guild=True)
    async def antiraid_avatar(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """
        Security against accounts which don't have an avatar.
        """
        if status is False:
            await self.bot.pool.execute(
                "UPDATE antiraid SET avatar = NULL WHERE guild_id = $1", ctx.guild.id
            )
            return await ctx.approve("Default avatar protection has been disabled")

        await self.bot.pool.execute(
            """
            INSERT INTO antiraid (guild_id, avatar)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET avatar = EXCLUDED.avatar
            """,
            ctx.guild.id,
            json.dumps({"punishment": flags.punishment}),
        )
        return await ctx.approve(
            f"Default avatar protection has been enabled with punishment as **{flags.punishment}**"
        )

    @antiraid.command(
        name="automation",
        aliases=["selfbot", "browser", "web"],
        example="(on/off) (--punishment ban)"
    )
    @has_permissions(manage_guild=True)
    async def antiraid_automation(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """
        Security against accounts which are only active on browser.

        This is a common trait of selfbots and other automation tools.
        """
        if status is False:
            await self.bot.pool.execute(
                "UPDATE antiraid SET browser = NULL WHERE guild_id = $1", ctx.guild.id
            )
            return await ctx.approve("Automation protection has been disabled")

        members = list(
            filter(
                lambda member: member.web_status != DiscordStatus.offline
                and all(
                    status == DiscordStatus.offline
                    for status in [member.mobile_status, member.desktop_status]
                )
                and not member.bot
                and not member.premium_since,
                ctx.guild.members,
            )
        )
        if members:
            try:
                await ctx.prompt(
                    f"{plural(members, md='`')} members are currently only online via browser.",
                    f"Would you like to **{flags.punishment}** them now? This does not affect boosters",
                )
            except UserInputError:
                ...
            else:
                async with ctx.typing():
                    for member in members:
                        await self.do_punishment(
                            ctx.guild,
                            member,
                            punishment=flags.punishment,
                            reason="Automation detected",
                        )

        await self.bot.pool.execute(
            """
            INSERT INTO antiraid (guild_id, browser)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET browser = EXCLUDED.browser
            """,
            ctx.guild.id,
            json.dumps({"punishment": flags.punishment}),
        )
        return await ctx.approve(
            f"Automation protection has been enabled with punishment as **{flags.punishment}**"
        )

    @antiraid.group(name="filter", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def antiraid_filter(self, ctx: Context) -> Message:
        """Configure Discord's AutoMod filters."""
        return await ctx.send_help(ctx.command)

    @antiraid_filter.command(name="links", example="(invites/external/all) (on/off) (--punishment delete)")
    @has_permissions(manage_guild=True)
    async def filter_links(
        self,
        ctx: Context,
        filter_type: Literal["invites", "external", "all"],
        status: Annotated[bool, Status],
        *,
        flags: AutomodFlags,
    ) -> Message:
        """Setup Discord AutoMod for link filtering
        
        Types:
        - invites - Only Discord invite links
        - external - Only non-Discord links
        - all - All types of links
        """
        patterns = {
            "invites": [
                r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|com/invite))/[a-zA-Z0-9-]+"
            ],
            "external": [
                r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?",
                r"(?:https?://)?(?:\d{1,3}\.){3}\d{1,3}(?:/[^\s]*)?"
            ],
            "all": [
                r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|com/invite))/[a-zA-Z0-9-]+",
                r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?",
                r"(?:https?://)?(?:\d{1,3}\.){3}\d{1,3}(?:/[^\s]*)?"
            ]
        }

        try:
            if not status:
                rules = await ctx.guild.fetch_automod_rules()
                for rule in rules:
                    if rule.name == f"Xrypton - {filter_type.title()} Filter":
                        await rule.delete()
                return await ctx.approve(f"Removed {filter_type} filter")

            actions = [AutoModRuleAction(type=AutoModRuleActionType.block_message)]
            
            if flags.punishment == "timeout":
                actions.append(
                    AutoModRuleAction(
                        type=AutoModRuleActionType.timeout,
                        duration=timedelta(hours=1)
                    )
                )

            trigger = AutoModTrigger(
                type=AutoModRuleTriggerType.keyword,
                regex_patterns=patterns[filter_type]
            )

            exempt_roles = [role for role in ctx.guild.roles if role.permissions.manage_guild]

            rule = await ctx.guild.create_automod_rule(
                name=f"Xrypton - {filter_type.title()} Filter",
                event_type=AutoModRuleEventType.message_send,
                trigger=trigger,
                actions=actions,
                enabled=True,
                exempt_roles=exempt_roles,
                reason="Created via Xrypton antiraid filter command"
            )
            return await ctx.approve(f"Created {filter_type} filter with **{flags.punishment}** punishment")

        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules")
        except discord.HTTPException as e:
            return await ctx.warn(f"Failed to manage AutoMod rule: {e}")

    @antiraid_filter.group(name="exempt", aliases=["whitelist"], invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def filter_exempt(self, ctx: Context) -> Message:
        """Manage role exemptions for AutoMod filters"""
        return await ctx.send_help(ctx.command)

    @filter_exempt.command(name="add")
    @has_permissions(manage_guild=True)
    async def exempt_add(self, ctx: Context, target: Union[Role, Member]) -> Message:
        """Add a role or member to be exempt from AutoMod filters
        
        target: Can be either a role or member mention/ID"""
        try:
            rules = await ctx.guild.fetch_automod_rules()
            updated = False
            
            for rule in rules:
                if rule.name.startswith("Xrypton -"):
                    if isinstance(target, Role):
                        if target not in rule.exempt_roles:
                            await rule.edit(exempt_roles=[*rule.exempt_roles, target])
                            updated = True
                    else:  
                        if target not in rule.exempt_users:
                            await rule.edit(exempt_users=[*rule.exempt_users, target])
                            updated = True
            
            if updated:
                return await ctx.approve(f"Added {target.mention} to filter exemptions")
            else:
                return await ctx.warn("No active filter rules found")

        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules")
        except discord.HTTPException as e:
            return await ctx.warn(f"Failed to update exemptions: {e}")

    @filter_exempt.command(name="remove")
    @has_permissions(manage_guild=True)
    async def exempt_remove(self, ctx: Context, role: Role) -> Message:
        """Remove a role's exemption from AutoMod filters"""
        try:
            rules = await ctx.guild.fetch_automod_rules()
            updated = False
            
            for rule in rules:
                if rule.name.startswith("Xrypton -"):
                    if role.id in rule.exempt_roles:
                        await rule.edit(exempt_roles=[r for r in rule.exempt_roles if r != role.id])
                        updated = True
            
            if updated:
                return await ctx.approve(f"Removed {role.mention} from filter exemptions")
            else:
                return await ctx.warn("Role was not exempt from any filters")

        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules")
        except discord.HTTPException as e:
            return await ctx.warn(f"Failed to update exemptions: {e}")

    @filter_exempt.command(name="list")
    @has_permissions(manage_guild=True)
    async def exempt_list(self, ctx: Context) -> Message:
        """List all roles exempt from AutoMod filters"""
        try:
            rules = await ctx.guild.fetch_automod_rules()
            exempt_roles = set()
            
            for rule in rules:
                if rule.name.startswith("Xrypton -"):
                    exempt_roles.update(rule.exempt_roles)
            
            if not exempt_roles:
                return await ctx.warn("No roles are exempt from filters")
            
            roles = [ctx.guild.get_role(role_id) for role_id in exempt_roles]
            roles = [role.mention for role in roles if role]
            
            return await ctx.approve(
                "Exempt roles:",
                "\n".join(roles) if roles else "No valid exempt roles found"
            )

        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules")
        except discord.HTTPException as e:
            return await ctx.warn(f"Failed to fetch exemptions: {e}")

    # ===== Listeners =====

    @Cog.listener("on_member_join")
    async def check_raid(self, member: Member) -> None:
        """Check for simultaneous joins & default avatars."""
        if member.bot:
            return

        # Fetch config
        config = await self.bot.pool.fetchrow(
            "SELECT * FROM antiraid WHERE guild_id = $1", member.guild.id
        )
        if not config:
            return

        # If locked, punish immediately
        if config["locked"] is True:
            joins_config = json.loads(config["joins"]) if config["joins"] else None
            if joins_config:
                punished = await self.do_punishment(
                    member.guild,
                    member,
                    punishment=joins_config["punishment"],
                    reason="Server is on lockdown. (ANTIRAID ACTIVE)",
                )
                log.info(
                    "%s %s (%s) during an active raid in %s (%s).",
                    "Punished" if punished else "Failed to punish",
                    member, member.id, member.guild, member.guild.id,
                )
            return

        # Check default avatar
        if self.is_default(member.avatar) and config.get("avatar"):
            avatar_config = json.loads(config["avatar"])
            punished = await self.do_punishment(
                member.guild,
                member,
                punishment=avatar_config["punishment"],
                reason="Default avatar detected",
            )
            log.debug(
                "Default avatar detected from %s (%s) in %s (%s) [%s].",
                member, member.id, member.guild, member.guild.id,
                "PUNISHED" if punished else "FAILED TO PUNISH",
            )
            return

        # Check browser-only
        if (member.web_status != DiscordStatus.offline
            and all(status == DiscordStatus.offline for status in [member.mobile_status, member.desktop_status])
            and config.get("browser")):
            browser_config = json.loads(config["browser"])
            punished = await self.do_punishment(
                member.guild,
                member,
                punishment=browser_config["punishment"],
                reason="Spoofed gateway detected (BROWSER)",
            )
            log.debug(
                "Spoofed gateway detected from %s (%s) in %s (%s) [%s].",
                member, member.id, member.guild, member.guild.id,
                "PUNISHED" if punished else "FAILED TO PUNISH",
            )
            return

        # Check joins
        if not config.get("joins"):
            return
        joins_config = json.loads(config["joins"])

        # Insert join record
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO antiraid_joins (guild_id, user_id, joined_at) VALUES ($1, $2, CURRENT_TIMESTAMP)",
                member.guild.id, member.id
            )

        # Count recent joins (within 15 seconds)
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as count
                FROM antiraid_joins
                WHERE guild_id = $1 AND joined_at > (CURRENT_TIMESTAMP - INTERVAL '15 seconds')
                """,
                member.guild.id
            )
            count = row["count"] if row else 0

        if count < joins_config["amount"]:
            return

        # Raid detected
        # Get the members (we need Member objects for those recent joins)
        async with self.bot.pool.acquire() as conn:
            recent = await conn.fetch(
                """
                SELECT user_id
                FROM antiraid_joins
                WHERE guild_id = $1 AND joined_at > (CURRENT_TIMESTAMP - INTERVAL '15 seconds')
                ORDER BY joined_at ASC
                """,
                member.guild.id
            )
            member_ids = [r["user_id"] for r in recent]
            members = [m for m in member.guild.members if m.id in member_ids]

        # Submit incident
        future = self.submit_incident(member.guild, members, joins_config["punishment"])
        self.bot.loop.create_task(future)

        # Punish each member
        for m in members:
            if not isinstance(m, Member):
                continue
            await self.do_punishment(
                m.guild,
                m,
                punishment=joins_config["punishment"],
                reason=f"Detected {len(members)}/{joins_config['amount']} simultaneous joins",
            )

    @Cog.listener("on_message")
    async def check_mentions(self, message: Message) -> None:
        """Check for mention spam."""
        if (
            not message.guild
            or not isinstance(message.author, Member)
            or message.author.bot
            or message.author.guild_permissions.manage_messages
        ):
            return

        mentions = sum(
            not member.bot and member.id != message.author.id
            for member in message.mentions
        )
        if not mentions or mentions <= 2:
            return

        config = await self.bot.pool.fetchrow(
            "SELECT mentions FROM antiraid WHERE guild_id = $1", message.guild.id
        )
        if not config or not config["mentions"]:
            return
        mention_config = json.loads(config["mentions"])

        if mentions <= mention_config["amount"]:
            return

        punished = await self.do_punishment(
            message.guild,
            message.author,
            punishment=mention_config["punishment"],
            reason=f"Mention spam detected ({mentions}/{mention_config['amount']})",
        )
        log.info(
            "Mention spam detected from %s (%s) in %s (%s) [%s].",
            message.author, message.author.id, message.guild, message.guild.id,
            "PUNISHED" if punished else "FAILED TO PUNISH",
        )
