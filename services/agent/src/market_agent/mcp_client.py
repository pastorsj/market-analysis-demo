"""Lifespan-owned direct MCP client with immutable tool and resource contracts."""

from __future__ import annotations

from datetime import date, datetime, UTC
from functools import partial
import hashlib
import ipaddress
import json
import math
import re
from types import SimpleNamespace
from typing import Any
from collections.abc import Callable
from urllib.parse import urlsplit

from mcp import Client

from .config import TOOLS


class ToolClientError(RuntimeError):
    pass


TOOL_SCHEMA_SHA256 = {
    "detect_market_shock": "a0895456a4cfc81e0c60a77801f85eec3806d215e551ef6724f7b2acefda077a",
    "get_price_context": "174914c036abc34ba2f123a8f4b1b212de759019ab65aa6dfb90981d1a54c325",
    "search_news": "658c90322e758dccb75b532c44eb5d2c34be029bdc92904b6c00ec3e51c8eab1",
    "find_historical_analogues": "566ca6f952802416ccd5276f97fbeb20075daf695eeb8b5d96580bd0adb8c3ec",
    "trace_shock_propagation": "c5157675c3612597ffa3fc281f59646d05c1b58f4e0a08acfca4e321c6a2e408",
    "predict_volatility_risk": "53c83d0416af17204d995d1a814e506e1610e1b342fc909b8c0997c1d3fb44d2",
    "project_news_topics": "64c759cfcfcc8c3a75f34fc704daebbc8af7f90240174dc65c9d23c19fbbf0bd",
}
_SAFE = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_TARGETS = ("NVDA", "AMD", "JPM", "GS", "SCHW")
_INSTRUMENTS = {*_TARGETS, "QQQ", "SPY", "XLF"}
_OPERATIONS = {"get_price_context", "detect_market_shock", "search_news", "market_series"}
_ENGINES = {"cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml", "deterministic"}
_SOURCES = {"market", "news", "filing", "release", "relationship", "model"}
_TOP = {
    "schema_version",
    "mode",
    "requested_as_of",
    "resolved_session",
    "cutoff_at",
    "selected_ticker",
    "coverage",
    "watchlist",
    "series",
    "evidence",
    "receipts",
    "limitations",
}
_UNSAFE_TEXT = re.compile(
    r"(?:https?://|\bbearer\s+|\b(?:api[_ -]?key|authorization|password|secret|access[_ -]?token)\b\s*[:=])",
    re.I,
)


def _wire(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        result = value.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(result, dict):
            return result
    raise ToolClientError("MCP returned an invalid tool descriptor")


def validate_tool_descriptors(tools: list[Any]) -> None:
    rows = [_wire(tool) for tool in tools]
    names = [row.get("name") for row in rows]
    if len(names) != len(set(names)) or set(names) != set(TOOLS):
        raise ToolClientError(f"MCP contract drift: expected exactly {len(TOOLS)} unique tools")
    for row in rows:
        schema, annotations = row.get("inputSchema"), row.get("annotations")
        digest = (
            hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if isinstance(schema, dict)
            else ""
        )
        if digest != TOOL_SCHEMA_SHA256[row["name"]]:
            raise ToolClientError(f"MCP input schema drift for {row['name']}")
        if (
            not isinstance(annotations, dict)
            or any(annotations.get(key) is not expected for key, expected in _SAFE.items())
            or set(annotations) - {*_SAFE, "title"}
        ):
            raise ToolClientError(f"unsafe or missing MCP annotations for {row['name']}")


def _exact(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ToolClientError("MCP dashboard contract drift")
    return value


def _day(value: object) -> date:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else (_ for _ in ()).throw(ValueError())
    except ValueError as exc:
        raise ToolClientError("MCP dashboard date is invalid") from exc


def _instant(value: object) -> datetime:
    try:
        result = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str) and value.endswith("Z")
            else None
        )
    except ValueError as exc:
        raise ToolClientError("MCP dashboard timestamp is invalid") from exc
    if result is None or result.tzinfo is None:
        raise ToolClientError("MCP dashboard timestamp is invalid")
    return result.astimezone(UTC)


def _numeric(value: object, *, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ToolClientError("MCP dashboard numeric value is invalid")


def _safe_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value == value.strip()
        and not _UNSAFE_TEXT.search(value)
    )


def _public_https(value: object) -> bool:
    try:
        parsed = urlsplit(value if isinstance(value, str) else "")
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or host.endswith((".invalid", ".internal", ".local"))
            or host in {"localhost", "tools", "agent", "model"}
        ):
            return False
        try:
            return not ipaddress.ip_address(host).is_private
        except ValueError:
            return True
    except ValueError:
        return False


