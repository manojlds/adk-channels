"""Process 2: Slack bridge that calls an existing ADK backend over HTTP.

Run ``backend.py`` first, then start this process with Slack credentials.
"""

from __future__ import annotations

# ruff: noqa: E402, I001

import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge, IncomingMessage, RunResult
from adk_channels.config import AdapterConfig
from adk_channels.server_integration import ChannelsFastAPIIntegration
from examples.session_service import create_sqlite_session_service, resolve_session_db_path

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("two_process_slack_bridge")


def _channel_id(message: IncomingMessage) -> str:
    metadata_channel_id = message.metadata.get("channel_id")
    if metadata_channel_id:
        return str(metadata_channel_id)
    return message.sender.split(":", 1)[0]


def app_resolver(message: IncomingMessage) -> str:
    """Route Slack channels to backend apps.

    Set these env vars to route specific Slack channels:
    - ADK_CHANNELS_SUPPORT_CHANNEL_ID=C...
    - ADK_CHANNELS_ENGINEERING_CHANNEL_ID=C...
    """
    channel_id = _channel_id(message)
    if channel_id == os.environ.get("ADK_CHANNELS_SUPPORT_CHANNEL_ID"):
        return "support"
    if channel_id == os.environ.get("ADK_CHANNELS_ENGINEERING_CHANNEL_ID"):
        return "engineering"
    return "default"


class ADKBackendClient:
    """Small stdlib HTTP client for ADK's official FastAPI endpoints."""

    def __init__(self, *, base_url: str, app_name: str, timeout_seconds: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout_seconds = timeout_seconds

    async def call(self, session_id: str, text: str) -> RunResult:
        return await asyncio.to_thread(self._call_sync, session_id, text)

    def _call_sync(self, session_id: str, text: str) -> RunResult:
        user_id = self._derive_user_id(session_id)
        self._ensure_session(user_id=user_id, session_id=session_id)

        response_body = self._request_json(
            "/run",
            method="POST",
            payload={
                "app_name": self.app_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {
                    "role": "user",
                    "parts": [{"text": text}],
                },
            },
        )

        events = json.loads(response_body)
        result = self._extract_run_result(events)
        if not result.response:
            raise RuntimeError(f"ADK backend response did not include response text: {response_body}")
        return result

    def _ensure_session(self, *, user_id: str, session_id: str) -> None:
        session_path = f"/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        try:
            self._request_json(session_path, method="GET")
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise

        self._request_json(
            f"/apps/{self.app_name}/users/{user_id}/sessions",
            method="POST",
            payload={
                "session_id": session_id,
                "state": {
                    "channel": "slack",
                    "slack_thread_key": user_id,
                },
            },
        )

    def _request_json(self, path: str, *, method: str, payload: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            error_body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"ADK backend returned HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not call ADK backend at {url}: {exc.reason}") from exc

    @classmethod
    def _extract_run_result(cls, events: Any) -> RunResult:
        if not isinstance(events, list):
            return RunResult(ok=True, response="")

        thoughts: list[str] = []
        responses: list[str] = []
        tool_interactions: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue

            content = event.get("content")
            if not isinstance(content, dict):
                continue

            parts = content.get("parts")
            if not isinstance(parts, list):
                continue

            for part in parts:
                if not isinstance(part, dict):
                    continue

                text = part.get("text")
                if isinstance(text, str) and text:
                    if part.get("thought"):
                        thoughts.append(text)
                    else:
                        responses.append(text)

                interaction = cls._extract_tool_interaction(part)
                if interaction is not None:
                    tool_interactions.append(interaction)

        return RunResult(
            ok=True,
            response="\n".join(responses).strip(),
            thoughts=thoughts,
            tool_interactions=tool_interactions,
        )

    @classmethod
    def _extract_tool_interaction(cls, part: dict[str, Any]) -> dict[str, Any] | None:
        function_call = cls._first_dict(part, "function_call", "functionCall")
        if function_call is not None:
            raw_payload = function_call.get("args")
            return {
                "type": "tool_call",
                "name": str(function_call.get("name") or "tool"),
                "payload": cls._stringify_payload(raw_payload),
                "raw_payload": raw_payload,
            }

        function_response = cls._first_dict(part, "function_response", "functionResponse")
        if function_response is not None:
            raw_payload = function_response.get("response")
            return {
                "type": "tool_result",
                "name": str(function_response.get("name") or "tool"),
                "payload": cls._stringify_payload(raw_payload),
                "raw_payload": raw_payload,
            }

        executable_code = cls._first_dict(part, "executable_code", "executableCode")
        if executable_code is not None:
            language = str(executable_code.get("language") or "")
            language_prefix = f"[{language}] " if language else ""
            return {
                "type": "code",
                "name": "executable_code",
                "payload": f"{language_prefix}{cls._stringify_payload(executable_code.get('code'))}".strip(),
            }

        code_execution_result = cls._first_dict(part, "code_execution_result", "codeExecutionResult")
        if code_execution_result is not None:
            return {
                "type": "code_result",
                "name": "code_execution_result",
                "payload": cls._stringify_payload(code_execution_result.get("output")),
            }

        return None

    @staticmethod
    def _first_dict(data: dict[str, Any], *keys: str) -> dict[str, Any] | None:
        for key in keys:
            value = data.get(key)
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _stringify_payload(payload: Any, *, max_length: int = 800) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            text = payload
        else:
            try:
                text = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            except TypeError:
                text = str(payload)

        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 3]}..."

    def _derive_user_id(self, session_id: str) -> str:
        prefix = f"{self.app_name}:"
        if session_id.startswith(prefix):
            return session_id[len(prefix) :]
        return session_id


