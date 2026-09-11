import asyncio
import discord
from discord.ext.commands import Cog, group, hybrid_group, has_permissions
from discord import TextChannel, Member, Embed

from base.context import Context
from base.config import COLORS, EMOJIS
from base.managers.EmbedBuilder import EmbedBuilder, send_embed

class WL(Cog):
    """Welcome and leave message management."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Create the welcome and leave tables."""
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS welcome (
                guild_id BIGINT,
                channel_id BIGINT,
                message TEXT,
                PRIMARY KEY (guild_id, channel_id)
            )
            """
        )
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS leave (
                guild_id BIGINT,
                channel_id BIGINT,
                message TEXT,
                PRIMARY KEY (guild_id, channel_id)
            )
            """
        )

    # ---------- WELCOME ----------
    @hybrid_group(
        name="welcome",
        aliases=["welc", "welcomer", "wlc"],
        invoke_without_command=True,
        description="Configure the welcome module",
    )
    @has_permissions(manage_messages=True)
    async def welcome(self, ctx: Context):
        return await ctx.send_help(ctx.command)

    @welcome.command(
        name="add", aliases=["create", "set"], description="Add a welcome message"
    )
    @has_permissions(manage_messages=True)
    async def welcome_add(
        self, ctx: Context, channel: discord.TextChannel, *, message: str
    ):
        processed_message = EmbedBuilder.embed_replacement(ctx.author, message)
        content, embed, view = await EmbedBuilder.to_object(processed_message)

        check = await self.bot.pool.fetchrow(
            "SELECT * FROM welcome WHERE channel_id = $1", channel.id
        )
        if check:
            await self.bot.pool.execute(
                "UPDATE welcome SET message = $1 WHERE channel_id = $2",
                message,
                channel.id,
            )
            await ctx.approve(f"Edited {channel.mention}'s welcome message to:")
            if content or embed:
                return await ctx.send(content=content, embed=embed, view=view)
            else:
                return await ctx.send(content=processed_message)

        if ctx.guild:
            await self.bot.pool.execute(
                "INSERT INTO welcome VALUES ($1, $2, $3)",
                ctx.guild.id,
                channel.id,
                message,
            )
            await ctx.approve(f"Added a welcome message in {channel.mention}.")
            if content or embed:
                return await ctx.send(content=content, embed=embed, view=view)
            else:
                return await ctx.send(content=processed_message)

    @welcome.command(
        name="remove", aliases=["delete", "del"], description="Remove a welcome message"
    )
    @has_permissions(manage_messages=True)
    async def welcome_remove(self, ctx: Context, *, channel: discord.TextChannel):
        if ctx.guild:
            data = await self.bot.pool.fetchrow(
                "SELECT * FROM welcome WHERE guild_id = $1 AND channel_id = $2",
                ctx.guild.id,
                channel.id,
            )
            if data:
                await self.bot.pool.execute(
                    "DELETE FROM welcome WHERE guild_id = $1 AND channel_id = $2",
                    ctx.guild.id,
                    channel.id,
                )
                await ctx.approve(
                    f"Removed the **welcome settings** from {channel.mention}!"
                )
            else:
                return await ctx.warn(
                    f"There are no **welcome settings** saved for {channel.mention}."
                )

    @welcome.command(
        name="view", aliases=["test"], description="Test a welcome message"
    )
    @has_permissions(manage_messages=True)
    async def welcome_test(self, ctx: Context, channel: discord.TextChannel):
        res = await self.bot.pool.fetchrow(
            "SELECT * from welcome WHERE guild_id = $1 AND channel_id = $2",
            ctx.guild.id,
            channel.id,
        )
        if res:
            channel_id = res["channel_id"]
            channel = ctx.guild.get_channel(channel_id)
            if channel is None:
                return
            message = res["message"]
            processed_message = EmbedBuilder.embed_replacement(ctx.author, message)
            content, embed, view = await EmbedBuilder.to_object(processed_message)
            if content or embed:
                await channel.send(content=content, embed=embed, view=view)
            else:
                await channel.send(content=processed_message)
        else:
            return

    @welcome.command(
        name="list", description="Show a list of channels with a welcome message."
    )
    @has_permissions(manage_messages=True)
    async def welcome_list(self, ctx: Context):
        if ctx.guild:
            res = await self.bot.pool.fetch(
                "SELECT channel_id, message FROM welcome WHERE guild_id = $1",
                ctx.guild.id,
            )
            if not res:
                return await ctx.warn(
                    "There are no welcome messages set up in this guild."
                )

            entries = [
                f"`{i}` {self.bot.get_channel(entry['channel_id']).mention if self.bot.get_channel(entry['channel_id']) else 'Channel ID: ' + str(entry['channel_id'])} (`{entry['channel_id']}`)"
                for i, entry in enumerate(res, start=1)
            ]

            embeds = []
            embed = discord.Embed(
                color=COLORS.neutral, title="Welcome channels", description=""
            )
            count = 0
            for entry in entries:
                embed.description += f"{entry}\n"
                count += 1
                if count == 5:
                    embed.set_footer(
                        text=f"Page {len(embeds) + 1}/{(len(entries) + 4) // 5} (entries: {len(entries)})"
                    )
                    embed.set_author(
                        name=ctx.author.display_name,
                        icon_url=ctx.author.display_avatar.url,
                    )
                    embeds.append(embed)
                    embed = discord.Embed(
                        color=COLORS.neutral, title="Welcome channels", description=""
                    )
                    count = 0

            if count > 0:
                embed.set_footer(
                    text=f"Page {len(embeds) + 1}/{(len(entries) + 4) // 5} ({len(entries)} entries)"
                )
                embed.set_author(
                    name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url
                )
                embeds.append(embed)

            if len(embeds) > 1:
                await ctx.paginate(embeds)
            else:
                await ctx.send(embed=embeds[0])

    @welcome.command(
        name="variables", description="Show available variables for welcome messages."
    )
    @has_permissions(manage_messages=True)
    async def welcome_variables(self, ctx: Context):
        embed = discord.Embed(
            color=COLORS.neutral,
            title="Welcome Variables",
            description="",
        )
        embed.add_field(
            name="User",
            value=(
                "`{user}`, `{user.name}`, `{user.mention}`, `{user.avatar}`, "
                "`{user.discriminator}`, `{user.joined_at}`, `{user.created_at}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Guild",
            value=(
                "`{guild.name}`, `{guild.count}`, `{guild.count.format}`, "
                "`{guild.id}`, `{guild.created_at}`, `{guild.boost_count}`, "
                "`{guild.boost_count.format}`, `{guild.booster_count}`, "
                "`{guild.booster_count.format}`, `{guild.boost_tier}`, "
                "`{guild.vanity}`, `{guild.icon}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Special",
            value="`{invisible}`, `{botcolor}`",
            inline=False,
        )
        await ctx.send(embed=embed)

    @Cog.listener("on_member_join")
    async def welcome_listener(self, member: discord.Member):
        res = await self.bot.pool.fetch(
            "SELECT * FROM welcome WHERE guild_id = $1", member.guild.id
        )
        for result in res:
            channel = self.bot.get_channel(result["channel_id"])
            if channel:
                processed_message = EmbedBuilder.embed_replacement(
                    member, result["message"]
                )
                content, embed, view = await EmbedBuilder.to_object(processed_message)
                if content or embed:
                    await channel.send(content=content, embed=embed, view=view)
                else:
                    await channel.send(content=processed_message)
                await asyncio.sleep(0.4)

    # ---------- LEAVE ----------
    @hybrid_group(
        name="leaves",
        aliases=["bye", "leave", "leaver", "goodbye"],
        description="Configure the leave messages.",
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def leaves(self, ctx: Context):
        return await ctx.send_help(ctx.command)

    @leaves.command(name="add", aliases=["set"], description="Add a leave message to a channel.")
    @has_permissions(manage_messages=True)
    async def leaves_add(self, ctx: Context, channel: TextChannel, *, message: str):
        query = "SELECT 1 FROM leave WHERE channel_id = $1"
        action = "Edited" if await self.bot.pool.fetchrow(query, channel.id) else "Set"

        await self.bot.pool.execute(
            "INSERT OR REPLACE INTO leave (guild_id, channel_id, message) VALUES (?, ?, ?)",
            ctx.guild.id,
            channel.id,
            message,
        )

        await ctx.approve(f"**{action}** {channel.mention}'s leave message to: ")
        await send_embed(ctx.channel, message, ctx.author)

    @leaves.command(name="remove", aliases=["delete", "rm"], description="Removes a channel's leave message")
    @has_permissions(manage_messages=True)
    async def leave_remove(self, ctx: Context, *, channel: TextChannel):
        if await self.bot.pool.fetchrow(
            "SELECT * FROM leave WHERE guild_id = $1 AND channel_id = $2",
            ctx.guild.id,
            channel.id,
        ):
            await self.bot.pool.execute(
                "DELETE FROM leave WHERE guild_id = $1 AND channel_id = $2",
                ctx.guild.id,
                channel.id,
            )
            return await ctx.approve(
                f"**Removed** the leave configuration from {channel.mention}."
            )
        else:
            return await ctx.warn(f"No **leave messages** found in that channel.")

    @leaves.command(name="test", aliases=["view"], description="Test a channel's leave message.")
    @has_permissions(manage_messages=True)
    async def leaves_view(self, ctx: Context, channel: TextChannel = None):
        if channel is None:
            channel = ctx.channel

        data = await self.bot.pool.fetch(
            "SELECT * FROM leave WHERE guild_id = $1", ctx.guild.id
        )

        for result in data:
            channel = self.bot.get_channel(result["channel_id"])
            if channel:
                await send_embed(channel, result["message"], ctx.author)
                await ctx.message.add_reaction(f"{EMOJIS.APPROVE}")
                await asyncio.sleep(0.5)

    @leaves.command(name="list")
    @has_permissions(manage_messages=True)
    async def leaves_list(self, ctx: Context):
        """List all leave messages set in the guild."""
        rows = await self.bot.pool.fetch(
            "SELECT channel_id, message FROM leave WHERE guild_id=$1",
            ctx.guild.id,
        )
        if not rows:
            return await ctx.warn("No leave messages are set in this guild.")

        embed = Embed(title="Leave Messages", color=COLORS.neutral)
        for index, row in enumerate(rows, start=1):
            channel = ctx.guild.get_channel(row["channel_id"])
            embed.add_field(
                name=f"`{index}.` {channel.mention if channel else 'Unknown Channel'} (`{channel.id}`)",
                value="",
                inline=False,
            )

        await ctx.send(embed=embed)

    @leaves.command(
        name="variables", description="Show available variables for leave messages."
    )
    @has_permissions(manage_messages=True)
    async def leaves_variables(self, ctx: Context):
        embed = discord.Embed(
            color=COLORS.neutral,
            title="Leave Variables",
            description="",
        )
        embed.add_field(
            name="User",
            value=(
                "`{user}`, `{user.name}`, `{user.mention}`, `{user.avatar}`, "
                "`{user.discriminator}`, `{user.joined_at}`, `{user.created_at}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Guild",
            value=(
                "`{guild.name}`, `{guild.count}`, `{guild.count.format}`, "
                "`{guild.id}`, `{guild.created_at}`, `{guild.boost_count}`, "
                "`{guild.boost_count.format}`, `{guild.booster_count}`, "
                "`{guild.booster_count.format}`, `{guild.boost_tier}`, "
                "`{guild.vanity}`, `{guild.icon}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Special",
            value="`{invisible}`, `{botcolor}`",
            inline=False,
        )
        await ctx.send(embed=embed)

    @Cog.listener("on_member_remove")
    async def dispatch_leaves(self, member: Member):
        """Dispatches the leave messages."""
        data = await self.bot.pool.fetch(
            "SELECT * FROM leave WHERE guild_id = $1", member.guild.id
        )
        for result in data:
            channel = self.bot.get_channel(result["channel_id"])
            if channel:
                await send_embed(channel, result["message"], member)
                await asyncio.sleep(0.5)