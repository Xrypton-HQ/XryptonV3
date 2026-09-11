from __future__ import annotations

import json
import random
from contextlib import suppress
from datetime import datetime, timedelta
from typing import List, Optional, Annotated, cast

import discord
from discord import Embed, HTTPException, Member, Message, TextChannel, Role, User
from discord.ext import commands, tasks
from discord.ext.commands import (
    group,
    has_permissions,
    parameter,
    flag,
    Range,
    Greedy,
    MessageConverter,
    CommandError,
)
from discord.utils import format_dt, get, utcnow

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.managers.types import CogMeta
from base.managers.predicates import has_permissions, example
from base.config import CLIENT, EMOJIS, COLORS


class DurationConverter:
    """Convert strings like '1w', '2d', '3h', '4m' to timedelta."""

    @staticmethod
    async def convert(ctx: Context, argument: str) -> timedelta:
        units = {
            "w": timedelta(weeks=1),
            "d": timedelta(days=1),
            "h": timedelta(hours=1),
            "m": timedelta(minutes=1),
            "s": timedelta(seconds=1),
        }
        try:
            value = int(argument[:-1])
            unit = argument[-1].lower()
            if unit not in units:
                raise ValueError
            return units[unit] * value
        except (ValueError, IndexError):
            raise CommandError("Invalid duration format. Use e.g. `1w`, `2d`, `3h`, `4m`, `5s`.")


class GiveawayFlags:
    """Flag container for giveaway start command."""

    required: Optional[Role] = flag(default=None, description="Role required to enter the giveaway")
    bonus: List[str] = flag(
        default=lambda _: [],
        description="Roles that get bonus entries (format: Role:entries, e.g. @role:2)"
    )
    winners: Range[int, 1, 25] = flag(description="Number of winners")
    prize: str = flag(description="The prize for the giveaway")


