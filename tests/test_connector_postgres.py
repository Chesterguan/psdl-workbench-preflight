import os
import pytest

from preflight.connector.postgres_connector import parse_pg_plan, PostgresConnector

PG_PLAN = [{
    "Plan": {
        "Node Type": "Hash Join", "Join Type": "Inner", "Plan Rows": 4200,
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "measurement",
             "Plan Rows": 5000, "Filter": "(measurement_concept_id = 3016723)"},
            {"Node Type": "Index Scan", "Relation Name": "person",
             "Index Name": "person_pkey", "Plan Rows": 100},
        ],
    }
}]


def test_parse_pg_plan_extracts_facts():
    facts = parse_pg_plan(PG_PLAN)
    assert facts.total_estimated_rows == 4200
    ops = {n.op for n in facts.nodes}
    assert "Hash Join" in ops and "Seq Scan" in ops
    seq = next(n for n in facts.nodes if n.op == "Seq Scan")
    assert seq.table == "measurement"
    assert seq.estimated_rows == 5000
    idx = next(n for n in facts.nodes if n.op == "Index Scan")
    assert idx.index_used is True


def test_seq_scan_on_large_table_emits_missing_index_hint():
    facts = parse_pg_plan(PG_PLAN)
    assert any("measurement" in h for h in facts.missing_index_hints)


def test_seq_scan_hint_redacts_numeric_literal():
    # F1: a plan Filter literal (could be an MRN/patient_id) must NOT reach the hint.
    facts = parse_pg_plan(PG_PLAN)
    joined = " ".join(facts.missing_index_hints)
    assert "3016723" not in joined               # literal value redacted
    assert "measurement_concept_id" in joined    # column kept for index advice
    assert "?" in joined                          # redaction placeholder present


def test_seq_scan_hint_redacts_string_and_date_literals():
    plan = [{"Plan": {
        "Node Type": "Seq Scan", "Relation Name": "patient", "Plan Rows": 5000,
        "Filter": "((pat_mrn = '1234567') AND (birth_date = '1950-02-03'))",
    }}]
    joined = " ".join(parse_pg_plan(plan).missing_index_hints)
    assert "1234567" not in joined and "1950-02-03" not in joined  # PHI literals gone
    assert "pat_mrn" in joined and "birth_date" in joined          # columns kept


def test_redact_literals_preserves_identifiers_with_digits():
    from preflight.connector.postgres_connector import _redact_literals
    out = _redact_literals("(order_results_2 = 42)")
    assert "order_results_2" in out   # trailing digit in identifier preserved
    assert "42" not in out            # standalone numeric literal redacted


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_PG_DSN"),
                    reason="set PREFLIGHT_PG_DSN to run live Postgres test")
def test_live_postgres_explain():
    conn = PostgresConnector(os.environ["PREFLIGHT_PG_DSN"])
    facts = conn.analyze("SELECT 1")
    assert facts is not None
