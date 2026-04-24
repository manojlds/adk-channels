"""Telegram adapter for adk-channels using python-telegram-bot."""

from __future__ import annotations

import logging
from typing import Any

from adk_channels.adapters.base import BaseChannelAdapter
from adk_channels.config import AdapterConfig
from adk_channels.types import AdapterDirection, ChannelMessage, IncomingMessage, OnIncomingMessage

logger = logging.getLogger("adk_channels.adapters.telegram")

MAX_LENGTH = 4096  # Telegram message limit


async def create_telegram_adapter(config: AdapterConfig) -> BaseChannelAdapter:
    """Factory for creating a Telegram adapter."""
    return TelegramAdapter(config)


class TelegramAdapter(BaseChannelAdapter):
    """Bidirectional Telegram adapter using python-telegram-bot."""

    direction = AdapterDirection.BIDIRECTIONAL

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__()
        self._config = config
        self._bot_token = str(config.model_extra.get("bot_token", "")) if config.model_extra else ""
        self._allowed_chat_ids: list[int] = (
            list(config.model_extra.get("allowed_chat_ids", [])) if config.model_extra else []
        )
        self._parse_mode = str(config.model_extra.get("parse_mode", "Markdown") if config.model_extra else "Markdown")

        if not self._bot_token:
            raise ValueError("Telegram adapter requires bot_token")

        self._app: Any | None = None
        self._on_message: OnIncomingMessage | None = None

    def _is_allowed(self, chat_id: int) -> bool:
        if not self._allowed_chat_ids:
            return True
        return chat_id in self._allowed_chat_ids

    async def send(self, message: ChannelMessage) -> None:
        if not message.text:
            raise ValueError("Telegram adapter requires text")

        from telegram import Bot

        bot = Bot(token=self._bot_token)
        chat_id = int(message.recipient)
        prefix = f"*[{(message.source or 'adk')}] *\n" if message.source else ""
        full = prefix + message.text

        # Telegram max message length is 4096
        if len(full) <= MAX_LENGTH:
            await bot.send_message(chat_id=chat_id, text=full, parse_mode=self._parse_mode)
            return

        # Split long messages
        remaining = full
        while remaining:
            if len(remaining) <= MAX_LENGTH:
                await bot.send_message(chat_id=chat_id, text=remaining, parse_mode=self._parse_mode)
                break
            split_at = remaining.rfind("\n", 0, MAX_LENGTH)
            if split_at < MAX_LENGTH // 2:
                split_at = MAX_LENGTH
            chunk = remaining[:split_at]
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=self._parse_mode)
            remaining = remaining[split_at:].lstrip("\n")

    async def send_typing(self, recipient: str) -> None:
        from telegram import Bot

        bot = Bot(token=self._bot_token)
        await bot.send_chat_action(chat_id=int(recipient), action="typing")

    async def start(self, on_message: OnIncomingMessage) -> None:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

        self._on_message = on_message

        application = Application.builder().token(self._bot_token).build()

        async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_message or not update.effective_chat:
                return
            chat_id = update.effective_chat.id
            if not self._is_allowed(chat_id):
                return

            text = update.effective_message.text or ""
            sender = str(chat_id)

            if self._on_message:
                result = self._on_message(
                    IncomingMessage(
                        adapter="telegram",
                        sender=sender,
                        text=text,
                        metadata={
                            "chat_id": chat_id,
                            "user_id": update.effective_user.id if update.effective_user else None,
                            "username": update.effective_user.username if update.effective_user else None,
                            "message_id": update.effective_message.message_id,
                        },
                    )
                )
                if result is not None:
                    await result

        async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Hello! I'm an ADK agent. Send me a message to get started.",
                )

        application.add_handler(CommandHandler("start", handle_start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

        self._app = application
        await application.initialize()
        await application.start()
        if application.updater:
            await application.updater.start_polling()

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None
