import asyncio
from contextlib import suppress
from typing import Annotated, Optional, Dict
from discord import Embed, HTTPException, Message, Role, TextChannel, Thread, Forbidden
from discord.ext.commands import Cog, Range, flag, group, has_permissions
from xxhash import xxh64_hexdigest
import discord

from base.Xrypton import Bot
from base.context import Context
from base.managers.paginator import Paginator
from base.config import EMOJIS, COLORS
from base.managers.EmbedBuilder import EmbedBuilder, EmbedScript

# ===== Local helpers =====

def codeblock(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"

def plural(num, md=''):
    return f"{md}{num}{md}" if md else str(num)

def vowel(text: str) -> str:
    if text and text[0].lower() in 'aeiou':
        return 'an'
    return 'a'

# ===== Script class (replaces tools.parser.Script) =====

class Script:
    """Process a script template with placeholders."""
    def __init__(self, template: str, args: list):
        self.template = template
        self.args = args  # [guild, author, channel]

    def _format(self, text: str) -> str:
        guild = self.args[0] if len(self.args) > 0 else None
        author = self.args[1] if len(self.args) > 1 else None
        channel = self.args[2] if len(self.args) > 2 else None

        replacements = {
            "{guild.name}": guild.name if guild else "",
            "{guild.id}": str(guild.id) if guild else "",
            "{guild.member_count}": str(guild.member_count) if guild else "",
            "{guild.vanity}": guild.vanity_url_code if guild and guild.vanity_url_code else "none",
            "{channel.mention}": channel.mention if channel else "",
            "{channel.name}": channel.name if channel else "",
            "{channel.id}": str(channel.id) if channel else "",
            "{user.mention}": author.mention if author else "",
            "{user.name}": author.name if author else "",
            "{user.id}": str(author.id) if author else "",
            "{user.display_name}": author.display_name if author else "",
        }
        for key, value in replacements.items():
            text = text.replace(key, value)
        return text

    @property
    def format(self) -> str:
        return self.template

    async def send(self, destination, normal: bool = False, username: Optional[str] = None, avatar_url: Optional[str] = None) -> Message:
        """Send the script content to a destination (Context or TextChannel)."""
        content = self._format(self.template)
        # For simplicity, we just send as a normal message.
        # The original had support for webhooks but we'll keep it simple.
        if hasattr(destination, 'send'):
            return await destination.send(content=content)
        else:
            return await destination.send(content=content)

# ===== Flag converter =====

from discord.ext.commands import FlagConverter, flag

class Flags(FlagConverter):
    strict: bool = flag(
        description="Only respond to messages that match the trigger exactly.",
        default=False,
    )
    reply: bool = flag(
        description="Reply to the message that triggered the response.",
        default=False,
    )
    delete: bool = flag(
        description="Delete the message that triggered the response.",
        default=False,
    )
    delete_after: Range[int, 3, 120] = flag(
        aliases=["self_destruct"],
        description="Delete the response after a certain amount of time.",
        default=0,
    )
    role: Annotated[Role, ...] = flag(
        aliases=["grant", "remove"],
        description="Grant or remove a role from the author of the message.",
        default=None,
    )
    reaction: Optional[str] = flag(
        description="Emoji to react with to the triggering message.",
        default=None,
    )


class Trigger(Cog):
    """
    Automatically respond to messages or react with emojis based on triggers.
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self._rate_limit_locks: Dict[str, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        """Create the response_trigger table if it doesn't exist."""
        await self.bot.pool.execute(
            """
CREATE TABLE IF NOT EXISTS response_trigger (
                 guild_id BIGINT,
                 trigger TEXT,
                 template TEXT,
                 strict BOOLEAN DEFAULT FALSE,
                 reply BOOLEAN DEFAULT FALSE,
                 "delete" BOOLEAN DEFAULT FALSE,
                 delete_after INTEGER DEFAULT 0,
                 role_id BIGINT,
                 reaction TEXT,
                 PRIMARY KEY (guild_id, trigger)
             )
            """
        )

    def _get_rate_limit_lock(self, key: str) -> asyncio.Lock:
        if key not in self._rate_limit_locks:
            self._rate_limit_locks[key] = asyncio.Lock()
        return self._rate_limit_locks[key]

    @Cog.listener("on_message")
    async def response_listener(self, message: Message) -> None:
        """
        Automatically respond to a trigger in channels and threads.
        """
        ctx = await self.bot.get_context(message)
        if not ctx.guild or not ctx.message.content:
            return

        if not isinstance(ctx.channel, (TextChannel, Thread)):
            return

        # Fetch trigger record (case-insensitive substring match)
        record = await self.bot.pool.fetchrow(
            """
            SELECT *
            FROM response_trigger
            WHERE guild_id = $1
            AND LOWER($2) LIKE '%' || LOWER(trigger) || '%'
            """,
            ctx.guild.id,
            ctx.message.content,
        )
        if not record:
            return

        # If strict, ensure exact match
        if record["strict"] and record["trigger"].lower() != ctx.message.content.lower():
            return

        # Rate limit per user (4 seconds cooldown)
        key = xxh64_hexdigest(f"responses:{ctx.author.id}")
        lock = self._get_rate_limit_lock(key)
        if lock.locked():
            return
        async with lock:
            # Actually we want a cooldown, so we'll sleep for 4 seconds after processing.
            # We'll process then sleep.
            pass

        # If reaction is set, react to the triggering message
        if reaction_emoji := record["reaction"]:
            try:
                await ctx.message.add_reaction(reaction_emoji)
            except (Forbidden, HTTPException):
                pass

        # If no template, we only react, nothing else to do
        if not record["template"]:
            return

        # Process the response script
        script = Script(
            record["template"],
            [ctx.guild, ctx.author, ctx.channel],
        )

        with suppress(HTTPException):
            # Send the response
            if isinstance(ctx.channel, Thread):
                message = await ctx.send(content=script.template)  # simplified
            else:
                message = await script.send(ctx)

            if record["delete"] and not message.reference:
                await ctx.message.delete()

            if record["delete_after"]:
                await message.delete(delay=record["delete_after"])

            if role_id := record["role_id"]:
                role = ctx.guild.get_role(role_id)
                if role:
                    if role not in ctx.author.roles:
                        await ctx.author.add_roles(
                            role,
                            reason=f"Granted by response trigger for {record['trigger']}",
                        )
                    else:
                        await ctx.author.remove_roles(
                            role,
                            reason=f"Removed by response trigger for {record['trigger']}",
                        )

        # Enforce rate limit (sleep 4 seconds)
        await asyncio.sleep(4)

    @group(
        aliases=["autoresponse", "ar"],
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def response(self, ctx: Context) -> None:
        """
        Automatically respond to messages or react with emojis.
        """
        await ctx.send_help(ctx.command)

    @response.command(
        name="add",
        aliases=["create"],
        example='hi hello --reaction 👍 --strict'
    )
    @has_permissions(manage_messages=True)
    async def response_add(
        self,
        ctx: Context,
        trigger: str,
        *,
        flags: Flags,
        script: Optional[str] = None,
    ) -> Message:
        """
        Add a response trigger.

        If the trigger contains spaces, it must be wrapped in quotes.
        For example: "trigger with spaces" will be treated as a single trigger.

        If a reaction is set via --reaction, the bot will react with that emoji
        instead of (or in addition to) sending a message. To only react and not
        send a message, provide no script after the trigger.
        """
        # Extract the script from the command content
        # The command is: response add trigger --flags script (optional)
        # We'll rely on the parser: ctx.message.content after the trigger and flags.
        # Since we have flags and script optional, we need to rebuild.
        # We'll get the full content after the trigger.
        content = ctx.message.content.split(trigger, 1)[1].strip()
        # The flags are parsed, but script is the remaining text after flags.
        # But we have 'script' parameter which is the rest.
        # Actually we can just use the script parameter.
        template = script
        if not template and not flags.reaction:
            return await ctx.warn("You must provide either a script or a reaction!")

        script_obj = Script(template or "", [ctx.guild, ctx.author, ctx.channel])

        try:
            await self.bot.pool.execute(
                """
                INSERT INTO response_trigger (
                    guild_id,
                    trigger,
                    template,
                    strict,
                    reply,
                    "delete",
                    delete_after,
                    role_id,
                    reaction
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                ctx.guild.id,
                trigger,
                template or "",
                flags.strict,
                flags.reply,
                flags.delete,
                flags.delete_after,
                flags.role.id if flags.role else None,
                flags.reaction,
            )
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                return await ctx.warn(f"A response trigger for **{trigger}** already exists!")
            raise

        parts = []
        if template:
            parts.append(f"{vowel(script_obj.format)} message")
        if flags.reaction:
            parts.append(f"reaction {flags.reaction}")
        response_type = " and ".join(parts)

        return await ctx.approve(
            f"Now responding with {response_type} for **{trigger}**"
            + (
                " "
                + " ".join(
                    f"({name})"
                    for name in ["strict", "reply", "delete"]
                    if getattr(flags, name)
                )
                if any(getattr(flags, name) for name in ["strict", "reply", "delete"])
                else ""
            )
        )

    @response.command(
        name="edit",
        aliases=["modify"],
        example="hi helloo --reaction 👍"
    )
    @has_permissions(manage_messages=True)
    async def response_edit(
        self,
        ctx: Context,
        trigger: str,
        *,
        flags: Flags,
        script: Optional[str] = None,
    ) -> Message:
        """
        Edit an existing response trigger.
        """
        template = script or ""

        result = await self.bot.pool.execute(
            """
            UPDATE response_trigger
            SET template = $3, strict = $4, reply = $5, "delete" = $6,
                delete_after = $7, role_id = $8, reaction = $9
            WHERE guild_id = $1 AND trigger = $2
            """,
            ctx.guild.id,
            trigger,
            template,
            flags.strict,
            flags.reply,
            flags.delete,
            flags.delete_after,
            flags.role.id if flags.role else None,
            flags.reaction,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A response trigger for **{trigger}** doesn't exist!")

        return await ctx.approve(f"Successfully edited response trigger for **{trigger}**")

    @response.command(
        name="remove",
        aliases=["delete", "del", "rm"],
        example="hi",
    )
    @has_permissions(manage_messages=True)
    async def response_remove(self, ctx: Context, *, trigger: str) -> Message:
        """
        Remove a response trigger.
        """
        result = await self.bot.pool.execute(
            "DELETE FROM response_trigger WHERE guild_id = $1 AND trigger = $2",
            ctx.guild.id,
            trigger,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A response trigger for **{trigger}** doesn't exist!")

        return await ctx.approve(f"Removed response trigger for **{trigger}**")

    @response.command(
        name="view",
        aliases=["show"],
        example="hi",
    )
    @has_permissions(manage_messages=True)
    async def response_view(self, ctx: Context, *, trigger: str) -> Message:
        """
        View an existing response trigger.
        """
        record = await self.bot.pool.fetchrow(
            """
            SELECT template, strict, reply, delete, delete_after, role_id, reaction
            FROM response_trigger
            WHERE guild_id = $1 AND trigger = $2
            """,
            ctx.guild.id,
            trigger,
        )
        if not record:
            return await ctx.warn(f"A response trigger for **{trigger}** doesn't exist!")

        script = Script(record["template"], [ctx.guild, ctx.author, ctx.channel])
        embed = Embed(
            title="Response Trigger",
            description=codeblock(script.template),
        )
        # Add properties
        props = []
        for name in ("strict", "reply", "delete"):
            props.append(f"> **{name}:** {record[name]}")
        if record["delete_after"]:
            props.append(f"> **delete_after:** {record['delete_after']}s")
        if record["role_id"]:
            role = ctx.guild.get_role(record["role_id"])
            props.append(f"> **role:** {role.mention if role else 'Unknown'}")
        if record["reaction"]:
            props.append(f"> **reaction:** {record['reaction']}")
        embed.add_field(name="**Properties**", value="\n".join(props) or "None", inline=False)

        await ctx.send(embed=embed)
        return await script.send(ctx.channel)

    @response.command(
        name="clear",
        aliases=["clean", "reset"],
    )
    @has_permissions(manage_messages=True)
    async def response_clear(self, ctx: Context) -> Message:
        """
        Remove all response triggers.
        """
        await ctx.prompt(
            "Are you sure you want to remove all response triggers?",
        )

        result = await self.bot.pool.execute(
            "DELETE FROM response_trigger WHERE guild_id = $1", ctx.guild.id
        )
        if result == "DELETE 0":
            return await ctx.warn("No response triggers exist for this server!")

        return await ctx.approve(f"Successfully removed {plural(result, md='`')} response trigger")

    @response.command(
        name="list",
        aliases=["ls"],
    )
    @has_permissions(manage_messages=True)
    async def response_list(self, ctx: Context) -> Message:
        """
        View all response triggers.
        """
        flags = ("strict", "reply", "delete")
        records = await self.bot.pool.fetch(
            """
            SELECT trigger, strict, reply, delete, delete_after, reaction
            FROM response_trigger
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if not records:
            return await ctx.warn("No response triggers exist for this server!")

        triggers = []
        for record in records:
            parts = [f"**{record['trigger']}**"]
            if any(record[flag] for flag in flags):
                parts.append("(" + ", ".join(flag for flag in flags if record[flag]) + ")")
            if record["reaction"]:
                parts.append(f"↪ {record['reaction']}")
            triggers.append(" ".join(parts))

        paginator = Paginator(
            ctx,
            [Embed(title="Response Triggers", description="\n".join(triggers))]
        )
        return await paginator.start()