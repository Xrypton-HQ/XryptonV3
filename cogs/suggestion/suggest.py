import discord
import datetime
from discord import TextChannel, Thread, app_commands, Interaction, ForumChannel, ButtonStyle
from discord.ui import View, Button, Modal
from discord.ext.commands import Cog, group, has_permissions
from typing import Optional

from base.Xrypton import Bot
from base.context import Context

# ===== Suggestion View and Modal =====

class SuggestionView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def get_votes(self, message_id):
        votes = await self.bot.pool.fetch(
            """SELECT vote_type FROM suggestion_votes 
            WHERE message_id = $1""",
            message_id
        )
        upvotes = len([v for v in votes if v['vote_type'] == 1])
        downvotes = len([v for v in votes if v['vote_type'] == -1])
        return upvotes, downvotes

    async def update_vote_count(self, message):
        upvotes, downvotes = await self.get_votes(message.id)
        embed = message.embeds[0]
        
        for i, field in enumerate(embed.fields):
            if field.name == "Votes":
                embed.set_field_at(
                    i,
                    name="Votes",
                    value=f"👍 `{upvotes}` • 👎 `{downvotes}`",
                    inline=False
                )
                break
        
        await message.edit(embed=embed)

    async def handle_vote(self, interaction: Interaction, vote_type: int):
        current_vote = await self.bot.pool.fetchval(
            """SELECT vote_type FROM suggestion_votes 
            WHERE message_id = $1 AND user_id = $2""",
            interaction.message.id,
            interaction.user.id
        )

        embed = discord.Embed(color=discord.Color.green())

        if current_vote == vote_type:  
            await self.bot.pool.execute(
                """DELETE FROM suggestion_votes 
                WHERE message_id = $1 AND user_id = $2""",
                interaction.message.id,
                interaction.user.id
            )
            embed.description = "Your vote has been removed!"
        else:  
            await self.bot.pool.execute(
                """INSERT INTO suggestion_votes (guild_id, message_id, user_id, vote_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (message_id, user_id) 
                DO UPDATE SET vote_type = $4""",
                interaction.guild.id,
                interaction.message.id,
                interaction.user.id,
                vote_type
            )
            embed.description = "Your vote has been recorded!"

        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.update_vote_count(interaction.message)

    @discord.ui.button(
        label="Upvote", 
        style=ButtonStyle.green, 
        custom_id="suggest_upvote",
        row=0
    )
    async def upvote(self, interaction: Interaction, button: Button):
        await self.handle_vote(interaction, 1)

    @discord.ui.button(
        label="Downvote", 
        style=ButtonStyle.red, 
        custom_id="suggest_downvote",
        row=0
    )
    async def downvote(self, interaction: Interaction, button: Button):
        await self.handle_vote(interaction, -1)

    @discord.ui.button(
        label="Create Suggestion", 
        style=ButtonStyle.blurple, 
        custom_id="suggest_create",
        row=0
    )
    async def create_suggestion(self, interaction: Interaction, button: Button):
        settings = await self.bot.pool.fetchrow(
            "SELECT anonymous_allowed FROM suggestion WHERE guild_id = $1",
            interaction.guild.id
        )
        await interaction.response.send_modal(SuggestModal(
            anonymous_allowed=settings.get('anonymous_allowed', False) if settings else False
        ))

