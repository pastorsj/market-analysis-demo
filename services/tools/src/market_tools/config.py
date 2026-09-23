import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, field_validator

EMBED_ID = "nvidia/Nemotron-3-Embed-1B-BF16"
EMBED_REVISION = "9e0b24858b1195815ecb1188ffa1b73bcea7b30a"
FORBIDDEN_ENV = {"NVIDIA_BASE_URL", "NVIDIA_INFERENCE_API_KEY", "LLM_MODEL"}

class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: Path = Path("/srv/market-shock")
    port: int = 8000
    mcp_path: str = "/mcp"
    strict_gpu: bool = True
    embed_model: str = EMBED_ID
    embed_revision: str = EMBED_REVISION
    embed_dimension: int = 2048
    embed_path: Path = Path("/srv/market-shock/models/hf/nemotron-3-embed-1b-bf16-9e0b248")
    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts: raise ValueError("root must be absolute and contained")
        return value
    @classmethod
    def from_env(cls):
        leaked = sorted(k for k in FORBIDDEN_ENV if os.getenv(k))
        if leaked: raise ValueError("frontier configuration is forbidden in tools")
        return cls(
            root=Path(os.getenv("MARKET_SHOCK_ROOT", "/srv/market-shock")),
            strict_gpu=os.getenv("MARKET_TOOLS_STRICT_GPU", "true").lower() == "true",
            embed_path=Path(os.getenv(
                "NEMOTRON_EMBED_PATH",
                "/srv/market-shock/models/hf/nemotron-3-embed-1b-bf16-9e0b248",
            )),
        )
