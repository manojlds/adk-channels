"""Multi-app bridge that routes incoming messages to different ADK agents/apps."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from adk_channels.config import BridgeConfig
from adk_channels.registry import ChannelRegistry
from adk_channels.types import (
    ChannelMessage,
    IncomingMessage,
    QueuedPrompt,
    RunResult,
    SenderSession,
)

logger = logging.getLogger("adk_channels.multi_app_bridge")

_id_counter = 0


def _next_id() -> str:
    global _id_counter
    _id_counter += 1
    return f"msg-{int(time.time() * 1000)}-{_id_counter}"


AppResolver = Callable[[IncomingMessage], str | Awaitable[str]]
AgentFactory = Callable[[], Any]
AgentRunner = Callable[[str, str, str], Any]  # (app_name, session_id, text) -> response


class MultiAppBridge:
    """Routes incoming messages to different ADK agents based on a resolver.

    This is designed for FastAPI deployments with multiple ADK agent apps.
    Each incoming message is resolved to an app name, then dispatched to that
    app's agent via Runner, HTTP, or a custom runner.

    Example:
        bridge = MultiAppBridge(
            bridge_config=config.bridge,
            registry=registry,
            app_resolver=lambda msg: "support" if "support" in msg.sender else "default",
            agent_factories={
                "support": lambda: Agent(model="gemini-2.0-flash", name="support_bot", ...),
                "default": lambda: Agent(model="gemini-2.0-flash", name="general_bot", ...),
            },
        )
    """

    def __init__(
        self,
        bridge_config: BridgeConfig | None,
        registry: ChannelRegistry,
        app_resolver: AppResolver | None = None,
        agent_factories: dict[str, AgentFactory] | None = None,
        agent_runners: dict[str, AgentRunner] | None = None,
        http_clients: dict[str, Callable[[str, str], Awaitable[str]]] | None = None,
        session_service_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Initialize the multi-app bridge.

        Args:
            bridge_config: Bridge configuration
            registry: Channel registry for sending replies
            app_resolver: Callable that resolves an IncomingMessage to an app name.
                          Defaults to a resolver that always returns "default".
            agent_factories: Dict of app_name -> callable that returns an ADK Agent.
                             Used for direct Runner invocation.
            agent_runners: Dict of app_name -> callable(session_id, text) for custom runners.
            http_clients: Dict of app_name -> async callable(session_id, text) that calls
                          an ADK FastAPI endpoint internally.
            session_service_factory: Optional callable that returns a shared SessionService
                                     (e.g., InMemorySessionService or persistent store).
                                     If not provided, each message gets a fresh session.
        """
        self._config = bridge_config or BridgeConfig()
        self._registry = registry
        self._app_resolver = app_resolver or (lambda _msg: "default")
        self._agent_factories = agent_factories or {}
        self._agent_runners = agent_runners or {}
        self._http_clients = http_clients or {}
        self._session_service_factory = session_service_factory

        # Per-app, per-sender sessions
        # Structure: {app_name: {sender_key: SenderSession}}
        self._sessions: dict[str, dict[str, SenderSession]] = {}
        self._active_count = 0
        self._running = False

        # Shared session service if provided
        self._shared_session_service: Any | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._session_service_factory:
            self._shared_session_service = self._session_service_factory()
        logger.info(
            "Multi-app bridge started with apps: %s",
            list(self._agent_factories.keys()) or list(self._agent_runners.keys()) or list(self._http_clients.keys()),
        )

    def stop(self) -> None:
        self._running = False
        for app_sessions in self._sessions.values():
            for session in app_sessions.values():
                if session.abort_controller:
                    session.abort_controller.cancel()
        self._sessions.clear()
        self._active_count = 0
        self._shared_session_service = None
        logger.info("Multi-app bridge stopped")

    def is_active(self) -> bool:
        return self._running

    async def handle_message(self, message: IncomingMessage) -> None:
        """Handle an incoming message by resolving it to an app and dispatching."""
        if not self._running:
            return

        text = (message.text or "").strip()
        if not text:
            return

        # Resolve app
        app_name_raw = self._app_resolver(message)
        if asyncio.iscoroutine(app_name_raw):
            app_name = await app_name_raw
        else:
            app_name = app_name_raw
        app_name = str(app_name)

        sender_key = f"{message.adapter}:{message.sender}"

        # Get or create app + session
        app_sessions = self._sessions.setdefault(app_name, {})
        session = app_sessions.get(sender_key)
        if not session:
            session = self._create_session(message)
            app_sessions[sender_key] = session

        # Check queue depth
        if len(session.queue) >= self._config.max_queue_per_sender:
            await self._send_reply(
                message.adapter,
                message.sender,
                f"Queue full ({self._config.max_queue_per_sender} pending). Wait for current prompts to finish.",
            )
            return

        # Enqueue
        queued = QueuedPrompt(
            id=_next_id(),
            adapter=message.adapter,
            sender=message.sender,
            text=text,
            attachments=message.attachments,
            metadata={**message.metadata, "app_name": app_name},
            enqueued_at=time.time(),
        )
        session.queue.append(queued)
        session.message_count += 1

        logger.info(
            "Enqueued message %s for app '%s' from %s (queue depth: %d)",
            queued.id,
            app_name,
            sender_key,
            len(session.queue),
        )

        await self._process_next(app_name, sender_key)

    async def _process_next(self, app_name: str, sender_key: str) -> None:
        app_sessions = self._sessions.get(app_name)
        if not app_sessions:
            return
        session = app_sessions.get(sender_key)
        if not session or session.processing or not session.queue:
            return
        if self._active_count >= self._config.max_concurrent:
            return

        session.processing = True
        self._active_count += 1
        prompt = session.queue.pop(0)

        # Typing indicator
        adapter = self._registry.get_adapter(prompt.adapter)
        if adapter and self._config.typing_indicators:
            with __import__("contextlib").suppress(Exception):
                await adapter.send_typing(prompt.sender)

        logger.info("Processing message %s for app '%s' from %s", prompt.id, app_name, sender_key)
        start_time = time.time()

        try:
            result = await self._run_agent_prompt(app_name, prompt, sender_key)
            duration_ms = (time.time() - start_time) * 1000

            if result.ok:
                await self._send_reply(prompt.adapter, prompt.sender, result.response)
            else:
                error_msg = result.error or "Something went wrong. Please try again."
                await self._send_reply(prompt.adapter, prompt.sender, f"Error: {error_msg}")

            logger.info(
                "Completed message %s for app '%s' in %.0fms (ok=%s)",
                prompt.id,
                app_name,
                duration_ms,
                result.ok,
            )

        except Exception as exc:
            logger.exception("Error processing message %s", prompt.id)
            await self._send_reply(prompt.adapter, prompt.sender, f"Unexpected error: {exc}")
        finally:
            session.processing = False
            self._active_count -= 1

            if session.queue:
                await self._process_next(app_name, sender_key)
            await self._drain_waiting()

    async def _run_agent_prompt(self, app_name: str, prompt: QueuedPrompt, sender_key: str) -> RunResult:
        """Run the agent prompt for a specific app."""
        try:
            # Priority: custom runner > HTTP client > agent factory > error
            if app_name in self._agent_runners:
                runner = self._agent_runners[app_name]
                if asyncio.iscoroutinefunction(runner):
                    response = await runner(app_name, sender_key, prompt.text)
                else:
                    response = runner(app_name, sender_key, prompt.text)
                return RunResult(ok=True, response=str(response))

            if app_name in self._http_clients:
                client = self._http_clients[app_name]
                response = await client(sender_key, prompt.text)
                return RunResult(ok=True, response=str(response))

            if app_name in self._agent_factories:
                return await self._run_with_adk_runner(app_name, prompt, sender_key)

            # Fallback to default if no specific app configured
            if "default" in self._agent_runners:
                runner = self._agent_runners["default"]
                if asyncio.iscoroutinefunction(runner):
                    response = await runner("default", sender_key, prompt.text)
                else:
                    response = runner("default", sender_key, prompt.text)
                return RunResult(ok=True, response=str(response))

            if "default" in self._agent_factories:
                return await self._run_with_adk_runner("default", prompt, sender_key)

            return RunResult(
                ok=False,
                response="",
                error=f"No agent configured for app '{app_name}'",
            )
        except Exception as exc:
            logger.exception("Agent execution failed for app '%s'", app_name)
            return RunResult(ok=False, response="", error=str(exc))

    async def _run_with_adk_runner(self, app_name: str, prompt: QueuedPrompt, sender_key: str) -> RunResult:
        """Run using ADK's Runner pattern with optional shared session service."""
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part

        factory = self._agent_factories.get(app_name)
        if not factory:
            return RunResult(ok=False, response="", error=f"No factory for app '{app_name}'")

        agent = factory()

        session_service = self._shared_session_service or InMemorySessionService()  # type: ignore[no-untyped-call]

        runner = Runner(agent=agent, app_name=app_name, session_service=session_service)
        message = Content(role="user", parts=[Part(text=prompt.text)])

        responses = []
        async for event in runner.run_async(
            user_id=sender_key,
            session_id=f"{app_name}:{sender_key}",
            new_message=message,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        responses.append(part.text)

        return RunResult(ok=True, response="\n".join(responses) or "(no response)")

    async def _send_reply(self, adapter: str, recipient: str, text: str) -> None:
        result = await self._registry.send(ChannelMessage(adapter=adapter, recipient=recipient, text=text))
        if not result.get("ok"):
            logger.error("Failed to send reply: %s", result.get("error"))

    async def _drain_waiting(self) -> None:
        if self._active_count >= self._config.max_concurrent:
            return
        for app_name, app_sessions in self._sessions.items():
            for key, session in app_sessions.items():
                if not session.processing and session.queue:
                    await self._process_next(app_name, key)
                    if self._active_count >= self._config.max_concurrent:
                        return

    def _create_session(self, message: IncomingMessage) -> SenderSession:
        display_name = message.metadata.get("user_name") or message.metadata.get("username") or message.sender
        return SenderSession(
            adapter=message.adapter,
            sender=message.sender,
            display_name=str(display_name),
            queue=[],
            processing=False,
            abort_controller=None,
            message_count=0,
            started_at=time.time(),
        )

    def get_stats(self) -> dict[str, Any]:
        total_queued = sum(len(s.queue) for app_sessions in self._sessions.values() for s in app_sessions.values())
        return {
            "active": self._running,
            "apps": list(self._sessions.keys()),
            "sessions": sum(len(s) for s in self._sessions.values()),
            "active_prompts": self._active_count,
            "total_queued": total_queued,
        }
