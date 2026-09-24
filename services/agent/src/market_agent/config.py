"""Runtime settings, approved model identities, and company display names."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Approved models (see docs/ARCHITECTURE.md). Llama-family models are not allowed.
LOCAL_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
LOCAL_MODEL_REVISION = "bee7596271d1495f6992ae224aefde4410e816b8"
SPECULATOR_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark"
SPECULATOR_REVISION = "8a0177116d138011e63103110f136ec0ca09ebbf"
EMBED_MODEL = "nvidia/Nemotron-3-Embed-1B-BF16"
EMBED_REVISION = "9e0b24858b1195815ecb1188ffa1b73bcea7b30a"
JUDGE_MODEL = "openai/openai/gpt-5.6-luna"
CAPABLE_MODEL = "nvidia/nvidia/nemotron-3-ultra"

MAX_TURNS = 4
TOOLS_URL = "http://tools:8000/mcp"
MODEL_URL = "http://model:8001/v1"

# Display names and aliases used to recognize companies in free-text questions.
# Tickers themselves come from the prepared scenario at runtime.
COMPANIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "NVDA": ("NVIDIA", ("nvidia",)),
    "AMD": ("Advanced Micro Devices", ("advanced micro devices",)),
    "AVGO": ("Broadcom", ("broadcom",)),
    "JPM": ("JPMorgan Chase", ("jpmorgan", "jp morgan", "j.p. morgan")),
    "GS": ("Goldman Sachs", ("goldman sachs", "goldman")),
    "SCHW": ("Charles Schwab", ("charles schwab", "schwab")),
}


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    state_root: Path
    scenario_root: Path
    events_root: Path
    skills_root: Path
    remote_enabled: bool
    remote_url: str | None
    remote_key: str | None
    langsmith_project_url: str | None

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            state_root=Path(os.getenv("MARKET_SHOCK_STATE_ROOT", "/srv/market-shock/state")),
            scenario_root=Path(os.getenv("MARKET_SHOCK_SCENARIO_ROOT", "/srv/market-shock/scenario")),
            events_root=Path(
                os.getenv("MARKET_SHOCK_EVENT_CATALOG_ROOT", "/srv/market-shock/events/current")
            ),
            skills_root=Path(os.getenv("MARKET_SHOCK_SKILLS_ROOT", "/opt/market-agent/skills")),
            remote_enabled=_flag("REMOTE_ROUTING_ENABLED"),
            remote_url=os.getenv("NVIDIA_BASE_URL") or None,
            remote_key=os.getenv("NVIDIA_INFERENCE_API_KEY") or None,
            langsmith_project_url=os.getenv("LANGSMITH_PROJECT_URL") or None,
        )
        if settings.remote_enabled and not (settings.remote_url and settings.remote_key):
            raise ValueError("REMOTE_ROUTING_ENABLED requires NVIDIA_BASE_URL and NVIDIA_INFERENCE_API_KEY")
        return settings

    @property
    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in stored records or API responses."""
        values = (self.remote_key, os.getenv("LANGSMITH_API_KEY"))
        return tuple(value for value in values if value)
