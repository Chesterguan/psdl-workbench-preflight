from preflight.connector.redact import redact_literals


def test_redacts_string_and_numeric_literals():
    assert redact_literals("(person_id = 999000123)") == "(person_id = ?)"
    assert redact_literals("(pat_mrn = '1234567')") == "(pat_mrn = ?)"


def test_preserves_identifiers_with_digits():
    out = redact_literals("(order_results_2 = 42)")
    assert "order_results_2" in out and "42" not in out


def test_postgres_connector_reexports_redactor():
    # Existing test imports `_redact_literals` from postgres_connector — keep it working.
    from preflight.connector.postgres_connector import _redact_literals
    assert _redact_literals("(x = 5)") == "(x = ?)"
