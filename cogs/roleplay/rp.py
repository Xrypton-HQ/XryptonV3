import random
import discord
import io
from datetime import datetime, timedelta
from typing import Union, Optional, cast

from discord import Embed, Member, User, Message
from discord.ext.commands import (
    Cog,
    hybrid_command,
    command,
    BucketType,
    cooldown,
    parameter,
    hybrid_group,
    is_nsfw,
    group,
)
from aiohttp import ClientSession, TCPConnector
from discord.ext import commands
from base.Xrypton import Bot
from base.context import Context
from base.config import CLIENT

log = None  # We'll skip logging for now

# ---- Helper: ordinal ----
def ordinal(n: int) -> str:
    """Return ordinal string for an integer."""
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

# ---- Base URLs ----
BASE_URL = "https://nekos.best"
BASE_URL1 = "https://purrbot.site"
BASE_URL2 = "https://api.waifu.pics"

# ---- Actions mapping ----
ACTIONS = {
    "bite": "bites",
    "cuddle": "cuddles",
    "feed": "feeds",
    "hug": "hugs",
    "kiss": "kisses",
    "pat": "pats",
    "poke": "pokes",
    "punch": "punches",
    "slap": "slaps",
    "smug": "smugs at",
    "tickle": "tickles",
    "neko": "shows a neko to",
    "waifu": "shows a waifu to",
    "husbando": "shows a husbando to",
    "kitsune": "shows a kitsune to",
    "lurk": "lurks at",
    "shoot": "shot",
    "sleep": "sleeps with",
    "shrug": "shrugs at",
    "stare": "stares at",
    "wave": "waves at",
    "smile": "smiles at",
    "peck": "pecked",
    "wink": "winks at",
    "blush": "blushes at",
    "yeet": "yeets",
    "think": "thinks about",
    "highfive": "high-fives",
    "bored": "is bored with",
    "nom": "noms",
    "yawn": "yawns at",
    "facepalm": "facepalms at",
    "happy": "is happy with",
    "baka": "calls baka",
    "nod": "nods at",
    "nope": "nopes at",
    "dance": "dances with",
    "handshake": "shakes hands with",
    "cry": "cries with",
    "pout": "pouts at",
    "handhold": "holds hands with",
    "thumbsup": "gives a thumbs up to",
    "laugh": "laughs with",
    "anal": "fucked",
    "blowjob": "sucked off",
    "cum": "cums on",
    "pussylick": "eats out",
    "threesome_ffm": "has a threesome",
    "threesome_fmm": "has a threesome",
    "threesome_fff": "has a threesome",
    "lick": "licks",
    "bully": "bullies",
    "bonk": "bonks",
    "cringe": "cringes at",
    "awoo": "awoos at"
}

# ---- Rate limit decorator (using SQLite) ----
def roleplay_ratelimit():
    async def predicate(ctx: Context):
        user_id = ctx.author.id
        # Get current time
        now = datetime.utcnow()
        # We'll store in table: user_id (primary key), count, timestamp (last reset)
        # Check if user exists
        row = await ctx.bot.pool.fetchrow(
            "SELECT count, timestamp FROM roleplay_ratelimit WHERE user_id = $1",
            user_id
        )
        if row:
            # If more than 60 seconds have passed, reset
            if (now - row['timestamp']).total_seconds() >= 60:
                # Reset count to 1 and update timestamp
                await ctx.bot.pool.execute(
                    "UPDATE roleplay_ratelimit SET count = 1, timestamp = $1 WHERE user_id = $2",
                    now, user_id
                )
                return True
            else:
                # Within window
                if row['count'] >= 15:
                    # Rate limited
                    if ctx.interaction:
                        await ctx.warn("You can only use roleplay commands 15 times per minute.")
                    else:
                        await ctx.message.add_reaction("⏰")
                    return False
                else:
                    # Increment count
                    await ctx.bot.pool.execute(
                        "UPDATE roleplay_ratelimit SET count = count + 1 WHERE user_id = $1",
                        user_id
                    )
                    return True
        else:
            # Insert new record with count 1 and current timestamp
            await ctx.bot.pool.execute(
                "INSERT INTO roleplay_ratelimit (user_id, count, timestamp) VALUES ($1, 1, $2)",
                user_id, now
            )
            return True

    return commands.check(predicate)

