"""Chat bridge that routes incoming channel messages to an ADK agent."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
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
    ) -> None:
        """Initialize the chat bridge.

        Args:
            bridge_config: Bridge configuration
            registry: Channel registry for sending replies
            agent_runner: Optional async callable(session_id, text) -> response_str
            agent_factory: Optional callable that returns an ADK agent/Runner
        """
        self._config = bridge_config or BridgeConfig()
        self._registry = registry
        self._agent_runner = agent_runner
        self._agent_factory = agent_factory
        self._sessions: dict[str, SenderSession] = {}
        self._active_count = 0
        self._running = False

    def start(self) -> None:
        """Start the chat bridge."""
        if self._running:
            return
        self._running = True
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

        text = (message.text or "").strip()
        if not text:
            return

        sender_key = f"{message.adapter}:{message.sender}"

        # Get or create session
        session = self._sessions.get(sender_key)
        if not session:
            session = self._create_session(message)
            self._sessions[sender_key] = session

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
            with __import__("contextlib").suppress(Exception):
                await adapter.send_typing(prompt.sender)

        logger.info("Processing message %s from %s", prompt.id, sender_key)
        start_time = time.time()

        try:
            result = await self._run_agent_prompt(prompt, sender_key)
            duration_ms = (time.time() - start_time) * 1000

            if result.ok:
                await self._send_reply(prompt.adapter, prompt.sender, result.response)
            else:
                error_msg = result.error or "Something went wrong. Please try again."
                await self._send_reply(prompt.adapter, prompt.sender, f"Error: {error_msg}")

            logger.info(
                "Completed message %s in %.0fms (ok=%s)",
                prompt.id,
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
                await self._process_next(sender_key)
            await self._drain_waiting()

    async def _run_agent_prompt(self, prompt: QueuedPrompt, sender_key: str) -> RunResult:
        """Run the ADK agent with the given prompt."""
        try:
            if self._agent_runner:
                # User-provided runner
                if asyncio.iscoroutinefunction(self._agent_runner):
                    response = await self._agent_runner(sender_key, prompt.text)
                else:
                    response = self._agent_runner(sender_key, prompt.text)
                return RunResult(ok=True, response=str(response))
            elif self._agent_factory:
                # ADK Runner pattern
                from google.adk.runners import Runner
                from google.adk.sessions import InMemorySessionService

                agent = self._agent_factory()
                session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
                runner = Runner(agent=agent, app_name="adk-channels", session_service=session_service)

                # Use sender_key as session ID for persistent conversations
                content = prompt.text
                from google.genai.types import Content, Part

                message = Content(role="user", parts=[Part(text=content)])

                responses = []
                async for event in runner.run_async(
                    user_id=sender_key,
                    session_id=sender_key,
                    new_message=message,
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                responses.append(part.text)

                return RunResult(ok=True, response="\n".join(responses) or "(no response)")
            else:
                return RunResult(
                    ok=False,
                    response="",
                    error="No agent_runner or agent_factory configured",
                )
        except Exception as exc:
            logger.exception("Agent execution failed")
            return RunResult(ok=False, response="", error=str(exc))

    async def _send_reply(self, adapter: str, recipient: str, text: str) -> None:
        """Send a reply back through the originating adapter."""
        result = await self._registry.send(ChannelMessage(adapter=adapter, recipient=recipient, text=text))
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
        total_queued = sum(len(s.queue) for s in self._sessions.values())
        return {
            "active": self._running,
            "sessions": len(self._sessions),
            "active_prompts": self._active_count,
            "total_queued": total_queued,
        }
