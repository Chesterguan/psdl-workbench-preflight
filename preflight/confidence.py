"""§8 Confidence score from catalog coverage and live-plan availability."""
from __future__ import annotations

from preflight.contracts import Confidence


def score_confidence(known_ratio: float, has_plan: bool) -> Confidence:
    good_catalog = known_ratio >= 0.7
    if has_plan and good_catalog:
        return Confidence.HIGH
    if has_plan or good_catalog:
        return Confidence.MEDIUM
    return Confidence.LOW
