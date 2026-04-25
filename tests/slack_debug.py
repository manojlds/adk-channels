"""Minimal Slack adapter test — bypasses FastAPI/Bridge to debug raw Slack connectivity."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("slack_test")


async def main() -> None:
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")

    if not bot_token or not app_token:
        logger.error("Missing tokens. Check .env file.")
        return

    logger.info("Bot token: %s...", bot_token[:15])
    logger.info("App token: %s...", app_token[:15])

    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp
    from slack_sdk.web.async_client import AsyncWebClient

    web = AsyncWebClient(token=bot_token)

    # Test auth
    try:
        auth = await web.auth_test()
        logger.info("Auth OK: bot=%s team=%s", auth.get("user"), auth.get("team"))
    except Exception as e:
        logger.error("Auth failed: %s", e)
        return

    app = AsyncApp(token=bot_token)

    @app.event("app_mention")
    async def on_mention(event, say, ack):
        await ack()
        logger.info("📨 APP_MENTION: %s", event)

    @app.event("message")
    async def on_message(event, say, ack):
        await ack()
        # Skip bot messages
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return
        logger.info("📨 MESSAGE: %s", event)

    @app.command("/adk")
    async def on_command(ack, command):
        await ack("Received!")
        logger.info("📨 COMMAND: %s", command)

    handler = AsyncSocketModeHandler(app, app_token)
    logger.info("Connecting to Slack Socket Mode...")
    await handler.connect_async()
    logger.info("✅ Connected! Waiting for events...")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await handler.close_async()


if __name__ == "__main__":
    asyncio.run(main())
