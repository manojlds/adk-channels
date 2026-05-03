"""Configuration for adk-channels using pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdapterConfig(BaseModel):
    """Configuration for a single adapter."""

    type: str
    model_config = {"extra": "allow"}


class RouteConfig(BaseModel):
    """Route alias pointing to an adapter + recipient."""

    adapter: str
    recipient: str


class SessionRule(BaseModel):
    """Per-sender session mode override using a glob pattern."""

    pattern: str
    mode: Literal["persistent", "stateless"]


class BridgeConfig(BaseModel):
    """Chat bridge configuration."""

    session_mode: Literal["persistent", "stateless"] = "persistent"
    session_scope: Literal["sender", "user", "channel", "thread"] = "sender"
    session_rules: list[SessionRule] = Field(default_factory=list)
    idle_timeout_minutes: int = 30
    max_queue_per_sender: int = 5
    timeout_ms: int = 300_000
    max_concurrent: int = 2
    typing_indicators: bool = True
    send_thoughts: bool = True
    commands: bool = True


class ChannelsConfig(BaseSettings):
    """Top-level configuration for adk-channels."""

    model_config = SettingsConfigDict(
        env_prefix="ADK_CHANNELS_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    adapters: dict[str, AdapterConfig] = Field(default_factory=dict)
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)

    @classmethod
    def from_file(cls, path: str) -> ChannelsConfig:
        """Load from a JSON or YAML file (simple JSON for now)."""
        import json

        with open(path) as f:
            data = json.load(f)
        return cls(**data)

    def get_adapter_config(self, name: str) -> AdapterConfig | None:
        return self.adapters.get(name)

    def get_route(self, alias: str) -> RouteConfig | None:
        return self.routes.get(alias)
