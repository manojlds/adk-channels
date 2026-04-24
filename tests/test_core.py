"""Tests for adk-channels core components."""

from __future__ import annotations

import asyncio

import pytest

from adk_channels.adapters.base import BaseChannelAdapter
from adk_channels.config import AdapterConfig, BridgeConfig, ChannelsConfig, RouteConfig
from adk_channels.registry import ChannelRegistry
from adk_channels.types import (
    AdapterDirection,
    ChannelMessage,
    IncomingMessage,
    RunResult,
    SenderSession,
)


class FakeAdapter(BaseChannelAdapter):
    """Fake adapter for testing."""

    direction = AdapterDirection.BIDIRECTIONAL

    def __init__(self) -> None:
        self.sent_messages: list[ChannelMessage] = []
        self.started = False
        self.stopped = False
        self.on_message = None

    async def send(self, message: ChannelMessage) -> None:
        self.sent_messages.append(message)

    async def start(self, on_message) -> None:
        self.started = True
        self.on_message = on_message

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_adapter():
    return FakeAdapter()


class TestTypes:
    def test_incoming_message_creation(self):
        msg = IncomingMessage(adapter="slack", sender="U123", text="hello", metadata={"key": "value"})
        assert msg.adapter == "slack"
        assert msg.text == "hello"

    def test_channel_message_creation(self):
        msg = ChannelMessage(adapter="slack", recipient="C123", text="hi")
        assert msg.recipient == "C123"

    def test_run_result(self):
        result = RunResult(ok=True, response="hello")
        assert result.ok
        assert result.response == "hello"


class TestConfig:
    def test_adapter_config(self):
        cfg = AdapterConfig(type="slack")
        assert cfg.type == "slack"

    def test_bridge_config_defaults(self):
        cfg = BridgeConfig()
        assert cfg.enabled is False
        assert cfg.session_mode == "persistent"
        assert cfg.max_concurrent == 2

    def test_channels_config_from_env(self, monkeypatch):
        monkeypatch.setenv("ADK_CHANNELS_ADAPTERS__SLACK__TYPE", "slack")
        monkeypatch.setenv("ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("ADK_CHANNELS_BRIDGE__ENABLED", "true")

        config = ChannelsConfig()
        assert "slack" in config.adapters
        assert config.adapters["slack"].type == "slack"
        assert config.bridge.enabled is True

    def test_route_config(self):
        route = RouteConfig(adapter="slack", recipient="C123")
        assert route.adapter == "slack"
        assert route.recipient == "C123"


class TestRegistry:
    @pytest.mark.asyncio
    async def test_register_custom_adapter(self, fake_adapter):
        registry = ChannelRegistry()
        registry.register("test", fake_adapter)

        adapters = registry.list_adapters()
        assert any(a["name"] == "test" for a in adapters)

    @pytest.mark.asyncio
    async def test_send_via_adapter(self, fake_adapter):
        registry = ChannelRegistry()
        registry.register("test", fake_adapter)

        result = await registry.send(ChannelMessage(adapter="test", recipient="R123", text="hello"))
        assert result["ok"] is True
        assert len(fake_adapter.sent_messages) == 1
        assert fake_adapter.sent_messages[0].text == "hello"

    @pytest.mark.asyncio
    async def test_send_unknown_adapter(self):
        registry = ChannelRegistry()
        result = await registry.send(ChannelMessage(adapter="unknown", recipient="R123", text="hello"))
        assert result["ok"] is False
        assert "No adapter" in result["error"]

    @pytest.mark.asyncio
    async def test_route_resolution(self, fake_adapter):
        registry = ChannelRegistry()
        registry.register("slack", fake_adapter)

        # Manually add route
        registry._routes["ops"] = ("slack", "C123")

        result = await registry.send(ChannelMessage(adapter="ops", recipient="", text="alert"))
        assert result["ok"] is True
        assert fake_adapter.sent_messages[0].recipient == "C123"

    @pytest.mark.asyncio
    async def test_stop_all(self, fake_adapter):
        registry = ChannelRegistry()
        registry.register("test", fake_adapter)
        await registry.stop_all()
        assert fake_adapter.stopped

    def test_get_errors(self):
        registry = ChannelRegistry()
        # No adapters loaded -> no errors
        assert registry.get_errors() == []


class TestBridge:
    @pytest.mark.asyncio
    async def test_bridge_start_stop(self, fake_adapter):
        registry = ChannelRegistry()
        registry.register("slack", fake_adapter)

        from adk_channels.bridge import ChatBridge

        bridge = ChatBridge(
            bridge_config=BridgeConfig(enabled=True),
            registry=registry,
            agent_runner=lambda s, t: f"Echo: {t}",
        )
        bridge.start()
        assert bridge.is_active()
        bridge.stop()
        assert not bridge.is_active()

    @pytest.mark.asyncio
    async def test_bridge_handle_message(self, fake_adapter):
        registry = ChannelRegistry()
        registry.register("slack", fake_adapter)

        from adk_channels.bridge import ChatBridge

        bridge = ChatBridge(
            bridge_config=BridgeConfig(enabled=True, max_concurrent=1),
            registry=registry,
            agent_runner=lambda s, t: f"Echo: {t}",
        )
        bridge.start()

        msg = IncomingMessage(adapter="slack", sender="U123", text="hello")
        await bridge.handle_message(msg)

        # Give async processing time
        await asyncio.sleep(0.1)

        assert len(fake_adapter.sent_messages) == 1
        assert "Echo: hello" in fake_adapter.sent_messages[0].text

        bridge.stop()

    def test_bridge_stats(self):
        registry = ChannelRegistry()
        from adk_channels.bridge import ChatBridge

        bridge = ChatBridge(
            bridge_config=BridgeConfig(enabled=True),
            registry=registry,
        )
        bridge.start()
        stats = bridge.get_stats()
        assert stats["active"] is True
        assert stats["sessions"] == 0
