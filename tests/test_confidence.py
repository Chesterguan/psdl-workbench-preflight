from preflight.contracts import Confidence
from preflight.confidence import score_confidence


def test_high_confidence_with_plan_and_full_catalog():
    assert score_confidence(known_ratio=1.0, has_plan=True) == Confidence.HIGH


def test_medium_confidence_full_catalog_no_plan():
    assert score_confidence(known_ratio=1.0, has_plan=False) == Confidence.MEDIUM


def test_low_confidence_sparse_catalog():
    assert score_confidence(known_ratio=0.2, has_plan=False) == Confidence.LOW
    assert score_confidence(known_ratio=0.2, has_plan=True) == Confidence.MEDIUM
