import re
import discord
from contextlib import suppress
from logging import getLogger
from typing import Annotated, Optional, cast

from discord import Embed, HTTPException, Message, RawReactionActionEvent, Role, Member
from discord.ext.commands import Cog, group, has_permissions, BadArgument, Converter

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import EMOJIS, COLORS

log = getLogger("xrypton/roles")

# ===== StrictRole Converter =====
class StrictRole(Converter):
    """A role converter that checks hierarchy and dangerous permissions."""
    def __init__(self, check_dangerous: bool = False):
        self.check_dangerous = check_dangerous

    async def convert(self, ctx: Context, argument: str) -> Role:
        role = await discord.RoleConverter().convert(ctx, argument)
        if not role.is_assignable():
            raise BadArgument("I cannot assign that role (hierarchy/permissions).")
        if role.is_integrated():
            raise BadArgument("That role is integrated and cannot be managed.")
        if self.check_dangerous:
            # Check for dangerous permissions
            dangerous_perms = [
                "administrator", "ban_members", "kick_members", "manage_guild",
                "manage_channels", "manage_roles", "manage_messages", "manage_webhooks",
                "manage_emojis", "manage_threads", "mention_everyone", "moderate_members"
            ]
            if any(getattr(role.permissions, perm, False) for perm in dangerous_perms):
                raise BadArgument("That role has dangerous permissions and cannot be used for self-assignment.")
        return role

