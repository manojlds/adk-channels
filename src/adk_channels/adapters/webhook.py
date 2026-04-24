"""Webhook adapter for adk-channels (outgoing HTTP POST)."""

from __future__ import annotations

import json
import logging

import aiohttp

from adk_channels.adapters.base import BaseChannelAdapter
from adk_channels.config import AdapterConfig
from adk_channels.types import AdapterDirection, ChannelMessage, OnIncomingMessage

logger = logging.getLogger("adk_channels.adapters.webhook")


async def create_webhook_adapter(config: AdapterConfig) -> BaseChannelAdapter:
    """Factory for creating a Webhook adapter."""
    return WebhookAdapter(config)


class WebhookAdapter(BaseChannelAdapter):
    """Outgoing webhook adapter for sending HTTP POST requests."""

    direction = AdapterDirection.OUTGOING

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__()
        self._config = config
        self._default_url = str(config.model_extra.get("url", "")) if config.model_extra else ""
        self._method = str(config.model_extra.get("method", "POST") if config.model_extra else "POST").upper()
        self._content_type = str(
            config.model_extra.get("content_type", "application/json") if config.model_extra else "application/json"
        )
        self._headers: dict[str, str] = dict(config.model_extra.get("headers", {})) if config.model_extra else {}
        self._payload_mode = str(
            config.model_extra.get("payload_mode", "envelope") if config.model_extra else "envelope"
        )
        self._session: aiohttp.ClientSession | None = None

    async def send(self, message: ChannelMessage) -> None:
        url = message.recipient or self._default_url
        if not url:
            raise ValueError("Webhook adapter requires a URL (recipient or default)")

        if not self._session:
            self._session = aiohttp.ClientSession()

        headers = dict(self._headers)
        content_type = message.metadata.get("content_type") if message.metadata else None
        if content_type:
            headers["Content-Type"] = content_type
        elif self._content_type:
            headers["Content-Type"] = self._content_type

        method = message.metadata.get("method", self._method).upper() if message.metadata else self._method

        if self._payload_mode == "raw" or (message.metadata and message.metadata.get("raw_body") is not None):
            payload = message.metadata.get("raw_body") if message.metadata else None
            if payload is None and message.text is not None:
                payload = message.text
        else:
            # envelope mode
            payload = {
                "text": message.text,
                "source": message.source,
                "adapter": message.adapter,
                "recipient": message.recipient,
                "metadata": message.metadata,
                "timestamp": __import__("time").time(),
            }

        data = json.dumps(payload) if isinstance(payload, dict) else str(payload) if payload is not None else None

        async with self._session.request(
            method=method,
            url=url,
            headers=headers,
            data=data,
        ) as resp:
            resp.raise_for_status()
            logger.debug("Webhook %s %s -> %s", method, url, resp.status)

    async def start(self, on_message: OnIncomingMessage) -> None:
        # Outgoing-only adapter; nothing to start
        pass

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
