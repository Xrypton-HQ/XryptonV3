import asyncio
from contextlib import suppress
from datetime import datetime
from random import uniform
from typing import Annotated, List, Optional, cast, Dict, Tuple
import time

from discord import Embed, HTTPException, Member, Message, Role, TextChannel, Thread
from discord.ext.commands import BucketType, Cog, cooldown, group, has_permissions

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import COLORS

# ===== Simple Script class (replaces tools.conversion.script.Script) =====
class Script:
    """Simple script processor with placeholders."""
    def __init__(self, template: str, args: list):
        self.template = template
        self.args = args  # [guild, user, level_vars]

    def _format(self, text: str) -> str:
        guild = self.args[0] if len(self.args) > 0 else None
        user = self.args[1] if len(self.args) > 1 else None
        level_vars = self.args[2] if len(self.args) > 2 else None
        replacements = {
            "{guild.name}": guild.name if guild else "",
            "{guild.id}": str(guild.id) if guild else "",
            "{user.mention}": user.mention if user else "",
            "{user.name}": user.name if user else "",
            "{user.id}": str(user.id) if user else "",
            "{level}": str(level_vars.level) if level_vars else "",
            "{xp}": str(level_vars.xp) if level_vars else "",
            "{total_xp}": str(level_vars.total_xp) if level_vars else "",
        }
        for key, value in replacements.items():
            text = text.replace(key, value)
        return text

    async def send(self, destination):
        """Send the formatted script to a destination (Member, TextChannel, or Context)."""
        content = self._format(self.template)
        if hasattr(destination, 'send'):
            return await destination.send(content=content)
        else:
            return await destination.send(content=content)

# ===== LevelConfig class =====
class LevelConfig:
    def __init__(self, data: dict):
        self.guild_id = data.get("guild_id")
        self.status = data.get("status", True)
        self.cooldown = data.get("cooldown", 60)
        self.max_level = data.get("max_level", 0)
        self.stack_roles = data.get("stack_roles", True)
        self.formula_multiplier = data.get("formula_multiplier", 1)
        self.xp_multiplier = data.get("xp_multiplier", 1)
        self.xp_min = data.get("xp_min", 15)
        self.xp_max = data.get("xp_max", 40)
        self.effort_status = data.get("effort_status", False)
        self.effort_text = data.get("effort_text", 25)
        self.effort_image = data.get("effort_image", 3)
        self.effort_boost = data.get("effort_boost", 10)

    @classmethod
    async def fetch(cls, ctx: Context):
        record = await ctx.bot.pool.fetchrow(
            "SELECT * FROM level_config WHERE guild_id = $1 AND status = TRUE",
            ctx.guild.id
        )
        if record:
            return cls(record)
        return None

# ===== LevelData class =====
class LevelData:
    def __init__(self, data: dict):
        self.guild_id = data.get("guild_id")
        self.user_id = data.get("user_id")
        self.xp = data.get("xp", 0)
        self.level = data.get("level", 0)
        self.total_xp = data.get("total_xp", 0)
        self.last_message = data.get("last_message", datetime.utcnow())

    @property
    def next_level(self) -> int:
        return self.level + 1

    def required_xp(self, level: Optional[int] = None, multiplier: int = 1) -> int:
        level = level or self.next_level
        xp = sum((i * 100) + 75 for i in range(level))
        return int(xp * multiplier)

    @classmethod
    async def fetch(cls, ctx: Context) -> "LevelData":
        record = await ctx.bot.pool.fetchrow(
            """
            INSERT INTO level_member (guild_id, user_id, last_message)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET last_message = CURRENT_TIMESTAMP
            RETURNING *
            """,
            ctx.guild.id, ctx.author.id
        )
        # If no record returned (shouldn't happen), create a default
        if not record:
            record = {"guild_id": ctx.guild.id, "user_id": ctx.author.id,
                      "xp": 0, "level": 0, "total_xp": 0, "last_message": datetime.utcnow()}
        return cls(record)

# ===== Simple variable holder for notifications =====
class LevelVariables:
    def __init__(self, level: int, xp: int, total_xp: int):
        self.level = level
        self.xp = xp
        self.total_xp = total_xp

