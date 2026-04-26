"""Chat bridge that routes incoming channel messages to an ADK agent."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from collections.abc import Callable
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


class ChatBridge:
    """Routes incoming messages to an ADK agent and sends responses back."""

    def __init__(
        self,
        bridge_config: BridgeConfig | None,
        registry: ChannelRegistry,
        agent_runner: Callable[[str, str], Any] | None = None,
        agent_factory: Callable[[], Any] | None = None,
        interaction_handler: InteractionHandler | None = None,
    ) -> None:
        """Initialize the chat bridge.

        Args:
            bridge_config: Bridge configuration
            registry: Channel registry for sending replies
            agent_runner: Optional async callable(session_id, text) -> response_str
            agent_factory: Optional callable that returns an ADK agent/Runner
            interaction_handler: Optional callable to handle interactive messages
                                 (for example Slack block actions) before agent execution.
        """
        self._config = bridge_config or BridgeConfig()
        self._registry = registry
        self._agent_runner = agent_runner
        self._agent_factory = agent_factory
        self._interaction_handler = interaction_handler
        self._sessions: dict[str, SenderSession] = {}
        self._active_count = 0
        self._running = False
        self._session_service: Any | None = None

    def start(self) -> None:
        """Start the chat bridge."""
        if self._running:
            return
        self._running = True
        if self._agent_factory:
            from google.adk.sessions import InMemorySessionService

            self._session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
        logger.info("Chat bridge started")

    def stop(self) -> None:
        """Stop the chat bridge."""
        self._running = False
        for session in self._sessions.values():
            if session.abort_controller:
                session.abort_controller.cancel()
        self._sessions.clear()
        self._active_count = 0
        logger.info("Chat bridge stopped")

    def is_active(self) -> bool:
        return self._running

    async def handle_message(self, message: IncomingMessage) -> None:
        """Handle an incoming message from any adapter."""
        if not self._running:
            return

        if await self._dispatch_interaction(message):
            return

        text = (message.text or "").strip()
        if not text:
            return

        now = time.time()
        self._prune_idle_sessions(now)

        sender_key = self._resolve_sender_key(message)

        # Get or create session
        session = self._sessions.get(sender_key)
        if not session:
            session = self._create_session(message)
            self._sessions[sender_key] = session
            logger.info(
                "Created bridge session %s (mode=%s scope=%s)",
                sender_key,
                self._resolve_session_mode(sender_key),
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
            metadata=message.metadata,
            enqueued_at=time.time(),
        )
        session.queue.append(queued)
        session.message_count += 1
        session.last_activity_at = now

        logger.info(
            "Enqueued message %s from %s (queue depth: %d)",
            queued.id,
            sender_key,
            len(session.queue),
        )

        await self._process_next(sender_key)

    async def _process_next(self, sender_key: str) -> None:
        session = self._sessions.get(sender_key)
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
            with suppress(Exception):
                await adapter.send_typing(prompt.sender)

        logger.info("Processing message %s from %s", prompt.id, sender_key)
        start_time = time.time()

        try:
            timeout_seconds = self._config.timeout_ms / 1000 if self._config.timeout_ms > 0 else None
            if timeout_seconds is None:
                result = await self._run_agent_prompt(prompt, sender_key)
            else:
                result = await asyncio.wait_for(self._run_agent_prompt(prompt, sender_key), timeout=timeout_seconds)
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
                "Completed message %s in %.0fms (ok=%s)",
                prompt.id,
                duration_ms,
                result.ok,
            )

        except asyncio.TimeoutError:
            logger.warning("Message %s timed out after %dms", prompt.id, self._config.timeout_ms)
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
                await self._process_next(sender_key)
            await self._drain_waiting()

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

    async def _run_agent_prompt(self, prompt: QueuedPrompt, sender_key: str) -> RunResult:
        """Run the ADK agent with the given prompt."""
        session_mode = self._resolve_session_mode(sender_key)
        run_session_id = sender_key if session_mode == "persistent" else f"{sender_key}:{prompt.id}"

        try:
            if self._agent_runner:
                # User-provided runner
                if asyncio.iscoroutinefunction(self._agent_runner):
                    response = await self._agent_runner(run_session_id, prompt.text)
                else:
                    response = self._agent_runner(run_session_id, prompt.text)
                return RunResult(ok=True, response=str(response))
            elif self._agent_factory:
                # ADK Runner pattern
                from google.adk.runners import Runner
                from google.adk.sessions import InMemorySessionService
                from google.genai.types import Content, Part

                agent = self._agent_factory()
                session_service = self._session_service
                if session_service is None:
                    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
                    self._session_service = session_service
                runner = Runner(agent=agent, app_name="adk-channels", session_service=session_service)

                # Create session if it doesn't exist
                session = await session_service.get_session(
                    app_name="adk-channels",
                    user_id=sender_key,
                    session_id=run_session_id,
                )
                if session is None:
                    await session_service.create_session(
                        app_name="adk-channels",
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

                if thoughts:
                    logger.debug("Thoughts captured (%s): %d thought parts", sender_key, len(thoughts))

                response_text = "\n".join(responses).strip()
                if not response_text:
                    response_text = fallback_response_from_tool_interactions(tool_interactions) or "(no response)"

                return RunResult(
                    ok=True,
                    response=response_text,
                    thoughts=thoughts,
                    tool_interactions=tool_interactions,
                )
            else:
                return RunResult(
                    ok=False,
                    response="",
                    error="No agent_runner or agent_factory configured",
                )
        except Exception as exc:
            logger.exception("Agent execution failed")
            return RunResult(ok=False, response="", error=str(exc))

    async def _send_reply(
        self,
        adapter: str,
        recipient: str,
        text: str,
        thoughts: list[str] | None = None,
        tool_interactions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Send a reply back through the originating adapter."""
        metadata: dict[str, Any] = {}
        if thoughts:
            metadata["thoughts"] = thoughts
        if tool_interactions:
            metadata["tool_interactions"] = tool_interactions
        result = await self._registry.send(
            ChannelMessage(
                adapter=adapter,
                recipient=recipient,
                text=text,
                metadata=metadata,
            )
        )
        if not result.get("ok"):
            logger.error("Failed to send reply: %s", result.get("error"))

    async def _drain_waiting(self) -> None:
        """Process waiting senders when a slot frees up."""
        if self._active_count >= self._config.max_concurrent:
            return
        for key, session in self._sessions.items():
            if not session.processing and session.queue:
                await self._process_next(key)
                if self._active_count >= self._config.max_concurrent:
                    break

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

    def _resolve_session_mode(self, sender_key: str) -> str:
        for rule in self._config.session_rules:
            if fnmatch.fnmatch(sender_key, rule.pattern):
                return rule.mode
        return self._config.session_mode

    def _prune_idle_sessions(self, now: float) -> None:
        idle_timeout_minutes = self._config.idle_timeout_minutes
        if idle_timeout_minutes <= 0:
            return

        idle_timeout_seconds = idle_timeout_minutes * 60
        stale_keys = [
            key
            for key, session in self._sessions.items()
            if not session.processing
            and not session.queue
            and now - (session.last_activity_at or session.started_at) > idle_timeout_seconds
        ]

        for key in stale_keys:
            self._sessions.pop(key, None)

        if stale_keys:
            logger.info("Pruned %d idle bridge sessions", len(stale_keys))

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

    def get_stats(self) -> dict[str, Any]:
        total_queued = sum(len(s.queue) for s in self._sessions.values())
        return {
            "active": self._running,
            "sessions": len(self._sessions),
            "active_prompts": self._active_count,
            "total_queued": total_queued,
        }
