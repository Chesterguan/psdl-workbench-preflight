from preflight.contracts import (
    GeneratedSQL, RiskLevel, Confidence, RuntimeCategory,
    PreflightReport, StudySummary, ScaleEstimate,
)


def test_generated_sql_defaults():
    g = GeneratedSQL(query="SELECT 1", dialect="duckdb")
    assert g.target == "generic"
    assert g.dialect == "duckdb"


def test_enums_have_expected_members():
    assert RiskLevel.CRITICAL.value == "CRITICAL"
    assert set(RuntimeCategory) == {
        RuntimeCategory.FAST, RuntimeCategory.MODERATE, RuntimeCategory.HEAVY,
        RuntimeCategory.EXTREME, RuntimeCategory.UNKNOWN,
    }
    assert set(Confidence) == {Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH}


def test_report_is_json_serializable():
    report = PreflightReport(
        summary=StudySummary(execution_target="duckdb", tables=["person"],
                             domains=["demographics"], query_shape="1 join, 0 CTEs"),
        scale=ScaleEstimate(output_records=100, confidence=Confidence.MEDIUM),
        risk_level=RiskLevel.LOW,
        runtime_category=RuntimeCategory.FAST,
        confidence=Confidence.MEDIUM,
    )
    dumped = report.model_dump(mode="json")
    assert dumped["risk_level"] == "LOW"
    assert dumped["summary"]["execution_target"] == "duckdb"