def _build_config() -> ChannelsConfig:
    config = ChannelsConfig()
    if "slack" in config.adapters:
        config.bridge.enabled = True
        return config

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not bot_token or not app_token:
        raise RuntimeError(
            "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN, or configure "
            "ADK_CHANNELS_ADAPTERS__SLACK__TYPE/BOT_TOKEN/APP_TOKEN"
        )

    config.adapters["slack"] = AdapterConfig(
        type="slack",
        bot_token=bot_token,
        app_token=app_token,
        respond_to_mentions_only=True,
    )
    config.bridge.enabled = True
    return config


def create_app() -> FastAPI:
    """Create the standalone Slack bridge process."""
    backend_url = os.environ.get("ADK_BACKEND_URL", "http://127.0.0.1:8001")
    config = _build_config()
    registry = ChannelRegistry()
    clients = {
        app_name: ADKBackendClient(base_url=backend_url, app_name=app_name).call
        for app_name in ("support", "engineering", "default")
    }

    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        app_resolver=app_resolver,
        http_clients=clients,
        # This local demo shares the backend SQLite DB so unmentioned Slack
        # thread replies can be checked after a bridge restart.
        session_service_factory=create_sqlite_session_service,
    )

    app = FastAPI(title="Two-Process Slack Bridge")
    integration = ChannelsFastAPIIntegration(
        fastapi_app=app,
        registry=registry,
        bridge=bridge,
        config=config,
    )
    integration.setup()

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "slack-bridge",
            "backend_url": backend_url,
            "session_db": resolve_session_db_path(),
        }

    return app


def main() -> None:
    port = int(os.environ.get("SLACK_BRIDGE_PORT", "8002"))
    logger.info("Starting Slack bridge on http://0.0.0.0:%d", port)
    logger.info("ADK backend: %s", os.environ.get("ADK_BACKEND_URL", "http://127.0.0.1:8001"))
    logger.info("Bridge health: http://0.0.0.0:%d/channels/health", port)
    logger.info("Shared session DB: %s", resolve_session_db_path())
    uvicorn.run(create_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
