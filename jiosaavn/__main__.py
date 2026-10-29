import sys
import logging
import logging.config
import importlib
from dotenv import load_dotenv
import asyncio

# Ensure UTF-8 output on Windows console so emoji logs never crash
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def main():
    # Get logging configurations
    logging.config.fileConfig('logging.conf')
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("pyrogram").setLevel(logging.WARNING)

    load_dotenv()
    # Set a new event loop before importing the bot
    asyncio.set_event_loop(asyncio.new_event_loop())
    bot = importlib.import_module("jiosaavn.bot").Bot
    bot().run()


if __name__ == "__main__":
    main()
