import discord
import datetime
import sqlite3
from discord import Embed, TextChannel, app_commands, Interaction
from discord.ext.commands import has_permissions, hybrid_group, Cog
from discord.ui import Modal, TextInput

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import EMOJIS, COLORS

# ===== Confession Modal =====
class ConfessModal(Modal, title="Confess Anonymously"):
    content = TextInput(
        label="Your confession",
        placeholder="Type your confession here...",
        style=discord.TextStyle.long,
        required=True,
        max_length=2000,
    )

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Check blacklist
        blacklist = await interaction.client.pool.fetch(
            "SELECT word FROM confess_blacklist WHERE guild_id = $1",
            interaction.guild.id
        )
        content_lower = self.content.value.lower()
        for record in blacklist:
            if record["word"] in content_lower:
                return await interaction.followup.send(
                    "Your confession contains a blacklisted word.", ephemeral=True
                )

        # Get config
        config = await interaction.client.pool.fetchrow(
            "SELECT channel_id, confession, upvote, downvote FROM confess WHERE guild_id = $1",
            interaction.guild.id
        )
        if not config:
            return await interaction.followup.send(
                "Confessions are not enabled in this server.", ephemeral=True
            )

        channel = interaction.guild.get_channel(config["channel_id"])
        if not channel:
            return await interaction.followup.send(
                "Confession channel not found.", ephemeral=True
            )

        # Increment confession count
        new_count = config["confession"] + 1
        await interaction.client.pool.execute(
            "UPDATE confess SET confession = $1 WHERE guild_id = $2",
            new_count, interaction.guild.id
        )

        # Build embed
        embed = Embed(
            description=self.content.value,
            color=0x2B2D31,
            timestamp=datetime.datetime.now()
        )
        embed.set_author(
            name=f"anonymous confession #{new_count}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
        )

        # Add reactions if configured
        upvote = config.get("upvote") or "👍"
        downvote = config.get("downvote") or "👎"

        # Send to channel
        message = await channel.send(embed=embed)
        try:
            await message.add_reaction(upvote)
            await message.add_reaction(downvote)
        except:
            pass

        # Store in DB
        await interaction.client.pool.execute(
            """
            INSERT INTO confess_members (guild_id, user_id, confession)
            VALUES ($1, $2, $3)
            """,
            interaction.guild.id, interaction.user.id, new_count
        )

        await interaction.followup.send(
            "Your confession has been sent anonymously.", ephemeral=True
        )

