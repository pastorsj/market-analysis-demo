"""Daily bars loaded once into cuDF, with point-in-time features computed on the GPU."""

from __future__ import annotations

import math
from typing import Any
from zoneinfo import ZoneInfo

from .features import daily_features
from .scenario import Scenario, ScenarioError

_BAR_COLUMNS = [
    "instrument_id",
    "session_date",
    "bar_end",
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
    "adjusted_close",
    "volume",
    "source_id",
    "source_row_id",
]
_FEATURES = (
    "prior_close",
    "return_1d_pct",
    "return_5d_pct",
    "opening_gap_pct",
    "open_to_close_pct",
    "volume_baseline_median",
    "volume_ratio",
)
EASTERN = ZoneInfo("America/New_York")


def _clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class MarketData:
    """All daily bars plus derived features, resident on the GPU."""

    def __init__(self, scenario: Scenario, cudf: Any, cp: Any):
        self.scenario, self.cudf, self.cp = scenario, cudf, cp
        frame = cudf.read_parquet([str(path) for path in scenario.bar_paths], columns=_BAR_COLUMNS)
        frame = frame.sort_values(["instrument_id", "session_date"]).reset_index(drop=True)
        position = {item.session_date: index for index, item in enumerate(scenario.sessions)}
        parts = []
        for ticker in frame["instrument_id"].unique().to_arrow().to_pylist():
            part = frame[frame["instrument_id"] == ticker].reset_index(drop=True)
            values = {
                name: part[name].astype("float64").to_cupy(na_value=cp.nan)
                for name in ("adjusted_open", "adjusted_close", "volume")
            }
            for name, array in daily_features(
                cp, values["adjusted_open"], values["adjusted_close"], values["volume"]
            ).items():
                part[name] = cudf.Series(array, nan_as_null=False)
            parts.append(part)
        self.frame = cudf.concat(parts, ignore_index=True)
        self.frame["position"] = self.frame["session_date"].map(position)
        if self.frame["position"].isna().any():
            raise ScenarioError("bar_outside_session_calendar")
        self.instruments = set(frame["instrument_id"].unique().to_arrow().to_pylist())
        sectors = cudf.read_parquet(str(scenario.instruments_path)).to_pandas()
        self.sectors = {row.instrument_id: row.sector for row in sectors.itertuples()}
        self.first_session = scenario.sessions[0].session_date
        self.last_session = scenario.sessions[-1].session_date

    def rows(self, ticker: str, session_date: str, count: int) -> list[dict[str, Any]]:
        """The last ``count`` bars for ``ticker`` on or before ``session_date``."""
        frame = self.frame
        selected = frame[(frame["instrument_id"] == ticker) & (frame["session_date"] <= session_date)]
        records = selected.tail(count).to_pandas().to_dict("records")
        return [{key: _clean(value) for key, value in row.items()} for row in records]

    def returns_matrix(self, tickers: list[str], session_date: str, count: int) -> tuple[list[str], Any]:
        """Aligned daily returns (sessions x tickers) ending at ``session_date``, on the GPU."""
        frame = self.frame
        selected = frame[frame["instrument_id"].isin(tickers) & (frame["session_date"] <= session_date)]
        wide = selected.pivot(index="session_date", columns="instrument_id", values="return_1d_pct")
        wide = wide.sort_index().tail(count).dropna(axis=1, thresh=int(count * 0.9)).dropna()
        return list(wide.columns), wide.to_cupy()

    def history(self, ticker: str, session_date: str, *, inclusive: bool = False) -> Any:
        """One ticker's feature rows before (or through) ``session_date``, as a cuDF frame."""
        frame = self.frame
        dates = frame["session_date"]
        cutoff = dates <= session_date if inclusive else dates < session_date
        return frame[(frame["instrument_id"] == ticker) & cutoff]

    def sector(self, ticker: str) -> str | None:
        return self.sectors.get(ticker)

    def benchmarks(self, ticker: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self.scenario.benchmarks_for(self.sector(ticker))

    def in_coverage(self, as_of_date: str) -> bool:
        return self.first_session <= as_of_date <= self.last_session