class SuggestModal(Modal, title="Create a Suggestion"):
    def __init__(self, anonymous_allowed: bool = False):
        super().__init__()
        self.anonymous_allowed = anonymous_allowed

        self.add_item(discord.ui.TextInput(
            label="Title",
            placeholder="Brief summary of your suggestion (optional)",
            max_length=100,
            required=False,
            style=discord.TextStyle.short
        ))

        self.add_item(discord.ui.TextInput(
            label="Description",
            placeholder="Detailed explanation of your suggestion (No links allowed)",
            style=discord.TextStyle.long,
            required=True,
            max_length=4000
        ))

        if anonymous_allowed:
            self.add_item(discord.ui.TextInput(
                label="Anonymous Suggestion",
                placeholder="Type 'yes' to make anonymous (Server allows anonymous suggestions)",
                required=False,
                max_length=3,
                style=discord.TextStyle.short
            ))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if "http" in self.children[1].value.lower():
                return await interaction.response.send_message(
                    "Links are not allowed in suggestions!", ephemeral=True
                )

            suggestion_data = await interaction.client.pool.fetchrow(
                """
                UPDATE suggestion 
                SET suggestion_id = COALESCE(suggestion_id, 0) + 1 
                WHERE guild_id = $1 
                RETURNING *
                """,
                interaction.guild.id
            )
            
            if not suggestion_data:
                return await interaction.response.send_message(
                    "Suggestions are not enabled in this server!", ephemeral=True
                )

            channel = interaction.guild.get_channel(suggestion_data["channel_id"])
            count = suggestion_data["suggestion_id"]
            is_anonymous = (
                self.anonymous_allowed and 
                len(self.children) > 2 and 
                self.children[2].value.lower() == "yes"
            )

            embed = discord.Embed(
                description=self.children[1].value,
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now()
            )
            
            if self.children[0].value:
                embed.title = self.children[0].value

            embed.set_author(
                name=f"Suggestion #{count}" + (" (Anonymous)" if is_anonymous else ""),
                icon_url=interaction.guild.icon
            )

            embed.add_field(
                name="Votes",
                value=f"👍 `0` • 👎 `0`",
                inline=False
            )

            if not is_anonymous:
                embed.set_footer(
                    text=f"Suggested by {interaction.user}",
                    icon_url=interaction.user.display_avatar.url
                )
            else:
                embed.set_footer(text="Anonymous Suggestion")

            msg = None
            if isinstance(channel, discord.ForumChannel):
                thread_name = self.children[0].value if self.children[0].value else f"Suggestion #{count}"
                if not self.children[0].value:
                    thread_name = f"Suggestion #{count}"
                else:
                    thread_name = f"{self.children[0].value} #{count}"
                
                thread = await channel.create_thread(
                    name=thread_name,
                    content="",
                    embed=embed,
                    view=SuggestionView(interaction.client)
                )
                msg = thread.message
            else:
                msg = await channel.send(embed=embed, view=SuggestionView(interaction.client))
                if suggestion_data.get("thread_enabled", False):
                    await msg.create_thread(
                        name=f"Suggestion #{count} Discussion",
                        auto_archive_duration=1440
                    )

            await interaction.client.pool.execute(
                """UPDATE suggestion 
                SET suggestion_id = $1 
                WHERE guild_id = $2""",
                count,
                interaction.guild.id
            )

            await interaction.client.pool.execute(
                """INSERT INTO suggestion_entries 
                (guild_id, message_id, author_id, suggestion_id, is_anonymous)
                VALUES ($1, $2, $3, $4, $5)""",
                interaction.guild.id,
                msg.id if msg else 0,
                interaction.user.id,
                count,
                is_anonymous
            )

            success_embed = discord.Embed(
                title="✅ Suggestion Posted",
                description=(
                    f"Your suggestion has been posted in {channel.mention}\n\n"
                    "**Note:** To include images in your suggestions, "
                    "please use the `/suggest` command instead."
                ),
                color=discord.Color.green()
            )
            
            await interaction.response.send_message(embed=success_embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"Error submitting suggestion: {str(e)}", ephemeral=True
            )