# ===== DynamicRoleButton =====
class DynamicRoleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"RB:(?P<message_id>[0-9]+):(?P<role_id>[0-9]+)"):
    def __init__(self, message_id: int, role_id: int, emoji: discord.Emoji) -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label=None,
                emoji=emoji,
                custom_id=f"RB:{message_id}:{role_id}",
            )
        )
        self.message_id: int = message_id
        self.role_id: int = role_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        message_id = int(match["message_id"])
        role_id = int(match["role_id"])
        return cls(message_id, role_id, item.emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            return await interaction.warn("This role button no longer exists.", ephemeral=True)

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            return await interaction.warn("Could not find you in this server.", ephemeral=True)

        if role in member.roles:
            await member.remove_roles(role)
            return await interaction.approve(f"**Removed** role {role.mention} from you.")
        else:
            await member.add_roles(role)
            return await interaction.approve(f"**Added** role {role.mention} to you.")

# ===== Main Cog =====
class Roles(Cog):
    """
    Reaction roles, button roles, and auto-role assignment.
    """

    def __init__(self, bot: Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Create necessary tables."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS reaction_role (
                guild_id BIGINT,
                channel_id BIGINT,
                message_id BIGINT,
                role_id BIGINT,
                emoji TEXT,
                PRIMARY KEY (guild_id, message_id, emoji)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS autorole (
                guild_id BIGINT PRIMARY KEY,
                role_id BIGINT
            )
            """
        ]
        for q in queries:
            await self.bot.pool.execute(q)

    # ========== Reaction Roles ==========
    @group(aliases=["rr"], invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def reactionrole(self, ctx: Context) -> Message:
        """Allow members to assign roles to themselves via reactions."""
        return await ctx.send_help(ctx.command)

    @reactionrole.command(name="add", aliases=["create"], example="(message) (emoji) (role)")
    @has_permissions(manage_roles=True)
    async def reactionrole_add(
        self,
        ctx: Context,
        message: Message,
        emoji: str,
        *,
        role: Annotated[Role, StrictRole(check_dangerous=True)],
    ) -> Message:
        """Add a new reaction role to a message."""
        if message.guild != ctx.guild:
            return await ctx.warn("The message must be in this server!")

        try:
            await message.add_reaction(emoji)
        except (HTTPException, TypeError):
            return await ctx.warn(
                "I couldn't add the reaction to the message!",
                "If you're using a custom emoji from another server,",
                "you can react with it first and then run this command",
            )

        try:
            await self.bot.pool.execute(
                """
                INSERT INTO reaction_role (guild_id, channel_id, message_id, role_id, emoji)
                VALUES ($1, $2, $3, $4, $5)
                """,
                ctx.guild.id, message.channel.id, message.id, role.id, emoji
            )
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                return await ctx.warn("That reaction role already exists!")
            raise

        return await ctx.approve(
            f"Now assigning {role.mention} for **{emoji}** on [`{message.id}`]({message.jump_url})"
        )

    @reactionrole.command(name="remove", aliases=["delete", "del", "rm"], example="(message) (emoji)")
    @has_permissions(manage_roles=True)
    async def reactionrole_remove(self, ctx: Context, message: Message, emoji: str) -> Message:
        """Remove a reaction role from a message."""
        if message.guild != ctx.guild:
            return await ctx.warn("The message must be in this server!")

        result = await self.bot.pool.execute(
            """
            DELETE FROM reaction_role
            WHERE guild_id = $1 AND message_id = $2 AND emoji = $3
            """,
            ctx.guild.id, message.id, emoji
        )
        if result == "DELETE 0":
            return await ctx.warn("That reaction role doesn't exist!")

        return await ctx.approve(
            f"No longer assigning a role for **{emoji}** on [`{message.id}`]({message.jump_url})"
        )

    @reactionrole.command(name="clear", aliases=["clean", "reset"])
    @has_permissions(manage_roles=True)
    async def reactionrole_clear(self, ctx: Context, message: Optional[Message] = None) -> Message:
        """Remove all reaction roles (optionally for a specific message)."""
        if message is None:
            await ctx.prompt("Are you sure you want to remove all reaction roles?")
            result = await self.bot.pool.execute(
                "DELETE FROM reaction_role WHERE guild_id = $1", ctx.guild.id
            )
            if result == "DELETE 0":
                return await ctx.warn("No reaction roles exist for this server!")
            return await ctx.approve("Successfully removed all reaction roles")

        if message.guild != ctx.guild:
            return await ctx.warn("The message must be in this server!")

        result = await self.bot.pool.execute(
            "DELETE FROM reaction_role WHERE guild_id = $1 AND message_id = $2",
            ctx.guild.id, message.id
        )
        if result == "DELETE 0":
            return await ctx.warn(f"No reaction roles exist for that [`message`]({message.jump_url})!")
        return await ctx.approve(f"Successfully removed all reaction roles from [`{message.id}`]({message.jump_url})")

    @reactionrole.command(name="list", aliases=["ls"])
    @has_permissions(manage_roles=True)
    async def reactionrole_list(self, ctx: Context) -> Message:
        """View all reaction roles."""
        records = await self.bot.pool.fetch(
            """
            SELECT channel_id, message_id, role_id, emoji
            FROM reaction_role
            WHERE guild_id = $1
            """, ctx.guild.id
        )
        entries = []
        for record in records:
            channel = ctx.guild.get_channel(record["channel_id"])
            if not channel:
                continue
            msg = channel.get_partial_message(record["message_id"])
            if not msg:
                continue
            role = ctx.guild.get_role(record["role_id"])
            if not role:
                continue
            entries.append(
                f"[`{record['message_id']}`]({msg.jump_url}) - {role.mention} for **{record['emoji']}**"
            )
        if not entries:
            return await ctx.warn("No reaction roles exist for this server!")

        embed = Embed(title="Reaction Roles", description="\n".join(entries))
        paginator = Paginator(ctx, [embed])
        return await paginator.start()

    @Cog.listener("on_raw_reaction_add")
    async def reactionrole_event(self, payload: RawReactionActionEvent) -> None:
        """Assign a role when a reaction is added."""
        guild = payload.guild_id and self.bot.get_guild(payload.guild_id)
        member = payload.member or (guild and guild.get_member(payload.user_id))
        if not guild or not member or member.bot:
            return

        role_id = await self.bot.pool.fetchval(
            """
            SELECT role_id FROM reaction_role
            WHERE guild_id = $1 AND message_id = $2 AND emoji = $3
            """,
            guild.id, payload.message_id, str(payload.emoji)
        )
        if role_id is None:
            return
        if not guild.me.guild_permissions.manage_roles:
            return
        role = guild.get_role(role_id)
        if not role or role >= guild.me.top_role or role in member.roles:
            return

        with suppress(HTTPException):
            await member.add_roles(role, reason="Reaction role")

    @Cog.listener("on_raw_reaction_remove")
    async def reactionrole_remove_event(self, payload: RawReactionActionEvent) -> None:
        """Remove a role when a reaction is removed."""
        guild = payload.guild_id and self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        role_id = await self.bot.pool.fetchval(
            """
            SELECT role_id FROM reaction_role
            WHERE guild_id = $1 AND message_id = $2 AND emoji = $3
            """,
            guild.id, payload.message_id, str(payload.emoji)
        )
        if role_id is None:
            return
        if not guild.me.guild_permissions.manage_roles:
            return
        role = guild.get_role(role_id)
        if not role or role >= guild.me.top_role or role not in member.roles:
            return

        with suppress(HTTPException):
            await member.remove_roles(role, reason="Reaction role")

    # ========== Button Roles ==========
    @group(name="rolebutton", invoke_without_command=True)
    @has_permissions(manage_messages=True)
    async def rolebutton(self, ctx: Context) -> Message:
        """Allow members to assign roles to themselves via buttons."""
        return await ctx.send_help(ctx.command)

    @rolebutton.command(name="add", example="(message) (emoji) (role)")
    @has_permissions(manage_messages=True)
    async def rolebutton_add(
        self,
        ctx: Context,
        message: Message,
        emoji: str,
        role: Annotated[Role, StrictRole(check_dangerous=True)],
    ) -> Message:
        """Add a role button to a message."""
        prefix = await self.bot.get_prefix(ctx.message)
        if message.author.id != self.bot.user.id:
            return await ctx.warn(
                f"I can only add role **buttons** to my own **messages**. "
                f"You can create an **embed** using `{prefix}createembed (code)` "
                f"and add the **button** there."
            )

        if role.is_premium_subscriber():
            return await ctx.warn("I cannot assign integrated roles to users.")

        view = discord.ui.View()
        for component in message.components:
            if isinstance(component, discord.components.ActionRow):
                for button in component.children:
                    if button.custom_id == f"RB:{message.id}:{role.id}":
                        return await ctx.warn(
                            f"**Role** {role.mention} is already **assigned** to this **message**."
                        )
                    if button.custom_id.startswith("RB"):
                        # Parse existing button
                        parts = button.custom_id.split(":")
                        if len(parts) == 3:
                            view.add_item(
                                DynamicRoleButton(
                                    message_id=int(parts[1]),
                                    role_id=int(parts[2]),
                                    emoji=button.emoji,
                                )
                            )
                        else:
                            # fallback: just add a normal button (shouldn't happen)
                            view.add_item(
                                discord.ui.Button(
                                    style=button.style,
                                    label=button.label,
                                    emoji=button.emoji,
                                    url=button.url,
                                    disabled=button.disabled,
                                )
                            )
                    else:
                        view.add_item(
                            discord.ui.Button(
                                style=button.style,
                                label=button.label,
                                emoji=button.emoji,
                                url=button.url,
                                disabled=button.disabled,
                            )
                        )

        view.add_item(DynamicRoleButton(message.id, role.id, emoji))
        await message.edit(view=view)
        return await ctx.approve(
            f"Added **role** {role.mention} to [**message**]({message.jump_url})"
        )

    @rolebutton.command(name="remove", example="(message) (role)")
    @has_permissions(manage_messages=True)
    async def rolebutton_remove(self, ctx: Context, message: Message, role: Role) -> Message:
        """Remove a role button from a message."""
        prefix = await self.bot.get_prefix(ctx.message)
        if message.author.id != self.bot.user.id:
            return await ctx.warn(
                f"I can only remove role **buttons** from my own **messages**. "
                f"You can create an **embed** using `{prefix}createembed (code)` "
                f"and manage the button there."
            )

        view = discord.ui.View()
        removed = False
        for component in message.components:
            if isinstance(component, discord.components.ActionRow):
                for button in component.children:
                    if button.custom_id == f"RB:{message.id}:{role.id}":
                        removed = True
                        continue
                    if button.custom_id.startswith("RB"):
                        parts = button.custom_id.split(":")
                        if len(parts) == 3:
                            view.add_item(
                                DynamicRoleButton(
                                    message_id=int(parts[1]),
                                    role_id=int(parts[2]),
                                    emoji=button.emoji,
                                )
                            )
                        else:
                            view.add_item(
                                discord.ui.Button(
                                    style=button.style,
                                    label=button.label,
                                    emoji=button.emoji,
                                    url=button.url,
                                    disabled=button.disabled,
                                )
                            )
                    else:
                        view.add_item(
                            discord.ui.Button(
                                style=button.style,
                                label=button.label,
                                emoji=button.emoji,
                                url=button.url,
                                disabled=button.disabled,
                            )
                        )

        if not removed:
            return await ctx.warn(
                f"No button for **{role.mention}** found on that message."
            )

        await message.edit(view=view)
        return await ctx.approve(
            f"Removed **role** {role.mention} from [**message**]({message.jump_url})"
        )

    # ========== Autorole ==========
    @group(name="autorole", invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def autorole(self, ctx: Context) -> Message:
        """Set a role that will be given to new members automatically."""
        record = await self.bot.pool.fetchrow(
            "SELECT role_id FROM autorole WHERE guild_id = $1", ctx.guild.id
        )
        if not record:
            return await ctx.warn("No autorole is set for this server.")
        role = ctx.guild.get_role(record["role_id"])
        if not role:
            return await ctx.warn("The autorole role no longer exists. Please remove and re-add.")
        return await ctx.approve(f"Current autorole is {role.mention}")

    @autorole.command(name="set", example="@Member")
    @has_permissions(manage_roles=True)
    async def autorole_set(
        self,
        ctx: Context,
        role: Annotated[Role, StrictRole(check_dangerous=True)],
    ) -> Message:
        """Set the role to give to new members."""
        if role == ctx.guild.default_role:
            return await ctx.warn("You cannot assign the @everyone role.")

        await self.bot.pool.execute(
            """
            INSERT INTO autorole (guild_id, role_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET role_id = EXCLUDED.role_id
            """,
            ctx.guild.id, role.id
        )
        return await ctx.approve(f"New members will now receive {role.mention}")

    @autorole.command(name="remove", aliases=["delete", "del", "rm"])
    @has_permissions(manage_roles=True)
    async def autorole_remove(self, ctx: Context) -> Message:
        """Remove the autorole configuration."""
        result = await self.bot.pool.execute(
            "DELETE FROM autorole WHERE guild_id = $1", ctx.guild.id
        )
        if result == "DELETE 0":
            return await ctx.warn("No autorole is set for this server.")
        return await ctx.approve("Autorole has been removed.")

    @autorole.command(name="list", aliases=["ls"])
    @has_permissions(manage_roles=True)
    async def autorole_list(self, ctx: Context) -> Message:
        """List all servers with autorole? (This is per-server, so just show current.)"""
        # Since autorole is per-guild, we just show the current one via the main command.
        return await self.autorole(ctx)

    @Cog.listener("on_member_join")
    async def autorole_assign(self, member: Member) -> None:
        """Assign the autorole to a new member."""
        if member.bot:
            return
        record = await self.bot.pool.fetchrow(
            "SELECT role_id FROM autorole WHERE guild_id = $1", member.guild.id
        )
        if not record:
            return
        role = member.guild.get_role(record["role_id"])
        if not role:
            # Role might have been deleted; clean up
            await self.bot.pool.execute(
                "DELETE FROM autorole WHERE guild_id = $1", member.guild.id
            )
            return
        if not member.guild.me.guild_permissions.manage_roles:
            return
        if role >= member.guild.me.top_role:
            return
        with suppress(HTTPException):
            await member.add_roles(role, reason="Autorole")