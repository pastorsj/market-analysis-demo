"""Seven bounded, cutoff-filtered tools over one verified artifact bundle."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timedelta, UTC
from pathlib import Path

from .artifacts import ArtifactBundle, parse_time
from .market_analytics import gpu_price_performance, gpu_shock_metrics
from .market_store import EMBED_ID, EMBED_REV, MarketStoreError
from .models import (
    Artifact,
    Citation,
    CoverageItem,
    EvidenceItem,
    ExecutionReceipt,
    ToolLimitation,
    ToolResult,
    stable_id,
)


_ANALOGUE_FEATURES = (
    {
        "name": "absolute_return_pct",
        "units": "percentage_points",
        "scale_divisor": 20.0,
        "scaled_cap": 5.0,
        "semantics": "unsigned_return_magnitude",
    },
    {
        "name": "volume_ratio",
        "units": "ratio_to_prior_20_session_median",
        "scale_divisor": 5.0,
        "scaled_cap": 5.0,
        "semantics": "relative_trading_volume",
    },
)


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _source_type(value: str) -> str:
    return {"company_release": "release", "primary_source": "release", "filing": "filing"}.get(value, "news")


def _coverage(
    dimension: str, key: str, status: str, required: bool, observed: int, expected: int | None = None
) -> CoverageItem:
    return CoverageItem(
        dimension=dimension,
        key=key,
        status=status,
        required=required,
        observed_count=observed,
        expected_count=expected,
    )


def _limitation(code: str, message: str, *affected: str) -> ToolLimitation:
    return ToolLimitation(code=code, message=message, affected=list(affected))


class ToolEngine:
    def __init__(self, bundle: ArtifactBundle, strict_gpu: bool = True, embed_path: Path | None = None):
        if not strict_gpu:
            raise ToolExecutionError("strict_gpu_required")
        self.bundle = bundle
        self.embed_model = self.semantic_index = None
        self.semantic_ids: list[str] = []
        try:
            import cupy as cp
            import cudf
            import cugraph
            from cuml.manifold import UMAP
            from xgboost import XGBRegressor

            self.cp, self.cudf, self.cugraph, self.UMAP, self.XGBRegressor = (
                cp,
                cudf,
                cugraph,
                UMAP,
                XGBRegressor,
            )
            self.device = cp.cuda.runtime.getDeviceProperties(0)["name"].decode()
        except (ImportError, RuntimeError) as exc:
            raise ToolExecutionError("gpu_runtime_unavailable") from exc
        if embed_path is None:
            raise ToolExecutionError("embedding_checkpoint_required")
        self._load_embed_model(embed_path)
        self._load_semantic_index()

    def _load_embed_model(self, path: Path) -> None:
        if not path.is_dir():
            raise ToolExecutionError("embedding_checkpoint_unavailable")
        try:
            import torch
            from sentence_transformers import SentenceTransformer

            if not torch.cuda.is_available():
                raise ToolExecutionError("embedding_cuda_unavailable")
            roots = (self.bundle.root / "scenario/indexes", self.bundle.root / "indexes")
            root = next((item for item in roots if (item / "cuvs-index.json").is_file()), None)
            metadata = (
                {} if root is None else json.loads((root / "cuvs-index.json").read_text(encoding="utf-8"))
            )
            attention = metadata.get("attention")
            if attention not in {"flash_attention_2", "sdpa"}:
                raise ToolExecutionError("semantic_index_attention_mismatch")
            self.embed_model = SentenceTransformer(
                str(path),
                device="cuda",
                local_files_only=True,
                model_kwargs={"dtype": torch.bfloat16, "attn_implementation": attention},
            )
            self.embed_model.max_seq_length, self.embed_attention = 4096, attention
        except ToolExecutionError:
            raise
        except ImportError as exc:
            raise ToolExecutionError("embedding_runtime_unavailable") from exc
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError(f"embedding_load_failed_{type(exc).__name__.lower()}") from exc

    def _load_semantic_index(self) -> None:
        roots = (self.bundle.root / "scenario/indexes", self.bundle.root / "indexes")
        root = next((item for item in roots if (item / "cuvs-index.json").is_file()), None)
        if root is None:
            raise ToolExecutionError("semantic_index_unavailable")
        try:
            metadata = json.loads((root / "cuvs-index.json").read_text(encoding="utf-8"))
            identity = (
                metadata.get("model_id"),
                metadata.get("revision"),
                metadata.get("dimension"),
                metadata.get("attention"),
            )
            if metadata.get("fixture_only") or identity != (EMBED_ID, EMBED_REV, 2048, self.embed_attention):
                raise ToolExecutionError("semantic_index_model_mismatch")
            index_path = self.bundle.root / metadata["index_path"]
            if not index_path.is_file():
                index_path = self.bundle.root / "scenario" / metadata["index_path"]
            from cuvs.neighbors import brute_force

            self.semantic_index = brute_force.load(str(index_path))
            self.semantic_ids = json.loads((root / "embedding_ids.json").read_text(encoding="utf-8"))
        except ToolExecutionError:
            raise
        except (ImportError, KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("semantic_index_load_failed") from exc

    def _receipt(self, engine: str, started: float, gpu: bool) -> ExecutionReceipt:
        if engine != "deterministic" and not gpu:
            raise ToolExecutionError(f"{engine.replace('-', '_')}_gpu_execution_unavailable")
        store = self.bundle.store
        binding = {
            "scenario_id": store.manifest["scenario_id"],
            "market_manifest_sha256": store.manifest["market"]["manifest_sha256"],
            "document_manifest_sha256": store.manifest["documents"]["manifest_sha256"],
            "market_readiness_sha256": store.manifest["readiness"]["market"]["sha256"],
            "document_readiness_sha256": store.manifest["readiness"]["documents"]["sha256"],
        }
        return ExecutionReceipt(
            engine=engine if gpu else "deterministic",
            device=self.device if gpu else "control",
            gpu_executed=gpu,
            fallback_used=False,
            duration_ms=(time.perf_counter() - started) * 1000,
            artifact_manifest_sha256=self.bundle.manifest_sha256,
            **binding,
        )

    def _result(
        self,
        tool: str,
        as_of: datetime,
        started: float,
        outcome: str,
        data: dict,
        coverage: list[CoverageItem],
        limitations: list[ToolLimitation],
        *,
        engine: str = "deterministic",
        gpu: bool = False,
        evidence=(),
        citations=(),
        artifacts=None,
        warnings=None,
    ) -> ToolResult:
        return ToolResult(
            tool=tool,
            as_of=as_of,
            outcome=outcome,
            coverage=coverage,
            limitations=limitations,
            evidence=list(evidence),
            citations=list(citations),
            receipt=self._receipt(engine, started, gpu),
            data=data,
            artifacts=artifacts or [],
            warnings=warnings or [],
        )

    def _market_evidence(self, row: dict) -> tuple[EvidenceItem, Citation]:
        observed = parse_time(row["bar_end"])
        available = parse_time(row.get("available_at", row["bar_end"]))
        aliases = {
            "open": row.get("adjusted_open", row.get("open")),
            "high": row.get("adjusted_high", row.get("high")),
            "low": row.get("adjusted_low", row.get("low")),
            "close": row.get("adjusted_close", row.get("close")),
            "volume": row.get("volume"),
        }
        values = {key: float(value) for key, value in aliases.items() if value is not None}
        values.update(
            ticker=row["instrument_id"],
            interval=row.get("interval", "1d"),
            price_basis=row.get("price_basis", "provider_adjusted"),
            vintage_status=row.get("vintage_status", "unknown"),
        )
        eid = stable_id("ev", self.bundle.manifest_sha256, row["source_id"], row["source_row_id"])
        canonical = {
            **values,
            **{
                key: row.get(key)
                for key in (
                    "session_date",
                    "bar_start",
                    "bar_end",
                    "source_id",
                    "source_row_id",
                    "captured_at",
                    "source_capture_id",
                    "source_capture_sha256",
                )
            },
        }
        content = json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":"))
        display = ", ".join(f"{key} {value}" for key, value in aliases.items() if value is not None)
        citation = Citation(
            citation_id=stable_id("cit", row["source_id"], row["source_row_id"], length=16),
            evidence_id=eid,
            title=f"{row['instrument_id']} session market record for {observed.date()}",
            url=row.get(
                "attribution_url",
                f"https://www.nasdaq.com/market-activity/stocks/{row['instrument_id'].lower()}/historical",
            ),
            source_type="market",
            published_at=observed,
            available_at=available,
            excerpt=f"Adjusted session record: {display}.",
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        )
        return EvidenceItem(
            evidence_id=eid,
            observed_at=observed,
            available_at=available,
            source_id=row["source_id"],
            values=values,
        ), citation

    def _document_evidence(self, row: dict, score: float | None = None) -> tuple[EvidenceItem, Citation]:
        published, available = parse_time(row["published_at"]), parse_time(row["available_at"])
        values = {"title": row["title"], "text": row["text"], "source_type": row["source_type"]}
        if score is not None:
            values["similarity"] = round(score, 6)
        content_sha = hashlib.sha256(row["text"].encode()).hexdigest()
        eid = stable_id(
            "ev", self.bundle.manifest_sha256, row["source_id"], row.get("revision", "1"), content_sha
        )
        citation = Citation(
            citation_id=stable_id("cit", row["source_id"], row.get("revision", "1"), length=16),
            evidence_id=eid,
            title=row["title"],
            url=row["canonical_url"],
            source_type=_source_type(row["source_type"]),
            published_at=published,
            available_at=available,
            excerpt=row["text"][:1200],
            content_sha256=content_sha,
        )
        return EvidenceItem(
            evidence_id=eid,
            observed_at=published,
            available_at=available,
            source_id=row["source_id"],
            values=values,
        ), citation

    def _derived_evidence(self, row: dict) -> tuple[EvidenceItem, Citation]:
        observed = parse_time(row["feature_at"])
        source_row = row["source_row_id"]
        content = json.dumps(row, sort_keys=True, separators=(",", ":"))
        eid = stable_id("ev", self.bundle.manifest_sha256, "derived", row["source_id"], source_row)
        source = self.bundle.store.sources.get(row["source_id"])
        url = (
            source["attribution_url"]
            if source
            else f"https://www.nasdaq.com/market-activity/stocks/{row['instrument_id'].lower()}/historical"
        )
        values = {
            key: value
            for key, value in row.items()
            if key not in {"source_id", "source_row_id", "input_source_row_ids", "summary"}
        }
        citation = Citation(
            citation_id=stable_id("cit", "derived", row["source_id"], source_row, length=16),
            evidence_id=eid,
            title=f"Observed {row['instrument_id']} feature row for {row['session_date']}",
            url=url,
            source_type="market",
            published_at=observed,
            available_at=observed,
            excerpt=row["summary"]
            if row.get("summary")
            else f"Observed return {row['return_1d']}; volume ratio {row['volume_ratio']}.",
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        )
        return EvidenceItem(
            evidence_id=eid,
            observed_at=observed,
            available_at=observed,
            source_id=row["source_id"],
            values=values,
        ), citation

    def _metric_evidence(
        self, row: dict, values: dict, purpose: str, inputs: list[dict]
    ) -> tuple[EvidenceItem, Citation]:
        source_rows = [item["source_row_id"] for item in inputs]
        observed = row["bar_end"]
        payload = {
            "instrument_id": row["instrument_id"],
            "session_date": row.get("session_date", observed[:10]),
            "feature_at": observed,
            "source_id": row["source_id"],
            "source_row_id": stable_id("row", "derived", purpose, source_rows),
            "input_source_row_ids": source_rows,
            **values,
        }
        return self._derived_evidence(payload)

    def _action_evidence(self, row: dict) -> tuple[EvidenceItem, Citation]:
        published, effective = parse_time(row["published_at"]), parse_time(row["effective_at"])
        digest = row["source_sha256"]
        values = {
            "ticker": row["instrument_id"],
            **{
                key: row[key]
                for key in (
                    "action_type",
                    "factor",
                    "published_at",
                    "effective_at",
                    "captured_at",
                    "vintage_status",
                    "attribution_url",
                    "source_sha256",
                )
            },
        }
        eid = stable_id("ev", self.bundle.manifest_sha256, "corporate-action", values)
        citation_id = stable_id("cit", "corporate-action", values, length=16)
        citation = Citation(
            citation_id=citation_id,
            evidence_id=eid,
            title=f"{row['instrument_id']} {row['action_type']} corporate action",
            url=row["attribution_url"],
            source_type="market",
            published_at=published,
            available_at=published,
            excerpt=f"{row['action_type']} factor {row['factor']} effective {row['effective_at']}.",
            content_sha256=digest,
        )
        return EvidenceItem(
            evidence_id=eid,
            observed_at=effective,
            available_at=published,
            source_id="corporate-action",
            values=values,
        ), citation

    def _computed_evidence(
        self,
        ticker: str,
        as_of: datetime,
        purpose: str,
        values: dict,
        input_ids: list[str],
        source_type: str = "market",
    ) -> tuple[EvidenceItem, Citation]:
        payload = {"ticker": ticker, "purpose": purpose, "input_evidence_ids": input_ids, **values}
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(content.encode()).hexdigest()
        eid = stable_id("ev", self.bundle.manifest_sha256, purpose, digest)
        source = f"computed-{purpose}"
        citation = Citation(
            citation_id=stable_id("cit", source, digest, length=16),
            evidence_id=eid,
            title=f"{ticker} {purpose.replace('_', ' ')} result",
            url="https://developer.nvidia.com/",
            source_type=source_type,
            published_at=as_of,
            available_at=as_of,
            excerpt=str(values["summary"])[:1200],
            content_sha256=digest,
        )
        return EvidenceItem(
            evidence_id=eid, observed_at=as_of, available_at=as_of, source_id=source, values=payload
        ), citation

    def _window(self, ticker: str, as_of: datetime, lookback: int, fields: tuple[str, ...]):
        try:
            window = self.bundle.store.window(ticker, as_of, lookback=lookback, fields=fields)
        except MarketStoreError as exc:
            raise ToolExecutionError(exc.code) from exc
        if window.rows and not window.gpu_executed:
            raise ToolExecutionError("market_gpu_execution_unavailable")
        return window

    def _eligible_actions(self, ticker: str, as_of: datetime, resolved: str | None) -> list[dict]:
        store = self.bundle.store
        columns = (
            "instrument_id",
            "action_type",
            "factor",
            "published_at",
            "effective_at",
            "captured_at",
            "vintage_status",
            "attribution_url",
            "source_sha256",
        )
        session = next((row for row in store.sessions if row["session_date"] == resolved), None)
        if session is None:
            return []
        try:
            rows = list(
                store.reader(
                    [store.market_root / "actions.parquet"], list(columns), [("instrument_id", "==", ticker)]
                )
            )
            close = parse_time(session["close_at"])
        except MarketStoreError as exc:
            raise ToolExecutionError(exc.code) from exc
        except Exception as exc:
            raise ToolExecutionError("corporate_action_read_failed") from exc
        eligible = []
        try:
            for row in rows:
                published, effective, captured = (
                    parse_time(row[key]) for key in ("published_at", "effective_at", "captured_at")
                )
                factor = float(row["factor"])
                digest = row["source_sha256"]
                valid = (
                    set(row) == set(columns)
                    and row["instrument_id"] == ticker
                    and isinstance(row["action_type"], str)
                    and bool(row["action_type"])
                    and type(row["factor"]) in {int, float}
                    and math.isfinite(factor)
                    and factor > 0
                    and max(published, effective) <= captured
                    and row["vintage_status"] in {"reconstructed_later", "archived_at_cutoff"}
                    and isinstance(digest, str)
                    and len(digest) == 64
                    and all(char in "0123456789abcdef" for char in digest)
                    and str(row["attribution_url"]).startswith("https://")
                )
                if not valid:
                    raise ValueError
                if published <= as_of and effective <= close:
                    eligible.append(
                        {
                            "ticker": ticker,
                            **{key: factor if key == "factor" else row[key] for key in columns[1:]},
                        }
                    )
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolExecutionError("corporate_action_contract_failed") from exc
        return sorted(
            eligible,
            key=lambda row: (
                row["effective_at"],
                row["published_at"],
                row["action_type"],
                row["source_sha256"],
            ),
        )

    def get_price_context(self, ticker: str, as_of: datetime) -> ToolResult:
        started = time.perf_counter()
        store = self.bundle.store
        required, optional = store.benchmark_symbols(ticker)
        target_window = self._window(
            ticker, as_of, 21, ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume")
        )
        if (
            target_window.status == "unsupported_fields"
            and "adjusted_close" not in target_window.missing_fields
        ):
            target_window = self._window(ticker, as_of, 21, ("adjusted_close",))
        try:
            target = gpu_price_performance(target_window)
        except Exception as exc:
            raise ToolExecutionError("cudf_analytics_failed") from exc
        target_coverage = _coverage(
            "instrument", ticker, "available" if target else "missing", True, int(bool(target)), 1
        )
        if target is None:
            limit = _limitation(
                f"market_{target_window.status}",
                f"Only a cutoff-qualified {ticker} close is available; a comparable return is unavailable."
                if target_window.rows
                else f"No eligible {ticker} adjusted market row was found.",
                ticker,
            )
            data = {
                "summary": limit.message,
                "performance": [],
                "applied_actions": [],
                "resolved_session": target_window.resolved_session,
                "price_basis": "provider_adjusted",
                "vintage_status": store.market["vintage_status"],
            }
            return self._result(
                "get_price_context", as_of, started, "no_data", data, [target_coverage], [limit]
            )
        windows = [target_window]
        performance = [target]
        coverage = [target_coverage]
        limits = []
        for symbol, is_required in [(value, True) for value in required] + [
            (value, False) for value in optional
        ]:
            window = self._window(symbol, as_of, 2, ("adjusted_close",))
            windows.append(window)
            try:
                value = gpu_price_performance(window)
            except Exception as exc:
                raise ToolExecutionError("cudf_analytics_failed") from exc
            present = value is not None
            coverage.append(
                _coverage(
                    "instrument", symbol, "available" if present else "missing", is_required, int(present), 1
                )
            )
            if present:
                performance.append(value)
            else:
                limits.append(
                    _limitation(
                        "required_benchmark_unavailable" if is_required else "optional_benchmark_unavailable",
                        f"{symbol} comparison evidence is unavailable.",
                        symbol,
                    )
                )
        pairs = [self._market_evidence(window.rows[-1]) for window in windows if window.rows]
        evidence, citations = map(list, zip(*pairs))
        summary = f"{ticker} adjusted close was ${target['close']:.2f} on {target['resolved_session']}" + (
            "."
            if target["change_pct"] is None
            else f", {target['change_pct']:.2f}% from the prior completed session."
        )
        metric_parts = [
            f"{name} {target[key]:.2f} USD"
            for name, key in (
                ("open", "adjusted_open"),
                ("high", "adjusted_high"),
                ("low", "adjusted_low"),
                ("close", "adjusted_close"),
                ("prior adjusted close", "prior_adjusted_close"),
            )
            if isinstance(target.get(key), (int, float))
        ]
        if isinstance(target.get("volume"), (int, float)):
            metric_parts.append(f"volume {target['volume']:,} shares")
        metric_parts.extend(
            f"{name} {target[key]:+.2f}%"
            for name, key in (
                ("daily close-to-prior-close return", "return_1_session_pct"),
                ("five-session return", "return_5_sessions_pct"),
                ("opening gap (open vs prior close)", "opening_gap_pct"),
                ("intraday open-to-close return", "open_to_close_return_pct"),
            )
            if isinstance(target.get(key), (int, float))
        )
        if all(
            isinstance(target.get(key), (int, float))
            for key in ("volume_baseline_sessions", "volume_baseline_median")
        ):
            volume_context = f"a {target['volume_baseline_sessions']}-session median of {target['volume_baseline_median']:,.2f} shares"
            metric_parts.append(
                f"volume ratio {target['volume_ratio']:.2f}× versus {volume_context}"
                if isinstance(target.get("volume_ratio"), (int, float))
                else f"volume baseline {volume_context}"
            )
        item, citation = self._metric_evidence(
            target_window.rows[-1],
            {
                **target,
                "summary": f"Adjusted price metrics for {ticker} on {target['resolved_session']}: {'; '.join(metric_parts)}.",
            },
            "price-context",
            target_window.rows,
        )
        evidence.append(item)
        citations.append(citation)
        actions = self._eligible_actions(ticker, as_of, target_window.resolved_session)
        for action in actions:
            item, citation = self._action_evidence(
                {"instrument_id": action["ticker"], **{key: action[key] for key in action if key != "ticker"}}
            )
            evidence.append(item)
            citations.append(citation)
        data = {
            "summary": summary,
            "performance": performance,
            "resolved_session": target_window.resolved_session,
            "required_benchmarks": list(required),
            "optional_benchmarks": list(optional),
            "applied_actions": actions,
            "price_basis": "provider_adjusted",
            "vintage_status": store.market["vintage_status"],
        }
        return self._result(
            "get_price_context",
            as_of,
            started,
            "partial" if limits else "ok",
            data,
            coverage,
            limits,
            engine="cudf",
            gpu=target_window.gpu_executed,
            evidence=evidence,
            citations=citations,
            warnings=[
                "Historical prices are a current-capture reconstruction, not historical-vintage evidence."
            ]
            if store.market["vintage_status"] == "reconstructed_later"
            else [],
        )

    def detect_market_shock(self, ticker: str, as_of: datetime) -> ToolResult:
        started = time.perf_counter()
        store = self.bundle.store
        required, optional = store.benchmark_symbols(ticker)
        benchmark = required[0] if required else "SPY"
        target = self._window(ticker, as_of, 21, ("adjusted_close", "volume"))
        if target.status != "ready" or len(target.rows) < 2:
            observed = len(target.rows)
            coverage = [
                _coverage(
                    "market_window",
                    f"{ticker}:shock",
                    "partial" if observed else "missing",
                    True,
                    observed,
                    21,
                )
            ]
            if observed:
                coverage.append(_coverage("market_window", f"{ticker}:shock_metric", "missing", True, 0, 2))
            limit = _limitation(
                "insufficient_target_history", "At least two completed target sessions are required.", ticker
            )
            return self._result(
                "detect_market_shock",
                as_of,
                started,
                "no_data",
                {
                    "summary": limit.message,
                    "is_shock": None,
                    "resolved_session": target.resolved_session,
                    "price_basis": "provider_adjusted",
                },
                coverage,
                [limit],
            )
        comparison_windows = [
            self._window(symbol, as_of, 2, ("adjusted_close",)) for symbol in (required or (benchmark,))
        ]
        comparison = comparison_windows[0]
        try:
            metrics = gpu_shock_metrics(target, comparison)
        except Exception as exc:
            raise ToolExecutionError("cudf_analytics_failed") from exc
        if metrics is None or metrics["return_pct"] is None:
            raise ToolExecutionError("shock_metric_contract_failed")
        coverage = [
            _coverage(
                "market_window",
                f"{ticker}:shock",
                "available" if len(target.rows) >= 21 else "partial",
                True,
                len(target.rows),
                21,
            ),
            *(
                _coverage(
                    "instrument",
                    symbol,
                    "available" if len(window.rows) >= 2 else "missing",
                    True,
                    len(window.rows),
                    2,
                )
                for symbol, window in zip((required or (benchmark,)), comparison_windows, strict=True)
            ),
        ]
        limits = []
        if len(target.rows) < 21:
            limits.append(
                _limitation(
                    "insufficient_volume_history",
                    "Fewer than 20 prior sessions were available for the volume baseline.",
                    ticker,
                )
            )
        for symbol, window in zip((required or (benchmark,)), comparison_windows, strict=True):
            if len(window.rows) < 2:
                limits.append(
                    _limitation("required_benchmark_unavailable", f"{symbol} history is unavailable.", symbol)
                )
        evidence, citations = zip(
            *(
                self._market_evidence(window.rows[-1])
                for window in (target, *comparison_windows)
                if window.rows
            )
        )
        abnormal, ratio = metrics["market_adjusted_return_pct"], metrics["volume_ratio"]
        summary = (
            f"{ticker}'s adjusted daily close-to-prior-close return was {metrics['return_pct']:.2f}%"
            + ("" if abnormal is None else f", {abnormal:.2f} percentage points relative to {benchmark}")
            + (
                ""
                if ratio is None
                else f"; volume was {ratio:.2f}× its {metrics['volume_baseline_sessions']}-session median"
            )
            + "."
        )
        summary += (
            f" {benchmark} daily close-to-prior-close return {metrics['benchmark_return_pct']:+.2f}%."
            if metrics["benchmark_return_pct"] is not None
            else f" {benchmark} return unavailable."
        )
        flag = "unavailable" if metrics["is_shock"] is None else str(metrics["is_shock"]).lower()
        summary += f" Detector is_shock={flag}: absolute benchmark-relative return >= 5 percentage points OR volume ratio >= 2; requires benchmark return. This is not a causal or systemic classification."
        data = {
            "summary": summary,
            **metrics,
            "benchmark": benchmark,
            "resolved_session": target.resolved_session,
            "price_basis": "provider_adjusted",
            "vintage_status": target.vintage_status,
            "optional_benchmarks": list(optional),
        }
        derived_item, derived_citation = self._metric_evidence(
            target.rows[-1],
            {"ticker": ticker, **metrics, "benchmark": benchmark, "summary": summary},
            "shock",
            [*target.rows, *(row for window in comparison_windows for row in window.rows)],
        )
        return self._result(
            "detect_market_shock",
            as_of,
            started,
            "partial" if limits else "ok",
            data,
            coverage,
            limits,
            engine="cudf",
            gpu=target.gpu_executed,
            evidence=(*evidence, derived_item),
            citations=(*citations, derived_citation),
            warnings=[
                "Historical prices are a current-capture reconstruction, not historical-vintage evidence."
            ],
        )

    def _embeddings(self, texts: list[str], *, query: bool, dimensions: int = 2048):
        if self.embed_model is None:
            raise ToolExecutionError("embedding_model_unavailable")
        try:
            method = self.embed_model.encode_query if query else self.embed_model.encode_document
            vectors = method(texts, batch_size=min(8, len(texts)), convert_to_tensor=True)
            if dimensions != 2048:
                vectors = vectors[:, :dimensions]
            if not vectors.is_cuda or vectors.shape != (len(texts), dimensions):
                raise ToolExecutionError("embedding_tensor_invalid")
            return vectors.float()
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("embedding_execution_failed") from exc

    def _rank_documents(self, documents: list[dict], query: str, top_k: int) -> list[tuple[dict, float]]:
        if self.semantic_index is None or self.cp is None:
            raise ToolExecutionError("cuvs_runtime_unavailable")
        queries = self._embeddings([query], query=True)
        from cuvs.neighbors import brute_force

        distances, neighbors = brute_force.search(
            self.semantic_index, self.cp.from_dlpack(queries), k=len(self.semantic_ids)
        )
        eligible = {row.get("chunk_id") for row in documents}
        by_chunk = {row.get("chunk_id"): row for row in self.bundle.documents}
        ranked = [
            (by_chunk[self.semantic_ids[index]], float(score))
            for index, score in zip(
                self.cp.asnumpy(neighbors)[0].tolist(), self.cp.asnumpy(distances)[0].tolist(), strict=True
            )
            if self.semantic_ids[index] in eligible and self.semantic_ids[index] in by_chunk
        ]
        return ranked[:top_k]

    def search_news(self, ticker: str, as_of: datetime, query: str, top_k: int = 5) -> ToolResult:
        if not query.strip() or len(query) > 1000 or not 1 <= top_k <= 20:
            raise ToolExecutionError("invalid_search_bounds")
        started = time.perf_counter()
        documents = self.bundle.eligible_documents(ticker, as_of)
        lowered = query.lower()
        days = 1 if "same-day" in lowered or "same day" in lowered else 90
        if days is not None:
            documents = (
                [row for row in documents if parse_time(row["available_at"]).date() == as_of.date()]
                if days == 1
                else [
                    row
                    for row in documents
                    if parse_time(row["available_at"]) >= as_of - timedelta(days=days)
                ]
            )
        if not documents:
            limit = _limitation(
                "no_eligible_documents", "No cutoff-qualified documents were eligible.", ticker
            )
            return self._result(
                "search_news",
                as_of,
                started,
                "no_data",
                {"summary": limit.message, "matches": []},
                [_coverage("documents", ticker, "missing", True, 0, 1)],
                [limit],
            )
        try:
            identity = lambda row: (row.get("chunk_id"), row["source_id"], row.get("revision", "1"))
            semantic = self._rank_documents(documents, query, len(documents))
            scores = {identity(row): score for row, score in semantic}
            newest = {}
            [
                newest.setdefault(row["source_type"], row)
                for row in sorted(documents, key=lambda item: item["available_at"], reverse=True)
            ]
            selected = {identity(row) for row in newest.values()}
            ranked = [
                *((row, scores.get(identity(row), 0.0)) for row in newest.values()),
                *(pair for pair in semantic if identity(pair[0]) not in selected),
            ][:top_k]
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("cuvs_search_failed") from exc
        if not ranked:
            limit = _limitation("no_semantic_matches", "No eligible semantic matches were returned.", ticker)
            return self._result(
                "search_news",
                as_of,
                started,
                "no_data",
                {"summary": limit.message, "matches": []},
                [_coverage("documents", f"{ticker}:matches", "missing", True, 0, 1)],
                [limit],
                engine="cuvs",
                gpu=True,
            )
        evidence, citations = zip(*(self._document_evidence(row, score) for row, score in ranked))
        return self._result(
            "search_news",
            as_of,
            started,
            "ok",
            {
                "summary": f"Found {len(evidence)} eligible, deduplicated sources for {ticker} at the cutoff.",
                "matches": [item.values for item in evidence],
                "source_types": sorted({_source_type(row["source_type"]) for row, _ in ranked}),
            },
            [_coverage("documents", ticker, "available", True, len(ranked), min(top_k, len(documents)))],
            [],
            engine="cuvs",
            gpu=True,
            evidence=evidence,
            citations=citations,
        )

    def _market_feature(
        self, ticker: str, as_of: datetime
    ) -> tuple[dict | None, dict | None, int, list[dict]]:
        window = self._window(ticker, as_of, 21, ("adjusted_close", "volume"))
        rows = list(window.rows)
        if len(rows) < 21:
            return None, rows[-1] if rows else None, len(rows), rows
        prior, current = rows[-2], rows[-1]
        before = float(prior.get("adjusted_close", prior.get("close")))
        close = float(current.get("adjusted_close", current.get("close")))
        volumes = [float(row["volume"]) for row in rows[:-1][-20:] if row.get("volume") is not None]
        if before <= 0 or not volumes or statistics.median(volumes) <= 0:
            return None, current, len(rows), rows
        change = close / before - 1
        ratio = float(current["volume"]) / statistics.median(volumes)
        if not all(math.isfinite(value) for value in (change, ratio)):
            raise ToolExecutionError("invalid_market_feature")
        return (
            {
                "instrument_id": ticker,
                "session_date": current.get("session_date", current["bar_end"][:10]),
                "feature_at": current["bar_end"],
                "return_1d": change,
                "absolute_return_pct": abs(change) * 100,
                "volume_ratio": ratio,
                "volume_baseline_sessions": len(volumes),
                "source_id": current["source_id"],
                "source_row_id": current["source_row_id"],
                "future_outcome_excluded": True,
            },
            current,
            len(rows),
            rows,
        )

    def _analogue_rows(self) -> list[dict]:
        try:
            value = json.loads(
                (self.bundle.root / "processed/analogue_features.json").read_text(encoding="utf-8")
            )
            if value.get("schema_version") != 2 or not isinstance(value.get("rows"), list):
                raise ValueError
            return value["rows"]
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("analogue_artifact_unreadable") from exc

    @staticmethod
    def _analogue_vector(row: dict) -> dict[str, float]:
        return {
            spec["name"]: min(float(row[spec["name"]]) / spec["scale_divisor"], spec["scaled_cap"])
            for spec in _ANALOGUE_FEATURES
        }

    @classmethod
    def _analogue_profile(cls, row: dict) -> dict[str, object]:
        signed = float(row["return_1d"]) * 100
        return {
            "signed_return_pct": signed,
            "signed_direction": "up" if signed > 0 else "down" if signed < 0 else "flat",
            "absolute_return_pct": float(row["absolute_return_pct"]),
            "volume_ratio": float(row["volume_ratio"]),
            "scaled_vector": cls._analogue_vector(row),
        }

    @staticmethod
    def _analogue_contract() -> dict[str, object]:
        return {
            "algorithm": "cuvs-sqeuclidean-v1",
            "distance_metric": "squared_euclidean",
            "lower_distance_is_more_similar": True,
            "ranked_features": [dict(spec) for spec in _ANALOGUE_FEATURES],
            "reported_not_ranked": [
                {
                    "name": "signed_return_pct",
                    "reason": "Direction is excluded; absolute_return_pct ranks magnitude only.",
                }
            ],
        }

    def _rank_features(self, query: dict, rows: list[dict], top_k: int) -> list[tuple[dict, float]]:
        query_vector = list(self._analogue_vector(query).values())
        vectors = [list(self._analogue_vector(row).values()) for row in rows]
        if self.cp is not None:
            from cuvs.neighbors import brute_force

            matrix = self.cp.asarray(vectors, dtype=self.cp.float32)
            queries = self.cp.asarray([query_vector], dtype=self.cp.float32)
            index = brute_force.build(matrix, metric="sqeuclidean")
            distances, neighbors = brute_force.search(index, queries, k=min(top_k, len(rows)))
            return [
                (rows[index], float(score))
                for index, score in zip(
                    self.cp.asnumpy(neighbors)[0].tolist(),
                    self.cp.asnumpy(distances)[0].tolist(),
                    strict=True,
                )
            ]
        raise ToolExecutionError("cuvs_runtime_unavailable")

    def find_historical_analogues(self, ticker: str, as_of: datetime, top_k: int = 3) -> ToolResult:
        if not 1 <= top_k <= 20:
            raise ToolExecutionError("invalid_analogue_bounds")
        started = time.perf_counter()
        query, current, observed, target_rows = self._market_feature(ticker, as_of)
        expected = 21
        contract = self._analogue_contract()
        if query is None or current is None:
            limit = _limitation(
                "insufficient_target_history", "Observed target features are unavailable.", ticker
            )
            coverage = [
                _coverage(
                    "market_window",
                    f"{ticker}:features",
                    "partial" if observed else "missing",
                    True,
                    observed,
                    expected,
                )
            ]
            if observed:
                coverage.append(
                    _coverage("analogue_candidates", f"{ticker}:target_feature", "missing", True, 0, 1)
                )
            return self._result(
                "find_historical_analogues",
                as_of,
                started,
                "no_data",
                {
                    "summary": limit.message,
                    "feature_contract": contract,
                    "target_features": None,
                    "analogues": [],
                },
                coverage,
                [limit],
            )
        target_profile = self._analogue_profile(query)
        target_values = {
            "summary": f"{ticker} target analogue features on {query['session_date']}: absolute return {target_profile['absolute_return_pct']:.4f} percentage points and volume ratio {target_profile['volume_ratio']:.4f}.",
            "return_1d": query["return_1d"],
            "volume_baseline_sessions": query["volume_baseline_sessions"],
            "future_outcome_excluded": True,
            **target_profile,
            "feature_contract": contract,
        }
        target_item, target_citation = self._metric_evidence(
            current, target_values, "analogue-target-features", target_rows
        )
        target_output = {
            "ticker": ticker,
            "session_date": query["session_date"],
            "feature_at": query["feature_at"],
            **target_profile,
            "evidence_id": target_item.evidence_id,
        }
        rows = [
            row
            for row in self._analogue_rows()
            if row.get("source_row_id") != query["source_row_id"]
            and parse_time(row["feature_at"]) < as_of
            and row.get("session_date") < query["session_date"]
            and row.get("volume_ratio") is not None
            and all(math.isfinite(float(row[key])) for key in ("absolute_return_pct", "volume_ratio"))
        ]
        if not rows:
            limit = _limitation(
                "no_eligible_analogues", "No prior observed feature rows were eligible.", ticker
            )
            coverage = [
                _coverage("market_window", f"{ticker}:features", "available", True, observed, expected),
                _coverage("analogue_candidates", ticker, "missing", True, 0, 1),
            ]
            return self._result(
                "find_historical_analogues",
                as_of,
                started,
                "no_data",
                {
                    "summary": limit.message,
                    "feature_contract": contract,
                    "target_features": target_output,
                    "analogues": [],
                },
                coverage,
                [limit],
                evidence=[target_item],
                citations=[target_citation],
            )
        try:
            ranked = self._rank_features(query, rows, top_k)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("cuvs_analogue_failed") from exc
        output = []
        evidence, citations = [target_item], [target_citation]
        for row, distance in ranked:
            candidate_profile = self._analogue_profile(row)
            candidate_item, candidate_citation = self._derived_evidence(row)
            components = {
                name: round(
                    (target_profile["scaled_vector"][name] - candidate_profile["scaled_vector"][name]) ** 2, 8
                )
                for name in target_profile["scaled_vector"]
            }
            comparison = {
                "absolute_return_pct_delta": candidate_profile["absolute_return_pct"]
                - target_profile["absolute_return_pct"],
                "volume_ratio_delta": candidate_profile["volume_ratio"] - target_profile["volume_ratio"],
                "signed_direction_match": candidate_profile["signed_direction"]
                == target_profile["signed_direction"],
            }
            values = {
                "summary": f"{row['instrument_id']} on {row['session_date']} had analogue distance {distance:.8f} from {ticker}; ranking compared unsigned return magnitude and relative volume, not signed direction.",
                "analogue_ticker": row["instrument_id"],
                "session_date": row["session_date"],
                "return_1d": row["return_1d"],
                "absolute_return_pct": row["absolute_return_pct"],
                "volume_ratio": row["volume_ratio"],
                "target_features": target_profile,
                "candidate_features": candidate_profile,
                "distance_components": components,
                "comparison": comparison,
                "distance": round(distance, 8),
                "algorithm": contract["algorithm"],
                "distance_metric": contract["distance_metric"],
            }
            item, citation = self._computed_evidence(
                ticker,
                as_of,
                "analogue-ranking",
                values,
                [target_item.evidence_id, candidate_item.evidence_id],
                "model",
            )
            evidence.extend((candidate_item, item))
            citations.extend((candidate_citation, citation))
            output.append(
                {
                    "ticker": row["instrument_id"],
                    "session_date": row["session_date"],
                    "feature_at": row["feature_at"],
                    "return_1d": row["return_1d"],
                    "signed_return_pct": candidate_profile["signed_return_pct"],
                    "absolute_return_pct": row["absolute_return_pct"],
                    "volume_ratio": row["volume_ratio"],
                    "signed_direction_match": comparison["signed_direction_match"],
                    "absolute_return_pct_delta": comparison["absolute_return_pct_delta"],
                    "volume_ratio_delta": comparison["volume_ratio_delta"],
                    "absolute_return_pct_distance_component": components["absolute_return_pct"],
                    "volume_ratio_distance_component": components["volume_ratio"],
                    "features": candidate_profile,
                    "distance_components": components,
                    "comparison": comparison,
                    "distance": round(distance, 8),
                    "feature_evidence_id": candidate_item.evidence_id,
                    "evidence_id": item.evidence_id,
                }
            )
        limits = []
        if len(ranked) < top_k:
            limits.append(
                _limitation(
                    "insufficient_analogue_candidates",
                    f"Only {len(ranked)} eligible candidates were found.",
                    ticker,
                )
            )
        coverage = [
            _coverage("market_window", f"{ticker}:features", "available", True, observed, expected),
            _coverage(
                "analogue_candidates",
                ticker,
                "available" if not limits else "partial",
                True,
                len(ranked),
                top_k,
            ),
        ]
        match_count = sum(bool(item["signed_direction_match"]) for item in output)
        direction_summary = {
            "candidate_count": len(output),
            "match_count": match_count,
            "mismatch_count": len(output) - match_count,
            "all_candidates_opposite_direction": bool(output) and match_count == 0,
        }
        return self._result(
            "find_historical_analogues",
            as_of,
            started,
            "partial" if limits else "ok",
            {
                "summary": f"Ranked {len(output)} prior observed market patterns by unsigned return magnitude and relative volume; signed direction and causal context are not ranking features.",
                "feature_contract": contract,
                "direction_summary": direction_summary,
                "target_features": target_output,
                "analogues": output,
            },
            coverage,
            limits,
            engine="cuvs",
            gpu=True,
            evidence=evidence,
            citations=citations,
        )

    def _trace_paths(self, edges: list[dict], ticker: str, max_depth: int) -> list[dict]:
        if self.cudf is None or self.cugraph is None:
            raise ToolExecutionError("cugraph_runtime_unavailable")
        nodes = {
            name: index
            for index, name in enumerate(
                sorted({ticker, *(row["from_id"] for row in edges), *(row["to_id"] for row in edges)})
            )
        }
        frame = self.cudf.DataFrame(
            {"src": [nodes[row["from_id"]] for row in edges], "dst": [nodes[row["to_id"]] for row in edges]}
        )
        graph = self.cugraph.Graph(directed=True)
        graph.from_cudf_edgelist(frame, source="src", destination="dst", store_transposed=True)
        traversed = self.cugraph.bfs(graph, start=nodes[ticker])
        distance = {
            int(row["vertex"]): int(row["distance"])
            for row in traversed[["vertex", "distance"]].to_pandas().to_dict("records")
            if 0 <= int(row["distance"]) <= max_depth
        }
        paths = [
            {
                "from": row["from_id"],
                "to": row["to_id"],
                "relation": row["relation"],
                "hops": distance[nodes[row["to_id"]]],
                "source_row_id": row.get("source_row_id", stable_id("rel", row)),
            }
            for row in edges
            if nodes[row["from_id"]] in distance
            and nodes[row["to_id"]] in distance
            and 0 < distance[nodes[row["to_id"]]] <= max_depth
        ]
        return paths

    def _relation_evidence(self, edge: dict, as_of: datetime) -> tuple[EvidenceItem, Citation]:
        source_row = edge.get("source_row_id", stable_id("rel", edge))
        observed = datetime.combine(
            datetime.fromisoformat(edge["valid_from"]).date(), datetime.min.time(), UTC
        )
        source_id = edge.get("source_id", "relationship-policy")
        document = next(
            (
                row
                for row in self.bundle.documents
                if row["source_id"] == source_id and parse_time(row["available_at"]) <= as_of
            ),
            None,
        )
        store = self.bundle.store
        market_source = next(
            (
                store.sources.get(value)
                for value in edge.get("input_source_ids", [])
                if store.sources.get(value)
            ),
            None,
        )
        url = (
            document["canonical_url"]
            if document
            else market_source["attribution_url"]
            if market_source
            else "https://www.nyse.com/markets/hours-calendars"
        )
        content = json.dumps(edge, sort_keys=True, separators=(",", ":"))
        eid = stable_id("ev", self.bundle.manifest_sha256, source_id, source_row)
        values = {
            "from": edge["from_id"],
            "to": edge["to_id"],
            "relation": edge["relation"],
            "valid_from": edge["valid_from"],
        }
        citation = Citation(
            citation_id=stable_id("cit", source_id, source_row, length=16),
            evidence_id=eid,
            title=f"{edge['from_id']} to {edge['to_id']} relationship record",
            url=url,
            source_type="relationship",
            published_at=observed,
            available_at=observed,
            excerpt=f"{edge['from_id']} to {edge['to_id']}: {edge['relation']}.",
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        )
        return EvidenceItem(
            evidence_id=eid, observed_at=observed, available_at=observed, source_id=source_id, values=values
        ), citation

    def trace_shock_propagation(self, ticker: str, as_of: datetime, max_depth: int = 2) -> ToolResult:
        if not 1 <= max_depth <= 3:
            raise ToolExecutionError("invalid_graph_bounds")
        started = time.perf_counter()
        edges = [
            row
            for row in self.bundle.relations
            if datetime.fromisoformat(row["valid_from"]).date() <= as_of.date()
            and (not row.get("valid_to") or datetime.fromisoformat(row["valid_to"]).date() >= as_of.date())
        ]
        if not any(row["from_id"] == ticker for row in edges):
            limit = _limitation(
                "no_relationship_paths", "No eligible relationship path starts at the target.", ticker
            )
            return self._result(
                "trace_shock_propagation",
                as_of,
                started,
                "no_data",
                {"summary": limit.message, "paths": []},
                [_coverage("graph_paths", ticker, "missing", True, 0, 1)],
                [limit],
            )
        try:
            paths = self._trace_paths(edges, ticker, max_depth)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("cugraph_traversal_failed") from exc
        if not paths:
            limit = _limitation("no_reachable_paths", "The graph traversal returned no bounded path.", ticker)
            return self._result(
                "trace_shock_propagation",
                as_of,
                started,
                "no_data",
                {"summary": limit.message, "paths": []},
                [_coverage("graph_paths", ticker, "missing", True, 0, 1)],
                [limit],
                engine="cugraph",
                gpu=True,
            )
        relevant = [
            next(
                row
                for row in edges
                if row["from_id"] == path["from"]
                and row["to_id"] == path["to"]
                and row["relation"] == path["relation"]
            )
            for path in paths
        ]
        evidence, citations = zip(*(self._relation_evidence(row, as_of) for row in relevant))
        return self._result(
            "trace_shock_propagation",
            as_of,
            started,
            "ok",
            {
                "summary": f"The prepared relationship graph exposes {len(paths)} bounded channels from {ticker}.",
                "paths": paths,
            },
            [_coverage("graph_paths", ticker, "available", True, len(paths), 1)],
            [],
            engine="cugraph",
            gpu=True,
            evidence=evidence,
            citations=citations,
            warnings=["Relationships are exposure channels, not proof of causal transmission."],
        )

    def _risk_contract(self, as_of: datetime) -> tuple[dict, Path] | None:
        try:
            metadata = json.loads((self.bundle.root / "models/risk-model.json").read_text(encoding="utf-8"))
            model_name = metadata["model_path"]
            if Path(model_name).name != model_name or model_name != "risk-model.ubj":
                raise ValueError
            path = self.bundle.root / "models" / model_name
            output = metadata["output_contract"]
            expected = {
                "kind": "continuous_realized_volatility",
                "units": "annualized_decimal_standard_deviation",
                "horizon_completed_sessions": 5,
                "probability": False,
                "band_thresholds": None,
            }
            if not path.is_file() or output != expected:
                raise ValueError
            if parse_time(metadata["training_cutoff"]) > as_of:
                return None
            return metadata, path
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("risk_model_artifact_unreadable") from exc

    def _predict_risk(self, path: Path, features: list[float]) -> tuple[float, bool]:
        if self.cp is None or self.XGBRegressor is None:
            raise ToolExecutionError("xgboost_gpu_unavailable")
        model = self.XGBRegressor(device="cuda")
        model.load_model(path)
        booster = model.get_booster()
        booster.set_param({"device": "cuda:0"})
        prediction = float(booster.inplace_predict(self.cp.asarray([features], dtype=self.cp.float32))[0])
        gpu = '"device":"cuda:0"' in booster.save_config()
        if not gpu or not math.isfinite(prediction) or prediction < 0:
            raise ToolExecutionError("risk_prediction_invalid")
        return prediction, True

    def predict_volatility_risk(self, ticker: str, as_of: datetime) -> ToolResult:
        started = time.perf_counter()
        shock = self.detect_market_shock(ticker, as_of)
        change, ratio = shock.data.get("return_pct"), shock.data.get("volume_ratio")
        baseline = shock.data.get("volume_baseline_sessions", 0)
        if shock.outcome == "no_data" or change is None or ratio is None or int(baseline) < 20:
            limits = list(shock.limitations) or [
                _limitation(
                    "risk_inputs_unavailable", "Observed return and volume features are unavailable.", ticker
                )
            ]
            coverage = list(shock.coverage)
            if not any(item.required and item.status == "missing" for item in coverage):
                coverage.append(_coverage("market_window", f"{ticker}:risk_features", "missing", True, 0, 1))
            return self._result(
                "predict_volatility_risk",
                as_of,
                started,
                "no_data",
                {
                    "summary": "A realized-volatility estimate is unavailable because required observed features are missing."
                },
                coverage,
                limits,
                engine=shock.receipt.engine,
                gpu=shock.receipt.gpu_executed,
                evidence=shock.evidence,
                citations=shock.citations,
            )
        contract = self._risk_contract(as_of)
        if contract is None:
            limit = _limitation(
                "risk_model_unavailable_at_cutoff",
                "No qualified risk model was eligible at the cutoff.",
                ticker,
            )
            return self._result(
                "predict_volatility_risk",
                as_of,
                started,
                "no_data",
                {"summary": limit.message},
                [*shock.coverage, _coverage("risk_model", ticker, "missing", True, 0, 1)],
                [*shock.limitations, limit],
                engine=shock.receipt.engine,
                gpu=shock.receipt.gpu_executed,
                evidence=shock.evidence,
                citations=shock.citations,
            )
        metadata, path = contract
        try:
            prediction, gpu = self._predict_risk(path, [abs(float(change)), float(ratio)])
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("xgboost_prediction_failed") from exc
        output = metadata["output_contract"]
        limits = list(shock.limitations)
        data = {
            "summary": f"The qualified model estimates {prediction:.2%} annualized realized volatility over the next 5 completed sessions.",
            "predicted_realized_volatility": prediction,
            "units": output["units"],
            "horizon_sessions": output["horizon_completed_sessions"],
            "output_kind": output["kind"],
            "training_cutoff": metadata["training_cutoff"],
        }
        item, citation = self._computed_evidence(
            ticker,
            as_of,
            "volatility-risk",
            {
                **data,
                "model_sha256": metadata.get("model_sha256", hashlib.sha256(path.read_bytes()).hexdigest()),
            },
            [value.evidence_id for value in shock.evidence],
            "model",
        )
        return self._result(
            "predict_volatility_risk",
            as_of,
            started,
            "partial" if limits else "ok",
            data,
            [*shock.coverage, _coverage("risk_model", ticker, "available", True, 1, 1)],
            limits,
            engine="xgboost-gpu",
            gpu=gpu,
            evidence=(*shock.evidence, item),
            citations=(*shock.citations, citation),
            warnings=["This is a volatility estimate, not a price forecast or investment recommendation."],
        )

    def _project_topics(self, documents: list[dict], dimensions: int) -> list[list[float]]:
        vectors = self._embeddings(
            [row["title"] + " " + row["text"] for row in documents], query=False, dimensions=32
        )
        if self.cp is None or self.UMAP is None:
            raise ToolExecutionError("cuml_runtime_unavailable")
        projection = self.UMAP(
            n_components=dimensions,
            n_neighbors=min(15, len(documents) - 1),
            init="random",
            random_state=7,
            force_serial_epochs=True,
        ).fit_transform(self.cp.from_dlpack(vectors))
        points = self.cp.asnumpy(projection).tolist()
        if len(points) != len(documents) or any(
            len(point) != dimensions or not all(math.isfinite(float(value)) for value in point)
            for point in points
        ):
            raise ToolExecutionError("topic_projection_invalid")
        return points

    def project_news_topics(
        self, ticker: str, as_of: datetime, dimensions: int = 2, max_documents: int = 99
    ) -> ToolResult:
        if dimensions not in (2, 3) or not 2 <= max_documents <= 99:
            raise ToolExecutionError("invalid_projection_bounds")
        started = time.perf_counter()
        documents = self.bundle.eligible_documents(ticker, as_of)[:max_documents]
        if not documents:
            limit = _limitation(
                "no_projection_documents",
                "No cutoff-qualified documents were eligible for projection.",
                ticker,
            )
            return self._result(
                "project_news_topics",
                as_of,
                started,
                "no_data",
                {"summary": limit.message},
                [_coverage("projection_documents", ticker, "missing", True, 0, 3)],
                [limit],
            )
        evidence, citations = zip(*(self._document_evidence(row) for row in documents))
        if len(documents) < 3:
            limit = _limitation(
                "insufficient_projection_documents",
                "At least three real documents are required for UMAP.",
                ticker,
            )
            return self._result(
                "project_news_topics",
                as_of,
                started,
                "partial",
                {"summary": f"Found {len(documents)} eligible documents, but no projection was produced."},
                [_coverage("projection_documents", ticker, "partial", True, len(documents), 3)],
                [limit],
                evidence=evidence,
                citations=citations,
            )
        try:
            coordinates = self._project_topics(documents, dimensions)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("cuml_projection_failed") from exc
        projection = {
            "dimensions": dimensions,
            "points": [
                {"evidence_id": item.evidence_id, "coordinates": point}
                for item, point in zip(evidence, coordinates, strict=True)
            ],
        }
        # Distinct parameterizations or inputs/results must coexist in one report.
        artifact = Artifact(
            artifact_id=stable_id(
                "artifact",
                self.bundle.manifest_sha256,
                ticker,
                as_of,
                "topics",
                dimensions,
                max_documents,
                projection,
            ),
            kind="topic_projection",
            title=f"{ticker} evidence topic map",
            data=projection,
        )
        item, citation = self._computed_evidence(
            ticker,
            as_of,
            "topic-projection",
            {
                "summary": f"Projected {len(coordinates)} eligible evidence documents into {dimensions} dimensions.",
                "dimensions": dimensions,
                "document_count": len(coordinates),
                "artifact_sha256": hashlib.sha256(
                    json.dumps(artifact.data, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            },
            [value.evidence_id for value in evidence],
            "model",
        )
        return self._result(
            "project_news_topics",
            as_of,
            started,
            "ok",
            {
                "summary": f"Projected {len(coordinates)} eligible evidence documents into {dimensions} dimensions."
            },
            [_coverage("projection_documents", ticker, "available", True, len(documents), 3)],
            [],
            engine="cuml",
            gpu=True,
            evidence=(item, *evidence),
            citations=(citation, *citations),
            artifacts=[artifact],
        )
