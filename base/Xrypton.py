import glob
import discord_ios
import os
import time
import pathlib
import re
import aiosqlite
import sqlite3
import hashlib
import inspect
import aiohttp
import json

from typing import List, Dict, Optional, Any
from datetime import datetime

from base.config import CLIENT
from .config import EMOJIS, COLORS, CLIENT as Config

def _parse_timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
        return None


sqlite3.register_converter("TIMESTAMP", _parse_timestamp)
sqlite3.register_adapter(datetime, lambda d: d.isoformat())



from discord import (
    AllowedMentions,
    Message,
    CustomActivity,
    Intents,
    Embed,
    Guild,
    Forbidden,
    Member,
)
from discord.utils import format_dt
from discord.ext.commands import (
    AutoShardedBot,
    when_mentioned_or,
    ExtensionFailed,
    CommandError,
    CheckFailure,
    CommandNotFound,
    NotOwner,
    CommandOnCooldown,
    ChannelNotFound,
    RoleNotFound,
    ThreadNotFound,
    UserNotFound,
    MissingPermissions,
    MinimalHelpCommand,
    CooldownMapping,
    BucketType,
    MissingRequiredArgument,
    UserInputError,
    BadArgument,
)

from loguru import logger
from base.context import Context, XryptonHelp
from collections import defaultdict
from os import environ

environ["JISHAKU_HIDE"] = "True"
environ["JISHAKU_RETAIN"] = "True"
environ["JISHAKU_NO_UNDERSCORE"] = "True"
environ["JISHAKU_SHELL_NO_DM_TRACEBACK"] = "True"


def _convert_query(query: str, params: tuple) -> tuple[str, list]:
    """Convert a PostgreSQL parameterized query to SQLite.

    Mirrors asyncpg positional semantics: ``$1`` refers to the first
    argument and may be reused multiple times in the query (each reuse maps
    to the same value). SQLite uses anonymous ``?`` placeholders whose values
    are consumed positionally, so we rebuild both the SQL and the parameter
    list by walking the query left-to-right.

    Also handles ``ANY($n::type[])`` with list arguments -> ``IN (?, ?, ...)``.
    """
    params_list = list(params)
    out_params: list = []

    any_re = re.compile(r"ANY\((\$\d+)(?::\w+\[\])?\)")

    def replace_any(match: "re.Match") -> str:
        n = int(match.group(1)[1:]) - 1
        value = params_list[n] if n < len(params_list) else []
        if isinstance(value, (list, tuple)):
            out_params.extend(value)
            return "IN (" + ",".join("?" * len(value)) + ")"
        return "?"

    query = any_re.sub(replace_any, query)

    token_re = re.compile(r"\$(\d+)")
    if not token_re.search(query):
        result = re.sub(r"::\w+(?:\[\])?", "", query)
        return result, params_list

    out_query: list = []
    pos = 0
    for m in token_re.finditer(query):
        out_query.append(query[pos : m.start()])
        n = int(m.group(1)) - 1
        out_query.append("?")
        out_params.append(params_list[n] if n < len(params_list) else None)
        pos = m.end()
    out_query.append(query[pos:])

    result = "".join(out_query)
    result = re.sub(r"::\w+(?:\[\])?", "", result)
    return result, out_params


class SqliteConnection:
    """Asyncpg-style connection wrapper around a single aiosqlite connection.

    Exposes ``fetchval``/``fetchrow``/``fetch``/``execute``/``transaction`` so
    code written against asyncpg (e.g. the economy cog) works unchanged.
    """

    def __init__(self, db: Any):
        self.db = db

    async def execute(self, query: str, *params) -> None:
        query, params = _convert_query(query, params)
        await self.db.execute(query, params)
        if not self.db.in_transaction:
            await self.db.commit()

    async def fetchval(self, query: str, *params) -> Optional[Any]:
        query, params = _convert_query(query, params)
        cursor = await self.db.execute(query, params)
        row = await cursor.fetchone()
        return row[0] if row else None

    async def fetchrow(self, query: str, *params) -> Optional[dict]:
        query, params = _convert_query(query, params)
        cursor = await self.db.execute(query, params)
        row = await cursor.fetchone()
        return dict(row) if row and hasattr(row, "keys") else (row if row else None)

    async def fetch(self, query: str, *params) -> List[dict]:
        query, params = _convert_query(query, params)
        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) if hasattr(row, "keys") else row for row in rows] if rows else []

    def transaction(self):
        return _SqliteTransaction(self)


