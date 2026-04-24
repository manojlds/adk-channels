"""FastAPI server for webhook/HTTP adapters."""

from __future__ import annotations

import logging
from typing import Any

try:
    from fastapi import FastAPI, Request, Response
except ImportError:
    FastAPI = None  # type: ignore

logger = logging.getLogger("adk_channels.server")


class WebhookServer:
    """Optional FastAPI server for receiving webhook events."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        if FastAPI is None:
            raise ImportError("fastapi is required for WebhookServer. Install: pip install adk-channels[webhook]")
        self.host = host
        self.port = port
        self._app = FastAPI(title="ADK Channels Webhook Server")
        self._handlers: dict[str, Any] = {}

    @property
    def app(self) -> FastAPI:
        return self._app

    def register_webhook(self, path: str, handler: Any) -> None:
        """Register a webhook handler at a specific path."""
        self._handlers[path] = handler

        @self._app.post(path)
        async def webhook_endpoint(request: Request) -> Response:
            try:
                body = await request.json()
                result = await handler(body)
                return Response(content=result.get("text", "OK"), status_code=200)
            except Exception as exc:
                logger.exception("Webhook error at %s", path)
                return Response(content=f"Error: {exc}", status_code=500)

    async def start(self) -> None:
        import uvicorn

        config = uvicorn.Config(self._app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
