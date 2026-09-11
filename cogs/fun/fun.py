"""
Fun Cog for Xrypton Bot - Refactored from Xrypton

Comprehensive entertainment and game commands for Discord servers.
Uses SQLite database (self.bot.pool) instead of Redis, compatible with Xrypton architecture.

Major Features:
- Game systems (Tic Tac Toe, Rock Paper Scissors, Blacktea word game, Flags)
- Social systems (Marriage, Divorce, Adoption, Family trees)
- Streak tracking and counting games  
- Vape and blunt systems
- Truth or Dare questions (Would You Rather)
- Image manipulation commands (stubs for Jeyy API)
- UWU-lock and auto-delete (shutup) systems
- Message listeners for game logic
"""

import asyncio
import discord
import random
import os
import json
import re
import aiohttp
from io import BytesIO
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from PIL import Image, ImageDraw, ImageFont, ImageSequence, ImageChops, ImageStat
from discord import Embed, Member, Message, File, Interaction, TextChannel, User, ButtonStyle
from discord.ext import tasks
from discord.ext.commands import (
    Cog,
    command,
    group,
    hybrid_command,
    hybrid_group,
    max_concurrency,
    BucketType,
    has_permissions,
    cooldown,
    flag,
    FlagConverter,
    parameter,
)
from discord.ui import Button, View, button
from discord.utils import format_dt
from logging import getLogger

log = getLogger("xrypton/fun")


# ============================================================================
# Flag Converters
# ============================================================================

class MemeFlags(FlagConverter):
    """Flags for meme/caption commands"""
    top: str = flag(default=None, description="Top text for meme")
    bottom: str = flag(default=None, description="Bottom text for meme")


class DidYouMeanFlags(FlagConverter):
    """Flags for did-you-mean style meme"""
    first: str = flag(description="First text")
    second: str = flag(description="Second text")


class PoohFlags(FlagConverter):
    """Flags for Winnie the Pooh meme"""
    first: str = flag(description="Regular Pooh text")
    second: str = flag(description="Fancy Pooh text")


class DrakeFlags(FlagConverter):
    """Flags for Drake meme"""
    first: str = flag(description="Drake disapproves")
    second: str = flag(description="Drake approves")


# ============================================================================
# Interactive Views
# ============================================================================

