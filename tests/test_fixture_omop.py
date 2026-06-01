from fixtures.build_omop import build_omop_duckdb


def test_fixture_has_expected_tables_and_rows():
    con = build_omop_duckdb()
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"person", "visit_occurrence", "measurement"} <= tables
    n = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
    assert n > 0
