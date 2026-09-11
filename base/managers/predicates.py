from discord.ext.commands import check, MissingPermissions
from typing import Any


def example(example: str) -> Any:
    def decorator(command):
        # Only set when an actual example string was provided. This keeps
        # ``command._example`` falsy when no example was given so the
        # fallback chain in ``send_command_help`` can find one elsewhere
        # (e.g. ``command.callback._example``).
        if example:
            command._example = example
            if hasattr(command, "callback") and command.callback is not None:
                # ``callback`` may not be set yet if this decorator runs
                # *after* the command decorator, so guard against that.
                try:
                    command.callback._example = example
                except AttributeError:
                    pass
        return command

    return decorator


def has_permissions(**perms: str) -> Any:
    from base.context import Context
    from discord import Permissions

    valid_flags = set(Permissions.VALID_FLAGS)

    async def predicate(ctx: Context):
        if ctx.author.id in ctx.bot.owner_ids:
            return True

        author_perms = ctx.author.guild_permissions
        discord_perms = {k: v for k, v in perms.items() if k in valid_flags}
        if discord_perms and all(
            getattr(author_perms, perm, False) for perm in discord_perms
        ):
            return True

        if "guild_owner" in perms and perms["guild_owner"] and ctx.author.id == ctx.guild.owner_id:
            return True

        role_ids = [r.id for r in ctx.author.roles]
        if not role_ids:
            raise MissingPermissions(perms)

        results = await ctx.bot.pool.fetch(
            "SELECT permission FROM fake_permissions WHERE guild_id = $1 AND role_id = ANY($2::bigint[])",
            ctx.guild.id,
            role_ids,
        )

        fake_perms = {
            p for row in results for p in row["permission"].split(",") if p
        }
        if "administrator" in fake_perms or any(perm in fake_perms for perm in perms):
            return True

        raise MissingPermissions(perms)

    return check(predicate)


def is_owner() -> Any:
    from base.context import Context
    from base.config import CLIENT

    async def predicate(ctx: Context):
        return ctx.author.id in CLIENT.OWNER

    return check(predicate)