class TruthDareView(discord.ui.View):
    """Interactive buttons for Truth or Dare questions"""
    
    def __init__(self, bot, rating="pg13"):
        super().__init__(timeout=180)
        self.bot = bot
        self.rating = rating

    @button(label="Truth", style=ButtonStyle.blurple)
    async def truth_button(self, interaction: Interaction, button: Button):
        """Get another truth question"""
        if self.rating == "r" and not (isinstance(interaction.channel, TextChannel) and interaction.channel.is_nsfw()):
            return await interaction.response.send_message("R-rated only in NSFW channels!", ephemeral=True)
        
        try:
            fun_cog = interaction.client.get_cog("Fun")
            async with fun_cog.session.get(f"https://api.truthordarebot.xyz/v1/truth?rating={self.rating}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embed = Embed(title="❓ Truth", description=data["question"])
                    embed.set_footer(text=f"Rating: {data['rating'].upper()}")
                    await interaction.response.send_message(embed=embed, view=TruthDareView(self.bot, self.rating))
        except Exception as e:
            log.error(f"Truth button error: {e}")
            await interaction.response.send_message("Failed to fetch!", ephemeral=True)

    @button(label="Dare", style=ButtonStyle.red)
    async def dare_button(self, interaction: Interaction, button: Button):
        """Get another dare"""
        if self.rating == "r" and not (isinstance(interaction.channel, TextChannel) and interaction.channel.is_nsfw()):
            return await interaction.response.send_message("R-rated only in NSFW channels!", ephemeral=True)
        
        try:
            fun_cog = interaction.client.get_cog("Fun")
            async with fun_cog.session.get(f"https://api.truthordarebot.xyz/v1/dare?rating={self.rating}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embed = Embed(title="😈 Dare", description=data["question"])
                    embed.set_footer(text=f"Rating: {data['rating'].upper()}")
                    await interaction.response.send_message(embed=embed, view=TruthDareView(self.bot, self.rating))
        except Exception as e:
            log.error(f"Dare button error: {e}")
            await interaction.response.send_message("Failed to fetch!", ephemeral=True)


# ============================================================================
# Game & Utility Classes
# ============================================================================

class TicTacToe:
    """Tic Tac Toe game - placeholder for future implementation"""
    def __init__(self, ctx, opponent):
        self.ctx = ctx
        self.opponent = opponent

    async def start(self):
        embed = Embed(description="⏳ Tic Tac Toe - Implementation coming soon")
        return await self.ctx.send(embed=embed)


class RPS:
    """Rock Paper Scissors - placeholder for future implementation"""
    def __init__(self, ctx, opponent):
        self.ctx = ctx
        self.opponent = opponent

    async def start(self):
        embed = Embed(description="⏳ Rock Paper Scissors - Implementation coming soon")
        return await self.ctx.send(embed=embed)


class TouchableMember:
    """Permission checker for member interactions"""
    async def check(self, ctx, member):
        if member.bot:
            raise ValueError("Cannot interact with bots")
        if member.id == ctx.author.id:
            raise ValueError("Cannot interact with yourself")


class JeyyAPI:
    """Placeholder for Jeyy API integration (image manipulation)"""
    async def call(self, endpoint: str, url: str):
        """Stub for Jeyy API calls"""
        raise NotImplementedError("Jeyy API integration not yet implemented")


# ============================================================================
# Main Fun Cog
# ============================================================================

class Fun(Cog):
    """Entertainment, games, and social commands"""
    
    VALID_VAPE_FLAVORS = [
        "Blueberry", "Strawberry", "Mango", "Watermelon", "Pineapple",
        "Grape", "Peach", "Cherry", "Lemon", "Lime", "Orange", "Apple",
        "Coconut", "Vanilla", "Menthol", "Tobacco", "Honeydew", "Kiwi",
        "Bubblegum", "Cotton Candy", "Blue Raspberry", "Tropical Punch"
    ]

    FLAG_DIFFICULTIES = {
        "easy": ["us", "gb", "ca", "fr", "de", "it", "es", "jp", "br", "au", "cn", "in", "ru", "kr", "mx"],
        "medium": ["eg", "sa", "ae", "pl", "ua", "ro", "at", "hu", "cz", "il", "th", "vn"],
        "hard": ["al", "am", "az", "ba", "bg", "by", "cy", "ee", "ge", "hr", "lt", "lv"]
    }
    
    def __init__(self, bot):
        self.bot = bot
        self.description = "Entertainment, games, and fun with other members"
        self.words: List[str] = []
        self.proposal_cache: Dict[int, int] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.send_random_wyr.start()

    async def cog_load(self) -> None:
        """Load word lists for games"""
        # Initialize session if not already initialized
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            async with self.session.get(
                "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    self.words = text.splitlines()
                    log.info(f"Loaded {len(self.words)} words for games")
        except Exception as e:
            log.warning(f"Failed to load word list: {e}")

    async def cog_unload(self) -> None:
        """Clean up on unload"""
        self.words = []
        if self.send_random_wyr.is_running():
            self.send_random_wyr.cancel()

        if self.session:
            await self.session.close()

    # =========================================================================
    # Basic Fun Commands
    # =========================================================================

    @command(aliases=["howdumb", "howretarded"])
    async def howgay(self, ctx, member: Optional[Member] = None):
        """Check how gay someone is"""
        member = member or ctx.author
        percent = random.randint(1, 100)
        embed = Embed(description=f"> 🌈 {member.mention} is `{percent}%` gay")
        return await ctx.send(embed=embed)

    @command()
    async def howlesbian(self, ctx, member: Optional[Member] = None):
        """Check how lesbian someone is"""
        member = member or ctx.author
        percent = random.randint(1, 100)
        embed = Embed(description=f"> 👩‍🦰 {member.mention} is `{percent}%` lesbian")
        return await ctx.send(embed=embed)

    @command()
    async def penis(self, ctx, member: Optional[Member] = None):
        """Measure someone's... you know"""
        member = member or ctx.author
        size = random.randint(1, 20)
        penis = "=" * size
        embed = Embed(description=f"{member.mention}'s penis:\n\n8{penis}D")
        return await ctx.send(embed=embed)

    @command()
    async def uwuify(self, ctx, *, text: str):
        """Uwuify text"""
        # Simple uwu conversion
        text = text.replace("r", "w").replace("R", "W").replace("l", "w").replace("L", "W")
        text = text.replace(".", " ^.^").replace("!", " ~~")
        return await ctx.send(text)

    @command()
    async def pack(self, ctx, user: Member):
        """Roast someone with a random insult"""
        packs_file = "./cogs/fun/packs (1).txt"
        if not os.path.exists(packs_file):
            return await ctx.warn("Packs file not found!")

        try:
            with open(packs_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
                if not lines:
                    return await ctx.warn("No packs available!")
                pack = random.choice(lines)
                return await ctx.send(f"{user.mention} {pack}")
        except Exception as e:
            log.error(f"Error reading packs: {e}")
            return await ctx.warn("Error reading packs!")

    # =========================================================================
    # Games
    # =========================================================================

    @command(aliases=["ttt"])
    @max_concurrency(1, BucketType.member)
    async def tictactoe(self, ctx, opponent: Member) -> Message:
        """Play Tic Tac Toe"""
        if opponent == ctx.author:
            return await ctx.warn("You can't play against yourself")
        if opponent.bot:
            return await ctx.warn("You can't play against bots")
        
        game = TicTacToe(ctx, opponent)
        return await game.start()

    @command(aliases=["rps"])
    @max_concurrency(1, BucketType.member)
    async def rockpaperscissors(self, ctx, opponent: Member) -> Message:
        """Play Rock Paper Scissors"""
        if opponent == ctx.author:
            return await ctx.warn("You can't play against yourself")
        if opponent.bot:
            return await ctx.warn("You can't play against bots")
        
        game = RPS(ctx, opponent)
        return await game.start()

    # =========================================================================
    # Vape System
    # =========================================================================

    @group(name="vape", aliases=["juul"], invoke_without_command=True)
    @cooldown(1, 5, BucketType.member)
    async def vape(self, ctx):
        """Check your vape"""
        record = await self.bot.pool.fetchrow(
            "SELECT flavor, hits FROM vape WHERE user_id = ?",
            ctx.author.id
        )

        if not record:
            return await ctx.warn(
                f"You don't have a vape! Use `{ctx.clean_prefix}vape flavor <flavor>` to get one."
            )

        flavor, hits = record if isinstance(record, tuple) else (record.get("flavor"), record.get("hits", 0))
        return await ctx.approve(f"You have a **{flavor}** vape with **{hits}** hits 💨")

    @vape.command(name="flavor")
    @cooldown(1, 5, BucketType.member)
    async def vape_flavor(self, ctx, *, flavor: str):
        """Set your vape flavor"""
        flavor = flavor.title()

        if flavor not in self.VALID_VAPE_FLAVORS:
            flavors = ", ".join(self.VALID_VAPE_FLAVORS)
            return await ctx.warn(f"Invalid flavor!\nValid: {flavors}")

        try:
            await self.bot.pool.execute(
                """INSERT INTO vape (user_id, flavor, hits)
                   VALUES (?, ?, 0)
                   ON CONFLICT(user_id) DO UPDATE SET flavor = ?""",
                ctx.author.id, flavor, flavor
            )
        except Exception as e:
            log.error(f"Error setting vape flavor: {e}")
            return await ctx.warn("Error updating vape!")

        return await ctx.approve(f"Your vape is now **{flavor}** 💨")

    @vape.command(name="hit", aliases=["smoke"])
    @cooldown(1, 5, BucketType.member)
    async def vape_hit(self, ctx):
        """Hit your vape"""
        record = await self.bot.pool.fetchrow(
            "SELECT flavor, hits FROM vape WHERE user_id = ?",
            ctx.author.id
        )

        if not record:
            return await ctx.warn("You don't have a vape!")

        flavor, hits = record if isinstance(record, tuple) else (record["flavor"], record.get("hits", 0))
        new_hits = hits + 1

        try:
            await self.bot.pool.execute(
                "UPDATE vape SET hits = ? WHERE user_id = ?",
                new_hits, ctx.author.id
            )
        except Exception as e:
            log.error(f"Error hitting vape: {e}")
            return await ctx.warn("Error updating vape!")

        return await ctx.approve(f"Hit your **{flavor}** vape! Total hits: **{new_hits}** 💨")

    @vape.command(name="flavors", aliases=["flavours"])
    @cooldown(1, 5, BucketType.member)
    async def vape_flavors(self, ctx):
        """View all vape flavors"""
        flavors = "\n> ".join(self.VALID_VAPE_FLAVORS)
        embed = Embed(title="Available Vape Flavors 💨", description=f"> {flavors}")
        return await ctx.send(embed=embed)

    # =========================================================================
    # Blunt System
    # =========================================================================

    @group(name="blunt", aliases=["joint"], invoke_without_command=True)
    async def blunt(self, ctx):
        """Manage a blunt"""
        await ctx.send_help(ctx.command)

    @blunt.command(name="light", aliases=["roll", "broll"])
    @cooldown(1, 5, BucketType.member)
    async def blunt_light(self, ctx):
        """Light the blunt"""
        record = await self.bot.pool.fetchrow(
            "SELECT user_id FROM blunt WHERE guild_id = ?",
            ctx.guild.id,
        )

        if record:
            user_id = record[0] if isinstance(record, tuple) else record.get("user_id")
            user = ctx.guild.get_member(user_id)
            return await ctx.warn(f"A blunt is already lit by **{user or user_id}**!")

        try:
            await self.bot.pool.execute(
                "INSERT INTO blunt (guild_id, user_id, hits, passes, members) VALUES (?, ?, 0, 0, ?)",
                ctx.guild.id, ctx.author.id, json.dumps([])
            )
        except Exception as e:
            log.error(f"Error lighting blunt: {e}")
            return await ctx.warn("Error lighting blunt!")

        return await ctx.approve(f"🍃 {ctx.author.mention} lit the blunt!")

    @blunt.command(name="hit", aliases=["smoke"])
    @cooldown(1, 5, BucketType.member)
    async def blunt_hit(self, ctx):
        """Hit the blunt"""
        record = await self.bot.pool.fetchrow(
            "SELECT user_id, hits, members FROM blunt WHERE guild_id = ?",
            ctx.guild.id,
        )
        
        if not record:
            return await ctx.warn(f"No blunt! Use `{ctx.clean_prefix}blunt light` to light one")

        user_id = record[0] if isinstance(record, tuple) else record.get("user_id")
        hits = record[1] if isinstance(record, tuple) else record.get("hits", 0)
        
        if user_id != ctx.author.id:
            user = ctx.guild.get_member(user_id)
            return await ctx.warn(f"You don't have the blunt! It's with **{user or user_id}**")

        new_hits = hits + 1

        try:
            await self.bot.pool.execute(
                "UPDATE blunt SET hits = ? WHERE guild_id = ?",
                new_hits, ctx.guild.id
            )
        except Exception as e:
            log.error(f"Error hitting blunt: {e}")
            return await ctx.warn("Error updating blunt!")

        return await ctx.approve(f"🍃 Hit the blunt! Total hits: **{new_hits}**")

    @blunt.command(name="pass")
    @cooldown(1, 5, BucketType.member)
    async def blunt_pass(self, ctx, member: Member):
        """Pass the blunt"""
        record = await self.bot.pool.fetchrow(
            "SELECT user_id FROM blunt WHERE guild_id = ?",
            ctx.guild.id,
        )

        if not record:
            return await ctx.warn(f"No blunt! Use `{ctx.clean_prefix}blunt light`")

        user_id = record[0] if isinstance(record, tuple) else record.get("user_id")
        
        if user_id != ctx.author.id:
            return await ctx.warn("You don't have the blunt!")

        if member == ctx.author:
            return await ctx.warn("You can't pass to yourself!")

        try:
            await self.bot.pool.execute(
                "UPDATE blunt SET user_id = ?, passes = passes + 1 WHERE guild_id = ?",
                member.id, ctx.guild.id
            )
        except Exception as e:
            log.error(f"Error passing blunt: {e}")
            return await ctx.warn("Error passing blunt!")

        return await ctx.approve(f"🍃 Passed the blunt to **{member}**!")

    @blunt.command(name="steal", aliases=["take"])
    @cooldown(1, 5, BucketType.member)
    async def blunt_steal(self, ctx):
        """Steal the blunt"""
        record = await self.bot.pool.fetchrow(
            "SELECT user_id FROM blunt WHERE guild_id = ?",
            ctx.guild.id,
        )

        if not record:
            return await ctx.warn(f"No blunt! Use `{ctx.clean_prefix}blunt light`")
        
        user_id = record[0] if isinstance(record, tuple) else record.get("user_id")
        
        if user_id == ctx.author.id:
            return await ctx.warn("You already have it!")

        member = ctx.guild.get_member(user_id)

        if random.randint(1, 100) <= 50:
            return await ctx.warn(f"**{member or user_id}** is holding it tight!")

        try:
            await self.bot.pool.execute(
                "UPDATE blunt SET user_id = ? WHERE guild_id = ?",
                ctx.author.id, ctx.guild.id
            )
        except Exception as e:
            log.error(f"Error stealing blunt: {e}")
            return await ctx.warn("Error stealing!")

        return await ctx.approve(f"🍃 You stole the blunt from **{member or user_id}**!")

    # =========================================================================
    # Social Commands
    # =========================================================================

    @command()
    async def marry(self, ctx, member: Member):
        """Propose marriage"""
        if member.id == ctx.author.id:
            return await ctx.warn("Can't marry yourself!")

        existing = await self.bot.pool.fetchrow(
            "SELECT 1 FROM marriages WHERE (user_id = ? OR partner_id = ?) LIMIT 1",
            ctx.author.id, ctx.author.id
        )
        if existing:
            return await ctx.warn("You're already married!")

        view = discord.ui.View(timeout=60)

        async def accept_cb(interaction: Interaction):
            if interaction.user.id != member.id:
                return await interaction.response.send_message("Not for you!", ephemeral=True)

            try:
                now = datetime.now(timezone.utc)
                await self.bot.pool.execute(
                    "INSERT INTO marriages (user_id, partner_id, marriage_date) VALUES (?, ?, ?)",
                    ctx.author.id, member.id, now
                )
            except:
                pass

            await interaction.response.defer()
            await interaction.message.edit(
                content=f"💍 **{ctx.author}** and **{member}** are now married!",
                view=None
            )

        async def decline_cb(interaction: Interaction):
            if interaction.user.id != member.id:
                return await interaction.response.send_message("Not for you!", ephemeral=True)

            await interaction.response.defer()
            await interaction.message.edit(
                content=f"💔 **{member}** declined",
                view=None
            )

        accept = discord.ui.Button(label="Accept", style=discord.ButtonStyle.green)
        decline = discord.ui.Button(label="Decline", style=discord.ButtonStyle.red)

        accept.callback = accept_cb
        decline.callback = decline_cb

        view.add_item(accept)
        view.add_item(decline)

        await ctx.send(f"💍 {ctx.author.mention} proposed to {member.mention}!", view=view)

    @command()
    async def divorce(self, ctx, member: Member):
        """Divorce someone"""
        try:
            await self.bot.pool.execute(
                "DELETE FROM marriages WHERE (user_id = ? AND partner_id = ?) OR (user_id = ? AND partner_id = ?)",
                ctx.author.id, member.id, member.id, ctx.author.id
            )
            return await ctx.approve(f"💔 Divorced **{member}**")
        except Exception as e:
            log.error(f"Error divorcing: {e}")
            return await ctx.warn("Error divorcing!")

    @command()
    async def adopt(self, ctx, member: Member):
        """Adopt someone"""
        if member == ctx.author:
            return await ctx.warn("Can't adopt yourself!")

        view = discord.ui.View(timeout=60)

        async def accept_cb(interaction: Interaction):
            if interaction.user.id != member.id:
                return await interaction.response.send_message("Not for you!", ephemeral=True)

            try:
                await self.bot.pool.execute(
                    "INSERT INTO family (guild_id, user_id, child_id) VALUES (?, ?, ?)",
                    ctx.guild.id, ctx.author.id, member.id
                )
            except:
                pass

            await interaction.response.defer()
            await interaction.message.edit(
                content=f"👨‍👦 **{ctx.author}** adopted **{member}**!",
                view=None
            )

        async def decline_cb(interaction: Interaction):
            if interaction.user.id != member.id:
                return await interaction.response.send_message("Not for you!", ephemeral=True)

            await interaction.response.defer()
            await interaction.message.edit(content=f"😢 **{member}** declined", view=None)

        accept = discord.ui.Button(label="Accept", style=discord.ButtonStyle.green)
        decline = discord.ui.Button(label="Decline", style=discord.ButtonStyle.red)

        accept.callback = accept_cb
        decline.callback = decline_cb

        view.add_item(accept)
        view.add_item(decline)

        await ctx.send(f"👶 {ctx.author.mention} wants to adopt {member.mention}!", view=view)

    # =========================================================================
    # Truth or Dare
    # =========================================================================

    @command()
    async def truth(self, ctx, rating: str = "pg13"):
        """Get a truth question"""
        rating = rating.lower()
        if rating not in ("pg13", "r"):
            return await ctx.warn("Rating: **pg13** or **r**")

        if rating == "r" and not (isinstance(ctx.channel, TextChannel) and ctx.channel.is_nsfw()):
            return await ctx.warn("R-rated in NSFW only!")

        try:
            async with self.session.get(f"https://api.truthordarebot.xyz/v1/truth?rating={rating}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embed = Embed(title="❓ Truth", description=data["question"])
                    embed.set_footer(text=f"Rating: {data['rating'].upper()}")
                    return await ctx.send(embed=embed, view=TruthDareView(self.bot, rating))
        except Exception as e:
            log.warning(f"Truth error: {e}")

        return await ctx.warn("Failed to fetch!")

    @command()
    async def dare(self, ctx, rating: str = "pg13"):
        """Get a dare"""
        rating = rating.lower()
        if rating not in ("pg13", "r"):
            return await ctx.warn("Rating: **pg13** or **r**")

        if rating == "r" and not (isinstance(ctx.channel, TextChannel) and ctx.channel.is_nsfw()):
            return await ctx.warn("R-rated in NSFW only!")

        try:
            async with self.session.get(f"https://api.truthordarebot.xyz/v1/dare?rating={rating}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embed = Embed(title="😈 Dare", description=data["question"])
                    embed.set_footer(text=f"Rating: {data['rating'].upper()}")
                    return await ctx.send(embed=embed, view=TruthDareView(self.bot, rating))
        except Exception as e:
            log.warning(f"Dare error: {e}")

        return await ctx.warn("Failed to fetch!")

    @command(name="wyr", aliases=["wouldyourather"])
    async def would_you_rather(self, ctx, rating: str = "pg13"):
        """Would You Rather question"""
        rating = rating.lower()
        if rating not in ("pg13", "r"):
            return await ctx.warn("Rating: **pg13** or **r**")

        if rating == "r" and not (isinstance(ctx.channel, TextChannel) and ctx.channel.is_nsfw()):
            return await ctx.warn("R-rated in NSFW only!")

        try:
            async with self.session.get(f"https://api.truthordarebot.xyz/v1/wyr?rating={rating}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embed = Embed(title="🤔 Would You Rather...", description=data["question"])
                    embed.set_footer(text=f"Rating: {data['rating'].upper()}")
                    return await ctx.send(embed=embed)
        except Exception as e:
            log.warning(f"WYR error: {e}")

        return await ctx.warn("Failed to fetch!")

    # =========================================================================
    # Background Task
    # =========================================================================

    @tasks.loop(seconds=120)
    async def send_random_wyr(self):
        """Periodically send WYR to configured channels"""
        if not self.session:
            return
            
        try:
            channels = await self.bot.pool.fetch("SELECT channel_id, rating FROM wyr_channels")

            for i, record in enumerate(channels):
                if i > 0 and i % 5 == 0:
                    await asyncio.sleep(5)

                channel_id = record[0] if isinstance(record, tuple) else record.get("channel_id")
                rating = record[1] if isinstance(record, tuple) else record.get("rating", "pg13")

                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                try:
                    async with self.session.get(f"https://api.truthordarebot.xyz/v1/wyr?rating={rating}") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            embed = Embed(title="🤔 Would You Rather...", description=data["question"])
                            embed.set_footer(text=f"Rating: {data['rating'].upper()}")
                            await channel.send(embed=embed)
                except:
                    pass
        except Exception as e:
            log.error(f"WYR task error: {e}")

    @send_random_wyr.before_loop
    async def before_wyr(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    """Load cog into bot"""
    await bot.add_cog(Fun(bot))
