import asyncio
from typing import Optional, cast, Dict
from discord import Embed, HTTPException, Message, TextChannel
from discord.ext.commands import Cog, group, has_permissions
from discord.utils import utcnow
from xxhash import xxh32_hexdigest

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import EMOJIS, COLORS

# ===== Local helpers =====

def codeblock(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"

def vowel(text: str) -> str:
    """Return 'a' or 'an' depending on the first letter of the text."""
    if text and text[0].lower() in 'aeiou':
        return 'an'
    return 'a'

async def quietly_delete(message: Message):
    try:
        await message.delete()
    except:
        pass

# ===== Script class (replaces tools.parser.Script) =====

class Script:
    """Simple script processor with placeholders."""
    def __init__(self, template: str, args: list):
        self.template = template
        self.args = args  # guild, channel, user (or any objects)

    @classmethod
    def from_message(cls, message: Message):
        if message.content:
            return cls(message.content, [])
        return None

    def _format(self, text: str) -> str:
        # Replace placeholders with actual values from args
        # args: [guild, channel, user, ...]
        guild = self.args[0] if len(self.args) > 0 else None
        channel = self.args[1] if len(self.args) > 1 else None
        user = self.args[2] if len(self.args) > 2 else None

        replacements = {
            "{guild.name}": guild.name if guild else "",
            "{guild.id}": str(guild.id) if guild else "",
            "{guild.member_count}": str(guild.member_count) if guild else "",
            "{guild.vanity}": guild.vanity_url_code if guild and guild.vanity_url_code else "none",
            "{channel.mention}": channel.mention if channel else "",
            "{channel.name}": channel.name if channel else "",
            "{channel.id}": str(channel.id) if channel else "",
            "{user.mention}": user.mention if user else "",
            "{user.name}": user.name if user else "",
            "{user.id}": str(user.id) if user else "",
            "{user.display_name}": user.display_name if user else "",
        }
        for key, value in replacements.items():
            text = text.replace(key, value)
        return text

    async def send(self, destination):
        """Send the formatted script to a destination (channel or context)."""
        content = self._format(self.template)
        # If destination is a context, use its send method; else use channel.send
        if hasattr(destination, 'send'):
            # It might be a Context or a TextChannel
            return await destination.send(content=content)
        else:
            # Assume it's a channel or similar
            return await destination.send(content=content)

    @property
    def format(self):
        return self.template


class Sticky(Cog):
    """
    Stick messages to the bottom of a channel.
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self._locks: Dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        """Create the sticky_message table if it doesn't exist."""
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS sticky_message (
                guild_id BIGINT,
                channel_id BIGINT,
                message_id BIGINT,
                template TEXT,
                PRIMARY KEY (guild_id, channel_id)
            )
            """
        )

    def get_lock(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    @Cog.listener("on_message")
    async def sticky_listener(self, message: Message) -> None:
        """
        Stick messages to the bottom of a channel.
        We don't want to send sticky messages constantly, so we'll
        only send them if messages haven't been sent within 3 seconds.
        """
        if (
            not message.guild
            or message.author.bot
            or not isinstance(message.channel, TextChannel)
        ):
            return

        guild = message.guild
        channel = message.channel
        record = await self.bot.pool.fetchrow(
            """
            SELECT message_id, template
            FROM sticky_message
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            guild.id,
            channel.id,
        )
        if not record:
            return

        lock = self.get_lock(channel.id)
        # Attempt to acquire the lock; if already locked, it means a sticky is being processed.
        if lock.locked():
            return

        async with lock:
            # We have the lock; we can safely process.
            # Check again if the sticky still exists (could have been removed while we waited)
            record = await self.bot.pool.fetchrow(
                """
                SELECT message_id, template
                FROM sticky_message
                WHERE guild_id = $1
                AND channel_id = $2
                """,
                guild.id,
                channel.id,
            )
            if not record:
                return

            last_message = channel.get_partial_message(record["message_id"])
            time_since = utcnow() - last_message.created_at
            time_to_wait = 6 - time_since.total_seconds()
            if time_to_wait > 1:
                await asyncio.sleep(time_to_wait)

            script = Script(
                record["template"],
                [guild, channel, message.author],
            )

            try:
                new_message = await script.send(channel)
            except HTTPException:
                # If sending fails, remove the sticky
                await self.bot.pool.execute(
                    """
                    DELETE FROM sticky_message
                    WHERE guild_id = $1
                    AND channel_id = $2
                    """,
                    guild.id,
                    channel.id,
                )
            else:
                await self.bot.pool.execute(
                    """
                    UPDATE sticky_message
                    SET message_id = $3
                    WHERE guild_id = $1
                    AND channel_id = $2
                    """,
                    guild.id,
                    channel.id,
                    new_message.id,
                )
            # Delete the old message after sending the new one
            await quietly_delete(last_message)

    @group(
        aliases=["stickymessage", "stickymsg"],
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def sticky(self, ctx: Context) -> Message:
        """
        Stick messages to the bottom of a channel.
        """
        return await ctx.send_help(ctx.command)

    @sticky.command(
        name="add",
        aliases=["create"],
        example="(#channel) (script)",
    )
    @has_permissions(manage_messages=True)
    async def sticky_add(
        self,
        ctx: Context,
        channel: TextChannel,
        *,
        script: Script,
    ) -> Message:
        """
        Add a sticky message to a channel.
        """
        # Check if a sticky already exists for this channel
        existing = await self.bot.pool.fetchval(
            "SELECT 1 FROM sticky_message WHERE guild_id = $1 AND channel_id = $2",
            ctx.guild.id,
            channel.id,
        )
        if existing:
            return await ctx.warn(
                "A sticky message already exists for that channel!",
            )

        try:
            message = await script.send(channel)
            await self.bot.pool.execute(
                """
                INSERT INTO sticky_message (
                    guild_id,
                    channel_id,
                    message_id,
                    template
                )
                VALUES ($1, $2, $3, $4)
                """,
                ctx.guild.id,
                channel.id,
                message.id,
                script.template,
            )
        except HTTPException as exc:
            return await ctx.warn(
                "Your sticky message wasn't able to be sent!", codeblock(exc.text)
            )

        return await ctx.approve(
            f"Added {vowel(script.format)} sticky message to {channel.mention}",
        )

    @sticky.command(
        name="existing",
        aliases=["from"],
        example="(#channel) (message)",
    )
    @has_permissions(manage_messages=True)
    async def sticky_existing(
        self,
        ctx: Context,
        channel: TextChannel,
        message: Message,
    ) -> Message:
        """
        Add a sticky message to a channel from an existing message.
        """
        script = Script.from_message(message)
        if not script:
            return await ctx.warn("That message doesn't have any content!")

        return await self.sticky_add(ctx, channel, script=script)

    @sticky.command(
        name="edit",
        aliases=["update"],
        example="(#channel) (script)",
    )
    @has_permissions(manage_messages=True)
    async def sticky_edit(
        self,
        ctx: Context,
        channel: TextChannel,
        *,
        script: Script,
    ) -> Message:
        """
        Edit an existing sticky message.
        """
        message_id = await self.bot.pool.fetchval(
            """
            UPDATE sticky_message
            SET template = $3
            WHERE guild_id = $1
            AND channel_id = $2
            RETURNING message_id
            """,
            ctx.guild.id,
            channel.id,
            script.template,
        )
        if not message_id:
            return await ctx.warn(f"{channel.mention} doesn't have a sticky message!")

        message = channel.get_partial_message(message_id)
        await quietly_delete(message)

        try:
            new_message = await script.send(channel)
        except HTTPException as exc:
            return await ctx.warn(
                "Your sticky message wasn't able to be sent!", codeblock(exc.text)
            )

        await self.bot.pool.execute(
            """
            UPDATE sticky_message
            SET message_id = $3
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            ctx.guild.id,
            channel.id,
            new_message.id,
        )

        return await ctx.approve(f"Updated the sticky message in {channel.mention}")

    @sticky.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        example="#general",
    )
    @has_permissions(manage_messages=True)
    async def sticky_remove(
        self,
        ctx: Context,
        channel: TextChannel,
    ) -> Message:
        """
        Remove a sticky message from a channel.
        """
        message_id = await self.bot.pool.fetchval(
            """
            DELETE FROM sticky_message
            WHERE guild_id = $1
            AND channel_id = $2
            RETURNING message_id
            """,
            ctx.guild.id,
            channel.id,
        )
        if not message_id:
            return await ctx.warn(f"{channel.mention} doesn't have a sticky message!")

        message = channel.get_partial_message(message_id)
        await quietly_delete(message)

        return await ctx.approve(f"Removed the sticky message from {channel.mention}")

    @sticky.command(
        name="view",
        aliases=["show"],
        example="#general",
    )
    @has_permissions(manage_messages=True)
    async def sticky_view(
        self,
        ctx: Context,
        channel: TextChannel,
    ) -> Message:
        """
        View an existing sticky message.
        """
        template = await self.bot.pool.fetchval(
            """
            SELECT template
            FROM sticky_message
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            ctx.guild.id,
            channel.id,
        )
        if not template:
            return await ctx.warn(f"{channel.mention} doesn't have a sticky message!")

        script = Script(template, [ctx.guild, ctx.author, channel])

        await ctx.send(codeblock(script.template))
        return await script.send(ctx.channel)

    @sticky.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_guild=True)
    async def sticky_list(self, ctx: Context) -> Message:
        """
        View all channels with sticky messages.
        """
        records = await self.bot.pool.fetch(
            """
            SELECT channel_id, message_id
            FROM sticky_message
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        channels = []
        for record in records:
            channel = ctx.guild.get_channel(record["channel_id"])
            if channel and isinstance(channel, TextChannel):
                message = channel.get_partial_message(record["message_id"])
                jump_url = f"https://discord.com/channels/{ctx.guild.id}/{channel.id}/{record['message_id']}"
                channels.append(
                    f"{channel.mention} (`{channel.id}`) - [Message]({jump_url})"
                )
        if not channels:
            return await ctx.warn("No sticky messages exist for this server!")

        embed = Embed(title="Sticky Messages", description="\n".join(channels))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()