# ===== Main Cog =====
class Confessions(Cog):
    """Anonymous confession commands."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS confess (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT,
                confession INTEGER DEFAULT 0,
                upvote TEXT,
                downvote TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS confess_members (
                guild_id BIGINT,
                user_id BIGINT,
                confession INTEGER,
                PRIMARY KEY (guild_id, confession)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS confess_mute (
                guild_id BIGINT,
                user_id BIGINT,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS confess_blacklist (
                guild_id BIGINT,
                word TEXT,
                PRIMARY KEY (guild_id, word)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS confess_replies (
                message_id BIGINT PRIMARY KEY,
                user_id BIGINT
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    @hybrid_group(invoke_without_command=True)
    async def confessions(self, ctx: Context):
        """Configure anonymous confessions."""
        return await ctx.send_help(ctx.command)

    @confessions.command(name="mute", example="34")
    @has_permissions(manage_messages=True)
    async def confessions_mute(self, ctx: Context, *, number: int):
        """Mute a member that sent a specific confession."""
        check = await self.bot.pool.fetchrow(
            "SELECT channel_id FROM confess WHERE guild_id = $1", ctx.guild.id
        )
        if check is None:
            return await ctx.warn("Confessions aren't **enabled** in this server!")

        re = await self.bot.pool.fetchrow(
            "SELECT * FROM confess_members WHERE guild_id = $1 AND confession = $2",
            ctx.guild.id, number
        )
        if re is None:
            return await ctx.warn("**Couldn't** find that confession!")

        member_id = re["user_id"]

        r = await self.bot.pool.fetchrow(
            "SELECT * FROM confess_mute WHERE guild_id = $1 AND user_id = $2",
            ctx.guild.id, member_id
        )
        if r:
            return await ctx.warn("This **member** is **already** confession muted!")

        await self.bot.pool.execute(
            "INSERT INTO confess_mute (guild_id, user_id) VALUES ($1, $2)",
            ctx.guild.id, member_id
        )
        return await ctx.approve(f"**Muted** the author of confession #{number}.")

    @confessions.command(name="unmute", example="34 or all")
    @has_permissions(manage_messages=True)
    async def confessions_unmute(self, ctx: Context, *, number: str):
        """Unmute a member that sent a specific confession, or unmute all."""
        check = await self.bot.pool.fetchrow(
            "SELECT channel_id FROM confess WHERE guild_id = $1", ctx.guild.id
        )
        if check is None:
            return await ctx.warn("Confessions aren't **enabled** in this server!")

        if number.lower() == "all":
            await ctx.prompt("Are you sure you want to unmute **everyone** in confession mute?")
            await self.bot.pool.execute(
                "DELETE FROM confess_mute WHERE guild_id = $1", ctx.guild.id
            )
            return await ctx.approve("Unmuted **everyone** in confession mute!")

        try:
            num = int(number)
        except ValueError:
            return await ctx.warn("Invalid number. Use a confession ID or `all`.")

        re = await self.bot.pool.fetchrow(
            "SELECT * FROM confess_members WHERE guild_id = $1 AND confession = $2",
            ctx.guild.id, num
        )
        if re is None:
            return await ctx.warn("**Couldn't** find that confession!")

        member_id = re["user_id"]

        r = await self.bot.pool.fetchrow(
            "SELECT * FROM confess_mute WHERE guild_id = $1 AND user_id = $2",
            ctx.guild.id, member_id
        )
        if not r:
            return await ctx.warn("This **member** is **not** confession muted!")

        await self.bot.pool.execute(
            "DELETE FROM confess_mute WHERE guild_id = $1 AND user_id = $2",
            ctx.guild.id, member_id
        )
        return await ctx.approve(f"**Unmuted** the author of confession #{number}!")

    @confessions.command(name="add", example="#confessions")
    @has_permissions(manage_guild=True)
    async def confessions_add(self, ctx: Context, *, channel: TextChannel):
        """Set the confession channel."""
        check = await self.bot.pool.fetchrow(
            "SELECT * FROM confess WHERE guild_id = $1", ctx.guild.id
        )
        if check is not None:
            await self.bot.pool.execute(
                "UPDATE confess SET channel_id = $1 WHERE guild_id = $2",
                channel.id, ctx.guild.id
            )
        else:
            await self.bot.pool.execute(
                "INSERT INTO confess (guild_id, channel_id, confession) VALUES ($1, $2, 0)",
                ctx.guild.id, channel.id
            )
        return await ctx.approve(f"Set the confessions channel to {channel.mention}!")

    @confessions.command(name="remove")
    @has_permissions(manage_guild=True)
    async def confessions_remove(self, ctx: Context):
        """Remove confession channel and all data."""
        check = await self.bot.pool.fetchrow(
            "SELECT channel_id FROM confess WHERE guild_id = $1", ctx.guild.id
        )
        if check is None:
            return await ctx.warn("Confessions aren't **enabled** in this server!")

        await self.bot.pool.execute(
            "DELETE FROM confess WHERE guild_id = $1", ctx.guild.id
        )
        await self.bot.pool.execute(
            "DELETE FROM confess_members WHERE guild_id = $1", ctx.guild.id
        )
        await self.bot.pool.execute(
            "DELETE FROM confess_mute WHERE guild_id = $1", ctx.guild.id
        )
        await self.bot.pool.execute(
            "DELETE FROM confess_blacklist WHERE guild_id = $1", ctx.guild.id
        )
        return await ctx.approve("**Disabled** confessions for this server!")

    @confessions.command(name="channel")
    async def confessions_channel(self, ctx: Context):
        """Get the confessions channel."""
        check = await self.bot.pool.fetchrow(
            "SELECT * FROM confess WHERE guild_id = $1", ctx.guild.id
        )
        if check is not None:
            channel = ctx.guild.get_channel(check["channel_id"])
            embed = Embed(
                description=f"Confession Channel: {channel.mention}\nConfessions Sent: **{check['confession']}**"
            )
            return await ctx.send(embed=embed)
        return await ctx.warn("Confessions aren't **enabled** in this server!")

    @app_commands.command(name="confess")
    async def confess(self, interaction: Interaction):
        """Confess anonymously in the server."""
        # Check mute
        muted = await interaction.client.pool.fetchrow(
            "SELECT * FROM confess_mute WHERE guild_id = $1 AND user_id = $2",
            interaction.guild.id, interaction.user.id
        )
        if muted:
            await interaction.response.send_message(
                "You are **muted** from sending confessions in this server!",
                ephemeral=True
            )
            return

        # Check if enabled
        check = await interaction.client.pool.fetchrow(
            "SELECT channel_id FROM confess WHERE guild_id = $1",
            interaction.guild.id
        )
        if check:
            await interaction.response.send_modal(ConfessModal())
        else:
            await interaction.response.send_message(
                "Confessions **aren't** enabled in this server!",
                ephemeral=True
            )

    @confessions.group(name="blacklist", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def confessions_blacklist(self, ctx: Context):
        """Manage confession blacklisted words."""
        return await ctx.send_help(ctx.command)

    @confessions_blacklist.command(name="add", example="badword")
    @has_permissions(manage_guild=True)
    async def blacklist_add(self, ctx: Context, *, word: str):
        """Add a word to the confession blacklist."""
        try:
            await self.bot.pool.execute(
                "INSERT INTO confess_blacklist (guild_id, word) VALUES ($1, $2)",
                ctx.guild.id, word.lower()
            )
            return await ctx.approve(f"Added `{word}` to the confession blacklist")
        except sqlite3.IntegrityError:
            return await ctx.warn(f"`{word}` is already blacklisted")

    @confessions_blacklist.command(name="remove", example="badword")
    @has_permissions(manage_guild=True)
    async def blacklist_remove(self, ctx: Context, *, word: str):
        """Remove a word from the confession blacklist."""
        result = await self.bot.pool.execute(
            "DELETE FROM confess_blacklist WHERE guild_id = $1 AND word = $2",
            ctx.guild.id, word.lower()
        )
        if result == "DELETE 0":
            return await ctx.warn(f"`{word}` is not blacklisted!")
        return await ctx.approve(f"Removed `{word}` from the confession blacklist")

    @confessions_blacklist.command(name="list")
    @has_permissions(manage_guild=True)
    async def blacklist_list(self, ctx: Context):
        """List all blacklisted words."""
        words = await self.bot.pool.fetch(
            "SELECT word FROM confess_blacklist WHERE guild_id = $1 ORDER BY word",
            ctx.guild.id
        )
        if not words:
            return await ctx.warn("No words are blacklisted")

        entries = [f"`{record['word']}`" for record in words]
        embed = Embed(title="Confession Blacklisted Words", description="\n".join(entries))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    @confessions_blacklist.command(name="clear")
    @has_permissions(manage_guild=True)
    async def blacklist_clear(self, ctx: Context):
        """Clear all blacklisted words."""
        await self.bot.pool.execute(
            "DELETE FROM confess_blacklist WHERE guild_id = $1", ctx.guild.id
        )
        return await ctx.approve("Cleared all blacklisted words")

    @confessions.command(name="report", example="34")
    async def confessions_report(self, ctx: Context, confession_id: str):
        """Report a confession or reply."""
        report_channel = self.bot.get_channel(1333340804067889235)
        if not report_channel:
            return await ctx.warn("Report channel not found. Please contact staff.")

        try:
            if len(confession_id) > 10:
                # It's a message ID (reply)
                message_id = int(confession_id)
                check = await self.bot.pool.fetchrow(
                    "SELECT channel_id FROM confess WHERE guild_id = $1", ctx.guild.id
                )
                if not check:
                    return await ctx.warn("Confessions **aren't** enabled in this server!")

                confess_channel = ctx.guild.get_channel(check['channel_id'])
                if not confess_channel:
                    return await ctx.warn("Confession channel not found!")

                # Try to fetch the message from active threads
                message = None
                for thread in confess_channel.threads:
                    try:
                        msg = await thread.fetch_message(message_id)
                        if msg:
                            message = msg
                            break
                    except (discord.NotFound, discord.HTTPException):
                        continue

                if not message:
                    # Check archived threads
                    async for thread in confess_channel.archived_threads():
                        try:
                            msg = await thread.fetch_message(message_id)
                            if msg:
                                message = msg
                                break
                        except:
                            continue

                if not message or not message.author.bot or not message.embeds:
                    return await ctx.warn("Reply not found.")

                reply_author = await self.bot.pool.fetchrow(
                    "SELECT user_id FROM confess_replies WHERE message_id = $1",
                    message_id
                )

                report_embed = Embed(
                    title="Confession Reply Report",
                    description=f"**Reply Content:**\n{message.embeds[0].description}",
                    timestamp=datetime.datetime.now(),
                    color=discord.Color.red()
                )
                report_embed.add_field(name="Reporter", value=f"{ctx.author} ({ctx.author.id})")
                report_embed.add_field(name="Server", value=f"{ctx.guild.name} ({ctx.guild.id})")
                report_embed.add_field(name="Channel", value=f"{confess_channel.mention} ({confess_channel.id})")
                report_embed.add_field(name="Message ID", value=message_id)
                if reply_author:
                    report_embed.add_field(name="Reply Author ID", value=reply_author['user_id'])

                await report_channel.send(embed=report_embed)
                return await ctx.approve("Report sent successfully.")

            else:
                # It's a confession number
                confession_num = int(confession_id)
                confession_data = await self.bot.pool.fetchrow(
                    """
                    SELECT cm.user_id, cm.guild_id, c.channel_id, c.confession 
                    FROM confess_members cm
                    JOIN confess c ON c.guild_id = cm.guild_id
                    WHERE cm.guild_id = $1 AND cm.confession = $2
                    """,
                    ctx.guild.id, confession_num
                )
                if not confession_data:
                    return await ctx.warn("Confession not found.")

                channel = ctx.guild.get_channel(confession_data['channel_id'])
                if not channel:
                    return await ctx.warn("Confession channel not found.")

                async for message in channel.history(limit=100):
                    if (message.author.bot and message.embeds
                        and message.embeds[0].author.name == f"anonymous confession #{confession_num}"):
                        report_embed = Embed(
                            title="Confession Report",
                            description=f"**Confession #{confession_num}:**\n{message.embeds[0].description}",
                            timestamp=datetime.datetime.now(),
                            color=discord.Color.red()
                        )
                        report_embed.add_field(name="Reporter", value=f"{ctx.author} ({ctx.author.id})")
                        report_embed.add_field(name="Server", value=f"{ctx.guild.name} ({ctx.guild.id})")
                        report_embed.add_field(name="Confession Author ID", value=confession_data['user_id'])

                        await report_channel.send(embed=report_embed)
                        return await ctx.approve("Report sent successfully.")

                return await ctx.warn("Couldn't find the confession message.")

        except ValueError:
            return await ctx.warn("Invalid confession ID or message ID.")

    @confessions.group(name="emojis", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def confessions_emojis(self, ctx: Context):
        """Manage confession reaction emojis."""
        return await ctx.send_help(ctx.command)

    @confessions_emojis.command(name="set", example="👍 👎")
    @has_permissions(manage_guild=True)
    async def emojis_set(self, ctx: Context, upvote: str, downvote: str):
        """Set custom reaction emojis for confessions. Use 'none' to disable reactions."""
        if upvote.lower() != "none":
            try:
                await ctx.message.add_reaction(upvote)
                await ctx.message.remove_reaction(upvote, ctx.me)
            except discord.HTTPException:
                return await ctx.warn(f"Invalid upvote emoji: {upvote}")

        if downvote.lower() != "none":
            try:
                await ctx.message.add_reaction(downvote)
                await ctx.message.remove_reaction(downvote, ctx.me)
            except discord.HTTPException:
                return await ctx.warn(f"Invalid downvote emoji: {downvote}")

        await self.bot.pool.execute(
            """
            UPDATE confess 
            SET upvote = $1, downvote = $2 
            WHERE guild_id = $3
            """,
            None if upvote.lower() == "none" else upvote,
            None if downvote.lower() == "none" else downvote,
            ctx.guild.id
        )

        if upvote.lower() == "none" and downvote.lower() == "none":
            return await ctx.approve("Disabled confession reactions")
        return await ctx.approve(f"Set confession reactions to {upvote} and {downvote}")

    @confessions_emojis.command(name="reset")
    @has_permissions(manage_guild=True)
    async def emojis_reset(self, ctx: Context):
        """Reset confession reaction emojis to default (👍 👎)."""
        await self.bot.pool.execute(
            "UPDATE confess SET upvote = $1, downvote = $2 WHERE guild_id = $3",
            "👍", "👎", ctx.guild.id
        )
        return await ctx.approve("Reset confession reactions to default (👍 👎)")

    @confessions_emojis.command(name="view")
    @has_permissions(manage_guild=True)
    async def emojis_view(self, ctx: Context):
        """View current confession reaction emojis."""
        data = await self.bot.pool.fetchrow(
            "SELECT upvote, downvote FROM confess WHERE guild_id = $1", ctx.guild.id
        )
        if not data:
            return await ctx.warn("Confessions are not set up in this server!")
        if not data['upvote'] and not data['downvote']:
            return await ctx.warn("Confession reactions are disabled")
        return await ctx.neutral(f"Current confession reactions: {data['upvote']} {data['downvote']}")

async def setup(bot: Bot):
    await bot.add_cog(Confessions(bot))