from contextlib import asynccontextmanager
import os
from pathlib import Path
import re

from fastapi import FastAPI, Header, HTTPException, Response
from .api import router as investigation_router
from .checkpoint import CheckpointStore
from .config import LOCAL_MODEL, LUNA_MODEL, CAPABLE_MODEL, Settings
from .coverage import EMBED_MODEL, EMBED_REVISION
from .dashboard import router as dashboard_router
from .dependencies import probe
from .evidence import EvidenceExecutor
from .deep_runtime import MarketDeepAgent, SKILL_ID
from .mcp_client import MarketToolClient
from .event_catalog import EventCatalogError, ShockEventCatalog
from .relay_tracing import RelayTracing
from .security import BOUNDARY_DESTINATIONS
from .state import StateStore
from .schemas import MAX_INVESTIGATION_TURNS
from .generation_health import generation_health

settings = Settings.from_env()
relay_tracing = RelayTracing()
status = {"tools": False, "model": False, "event_catalog": False, "mode": "starting"}
APP_VERSION = "1.2.0"
PRIMARY_COMPANIES = (
    ("NVDA", "NVIDIA"),
    ("AMD", "Advanced Micro Devices"),
    ("JPM", "JPMorgan Chase"),
    ("GS", "Goldman Sachs"),
    ("SCHW", "Charles Schwab"),
)
LANGSMITH_PROJECT_LINK_PATTERN = re.compile(
    r"https://smith\.langchain\.com/o/[A-Za-z0-9-]+/projects/p/[A-Za-z0-9-]+"
)


_PUBLIC_MODELS = [
    {
        "model_id": LOCAL_MODEL,
        "revision": "bee7596271d1495f6992ae224aefde4410e816b8",
        "location": "local_model_service",
        "identity_basis": "immutable_revision",
        "roles": ["local_generation", "switchyard_efficient_target"],
        "route_eligible": True,
        "dependency": "model",
    },
    {
        "model_id": "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark",
        "revision": "8a0177116d138011e63103110f136ec0ca09ebbf",
        "location": "local_model_service",
        "identity_basis": "immutable_revision",
        "roles": ["speculative_assistant"],
        "route_eligible": False,
        "dependency": "model",
    },
    {
        "model_id": EMBED_MODEL,
        "revision": EMBED_REVISION,
        "location": "local_tools_service",
        "identity_basis": "immutable_revision",
        "roles": ["retrieval_embedding"],
        "route_eligible": False,
        "dependency": "tools",
    },
    {
        "model_id": LUNA_MODEL,
        "revision": None,
        "location": "internal_inference_server",
        "identity_basis": "exact_server_route_id",
        "roles": ["switchyard_classifier"],
        "route_eligible": True,
        "dependency": "remote_routing",
    },
    {
        "model_id": CAPABLE_MODEL,
        "revision": None,
        "location": "internal_inference_server",
        "identity_basis": "exact_server_route_id",
        "roles": ["switchyard_capable_target", "report_formatter"],
        "route_eligible": True,
        "dependency": "remote_routing",
    },
]


def _langsmith_project_link() -> str | None:
    if not relay_tracing.settings.langsmith_enabled:
        return None
    value = os.getenv("LANGSMITH_PROJECT_URL", "").strip()
    if not LANGSMITH_PROJECT_LINK_PATTERN.fullmatch(value):
        raise RuntimeError("invalid LangSmith project link")
    return value


def _assert_public_status(value, *, private_values=(), safe_project_link=None):
    unsafe_fields = {"url", "uri", "endpoint", "key", "token", "secret", "credential", "password", "host"}
    private = tuple(item for item in private_values if isinstance(item, str) and item)
    if safe_project_link is not None and not LANGSMITH_PROJECT_LINK_PATTERN.fullmatch(safe_project_link):
        raise RuntimeError("unsafe status contract project link")

    def visit(item, *, field=None):
        if isinstance(item, dict):
            if any(
                not isinstance(key, str) or unsafe_fields & set(key.lower().replace("-", "_").split("_"))
                for key in item
            ):
                raise RuntimeError("unsafe status contract field")
            for key, child in item.items():
                visit(child, field=key)
            return
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
            return
        elif isinstance(item, str):
            lowered = item.lower()
            unsafe_value = any(
                term in lowered
                for term in ("bearer ", "api_key", "api-key", "credential", "password", "secret")
            )
            approved_link = field == "project_link" and item == safe_project_link
            if (
                "llama" in lowered
                or ("://" in lowered and not approved_link)
                or unsafe_value
                or any(secret in item for secret in private)
            ):
                raise RuntimeError("unsafe status contract value")
            return
        return

    visit(value)


