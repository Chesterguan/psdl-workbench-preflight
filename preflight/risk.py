"""§4 Execution risk. Deterministic rule-based scoring."""
from __future__ import annotations

from typing import List, Tuple

from preflight.catalog.loader import Catalog
from preflight.contracts import RiskLevel, ScaleEstimate
from preflight.parse.sql import ParsedSQL

_RISK_SCORE = {"very_high": 3, "high": 2, "medium": 1, "low": 0, "unknown": 1}
_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


def assess_risk(parsed: ParsedSQL, catalog: Catalog,
                scale: ScaleEstimate) -> Tuple[RiskLevel, List[str]]:
    score = 0
    reasons: List[str] = []

    for t in parsed.base_tables:
        prof = catalog.profile(t)
        pts = _RISK_SCORE.get(prof.risk, 1)
        if pts >= 2:
            score += pts
            reasons.append(f"{prof.risk.replace('_', ' ').title()} risk table: {t}")

    out = scale.output_records or 0
    if out >= 1_000_000_000:
        score += 3
        reasons.append(f"Very large output cardinality (~{out:,} rows)")
    elif out >= 1_000_000:
        score += 2
        reasons.append(f"Large output cardinality (~{out:,} rows)")

    if parsed.join_count >= 4:
        score += 2
        reasons.append(f"Deep join chain ({parsed.join_count} joins)")
    elif parsed.join_count >= 2:
        score += 1

    if not parsed.filter_columns:
        for t in parsed.base_tables:
            if catalog.profile(t).category == "clinical_event":
                score += 3
                reasons.append(f"Unfiltered scan of clinical event table: {t} (no filter)")
                break

    if score >= 6:
        level = RiskLevel.CRITICAL
    elif score >= 4:
        level = RiskLevel.HIGH
    elif score >= 2:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    if not reasons:
        reasons.append("Small, well-filtered query over low-risk tables")
    return level, reasons