class _SqliteTransaction:
    """Async context manager implementing BEGIN/COMMIT/ROLLBACK.

    aiosqlite's Connection does not expose ``transaction()`` (unlike asyncpg),
    so we manage transactions manually. Requires the connection to run in
    autocommit mode (``isolation_level=None``) so explicit BEGIN/COMMIT work.
    """

    def __init__(self, conn: "SqliteConnection"):
        self.conn = conn
        self.db = conn.db
        self._depth = 0

    async def __aenter__(self) -> "_SqliteTransaction":
        if self._depth == 0:
            await self.db.execute("BEGIN")
        self._depth += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._depth -= 1
        if self._depth == 0:
            if exc_type is not None:
                await self.db.execute("ROLLBACK")
            else:
                await self.db.commit()
        return False


class _ConnectionContext:
    """Async context manager yielding a :class:`SqliteConnection`.

    Mirrors asyncpg's ``pool.acquire()`` so code written against asyncpg can
    use ``async with pool.acquire() as conn:`` unchanged.
    """

    def __init__(self, db: Any):
        self.db = db

    async def __aenter__(self) -> "SqliteConnection":
        return SqliteConnection(self.db)

    async def __aexit__(self, *args) -> bool:
        return False


class SqlitePool:
    """Wrapper around aiosqlite to provide asyncpg-like interface."""

    def __init__(self, db: Any):
        self.db = db

    def acquire(self) -> _ConnectionContext:
        return _ConnectionContext(self.db)

    async def execute(self, query: str, *params) -> None:
        """Execute a query without returning results."""
        query, params = _convert_query(query, params)
        await self.db.execute(query, params)
        await self.db.commit()

    async def fetchval(self, query: str, *params) -> Optional[Any]:
        """Fetch a single value (first column of first row)."""
        query, params = _convert_query(query, params)
        cursor = await self.db.execute(query, params)
        row = await cursor.fetchone()
        return row[0] if row else None

    async def fetchrow(self, query: str, *params) -> Optional[dict]:
        """Fetch a single row as a dict."""
        query, params = _convert_query(query, params)
        cursor = await self.db.execute(query, params)
        row = await cursor.fetchone()
        return dict(row) if row and hasattr(row, "keys") else (row if row else None)

    async def fetch(self, query: str, *params) -> List[dict]:
        """Fetch all rows as dicts."""
        query, params = _convert_query(query, params)
        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) if hasattr(row, "keys") else row for row in rows] if rows else []


