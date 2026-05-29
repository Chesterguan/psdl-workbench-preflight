from preflight.catalog.loader import load_catalog, TableProfile


def test_load_omop_and_lookup_known_table():
    cat = load_catalog("omop")
    prof = cat.profile("measurement")
    assert isinstance(prof, TableProfile)
    assert prof.category == "clinical_event"
    assert prof.volume == "huge"
    assert prof.row_estimate and prof.row_estimate > 1_000_000
    assert cat.is_known("measurement") is True


def test_unknown_table_returns_generic_fallback():
    cat = load_catalog("omop")
    prof = cat.profile("some_random_table")
    assert prof.volume == "unknown"
    assert prof.risk == "unknown"
    assert cat.is_known("some_random_table") is False


def test_join_fanout_lookup():
    cat = load_catalog("omop")
    assert cat.join_fanout("person", "measurement") == "high"
    assert cat.join_fanout("person", "nonexistent") == "unknown"