class GiveawayEntry:
    """Represents a giveaway entry stored in the database."""

    def __init__(
        self,
        bot: Bot,
        guild_id: int,
        user_id: int,
        channel_id: int,
        prize: str,
        emoji: str,
        winners: int,
        ends_at: datetime,
        message_id: int = 0,
        ended: bool = False,
        created_at: Optional[datetime] = None,
        required_roles: Optional[List[int]] = None,
        bonus_roles: Optional[dict[int, int]] = None,
    ):
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.prize = prize
        self.emoji = emoji
        self.winners = winners
        self.ends_at = ends_at
        self.ended = ended
        self.created_at = created_at or utcnow()
        self.required_roles = required_roles or []
        self.bonus_roles = bonus_roles or {}

    def __str__(self) -> str:
        return f"{f'{self.winners}x ' if self.winners > 1 else ''}{self.prize}"

    def __repr__(self) -> str:
        return f"<GiveawayEntry guild_id={self.guild_id} channel_id={self.channel_id} message_id={self.message_id} prize={self.prize!r} winners={self.winners} ends_at={self.ends_at!r}>"

    def __eq__(self, other) -> bool:
        if not isinstance(other, GiveawayEntry):
            return NotImplemented
        return self.message_id == other.message_id

    @property
    def is_ended(self) -> bool:
        return self.ends_at <= utcnow() or self.ended

    @property
    def channel(self) -> Optional[TextChannel]:
        return self.bot.get_channel(self.channel_id)  # type: ignore

    @property
    def user(self) -> Optional[User]:
        return self.bot.get_user(self.user_id)

    @property
    def message_url(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"

    def embed(self, winners: List[Member] = None) -> Embed:
        winners = winners or []
        embed = Embed(title=str(self), color=COLORS.neutral)
        if not self.is_ended:
            embed.description = (
                f"React with {self.emoji} to enter!\n"
                f"> Ends {format_dt(self.ends_at, 'R')}"
            )
        else:
            if not winners:
                embed.description = "No winner was drawn! 🥺"
            else:
                embed.description = (
                    f"Congratulations! 🎉\n"
                    f"> {', '.join(winner.mention for winner in winners)}!"
                )

        if self.user:
            embed.set_footer(text=f"Started by {self.user}")

        if self.required_roles:
            roles = []
            for role_id in self.required_roles:
                role = self.bot.get_guild(self.guild_id).get_role(role_id) if self.bot.get_guild(self.guild_id) else None
                if role:
                    roles.append(role.mention)
            if roles:
                embed.add_field(name="Required Roles", value=", ".join(roles), inline=False)

        if self.bonus_roles:
            bonus_lines = []
            for role_id, bonus in self.bonus_roles.items():
                role = self.bot.get_guild(self.guild_id).get_role(role_id) if self.bot.get_guild(self.guild_id) else None
                if role:
                    bonus_lines.append(f"{role.mention}: +{bonus} entries")
            if bonus_lines:
                embed.add_field(name="Bonus Entries", value="\n".join(bonus_lines), inline=False)

        return embed

    async def message(self) -> Optional[Message]:
        if (channel := self.channel) is not None:
            with suppress(HTTPException):
                return await channel.fetch_message(self.message_id)
        return None

    async def can_enter(self, member: Member) -> bool:
        """Check if a member can enter the giveaway."""
        if not self.required_roles:
            return True
        return any(role.id in self.required_roles for role in member.roles)

    async def get_entry_count(self, member: Member) -> int:
        """Get total number of entries for a member (base + bonus)."""
        entries = 1  # base entry

        # Bonus from this giveaway
        for role_id, bonus in self.bonus_roles.items():
            if get(member.roles, id=role_id):
                entries += bonus

        # Global bonus from settings
        settings = await self.bot.pool.fetchval(
            "SELECT bonus_roles FROM giveaway_settings WHERE guild_id = $1",
            member.guild.id,
        )
        if settings:
            global_bonus = json.loads(settings)
            for role_id, bonus in global_bonus.items():
                if get(member.roles, id=int(role_id)):
                    entries += int(bonus)

        return entries

    async def entrants(self, message: Message) -> List[tuple[Member, int]]:
        """Return a list of (Member, entry_count) for all eligible entrants."""
        reaction = get(message.reactions, emoji=self.emoji)
        if not reaction:
            return []

        entrants = []
        async for user in reaction.users():
            if user.bot:
                continue
            member = message.guild.get_member(user.id)
            if not member:
                continue
            if not await self.can_enter(member):
                continue
            count = await self.get_entry_count(member)
            entrants.append((member, count))

        return entrants

    async def draw_winners(self, message: Message) -> List[Member]:
        """Draw winners from the entrants pool with weighted entries."""
        entrants = await self.entrants(message)
        if not entrants:
            return []

        pool = []
        for member, count in entrants:
            pool.extend([member] * count)

        with suppress(ValueError):
            return random.sample(pool, min(self.winners, len(pool)))

        return []

    async def end(self) -> None:
        """Mark the giveaway as ended in the database."""
        self.ended = True
        await self.bot.pool.execute(
            """
            UPDATE giveaway
            SET ended = TRUE
            WHERE guild_id = $1 AND channel_id = $2 AND message_id = $3
            """,
            self.guild_id,
            self.channel_id,
            self.message_id,
        )

    async def save(self, message: Message) -> None:
        """Save the giveaway entry to the database."""
        await self.bot.pool.execute(
            """
            INSERT INTO giveaway (
                guild_id, user_id, channel_id, message_id,
                prize, emoji, winners, ends_at, created_at,
                required_roles, bonus_roles
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            self.guild_id,
            self.user_id,
            self.channel_id,
            message.id,
            self.prize,
            self.emoji,
            self.winners,
            self.ends_at,
            self.created_at,
            json.dumps(self.required_roles),
            json.dumps(self.bonus_roles),
        )

    @classmethod
    def from_record(cls, bot: Bot, record) -> "GiveawayEntry":
        """Create a GiveawayEntry from a database record."""
        return cls(
            bot=bot,
            guild_id=record["guild_id"],
            user_id=record["user_id"],
            channel_id=record["channel_id"],
            message_id=record["message_id"],
            prize=record["prize"],
            emoji=record["emoji"],
            winners=record["winners"],
            ended=record["ended"],
            ends_at=record["ends_at"],
            created_at=record["created_at"],
            required_roles=json.loads(record["required_roles"]) if record["required_roles"] else [],
            bonus_roles=json.loads(record["bonus_roles"]) if record["bonus_roles"] else {},
        )

    @classmethod
    async def fetch(cls, ctx: Context, message: Message) -> Optional["GiveawayEntry"]:
        """Fetch a giveaway entry by its message."""
        record = await ctx.bot.pool.fetchrow(
            """
            SELECT *
            FROM giveaway
            WHERE guild_id = $1 AND channel_id = $2 AND message_id = $3
            """,
            ctx.guild.id,
            message.channel.id,
            message.id,
        )
        if record:
            return cls.from_record(ctx.bot, record)
        return None

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> "GiveawayEntry":
        """Converter for giveaway entries."""
        message = await MessageConverter().convert(ctx, argument)
        if message.guild != ctx.guild:
            raise CommandError("The message must be in this server!")

        entry = await cls.fetch(ctx, message)
        if not entry:
            raise CommandError(f"No giveaway exists for that [`message`]({message.jump_url})!")
        return entry

    @classmethod
    async def fallback(cls, ctx: Context) -> "GiveawayEntry":
        """Fallback converter when no argument is given."""
        if not ctx.replied_message:
            record = await ctx.bot.pool.fetchrow(
                """
                SELECT *
                FROM giveaway
                WHERE guild_id = $1
                ORDER BY created_at DESC
                """,
                ctx.guild.id,
            )
            if record:
                return cls.from_record(ctx.bot, record)
            raise CommandError("No giveaways exist for this server!")

        with suppress(CommandError):
            return await cls.convert(ctx, ctx.replied_message.jump_url)

        raise CommandError("No giveaways exist for this server!")


class Giveaway(CogMeta):
    """Various giveaway utilities."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.check_giveaways.start()

    async def cog_load(self) -> None:
        """Create tables and start the loop."""
        await self._ensure_tables()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        """Cancel the loop."""
        self.check_giveaways.cancel()
        return await super().cog_unload()

    async def _ensure_tables(self):
        """Create giveaway tables if they don't exist."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS giveaway (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                emoji TEXT NOT NULL,
                winners INTEGER NOT NULL,
                ends_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended BOOLEAN NOT NULL DEFAULT 0,
                required_roles TEXT DEFAULT '[]',
                bonus_roles TEXT DEFAULT '{}',
                PRIMARY KEY (guild_id, channel_id, message_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS giveaway_settings (
                guild_id INTEGER PRIMARY KEY,
                bonus_roles TEXT DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_giveaway_ends ON giveaway(ends_at)",
        ]
        for query in queries:
            await self.bot.pool.execute(query)

    @tasks.loop(seconds=15)
    async def check_giveaways(self):
        """Check for ended giveaways and draw winners."""
        records = await self.bot.pool.fetch(
            """
            SELECT *
            FROM giveaway
            WHERE ends_at <= CURRENT_TIMESTAMP AND ended = FALSE
            """
        )
        for record in records:
            giveaway = GiveawayEntry.from_record(self.bot, record)
            if not giveaway.channel:
                await self.bot.pool.execute(
                    """
                    DELETE FROM giveaway
                    WHERE guild_id = $1 AND channel_id = $2 AND message_id = $3
                    """,
                    giveaway.guild_id,
                    giveaway.channel_id,
                    giveaway.message_id,
                )
                continue

            message = await giveaway.message()
            if not message or not message.reactions:
                await self.bot.pool.execute(
                    """
                    DELETE FROM giveaway
                    WHERE guild_id = $1 AND channel_id = $2 AND message_id = $3
                    """,
                    giveaway.guild_id,
                    giveaway.channel_id,
                    giveaway.message_id,
                )
                continue

            await self.draw_giveaway(giveaway, message)

    async def draw_giveaway(self, giveaway: GiveawayEntry, message: Message):
        """Draw winners and end the giveaway."""
        await giveaway.end()
        winners = await giveaway.draw_winners(message)
        await message.edit(
            content="🎉 **GIVEAWAY ENDED** 🎉",
            embed=giveaway.embed(winners),
        )
        if winners:
            await message.reply(
                content=f"Congratulations {' '.join(w.mention for w in winners)}! You won **{giveaway.prize}**!"
            )

    @group(
        name="giveaway",
        aliases=["gw"],
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    @example(",giveaway")
    async def giveaway(self, ctx: Context) -> Message:
        """Manage giveaways."""
        return await ctx.send_help(ctx.command)

    @giveaway.command(
        name="start",
        aliases=["create"],
    )
    @has_permissions(manage_messages=True)
    @example(
        ",giveaway start #giveaways 1w winners:2 prize:'Nitro Classic' required:@booster bonus:@vip:2"
    )
    async def giveaway_start(
        self,
        ctx: Context,
        channel: Optional[TextChannel],
        duration: Annotated[timedelta, DurationConverter.convert],
        *,
        flags: GiveawayFlags,  # type: ignore
    ) -> Optional[Message]:
        """
        Start a giveaway.

        Duration must be between 5 minutes and 4 weeks.
        Use flags:
        - winners: number of winners (1-25)
        - prize: the prize description
        - required: role required to enter
        - bonus: role:entries for bonus entries (can be used multiple times)
        """
        channel = channel or ctx.channel
        if not isinstance(channel, TextChannel):
            return await ctx.deny("You can only start giveaways in text channels!")

        if duration < timedelta(minutes=5) or duration > timedelta(weeks=4):
            return await ctx.deny("Duration must be between 5 minutes and 4 weeks.")

        bonus_roles = {}
        for bonus in flags.bonus:
            try:
                parts = bonus.split(":")
                if len(parts) != 2:
                    raise ValueError
                role_id = int(parts[0].strip("<@&>"))
                entries = int(parts[1])
                if entries < 1:
                    raise ValueError
                bonus_roles[role_id] = entries
            except (ValueError, IndexError):
                return await ctx.deny(f"Invalid bonus format: `{bonus}`. Use `role:entries` e.g. `@vip:2`.")

        if flags.winners < 1 or flags.winners > 25:
            return await ctx.deny("Winners must be between 1 and 25.")

        giveaway = GiveawayEntry(
            bot=self.bot,
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            channel_id=channel.id,
            prize=flags.prize,
            emoji="🎉",
            winners=flags.winners,
            ends_at=ctx.message.created_at + duration,
            required_roles=[flags.required.id] if flags.required else [],
            bonus_roles=bonus_roles,
        )

        embed = giveaway.embed()
        message = await channel.send(
            content="🎉 **GIVEAWAY** 🎉",
            embed=embed,
        )

        async def check_reaction(payload):
            if (
                payload.message_id == message.id
                and payload.emoji.name == "🎉"
                and payload.user_id != self.bot.user.id
            ):
                member = channel.guild.get_member(payload.user_id)
                if flags.required and flags.required not in member.roles:
                    await message.remove_reaction(payload.emoji, member)

        self.bot.add_listener(check_reaction, name="on_raw_reaction_add")
        await message.add_reaction("🎉")
        await giveaway.save(message)

        if channel == ctx.channel:
            return await ctx.check()
        return await ctx.approve(
            f"Giveaway started in {channel.mention} for [**{giveaway.prize}**]({message.jump_url})"
        )

    @giveaway.command(
        name="end",
        aliases=["stop"],
    )
    @has_permissions(manage_messages=True)
    @example(",giveaway end 1234567890")
    async def giveaway_end(
        self,
        ctx: Context,
        giveaway: GiveawayEntry = parameter(
            default=GiveawayEntry.fallback,  # type: ignore
        ),
    ) -> Optional[Message]:
        """End a giveaway early."""
        if giveaway.is_ended:
            return await ctx.deny("That giveaway has already ended!")

        message = await giveaway.message()
        if not message:
            return await ctx.deny("That giveaway no longer exists!")

        await self.draw_giveaway(giveaway, message)
        if message.channel == ctx.channel:
            return await ctx.check()
        return await ctx.approve(
            f"Giveaway ended for [**{giveaway.prize}**]({message.jump_url})"
        )

    @giveaway.command(
        name="reroll",
        aliases=["redraw"],
    )
    @has_permissions(manage_messages=True)
    @example(",giveaway reroll 1234567890")
    async def giveaway_reroll(
        self,
        ctx: Context,
        giveaway: GiveawayEntry = parameter(
            default=GiveawayEntry.fallback,  # type: ignore
        ),
    ) -> Optional[Message]:
        """Reroll a giveaway."""
        if not giveaway.is_ended:
            return await ctx.deny("That giveaway hasn't ended yet!")

        message = await giveaway.message()
        if not message:
            return await ctx.deny("That giveaway no longer exists!")

        await self.draw_giveaway(giveaway, message)
        if message.channel == ctx.channel:
            return await ctx.check()
        return await ctx.approve(
            f"Giveaway rerolled for [**{giveaway.prize}**]({message.jump_url})"
        )

    @giveaway.command(
        name="entrants",
        aliases=["entries"],
    )
    @has_permissions(manage_messages=True)
    @example(",giveaway entrants 1234567890")
    async def giveaway_entrants(
        self,
        ctx: Context,
        giveaway: GiveawayEntry = parameter(
            default=GiveawayEntry.fallback,  # type: ignore
        ),
    ) -> Optional[Message]:
        """View all entrants of a giveaway."""
        message = await giveaway.message()
        if not message:
            return await ctx.deny("That giveaway no longer exists!")

        entrants = await giveaway.entrants(message)
        if not entrants:
            return await ctx.deny("No one has entered that giveaway!")

        entries = [f"**{member}** (`{member.id}`) - {count} entries" for member, count in entrants]
        pages = []
        for i in range(0, len(entries), 15):
            embed = Embed(
                title="Giveaway Entrants",
                description="\n".join(entries[i:i+15]),
                color=COLORS.neutral,
            )
            pages.append(embed)

        paginator = Paginator(ctx, pages=pages)
        return await paginator.start(ctx)

    @giveaway.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_messages=True)
    @example(",giveaway list")
    async def giveaway_list(self, ctx: Context) -> Message:
        """View all active giveaways in this server."""
        records = await self.bot.pool.fetch(
            """
            SELECT *
            FROM giveaway
            WHERE guild_id = $1
            ORDER BY created_at DESC
            """,
            ctx.guild.id,
        )
        if not records:
            return await ctx.deny("No giveaways exist for this server!")

        entries = []
        for record in records:
            gw = GiveawayEntry.from_record(self.bot, record)
            status = " [ENDED]" if gw.is_ended else ""
            entries.append(
                f"**{gw.prize}** - [`{gw.message_id}`]({gw.message_url}){status}"
            )

        pages = []
        for i in range(0, len(entries), 15):
            embed = Embed(
                title="Giveaways",
                description="\n".join(entries[i:i+15]),
                color=COLORS.neutral,
            )
            pages.append(embed)

        paginator = Paginator(ctx, pages=pages)
        return await paginator.start(ctx)

    @giveaway.command(
        name="settings",
        aliases=["config"],
    )
    @has_permissions(manage_messages=True)
    @example(",giveaway settings")
    async def giveaway_settings(self, ctx: Context) -> Message:
        """View server-wide giveaway bonus settings."""
        settings = await self.bot.pool.fetchval(
            "SELECT bonus_roles FROM giveaway_settings WHERE guild_id = $1",
            ctx.guild.id,
        )
        bonus_roles = json.loads(settings) if settings else {}

        embed = Embed(title="Giveaway Settings", color=COLORS.neutral)
        desc = ["**Global Bonus Entries:**"]
        for role_id, bonus in bonus_roles.items():
            role = ctx.guild.get_role(int(role_id))
            if role:
                desc.append(f"{role.mention}: +{bonus} entries")
        embed.description = "\n".join(desc) if len(desc) > 1 else "No bonus roles configured."

        return await ctx.send(embed=embed)

    @giveaway.command(
        name="addrole",
    )
    @has_permissions(manage_messages=True)
    @example(",giveaway addrole @booster 2")
    async def giveaway_addrole(
        self,
        ctx: Context,
        role: Role,
        bonus: int = 1,
    ) -> Message:
        """
        Add a global bonus role for all giveaways.
        Bonus entries are added on top of the base entry.
        """
        await self.bot.pool.execute(
            """
            INSERT INTO giveaway_settings (guild_id, bonus_roles)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE
            SET bonus_roles = json_patch(giveaway_settings.bonus_roles, $2)
            """,
            ctx.guild.id,
            json.dumps({str(role.id): bonus}),
        )
        return await ctx.approve(f"Added {role.mention} with +{bonus} bonus entries.")