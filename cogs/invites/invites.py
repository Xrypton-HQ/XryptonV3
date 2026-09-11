import discord
from discord import app_commands, Interaction, Member, TextChannel, Embed
from discord.ext.commands import Cog, group, has_permissions
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import COLORS

class Invites(Cog):
    """
    Track and manage server invites.
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self.invite_cache = {}

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS invite_tracking (
                guild_id BIGINT,
                user_id BIGINT,
                inviter_id BIGINT,
                invite_code TEXT,
                uses INTEGER DEFAULT 1,
                bonus_uses INTEGER DEFAULT 0,
                joined_at TIMESTAMP,
                left_at TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS invite_config (
                guild_id BIGINT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT FALSE,
                log_channel_id BIGINT,
                fake_join_threshold REAL,
                account_age_requirement REAL,
                server_age_requirement REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS invite_rewards (
                guild_id BIGINT,
                role_id BIGINT,
                required_invites INTEGER,
                PRIMARY KEY (guild_id, role_id)
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    # ---- Helper methods ----
    def _parse_duration(self, duration: str) -> float:
        """
        Convert duration string to days.
        Examples: 3d, 24h, 1w, 1m
        Returns days as float or raises ValueError
        """
        time_units = {
            'h': 1,
            'd': 24,
            'w': 24 * 7,
            'm': 24 * 30
        }
        amount = int(duration[:-1])
        unit = duration[-1].lower()
        if unit not in time_units:
            raise ValueError("Invalid time unit. Use h/d/w/m (hours/days/weeks/months)")
        hours = amount * time_units[unit]
        return hours / 24

    def _format_duration(self, duration: str) -> str:
        amount = duration[:-1]
        unit = duration[-1].lower()
        units = {
            'h': 'hours',
            'd': 'days',
            'w': 'weeks',
            'm': 'months'
        }
        return f"{amount} {units[unit]}"

    def _format_stored_duration(self, days: float) -> str:
        if days >= 30:
            return f"{int(days/30)} months"
        elif days >= 7:
            return f"{int(days/7)} weeks"
        elif days >= 1:
            return f"{int(days)} days"
        else:
            return f"{int(days*24)} hours"

    def _make_paginator(self, ctx: Context, entries: List[Tuple[str, str]], title: str, per_page: int = 10) -> Paginator:
        """Helper to create a paginator from a list of (name, value) pairs."""
        pages = []
        for i in range(0, len(entries), per_page):
            chunk = entries[i:i+per_page]
            embed = Embed(title=title, color=COLORS.neutral)
            for name, value in chunk:
                embed.add_field(name=name, value=value, inline=False)
            pages.append(embed)
        if not pages:
            embed = Embed(title=title, description="No data", color=COLORS.neutral)
            pages.append(embed)
        return Paginator(ctx, pages)

    # ---- Commands ----
    @group(name="invites", aliases=["invitetracker", "invt", "it"], invoke_without_command=True)
    async def invites(self, ctx: Context, member: Optional[Member] = None):
        """
        View invite statistics for yourself or another member.
        """
        target = member or ctx.author

        data = await self.bot.pool.fetchrow(
            """
            SELECT 
                SUM(uses) as regular_invites,
                SUM(bonus_uses) as bonus_invites
            FROM invite_tracking 
            WHERE guild_id = $1 AND user_id = $2
            """,
            ctx.guild.id, target.id
        )

        if not data:
            return await ctx.warn(f"No invite data found for {target.mention}")

        total_invites = (data['regular_invites'] or 0) + (data['bonus_invites'] or 0)
        
        embed = Embed(
            title=f"Invite Statistics for {target.name}",
            color=COLORS.neutral,
            timestamp=datetime.now()
        )
        embed.add_field(name="Regular Invites", value=str(data['regular_invites'] or 0))
        embed.add_field(name="Bonus Invites", value=str(data['bonus_invites'] or 0))
        embed.add_field(name="Total Invites", value=str(total_invites))
        
        return await ctx.send(embed=embed)

    @invites.group(name="bonus")
    @has_permissions(manage_guild=True)
    async def invites_bonus(self, ctx: Context):
        """
        Manage bonus invites for the server.
        """
        await ctx.send_help(ctx.command)

    @invites_bonus.command(name="add")
    @has_permissions(manage_guild=True)
    async def bonus_add(self, ctx: Context, member: Member, amount: int):
        """
        Grant additional invite credits to a member.
        """
        if amount <= 0:
            return await ctx.warn("Amount must be positive")

        await self.bot.pool.execute(
            """
            INSERT INTO invite_tracking (guild_id, user_id, bonus_uses)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET bonus_uses = invite_tracking.bonus_uses + $3
            """,
            ctx.guild.id, member.id, amount
        )

        return await ctx.approve(f"Added {amount} bonus invites to {member.mention}")

    @invites_bonus.command(name="remove")
    @has_permissions(manage_guild=True)
    async def bonus_remove(self, ctx: Context, member: Member, amount: int):
        """
        Remove invite credits from a member.
        """
        if amount <= 0:
            return await ctx.warn("Amount must be positive")

        await self.bot.pool.execute(
            """
            UPDATE invite_tracking 
            SET bonus_uses = GREATEST(0, bonus_uses - $3)
            WHERE guild_id = $1 AND user_id = $2
            """,
            ctx.guild.id, member.id, amount
        )

        return await ctx.approve(f"Removed {amount} bonus invites from {member.mention}")

    @invites.command(name="code")
    async def invites_code(self, ctx: Context, member: Optional[Member] = None):
        """
        Display active invite codes for yourself or another member.
        """
        target = member or ctx.author
        
        invites = await ctx.guild.invites()
        user_invites = [inv for inv in invites if inv.inviter and inv.inviter.id == target.id]

        if not user_invites:
            return await ctx.warn(f"No active invite codes found for {target.mention}")

        entries = []
        for inv in user_invites:
            entries.append((
                f"Code: {inv.code}",
                f"Uses: {inv.uses}\nChannel: {inv.channel.mention}\nExpires: {'Never' if not inv.max_age else f'<t:{int((datetime.now() + timedelta(seconds=inv.max_age)).timestamp())}:R>'}"
            ))

        paginator = self._make_paginator(ctx, entries, f"Active Invite Codes for {target.name}", per_page=5)
        return await paginator.start()

    @invites.command(name="enable")
    @has_permissions(manage_guild=True)
    async def invites_enable(self, ctx: Context):
        """
        Enable invite tracking for the server.
        """
        await self.bot.pool.execute(
            """
            INSERT INTO invite_config (guild_id, is_enabled)
            VALUES ($1, true)
            ON CONFLICT (guild_id)
            DO UPDATE SET is_enabled = true
            """,
            ctx.guild.id
        )

        return await ctx.approve("Invite tracking has been enabled")

    @invites.command(name="disable")
    @has_permissions(manage_guild=True)
    async def invites_disable(self, ctx: Context):
        """
        Disable invite tracking for the server.
        """
        await self.bot.pool.execute(
            """
            INSERT INTO invite_config (guild_id, is_enabled)
            VALUES ($1, false)
            ON CONFLICT (guild_id)
            DO UPDATE SET is_enabled = false
            """,
            ctx.guild.id
        )

        return await ctx.approve("Invite tracking has been disabled")

    @invites.group(name="join", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invites_join(self, ctx: Context):
        """
        Manage join settings for invite tracking.
        """
        await ctx.send_help(ctx.command)

    @invites_join.command(name="stats")
    @has_permissions(manage_guild=True)
    async def join_stats(self, ctx: Context):
        """
        View detailed join statistics.
        """
        stats = await self.bot.pool.fetch(
            """
            WITH daily AS (
                SELECT inviter_id, COUNT(*) as daily
                FROM invite_tracking
                WHERE guild_id = $1
                AND joined_at > datetime('now', '-1 day')
                GROUP BY inviter_id
            ),
            weekly AS (
                SELECT inviter_id, COUNT(*) as weekly
                FROM invite_tracking
                WHERE guild_id = $1
                AND joined_at > datetime('now', '-7 days')
                GROUP BY inviter_id
            ),
            monthly AS (
                SELECT inviter_id, COUNT(*) as monthly
                FROM invite_tracking
                WHERE guild_id = $1
                AND joined_at > datetime('now', '-30 days')
                GROUP BY inviter_id
            )
            SELECT 
                i.inviter_id,
                COALESCE(d.daily, 0) as daily_invites,
                COALESCE(w.weekly, 0) as weekly_invites,
                COALESCE(m.monthly, 0) as monthly_invites
            FROM invite_tracking i
            LEFT JOIN daily d ON i.inviter_id = d.inviter_id
            LEFT JOIN weekly w ON i.inviter_id = w.inviter_id
            LEFT JOIN monthly m ON i.inviter_id = m.inviter_id
            WHERE i.guild_id = $1
            GROUP BY i.inviter_id
            ORDER BY monthly_invites DESC
            LIMIT 10
            """,
            ctx.guild.id
        )

        if not stats:
            return await ctx.warn("No invite statistics found")

        entries = []
        for stat in stats:
            member = ctx.guild.get_member(stat['inviter_id'])
            if not member:
                continue
            entries.append((
                member.name,
                f"Daily: {stat['daily_invites']}\nWeekly: {stat['weekly_invites']}\nMonthly: {stat['monthly_invites']}"
            ))

        paginator = self._make_paginator(ctx, entries, "📊 Invite Statistics", per_page=5)
        return await paginator.start()

    @invites_join.command(name="requirements")
    @has_permissions(manage_guild=True)
    async def join_requirements(self, ctx: Context, account_age: Optional[str] = None, server_age: Optional[str] = None):
        """
        Set join requirements for invite tracking.
        Examples: 3d, 24h, 1w, 1m
        """
        account_days = None
        server_days = None

        if account_age:
            try:
                account_days = self._parse_duration(account_age)
            except (ValueError, IndexError):
                return await ctx.warn("Invalid account age format. Examples: 3d, 24h, 1w, 1m")

        if server_age:
            try:
                server_days = self._parse_duration(server_age)
            except (ValueError, IndexError):
                return await ctx.warn("Invalid server age format. Examples: 3d, 24h, 1w, 1m")

        await self.bot.pool.execute(
            """
            INSERT INTO invite_config (guild_id, account_age_requirement, server_age_requirement)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id)
            DO UPDATE SET 
                account_age_requirement = COALESCE($2, invite_config.account_age_requirement),
                server_age_requirement = COALESCE($3, invite_config.server_age_requirement)
            """,
            ctx.guild.id, account_days, server_days
        )

        requirements = []
        if account_age is not None:
            requirements.append(f"Account Age: {self._format_duration(account_age)}")
        if server_age is not None:
            requirements.append(f"Server Age: {self._format_duration(server_age)}")

        return await ctx.approve(f"Updated join requirements:\n" + "\n".join(requirements))

    @invites_join.command(name="analytics")
    @has_permissions(manage_guild=True)
    async def join_analytics(self, ctx: Context):
        """
        View invite effectiveness analytics.
        """
        analytics = await self.bot.pool.fetch(
            """
            WITH invite_stats AS (
                SELECT 
                    invite_code,
                    COUNT(*) as total_joins,
                    COUNT(CASE WHEN joined_at > datetime('now', '-7 days') THEN 1 END) as active_members,
                    MIN(joined_at) as first_use,
                    MAX(joined_at) as last_use
                FROM invite_tracking
                WHERE guild_id = $1
                GROUP BY invite_code
            )
            SELECT 
                invite_code,
                total_joins,
                active_members,
                first_use,
                last_use,
                CASE 
                    WHEN total_joins > 0 THEN 
                        ROUND(CAST((active_members::float / total_joins * 100) as numeric), 1)
                    ELSE 0
                END as retention_rate
            FROM invite_stats
            ORDER BY total_joins DESC
            LIMIT 10
            """,
            ctx.guild.id
        )

        if not analytics:
            return await ctx.warn("No analytics data found")

        entries = []
        for stat in analytics:
            first_use = f"<t:{int(stat['first_use'].timestamp())}:R>" if stat['first_use'] else "N/A"
            last_use = f"<t:{int(stat['last_use'].timestamp())}:R>" if stat['last_use'] else "N/A"
            entries.append((
                f"Code: {stat['invite_code']}",
                f"Total Joins: {stat['total_joins']}\n"
                f"Active (7d): {stat['active_members']}\n"
                f"Retention: {stat['retention_rate']}%\n"
                f"First Use: {first_use}\n"
                f"Last Use: {last_use}"
            ))

        paginator = self._make_paginator(ctx, entries, "📈 Invite Analytics", per_page=5)
        return await paginator.start()

    @invites_join.command(name="threshold")
    @has_permissions(manage_guild=True)
    async def join_threshold(self, ctx: Context, *, duration: str):
        """
        Set minimum account age requirement for invite tracking.
        Examples: 3d, 24h, 1w, 1m
        """
        try:
            days = self._parse_duration(duration)
        except (ValueError, IndexError):
            return await ctx.warn("Invalid format. Examples: 3d, 24h, 1w, 1m")

        if days <= 0:
            return await ctx.warn("Duration must be positive")

        await self.bot.pool.execute(
            """
            INSERT INTO invite_config (guild_id, fake_join_threshold)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET fake_join_threshold = $2
            """,
            ctx.guild.id, days
        )

        return await ctx.approve(f"Join threshold set to {self._format_duration(duration)}")

    @invites_join.command(name="history")
    @has_permissions(manage_guild=True)
    async def join_history(self, ctx: Context, limit: Optional[int] = 10):
        """
        View recent server joins and their inviters.
        """
        if limit > 50:
            return await ctx.warn("Maximum history limit is 50")

        joins = await self.bot.pool.fetch(
            """
            SELECT 
                user_id,
                inviter_id,
                invite_code,
                joined_at
            FROM invite_tracking
            WHERE guild_id = $1
            ORDER BY joined_at DESC
            LIMIT $2
            """,
            ctx.guild.id, limit
        )

        if not joins:
            return await ctx.warn("No join history found")

        entries = []
        for join in joins:
            member = ctx.guild.get_member(join['user_id'])
            if not member:
                continue
            inviter = ctx.guild.get_member(join['inviter_id'])
            inviter_text = inviter.name if inviter else "Unknown"
            timestamp = f"<t:{int(join['joined_at'].timestamp())}:R>"
            entries.append((
                member.name,
                f"Invited by: {inviter_text}\nCode: {join['invite_code']}\nJoined: {timestamp}"
            ))

        paginator = self._make_paginator(ctx, entries, "📥 Recent Join History", per_page=5)
        return await paginator.start()

    @invites.command(name="inviter")
    async def invites_inviter(self, ctx: Context, member: Optional[Member] = None):
        """
        View who invited a member to the server.
        """
        target = member or ctx.author

        data = await self.bot.pool.fetchrow(
            """
            SELECT inviter_id, invite_code
            FROM invite_tracking
            WHERE guild_id = $1 AND user_id = $2
            """,
            ctx.guild.id, target.id
        )

        if not data or not data['inviter_id']:
            return await ctx.warn(f"No invite data found for {target.mention}")

        inviter = ctx.guild.get_member(data['inviter_id'])
        if not inviter:
            return await ctx.warn("Inviter is no longer in the server")

        embed = Embed(
            title="Invite Information",
            description=f"{target.mention} was invited by {inviter.mention}\nInvite Code: `{data['invite_code']}`",
            color=COLORS.neutral
        )
        return await ctx.send(embed=embed)

    @invites.command(name="leaderboard", aliases=["lb"])
    async def invites_leaderboard(self, ctx: Context):
        """
        Display the server's top inviters.
        """
        data = await self.bot.pool.fetch(
            """
            SELECT 
                user_id,
                SUM(uses) + SUM(bonus_uses) as total_invites,
                SUM(uses) as regular_invites,
                SUM(bonus_uses) as bonus_invites
            FROM invite_tracking 
            WHERE guild_id = $1
            GROUP BY user_id
            ORDER BY total_invites DESC
            """,
            ctx.guild.id
        )

        if not data:
            return await ctx.warn("No invite data found for this server")

        entries = []
        for idx, entry in enumerate(data, 1):
            member = ctx.guild.get_member(entry['user_id'])
            if not member:
                continue
            entries.append((
                f"#{idx} {member.name}",
                f"Total: {entry['total_invites']}\nRegular: {entry['regular_invites']}\nBonus: {entry['bonus_invites']}"
            ))

        paginator = self._make_paginator(ctx, entries, "📊 Invite Leaderboard", per_page=10)
        return await paginator.start()

    @invites.command(name="logs")
    @has_permissions(manage_guild=True)
    async def invites_logs(self, ctx: Context, channel: TextChannel):
        """
        Set the channel for invite tracking logs.
        """
        await self.bot.pool.execute(
            """
            INSERT INTO invite_config (guild_id, log_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET log_channel_id = $2
            """,
            ctx.guild.id, channel.id
        )

        return await ctx.approve(f"Invite logs will be sent to {channel.mention}")

    @invites.group(name="reward", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def invites_reward(self, ctx: Context):
        """
        Manage invite rewards for the server.
        """
        await ctx.send_help(ctx.command)

    @invites_reward.command(name="list")
    async def reward_list(self, ctx: Context):
        """
        View configured invite rewards.
        """
        rewards = await self.bot.pool.fetch(
            """
            SELECT role_id, required_invites
            FROM invite_rewards
            WHERE guild_id = $1
            ORDER BY required_invites ASC
            """,
            ctx.guild.id
        )

        if not rewards:
            return await ctx.warn("No invite rewards configured")

        entries = []
        for reward in rewards:
            role = ctx.guild.get_role(reward['role_id'])
            if role:
                entries.append((f"@{role.name}", f"Required Invites: {reward['required_invites']}"))

        paginator = self._make_paginator(ctx, entries, "🎁 Invite Rewards", per_page=10)
        return await paginator.start()

    @invites_reward.command(name="add")
    async def reward_add(self, ctx: Context, role: discord.Role, required_invites: int):
        """
        Add a new invite reward.
        """
        if required_invites <= 0:
            return await ctx.warn("Required invites must be positive")

        await self.bot.pool.execute(
            """
            INSERT INTO invite_rewards (guild_id, role_id, required_invites)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, role_id) 
            DO UPDATE SET required_invites = $3
            """,
            ctx.guild.id, role.id, required_invites
        )

        return await ctx.approve(f"Added reward: {role.mention} for {required_invites} invites")

    @invites_reward.command(name="remove")
    async def reward_remove(self, ctx: Context, role: discord.Role):
        """
        Remove an invite reward.
        """
        result = await self.bot.pool.execute(
            """
            DELETE FROM invite_rewards
            WHERE guild_id = $1 AND role_id = $2
            """,
            ctx.guild.id, role.id
        )

        if result == "DELETE 0":
            return await ctx.warn(f"No reward found for {role.mention}")

        return await ctx.approve(f"Removed reward for {role.mention}")

    @invites_reward.command(name="check")
    async def reward_check(self, ctx: Context, member: Optional[Member] = None):
        """
        Check available rewards and progress.
        """
        target = member or ctx.author

        invite_count = await self.bot.pool.fetchval(
            """
            SELECT SUM(uses) + SUM(bonus_uses) as total_invites
            FROM invite_tracking 
            WHERE guild_id = $1 AND user_id = $2
            """,
            ctx.guild.id, target.id
        ) or 0

        rewards = await self.bot.pool.fetch(
            """
            SELECT role_id, required_invites
            FROM invite_rewards
            WHERE guild_id = $1
            ORDER BY required_invites ASC
            """,
            ctx.guild.id
        )

        if not rewards:
            return await ctx.warn("No invite rewards configured")

        entries = []
        for reward in rewards:
            role = ctx.guild.get_role(reward['role_id'])
            if not role:
                continue
            has_role = role in target.roles
            progress = min(invite_count / reward['required_invites'] * 100, 100)
            status = "✅ Achieved" if has_role else f"⏳ Progress: {progress:.1f}%"
            entries.append((f"@{role.name}", f"Required: {reward['required_invites']}\n{status}"))

        embed = Embed(
            title=f"🎁 Invite Rewards Progress for {target.name}",
            description=f"Current Invites: {invite_count}",
            color=COLORS.neutral
        )
        paginator = Paginator(ctx, [embed])  # We'll just use one page with fields
        # But we have entries, we need to add fields
        # Since we have a paginator that expects embeds, we can just create one embed with all fields.
        # However if there are many, we can split.
        # Let's just create multiple embeds if needed.
        pages = []
        chunk_size = 5
        for i in range(0, len(entries), chunk_size):
            chunk = entries[i:i+chunk_size]
            e = Embed(
                title=f"🎁 Invite Rewards Progress for {target.name}",
                description=f"Current Invites: {invite_count}",
                color=COLORS.neutral
            )
            for name, value in chunk:
                e.add_field(name=name, value=value, inline=False)
            pages.append(e)
        if not pages:
            pages = [Embed(title="No rewards", color=COLORS.neutral)]
        paginator = Paginator(ctx, pages)
        return await paginator.start()

    @invites.command(name="settings")
    @has_permissions(manage_guild=True)
    async def invites_settings(self, ctx: Context):
        """
        View all invite tracking settings for the server.
        """
        settings = await self.bot.pool.fetchrow(
            """
            SELECT 
                is_enabled,
                log_channel_id,
                fake_join_threshold,
                account_age_requirement,
                server_age_requirement
            FROM invite_config
            WHERE guild_id = $1
            """,
            ctx.guild.id
        )

        if not settings:
            return await ctx.warn("No invite settings configured")

        embed = Embed(
            title="🔧 Invite Tracking Settings",
            color=COLORS.neutral,
            timestamp=datetime.now()
        )

        status = "✅ Enabled" if settings['is_enabled'] else "❌ Disabled"
        embed.add_field(name="Status", value=status, inline=True)

        requirements = []
        if settings['account_age_requirement']:
            requirements.append(f"Account Age: {settings['account_age_requirement']} days")
        if settings['server_age_requirement']:
            requirements.append(f"Server Age: {settings['server_age_requirement']} days")
        if settings['fake_join_threshold']:
            embed.add_field(
                name="Join Threshold",
                value=self._format_stored_duration(settings['fake_join_threshold']),
                inline=True
            )

        if requirements:
            embed.add_field(
                name="Join Requirements",
                value="\n".join(requirements),
                inline=True
            )

        log_channel = ctx.guild.get_channel(settings['log_channel_id']) if settings['log_channel_id'] else None
        log_status = f"📝 {log_channel.mention}" if log_channel else "❌ Not Set"
        embed.add_field(name="Log Channel", value=log_status, inline=True)

        # Rewards
        rewards = await self.bot.pool.fetch(
            """
            SELECT role_id, required_invites
            FROM invite_rewards
            WHERE guild_id = $1
            ORDER BY required_invites ASC
            """,
            ctx.guild.id
        )
        if rewards:
            reward_text = []
            for reward in rewards:
                role = ctx.guild.get_role(reward['role_id'])
                if role:
                    reward_text.append(f"{role.mention}: {reward['required_invites']} invites")
            embed.add_field(
                name="Invite Rewards",
                value="\n".join(reward_text) if reward_text else "None set",
                inline=True
            )

        # Statistics
        analytics = await self.bot.pool.fetchrow(
            """
            SELECT 
                COUNT(DISTINCT user_id) as total_tracked,
                COUNT(DISTINCT inviter_id) as total_inviters,
                COUNT(CASE WHEN joined_at > datetime('now', '-7 days') THEN 1 END) as recent_joins
            FROM invite_tracking
            WHERE guild_id = $1
            """,
            ctx.guild.id
        )
        if analytics:
            stats = [
                f"Total Tracked Members: {analytics['total_tracked']}",
                f"Active Inviters: {analytics['total_inviters']}",
                f"Recent Joins (7d): {analytics['recent_joins']}"
            ]
            embed.add_field(
                name="Statistics",
                value="\n".join(stats),
                inline=False
            )

        embed.set_footer(text=f"Use {ctx.prefix}invites help for detailed commands • Server ID: {ctx.guild.id}")
        return await ctx.send(embed=embed)

    async def _is_tracking_enabled(self, guild_id: int) -> bool:
        """Check if invite tracking is enabled for the guild"""
        return await self.bot.pool.fetchval(
            "SELECT is_enabled FROM invite_config WHERE guild_id = $1", guild_id
        ) or False

    async def _check_rewards(self, guild: discord.Guild, member: discord.Member):
        """Check and award invite rewards"""
        total_invites = await self.bot.pool.fetchval(
            """
            SELECT SUM(uses) + SUM(bonus_uses)
            FROM invite_tracking
            WHERE guild_id = $1 AND user_id = $2
            """,
            guild.id, member.id
        ) or 0

        rewards = await self.bot.pool.fetch(
            """
            SELECT role_id, required_invites
            FROM invite_rewards
            WHERE guild_id = $1
            ORDER BY required_invites ASC
            """,
            guild.id
        )

        for reward in rewards:
            role = guild.get_role(reward['role_id'])
            if not role:
                continue

            if total_invites >= reward['required_invites']:
                if role not in member.roles:
                    try:
                        await member.add_roles(role, reason="Invite reward")
                    except discord.HTTPException:
                        continue
            else:
                if role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Lost invite reward")
                    except discord.HTTPException:
                        continue

    # ---- Listeners ----
    @Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Initialize invite cache for new guilds"""
        if not await self._is_tracking_enabled(guild.id):
            return
        try:
            self.invite_cache[guild.id] = {
                invite.code: invite.uses 
                for invite in await guild.invites()
            }
        except discord.HTTPException:
            pass

    @Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        """Cache new invites when they're created"""
        if not await self._is_tracking_enabled(invite.guild.id):
            return
        if invite.guild.id not in self.invite_cache:
            self.invite_cache[invite.guild.id] = {}
        self.invite_cache[invite.guild.id][invite.code] = invite.uses

    @Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        """Remove deleted invites from cache"""
        if not await self._is_tracking_enabled(invite.guild.id):
            return
        if invite.guild.id in self.invite_cache:
            self.invite_cache[invite.guild.id].pop(invite.code, None)

    @Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Track which invite was used when a member joins"""
        if member.bot:
            return

        config = await self.bot.pool.fetchrow(
            """
            SELECT 
                is_enabled, 
                log_channel_id, 
                fake_join_threshold,
                account_age_requirement,
                server_age_requirement
            FROM invite_config 
            WHERE guild_id = $1 AND is_enabled = true
            """,
            member.guild.id
        )
        if not config:
            return

        try:
            current_invites = {
                invite.code: invite.uses 
                for invite in await member.guild.invites()
            }
        except discord.HTTPException:
            return

        used_invite = None
        inviter = None

        for code, uses in current_invites.items():
            cached_uses = self.invite_cache.get(member.guild.id, {}).get(code, 0)
            if uses > cached_uses:
                # Find the invite object
                for inv in await member.guild.invites():
                    if inv.code == code:
                        used_invite = inv
                        break
                break

        self.invite_cache[member.guild.id] = current_invites

        if not used_invite or not used_invite.inviter:
            return

        inviter = used_invite.inviter

        if config['fake_join_threshold']:
            account_age = (datetime.now(timezone.utc) - member.created_at).total_seconds() / 86400
            if account_age < config['fake_join_threshold']:
                if config['log_channel_id']:
                    log_channel = member.guild.get_channel(config['log_channel_id'])
                    if log_channel:
                        await log_channel.send(
                            f"⚠️ {member.mention} joined but didn't meet the age threshold "
                            f"(Account age: {account_age:.1f} days, Required: {config['fake_join_threshold']} days)"
                        )
                return

        await self.bot.pool.execute(
            """
            INSERT INTO invite_tracking (
                guild_id, 
                user_id, 
                inviter_id, 
                invite_code, 
                uses, 
                joined_at,
                left_at
            )
            VALUES ($1, $2, $3, $4, 1, $5, NULL)
            ON CONFLICT (guild_id, user_id) 
            DO UPDATE SET 
                inviter_id = $3,
                invite_code = $4,
                uses = invite_tracking.uses + 1,
                joined_at = $5,
                left_at = NULL
            """,
            member.guild.id, member.id, inviter.id, used_invite.code, datetime.now()
        )

        active_invites = await self.bot.pool.fetchval(
            """
            SELECT COUNT(*) 
            FROM invite_tracking 
            WHERE guild_id = $1 
            AND inviter_id = $2 
            AND left_at IS NULL
            """,
            member.guild.id, inviter.id
        )

        total_invites = await self.bot.pool.fetchval(
            """
            SELECT COUNT(*) 
            FROM invite_tracking 
            WHERE guild_id = $1 
            AND inviter_id = $2
            """,
            member.guild.id, inviter.id
        )

        await self._check_rewards(member.guild, inviter)

        if config['log_channel_id']:
            log_channel = member.guild.get_channel(config['log_channel_id'])
            if log_channel:
                embed = Embed(
                    title="Member Joined",
                    description=f"{member.mention} joined using {inviter.mention}'s invite",
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                embed.add_field(name="Invite Code", value=used_invite.code)
                embed.add_field(
                    name="Inviter Stats", 
                    value=f"Active: {active_invites}\nTotal: {total_invites}"
                )
                embed.set_footer(text=f"Member ID: {member.id}")
                await log_channel.send(embed=embed)

    @Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Track member leaves and update invite statistics"""
        if member.bot:
            return

        config = await self.bot.pool.fetchrow(
            """
            SELECT is_enabled, log_channel_id 
            FROM invite_config 
            WHERE guild_id = $1 AND is_enabled = true
            """,
            member.guild.id
        )
        if not config:
            return

        await self.bot.pool.execute(
            """
            UPDATE invite_tracking 
            SET left_at = $3
            WHERE guild_id = $1 AND user_id = $2
            """,
            member.guild.id, member.id, datetime.now()
        )

        invite_data = await self.bot.pool.fetchrow(
            "SELECT inviter_id FROM invite_tracking WHERE guild_id = $1 AND user_id = $2",
            member.guild.id, member.id
        )
        if not invite_data:
            return

        inviter = member.guild.get_member(invite_data['inviter_id'])
        if not inviter:
            return

        active_invites = await self.bot.pool.fetchval(
            """
            SELECT COUNT(*) 
            FROM invite_tracking 
            WHERE guild_id = $1 
            AND inviter_id = $2 
            AND left_at IS NULL
            """,
            member.guild.id, inviter.id
        )

        total_invites = await self.bot.pool.fetchval(
            """
            SELECT COUNT(*) 
            FROM invite_tracking 
            WHERE guild_id = $1 
            AND inviter_id = $2
            """,
            member.guild.id, inviter.id
        )

        await self._check_rewards(member.guild, inviter)

        if config['log_channel_id']:
            log_channel = member.guild.get_channel(config['log_channel_id'])
            if log_channel:
                embed = Embed(
                    title="Member Left",
                    description=f"{member.mention} left (Invited by {inviter.mention})",
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )
                embed.add_field(
                    name="Inviter Stats", 
                    value=f"Active: {active_invites}\nTotal: {total_invites}"
                )
                embed.set_footer(text=f"Member ID: {member.id}")
                await log_channel.send(embed=embed)