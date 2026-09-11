import asyncio
from base.Xrypton import Bot
from base.config import CLIENT

async def main():
    bot = Bot()
    try:
        await bot.start(CLIENT.TOKEN)
    except KeyboardInterrupt:
        exit()

if __name__ == "__main__":
        asyncio.run(main())