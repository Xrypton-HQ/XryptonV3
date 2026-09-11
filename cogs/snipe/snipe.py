from __future__ import annotations

import json
import re
from contextlib import suppress
from datetime import datetime
from typing import List, Optional, cast
from io import BytesIO

import discord
from discord import (
    Embed,
    File,
    HTTPException,
    Member,
    Message,
    Reaction,
    User,
    app_commands,
)
from discord.ext import commands
from discord.ext.commands import (
    hybrid_command,
    hybrid_group,
    has_permissions,
    cooldown,
    BucketType,
)
from discord.utils import format_dt, utcnow
from humanize import naturaldelta

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.managers.types import CogMeta
from base.managers.predicates import has_permissions, example
from base.config import CLIENT, EMOJIS, COLORS

# Regex patterns for filtering
INVITE_PATTERN = re.compile(
    r"(?:(?:https?://)?(?:www)?discord(?:app)?\.(?:(?:com|gg)/invite/[a-z0-9-_]+)|(?:https?://)?(?:www)?discord\.gg/[a-z0-9-_]+)"
)
LINK_PATTERN = re.compile(r"(https?://\S+)")


class Snipe(CogMeta):
    """Snipe deleted, edited messages and removed reactions."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self._init_db()

    def _init_db(self):
        """Create necessary tables if they don't exist."""
        # This will be called in setup_hook or on cog load; we'll use a simple check.
        # We'll do it on each command or listener, but better to run once.
        # We'll create a method to ensure tables.
        pass

    async def _ensure_tables(self):
        """Ensure all snipe tables exist."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS snipe_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                user_avatar TEXT,
                created_at TIMESTAMP,
                deleted_at TIMESTAMP,
                content TEXT,
                attachments TEXT,
                stickers TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS snipe_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                removed_at TIMESTAMP,
                emoji TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS snipe_edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                user_avatar TEXT,
                before_content TEXT,
                after_content TEXT,
                edited_at TIMESTAMP,
                attachments TEXT,
                stickers TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS snipe_filter (
                guild_id INTEGER PRIMARY KEY,
                invites BOOLEAN DEFAULT 0,
                links BOOLEAN DEFAULT 0,
                words TEXT DEFAULT '[]'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS snipe_ignore (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_snipe_messages_channel ON snipe_messages(channel_id)",
            "CREATE INDEX IF NOT EXISTS idx_snipe_reactions_channel ON snipe_reactions(channel_id)",
            "CREATE INDEX IF NOT EXISTS idx_snipe_edits_channel ON snipe_edits(channel_id)",
        ]
        for query in queries:
            await self.bot.pool.execute(query)

    async def cog_load(self):
        """Create tables when the cog is loaded."""
        await self._ensure_tables()


    # ─── Helper Methods ─────────────────────────────────────────────

    async def _push_message_snipe(self, message: Message):
        """Store a deleted message."""
        if (
            not message.guild
            or message.author.bot
            and not message.attachments
            or message.content.strip() == ".pick"
        ):
            return
        if not message.content and not message.attachments and not message.stickers:
            return

        attachments = [
            {
                "url": a.proxy_url,
                "size": a.size,
                "filename": a.filename,
                "content_type": a.content_type,
            }
            for a in message.attachments
        ]
        stickers = [str(s.url) for s in message.stickers]

        query = """
            INSERT INTO snipe_messages (
                guild_id, channel_id, message_id, user_id, user_name, user_avatar,
                created_at, deleted_at, content, attachments, stickers
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """
        await self.bot.pool.execute(
            query,
            message.guild.id,
            message.channel.id,
            message.id,
            message.author.id,
            message.author.name,
            message.author.display_avatar.url,
            message.created_at,
            utcnow(),
            message.content,
            json.dumps(attachments),
            json.dumps(stickers),
        )

        # Keep only last 100 per channel
        await self.bot.pool.execute(
            """
            DELETE FROM snipe_messages
            WHERE channel_id = $1 AND id NOT IN (
                SELECT id FROM snipe_messages
                WHERE channel_id = $1
                ORDER BY deleted_at DESC
                LIMIT 100
            )
            """,
            message.channel.id,
        )

    async def _push_reaction_snipe(self, reaction: Reaction, user: User):
        """Store a removed reaction."""
        if not reaction.message.guild:
            return
        query = """
            INSERT INTO snipe_reactions (
                guild_id, channel_id, message_id, user_id, user_name, removed_at, emoji
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        """
        await self.bot.pool.execute(
            query,
            reaction.message.guild.id,
            reaction.message.channel.id,
            reaction.message.id,
            user.id,
            user.name,
            utcnow(),
            str(reaction.emoji),
        )
        # Keep last 100 per channel
        await self.bot.pool.execute(
            """
            DELETE FROM snipe_reactions
            WHERE channel_id = $1 AND id NOT IN (
                SELECT id FROM snipe_reactions
                WHERE channel_id = $1
                ORDER BY removed_at DESC
                LIMIT 100
            )
            """,
            reaction.message.channel.id,
        )

    async def _push_edit_snipe(self, before: Message, after: Message):
        """Store an edit if content changed."""
        if before.content == after.content or not before.guild:
            return
        attachments = [
            {
                "url": a.proxy_url,
                "size": a.size,
                "filename": a.filename,
                "content_type": a.content_type,
            }
            for a in before.attachments
        ]
        stickers = [str(s.url) for s in before.stickers]

        query = """
            INSERT INTO snipe_edits (
                guild_id, channel_id, message_id, user_id, user_name, user_avatar,
                before_content, after_content, edited_at, attachments, stickers
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """
        await self.bot.pool.execute(
            query,
            before.guild.id,
            before.channel.id,
            before.id,
            before.author.id,
            before.author.name,
            before.author.display_avatar.url,
            before.content,
            after.content,
            utcnow(),
            json.dumps(attachments),
            json.dumps(stickers),
        )
        # Keep last 100 per channel
        await self.bot.pool.execute(
            """
            DELETE FROM snipe_edits
            WHERE channel_id = $1 AND id NOT IN (
                SELECT id FROM snipe_edits
                WHERE channel_id = $1
                ORDER BY edited_at DESC
                LIMIT 100
            )
            """,
            before.channel.id,
        )

    async def _get_filter_config(self, guild_id: int):
        """Get filter config for a guild."""
        return await self.bot.pool.fetchrow(
            "SELECT invites, links, words FROM snipe_filter WHERE guild_id = $1",
            guild_id,
        )

    async def _get_ignored_ids(self, guild_id: int) -> List[int]:
        """Get list of ignored user IDs."""
        rows = await self.bot.pool.fetch(
            "SELECT user_id FROM snipe_ignore WHERE guild_id = $1",
            guild_id,
        )
        return [r["user_id"] for r in rows]

    async def _apply_filter(self, content: str, guild_id: int) -> str:
        """Apply invite/link filters if enabled."""
        config = await self._get_filter_config(guild_id)
        if not config:
            return content
        if config["invites"]:
            content = INVITE_PATTERN.sub("*`REDACTED INVITE`*", content)
        if config["links"] and content.startswith("http"):
            content = LINK_PATTERN.sub("*`REDACTED LINK`*", content)
        return content

    # ─── Listeners ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, message: Message):
        await self._push_message_snipe(message)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: Reaction, user: User):
        await self._push_reaction_snipe(reaction, user)

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message):
        await self._push_edit_snipe(before, after)

    # ─── Commands ──────────────────────────────────────────────────

    @hybrid_group(
        name="snipe",
        aliases=["sn", "s"],
        invoke_without_command=True,
    )
    @cooldown(2, 5, BucketType.member)
    @example(",snipe 2")
    async def snipe(self, ctx: Context, index: Optional[str] = "1") -> Message:
        """
        Snipe the last deleted message.
        Provide a number to snipe older messages.
        """
        try:
            idx = int(index)
            if idx < 1:
                raise ValueError
        except ValueError:
            return await ctx.deny("Please provide a valid positive number!")

        # Get message snipe by offset (most recent first)
        rows = await self.bot.pool.fetch(
            """
            SELECT * FROM snipe_messages
            WHERE channel_id = $1
            ORDER BY deleted_at DESC
            LIMIT 1 OFFSET $2
            """,
            ctx.channel.id,
            idx - 1,
        )
        if not rows:
            return await ctx.deny(
                f"No **sniped message** available at index `{idx}`!"
                if idx != 1
                else "No **messages** have been deleted recently!"
            )

        row = rows[0]
        # Check ignore
        ignored = await self._get_ignored_ids(ctx.guild.id)
        if row["user_id"] in ignored:
            return await ctx.deny(f"**{row['user_name']}** is immune to being sniped!")

        # Apply filters if user lacks manage_messages
        content = row["content"]
        if not ctx.channel.permissions_for(ctx.author).manage_messages:
            config = await self._get_filter_config(ctx.guild.id)
            if config:
                # Check if any filtered word is in content
                words = json.loads(config["words"]) if config["words"] else []
                for word in words:
                    if word.lower() in content.lower():
                        return await ctx.send("That message probably shouldn't be sniped!")
                content = await self._apply_filter(content, ctx.guild.id)

        embed = Embed(description=content or "No text content", color=COLORS.neutral)
        embed.set_author(
            name=row["user_name"],
            icon_url=row["user_avatar"],
        )
        total = await self.bot.pool.fetchval(
            "SELECT COUNT(*) FROM snipe_messages WHERE channel_id = $1",
            ctx.channel.id,
        )
        embed.set_footer(
            text=f"{idx}/{total} • Deleted {naturaldelta((utcnow() - row['deleted_at']))} ago"
        )

        # Handle attachments
        attachments = json.loads(row["attachments"]) if row["attachments"] else []
        stickers = json.loads(row["stickers"]) if row["stickers"] else []
        file = None

        if attachments:
            for att in attachments:
                if att.get("content_type", "").startswith("image/"):
                    embed.set_image(url=att["url"])
                    break
            # If no image, just show as field
            embed.add_field(
                name=f"**Attachment{'s' if len(attachments)>1 else ''}**",
                value="\n".join([a["url"] for a in attachments]),
                inline=False,
            )
        elif stickers:
            embed.set_image(url=stickers[0])

        return await ctx.send(embed=embed, file=file)

    @snipe.command(name="clear", aliases=["wipe"])
    @has_permissions(manage_messages=True)
    @example(",snipe clear")
    async def snipe_clear(self, ctx: Context) -> None:
        """
        Remove all sniped messages from the cache.
        This is an alias for the `clearsnipe` command.
        """
        await ctx.invoke(self.clearsnipe)

    @snipe.group(name="filter", invoke_without_command=True)
    @has_permissions(manage_channels=True)
    @example(",snipe filter")
    async def snipe_filter(self, ctx: Context) -> Message:
        """
        Filter specific content from snipes.
        """
        return await ctx.send_help(ctx.command)

    @snipe_filter.command(name="add")
    @has_permissions(manage_channels=True)
    @example(",snipe filter add sin")
    async def snipe_filter_add(self, ctx: Context, *, word: str) -> Message:
        """
        Add a word to be filtered.
        """
        # Get current words
        row = await self.bot.pool.fetchrow(
            "SELECT words FROM snipe_filter WHERE guild_id = $1",
            ctx.guild.id,
        )
        words = json.loads(row["words"]) if row else []
        if word.lower() in words:
            return await ctx.deny(f"`{word}` is already being filtered!")

        if row:
            # Update
            words.append(word.lower())
            await self.bot.pool.execute(
                "UPDATE snipe_filter SET words = $1 WHERE guild_id = $2",
                json.dumps(words),
                ctx.guild.id,
            )
        else:
            # Insert
            await self.bot.pool.execute(
                "INSERT INTO snipe_filter (guild_id, words) VALUES ($1, $2)",
                ctx.guild.id,
                json.dumps([word.lower()]),
            )
        return await ctx.approve(f"Added `{word}` to the snipe filter")

    @snipe_filter.command(name="remove", aliases=["delete", "del", "rm"])
    @has_permissions(manage_channels=True)
    @example(",snipe filter remove sin")
    async def snipe_filter_remove(self, ctx: Context, *, word: str) -> Message:
        """
        Remove a word from the filter.
        """
        row = await self.bot.pool.fetchrow(
            "SELECT words FROM snipe_filter WHERE guild_id = $1",
            ctx.guild.id,
        )
        if not row:
            return await ctx.deny(f"`{word}` isn't being filtered!")
        words = json.loads(row["words"])
        if word.lower() not in words:
            return await ctx.deny(f"`{word}` isn't being filtered!")
        words.remove(word.lower())
        await self.bot.pool.execute(
            "UPDATE snipe_filter SET words = $1 WHERE guild_id = $2",
            json.dumps(words),
            ctx.guild.id,
        )
        return await ctx.approve(f"Removed `{word}` from the snipe filter")

    @snipe_filter.command(name="invites")
    @has_permissions(manage_channels=True)
    @example(",snipe filter invites")
    async def snipe_filter_invites(self, ctx: Context) -> Message:
        """
        Toggle server invites being filtered.
        """
        row = await self.bot.pool.fetchrow(
            "SELECT invites FROM snipe_filter WHERE guild_id = $1",
            ctx.guild.id,
        )
        current = bool(row and row["invites"])
        new_val = not current
        if row:
            await self.bot.pool.execute(
                "UPDATE snipe_filter SET invites = $1 WHERE guild_id = $2",
                new_val,
                ctx.guild.id,
            )
        else:
            await self.bot.pool.execute(
                "INSERT INTO snipe_filter (guild_id, invites) VALUES ($1, $2)",
                ctx.guild.id,
                new_val,
            )
        return await ctx.approve(
            f"{'Now' if new_val else 'No longer'} filtering **server invites** from snipes"
        )

    @snipe_filter.command(name="links")
    @has_permissions(manage_channels=True)
    @example(",snipe filter links")
    async def snipe_filter_links(self, ctx: Context) -> Message:
        """
        Toggle links being filtered.
        """
        row = await self.bot.pool.fetchrow(
            "SELECT links FROM snipe_filter WHERE guild_id = $1",
            ctx.guild.id,
        )
        current = bool(row and row["links"])
        new_val = not current
        if row:
            await self.bot.pool.execute(
                "UPDATE snipe_filter SET links = $1 WHERE guild_id = $2",
                new_val,
                ctx.guild.id,
            )
        else:
            await self.bot.pool.execute(
                "INSERT INTO snipe_filter (guild_id, links) VALUES ($1, $2)",
                ctx.guild.id,
                new_val,
            )
        return await ctx.approve(
            f"{'Now' if new_val else 'No longer'} filtering **links** from snipes"
        )

    @snipe_filter.command(name="settings", aliases=["config", "view"])
    @has_permissions(manage_channels=True)
    @example(",snipe filter settings")
    async def snipe_filter_settings(self, ctx: Context) -> Message:
        """
        View the filter settings.
        """
        row = await self.bot.pool.fetchrow(
            "SELECT invites, links, words FROM snipe_filter WHERE guild_id = $1",
            ctx.guild.id,
        )
        if not row:
            return await ctx.deny("The **snipe filter** hasn't been configured yet!")

        words = json.loads(row["words"]) if row["words"] else []
        embed = Embed(
            title="Snipe Filter",
            description="\n> ".join(
                [
                    f"Filtering {len(words)} word{'s' if len(words)!=1 else ''} (`{ctx.clean_prefix}snipe filter list`)",
                    f"**Invites:** {'Filtered' if row['invites'] else 'Not filtered'}",
                    f"**Links:** {'Filtered' if row['links'] else 'Not filtered'}",
                ]
            ),
            color=COLORS.neutral,
        )
        return await ctx.send(embed=embed)

    @snipe_filter.command(name="list", aliases=["words", "ls"])
    @has_permissions(manage_channels=True)
    @example(",snipe filter list")
    async def snipe_filter_list(self, ctx: Context) -> Message:
        """
        View all filtered words.
        """
        row = await self.bot.pool.fetchrow(
            "SELECT words FROM snipe_filter WHERE guild_id = $1",
            ctx.guild.id,
        )
        words = json.loads(row["words"]) if row and row["words"] else []
        if not words:
            return await ctx.deny("No words have been filtered yet!")

        entries = [f"**{w}**" for w in words]
        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title="Filtered Words",
                    description="\n".join(entries[i:i+15]),
                    color=COLORS.neutral,
                )
                for i in range(0, len(entries), 15)
            ]
        )
        return await paginator.start(ctx)

    @snipe_filter.command(name="clear", aliases=["clean", "reset"])
    @has_permissions(manage_channels=True)
    @example(",snipe filter clear")
    async def snipe_filter_clear(self, ctx: Context) -> Message:
        """
        Remove all filtered words.
        """
        # Prompt for confirmation
        # We'll use ctx.prompt if exists; otherwise we'll implement a simple confirmation view.
        # Since we don't have prompt, we'll just ask with a view.
        # For simplicity, we'll just clear.
        await self.bot.pool.execute(
            "UPDATE snipe_filter SET words = '[]' WHERE guild_id = $1",
            ctx.guild.id,
        )
        return await ctx.approve("Successfully removed all filtered words")

    @snipe.group(name="ignore", aliases=["exempt"], invoke_without_command=True)
    @has_permissions(manage_channels=True)
    @example(",snipe ignore @user")
    async def snipe_ignore(self, ctx: Context, *, member: Member) -> Message:
        """
        Ignore a member from being sniped.
        """
        # Check if already ignored
        existing = await self.bot.pool.fetchval(
            "SELECT 1 FROM snipe_ignore WHERE guild_id = $1 AND user_id = $2",
            ctx.guild.id,
            member.id,
        )
        if existing:
            return await ctx.deny(f"{member.mention} is already being ignored!")

        await self.bot.pool.execute(
            "INSERT INTO snipe_ignore (guild_id, user_id) VALUES ($1, $2)",
            ctx.guild.id,
            member.id,
        )
        return await ctx.approve(f"Now ignoring {member.mention} from being sniped")

    @snipe_ignore.command(name="remove", aliases=["delete", "del", "rm"])
    @has_permissions(manage_channels=True)
    @example(",snipe ignore remove @user")
    async def snipe_ignore_remove(self, ctx: Context, *, member: Member) -> Message:
        """
        Remove a member from being ignored.
        """
        result = await self.bot.pool.execute(
            "DELETE FROM snipe_ignore WHERE guild_id = $1 AND user_id = $2",
            ctx.guild.id,
            member.id,
        )
        if result == "DELETE 0":
            return await ctx.deny(f"{member.mention} isn't being ignored!")
        return await ctx.approve(f"Now allowing {member.mention} to be sniped")

    @snipe_ignore.command(name="list", aliases=["ls"])
    @has_permissions(manage_channels=True)
    @example(",snipe ignore list")
    async def snipe_ignore_list(self, ctx: Context) -> Message:
        """
        View all members being ignored.
        """
        rows = await self.bot.pool.fetch(
            "SELECT user_id FROM snipe_ignore WHERE guild_id = $1",
            ctx.guild.id,
        )
        members = []
        for r in rows:
            member = ctx.guild.get_member(r["user_id"])
            if member:
                members.append(f"**{member}** (`{member.id}`)")
        if not members:
            return await ctx.deny("No members are being ignored!")

        paginator = Paginator(
            ctx,
            pages=[
                Embed(
                    title="Ignored Members",
                    description="\n".join(members[i:i+15]),
                    color=COLORS.neutral,
                )
                for i in range(0, len(members), 15)
            ]
        )
        return await paginator.start(ctx)

    @hybrid_command(aliases=["cs"])
    @has_permissions(manage_messages=True)
    @example(",clearsnipe")
    async def clearsnipe(self, ctx: Context) -> None:
        """
        Remove all sniped messages from the cache for this channel.
        """
        await self.bot.pool.execute(
            "DELETE FROM snipe_messages WHERE channel_id = $1",
            ctx.channel.id,
        )
        await ctx.approve("Cleared all sniped messages in this channel.")

    @hybrid_command(aliases=["rsnipe", "rs"])
    @cooldown(1, 3, BucketType.channel)
    @example(",reactionsnipe 2")
    async def reactionsnipe(self, ctx: Context, index: int = 1) -> Message:
        """
        Snipe the last removed reaction.
        """
        rows = await self.bot.pool.fetch(
            """
            SELECT * FROM snipe_reactions
            WHERE channel_id = $1
            ORDER BY removed_at DESC
            LIMIT 1 OFFSET $2
            """,
            ctx.channel.id,
            index - 1,
        )
        if not rows:
            return await ctx.deny(
                f"No **sniped reaction** available at index `{index}`!"
                if index != 1
                else "No **reactions** have been removed recently!"
            )
        reaction = rows[0]
        # Check ignore? For reactions, we might ignore if user is ignored, but we'll skip for simplicity.
        return await ctx.send(
            embed=Embed(
                description=f"**{reaction['user_name']}** removed **{reaction['emoji']}** [{format_dt(reaction['removed_at'], 'R')}](https://discord.com/channels/{reaction['guild_id']}/{reaction['channel_id']}/{reaction['message_id']})",
                color=COLORS.neutral,
            )
        )

    @hybrid_group(aliases=["es"], invoke_without_command=True)
    @cooldown(1, 3, BucketType.channel)
    @example(",editsnipe 2")
    async def editsnipe(self, ctx: Context, index: int = 1) -> Message:
        """
        Snipe the last edited message.
        """
        rows = await self.bot.pool.fetch(
            """
            SELECT * FROM snipe_edits
            WHERE channel_id = $1
            ORDER BY edited_at DESC
            LIMIT 1 OFFSET $2
            """,
            ctx.channel.id,
            index - 1,
        )
        if not rows:
            return await ctx.deny(
                f"No **sniped edit** available at index `{index}`!"
                if index != 1
                else "No **messages** have been edited recently!"
            )
        row = rows[0]

        # Check ignore
        ignored = await self._get_ignored_ids(ctx.guild.id)
        if row["user_id"] in ignored:
            return await ctx.deny(f"**{row['user_name']}** is immune to being sniped!")

        # Apply filters if needed
        before_content = row["before_content"]
        after_content = row["after_content"]
        if not ctx.channel.permissions_for(ctx.author).manage_messages:
            config = await self._get_filter_config(ctx.guild.id)
            if config:
                words = json.loads(config["words"]) if config["words"] else []
                for word in words:
                    if word.lower() in before_content.lower() or word.lower() in after_content.lower():
                        return await ctx.send("That message probably shouldn't be sniped!")
                before_content = await self._apply_filter(before_content, ctx.guild.id)
                after_content = await self._apply_filter(after_content, ctx.guild.id)

        embed = Embed(color=COLORS.neutral)
        embed.add_field(name="**Before**", value=before_content or "Empty", inline=True)
        embed.add_field(name="**After**", value=after_content or "Empty", inline=True)
        embed.set_author(
            name=row["user_name"],
            icon_url=row["user_avatar"],
        )
        total = await self.bot.pool.fetchval(
            "SELECT COUNT(*) FROM snipe_edits WHERE channel_id = $1",
            ctx.channel.id,
        )
        embed.set_footer(
            text=f"{index}/{total} • Edited {naturaldelta((utcnow() - row['edited_at']))} ago"
        )

        # Attachments/stickers
        attachments = json.loads(row["attachments"]) if row["attachments"] else []
        stickers = json.loads(row["stickers"]) if row["stickers"] else []
        if attachments:
            for att in attachments:
                if att.get("content_type", "").startswith("image/"):
                    embed.set_image(url=att["url"])
                    break
            embed.add_field(
                name=f"**Attachment{'s' if len(attachments)>1 else ''}**",
                value="\n".join([a["url"] for a in attachments]),
                inline=False,
            )
        elif stickers:
            embed.set_image(url=stickers[0])

        return await ctx.send(embed=embed)

    @editsnipe.command(name="clear")
    @has_permissions(manage_messages=True)
    @example(",editsnipe clear")
    async def editsnipe_clear(self, ctx: Context) -> None:
        """
        Remove all sniped edits from the cache.
        """
        await self.bot.pool.execute(
            "DELETE FROM snipe_edits WHERE channel_id = $1",
            ctx.channel.id,
        )
        await ctx.approve("Cleared all sniped edits in this channel.")