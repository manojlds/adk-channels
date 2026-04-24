"""adk-channels — Multi-channel messaging integration for Google ADK."""

from __future__ import annotations

from adk_channels.bridge import ChatBridge
from adk_channels.config import (
    AdapterConfig,
    BridgeConfig,
    ChannelsConfig,
    RouteConfig,
)
from adk_channels.registry import ChannelRegistry
from adk_channels.types import (
    AdapterDirection,
    ChannelMessage,
    IncomingMessage,
    RunResult,
)

try:
    from adk_channels.multi_app_bridge import MultiAppBridge
except ImportError:
    MultiAppBridge = None  # type: ignore

__version__ = "0.1.0"
__all__ = [
    "ChannelRegistry",
    "ChatBridge",
    "MultiAppBridge",
    "ChannelsConfig",
    "AdapterConfig",
    "RouteConfig",
    "BridgeConfig",
    "IncomingMessage",
    "ChannelMessage",
    "RunResult",
    "AdapterDirection",
]
