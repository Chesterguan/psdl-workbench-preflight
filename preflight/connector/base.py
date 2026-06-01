"""Live connector interface. Implementations issue EXPLAIN-class statements only."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol

from preflight.contracts import PlanNode, QueryPlan


@dataclass
class PlanFacts:
    nodes: List[PlanNode] = field(default_factory=list)
    total_estimated_rows: Optional[int] = None
    missing_index_hints: List[str] = field(default_factory=list)

    def to_query_plan(self) -> QueryPlan:
        return QueryPlan(
            nodes=self.nodes,
            total_estimated_rows=self.total_estimated_rows,
            missing_index_hints=self.missing_index_hints,
        )


class Connector(Protocol):
    def analyze(self, sql: str) -> PlanFacts:
        """Return plan facts WITHOUT executing the query."""
        ...
