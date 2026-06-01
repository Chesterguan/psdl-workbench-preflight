import pytest

from preflight.parse.sql import parse_sql, PreflightParseError
from fixtures.build_omop import load_query


def test_tsql_parses_epic_fixture():
    sql = load_query("epic_or_cases")
    parsed = parse_sql(sql, dialect="tsql")
    tables = set(parsed.base_tables)
    assert {"PATIENT_ENCOUNTER_DTL", "ALL_PATIENTS", "ORDER_PROCEDURE_DTL",
            "OR_CASE_KEY_XREF"} <= tables
    assert parsed.join_count >= 3


def test_generic_dialect_rejects_epic_fixture():
    sql = load_query("epic_or_cases")
    with pytest.raises(PreflightParseError):
        parse_sql(sql, dialect="generic")