def validate_dashboard(value: object, ticker: str, requested: str) -> dict[str, Any]:
    result = _exact(value, _TOP)
    if (
        result.get("schema_version"),
        result.get("mode"),
        result.get("selected_ticker"),
        result.get("requested_as_of"),
    ) != ("market-dashboard-v1", "historical_reconstruction", ticker, requested):
        raise ToolClientError("MCP dashboard identity mismatch")
    requested_day, resolved = _day(requested), _day(result["resolved_session"])
    cutoff = _instant(result["cutoff_at"])
    coverage = _exact(
        result["coverage"],
        {"first_session", "last_session", "session_count", "scenario_id", "vintage_status"},
    )
    first, last = _day(coverage["first_session"]), _day(coverage["last_session"])
    if (
        not first <= resolved <= requested_day <= last
        or cutoff.date() != resolved
        or type(coverage.get("session_count")) is not int
        or coverage["session_count"] < 1
        or not all(_safe_text(coverage.get(key), 160) for key in ("scenario_id", "vintage_status"))
    ):
        raise ToolClientError("MCP dashboard coverage mismatch")
    rows = result.get("watchlist")
    row_keys = {
        "ticker",
        "outcome",
        "resolved_session",
        "close",
        "return_1_session_pct",
        "return_5_sessions_pct",
        "opening_gap_pct",
        "volume_ratio",
        "benchmark",
        "benchmark_return_pct",
        "market_adjusted_return_pct",
        "is_shock",
    }
    if not isinstance(rows, list) or [row.get("ticker") for row in rows if isinstance(row, dict)] != list(
        _TARGETS
    ):
        raise ToolClientError("MCP dashboard watchlist mismatch")
    for row in rows:
        item = _exact(row, row_keys)
        if (
            item["outcome"] not in {"ok", "partial", "no_data"}
            or item["resolved_session"] != result["resolved_session"]
            or item["benchmark"] not in {"QQQ", "SPY", "XLF"}
            or item["is_shock"] is not None
            and type(item["is_shock"]) is not bool
        ):
            raise ToolClientError("MCP dashboard watchlist mismatch")
        for key in (
            "close",
            "return_1_session_pct",
            "return_5_sessions_pct",
            "opening_gap_pct",
            "volume_ratio",
            "benchmark_return_pct",
            "market_adjusted_return_pct",
        ):
            _numeric(item[key])
        if (
            item["close"] is not None
            and item["close"] <= 0
            or item["volume_ratio"] is not None
            and item["volume_ratio"] < 0
            or item["outcome"] == "no_data"
            and any(
                item[key] is not None
                for key in row_keys - {"ticker", "outcome", "resolved_session", "benchmark"}
            )
        ):
            raise ToolClientError("MCP dashboard watchlist mismatch")
    series = _exact(result["series"], {"ticker", "benchmark", "points"})
    selected = next(row for row in rows if row["ticker"] == ticker)
    if (
        series["ticker"] != ticker
        or series["benchmark"] != selected["benchmark"]
        or not isinstance(series["points"], list)
        or not 1 <= len(series["points"]) <= 63
    ):
        raise ToolClientError("MCP dashboard series mismatch")
    point_keys = {
        "session_date",
        "ticker_close",
        "benchmark_close",
        "ticker_normalized_return_pct",
        "benchmark_normalized_return_pct",
        "volume",
    }
    point_days = []
    for point in series["points"]:
        item = _exact(point, point_keys)
        point_days.append(_day(item["session_date"]))
        for key in (
            "ticker_close",
            "benchmark_close",
            "ticker_normalized_return_pct",
            "benchmark_normalized_return_pct",
        ):
            _numeric(item[key], nullable=False)
        _numeric(item["volume"], nullable=False)
        if item["ticker_close"] <= 0 or item["benchmark_close"] <= 0 or item["volume"] < 0:
            raise ToolClientError("MCP dashboard series mismatch")
    if (
        point_days != sorted(set(point_days))
        or point_days[-1] != resolved
        or series["points"][0]["ticker_normalized_return_pct"] != 0
        or series["points"][0]["benchmark_normalized_return_pct"] != 0
        or selected["close"] is not None
        and abs(series["points"][-1]["ticker_close"] - selected["close"]) > 1e-8
    ):
        raise ToolClientError("MCP dashboard series mismatch")
    evidence = result.get("evidence")
    evidence_keys = {"citation_id", "title", "url", "source_type", "published_at", "available_at", "excerpt"}
    if not isinstance(evidence, list) or len(evidence) > 4:
        raise ToolClientError("MCP dashboard evidence mismatch")
    for row in evidence:
        item = _exact(row, evidence_keys)
        published, available = _instant(item["published_at"]), _instant(item["available_at"])
        if (
            item["source_type"] not in _SOURCES
            or not _public_https(item["url"])
            or published > available
            or available > cutoff
            or not all(
                _safe_text(item.get(key), maximum)
                for key, maximum in (("citation_id", 80), ("title", 500), ("excerpt", 1200))
            )
        ):
            raise ToolClientError("MCP dashboard evidence mismatch")
    receipts = result.get("receipts")
    receipt_keys = {"operation", "ticker", "engine", "device", "gpu_executed", "fallback_used", "duration_ms"}
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 20:
        raise ToolClientError("MCP dashboard receipts mismatch")
    for row in receipts:
        item = _exact(row, receipt_keys)
        _numeric(item["duration_ms"], nullable=False)
        execution_mismatch = (item["engine"] == "deterministic") == item["gpu_executed"]
        series_mismatch = item["operation"] == "market_series" and (
            item["engine"] != "cudf" or not item["gpu_executed"]
        )
        if (
            item["operation"] not in _OPERATIONS
            or item["ticker"] not in _INSTRUMENTS
            or item["engine"] not in _ENGINES
            or not _safe_text(item["device"], 160)
            or type(item["gpu_executed"]) is not bool
            or item["fallback_used"] is not False
            or item["duration_ms"] < 0
            or execution_mismatch
            or series_mismatch
        ):
            raise ToolClientError("MCP dashboard receipts mismatch")
    identities = [(item["operation"], item["ticker"]) for item in receipts]
    if (
        len(identities) != len(set(identities))
        or ("market_series", ticker) not in identities
        or any(("get_price_context", symbol) not in identities for symbol in _TARGETS)
    ):
        raise ToolClientError("MCP dashboard receipts mismatch")
    limitations = result.get("limitations")
    limitation_keys = {"code", "message", "affected"}
    if (
        not isinstance(limitations, list)
        or len(limitations) > 20
        or len({item.get("code") for item in limitations if isinstance(item, dict)}) != len(limitations)
    ):
        raise ToolClientError("MCP dashboard limitations mismatch")
    for row in limitations:
        item = _exact(row, limitation_keys)
        affected = item["affected"]
        if (
            not isinstance(item["code"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item["code"])
            or not _safe_text(item["message"], 500)
            or not isinstance(affected, list)
            or not len(affected) <= 20
            or len(set(affected)) != len(affected)
            or any(not _safe_text(value, 160) for value in affected)
        ):
            raise ToolClientError("MCP dashboard limitations mismatch")
    return result


class MarketToolClient:
    def __init__(self, url: str, timeout: float = 45, client_factory: Callable[..., Any] = Client):
        self.url, self.timeout, self._factory, self._client = url, timeout, client_factory, None

    async def open(self) -> dict[str, Any]:
        if self._client is not None:
            return self.tool_map()
        client = self._factory(self.url, read_timeout_seconds=self.timeout)
        await client.__aenter__()
        self._client = client
        try:
            await self.list_tools()
        except BaseException:
            self._client = None
            await client.__aexit__(None, None, None)
            raise
        return self.tool_map()

    async def list_tools(self) -> list[Any]:
        if self._client is None:
            raise ToolClientError("MCP client is closed")
        result = await self._client.list_tools(cache_mode="refresh")
        tools = list(result.tools)
        validate_tool_descriptors(tools)
        return tools

    def tool_map(self) -> dict[str, Any]:
        return {name: SimpleNamespace(name=name, ainvoke=partial(self.call, name)) for name in TOOLS}

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._client is None or name not in TOOLS:
            raise ToolClientError("MCP client is closed or tool is not allowlisted")
        result = await self._client.call_tool(name, arguments)
        payload = result.structured_content
        if result.is_error or not isinstance(payload, dict):
            raise ToolClientError(f"{name} returned no successful structured content")
        if (
            payload.get("schema_version") != "2.0"
            or payload.get("tool") != name
            or payload.get("outcome") not in {"ok", "partial", "no_data"}
        ):
            raise ToolClientError(f"{name} returned an invalid ToolResult v2 identity")
        return payload

    async def read_dashboard(self, ticker: str, requested: str) -> dict[str, Any]:
        if self._client is None or ticker not in _TARGETS:
            raise ToolClientError("MCP client is closed or dashboard scope is invalid")
        _day(requested)
        try:
            result = await self._client.read_resource(
                f"market://dashboard/{ticker}/{requested}", cache_mode="bypass"
            )
            contents = list(result.contents)
            if len(contents) != 1:
                raise ToolClientError("MCP dashboard returned invalid content")
            content = contents[0]
            text = content.get("text") if isinstance(content, dict) else getattr(content, "text", None)
            mime = (
                content.get("mimeType") if isinstance(content, dict) else getattr(content, "mime_type", None)
            )
            if not isinstance(text, str) or mime != "application/json":
                raise ToolClientError("MCP dashboard returned invalid content")
            return validate_dashboard(json.loads(text), ticker, requested)
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise ToolClientError("MCP dashboard returned invalid content") from exc

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.__aexit__(None, None, None)
