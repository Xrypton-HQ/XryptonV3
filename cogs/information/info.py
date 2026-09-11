from __future__ import annotations

from itertools import groupby
from typing import Optional, Literal, Union
import aiohttp
import json
from uuid import UUID
from datetime import datetime, timezone, timedelta

import discord
from discord import (
    ActivityType,
    Colour,
    Embed,
    TextChannel,
    CategoryChannel,
    VoiceChannel,
    Guild,
    Invite,
    ButtonStyle,
    Member,
    Message,
    PartialInviteGuild,
    Permissions,
    Role,
    Spotify,
    Status,
    Streaming,
    User,
    app_commands,
    Interaction,
    TextStyle,
    SelectOption
)
from discord.ext.commands import (
    hybrid_command,
    hybrid_group,
    command,
    Cog,
    has_permissions
)
from discord.ui import Button, View, Modal, TextInput, Select
from discord.utils import format_dt, oauth_url, utcnow

import time
from pathlib import Path

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.managers.types import CogMeta
from base.managers.predicates import has_permissions, example
from base.config import CLIENT, EMOJIS, COLORS

class Information(CogMeta):
    """View information on various things."""
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.description = "View information on various things."
        self._cached_lines = None
        self._cached_files = None
        self._cached_imports = None
        self._cached_functions = None
        self._cached_commands = None
        self._cached_member_count = None
        self._last_cache_time = 0

    async def _update_cache(self):
        """Update cached stats for the about command."""
        current_time = time.time()
        if (current_time - self._last_cache_time < 7200 and 
            all(x is not None for x in [
                self._cached_lines,
                self._cached_files, 
                self._cached_imports,
                self._cached_functions,
                self._cached_commands,
                self._cached_member_count
            ])):
            return
        
        try:
            self._cached_lines = sum(
                len(open(p, encoding='utf-8', errors='ignore').readlines()) 
                for p in Path('.').rglob('*.py') 
                if not any(x in str(p) for x in [
                    '.venv', '.git', '__pycache__', 
                    '.pytest_cache', 'build', 'dist', 
                    '.eggs', '*.egg-info'
                ])
            )
            
            self._cached_files = len([
                p for p in Path('.').rglob('*.py') 
                if not any(x in str(p) for x in [
                    '.venv', '.git', '__pycache__', 
                    '.pytest_cache', 'build', 'dist', 
                    '.eggs', '*.egg-info'
                ])
            ])
            
            self._cached_commands = len([
                cmd for cmd in self.bot.walk_commands() 
                if cmd.cog_name not in ('Jishaku',)
            ])
            
            self._cached_member_count = sum(g.member_count for g in self.bot.guilds)
            
            self._last_cache_time = current_time
        except Exception as e:
            print(f"[Cache] Error updating cache: {str(e)}")

    # ─── Listeners ──────────────────────────────────────────────────

    @Cog.listener("on_guild_update")
    async def guild_name_listener(self, before: Guild, after: Guild):
        if before.name != after.name:
            await self.bot.pool.execute(
                """
                INSERT INTO gnames (guild_id, name, changed_at) 
                VALUES ($1, $2, $3)
                """, 
                before.id, 
                before.name,
                datetime.now()
            )

    @Cog.listener("on_user_update")
    async def name_history_listener(self, before: User, after: User) -> None:
        if before.name == after.name and before.global_name == after.global_name:
            return

        await self.bot.pool.execute(
            """
            INSERT INTO name_history (user_id, username)
            VALUES ($1, $2)
            """,
            after.id,
            (
                before.name
                if after.name != before.name
                else (before.global_name or before.name)
            ),
        )

    @Cog.listener()
    async def on_member_unboost(self, member: Member) -> None:
        if not member.premium_since:
            return

        await self.bot.pool.execute(
            """
            INSERT INTO boosters_lost (guild_id, user_id, lasted_for)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id) DO UPDATE
            SET lasted_for = EXCLUDED.lasted_for
            """,
            member.guild.id,
            member.id,
            utcnow() - member.premium_since,
        )

    # ─── Commands ──────────────────────────────────────────────────

    @hybrid_command(aliases=["beep"])
    async def ping(self, ctx: Context) -> None:
        """
        View the bot's latency.
        """
        latency = round(self.bot.latency * 1000)
        start_time = time.time()
        message = await ctx.send(embed=discord.Embed(
            description=f"{EMOJIS.LOADING} {ctx.author.mention}: ping...",
            color=COLORS.neutral
        ))
        end_time = time.time()
        edit_latency = round((end_time - start_time) * 1000)
        
        await message.edit(embed=discord.Embed(
            description=f"{EMOJIS.APPROVE} {ctx.author.mention}: `{latency}ms` (edit: `{edit_latency}ms`)",
            color=COLORS.approve
        ))

    @hybrid_command()
    async def shards(self, ctx: Context):
        """
        View the bot shard latency.
        """
        embed = Embed(title=f"Total shards [{self.bot.shard_count}]")

        for shard_id in self.bot.shards:
            guilds = [g for g in self.bot.guilds if g.shard_id == shard_id]
            users = sum([g.member_count for g in guilds])
            shard_indicator = "🟢" if ctx.guild and ctx.guild.shard_id == shard_id else ""
            embed.add_field(
                name=f"Shard {shard_id} {shard_indicator}",
                value=f"**ping**: ``{round(self.bot.shards.get(shard_id).latency * 1000)}ms``\n**guilds**: ``{len(guilds)}``\n**users**: ``{users:,}``",
                inline=True,
            )
        
        if ctx.guild:
            embed.set_footer(text=f"You are on Shard {ctx.guild.shard_id}", icon_url=f"{self.bot.user.display_avatar.url}")

        await ctx.send(embed=embed)

    @command(aliases=["inv"])
    async def invite(self, ctx: Context) -> Message:
        """
        Get an invite link for the bot.
        """
        view = View()
        view.add_item(
            Button(
                url="https://discord.com/api/oauth2/authorize?client_id={}&permissions=8&scope=bot%20applications.commands".format(self.bot.user.id),
                style=ButtonStyle.link,
                label="Invite Me"
            )
        )
        return await ctx.send(view=view)

    @command(aliases=["discord"])
    async def support(self, ctx: Context) -> Message:
        """
        Get an invite link for the bot's support server.
        """
        view = View()
        view.add_item(
            Button(
                url="https://discord.gg/your-support-server",  # Replace with your support server
                style=ButtonStyle.link,
                label="Support Server"
            )
        )
        return await ctx.send(view=view)

    @hybrid_command(name="about", aliases=["botinfo", "bi"])
    async def about(self, ctx: Context) -> Message:
        """
        View information about the bot.
        """
        await self._update_cache()

        regular_cogs = len(self.bot.cogs)
        extensions = len(self.bot.extensions)
        total_modules = regular_cogs + extensions

        embed = Embed(
            description=(
                f"Developed and maintained by [Xrypton Team](https://xrypton.vercel.app)\n"
                f"Utilizing ``{self._cached_commands or 0:,}`` commands across ``{len(self.bot.cogs)}`` cogs (`{total_modules}` total modules)"
            ),
            color=COLORS.neutral
        )
        embed.set_author(
            name=self.bot.user.name,
            icon_url=self.bot.user.display_avatar.url,
        )

        embed.add_field(
            name="**Bot**",
            inline=True,
            value="\n".join(
            [
                f"**Users:** `{self._cached_member_count or 0:,}`",
                f"**Servers:** `{len(self.bot.guilds):,}`",
                f"**Created:** <t:{int(self.bot.user.created_at.timestamp())}:R>",
            ]
            ),
        )

        embed.add_field(
            name="**Code**",
            inline=True,
            value="\n".join(
                [
                    f"**Lines:** `{self._cached_lines or 0:,}`",
                    f"**Files:** `{self._cached_files or 0:,}`",
                    f"**Commands:** `{self._cached_commands or 0:,}`",
                ]
            ),
        )

        button1 = Button(
            label="GitHub",
            style=discord.ButtonStyle.gray,
            url="https://github.com/your-repo",  # Replace with your repo
        )

        button2 = Button(
            label="Support",
            style=discord.ButtonStyle.gray,
            url="https://discord.gg/your-support-server",  # Replace with your support server
        )

        button3 = Button(
            label="Website",
            style=discord.ButtonStyle.gray,
            url="https://xrypton.vercel.app",
        )

        view = discord.ui.View()
        view.add_item(button1)
        view.add_item(button2)
        view.add_item(button3)

        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        return await ctx.send(embed=embed, view=view)

    @app_commands.command(name='botinfo')
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def botinfo_slash(self, interaction: Interaction):
        """View information about the bot."""
        # Convert interaction to context
        ctx = await self.bot.get_context(interaction.message) if interaction.message else None
        if ctx:
            await self.about(ctx)
        else:
            # Fallback for slash-only
            await self.about(await Context.from_interaction(interaction))

    @command(example=",inviteinfo https://discord.gg/example", aliases=["ii"])
    async def inviteinfo(self, ctx: Context, *, invite: Invite) -> Message:
        """
        View information about an invite.
        """
        guild = invite.guild
        embed = Embed(
            description=f"{format_dt(guild.created_at)} ({format_dt(guild.created_at, 'R')})",
            color=COLORS.neutral
        )
        embed.set_author(
            name=f"{guild.name} ({guild.id})",
            url=invite.url,
            icon_url=guild.icon.url if guild.icon else None,
        )

        embed.add_field(
            name="**Information**",
            value=(
                f"**Invitier:** {invite.inviter or 'Vanity URL'}\n"
                f"**Channel:** {invite.channel or 'Unknown'}\n"
                f"**Created:** {format_dt(invite.created_at or guild.created_at)}"
            ),
        )
        embed.add_field(
            name="**Guild**",
            value=(
                f"**Members:** {invite.approximate_member_count:,}\n"
                f"**Members Online:** {invite.approximate_presence_count:,}\n"
                f"**Verification Level:** {guild.verification_level.name.title()}"
            ),
        )

        return await ctx.send(embed=embed)

    @command(example=",serverbanner https://discord.gg/example", aliases=["sbanner"])
    async def serverbanner(
        self,
        ctx: Context,
        *,
        invite: Optional[Invite] = None,
    ) -> Message:
        """
        View a server's banner if one is present.
        """
        guild = (
            invite.guild
            if invite and isinstance(invite.guild, PartialInviteGuild)
            else ctx.guild
        )
        if not guild.banner:
            return await ctx.deny(f"**{guild}** doesn't have a banner present!")

        embed = Embed(
            url=guild.banner.url,
            title=f"{guild}'s banner",
            color=COLORS.neutral
        )
        embed.set_image(url=guild.banner.url)

        return await ctx.send(embed=embed)

    @command(example=",servericon https://discord.gg/example", aliases=["sicon"])
    async def servericon(
        self,
        ctx: Context,
        *,
        invite: Optional[Invite] = None,
    ) -> Message:
        """
        View a server's icon if one is present.
        """
        guild = (
            invite.guild
            if invite and isinstance(invite.guild, PartialInviteGuild)
            else ctx.guild
        )
        if not guild.icon:
            return await ctx.deny(f"**{guild}** doesn't have a icon present!")

        embed = Embed(
            url=guild.icon.url,
            title=f"{guild}'s icon",
            color=COLORS.neutral
        )
        embed.set_image(url=guild.icon.url)

        return await ctx.send(embed=embed)

    @hybrid_command(
        aliases=["pfp", "avi", "av"],
        example=",avatar @user"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.default_permissions(use_application_commands=True)
    async def avatar(
        self,
        ctx: Context,
        *,
        user: Member | User = None,
    ) -> Message:
        """
        View a user's avatar.
        """
        if user is None:
            user = ctx.author

        embed = Embed(
            url=user.avatar.url if user.avatar else user.default_avatar.url,
            title="Your avatar" if user == ctx.author else f"{user.name}'s avatar",
            color=COLORS.neutral
        )
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.set_image(url=user.avatar.url if user.avatar else user.default_avatar.url)

        return await ctx.send(embed=embed)

    @hybrid_command(
        aliases=["spfp", "savi", "sav"],
        example=",serveravatar @user"
    )
    async def serveravatar(
        self,
        ctx: Context,
        *,
        member: Member = None,
    ) -> Message:
        """
        View a user's server avatar.
        """
        if member is None:
            member = ctx.author
            
        if not member.guild_avatar:
            return await ctx.deny(
                "You don't have a server avatar present!"
                if member == ctx.author
                else f"**{member}** doesn't have a server avatar present!"
            )

        embed = Embed(
            url=member.guild_avatar.url,
            title=(
                "Your server avatar"
                if member == ctx.author
                else f"{member.name}'s server avatar"
            ),
            color=COLORS.neutral
        )
        embed.set_image(url=member.guild_avatar.url)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        return await ctx.send(embed=embed)

    @hybrid_command(
        aliases=["ub"],
        example=",banner @user"
    )
    async def banner(
        self,
        ctx: Context,
        *,
        user: Member | User = None,
    ) -> Message:
        """
        View a user's banner if one is present.
        """
        if user is None:
            user = ctx.author

        fetched_user = await self.bot.fetch_user(user.id)

        if not fetched_user.banner:
            return await ctx.deny(
                "You don't have a banner present!"
                if user == ctx.author
                else f"**{user}** doesn't have a banner present!"
            )

        embed = Embed(
            url=fetched_user.banner.url,
            title="Your banner" if user == ctx.author else f"{user.name}'s banner",
            color=COLORS.neutral
        )
        embed.set_image(url=fetched_user.banner.url)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        return await ctx.send(embed=embed)

    @command(aliases=["mc"], example=",membercount")
    async def membercount(
        self,
        ctx: Context,
        *,
        guild: Optional[Guild] = None,
    ) -> Message:
        """
        View the member count of a server.
        """
        guild = guild or ctx.guild
        embed = Embed(color=COLORS.neutral)
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )

        humans = list(filter(lambda member: not member.bot, guild.members))
        bots = list(filter(lambda member: member.bot, guild.members))

        embed.add_field(name="**Members**", value=f"{len(guild.members):,}")
        embed.add_field(name="**Humans**", value=f"{len(humans):,}")
        embed.add_field(name="**Bots**", value=f"{len(bots):,}")

        return await ctx.send(embed=embed)

    @hybrid_command(aliases=["sinfo", "si"], example=",serverinfo")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.default_permissions(use_application_commands=True)
    async def serverinfo(
        self,
        ctx: Context,
        *,
        guild: str = None,
    ) -> Message:
        """
        View information about the server.
        """
        if guild:
            try:
                guild_id = int(guild)
                target_guild = self.bot.get_guild(guild_id)
            except ValueError:
                guild = guild.lower().strip()
                
                if 'discord.gg/' in guild:
                    invite_code = guild.split('discord.gg/')[-1]
                else:
                    invite_code = guild
                    
                try:
                    invite = await self.bot.fetch_invite(invite_code)
                    target_guild = invite.guild
                except (discord.NotFound, discord.HTTPException):
                    target_guild = discord.utils.get(self.bot.guilds, name=guild)
        else:
            target_guild = ctx.guild

        if not target_guild:
            return await ctx.deny("Server not found!")

        embed = Embed(
            description=f"{format_dt(target_guild.created_at)} ({format_dt(target_guild.created_at, 'R')})",
            color=COLORS.neutral
        )
        embed.set_author(
            name=f"{target_guild.name} ({target_guild.id})",
            url=target_guild.vanity_url if target_guild.vanity_url else None,
            icon_url=target_guild.icon.url if target_guild.icon else None,
        )

        embed.add_field(
            name="**Information**",
            value=(
                f"**Owner:** {target_guild.owner or target_guild.owner_id}\n"
                f"**Verification:** {target_guild.verification_level.name.title()}\n"
                f"**Nitro Boosts:** {target_guild.premium_subscription_count:,} (`Level {target_guild.premium_tier}`)"
            ),
        )
        embed.add_field(
            name="**Statistics**",
            value=(
                f"**Members:** {target_guild.member_count:,}\n"
                f"**Text Channels:** {len(target_guild.text_channels):,}\n"
                f"**Voice Channels:** {len(target_guild.voice_channels):,}\n"
            ),
        )

        if target_guild == ctx.guild and (roles := target_guild.roles[1:]):
            roles = list(reversed(roles))

            embed.add_field(
                name=f"**Roles ({len(roles)})**",
                value=(
                    ", ".join(role.mention for role in roles[:5])
                    + (f" (+{len(roles) - 5})" if len(roles) > 5 else "")
                ),
                inline=False,
            )

        return await ctx.send(embed=embed)

    @hybrid_command(aliases=["uinfo", "ui"], example=",userinfo @user")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.default_permissions(use_application_commands=True)
    async def userinfo(
        self,
        ctx: Context,
        *,
        user: Member | User = None,
    ) -> Message:
        """
        View information about a user.
        """
        if user is None:
            user = ctx.author

        embed = Embed(color=user.color if user.color != Colour.default() else COLORS.neutral)
        embed.title = f"{user} {'[BOT]' if user.bot else ''}"
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.description = ""

        if isinstance(user, Member):
            badges = []
            if user is ctx.guild.owner:
                badges.append("👑")
            if user.bot:
                badges.append("🤖")
            
            if badges:
                embed.description = f"{' '.join(badges)}"

        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"{len(user.mutual_guilds)} mutual servers")

        embed.add_field(
            name="**Created**",
            value=(
                format_dt(user.created_at, "D")
                + "\n> "
                + format_dt(user.created_at, "R")
            ),
        )

        if isinstance(user, Member) and user.joined_at:
            join_pos = sorted(
                user.guild.members,
                key=lambda member: member.joined_at or utcnow(),
            ).index(user)

            embed.add_field(
                name=f"**Joined ({join_pos + 1})**",
                value=(
                    format_dt(user.joined_at, "D")
                    + "\n> "
                    + format_dt(user.joined_at, "R")
                ),
            )

            if user.premium_since:
                embed.add_field(
                    name="**Boosted**",
                    value=(
                        format_dt(user.premium_since, "D")
                        + "\n> "
                        + format_dt(user.premium_since, "R")
                    ),
                )

            if roles := user.roles[1:]:
                embed.add_field(
                    name="**Roles**",
                    value=", ".join(role.mention for role in list(reversed(roles))[:5])
                    + (f" (+{len(roles) - 5})" if len(roles) > 5 else ""),
                    inline=False,
                )

            if (voice := user.voice) and voice.channel:
                members = len(voice.channel.members) - 1
                phrase = "Streaming inside" if voice.self_stream else "Inside"
                embed.description += f"🎙 {phrase} {voice.channel.mention} " + (
                    f"with {members} others" if members else "by themselves"
                )

            for activity_type, activities in groupby(
                user.activities,
                key=lambda activity: activity.type,
            ):
                activities = list(activities)
                if isinstance(activities[0], Spotify):
                    activity = activities[0]
                    embed.description += f"\n🎵 Listening to [**{activity.title}**]({activity.track_url}) by **{activity.artists[0]}**"

                elif isinstance(activities[0], Streaming):
                    embed.description += "\n🎥 Streaming " + ", ".join(
                        [
                            f"[**{activity.name}**]({activity.url})"
                            for activity in activities
                            if isinstance(activity, Streaming)
                        ]
                    )

                elif activity_type == ActivityType.playing:
                    embed.description += "\n🎮 Playing " + ", ".join(
                        [f"**{activity.name}**" for activity in activities]
                    )

                elif activity_type == ActivityType.watching:
                    embed.description += "\n📺 Watching " + ", ".join(
                        [f"**{activity.name}**" for activity in activities]
                    )

                elif activity_type == ActivityType.competing:
                    embed.description += "\n🏆 Competing in " + ", ".join(
                        [f"**{activity.name}**" for activity in activities]
                    )

        return await ctx.send(embed=embed)

    @hybrid_group(
        aliases=["names", "nh"],
        invoke_without_command=True,
        example=",namehistory @user"
    )
    async def namehistory(
        self,
        ctx: Context,
        *,
        user: Member | User = None,
    ) -> Message:
        """
        View a user's name history.
        """
        if user is None:
            user = ctx.author

        names = await self.bot.pool.fetch(
            """
            SELECT *
            FROM name_history
            WHERE user_id = $1
            ORDER BY changed_at DESC
            """,
            user.id,
        )
        if not names:
            return await ctx.deny(f"**{user}** doesn't have any name history!")

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title="Name History",
                    description="\n".join([
                        f"**{record['username']}** ({format_dt(record['changed_at'], 'R')})"
                        for record in names[i:i+10]
                    ]),
                    color=COLORS.neutral
                )
                for i in range(0, len(names), 10)
            ]
        )
        return await paginator.start(ctx)

    @namehistory.command(
        name="clear",
        aliases=["clean", "reset"],
    )
    async def namehistory_clear(self, ctx: Context) -> Message:
        """
        Remove all your name history.
        """
        await self.bot.pool.execute(
            """
            DELETE FROM name_history
            WHERE user_id = $1
            """,
            ctx.author.id,
        )
        return await ctx.approve("Successfully cleared your name history")

    @hybrid_command(
        aliases=["device", "presence"],
        example=",devices @user"
    )
    async def devices(
        self,
        ctx: Context,
        *,
        member: Member = None,
    ) -> Message:
        """
        View a member's platforms.
        """
        if member is None:
            member = ctx.author

        if member.status == Status.offline:
            return await ctx.deny(
                "You're appearing offline!"
                if member == ctx.author
                else f"**{member}** doesn't appear to be online!"
            )

        emojis = {
            Status.offline: "⚪️",
            Status.online: "🟢",
            Status.idle: "🟡",
            Status.dnd: "🔴",
        }

        embed = Embed(
            title="Your devices" if member == ctx.author else f"{member.name}'s devices",
            color=COLORS.neutral
        )
        embed.description = ""
        
        for activity_type, activities in groupby(
            member.activities,
            key=lambda activity: activity.type,
        ):
            activities = list(activities)
            if isinstance(activities[0], Spotify):
                activity = activities[0]
                embed.description += f"\n🎵 Listening to [**{activity.title}**]({activity.track_url}) by **{activity.artists[0]}**"

            elif isinstance(activities[0], Streaming):
                embed.description += "\n🎥 Streaming " + ", ".join(
                    [
                        f"[**{activity.name}**]({activity.url})"
                        for activity in activities
                        if isinstance(activity, Streaming)
                    ]
                )

            elif activity_type == ActivityType.playing:
                embed.description += "\n🎮 Playing " + ", ".join(
                    [f"**{activity.name}**" for activity in activities]
                )

            elif activity_type == ActivityType.watching:
                embed.description += "\n📺 Watching " + ", ".join(
                    [f"**{activity.name}**" for activity in activities]
                )

            elif activity_type == ActivityType.competing:
                embed.description += "\n🏆 Competing in " + ", ".join(
                    [f"**{activity.name}**" for activity in activities]
                )

        embed.description += "\n" + "\n".join(
            [
                f"{emojis[status]} **{device}**"
                for device, status in {
                    "Mobile": member.mobile_status,
                    "Desktop": member.desktop_status,
                    "Browser": member.web_status,
                }.items()
                if status != Status.offline
            ]
        )

        return await ctx.send(embed=embed)

    @hybrid_command()
    async def roles(self, ctx: Context) -> Message:
        """
        View the server roles.
        """
        roles = list(reversed(ctx.guild.roles[1:]))
        if not roles:
            return await ctx.deny(f"**{ctx.guild}** doesn't have any roles!")

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"Roles in {ctx.guild}",
                    description="\n".join([
                        f"{role.mention} (`{role.id}`)" 
                        for role in roles[i:i+15]
                    ]),
                    color=COLORS.neutral
                )
                for i in range(0, len(roles), 15)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_command(example=",inrole @role")
    async def inrole(self, ctx: Context, *, role: Role) -> Message:
        """
        View members which have a role.
        """
        members = role.members
        if not members:
            return await ctx.deny(f"{role.mention} doesn't have any members!")

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"Members with {role}",
                    description="\n".join([
                        f"{member.mention} (`{member.id}`)" 
                        for member in members[i:i+15]
                    ]),
                    color=COLORS.neutral
                )
                for i in range(0, len(members), 15)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_group(invoke_without_command=True)
    async def boosters(self, ctx: Context) -> Message:
        """
        View server boosters.
        """
        members = list(
            filter(
                lambda member: member.premium_since is not None,
                ctx.guild.members,
            )
        )
        if not members:
            return await ctx.deny("No members are currently boosting!")

        entries = [
            f"{member.mention} - boosted {format_dt(member.premium_since or utcnow(), 'R')}"
            for member in sorted(
                members,
                key=lambda member: member.premium_since or utcnow(),
                reverse=True,
            )
        ]

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title="Boosters",
                    description="\n".join(entries[i:i+15]),
                    color=COLORS.neutral
                )
                for i in range(0, len(entries), 15)
            ]
        )
        return await paginator.start(ctx)

    @boosters.command(name="lost")
    async def boosters_lost(self, ctx: Context) -> Message:
        """
        View all lost boosters.
        """
        records = await self.bot.pool.fetch(
            """
            SELECT *
            FROM boosters_lost
            WHERE guild_id = $1
            ORDER BY ended_at DESC
            """,
            ctx.guild.id,
        )
        
        users = []
        for record in records:
            user = self.bot.get_user(record["user_id"])
            if user:
                users.append(
                    f"{user.mention} stopped {format_dt(record['ended_at'], 'R')}"
                )
                
        if not users:
            return await ctx.deny("No boosters have been lost!")

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title="Boosters Lost",
                    description="\n".join(users[i:i+15]),
                    color=COLORS.neutral
                )
                for i in range(0, len(users), 15)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_command()
    async def bots(self, ctx: Context) -> Message:
        """
        View all bots in the server.
        """
        members = list(
            filter(
                lambda member: member.bot,
                ctx.guild.members,
            )
        )
        if not members:
            return await ctx.deny(f"**{ctx.guild}** doesn't have any bots!")

        entries = [
            f"{member.mention} (`{member.id}`)"
            for member in sorted(
                members,
                key=lambda member: member.joined_at or utcnow(),
                reverse=True,
            )
        ]

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"Bots in {ctx.guild}",
                    description="\n".join(entries[i:i+15]),
                    color=COLORS.neutral
                )
                for i in range(0, len(entries), 15)
            ]
        )
        return await paginator.start(ctx)

    @command(aliases=["bans"])
    @has_permissions(ban_members=True)
    async def banlist(self, ctx: Context) -> Message:
        """
        View all banned members.
        """
        bans = []
        async for ban in ctx.guild.bans():
            bans.append(ban)

        if not bans:
            return await ctx.deny("No members are currently banned!")

        entries = [
            f"{ban.user.mention} (`{ban.user.id}`) - {ban.reason or 'No reason'}"
            for ban in bans
        ]

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"Banned Members [{len(bans)}]",
                    description="\n".join(entries[i:i+15]),
                    color=COLORS.neutral
                )
                for i in range(0, len(entries), 15)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_command(aliases=["gi"])
    @has_permissions(manage_guild=True)
    async def guildinvites(self, ctx: Context) -> Message:
        """
        View all server invites.
        """
        invites = await ctx.guild.invites()
        if not invites:
            return await ctx.deny("No invites are currently present!")

        entries = [
            f"[{invite.code}]({invite.url}) by {invite.inviter.mention if invite.inviter else '**Unknown**'} expires {format_dt(invite.expires_at, 'R') if invite.expires_at else '**Never**'}"
            for invite in sorted(
                invites,
                key=lambda invite: invite.created_at or utcnow(),
                reverse=True,
            )
        ]

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"Invites in {ctx.guild}",
                    description="\n".join(entries[i:i+15]),
                    color=COLORS.neutral
                )
                for i in range(0, len(entries), 15)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_command(aliases=["emotes"])
    async def emojis(self, ctx: Context) -> Message:
        """
        View all server emojis.
        """
        emojis = ctx.guild.emojis
        if not emojis:
            return await ctx.deny(f"**{ctx.guild}** doesn't have any emojis!")

        entries = [f"{emoji} ([`{emoji.id}`]({emoji.url}))" for emoji in emojis]

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"Emojis in {ctx.guild}",
                    description="\n".join(entries[i:i:20]),
                    color=COLORS.neutral
                )
                for i in range(0, len(entries), 20)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_command()
    async def stickers(self, ctx: Context) -> Message:
        """
        View all server stickers.
        """
        stickers = ctx.guild.stickers
        if not stickers:
            return await ctx.deny(f"**{ctx.guild}** doesn't have any stickers!")

        entries = [
            f"[{sticker.name}]({sticker.url}) (`{sticker.id}`)"
            for sticker in stickers
        ]

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"Stickers in {ctx.guild}",
                    description="\n".join(entries[i:i:15]),
                    color=COLORS.neutral
                )
                for i in range(0, len(entries), 15)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_command(aliases=["firstmsg"])
    async def firstmessage(self, ctx: Context) -> Message:
        """
        View the first message sent.
        """
        try:
            messages = [message async for message in ctx.channel.history(limit=1, oldest_first=True)]
            if messages:
                message = messages[0]
                return await ctx.send(
                    embed=Embed(
                        description=f"Jump to the [`first message`]({message.jump_url}) sent by **{message.author}**",
                        color=COLORS.neutral
                    )
                )
            return await ctx.deny("No messages found in this channel!")
        except:
            return await ctx.deny("Could not fetch the first message!")

    @hybrid_command(aliases=["pos"], example=",position @user")
    async def position(self, ctx: Context, *, member: Member = None):
        """
        Check member join position.
        """
        if member is None:
            member = ctx.author

        pos = (
            sum(
                1
                for m in ctx.guild.members
                if m.joined_at is not None and m.joined_at < member.joined_at
            )
            + 1
        )

        embed = Embed(
            description=f"{member.mention} is member number ``{pos}``.",
            color=COLORS.neutral
        )
        await ctx.send(embed=embed)

    @command(example=",channelinfo #general", aliases=["chinfo", "cinfo", "ci"])
    async def channelinfo(
        self,
        ctx: Context,
        channel: Optional[Union[TextChannel, VoiceChannel, CategoryChannel]] = None,
    ):
        """
        View information about a channel.
        """
        if channel is None:
            channel = ctx.channel

        embed = Embed(title=f"{channel.name}", color=ctx.author.top_role.color or COLORS.neutral)
        embed.set_author(
            name=f"{ctx.author.display_name}",
            icon_url=f"{ctx.author.display_avatar.url}",
        )

        embed.add_field(name="Channel ID", value=f"``{channel.id}``", inline=False)

        embed.add_field(
            name="Type", value=f"``{str(channel.type).lower()}``", inline=False
        )

        if isinstance(channel, (TextChannel, VoiceChannel)):
            category = channel.category

            if category:
                embed.add_field(
                    name="Category",
                    value=f"``{category.name}`` (``{category.id}``)",
                    inline=False,
                )
            else:
                embed.add_field(name="Category", value="No category", inline=False)

        if isinstance(channel, TextChannel):
            embed.add_field(
                name="Topic",
                value=(
                    f"{channel.topic}" if channel.topic else "No topic on this channel"
                ),
                inline=False,
            )

        elif isinstance(channel, CategoryChannel):
            child_channels = [child.name for child in channel.channels]
            if child_channels:
                embed.add_field(
                    name=f"{len(child_channels)} Children",
                    value=", ".join(child_channels),
                    inline=False,
                )

        embed.add_field(
            name="Created On",
            value=f"{format_dt(channel.created_at)} ({format_dt(channel.created_at, 'R')})",
            inline=False,
        )

        await ctx.send(embed=embed)

    @command(example=",roleinfo @role", aliases=["rinfo"])
    async def roleinfo(self, ctx: Context, role: Optional[Role] = None):
        """
        View information about a role.
        """
        if role is None:
            role = ctx.author.top_role

        embed = Embed(title=f"{role.name}", color=role.color or COLORS.neutral)
        embed.set_author(
            name=f"{ctx.author.display_name}",
            icon_url=f"{ctx.author.display_avatar.url}",
        )
        embed.add_field(name="Role ID", value=f"``{role.id}``", inline=False)
        embed.add_field(name="Color", value=f"``{role.color}``", inline=False)

        specific_permissions = [
            "administrator",
            "ban_members",
            "kick_members",
            "manage_guild",
            "manage_channels",
            "manage_roles",
            "manage_messages",
            "view_audit_log",
            "manage_webhooks",
            "manage_expressions",
            "mute_members",
            "deafen_members",
            "move_members",
            "manage_nicknames",
            "mention_everyone",
            "view_guild_insights",
            "moderate_members",
        ]

        granted_permissions = [
            perm for perm in specific_permissions if getattr(role.permissions, perm)
        ]

        if granted_permissions:
            embed.add_field(
                name="Permissions",
                value=", ".join(granted_permissions) if len(granted_permissions) > 1 else granted_permissions[0],
                inline=False,
            )
        else:
            embed.add_field(
                name="Permissions",
                value="No dangerous permissions granted.",
                inline=False,
            )

        members_with_role = role.members
        member_names = [member.name for member in members_with_role][:5]

        if member_names:
            embed.add_field(
                name=f"{len(role.members)} Member(s)",
                value=", ".join(member_names) if len(member_names) > 1 else member_names[0],
                inline=False,
            )
        else:
            embed.add_field(
                name="Members with this Role",
                value="No members in this role.",
                inline=False,
            )

        if role.icon:
            embed.set_thumbnail(url=role.icon.url)

        if granted_permissions:
            embed.set_footer(
                text="Dangerous Permissions!",
                icon_url="https://cdn.discordapp.com/emojis/1308023743565529138.webp?size=64",
            )

        await ctx.send(embed=embed)

    @command(example=",gnames 1203514684326805524")
    async def gnames(self, ctx: Context, guild: Optional[Guild] = None):
        """
        View a guild's name history.
        """
        if not guild:
            guild = ctx.guild

        names = await self.bot.pool.fetch(
            """
            SELECT name, changed_at
            FROM gnames
            WHERE guild_id = $1
            ORDER BY changed_at DESC
            """,
            guild.id,
        )
        
        if not names:
            return await ctx.deny(f"**{guild}** doesn't have any name history!")

        entries = [
            f"**{record['name']}** ({format_dt(record['changed_at'], 'R')})"
            for record in names
        ]

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title=f"{guild.name} Name History",
                    description="\n".join(entries[i:i:15]),
                    color=COLORS.neutral
                )
                for i in range(0, len(entries), 15)
            ]
        )
        return await paginator.start(ctx)

    @command()
    @has_permissions(manage_guild=True)
    async def cleargnames(self, ctx: Context):
        """
        Clear the guild name history.
        """
        await self.bot.pool.execute(
            """
            DELETE FROM gnames 
            WHERE guild_id = $1
            """, 
            ctx.guild.id
        )
        await ctx.approve("Cleared the guild name history!")