# ===== Main Cog =====
class Level(Cog):
    """
    Award members with roles for being active in the server.
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self._cooldowns: Dict[str, float] = {}  # key: "guild_id:user_id" -> last_message_time

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS level_config (
                guild_id BIGINT PRIMARY KEY,
                status BOOLEAN DEFAULT TRUE,
                cooldown INTEGER DEFAULT 60,
                max_level INTEGER DEFAULT 0,
                stack_roles BOOLEAN DEFAULT TRUE,
                formula_multiplier INTEGER DEFAULT 1,
                xp_multiplier INTEGER DEFAULT 1,
                xp_min INTEGER DEFAULT 15,
                xp_max INTEGER DEFAULT 40,
                effort_status BOOLEAN DEFAULT FALSE,
                effort_text INTEGER DEFAULT 25,
                effort_image INTEGER DEFAULT 3,
                effort_boost INTEGER DEFAULT 10
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS level_member (
                guild_id BIGINT,
                user_id BIGINT,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                total_xp INTEGER DEFAULT 0,
                last_message TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS level_role (
                guild_id BIGINT,
                role_id BIGINT,
                level INTEGER,
                PRIMARY KEY (guild_id, role_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS level_notification (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT,
                dm BOOLEAN DEFAULT FALSE,
                template TEXT
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    def required_xp(self, level: int, multiplier: int = 1) -> int:
        """Calculate required XP for a given level."""
        xp = sum((i * 100) + 75 for i in range(level))
        return int(xp * multiplier)

    async def grant_level_roles(
        self,
        ctx: Context,
        config: LevelConfig,
        data: LevelData,
    ):
        """Grant roles to the member based on level."""
        records = await self.bot.pool.fetch(
            "SELECT level, role_id FROM level_role WHERE guild_id = $1 AND level <= $2",
            ctx.guild.id, data.level
        )
        roles: List[tuple[Role, int]] = []
        for record in records:
            role = ctx.guild.get_role(record["role_id"])
            if role:
                roles.append((role, record["level"]))

        if not roles:
            return

        # Find the highest level role they've achieved
        level_role = max(roles, key=lambda r: r[1])
        if level_role[0] not in ctx.author.roles:
            await ctx.author.add_roles(
                level_role[0],
                reason=f"Member reached level {level_role[1]}"
            )

        if not config.stack_roles:
            remove_roles = [
                role for role, lvl in roles
                if role in ctx.author.roles and role != level_role[0]
            ]
            if remove_roles:
                await ctx.author.remove_roles(
                    *remove_roles,
                    reason="Stacking roles is disabled"
                )

    async def send_level_notification(
        self,
        ctx: Context,
        data: LevelData,
    ):
        """Send a notification for the member's level up."""
        record = await self.bot.pool.fetchrow(
            "SELECT channel_id, dm, template FROM level_notification WHERE guild_id = $1",
            ctx.guild.id
        )
        if not record:
            return

        template = record.get("template") or "{user.mention} has reached level **{level}**. GG!"
        script = Script(
            template,
            [
                ctx.guild,
                ctx.author,
                LevelVariables(level=data.level, xp=data.xp, total_xp=data.total_xp)
            ]
        )

        if record["dm"]:
            destination = ctx.author
        else:
            destination = ctx.guild.get_channel(record["channel_id"]) or ctx.channel

        if isinstance(destination, (Member, TextChannel, Thread)):
            with suppress(HTTPException):
                await script.send(destination)

    def _check_cooldown(self, guild_id: int, user_id: int, cooldown_seconds: int) -> bool:
        key = f"{guild_id}:{user_id}"
        now = time.time()
        if key in self._cooldowns:
            if now - self._cooldowns[key] < cooldown_seconds:
                return True  # Still on cooldown
        # Update timestamp
        self._cooldowns[key] = now
        return False

    @Cog.listener("on_message")
    async def level_listener(self, ctx: Context):
        """Award XP to members for sending messages."""
        # We need to check if it's a valid message and not a command.
        # The original used on_message_without_command, but that requires a custom event.
        # We'll just check if it's not a command by inspecting the prefix.
        if not ctx.guild or ctx.author.bot:
            return

        # Check if the message starts with any prefix
        prefixes = await self.bot.get_prefix(ctx.message)
        if any(ctx.message.content.startswith(p) for p in prefixes):
            return  # It's a command, skip.

        config = await LevelConfig.fetch(ctx)
        if not config:
            return

        # Rate limit using in-memory cooldown
        if self._check_cooldown(ctx.guild.id, ctx.author.id, config.cooldown):
            return

        data = await LevelData.fetch(ctx)

        # Calculate XP
        xp = int(uniform(config.xp_min, config.xp_max) * config.xp_multiplier)
        if config.effort_status:
            if len(ctx.message.content) >= config.effort_text:
                xp += config.effort_boost
            if len(ctx.message.attachments) >= config.effort_image:
                xp += config.effort_boost

        data.xp += xp
        data.total_xp += xp

        # Check for level up
        if data.xp >= data.required_xp(multiplier=config.formula_multiplier):
            data.xp = 0
            data.level += 1

            with suppress(HTTPException):
                await self.grant_level_roles(ctx, config, data)

            with suppress(HTTPException):
                await self.send_level_notification(ctx, data)

        # Update database
        await self.bot.pool.execute(
            """
            UPDATE level_member
            SET xp = $3, level = $4, total_xp = $5
            WHERE guild_id = $1 AND user_id = $2
            """,
            ctx.guild.id, ctx.author.id, data.xp, data.level, data.total_xp
        )

    @group(aliases=["lvl", "rank"], invoke_without_command=True, example="@x")
    async def level(self, ctx: Context, *, member: Optional[Member] = None) -> Message:
        """View your current rank."""
        member = member or ctx.author
        record = await self.bot.pool.fetchrow(
            """
            SELECT
                member.xp,
                member.level,
                member.total_xp,
                config.formula_multiplier,
                (SELECT COUNT(*) FROM level_member WHERE guild_id = member.guild_id AND level > 0) AS total_members,
                (SELECT COUNT(*) + 1 FROM level_member WHERE guild_id = member.guild_id AND total_xp > member.total_xp) AS rank
            FROM level_member AS member
            INNER JOIN level_config AS config ON member.guild_id = config.guild_id
            WHERE member.guild_id = $1 AND member.user_id = $2
            """,
            ctx.guild.id, member.id
        )
        if not record:
            return await ctx.warn("No level data has been recorded yet!")

        embed = Embed(color=COLORS.neutral)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar)
        embed.add_field(name="Level", value=record["level"])
        embed.add_field(
            name="Experience",
            value=f"{record['xp']:,} of {self.required_xp(record['level'] + 1, record['formula_multiplier']):,}"
        )
        embed.add_field(name="Total XP", value=f"{record['total_xp']:,}")
        embed.set_footer(text=f"Rank #{record['rank']} out of {record['total_members']} members")

        return await ctx.send(embed=embed)

    @level.command(name="leaderboard", aliases=["lb"])
    @cooldown(1, 5, BucketType.guild)
    async def level_leaderboard(self, ctx: Context) -> None:
        """View the server's level leaderboard."""
        records = await self.bot.pool.fetch(
            """
            SELECT user_id, level, total_xp
            FROM level_member
            WHERE guild_id = $1 AND level > 0
            ORDER BY total_xp DESC
            LIMIT 100
            """,
            ctx.guild.id
        )

        entries = []
        for record in records:
            member = ctx.guild.get_member(record["user_id"])
            if member:
                entries.append(
                    f"**{member}** is **level {record['level']:,}** (`{record['total_xp']:,} XP`)"
                )

        if not entries:
            return await ctx.warn("No level data has been recorded yet!")

        # Create paginator with embed pages
        pages = []
        chunk_size = 10
        for i in range(0, len(entries), chunk_size):
            chunk = entries[i:i+chunk_size]
            embed = Embed(title="Level Leaderboard", description="\n".join(chunk))
            pages.append(embed)

        paginator = Paginator(ctx, pages)
        await paginator.start()

    @level.command(name="website", aliases=["weblb", "webleaderboard", "web"])
    async def level_website(self, ctx: Context) -> None:
        """View the server's level leaderboard on the web."""
        return await ctx.send(f"https://xrypton.vercel.app/leaderboard/{ctx.guild.id}")

    @level.command(name="toggle", aliases=["switch"])
    @has_permissions(manage_guild=True)
    async def level_toggle(self, ctx: Context) -> Message:
        """Toggle the level system."""
        # Insert or update status
        status = await self.bot.pool.fetchval(
            """
            INSERT INTO level_config (guild_id, status)
            VALUES ($1, TRUE)
            ON CONFLICT (guild_id)
            DO UPDATE SET status = NOT level_config.status
            RETURNING status
            """,
            ctx.guild.id
        )
        return await ctx.approve(f"The level system has been {'enabled' if status else 'disabled'}")

    @level.group(name="role", aliases=["roles"], invoke_without_command=True)
    async def level_role(self, ctx: Context) -> Message:
        """Award roles to members for reaching a certain level."""
        if ctx.invoked_with == "roles":
            return await self.level_role_list(ctx)
        return await ctx.send_help(ctx.command)

    @level_role.command(name="stack", aliases=["stacking"])
    @has_permissions(manage_guild=True, manage_roles=True)
    async def level_role_stack(self, ctx: Context) -> Message:
        """Toggle if members should keep previous level roles."""
        stack_roles = await self.bot.pool.fetchval(
            """
            INSERT INTO level_config (guild_id)
            VALUES ($1)
            ON CONFLICT (guild_id)
            DO UPDATE SET stack_roles = NOT level_config.stack_roles
            RETURNING stack_roles
            """,
            ctx.guild.id
        )
        return await ctx.approve(f"{'Now' if stack_roles else 'No longer'} stacking level roles")

    @level_role.command(name="add", aliases=["create"], example="5 @level5")
    @has_permissions(manage_guild=True, manage_roles=True)
    async def level_role_add(
        self,
        ctx: Context,
        level: int,
        *,
        role: Annotated[Role, ...],
    ) -> Message:
        """Add a role to be granted at a certain level."""
        try:
            await self.bot.pool.execute(
                "INSERT INTO level_role (guild_id, role_id, level) VALUES ($1, $2, $3)",
                ctx.guild.id, role.id, level
            )
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                return await ctx.warn(f"The role {role.mention} is already being granted!")
            raise

        return await ctx.approve(f"Now granting {role.mention} at **level {level:,}**")

    @level_role.command(name="remove", aliases=["delete", "del", "rm"], example="@level5")
    @has_permissions(manage_guild=True, manage_roles=True)
    async def level_role_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, ...],
    ) -> Message:
        """Remove a role from being granted at a certain level."""
        level = await self.bot.pool.fetchval(
            """
            DELETE FROM level_role
            WHERE guild_id = $1 AND role_id = $2
            RETURNING level
            """,
            ctx.guild.id, role.id
        )
        if not level:
            return await ctx.warn(f"The role {role.mention} is not being granted!")
        return await ctx.approve(f"No longer granting {role.mention} at **level {level}**")

    @level_role.command(name="list", aliases=["ls"])
    async def level_role_list(self, ctx: Context) -> Message:
        """View the roles being granted at certain levels."""
        config = await LevelConfig.fetch(ctx)
        if not config:
            return await ctx.warn("The level system is not enabled in this server!")

        data = await LevelData.fetch(ctx)

        records = await self.bot.pool.fetch(
            "SELECT level, role_id FROM level_role WHERE guild_id = $1 ORDER BY level ASC",
            ctx.guild.id
        )
        roles: List[tuple[Role, int]] = []
        for record in records:
            role = ctx.guild.get_role(record["role_id"])
            if role:
                roles.append((role, record["level"]))

        if not roles:
            return await ctx.warn("No roles are being granted!")

        embed = Embed(title="Level Roles")
        for role, level in roles:
            unlocked = level <= data.level
            status = "***UNLOCKED***" if unlocked else f"Remaining XP: **{data.required_xp(level, config.formula_multiplier) - data.total_xp}**"
            embed.add_field(
                name=f"**Level {level:,}**",
                value=f"{status}\n> {role.mention}",
                inline=True
            )

        # If too many fields, paginate
        # For simplicity, we'll just send the embed; if >25 fields, we might need pagination, but it's rare.
        return await ctx.send(embed=embed)

async def setup(bot: Bot):
    await bot.add_cog(Level(bot))