@asynccontextmanager
async def lifespan(app: FastAPI):
    catalog = settings.load_coverage()
    event_catalog, event_catalog_error = None, "event_catalog_unconfigured"
    event_root = getattr(settings, "event_catalog_root", None)
    if isinstance(event_root, Path):
        try:
            event_catalog = ShockEventCatalog.load(event_root, catalog)
            event_catalog_error = None
        except EventCatalogError as exc:
            event_catalog_error = exc.code
    store = StateStore(settings.state_root / "investigations.sqlite3", settings.state_root / "agent.key")
    checkpoints = CheckpointStore(
        settings.state_root / "checkpoints.sqlite3", settings.state_root / "agent.key"
    )
    mcp_client = MarketToolClient(settings.tools_url)
    app.state.store, app.state.running, app.state.catalog = store, {}, catalog
    app.state.event_catalog, app.state.event_catalog_error = event_catalog, event_catalog_error
    app.state.market_tool_client = mcp_client
    app.state.security_secrets = (
        {"NVIDIA_INFERENCE_API_KEY": settings.remote_key} if settings.remote_key else {}
    )
    app.state.security_boundaries = dict(BOUNDARY_DESTINATIONS)
    try:
        await relay_tracing.start()
        checkpointer = await checkpoints.open()
        app.state.investigator = MarketDeepAgent(
            settings, catalog, EvidenceExecutor(await mcp_client.open(), catalog), checkpointer, event_catalog
        )
        status.update(await probe(settings))
        status.update(
            {
                "checkpoint": True,
                "mcp_contract": True,
                "coverage": True,
                "event_catalog": event_catalog is not None,
                "mode": "ready"
                if status["tools"] and status["model"] and event_catalog is not None
                else "degraded",
            }
        )
        yield
    finally:
        await mcp_client.close()
        await checkpoints.close()
        await relay_tracing.shutdown()
        store.close()


app = FastAPI(
    title="Market Shock Investigator", version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None
)
app.include_router(investigation_router)
app.include_router(dashboard_router)


@app.get("/health/live")
async def live():
    return {"service": "agent", "live": True}


@app.get("/health/ready")
async def ready(
    response: Response,
    verify_generation: bool = False,
    require_idle: bool = False,
    maintenance_token: str | None = Header(default=None, alias="X-Market-Maintenance-Token"),
):
    maintenance_token = maintenance_token if isinstance(maintenance_token, str) else None
    if verify_generation and require_idle:
        raise HTTPException(
            status_code=400, detail="readiness probes must select generation or idle verification"
        )
    if require_idle:
        idle = (
            generation_health.owner is None
            and not generation_health.checking
            and generation_health.maintenance is None
        )
        if not idle:
            response.status_code = 409
        return {"service": "agent", "idle": idle}
    if maintenance_token is not None and not verify_generation:
        raise HTTPException(
            status_code=400, detail="maintenance token is only valid for generation verification"
        )
    if verify_generation:
        generation_health.request_fresh_check(maintenance_token)
    status.update(await probe(settings, maintenance_token=maintenance_token))
    ok = status["tools"] and status["model"] and bool(getattr(app.state, "event_catalog", None))
    status["mode"] = "ready" if ok else "degraded"
    if not ok:
        response.status_code = 503
    return {"service": "agent", "ready": ok, **status}