# ---- The Cog ----
class Roleplay(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.description = "Perform different actions on other members."

    async def cog_load(self):
        """Create the rate limit table if it doesn't exist."""
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS roleplay_ratelimit (
                user_id BIGINT PRIMARY KEY,
                count INTEGER NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )
            """
        )

    # ---- Helper methods ----

    async def waifu_send(self, ctx: Context, member: Optional[Member], category: str) -> Message:
        """Requests waifu.pics API and sends embed."""
        # Ratelimit check is done by decorator, but we still need to use the API
        url = f"{BASE_URL2}/sfw/{category}"
        async with ctx.typing():
            connector = TCPConnector()
            async with ClientSession(connector=connector) as session:
                async with session.get(url, proxy=None) as response:
                    try:
                        data = await response.json()
                        if not data.get("url"):
                            return await ctx.warn("Something went wrong, please try again later!")
                    except Exception:
                        return await ctx.warn("Something went wrong, please try again later!")

                    embed = Embed()
                    if member:
                        amount = 0
                        if member != ctx.author:
                            amount = cast(
                                int,
                                await self.bot.pool.fetchval(
                                    """
                                    INSERT INTO roleplay (user_id, target_id, category)
                                    VALUES ($1, $2, $3)
                                    ON CONFLICT (user_id, target_id, category)
                                    DO UPDATE SET amount = roleplay.amount + 1
                                    RETURNING amount
                                    """,
                                    ctx.author.id,
                                    member.id,
                                    category,
                                ),
                            )
                        embed.description = (
                            f"{ctx.author.mention} just {ACTIONS[category]} {member.mention}"
                            + (
                                f" for the **{ordinal(amount)}** time"
                                if member != ctx.author and amount
                                else ""
                            )
                        )
                    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar)
                    embed.set_image(url=data["url"])
                    return await ctx.send(embed=embed)

    async def send(self, ctx: Context, member: Optional[Member], category: str) -> Message:
        """Requests nekos.best API and sends embed."""
        # Ratelimit is handled by decorator, so we just fetch
        url = f"{BASE_URL}/api/v2/{category}"
        async with ctx.typing():
            connector = TCPConnector()
            async with ClientSession(connector=connector) as session:
                async with session.get(url, proxy=None) as response:
                    try:
                        data = await response.json()
                        if not data.get("results"):
                            return await ctx.warn("Something went wrong, please try again later!")
                    except Exception:
                        return await ctx.warn("Something went wrong, please try again later!")

                    embed = Embed()
                    if member:
                        amount = 0
                        if member != ctx.author:
                            amount = cast(
                                int,
                                await self.bot.pool.fetchval(
                                    """
                                    INSERT INTO roleplay (user_id, target_id, category)
                                    VALUES ($1, $2, $3)
                                    ON CONFLICT (user_id, target_id, category)
                                    DO UPDATE SET amount = roleplay.amount + 1
                                    RETURNING amount
                                    """,
                                    ctx.author.id,
                                    member.id,
                                    category,
                                ),
                            )
                        embed.description = (
                            f"{ctx.author.mention} just {ACTIONS[category]} {member.mention}"
                            + (
                                f" for the **{ordinal(amount)}** time"
                                if member != ctx.author and amount
                                else ""
                            )
                        )
                    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar)
                    embed.set_image(url=data["results"][0]["url"])
                    return await ctx.send(embed=embed)

    async def purrbot_send(self, ctx: Context, member: Optional[Member], category: str) -> Message:
        """Requests purrbot.site API (NSFW) and sends embed."""
        url = f"{BASE_URL1}/api/img/nsfw/{category}/gif"
        async with ctx.typing():
            connector = TCPConnector()
            async with ClientSession(connector=connector) as session:
                async with session.get(url, proxy=None) as response:
                    try:
                        data = await response.json()
                        if not data.get("link"):
                            return await ctx.warn("Something went wrong, please try again later!")
                    except Exception:
                        return await ctx.warn("Something went wrong, please try again later!")

                    embed = Embed()
                    if member:
                        amount = 0
                        if member != ctx.author:
                            amount = cast(
                                int,
                                await self.bot.pool.fetchval(
                                    """
                                    INSERT INTO roleplay (user_id, target_id, category)
                                    VALUES ($1, $2, $3)
                                    ON CONFLICT (user_id, target_id, category)
                                    DO UPDATE SET amount = roleplay.amount + 1
                                    RETURNING amount
                                    """,
                                    ctx.author.id,
                                    member.id,
                                    category,
                                ),
                            )
                        embed.description = (
                            f"{ctx.author.mention} just {ACTIONS[category]} {member.mention}"
                            + (
                                f" for the **{ordinal(amount)}** time"
                                if member != ctx.author and amount
                                else ""
                            )
                        )
                    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar)
                    embed.set_image(url=data["link"])
                    return await ctx.send(embed=embed)

    async def threesome_send(self, ctx: Context, member1: Member, member2: Member, category: str) -> Message:
        """Threesome action (purrbot)."""
        url = f"{BASE_URL1}/api/img/nsfw/{category}/gif"
        async with ctx.typing():
            connector = TCPConnector()
            async with ClientSession(connector=connector) as session:
                async with session.get(url, proxy=None) as response:
                    try:
                        data = await response.json()
                        if not data.get("link"):
                            return await ctx.warn("Something went wrong, please try again later!")
                    except Exception:
                        return await ctx.warn("Something went wrong, please try again later!")

                    embed = Embed()
                    embed.description = f"{ctx.author.mention} {ACTIONS[category]} {member1.mention} and {member2.mention}"
                    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar)
                    embed.set_image(url=data["link"])
                    return await ctx.send(embed=embed)

    # ---- NSFW commands ----

    @command(example="@x")
    @roleplay_ratelimit()
    @is_nsfw()
    @cooldown(1, 1, BucketType.member)
    async def anal(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Fuck a member anally."""
        return await self.purrbot_send(ctx, member, "anal")

    @command(example="@x")
    @roleplay_ratelimit()
    @is_nsfw()
    @cooldown(1, 1, BucketType.member)
    async def blowjob(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Give a member a blowjob."""
        return await self.purrbot_send(ctx, member, "blowjob")

    @command(example="@x")
    @roleplay_ratelimit()
    @is_nsfw()
    @cooldown(1, 1, BucketType.member)
    async def cum(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Cum on a member."""
        return await self.purrbot_send(ctx, member, "cum")

    @command(example="@x")
    @roleplay_ratelimit()
    @is_nsfw()
    @cooldown(1, 1, BucketType.member)
    async def pussylick(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Lick a members pussy."""
        return await self.purrbot_send(ctx, member, "pussylick")

    @group(name="threesome", invoke_without_command=True)
    @is_nsfw()
    async def threesome(self, ctx: Context):
        """Have a threesome with two other members."""
        await ctx.send_help(ctx.command)

    @threesome.command(name="ffm")
    @roleplay_ratelimit()
    @is_nsfw()
    @cooldown(1, 1, BucketType.member)
    async def threesome_ffm(self, ctx: Context, member1: Member, member2: Member):
        """Threesome with two girls and one boy."""
        return await self.threesome_send(ctx, member1, member2, "threesome_ffm")

    @threesome.command(name="fmm")
    @roleplay_ratelimit()
    @is_nsfw()
    @cooldown(1, 1, BucketType.member)
    async def threesome_fmm(self, ctx: Context, member1: Member, member2: Member):
        """Threesome with two boys and one girl."""
        return await self.threesome_send(ctx, member1, member2, "threesome_fmm")

    @threesome.command(name="fff")
    @roleplay_ratelimit()
    @is_nsfw()
    @cooldown(1, 1, BucketType.member)
    async def threesome_fff(self, ctx: Context, member1: Member, member2: Member):
        """Threesome with only girls."""
        return await self.threesome_send(ctx, member1, member2, "threesome_fff")

    # ---- SFW commands ----

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def cuddle(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Cuddle someone."""
        return await self.send(ctx, member, "cuddle")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def poke(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Poke someone."""
        return await self.send(ctx, member, "poke")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def kiss(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Kiss someone."""
        return await self.send(ctx, member, "kiss")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def hug(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Hug someone."""
        return await self.send(ctx, member, "hug")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def pat(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Pat someone."""
        return await self.send(ctx, member, "pat")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def tickle(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Tickle someone."""
        return await self.send(ctx, member, "tickle")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def lick(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Lick someone."""
        return await self.waifu_send(ctx, member, "lick")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def slap(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Slap someone."""
        return await self.send(ctx, member, "slap")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def feed(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Feed someone."""
        return await self.send(ctx, member, "feed")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def punch(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Punch someone."""
        return await self.send(ctx, member, "punch")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def highfive(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Highfive someone."""
        return await self.send(ctx, member, "highfive")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def bite(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Bite someone."""
        return await self.send(ctx, member, "bite")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def bully(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Bully someone."""
        return await self.waifu_send(ctx, member, "bully")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def bonk(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Bonk someone."""
        return await self.waifu_send(ctx, member, "bonk")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def cringe(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Cringe at someone."""
        return await self.waifu_send(ctx, member, "cringe")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def shoot(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Shoot someone."""
        return await self.send(ctx, member, "shoot")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def wave(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Wave to someone."""
        return await self.send(ctx, member, "wave")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def happy(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Be happy with someone."""
        return await self.send(ctx, member, "happy")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def peck(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Peck someone."""
        return await self.send(ctx, member, "peck")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def lurk(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Lurk at someone."""
        return await self.send(ctx, member, "lurk")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def sleep(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Sleep with someone."""
        return await self.send(ctx, member, "sleep")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def shrug(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Shrug at someone."""
        return await self.send(ctx, member, "shrug")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def wink(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Wink at someone."""
        return await self.send(ctx, member, "wink")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def dance(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Dance with someone."""
        return await self.send(ctx, member, "dance")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def yawn(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Yawn someone."""
        return await self.send(ctx, member, "yawn")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def nom(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Nom someone."""
        return await self.send(ctx, member, "nom")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def awoo(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Awoo at someone."""
        return await self.waifu_send(ctx, member, "awoo")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def yeet(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Yeet someone."""
        return await self.send(ctx, member, "yeet")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def think(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Think about someone."""
        return await self.send(ctx, member, "think")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def bored(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Be bored with someone."""
        return await self.send(ctx, member, "bored")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def blush(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Blush at someone."""
        return await self.send(ctx, member, "blush")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def stare(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Stare at someone."""
        return await self.send(ctx, member, "stare")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def nod(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Nod at someone."""
        return await self.send(ctx, member, "nod")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def handhold(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Hold hands with someone."""
        return await self.send(ctx, member, "handhold")

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def smug(self, ctx: Context, member: Optional[Member] = None) -> Message:
        """Smug at someone."""
        return await self.send(ctx, member, "smug")

    # ---- Custom CDN commands ----

    @hybrid_command(example="@x")
    @roleplay_ratelimit()
    async def nutkick(self, ctx: Context, user: Member):
        """Nutkick someone."""
        amount = 0
        if user != ctx.author:
            amount = cast(
                int,
                await self.bot.pool.fetchval(
                    """
                    INSERT INTO roleplay (user_id, target_id, category)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, target_id, category)
                    DO UPDATE SET amount = roleplay.amount + 1
                    RETURNING amount
                    """,
                    ctx.author.id,
                    user.id,
                    "nutkick",
                ),
            )
        images = f"https://r2.evict.bot/roleplay/nutkick/nutkick{random.randint(1, 8)}.gif"
        embed = Embed(
            description=f"**{ctx.author.mention}** just nutkicked {f'**{str(user.mention)}**' if user else 'themselves'}"
            + (
                f" for the **{ordinal(amount)}** time"
                if user != ctx.author and amount
                else " ... kinky"
            )
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
        embed.set_image(url=images)
        await ctx.send(embed=embed)

    @hybrid_command(example="@x")
    @roleplay_ratelimit()
    async def fuck(self, ctx: Context, user: Member):
        """Fuck someone."""
        amount = 0
        if user != ctx.author:
            amount = cast(
                int,
                await self.bot.pool.fetchval(
                    """
                    INSERT INTO roleplay (user_id, target_id, category)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, target_id, category)
                    DO UPDATE SET amount = roleplay.amount + 1
                    RETURNING amount
                    """,
                    ctx.author.id,
                    user.id,
                    "fuck",
                ),
            )
        images = f"https://r2.evict.bot/roleplay/fuck/fuck{random.randint(1, 11)}.gif"
        embed = Embed(
            description=f"**{ctx.author.mention}** just fucked {f'**{str(user.mention)}**' if user else 'themselves'}"
            + (
                f" for the **{ordinal(amount)}** time"
                if user != ctx.author and amount
                else " ... kinky"
            )
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
        embed.set_image(url=images)
        await ctx.send(embed=embed)

    @hybrid_command(example="@x")
    @roleplay_ratelimit()
    async def spank(self, ctx: Context, user: Member):
        """Spank someone."""
        amount = 0
        if user != ctx.author:
            amount = cast(
                int,
                await self.bot.pool.fetchval(
                    """
                    INSERT INTO roleplay (user_id, target_id, category)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, target_id, category)
                    DO UPDATE SET amount = roleplay.amount + 1
                    RETURNING amount
                    """,
                    ctx.author.id,
                    user.id,
                    "spank",
                ),
            )
        images = f"https://r2.evict.bot/roleplay/spank/spank{random.randint(1, 13)}.gif"
        embed = Embed(
            description=f"**{ctx.author.mention}** just spanked {f'**{str(user.mention)}**' if user else 'themselves'}"
            + (
                f" for the **{ordinal(amount)}** time"
                if user != ctx.author and amount
                else " ... kinky"
            )
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
        embed.set_image(url=images)
        await ctx.send(embed=embed)

    @command(example="@x")
    @roleplay_ratelimit()
    @cooldown(1, 1, BucketType.member)
    async def kill(self, ctx: Context, user: Member):
        """Kill someone."""
        amount = 0
        if user != ctx.author:
            amount = cast(
                int,
                await self.bot.pool.fetchval(
                    """
                    INSERT INTO roleplay (user_id, target_id, category)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, target_id, category)
                    DO UPDATE SET amount = roleplay.amount + 1
                    RETURNING amount
                    """,
                    ctx.author.id,
                    user.id,
                    "kill",
                ),
            )
        images = f"https://r2.evict.bot/roleplay/kill/kill{random.randint(1, 13)}.gif"
        embed = Embed(
            description=f"**{ctx.author.mention}** just killed {f'**{str(user.mention)}**' if user else 'themselves'}"
            + (
                f" for the **{ordinal(amount)}** time"
                if user != ctx.author and amount
                else " ... kinky"
            )
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
        embed.set_image(url=images)
        await ctx.send(embed=embed)

    # ---- Hybrid group for DM commands ----

    @hybrid_group(
        name="actions",
        description="Perform different actions on other members.",
        with_app_command=True,
        invoke_without_command=True
    )
    @discord.app_commands.allowed_installs(guilds=False, users=True)
    @discord.app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @discord.app_commands.default_permissions(use_application_commands=True)
    async def actions_group(self, ctx: Context):
        """Roleplay commands for DMs."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @actions_group.command(name="fuck")
    async def roleplay_fuck(self, ctx: Context, user: Union[Member, User]):
        """Fuck someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "fuck",
                    ),
                )
            image_num = random.randint(1, 11)
            image_path = f"assets/roleplay/fuck/fuck{image_num}.gif"
            file = discord.File(image_path, filename="roleplay.gif")
            embed = Embed(
                description=f"**{ctx.author.mention}** just fucked {f'**{str(user.mention)}**' if user else 'themselves'}"
                + (
                    f" for the **{ordinal(amount)}** time"
                    if user != ctx.author and amount
                    else " ... kinky"
                )
            )
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
            embed.set_image(url="attachment://roleplay.gif")
            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="spank")
    async def actions_spank(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Spank a user."
        )):
        """Spank someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "spank",
                    ),
                )
            image_num = random.randint(1, 13)
            image_path = f"assets/roleplay/spank/spank{image_num}.gif"
            file = discord.File(image_path, filename="roleplay.gif")
            embed = Embed(
                description=f"**{ctx.author.mention}** just spanked {f'**{str(user.mention)}**' if user else 'themselves'}"
                + (
                    f" for the **{ordinal(amount)}** time"
                    if user != ctx.author and amount
                    else " ... kinky"
                )
            )
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
            embed.set_image(url="attachment://roleplay.gif")
            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="kick")
    async def actions_kick(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Kick a user."
        )):
        """Kick someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "kick",
                    ),
                )
            image_num = random.randint(1, 13)
            image_path = f"assets/roleplay/kick/kick{image_num}.gif"
            file = discord.File(image_path, filename="roleplay.gif")
            embed = Embed(
                description=f"**{ctx.author.mention}** just kicked {f'**{str(user.mention)}**' if user else 'themselves'}"
                + (
                    f" for the **{ordinal(amount)}** time"
                    if user != ctx.author and amount
                    else " ... kinky"
                )
            )
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
            embed.set_image(url="attachment://roleplay.gif")
            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="nutkick")
    async def actions_nutkick(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Nutkick a user."
        )):
        """Nutkick someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "nutkick",
                    ),
                )
            image_num = random.randint(1, 8)
            image_path = f"assets/roleplay/nutkick/nutkick{image_num}.gif"
            file = discord.File(image_path, filename="roleplay.gif")
            embed = Embed(
                description=f"**{ctx.author.mention}** just nutkicked {f'**{str(user.mention)}**' if user else 'themselves'}"
                + (
                    f" for the **{ordinal(amount)}** time"
                    if user != ctx.author and amount
                    else " ... kinky"
                )
            )
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
            embed.set_image(url="attachment://roleplay.gif")
            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="bite")
    async def actions_bite(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Bite a user."
        )):
        """Bite someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "bite",
                    ),
                )
            url = "https://nekos.best/api/v2/bite"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just bit {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="cuddle")
    async def actions_cuddle(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Cuddle a user."
        )):
        """Cuddle someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "cuddle",
                    ),
                )
            url = "https://nekos.best/api/v2/cuddle"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just cuddled {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="poke")
    async def actions_poke(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Poke a user."
        )):
        """Poke someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "poke",
                    ),
                )
            url = "https://nekos.best/api/v2/poke"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just poked {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="kiss")
    async def actions_kiss(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Kiss a user."
        )):
        """Kiss someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "kiss",
                    ),
                )
            url = "https://nekos.best/api/v2/kiss"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just kissed {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="hug")
    async def actions_hug(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Hug a user."
        )):
        """Hug someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "hug",
                    ),
                )
            url = "https://nekos.best/api/v2/hug"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just hugged {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="pat")
    async def actions_pat(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Pat a user."
        )):
        """Pat someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "pat",
                    ),
                )
            url = "https://nekos.best/api/v2/pat"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just patted {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="tickle")
    async def actions_tickle(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Tickle a user."
        )):
        """Tickle someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "tickle",
                    ),
                )
            url = "https://nekos.best/api/v2/tickle"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just tickled {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="lick")
    async def actions_lick(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Lick a user."
        )):
        """Lick someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "lick",
                    ),
                )
            url = "https://nekos.best/api/v2/lick"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just licked {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="slap")
    async def actions_slap(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Slap a user."
        )):
        """Slap someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "slap",
                    ),
                )
            url = "https://nekos.best/api/v2/slap"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just slapped {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="punch")
    async def actions_punch(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Punch a user."
        )):
        """Punch someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "punch",
                    ),
                )
            url = "https://nekos.best/api/v2/punch"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just punched {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="shoot")
    async def actions_shoot(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Shoot a user."
        )):
        """Shoot someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "shoot",
                    ),
                )
            url = "https://nekos.best/api/v2/shoot"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just shot {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="nom")
    async def actions_nom(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Nom a user."
        )):
        """Nom someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "nom",
                    ),
                )
            url = "https://nekos.best/api/v2/nom"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just nommed {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @actions_group.command(name="yeet")
    async def actions_yeet(self, ctx: Context, user: Union[Member, User, None] = parameter(
            default=lambda ctx: ctx.author,
            description="Yeet a user."
        )):
        """Yeet someone (DMs only)."""
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.warn("This command can only be used in DMs!")
        try:
            amount = 0
            if user != ctx.author:
                amount = cast(
                    int,
                    await self.bot.pool.fetchval(
                        """
                        INSERT INTO roleplay (user_id, target_id, category)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, target_id, category)
                        DO UPDATE SET amount = roleplay.amount + 1
                        RETURNING amount
                        """,
                        ctx.author.id,
                        user.id,
                        "yeet",
                    ),
                )
            url = "https://nekos.best/api/v2/yeet"
            async with self.bot.session.get(url, proxy=None) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gif_url = data["results"][0]["url"]
                    async with self.bot.session.get(gif_url) as gif_resp:
                        if gif_resp.status == 200:
                            gif_data = await gif_resp.read()
                            file = discord.File(io.BytesIO(gif_data), filename="roleplay.gif")
                            embed = Embed(
                                description=f"**{ctx.author.mention}** just yeeted {f'**{str(user.mention)}**' if user else 'themselves'}"
                                + (
                                    f" for the **{ordinal(amount)}** time"
                                    if user != ctx.author and amount
                                    else " ... kinky"
                                )
                            )
                            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
                            embed.set_image(url="attachment://roleplay.gif")
                            await ctx.send(embed=embed, file=file)
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")
