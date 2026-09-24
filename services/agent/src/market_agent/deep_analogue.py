"""Validate analogue prose against the measured feature contract."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from .deep_answers import _normalize_answer_prose
from .evidence import EvidenceRun


def _analogue_claim_citation_ids(citations: tuple[Any, ...], run: EvidenceRun) -> tuple[str, ...]:
    """Bind analogue prose to target-shock and ranking evidence, not model-picked IDs."""
    available = {item.citation_id for item in citations}
    target_id: str | None = None
    for record in run.records:
        if record.identity.tool != "detect_market_shock" or not record.result:
            continue
        rows = record.result.get("citations", ())
        observed = [
            item
            for item in rows
            if isinstance(item, Mapping)
            and item.get("citation_id") in available
            and str(item.get("title", "")).startswith("Observed ")
        ]
        fallback = [
            item for item in rows if isinstance(item, Mapping) and item.get("citation_id") in available
        ]
        selected = observed[-1:] or fallback[:1]
        if selected:
            target_id = str(selected[0]["citation_id"])
            break
    ranking_ids = [
        item.citation_id
        for item in citations
        if item.source_type == "model" and "analogue-ranking" in item.title.lower()
    ]
    return tuple(dict.fromkeys(([target_id] if target_id else []) + ranking_ids))[:12]


def _analogue_contract_data(run: EvidenceRun) -> Mapping[str, Any] | None:
    for record in run.records:
        if record.identity.tool == "find_historical_analogues" and record.result:
            data = record.result.get("data")
            return data if isinstance(data, Mapping) else None
    return None


def _contract_sentences(value: str) -> list[str]:
    """Split prose without treating the market-comparison abbreviation `vs.` as a stop."""
    return re.split(r"(?<!vs\.)(?<=[.!?])(?=\s+)", value, flags=re.I)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_analogue_prose(data: Mapping[str, Any]) -> str | None:
    """Render measured analogue rows from structured evidence, never model arithmetic."""
    target = data.get("target_features")
    analogues = data.get("analogues")
    contract = data.get("feature_contract")
    direction = data.get("direction_summary")
    if not (
        isinstance(target, Mapping)
        and isinstance(analogues, list)
        and analogues
        and isinstance(contract, Mapping)
        and isinstance(direction, Mapping)
    ):
        return None
    specs = {
        str(item.get("name")): item
        for item in contract.get("ranked_features", ())
        if isinstance(item, Mapping)
    }
    absolute_spec = specs.get("absolute_return_pct")
    volume_spec = specs.get("volume_ratio")
    if not isinstance(absolute_spec, Mapping) or not isinstance(volume_spec, Mapping):
        return None
    absolute_scale = _finite_float(absolute_spec.get("scale_divisor"))
    volume_scale = _finite_float(volume_spec.get("scale_divisor"))
    absolute_cap = _finite_float(absolute_spec.get("scaled_cap"))
    volume_cap = _finite_float(volume_spec.get("scaled_cap"))
    target_signed = _finite_float(target.get("signed_return_pct"))
    target_absolute = _finite_float(target.get("absolute_return_pct"))
    target_volume = _finite_float(target.get("volume_ratio"))
    target_ticker = str(target.get("ticker") or "").strip()
    target_date = str(target.get("session_date") or "").strip()
    if (
        any(value is None or value <= 0 for value in (absolute_scale, volume_scale, absolute_cap, volume_cap))
        or any(value is None for value in (target_signed, target_absolute, target_volume))
        or not target_ticker
        or not target_date
    ):
        return None

    assert absolute_scale is not None and volume_scale is not None
    assert absolute_cap is not None and volume_cap is not None
    assert target_signed is not None and target_absolute is not None
    assert target_volume is not None
    target_direction = "up" if target_signed > 0 else "down" if target_signed < 0 else "flat"
    rows: list[str] = []
    match_count = 0
    for index, item in enumerate(analogues, start=1):
        if not isinstance(item, Mapping):
            return None
        ticker = str(item.get("ticker") or "").strip()
        session_date = str(item.get("session_date") or "").strip()
        signed = _finite_float(item.get("signed_return_pct"))
        absolute = _finite_float(item.get("absolute_return_pct"))
        volume = _finite_float(item.get("volume_ratio"))
        distance = _finite_float(item.get("distance"))
        if (
            not ticker
            or not session_date
            or any(value is None for value in (signed, absolute, volume, distance))
        ):
            return None
        assert signed is not None and absolute is not None
        assert volume is not None and distance is not None
        candidate_direction = "up" if signed > 0 else "down" if signed < 0 else "flat"
        direction_match = candidate_direction == target_direction
        match_count += int(direction_match)
        absolute_delta = absolute - target_absolute
        volume_delta = volume - target_volume
        absolute_component = (
            min(absolute / absolute_scale, absolute_cap) - min(target_absolute / absolute_scale, absolute_cap)
        ) ** 2
        volume_component = (
            min(volume / volume_scale, volume_cap) - min(target_volume / volume_scale, volume_cap)
        ) ** 2
        rows.append(
            f"{index}. **{ticker} ({session_date})** — distance {distance:.4f}; "
            f"return {signed:+.2f}% (magnitude {absolute:.2f}%, Δ {absolute_delta:+.2f} pp); "
            f"volume ratio {volume:.2f}× (Δ {volume_delta:+.2f}×); squared normalized "
            f"components {absolute_component:.4f} return + {volume_component:.4f} volume; "
            f"direction {candidate_direction} vs. target {target_direction} "
            f"({'match' if direction_match else 'mismatch'})."
        )

    candidate_count = len(rows)
    mismatch_count = candidate_count - match_count
    if (
        direction.get("candidate_count") != candidate_count
        or direction.get("match_count") != match_count
        or direction.get("mismatch_count") != mismatch_count
    ):
        raise ValueError("analogue_direction_contract")
    cap_clause = (
        f"each scaled feature capped at {absolute_cap:g}"
        if absolute_cap == volume_cap
        else f"caps of {absolute_cap:g} and {volume_cap:g}, respectively"
    )
    return (
        f"On {target_date}, {target_ticker} returned {target_signed:+.2f}% in one session "
        f"with a {target_volume:.2f}× volume ratio. The closest measured analogues were:\n\n"
        + "\n\n".join(rows)
        + "\n\n"
        + "Ranking method: squared Euclidean distance divides absolute-return magnitude "
        f"by {absolute_scale:g} percentage points and volume ratio by {volume_scale:g}×, "
        f"with {cap_clause}, then sums the two squared differences. Lower is closer; "
        "signed direction is excluded from ranking.\n\n"
        f"Where comparisons break down: {match_count} of {candidate_count} candidates "
        f"match the target direction and {mismatch_count} differ. Similarity here measures "
        "return magnitude and relative volume, not shared cause, sector, macro regime, "
        "business exposure, or eventual outcome. Candidate dates are shown above, but "
        "candidate-date-specific follow-up price, news, and context queries are unavailable."
    )


def _correct_analogue_contract_prose(value: str, run: EvidenceRun) -> str:
    """Replace only direction/scale prose with authoritative structured evidence."""
    data = _analogue_contract_data(run)
    if data is None:
        return value
    if canonical := _canonical_analogue_prose(data):
        return _normalize_answer_prose(canonical)
    canonical: list[str] = []
    cleaned = value
    direction = data.get("direction_summary")
    if (
        isinstance(direction, Mapping)
        and direction.get("all_candidates_opposite_direction") is True
        and direction.get("match_count") == 0
    ):
        target = data.get("target_features")
        target_direction = (
            str(target.get("signed_direction"))
            if isinstance(target, Mapping) and target.get("signed_direction")
            else "the target direction"
        )
        analogues = data.get("analogues")
        candidate_directions = (
            {
                str(item.get("features", {}).get("signed_direction"))
                for item in analogues
                if isinstance(item, Mapping)
                and isinstance(item.get("features"), Mapping)
                and item.get("features", {}).get("signed_direction")
            }
            if isinstance(analogues, list)
            else set()
        )
        candidate_direction = (
            next(iter(candidate_directions))
            if len(candidate_directions) == 1
            else "a direction different from the target"
        )
        cleaned = re.sub(
            r"\s*\([^)]*(?:signed[- ]direction|both\s+(?:up|down))[^)]*\)",
            "",
            cleaned,
            flags=re.I,
        )
        direction_marker = re.compile(
            r"\b(?:signed[- ]direction|opposite[- ]signed|same[- ]signed|"
            r"opposite direction|same direction|shares? the (?:up|down) direction)\b",
            re.I,
        )
        cleaned = "".join(
            sentence for sentence in _contract_sentences(cleaned) if direction_marker.search(sentence) is None
        ).strip()
        count = int(direction.get("candidate_count", 0) or 0)
        canonical.append(
            f"Direction check: all {count} returned candidates are {candidate_direction} "
            f"while the target is {target_direction}; none matches the target's signed "
            "direction, so every candidate has an opposite-signed breakdown. Signed "
            "direction is not a ranking feature."
        )
    cleaned = "".join(
        sentence
        for sentence in _contract_sentences(cleaned)
        if not re.search(
            r"\b(?:ranges?|ranging|within\s+(?:roughly\s+|~\s*)|overlaps?|overlapping|"
            r"dominates?|contributes?\s+more|var(?:y|ies))\b",
            sentence,
            re.I,
        )
    ).strip()
    contract = data.get("feature_contract")
    ranked = contract.get("ranked_features") if isinstance(contract, Mapping) else None
    if isinstance(ranked, list):
        specs = {str(item.get("name")): item for item in ranked if isinstance(item, Mapping)}
        absolute = specs.get("absolute_return_pct")
        volume = specs.get("volume_ratio")
        if absolute and volume:
            cleaned = "".join(
                sentence
                for sentence in _contract_sentences(cleaned)
                if not re.search(r"\bscale divisors?\b", sentence, re.I)
            ).strip()
            canonical.append(
                "Feature scaling: squared Euclidean distance divides absolute-return "
                f"magnitude by {float(absolute['scale_divisor']):g} percentage points "
                f"and volume ratio by {float(volume['scale_divisor']):g}×, caps each "
                f"scaled feature at {float(absolute['scaled_cap']):g}, then sums their "
                "squared differences."
            )
    if not canonical:
        return value
    first, separator, rest = cleaned.partition("\n\n")
    merged = first + "\n\n" + " ".join(canonical)
    if separator and rest:
        merged += "\n\n" + rest
    corrected = _normalize_answer_prose(merged)
    if (
        isinstance(direction, Mapping)
        and direction.get("all_candidates_opposite_direction") is True
        and re.search(
            r"\b(?:same[- ]signed direction|signed[- ]direction\s+matches?|"
            r"both\s+down|except\s+(?:the\s+)?(?:fifth|last))\b",
            corrected,
            re.I,
        )
    ):
        raise ValueError("analogue_direction_contract")
    if isinstance(contract, Mapping) and re.search(
        r"\bscale divisors?\b.{0,80}\b(?:omitted|missing|unavailable)\b",
        corrected,
        re.I,
    ):
        raise ValueError("analogue_feature_contract")
    return corrected


def _correct_analogue_contract_uncertainty(values: tuple[str, ...], run: EvidenceRun) -> tuple[str, ...]:
    """Replace model metric uncertainty with evidence-derived analogue limits."""
    data = _analogue_contract_data(run)
    if data is None:
        return values
    canonical: list[str] = []
    warning_text: list[str] = []
    for record in run.records:
        if not record.result:
            continue
        for key in ("warnings", "limitations"):
            items = record.result.get(key, ())
            if not isinstance(items, (list, tuple)):
                continue
            warning_text.extend(
                str(item.get("message", "")) if isinstance(item, Mapping) else str(item) for item in items
            )
    if any(re.search(r"(?:reconstruct|historical[- ]vintage)", item, re.I) for item in warning_text):
        canonical.append(
            "Historical market inputs are current-capture reconstructions, not historical-vintage evidence."
        )
    direction = data.get("direction_summary")
    if (
        isinstance(direction, Mapping)
        and direction.get("all_candidates_opposite_direction") is True
        and direction.get("match_count") == 0
    ):
        count = int(direction.get("candidate_count", 0) or 0)
        canonical.append(
            f"Signed direction is excluded from ranking; all {count} returned "
            "candidates have opposite signed direction."
        )
    canonical.extend(
        (
            "Candidate-specific narrative and causal context is unavailable from the "
            "bounded analogue evidence.",
            "Measured proximity does not establish a shared cause, sector, macro "
            "regime, business exposure, or outcome, and is not a forecast.",
        )
    )
    return tuple(dict.fromkeys(canonical))[:12]
