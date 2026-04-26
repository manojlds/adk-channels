"""Unified chat bridge that routes incoming channel messages to ADK agents.

Supports both single-agent and multi-app routing patterns. Single-agent params
(``agent_runner``, ``agent_factory``) are internally wrapped as ``{"default": ...}``
so all processing follows the multi-app code path.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from adk_channels.adk_events import collect_part_outputs, fallback_response_from_tool_interactions
from adk_channels.config import BridgeConfig
from adk_channels.interactions import InteractionHandler, normalize_interaction_result
from adk_channels.registry import ChannelRegistry
from adk_channels.types import (
    ChannelMessage,
    IncomingMessage,
    QueuedPrompt,
    RunResult,
    SenderSession,
)

logger = logging.getLogger("adk_channels.bridge")

_id_counter = 0


def _next_id() -> str:
    global _id_counter
    _id_counter += 1
    return f"msg-{int(time.time() * 1000)}-{_id_counter}"


AppResolver = Callable[[IncomingMessage], str | Awaitable[str]]
AgentFactory = Callable[[], Any]
AgentRunner = Callable[[str, str, str], Any]  # (app_name, session_id, text) -> response


class ChatBridge:
    """Routes incoming messages to ADK agents and sends responses back.

    Works in two modes:

    * **Single-agent** – pass ``agent_runner`` or ``agent_factory`` for a simple
      bot where every message goes to the same agent.
    * **Multi-app** – pass ``app_resolver`` together with ``agent_factories``,
      ``agent_runners``, or ``http_clients`` to route messages to different
      agents based on the resolver result.
    """

    def __init__(
        self,
        bridge_config: BridgeConfig | None,
        registry: ChannelRegistry,
        # Single-agent convenience params
        agent_runner: Callable[[str, str], Any] | None = None,
        agent_factory: Callable[[], Any] | None = None,
        # Multi-app params
        app_resolver: AppResolver | None = None,
        agent_factories: dict[str, AgentFactory] | None = None,
        agent_runners: dict[str, AgentRunner] | None = None,
        http_clients: dict[str, Callable[[str, str], Awaitable[str]]] | None = None,
        session_service_factory: Callable[[], Any] | None = None,
        # Shared
        interaction_handler: InteractionHandler | None = None,
    ) -> None:
        """Initialize the unified bridge.

        Args:
            bridge_config: Bridge configuration. If ``None``, defaults are used.
            registry: Channel registry for outbound sends and adapter lookup.
            agent_runner: Single-agent convenience runner with signature
                ``(session_id, text) -> response``. When provided, this is merged
                into ``agent_runners`` under the ``"default"`` key.
            agent_factory: Single-agent convenience factory returning an ADK agent.
                When provided, this is merged into ``agent_factories`` under the
                ``"default"`` key.
            app_resolver: Resolver for multi-app mode. Can return ``str`` or
                ``Awaitable[str]``. Defaults to ``"default"`` for all messages.
            agent_factories: Mapping of ``app_name -> agent factory``.
            agent_runners: Mapping of ``app_name -> runner`` where runner
                signature is ``(app_name, session_id, text) -> response``.
            http_clients: Mapping of ``app_name -> async HTTP client`` with
                signature ``(session_id, text) -> response``.
            session_service_factory: Optional factory for a shared ADK
                SessionService instance.
            interaction_handler: Optional callback invoked before agent routing
                for interactive events (for example Slack block actions).

        Notes:
            - Explicit ``agent_runners`` / ``agent_factories`` entries take
              precedence over single-agent convenience params because merges use
              ``setdefault("default", ...)``.
            - Dispatch priority is:
              ``agent_runners -> http_clients -> agent_factories``.
            - If the resolved app has no dispatch config but ``default`` does,
              dispatch falls back to ``default``.
        """
        self._config = bridge_config or BridgeConfig()
        self._registry = registry
        self._interaction_handler = interaction_handler
        self._session_service_factory = session_service_factory

        # --- Merge single-agent params into multi-app dicts ----------------
        merged_runners: dict[str, AgentRunner] = dict(agent_runners) if agent_runners else {}
        merged_factories: dict[str, AgentFactory] = dict(agent_factories) if agent_factories else {}

        if agent_runner is not None:
            _orig = agent_runner

            async def _wrapped_runner(_app: str, sid: str, text: str) -> Any:
                if asyncio.iscoroutinefunction(_orig):
                    return await _orig(sid, text)
                return _orig(sid, text)

            merged_runners.setdefault("default", _wrapped_runner)

        if agent_factory is not None:
            merged_factories.setdefault("default", agent_factory)

        self._app_resolver: AppResolver = app_resolver or (lambda _msg: "default")
        self._agent_factories = merged_factories
        self._agent_runners = merged_runners
        self._http_clients: dict[str, Callable[[str, str], Awaitable[str]]] = dict(http_clients) if http_clients else {}

        # Per-app, per-sender sessions: {app_name: {sender_key: SenderSession}}
        self._sessions: dict[str, dict[str, SenderSession]] = {}
        self._active_count = 0
        self._running = False

        self._shared_session_service: Any | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._session_service_factory:
            self._shared_session_service = self._session_service_factory()
        elif self._agent_factories:
            from google.adk.sessions import InMemorySessionService

            self._shared_session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
        logger.info(
            "Chat bridge started with apps: %s",
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
        logger.info("Chat bridge stopped")

    def is_active(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def handle_message(self, message: IncomingMessage) -> None:
        """Handle an incoming message by resolving it to an app and dispatching."""
        if not self._running:
            return

        if await self._dispatch_interaction(message):
            return

        text = (message.text or "").strip()
        if not text:
            return

        now = time.time()
        self._prune_idle_sessions(now)

        # Resolve app
        app_name_raw = self._app_resolver(message)
        if asyncio.iscoroutine(app_name_raw):
            app_name = await app_name_raw
        else:
            app_name = app_name_raw
        app_name = str(app_name)

        sender_key = self._resolve_sender_key(message)

        # Get or create app + session
        app_sessions = self._sessions.setdefault(app_name, {})
        session = app_sessions.get(sender_key)
        if (
            session is None
            and self._requires_existing_session(message)
            and not await self._existing_dispatch_session_exists(app_name, sender_key)
        ):
            logger.info(
                "Ignoring message for app=%s key=%s because it requires an existing session",
                app_name,
                sender_key,
            )
            return

        if not session:
            session = self._create_session(message)
            app_sessions[sender_key] = session
            logger.info(
                "Created session app=%s key=%s (mode=%s scope=%s)",
                app_name,
                sender_key,
                self._resolve_session_mode(app_name, sender_key),
                self._config.session_scope,
            )

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
        session.last_activity_at = now

        logger.info(
            "Enqueued message %s for app '%s' from %s (queue depth: %d)",
            queued.id,
            app_name,
            sender_key,
            len(session.queue),
        )

        self._schedule_process(app_name, sender_key)

    # ------------------------------------------------------------------
    # Processing pipeline
    # ------------------------------------------------------------------

    def _schedule_process(self, app_name: str, sender_key: str) -> None:
        """Synchronously claim a processing slot and spawn a background task."""
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

        asyncio.create_task(self._process_prompt(app_name, sender_key, prompt))

    async def _process_prompt(self, app_name: str, sender_key: str, prompt: QueuedPrompt) -> None:
        app_sessions = self._sessions.get(app_name)
        session = app_sessions.get(sender_key) if app_sessions else None
        if not session:
            self._active_count -= 1
            return

        # Typing indicator
        adapter = self._registry.get_adapter(prompt.adapter)
        if adapter and self._config.typing_indicators:
            with suppress(Exception):
                await adapter.send_typing(prompt.sender)

        logger.info("Processing message %s for app '%s' from %s", prompt.id, app_name, sender_key)
        start_time = time.time()

        try:
            timeout_seconds = self._config.timeout_ms / 1000 if self._config.timeout_ms > 0 else None
            if timeout_seconds is None:
                result = await self._run_agent_prompt(app_name, prompt, sender_key)
            else:
                result = await asyncio.wait_for(
                    self._run_agent_prompt(app_name, prompt, sender_key),
                    timeout=timeout_seconds,
                )
            duration_ms = (time.time() - start_time) * 1000

            if result.ok:
                await self._send_reply(
                    prompt.adapter,
                    prompt.sender,
                    result.response,
                    thoughts=result.thoughts,
                    tool_interactions=result.tool_interactions,
                )
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

        except asyncio.TimeoutError:
            logger.warning(
                "Message %s for app '%s' timed out after %dms",
                prompt.id,
                app_name,
                self._config.timeout_ms,
            )
            await self._send_reply(
                prompt.adapter,
                prompt.sender,
                "Error: Request timed out. Please try again with a shorter prompt.",
            )

        except Exception as exc:
            logger.exception("Error processing message %s", prompt.id)
            await self._send_reply(prompt.adapter, prompt.sender, f"Unexpected error: {exc}")
        finally:
            session.processing = False
            self._active_count -= 1
            session.last_activity_at = time.time()

            if session.queue:
                self._schedule_process(app_name, sender_key)
            self._drain_waiting_sync()

    async def _dispatch_interaction(self, message: IncomingMessage) -> bool:
        handler = self._interaction_handler
        if handler is None:
            return False

        try:
            raw_result = handler(message)
            if asyncio.iscoroutine(raw_result):
                result = await raw_result
            else:
                result = raw_result

            normalized = normalize_interaction_result(message, result)
            if normalized is None or not normalized.handled:
                return False

            for reply in normalized.replies:
                send_result = await self._registry.send(reply)
                if not send_result.get("ok"):
                    logger.error("Failed to send interaction reply: %s", send_result.get("error"))

            return True
        except Exception:
            logger.exception("Interaction handler failed")
            return False

    # ------------------------------------------------------------------
    # Agent dispatch
    # ------------------------------------------------------------------

    async def _run_agent_prompt(self, app_name: str, prompt: QueuedPrompt, sender_key: str) -> RunResult:
        """Run the agent prompt for a specific app."""
        dispatch_app_name = self._resolve_dispatch_app_name(app_name)
        if dispatch_app_name is None:
            return RunResult(
                ok=False,
                response="",
                error=f"No agent configured for app '{app_name}'",
            )

        if dispatch_app_name != app_name:
            logger.warning("No dispatch configured for app '%s', falling back to 'default'", app_name)

        try:
            return await self._dispatch_for_app(dispatch_app_name, prompt, sender_key)
        except Exception as exc:
            if dispatch_app_name != app_name:
                logger.exception(
                    "Agent execution failed for resolved app '%s' (dispatching to '%s')",
                    app_name,
                    dispatch_app_name,
                )
            else:
                logger.exception("Agent execution failed for app '%s'", app_name)
            return RunResult(ok=False, response="", error=str(exc))

    def _has_dispatch_target(self, app_name: str) -> bool:
        return app_name in self._agent_runners or app_name in self._http_clients or app_name in self._agent_factories

    @staticmethod
    def _requires_existing_session(message: IncomingMessage) -> bool:
        return bool(message.metadata.get("requires_existing_session"))

    def _resolve_dispatch_app_name(self, app_name: str) -> str | None:
        if self._has_dispatch_target(app_name):
            return app_name
        if self._has_dispatch_target("default"):
            return "default"
        return None

    async def _existing_dispatch_session_exists(self, app_name: str, sender_key: str) -> bool:
        dispatch_app_name = self._resolve_dispatch_app_name(app_name)
        if dispatch_app_name is None:
            return False

        if self._shared_session_service is None:
            logger.debug(
                "Cannot verify existing session for app=%s key=%s without a shared session service",
                dispatch_app_name,
                sender_key,
            )
            return False

        session_mode = self._resolve_session_mode(dispatch_app_name, sender_key)
        if session_mode != "persistent":
            return False

        run_session_id = self._build_run_session_id(dispatch_app_name, sender_key, "existing", session_mode)
        try:
            session = await self._shared_session_service.get_session(
                app_name=dispatch_app_name,
                user_id=sender_key,
                session_id=run_session_id,
            )
        except Exception:
            logger.exception("Failed to check existing session app=%s key=%s", dispatch_app_name, sender_key)
            return False

        return session is not None

    async def _dispatch_for_app(self, app_name: str, prompt: QueuedPrompt, sender_key: str) -> RunResult:
        """Dispatch a prompt to one configured app target."""
        session_mode = self._resolve_session_mode(app_name, sender_key)
        run_session_id = self._build_run_session_id(app_name, sender_key, prompt.id, session_mode)

        if app_name in self._agent_runners:
            runner = self._agent_runners[app_name]
            if asyncio.iscoroutinefunction(runner):
                response = await runner(app_name, run_session_id, prompt.text)
            else:
                response = runner(app_name, run_session_id, prompt.text)
            return RunResult(ok=True, response=str(response))

        if app_name in self._http_clients:
            client = self._http_clients[app_name]
            response = await client(run_session_id, prompt.text)
            return RunResult(ok=True, response=str(response))

        if app_name in self._agent_factories:
            return await self._run_with_adk_runner(app_name, prompt, sender_key, run_session_id)

        return RunResult(ok=False, response="", error=f"No agent configured for app '{app_name}'")

    async def _run_with_adk_runner(
        self,
        app_name: str,
        prompt: QueuedPrompt,
        sender_key: str,
        run_session_id: str,
    ) -> RunResult:
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

        session = await session_service.get_session(
            app_name=app_name,
            user_id=sender_key,
            session_id=run_session_id,
        )
        if session is None:
            await session_service.create_session(
                app_name=app_name,
                user_id=sender_key,
                session_id=run_session_id,
            )

        message = Content(role="user", parts=[Part(text=prompt.text)])

        thoughts: list[str] = []
        responses: list[str] = []
        tool_interactions: list[dict[str, Any]] = []
        async for event in runner.run_async(
            user_id=sender_key,
            session_id=run_session_id,
            new_message=message,
        ):
            if event.content and event.content.parts:
                part_thoughts, part_responses, part_tools = collect_part_outputs(event.content.parts)
                thoughts.extend(part_thoughts)
                responses.extend(part_responses)
                tool_interactions.extend(part_tools)

        response_text = "\n".join(responses).strip()
        if not response_text:
            response_text = fallback_response_from_tool_interactions(tool_interactions) or "(no response)"

        return RunResult(
            ok=True,
            response=response_text,
            thoughts=thoughts,
            tool_interactions=tool_interactions,
        )

    # ------------------------------------------------------------------
    # Reply helpers
    # ------------------------------------------------------------------

    async def _send_reply(
        self,
        adapter: str,
        recipient: str,
        text: str,
        thoughts: list[str] | None = None,
        tool_interactions: list[dict[str, Any]] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {}
        if thoughts:
            metadata["thoughts"] = thoughts
        if tool_interactions:
            metadata["tool_interactions"] = tool_interactions
        result = await self._registry.send(
            ChannelMessage(adapter=adapter, recipient=recipient, text=text, metadata=metadata)
        )
        if not result.get("ok"):
            logger.error("Failed to send reply: %s", result.get("error"))

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------

    def _drain_waiting_sync(self) -> None:
        """Schedule waiting senders when a slot frees up."""
        if self._active_count >= self._config.max_concurrent:
            return
        for app_name, app_sessions in self._sessions.items():
            for key, session in app_sessions.items():
                if not session.processing and session.queue:
                    self._schedule_process(app_name, key)
                    if self._active_count >= self._config.max_concurrent:
                        return

    # ------------------------------------------------------------------
    # Sender / session resolution
    # ------------------------------------------------------------------

    def _resolve_sender_key(self, message: IncomingMessage) -> str:
        identity = self._resolve_sender_identity(message)
        return f"{message.adapter}:{identity}"

    def _resolve_sender_identity(self, message: IncomingMessage) -> str:
        scope = self._config.session_scope
        metadata = message.metadata

        sender_channel, sender_thread = self._split_sender_thread(message.sender)
        channel_id_raw = metadata.get("channel_id")
        channel_id = str(channel_id_raw) if channel_id_raw is not None else sender_channel
        thread_ts_raw = metadata.get("thread_ts")
        thread_ts = str(thread_ts_raw) if thread_ts_raw is not None else sender_thread

        if scope == "user":
            user_id = metadata.get("user_id")
            if user_id is not None:
                return f"user:{user_id}"
            username = metadata.get("user_name") or metadata.get("username")
            if username is not None:
                return f"user:{username}"
            return message.sender

        if scope == "channel":
            return f"channel:{channel_id or message.sender}"

        if scope == "thread":
            if thread_ts:
                return f"thread:{channel_id or sender_channel}:{thread_ts}"
            return f"channel:{channel_id or sender_channel}"

        return message.sender

    @staticmethod
    def _split_sender_thread(sender: str) -> tuple[str, str | None]:
        if ":" not in sender:
            return sender, None
        channel, thread = sender.split(":", 1)
        return channel, thread or None

    def _resolve_session_mode(self, app_name: str, sender_key: str) -> str:
        app_sender_key = f"{app_name}:{sender_key}"
        for rule in self._config.session_rules:
            if fnmatch.fnmatch(app_sender_key, rule.pattern) or fnmatch.fnmatch(sender_key, rule.pattern):
                return rule.mode
        return self._config.session_mode

    @staticmethod
    def _build_run_session_id(app_name: str, sender_key: str, prompt_id: str, session_mode: str) -> str:
        base = f"{app_name}:{sender_key}"
        if session_mode == "persistent":
            return base
        return f"{base}:{prompt_id}"

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def _prune_idle_sessions(self, now: float) -> None:
        idle_timeout_minutes = self._config.idle_timeout_minutes
        if idle_timeout_minutes <= 0:
            return

        idle_timeout_seconds = idle_timeout_minutes * 60
        removed = 0
        for app_name in list(self._sessions.keys()):
            app_sessions = self._sessions.get(app_name)
            if app_sessions is None:
                continue

            stale_keys = [
                key
                for key, session in app_sessions.items()
                if not session.processing
                and not session.queue
                and now - (session.last_activity_at or session.started_at) > idle_timeout_seconds
            ]

            for key in stale_keys:
                app_sessions.pop(key, None)
                removed += 1

            if not app_sessions:
                self._sessions.pop(app_name, None)

        if removed:
            logger.info("Pruned %d idle sessions", removed)

    def _create_session(self, message: IncomingMessage) -> SenderSession:
        display_name = message.metadata.get("user_name") or message.metadata.get("username") or message.sender
        now = time.time()
        return SenderSession(
            adapter=message.adapter,
            sender=message.sender,
            display_name=str(display_name),
            queue=[],
            processing=False,
            abort_controller=None,
            message_count=0,
            started_at=now,
            last_activity_at=now,
        )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        total_queued = sum(len(s.queue) for app_sessions in self._sessions.values() for s in app_sessions.values())
        return {
            "active": self._running,
            "apps": list(self._sessions.keys()),
            "sessions": sum(len(s) for s in self._sessions.values()),
            "active_prompts": self._active_count,
            "total_queued": total_queued,
        }
