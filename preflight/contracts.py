"""Shared contracts for the Preflight analyzer. No I/O, no LLM, no DB."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ---- Input contract (mirrors main-repo SQLQuery shape; map via adapter at integration) ----
class GeneratedSQL(BaseModel):
    query: str
    dialect: str = "generic"        # sqlglot dialect: duckdb, postgres, tsql, ...
    target: str = "generic"         # schema family label: omop, epic, pcornet, generic


# ---- Enums ----
class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RuntimeCategory(str, Enum):
    FAST = "FAST"
    MODERATE = "MODERATE"
    HEAVY = "HEAVY"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


# ---- §1 Study Summary ----
class StudySummary(BaseModel):
    execution_target: str
    tables: List[str] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list)
    query_shape: str = ""


# ---- §2 Lineage ----
class LineageNode(BaseModel):
    table: str
    category: str = "unknown"
    volume: str = "unknown"
    est_rows: Optional[int] = None


class LineageEdge(BaseModel):
    source: str
    target: str
    kind: str = "join"              # join | cte | filter
    cardinality_transition: str = ""  # e.g. "1:N (fan-out)"


class Lineage(BaseModel):
    nodes: List[LineageNode] = Field(default_factory=list)
    edges: List[LineageEdge] = Field(default_factory=list)
    filters: List[str] = Field(default_factory=list)


# ---- §3 Scale ----
class StageEstimate(BaseModel):
    name: str
    est_rows: int


class ScaleEstimate(BaseModel):
    patients: Optional[int] = None
    encounters: Optional[int] = None
    events: Optional[int] = None
    intermediate_records: Optional[int] = None
    output_records: Optional[int] = None
    per_stage: List[StageEstimate] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW


# ---- §5 Bottlenecks ----
class Bottleneck(BaseModel):
    component: str
    reason: str
    contribution_pct: int


# ---- §6 Optimizations ----
class Optimization(BaseModel):
    action: str
    rationale: str
    expected_benefit: str = ""


# ---- §7 Query plan (live connector only) ----
class PlanNode(BaseModel):
    op: str
    table: Optional[str] = None
    estimated_rows: Optional[int] = None
    scan_type: Optional[str] = None
    join_type: Optional[str] = None
    index_used: Optional[bool] = None


class QueryPlan(BaseModel):
    nodes: List[PlanNode] = Field(default_factory=list)
    total_estimated_rows: Optional[int] = None
    missing_index_hints: List[str] = Field(default_factory=list)


# ---- The full report ----
class PreflightReport(BaseModel):
    summary: StudySummary
    lineage: Lineage = Field(default_factory=Lineage)
    scale: ScaleEstimate
    risk_level: RiskLevel
    risk_reasons: List[str] = Field(default_factory=list)
    bottlenecks: List[Bottleneck] = Field(default_factory=list)
    optimizations: List[Optimization] = Field(default_factory=list)
    query_plan: Optional[QueryPlan] = None
    runtime_category: RuntimeCategory
    confidence: Confidence
