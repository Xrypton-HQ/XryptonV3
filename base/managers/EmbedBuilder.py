import sys, os, discord, re
from discord.ext import commands
from typing import Union, Optional, Tuple, Dict, Any


class EmbedBuilder:
    @staticmethod
    def ordinal(num: int) -> str:
        """Convert from number to ordinal (10 - 10th)"""
        numb = str(num)
        if numb.startswith("0"):
            numb = numb.strip("0") or "0"
        if numb in ["11", "12", "13"]:
            return numb + "th"
        if numb.endswith("1"):
            return numb + "st"
        elif numb.endswith("2"):
            return numb + "nd"
        elif numb.endswith("3"):
            return numb + "rd"
        else:
            return numb + "th"

    @staticmethod
    def get_parts(params: str) -> list[str]:
        if not params:
            return []
        params = params.replace("{embed}", "").replace("{EMBED}", "").strip()
        parts = []
        for p in params.split("$v"):
            p = p.strip()
            if not p:
                continue
            if p.startswith("{") and p.endswith("}"):
                p = p[1:-1].strip()
            if p:
                parts.append(p)
        return parts

    @staticmethod
    def embed_replacement(user: Union[discord.Member, discord.User], params: str = None) -> Optional[str]:
        if params is None:
            return None

        guild = getattr(user, "guild", None)

        if "{user}" in params:
            params = params.replace(
                "{user}", str(user.name) + "#" + str(getattr(user, "discriminator", "0"))
            )
        if "{user.mention}" in params:
            params = params.replace("{user.mention}", user.mention)
        if "{user.name}" in params:
            params = params.replace("{user.name}", user.name)
        if "{user.avatar}" in params:
            params = params.replace("{user.avatar}", str(user.display_avatar.url))
        if "{user.joined_at}" in params:
            joined_at = getattr(user, "joined_at", None)
            params = params.replace(
                "{user.joined_at}",
                discord.utils.format_dt(joined_at, style="R") if joined_at else "N/A",
            )
        if "{user.created_at}" in params:
            params = params.replace(
                "{user.created_at}",
                discord.utils.format_dt(user.created_at, style="R") if user.created_at else "N/A",
            )
        if "{user.discriminator}" in params:
            params = params.replace("{user.discriminator}", str(getattr(user, "discriminator", "0")))

        if guild:
            if "{guild.name}" in params:
                params = params.replace("{guild.name}", guild.name)
            if "{guild.count}" in params:
                params = params.replace("{guild.count}", str(guild.member_count or len(guild.members)))
            if "{guild.count.format}" in params:
                params = params.replace(
                    "{guild.count.format}",
                    EmbedBuilder.ordinal(guild.member_count or len(guild.members)),
                )
            if "{guild.id}" in params:
                params = params.replace("{guild.id}", str(guild.id))
            if "{guild.created_at}" in params:
                params = params.replace(
                    "{guild.created_at}",
                    discord.utils.format_dt(guild.created_at, style="R") if guild.created_at else "N/A",
                )
            if "{guild.boost_count}" in params:
                params = params.replace(
                    "{guild.boost_count}", str(guild.premium_subscription_count or 0)
                )
            if "{guild.booster_count}" in params:
                params = params.replace(
                    "{guild.booster_count}", str(len(getattr(guild, "premium_subscribers", [])))
                )
            if "{guild.boost_count.format}" in params:
                params = params.replace(
                    "{guild.boost_count.format}",
                    EmbedBuilder.ordinal(guild.premium_subscription_count or 0),
                )
            if "{guild.booster_count.format}" in params:
                params = params.replace(
                    "{guild.booster_count.format}",
                    EmbedBuilder.ordinal(len(getattr(guild, "premium_subscribers", []))),
                )
            if "{guild.boost_tier}" in params:
                params = params.replace("{guild.boost_tier}", str(guild.premium_tier))
            if "{guild.vanity}" in params:
                vanity = getattr(guild, "vanity_url_code", None)
                params = params.replace(
                    "{guild.vanity}",
                    f"/{vanity}" if vanity else "none",
                )
            if "{guild.icon}" in params:
                if guild.icon:
                    params = params.replace("{guild.icon}", guild.icon.url)
                else:
                    params = params.replace("{guild.icon}", "https://none.none")
        else:
            for tag in [
                "{guild.name}", "{guild.count}", "{guild.count.format}",
                "{guild.id}", "{guild.created_at}", "{guild.boost_count}",
                "{guild.booster_count}", "{guild.boost_count.format}",
                "{guild.booster_count.format}", "{guild.boost_tier}",
                "{guild.vanity}", "{guild.icon}"
            ]:
                params = params.replace(tag, "N/A")

        if "{invisible}" in params:
            params = params.replace("{invisible}", "2B2D31")
        if "{botcolor}" in params:
            params = params.replace("{botcolor}", "7d7ead")

        return params

    @staticmethod
    async def to_object(params: str) -> Tuple[Optional[str], Optional[discord.Embed], discord.ui.View]:
        x: Dict[str, Any] = {}
        fields = []
        content = None
        view = discord.ui.View()

        for part in EmbedBuilder.get_parts(params):
            if part.startswith("content:"):
                c_val = part[len("content:") :].strip()
                if c_val:
                    content = c_val

            elif part.startswith("title:"):
                t_val = part[len("title:") :].strip()
                if t_val:
                    x["title"] = t_val

            elif part.startswith("description:"):
                d_val = part[len("description:") :]
                if d_val:
                    x["description"] = d_val

            elif part.startswith("color:"):
                raw_color = part[len("color:") :].strip().replace("#", "").replace("0x", "")
                try:
                    x["color"] = int(raw_color, 16)
                except Exception:
                    x["color"] = 0x2F3136

            elif part.startswith("image:"):
                img_url = part[len("image:") :].strip()
                if img_url:
                    x["image"] = {"url": img_url}

            elif part.startswith("thumbnail:"):
                thumb_url = part[len("thumbnail:") :].strip()
                if thumb_url:
                    x["thumbnail"] = {"url": thumb_url}

            elif part.startswith("author:"):
                z = part[len("author:") :].split(" && ")
                name = z[0].strip() if len(z) > 0 and z[0].strip() else None
                icon_url = z[1].strip() if len(z) > 1 and z[1].strip() else None
                url = z[2].strip() if len(z) > 2 and z[2].strip() else None

                if name or icon_url or url:
                    author_dict: Dict[str, Any] = {"name": name or "\u200b"}
                    if icon_url:
                        author_dict["icon_url"] = icon_url
                    if url:
                        author_dict["url"] = url
                    x["author"] = author_dict

            elif part.startswith("field:"):
                z = part[len("field:") :].split(" && ")
                name = z[0].strip() if len(z) > 0 and z[0].strip() else "\u200b"
                value = z[1].strip() if len(z) > 1 and z[1].strip() else "\u200b"
                inline_raw = z[2].strip().lower() if len(z) > 2 else "true"
                inline = False if inline_raw in ("false", "no", "0") else True

                fields.append({"name": name, "value": value, "inline": inline})

            elif part.startswith("footer:"):
                z = part[len("footer:") :].split(" && ")
                text = z[0].strip() if len(z) > 0 and z[0].strip() else None
                icon_url = z[1].strip() if len(z) > 1 and z[1].strip() else None

                if text or icon_url:
                    footer_dict: Dict[str, Any] = {"text": text or "\u200b"}
                    if icon_url:
                        footer_dict["icon_url"] = icon_url
                    x["footer"] = footer_dict

            elif part.startswith("button:"):
                z = part[len("button:") :].split(" && ")
                disabled = False
                style = discord.ButtonStyle.gray
                emoji = None
                label = None
                url = None

                for m in z:
                    m = m.strip()
                    if m.startswith("label:"):
                        label = m[len("label:") :].strip()
                    elif m.startswith("url:"):
                        url = m[len("url:") :].strip()
                    elif m.startswith("emoji:"):
                        emoji = m[len("emoji:") :].strip()
                    elif m == "disabled" or m.startswith("disabled:"):
                        disabled = True
                    elif m.startswith("style:"):
                        s = m[len("style:") :].strip().lower()
                        if s in ("red", "danger"):
                            style = discord.ButtonStyle.red
                        elif s in ("green", "success"):
                            style = discord.ButtonStyle.green
                        elif s in ("gray", "grey", "secondary"):
                            style = discord.ButtonStyle.gray
                        elif s in ("blue", "blurple", "primary"):
                            style = discord.ButtonStyle.blurple
                        elif s in ("link", "url"):
                            style = discord.ButtonStyle.link

                if url:
                    view.add_item(
                        discord.ui.Button(
                            style=discord.ButtonStyle.link,
                            label=label or None,
                            emoji=emoji or None,
                            url=url,
                            disabled=disabled,
                        )
                    )
                else:
                    view.add_item(
                        discord.ui.Button(
                            style=style,
                            label=label or None,
                            emoji=emoji or None,
                            disabled=disabled,
                        )
                    )

        if fields:
            x["fields"] = fields

        embed = discord.Embed.from_dict(x) if x else None
        return content, embed, view


class EmbedScript(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str):
        processed = EmbedBuilder.embed_replacement(ctx.author, argument) or argument
        content, embed, view = await EmbedBuilder.to_object(processed)

        res = {}
        if content:
            res["content"] = content
        if embed:
            res["embed"] = embed
        if view and len(view.children) > 0:
            res["view"] = view

        if res:
            return res
        return {"content": processed}


async def send_embed(destination, message, member):
    processed_message = EmbedBuilder.embed_replacement(member, message) or message
    content, embed, view = await EmbedBuilder.to_object(processed_message)
    await destination.send(
        content=content or (processed_message if not embed else None),
        embed=embed,
        view=view if (view and len(view.children) > 0) else None,
    )