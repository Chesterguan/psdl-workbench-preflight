"""Render a PreflightReport to text or JSON."""
from __future__ import annotations

import json

from preflight.contracts import PreflightReport


def render_json(report: PreflightReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)


def _fmt(n):
    return f"{n:,}" if isinstance(n, int) else "n/a"


def render_text(report: PreflightReport) -> str:
    s = report.summary
    lines = []
    lines.append("=== STUDY SUMMARY ===")
    lines.append(f"Execution Target: {s.execution_target}")
    lines.append(f"Tables: {', '.join(s.tables) or 'none'}")
    lines.append(f"Domains: {', '.join(s.domains) or 'none'}")
    lines.append(f"Query Shape: {s.query_shape}")

    lines.append("\n=== DATA LINEAGE ===")
    for n in report.lineage.nodes:
        rows = _fmt(n.est_rows) if n.est_rows is not None else "unknown"
        lines.append(f"  {n.table} [{n.category}, vol={n.volume}, ~{rows} rows]")
    for e in report.lineage.edges:
        lines.append(f"  {e.source} -> {e.target}  ({e.cardinality_transition})")
    if report.lineage.filters:
        lines.append(f"  filters: {', '.join(report.lineage.filters)}")

    sc = report.scale
    lines.append("\n=== SCALE ESTIMATION ===")
    lines.append(f"Patients: {_fmt(sc.patients)}")
    lines.append(f"Encounters: {_fmt(sc.encounters)}")
    lines.append(f"Events: {_fmt(sc.events)}")
    lines.append(f"Output Records: {_fmt(sc.output_records)}")
    lines.append(f"Runtime Category: {report.runtime_category.value}")
    lines.append(f"Scale Confidence: {sc.confidence.value}")

    lines.append("\n=== EXECUTION RISK ===")
    lines.append(f"Risk: {report.risk_level.value}")
    for r in report.risk_reasons:
        lines.append(f"  - {r}")

    lines.append("\n=== BOTTLENECK ANALYSIS ===")
    for b in report.bottlenecks:
        lines.append(f"  {b.component}: {b.contribution_pct}%  ({b.reason})")

    lines.append("\n=== OPTIMIZATION RECOMMENDATIONS ===")
    if not report.optimizations:
        lines.append("  (none — query is already lean)")
    for o in report.optimizations:
        lines.append(f"  * {o.action}")
        lines.append(f"      why: {o.rationale}")
        if o.expected_benefit:
            lines.append(f"      benefit: {o.expected_benefit}")

    if report.query_plan is not None:
        lines.append("\n=== QUERY PLAN (live) ===")
        qp = report.query_plan
        lines.append(f"Total Estimated Rows: {_fmt(qp.total_estimated_rows)}")
        for n in qp.nodes:
            bits = [n.op]
            if n.table:
                bits.append(f"table={n.table}")
            if n.join_type:
                bits.append(f"join={n.join_type}")
            if n.estimated_rows is not None:
                bits.append(f"~{_fmt(n.estimated_rows)} rows")
            if n.index_used:
                bits.append("index=yes")
            lines.append("  " + " ".join(bits))
        if qp.missing_index_hints:
            lines.append("  Missing Index Hints:")
            for hint in qp.missing_index_hints:
                lines.append(f"    - {hint}")

    lines.append("\n=== CONFIDENCE ===")
    lines.append(f"Overall Confidence: {report.confidence.value}")
    return "\n".join(lines)