class Bot(AutoShardedBot):
    pool: SqlitePool
    boot: datetime

    def __init__(self) -> None:
        super().__init__(
            command_prefix=",",  # type: ignore
            case_insensitive=True,
            intents=Intents.all(),
            help_command=XryptonHelp(),
            allowed_mentions=AllowedMentions(
                everyone=False, roles=False, replied_user=False
            ),
            activity=CustomActivity(name="🔗 xrypton.vercel.app/discord"),
            owner_ids=[
                1470775670262202590,
                1467913714484121725 # remove if u want
            ],
        )
        self._uptime = time.time()
        self.message_cache = defaultdict(list)
        self.cache_expiry_seconds = 30
        self.member_cooldown = CooldownMapping.from_cooldown(3, 5, BucketType.user)
        self.channel_cooldown = CooldownMapping.from_cooldown(4, 5, BucketType.channel)

    @property
    def ping(self) -> int:
        return round(self.latency * 1000)

    async def get_color(self, user_id: int) -> int:
        """Return a deterministic embed colour for a user."""
        digest = hashlib.md5(str(user_id).encode("utf-8")).hexdigest()
        return int(digest[:6], 16) | 0x000000

    @property
    def booted(self):
        return format_dt(self.boot, style="R")

    @property
    def uptime(self) -> str:
        return self.humanize_time(self._uptime)

    @property
    def linecount(self) -> int:
        return sum([
            len(f.open("r", encoding="utf-8", errors="ignore").readlines())
            for f in [
                f
                for f in pathlib.Path(os.getcwd()).glob("**/*.py")
                if f.is_file()
                and ".venv" not in f.parts
                and ".local" not in f.parts
            ]
        ])

    async def get_prefix(self, message: Message) -> tuple:
        if message.guild is None:
            return  # type: ignore

        guild_prefix = (
            await self.pool.fetchval(
                "SELECT prefix FROM prefix WHERE guild_id = $1", message.guild.id
            )
            or ","
        )

        return (guild_prefix, guild_prefix)

    async def load_modules(self, directory: str = "cogs"):
        """
        Recursively load all modules in the directory selected.

        Only package entry points (``__init__.py``) and top-level modules are
        loaded as extensions. Submodules that live inside a cog package are
        imported by the package itself via relative imports, so loading them
        directly would cause circular imports and noisy errors.
        """
        for module in glob.glob(f"{directory}/**/*.py", recursive=True):
            path = pathlib.Path(module)
            if path.name != "__init__.py" and path.parent != pathlib.Path(directory):
                continue
            module_path = (
                module.replace("/", ".")
                .replace("\\", ".")
                .replace(".__init__", "")[:-3]
            )
            try:
                await self.load_extension(module_path)
                logger.info(f"Loaded extension: {module_path}")
            except ExtensionFailed as e:
                if "has no 'setup' function" in str(e):
                    logger.info(f"Loaded extension: {module_path}")
                else:
                    logger.warning(f"Extension failed to load: {module} - {e}")
            except Exception as e:
                if "has no 'setup' function" in str(e):
                    logger.info(f"Loaded extension: {module_path}")
                else:
                    logger.warning(f"Extension error: {module} - {e}")

    async def _load_database(self) -> SqlitePool:
        try:
            db_path = pathlib.Path("Xrypton.db")
            schema_path = pathlib.Path("base/schema/schema.sql")
            
            db = await aiosqlite.connect(
                str(db_path),
                detect_types=sqlite3.PARSE_DECLTYPES,
                isolation_level=None,
            )
            db.row_factory = aiosqlite.Row
            logger.info(f"Database connection established at {db_path}")

            if schema_path.exists():
                with open(schema_path, "r") as file:
                    schema = file.read()
                    if schema.strip():
                        statements = [s.strip() for s in schema.split(';') if s.strip()]
                        for statement in statements:
                            await db.execute(statement)
                        await db.commit()
                        logger.info("Database schema loaded")
                    else:
                        logger.warning("Database schema file is empty")
                    file.close()
            else:
                logger.warning(f"Schema file not found at {schema_path}")

            return SqlitePool(db)  # type: ignore
        except Exception as e:
            logger.error(f"Error loading database: {e}")
            raise e

    async def setup_hook(self):
        self.pool = await self._load_database()  # type: ignore
        self.config = Config()
        await self.load_modules()
        self.boot = datetime.now()
        await self.load_extension("jishaku")
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} application commands")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

        await self.sync_commands_to_api()

        return await super().setup_hook()

    def get_command_arguments(self, command):
        args = []
        try:
            if not command.callback:
                return args
            sig = inspect.signature(command.callback)
            params = list(sig.parameters.values())
            for param in params[2:]:
                arg = {"name": param.name}
                if param.default is inspect.Parameter.empty:
                    arg["required"] = True
                else:
                    arg["required"] = False
                    arg["default"] = repr(param.default)
                if param.annotation is not inspect.Parameter.empty:
                    arg["type"] = str(param.annotation).replace("typing.", "")
                else:
                    arg["type"] = "Any"
                args.append(arg)
        except (ValueError, TypeError):
            pass
        return args

    def get_command_schema(self):
        categories = {}
        for command in self.walk_commands():
            if command.cog_name in ("Jishaku",):
                continue
            if not command.callback:
                continue
            cog_name = command.cog_name or "Uncategorized"
            if cog_name not in categories:
                categories[cog_name] = []
            categories[cog_name].append({
                "name": command.qualified_name,
                "description": command.description or "",
                "arguments": self.get_command_arguments(command),
            })
        return [
            {"name": name, "commands": cmds}
            for name, cmds in categories.items()
        ]

    async def sync_commands_to_api(self):
        api_url = os.getenv("COMMANDS_API_URL") or getattr(CLIENT, "COMMANDS_API_URL", "")
        if not api_url:
            return
        try:
            payload = {
                "categories": self.get_command_schema(),
                "totalCommands": len([c for c in self.walk_commands() if c.cog_name != "Jishaku"]),
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        logger.info(f"Synced {payload['totalCommands']} commands to API")
                    else:
                        logger.warning(f"Failed to sync commands: {resp.status} {text}")
        except Exception as e:
            logger.error(f"Error syncing commands to API: {e}")

    def format_size(self, bytes):
        """
        Convert bytes to a human-readable format.
        """
        if bytes >= 1024**3:
            return f"{bytes / (1024**3):.2f}GB"
        elif bytes >= 1024**2:
            return f"{bytes / (1024**2):.2f}MB"
        elif bytes >= 1024:
            return f"{bytes / 1024:.2f}KB"
        else:
            return f"{bytes} Bytes"

    def humanize_number(self, number: int) -> str:
        suffixes = ["", "k", "m", "b", "t"]
        magnitude = min(len(suffixes) - 1, (len(str(abs(number))) - 1) // 3)
        formatted_number = (
            "{:.1f}".format(number / 10 ** (3 * magnitude)).rstrip("0").rstrip(".")
        )
        return "{}{}".format(formatted_number, suffixes[magnitude])

    async def get_context(self, message: Message, *, cls=Context):  # type: ignore
        return await super().get_context(message, cls=cls)

    def humanize_time(self, start_time: float) -> str:
        uptime_seconds = abs(time.time() - start_time)
        intervals = (
            ("year", 31556952),
            ("month", 2629746),
            ("day", 86400),
            ("hour", 3600),
            ("minute", 60),
            ("second", 1),
        )

        result = []
        for name, count in intervals:
            value = uptime_seconds // count
            if value:
                uptime_seconds -= value * count
                result.append(f"{int(value)} {name}{'s' if value > 1 else ''}")

        return ", ".join(result)

    async def on_command_error(self, ctx: Context, exception: CommandError) -> None:
        exception = getattr(exception, "original", exception)  # type: ignore
        if type(exception) in (NotOwner, CheckFailure):
            return

        elif isinstance(exception, CommandOnCooldown):
            return await ctx.cooldown(
                f"Please wait **{exception.retry_after:.2f} seconds** before using this command again."
            )  # type: ignore

        elif isinstance(exception, CheckFailure):
            if isinstance(exception, MissingPermissions):
                return await ctx.deny(
                    f"You're **missing** permission: `{', '.join(p for p in exception.missing_permissions)}`"  # type: ignore
                )

        elif isinstance(exception, CommandNotFound):
            alias = ctx.message.content[len(ctx.clean_prefix) :].split(" ")[0]
            check = await self.pool.fetchval(
                "SELECT command FROM aliases WHERE guild_id = $1 AND alias = $2",
                ctx.guild.id,
                alias,
            )
            if check:
                ctx.message.content = ctx.message.content.replace(alias, check, 1)
                return await self.process_commands(ctx.message)

            rp_cog = ctx.bot.get_cog("Roleplay")
            if rp_cog is not None:
                rp_group = ctx.bot.get_command("roleplay")
                if rp_group is not None:
                    for subcmd in rp_group.commands:
                        if subcmd.name == alias:
                            rest = ctx.message.content[len(ctx.clean_prefix) + len(alias):]
                            ctx.message.content = f"{ctx.clean_prefix}roleplay {alias}{rest}"
                            return await self.process_commands(ctx.message)

        elif isinstance(exception, MissingRequiredArgument):
            return await ctx.send_help(ctx.command)

        elif isinstance(exception, BadArgument):
            return await ctx.deny(exception.args[0])

        elif isinstance(exception, UserInputError):
            return await ctx.send_help(ctx.command)

        return await ctx.deny(f"{exception}")

    async def on_message_edit(self, before, after):
        if before.content == after.content:
            return
        if after.author.bot:
            return

        ctx = await self.get_context(after)
        if ctx.valid:
            await self.invoke(ctx)

    async def log_lost_boost(self, member: Member):
        try:
            query = """
            INSERT OR REPLACE INTO lost_boosters (guild_id, user_id, username, discriminator, lost_at)
            VALUES (?, ?, ?, ?, ?)
            """
            await self.pool.execute(
                query,
                member.guild.id,
                member.id,
                member.name,
                member.discriminator,
                datetime.utcnow(),
            )  # type: ignore
            print(f"Logged lost boost for {member.name}#{member.discriminator}")
        except Exception as e:
            print(f"Error logging lost boost: {e}")

    async def on_message(self, message: Message) -> None:
        if message.author.bot:
            return

        check = await self.pool.fetchrow(
            "SELECT * FROM blacklist WHERE user_id = $1", message.author.id
        )
        if check:
            return

        prefix = await self.get_prefix(message)
        if not message.content.startswith(tuple(prefix)):
            return

        now = time.time()
        author_id = message.author.id

        self.message_cache[author_id] = [
            timestamp
            for timestamp in self.message_cache[author_id]
            if now - timestamp < self.cache_expiry_seconds
        ]
        self.message_cache[author_id].append(now)

        await self.process_commands(message)

    async def on_command(self, ctx) -> None:
        logger.info(
            f"{ctx.author} ({ctx.author.id}) executed {ctx.command} in {ctx.guild} ({ctx.guild.id})."
        )

    async def on_shard_ready(self, ctx) -> None:
        logger.debug(f"Shard {self.shard_id} has spawned.")

    def member_ratelimit(self, message: Message) -> Optional[int]:
        bucket = self.member_cooldown.get_bucket(message)
        return bucket.update_rate_limit()  # type: ignore

    def channel_ratelimit(self, message: Message) -> Optional[int]:
        bucket = self.channel_cooldown.get_bucket(message)
        return bucket.update_rate_limit()  # type: ignore

    async def process_commands(
        self: "Bot",
        message: Message,
    ):
        if message.content.startswith(tuple(await self.get_prefix(message))):
            mcd = self.member_ratelimit(message)
            ccd = self.channel_ratelimit(message)

            if mcd or ccd:
                return

            return await super().process_commands(message)

    async def setup_hook(self):
        self.pool = await self._load_database()  # type: ignore
        self.config = Config()
        await self.load_modules()
        self.boot = datetime.now()
        await self.load_extension("jishaku")
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} application commands")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")