@app.get("/api/status")
async def api_status():
    observed = await probe(settings, verify_generation=False)
    status.update({key: bool(observed.get(key)) for key in ("tools", "model")})
    catalog = app.state.catalog
    if (
        tuple(catalog.targets) != tuple(symbol for symbol, _ in PRIMARY_COMPANIES)
        or settings.local_model != LOCAL_MODEL
        or getattr(settings, "remote_model", CAPABLE_MODEL) not in {None, CAPABLE_MODEL}
        or getattr(settings, "judge_model", LUNA_MODEL) != LUNA_MODEL
    ):
        raise RuntimeError("invalid status contract configuration")
    dependencies = {
        key: bool(status.get(key))
        for key in ("tools", "model", "coverage", "checkpoint", "mcp_contract", "event_catalog")
    }
    common_ready = all(
        dependencies[key] for key in ("tools", "coverage", "checkpoint", "mcp_contract", "event_catalog")
    )
    switchyard_enabled = common_ready and dependencies["model"] and settings.remote_enabled
    project_link = _langsmith_project_link()
    observability = {
        "remote_inference_enabled": bool(settings.remote_enabled),
        "langsmith_export_enabled": bool(relay_tracing.settings.langsmith_enabled),
    }
    if project_link is not None:
        observability["project_link"] = project_link
    limitations = (
        (["reconstructed_later_market_data"] if catalog.vintage_status == "reconstructed_later" else [])
        + (
            ["licensed_news_unavailable"]
            if "licensed_news_metadata" not in catalog.document_source_kinds
            else []
        )
        + ["source_coverage_varies_by_ticker_and_cutoff"]
    )
    payload = {
        "schema_version": "system-status-v1",
        "service": "agent",
        "version": APP_VERSION,
        "ready": all(dependencies.values()) and bool(settings.remote_enabled),
        "dependencies": dependencies,
        "remote_routing_enabled": settings.remote_enabled,
        "observability": observability,
        "investigations": "available",
        "supported_tickers": list(catalog.targets),
        "companies": [{"symbol": symbol, "display_name": name} for symbol, name in PRIMARY_COMPANIES],
        "coverage": {
            "scenario_id": catalog.scenario_id,
            "scenario_manifest_sha256": catalog.scenario_manifest_sha256,
            "data_tier": catalog.data_tier,
            "vintage_status": catalog.vintage_status,
            "first_session": catalog.sessions[0].session_date.isoformat(),
            "last_session": catalog.sessions[-1].session_date.isoformat(),
            "session_count": len(catalog.sessions),
            "document_source_kinds": list(catalog.document_source_kinds),
        },
        "limitations": limitations,
        "models": list(_PUBLIC_MODELS),
        "routes": [
            {
                "mode": "switchyard_escalation",
                "enabled": switchyard_enabled,
                "disabled_reason": None
                if switchyard_enabled
                else "remote_routing_disabled"
                if not settings.remote_enabled
                else "required_dependencies_unavailable",
                "model_roles": [
                    "switchyard_classifier",
                    "local_generation",
                    "switchyard_capable_target",
                    "report_formatter",
                ],
                "evidence_tools_unchanged": True,
            }
        ],
        "contracts": {
            "max_investigation_turns": MAX_INVESTIGATION_TURNS,
            "max_concurrent_investigations": 1,
            "data": catalog.scenario_id,
            "skill": SKILL_ID,
            "prompt": "market-shock-grounded-synthesis/2.0.0",
            "safety": "bounded-equity-research/1.0.0",
            "generation_model": settings.local_model,
            "embedding_model": EMBED_MODEL,
            "embedding_revision": EMBED_REVISION,
        },
    }
    remote_key = (
        settings.remote_key.get_secret_value()
        if settings.remote_key and hasattr(settings.remote_key, "get_secret_value")
        else settings.remote_key
    )
    _assert_public_status(
        payload,
        private_values=(getattr(settings, "remote_url", None), remote_key),
        safe_project_link=project_link,
    )
    return payload


@app.post("/health/maintenance")
async def acquire_maintenance():
    return {"service": "agent", **generation_health.acquire_maintenance()}


@app.delete("/health/maintenance")
async def release_maintenance(
    maintenance_token: str | None = Header(default=None, alias="X-Market-Maintenance-Token"),
):
    return {"service": "agent", **generation_health.release_maintenance(maintenance_token)}
