import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

LOCAL_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
LUNA_MODEL = "openai/openai/gpt-5.6-luna"
SOL_MODEL = "openai/openai/gpt-5.6-sol"
CAPABLE_MODEL = "nvidia/nvidia/nemotron-3-ultra"
TOOLS = (
    "detect_market_shock",
    "get_price_context",
    "search_news",
    "find_historical_analogues",
    "trace_shock_propagation",
    "predict_volatility_risk",
    "project_news_topics",
)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools_url: str = "http://tools:8000/mcp"
    model_url: str = "http://model:8001/v1"
    local_model: str = LOCAL_MODEL
    state_root: Path = Path("/srv/market-shock/state")
    scenario_root: Path = Path("/srv/market-shock/scenario")
    event_catalog_root: Path = Path("/srv/market-shock/events/current")
    skills_root: Path = Path("/opt/market-agent/skills")
    data_gate: Literal["reconstruction", "release"] = "reconstruction"
    remote_enabled: bool = False
    remote_url: str | None = None
    remote_key: SecretStr | None = None
    remote_model: str | None = CAPABLE_MODEL
    judge_model: str = LUNA_MODEL

    @model_validator(mode="after")
    def validate_contract(self):
        if self.tools_url != "http://tools:8000/mcp" or self.model_url != "http://model:8001/v1":
            raise ValueError("internal service URLs are fixed")
        models = (self.local_model, self.remote_model or "", self.judge_model)
        if any("llama" in model.lower() for model in models) or self.local_model != LOCAL_MODEL:
            raise ValueError("unapproved model")
        if self.remote_model not in {None, CAPABLE_MODEL} or self.judge_model != LUNA_MODEL:
            raise ValueError("unapproved remote model")
        if self.remote_enabled and not all((self.remote_url, self.remote_key, self.remote_model)):
            raise ValueError("complete remote configuration required")
        if not all(
            path.is_absolute()
            for path in (self.state_root, self.scenario_root, self.event_catalog_root, self.skills_root)
        ):
            raise ValueError("runtime roots must be absolute")
        return self

    def load_coverage(self):
        """Load the mounted production catalog through the fail-closed v2 boundary."""
        from .coverage import CoverageCatalog

        return CoverageCatalog.load(self.scenario_root, gate=self.data_gate)

    @classmethod
    def from_env(cls):
        return cls(
            remote_enabled=os.getenv("REMOTE_ROUTING_ENABLED", "false").lower() == "true",
            remote_url=os.getenv("NVIDIA_BASE_URL") or None,
            remote_key=os.getenv("NVIDIA_INFERENCE_API_KEY") or None,
            remote_model=os.getenv("SWITCHYARD_FRONTIER_MODEL") or os.getenv("LLM_MODEL") or CAPABLE_MODEL,
            judge_model=os.getenv("SWITCHYARD_JUDGE_MODEL") or LUNA_MODEL,
            state_root=Path(os.getenv("MARKET_SHOCK_STATE_ROOT", "/srv/market-shock/state")),
            scenario_root=Path(os.getenv("MARKET_SHOCK_SCENARIO_ROOT", "/srv/market-shock/scenario")),
            event_catalog_root=Path(
                os.getenv("MARKET_SHOCK_EVENT_CATALOG_ROOT", "/srv/market-shock/events/current")
            ),
            skills_root=Path(os.getenv("MARKET_SHOCK_SKILLS_ROOT", "/opt/market-agent/skills")),
            data_gate=os.getenv("MARKET_SHOCK_DATA_GATE", "reconstruction"),
        )
