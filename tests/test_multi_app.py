"""Tests for multi-app bridge and server integration."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from adk_channels.config import BridgeConfig
from adk_channels.multi_app_bridge import MultiAppBridge
from adk_channels.registry import ChannelRegistry
from adk_channels.types import IncomingMessage


class FakeAdapter:
    direction = "bidirectional"

    def __init__(self):
        self.sent_messages = []
        self.started = False
        self.stopped = False

    async def send(self, message):
        self.sent_messages.append(message)

    async def start(self, on_message):
        self.started = True
        self.on_message = on_message

    async def stop(self):
        self.stopped = True


@pytest.fixture
def fake_adapter():
    return FakeAdapter()


@pytest.fixture
def registry(fake_adapter):
    reg = ChannelRegistry()
    reg.register("slack", fake_adapter)
    return reg


class TestMultiAppBridge:
    @pytest.mark.asyncio
    async def test_multi_app_routing(self, registry, fake_adapter):
        """Test that messages are routed to different apps."""
        calls = []

        async def support_runner(app, session, text):
            calls.append(("support", text))
            return "Support: got it"

        async def eng_runner(app, session, text):
            calls.append(("engineering", text))
            return "Eng: roger"

        def resolver(msg):
            return "support" if "support" in msg.sender else "engineering"

        bridge = MultiAppBridge(
            bridge_config=BridgeConfig(enabled=True, max_concurrent=2),
            registry=registry,
            app_resolver=resolver,
            agent_runners={
                "support": support_runner,
                "engineering": eng_runner,
            },
        )
        bridge.start()

        # Message to support channel
        msg1 = IncomingMessage(adapter="slack", sender="support-channel", text="help")
        await bridge.handle_message(msg1)

        # Message to engineering channel
        msg2 = IncomingMessage(adapter="slack", sender="eng-channel", text="deploy")
        await bridge.handle_message(msg2)

        await asyncio.sleep(0.2)
        bridge.stop()

        assert ("support", "help") in calls
        assert ("engineering", "deploy") in calls

    @pytest.mark.asyncio
    async def test_default_app_fallback(self, registry, fake_adapter):
        """Test fallback to default app when resolver returns unknown app."""
        calls = []

        async def default_runner(app, session, text):
            calls.append(("default", text))
            return "Default reply"

        bridge = MultiAppBridge(
            bridge_config=BridgeConfig(enabled=True, max_concurrent=1),
            registry=registry,
            agent_runners={"default": default_runner},
        )
        bridge.start()

        msg = IncomingMessage(adapter="slack", sender="U123", text="hello")
        await bridge.handle_message(msg)
        await asyncio.sleep(0.1)

        bridge.stop()

        assert ("default", "hello") in calls

    @pytest.mark.asyncio
    async def test_stateless_session_mode_uses_unique_session_ids(self, registry):
        seen_session_ids = []

        async def default_runner(app, session, text):
            seen_session_ids.append(session)
            return "ok"

        bridge = MultiAppBridge(
            bridge_config=BridgeConfig(enabled=True, session_mode="stateless"),
            registry=registry,
            agent_runners={"default": default_runner},
        )
        bridge.start()

        await bridge.handle_message(IncomingMessage(adapter="slack", sender="C123", text="hello"))
        await bridge.handle_message(IncomingMessage(adapter="slack", sender="C123", text="again"))

        bridge.stop()

        assert len(seen_session_ids) == 2
        assert seen_session_ids[0] != seen_session_ids[1]
        assert all(session_id.startswith("default:slack:C123") for session_id in seen_session_ids)

    @pytest.mark.asyncio
    async def test_stats(self, registry):
        bridge = MultiAppBridge(
            bridge_config=BridgeConfig(enabled=True),
            registry=registry,
        )
        bridge.start()
        stats = bridge.get_stats()
        assert stats["active"] is True
        assert stats["total_queued"] == 0
        bridge.stop()

    @pytest.mark.asyncio
    async def test_queue_full(self, registry, fake_adapter):
        """Test queue depth limit."""

        async def slow_runner(app, session, text):
            await asyncio.sleep(1)
            return "done"

        bridge = MultiAppBridge(
            bridge_config=BridgeConfig(enabled=True, max_queue_per_sender=1, max_concurrent=1),
            registry=registry,
            agent_runners={"default": slow_runner},
        )
        bridge.start()

        # First message starts processing (sleeps 1s)
        msg1 = IncomingMessage(adapter="slack", sender="U123", text="slow")
        await bridge.handle_message(msg1)
        await asyncio.sleep(0.05)  # Let it start processing

        # Second message queues
        msg2 = IncomingMessage(adapter="slack", sender="U123", text="queued")
        await bridge.handle_message(msg2)

        # Third message should be rejected (queue full)
        msg3 = IncomingMessage(adapter="slack", sender="U123", text="rejected")
        await bridge.handle_message(msg3)
        await asyncio.sleep(0.1)

        bridge.stop()

        # Should have: slow reply, queued reply, and queue full error
        assert len(fake_adapter.sent_messages) >= 2

    @pytest.mark.asyncio
    async def test_sync_runner(self, registry, fake_adapter):
        """Test that sync runners work too."""

        def sync_runner(app, session, text):
            return f"Sync: {text}"

        bridge = MultiAppBridge(
            bridge_config=BridgeConfig(enabled=True, max_concurrent=1),
            registry=registry,
            agent_runners={"default": sync_runner},
        )
        bridge.start()

        msg = IncomingMessage(adapter="slack", sender="U123", text="test")
        await bridge.handle_message(msg)
        await asyncio.sleep(0.1)

        bridge.stop()

        assert any("Sync: test" in m.text for m in fake_adapter.sent_messages)

    @pytest.mark.asyncio
    async def test_interaction_handler_short_circuits_app_routing(self, registry, fake_adapter):
        calls = []

        async def default_runner(app, session, text):
            calls.append((app, text))
            return "Default reply"

        async def interaction_handler(message: IncomingMessage):
            if message.metadata.get("event_type") == "block_action":
                return "Handled interaction"
            return None

        bridge = MultiAppBridge(
            bridge_config=BridgeConfig(enabled=True),
            registry=registry,
            agent_runners={"default": default_runner},
            interaction_handler=interaction_handler,
        )
        bridge.start()

        await bridge.handle_message(
            IncomingMessage(
                adapter="slack",
                sender="C123:thread-1",
                text="action:adk.tool.approval.approve",
                metadata={
                    "event_type": "block_action",
                    "tool_name": "approval",
                    "tool_action": "approve",
                    "action_value": '{"request_id":"req-1"}',
                },
            )
        )

        bridge.stop()

        assert calls == []
        assert any("Handled interaction" in (m.text or "") for m in fake_adapter.sent_messages)


class TestServerIntegration:
    @pytest.mark.asyncio
    async def test_create_fastapi_app(self):
        pytest.importorskip("fastapi")
        from adk_channels.server_integration import create_fastapi_app

        app = create_fastapi_app(
            agents={"default": MagicMock()},
        )
        assert app is not None

    @pytest.mark.asyncio
    async def test_create_fastapi_app_with_interaction_handler(self):
        pytest.importorskip("fastapi")
        from adk_channels.server_integration import create_fastapi_app

        async def interaction_handler(message: IncomingMessage):
            return None

        app = create_fastapi_app(
            agents={"default": MagicMock()},
            interaction_handler=interaction_handler,
        )
        assert app is not None

    @pytest.mark.asyncio
    async def test_integration_setup(self):
        pytest.importorskip("fastapi")
        from fastapi import FastAPI

        from adk_channels.server_integration import ChannelsFastAPIIntegration

        app = FastAPI()
        registry = ChannelRegistry()
        bridge = MagicMock()

        integration = ChannelsFastAPIIntegration(
            fastapi_app=app,
            registry=registry,
            bridge=bridge,
        )
        integration.setup()

        # Should have routes registered
        routes = [r.path for r in app.routes]
        assert "/channels/health" in routes
        assert "/channels/status" in routes
        assert "/channels/webhook/{adapter_name}" in routes
