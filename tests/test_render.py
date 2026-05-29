import json

from preflight.contracts import GeneratedSQL
from preflight.catalog.loader import load_catalog
from preflight.pipeline import run_preflight
from preflight.report.render import render_text, render_json
from fixtures.build_omop import load_query


def _report():
    sql = GeneratedSQL(query=load_query("crrt_flowsheet"), dialect="duckdb", target="omop")
    return run_preflight(sql, load_catalog("omop"))


def test_render_text_includes_all_sections():
    out = render_text(_report())
    for header in ["STUDY SUMMARY", "DATA LINEAGE", "SCALE", "RISK",
                   "BOTTLENECK", "OPTIMIZATION", "CONFIDENCE"]:
        assert header in out.upper()


def test_render_json_roundtrips():
    out = render_json(_report())
    data = json.loads(out)
    assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert data["summary"]["execution_target"] == "omop"