class Suggest(Cog):
    """
    Let your server members suggest stuff for your server.
    """

    def __init__(self, bot: Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Create tables if they don't exist."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS suggestion (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT,
                suggestion_id INTEGER DEFAULT 0,
                thread_enabled BOOLEAN DEFAULT FALSE,
                anonymous_allowed BOOLEAN DEFAULT FALSE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS suggestion_entries (
                guild_id BIGINT,
                message_id BIGINT,
                author_id BIGINT,
                suggestion_id INTEGER,
                is_anonymous BOOLEAN DEFAULT FALSE,
                PRIMARY KEY (guild_id, suggestion_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS suggestion_votes (
                guild_id BIGINT,
                message_id BIGINT,
                user_id BIGINT,
                vote_type INTEGER,
                PRIMARY KEY (message_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS suggestion_muted (
                guild_id BIGINT,
                user_id BIGINT,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    @group(name="suggestion", aliases=["suggest"], invoke_without_command=True)
    async def suggestion(self, ctx: Context):
        """Let your server members suggest stuff for your server."""
        await ctx.send_help(ctx.command)

    @has_permissions(manage_channels=True)
    @suggestion.command(name="channel", example="#suggestions")
    async def suggestion_add(self, ctx: Context, *, channel: Optional[TextChannel | Thread | ForumChannel]):
        """Set suggestions channel. Can be a text channel or forum channel."""
        if channel is None:
            channel = ctx.channel

        check = await self.bot.pool.fetchrow(
            "SELECT * FROM suggestion WHERE guild_id = $1", ctx.guild.id
        )
       
        if check is not None:
            await self.bot.pool.execute(
                "UPDATE suggestion SET channel_id = $1 WHERE guild_id = $2",
                channel.id, ctx.guild.id
            )
        else:
            await self.bot.pool.execute(
                "INSERT INTO suggestion (channel_id, guild_id) VALUES ($1, $2)",
                channel.id, ctx.guild.id
            )
        
        return await ctx.approve(f"I have set the suggestion channel to {channel.mention}.")
    
    @has_permissions(manage_channels=True)
    @suggestion.command(name="remove")
    async def suggestion_remove(self, ctx: Context):
        """Remove the suggestions config."""
        check = await self.bot.pool.fetchrow("SELECT * FROM suggestion WHERE guild_id = $1", ctx.guild.id)
        if check is None:
            return await ctx.warn("Suggestions are already disabled.")
        
        await ctx.prompt("Are you sure you would like to remove the suggestion system?")
        await self.bot.pool.execute("DELETE FROM suggestion WHERE guild_id = $1", ctx.guild.id)
        return await ctx.approve("Removed the suggestion channel.")
    
    @has_permissions(manage_messages=True)
    @suggestion.command(name="mute", example="43")
    async def suggestion_mute(self, ctx: Context, *, number: int):
        """Mute a member that send a specific suggestion."""
        check = await self.bot.pool.fetchrow(
            "SELECT channel_id FROM suggestion WHERE guild_id = $1", ctx.guild.id
        )
        if check is None:
            return await ctx.warn("Suggestions aren't **enabled** in this server.")

        entry = await self.bot.pool.fetchrow(
            """
            SELECT * FROM suggestion_entries 
            WHERE guild_id = $1 AND suggestion_id = $2
            """,
            ctx.guild.id, number
        )
        if entry is None:
            return await ctx.warn("I **couldn't** find that suggestion.")

        member_id = entry["author_id"]

        muted = await self.bot.pool.fetchrow(
            "SELECT * FROM suggestion_muted WHERE guild_id = $1 AND user_id = $2",
            ctx.guild.id, member_id
        )
        if muted:
            return await ctx.warn("This **member** is **already** suggestion muted.")

        await self.bot.pool.execute(
            "INSERT INTO suggestion_muted (guild_id, user_id) VALUES ($1, $2)",
            ctx.guild.id, member_id
        )
        return await ctx.approve(
            f"I have **muted** the author of suggestion #{number}."
        )

    @app_commands.command(name="suggest", description="Create a new suggestion")
    @app_commands.describe(
        description="Detailed explanation of your suggestion",
        title="Brief summary of your suggestion (optional)",
        image="Attach an image to your suggestion (optional)"
    )
    async def suggest_slash(
        self, 
        interaction: Interaction, 
        description: str,
        title: Optional[str] = None,
        image: Optional[discord.Attachment] = None
    ):
        suggestion_data = await self.bot.pool.fetchrow(
            """
            UPDATE suggestion 
            SET suggestion_id = COALESCE(suggestion_id, 0) + 1 
            WHERE guild_id = $1 
            RETURNING *
            """,
            interaction.guild.id
        )
        
        if not suggestion_data:
            return await interaction.response.send_message(
                "Suggestions are not enabled in this server!", ephemeral=True
            )

        if "http" in description.lower():
            return await interaction.response.send_message(
                "Links are not allowed in suggestions!", ephemeral=True
            )

        channel = interaction.guild.get_channel(suggestion_data["channel_id"])
        count = suggestion_data["suggestion_id"]
        is_anonymous = suggestion_data.get('anonymous_allowed', False)

        embed = discord.Embed(
            description=description,
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        
        if title:
            embed.title = title

        embed.set_author(
            name=f"Suggestion #{count}" + (" (Anonymous)" if is_anonymous else ""),
            icon_url=interaction.guild.icon
        )

        embed.add_field(
            name="Votes",
            value=f"👍 `0` • 👎 `0`",
            inline=False
        )

        if not is_anonymous:
            embed.set_footer(
                text=f"Suggested by {interaction.user}",
                icon_url=interaction.user.display_avatar.url
            )
        else:
            embed.set_footer(text="Anonymous Suggestion")

        if image:
            embed.set_image(url=image.url)

        msg = None
        if isinstance(channel, discord.ForumChannel):
            thread_name = title if title else f"Suggestion #{count}"
            if not title:
                thread_name = f"Suggestion #{count}"
            else:
                thread_name = f"{title} #{count}"
            
            thread = await channel.create_thread(
                name=thread_name,
                content="",
                embed=embed,
                view=SuggestionView(self.bot)
            )
            msg = thread.message
        else:
            msg = await channel.send(embed=embed, view=SuggestionView(self.bot))
            if suggestion_data.get("thread_enabled", False):
                await msg.create_thread(
                    name=f"Suggestion #{count} Discussion",
                    auto_archive_duration=1440
                )

        # Update suggestion_id was already done in the first query
        await self.bot.pool.execute(
            """INSERT INTO suggestion_entries 
            (guild_id, message_id, author_id, suggestion_id, is_anonymous)
            VALUES ($1, $2, $3, $4, $5)""",
            interaction.guild.id,
            msg.id if msg else 0,
            interaction.user.id,
            count,
            is_anonymous
        )

        success_embed = discord.Embed(
            title="✅ Suggestion Posted",
            description=f"Your suggestion has been posted in {channel.mention}",
            color=discord.Color.green()
        )
        
        await interaction.response.send_message(embed=success_embed, ephemeral=True)

    @app_commands.command(name="suggest_edit", description="Edit your previous suggestion")
    @app_commands.describe(
        suggestion_id="The ID of the suggestion to edit",
        new_title="New title for the suggestion (optional)",
        new_description="New description for the suggestion",
        new_image="New image for the suggestion (optional)"
    )
    async def suggest_edit(
        self,
        interaction: Interaction,
        suggestion_id: int,
        new_description: str,
        new_title: Optional[str] = None,
        new_image: Optional[discord.Attachment] = None
    ):
        """Edit a previous suggestion"""
        suggestion = await self.bot.pool.fetchrow(
            """
            SELECT se.*, s.channel_id 
            FROM suggestion_entries se
            JOIN suggestion s ON s.guild_id = se.guild_id
            WHERE se.guild_id = $1 
            AND se.suggestion_id = $2 
            AND se.author_id = $3
            AND se.is_anonymous = false
            """,
            interaction.guild.id,
            suggestion_id,
            interaction.user.id
        )

        if not suggestion:
            return await interaction.response.send_message(
                "You cannot edit this suggestion (it either doesn't exist, isn't yours, or was posted anonymously).",
                ephemeral=True
            )

        if "http" in new_description.lower():
            return await interaction.response.send_message(
                "Links are not allowed in suggestions", ephemeral=True
            )

        channel = interaction.guild.get_channel(suggestion["channel_id"])
        
        message = None
        if isinstance(channel, discord.ForumChannel):
            threads = [thread async for thread in channel.archived_threads(limit=None)]
            threads.extend(channel.threads)
            
            for thread in threads:
                if thread.name.startswith(f"Suggestion #{suggestion_id}"):
                    message = thread.starter_message or await thread.fetch_message(thread.id)
                    break
        else:
            try:
                message = await channel.fetch_message(suggestion["message_id"])
            except:
                pass

        if not message:
            return await interaction.response.send_message(
                "Could not find the suggestion message to edit.", ephemeral=True
            )

        embed = message.embeds[0]
        embed.title = new_title or embed.title
        embed.description = new_description
        
        if new_image:
            embed.set_image(url=new_image.url)

        await message.edit(embed=embed)
        
        if isinstance(channel, discord.ForumChannel) and message.thread:
            if new_title:
                new_thread_name = f"{new_title} #{suggestion_id}"
            else:
                new_thread_name = f"Suggestion #{suggestion_id}"
            await message.thread.edit(name=new_thread_name)

        return await interaction.response.send_message(
            "Your suggestion has been updated", ephemeral=True
        )

    @has_permissions(manage_channels=True)
    @suggestion.command(name="settings")
    async def suggestion_settings(self, ctx: Context):
        """View and modify suggestion settings"""
        settings = await self.bot.pool.fetchrow(
            "SELECT * FROM suggestion WHERE guild_id = $1", ctx.guild.id
        )
        
        if not settings:
            return await ctx.warn("Suggestions are not enabled in this server.")

        embed = discord.Embed(title="Suggestion Settings", color=discord.Color.blue())
        embed.add_field(
            name="Channel",
            value=f"<#{settings['channel_id']}>",
            inline=False
        )
        embed.add_field(
            name="Auto-Thread",
            value="✅ Enabled" if settings.get('thread_enabled') else "❌ Disabled",
            inline=True
        )
        embed.add_field(
            name="Anonymous Suggestions",
            value="✅ Allowed" if settings.get('anonymous_allowed') else "❌ Disabled",
            inline=True
        )
        
        await ctx.send(embed=embed)

    @has_permissions(manage_channels=True)
    @suggestion.command(name="threads")
    async def suggestion_threads(self, ctx: Context, enabled: bool):
        """Enable/disable automatic thread creation for suggestions"""
        await self.bot.pool.execute(
            "UPDATE suggestion SET thread_enabled = $1 WHERE guild_id = $2",
            enabled, ctx.guild.id
        )
        
        await ctx.approve(
            f"Auto-threads for suggestions have been {'enabled' if enabled else 'disabled'}."
        )

    @has_permissions(manage_channels=True)
    @suggestion.command(name="anonymous")
    async def suggestion_anonymous(self, ctx: Context, enabled: bool):
        """Enable/disable anonymous suggestions"""
        await self.bot.pool.execute(
            "UPDATE suggestion SET anonymous_allowed = $1 WHERE guild_id = $2",
            enabled, ctx.guild.id
        )
        
        await ctx.approve(
            f"Anonymous suggestions have been {'enabled' if enabled else 'disabled'}."
        )

async def setup(bot: Bot):
    bot.add_view(SuggestionView(bot))