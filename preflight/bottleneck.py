"""§5 Bottleneck analysis: rank tables by estimated row contribution."""
from __future__ import annotations

from typing import List

from preflight.catalog.loader import Catalog
from preflight.contracts import Bottleneck
from preflight.parse.sql import ParsedSQL


def find_bottlenecks(parsed: ParsedSQL, catalog: Catalog) -> List[Bottleneck]:
    rows = {t: catalog.profile(t).effective_rows() for t in parsed.base_tables}
    total = sum(rows.values()) or 1
    ranked = sorted(rows.items(), key=lambda kv: kv[1], reverse=True)

    result = []
    for table, r in ranked:
        prof = catalog.profile(table)
        pct = round(100 * r / total)
        reason = f"{prof.category.replace('_', ' ')} table, volume={prof.volume}"
        result.append(Bottleneck(component=table, reason=reason, contribution_pct=pct))

    drift = 100 - sum(b.contribution_pct for b in result)
    if result and drift != 0:
        result[0].contribution_pct += drift
    return result
