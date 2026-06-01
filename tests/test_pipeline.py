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


class _BoomConnector:
    """A connector whose live analysis fails (e.g. SHOWPLAN error mid-ETL)."""
    def analyze(self, sql):
        raise RuntimeError("conversion failed for value 'SECRET123'")


def test_connector_failure_degrades_gracefully_without_crashing():
    sql = GeneratedSQL(
        query="SELECT person_id FROM measurement WHERE measurement_concept_id = 3016723",
        dialect="duckdb", target="omop")
    report = run_preflight(sql, load_catalog("omop"), connector=_BoomConnector())

    # No crash: the report is still produced, just without a live plan.
    assert report.query_plan is None
    assert report.confidence != Confidence.HIGH          # treated as offline
    assert report.notes and any("plan unavailable" in n.lower() for n in report.notes)
    # The connector error text is redacted so a literal in the message can't leak.
    assert not any("SECRET123" in n for n in report.notes)
