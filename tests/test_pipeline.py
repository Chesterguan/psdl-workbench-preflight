from preflight.contracts import GeneratedSQL, Confidence, RiskLevel
from preflight.catalog.loader import load_catalog
from preflight.connector.duckdb_connector import DuckDBConnector
from preflight.pipeline import run_preflight
from fixtures.build_omop import build_omop_duckdb, load_query


def test_offline_pipeline_produces_all_sections():
    sql = GeneratedSQL(query=load_query("crrt_flowsheet"), dialect="duckdb", target="omop")
    cat = load_catalog("omop")
    report = run_preflight(sql, cat)

    assert report.summary.execution_target == "omop"
    assert "measurement" in report.summary.tables
    assert report.scale.output_records is not None
    assert report.risk_level in set(RiskLevel)
    assert report.bottlenecks and report.bottlenecks[0].component == "measurement"
    assert report.optimizations
    assert report.query_plan is None
    assert report.confidence == Confidence.MEDIUM


def test_pipeline_with_connector_populates_plan_and_high_confidence():
    con = build_omop_duckdb()
    sql = GeneratedSQL(query=load_query("crrt_flowsheet"), dialect="duckdb", target="omop")
    cat = load_catalog("omop")
    report = run_preflight(sql, cat, connector=DuckDBConnector(con))

    assert report.query_plan is not None
    assert report.confidence == Confidence.HIGH
    assert report.scale.confidence == Confidence.HIGH
