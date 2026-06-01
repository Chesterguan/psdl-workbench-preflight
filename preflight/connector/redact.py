"""Redact literal values from DB plan predicates before they reach reports (PHI safety)."""
from __future__ import annotations

import re

_STRING_LIT_RE = re.compile(r"'(?:[^']|'')*'")
_NUM_LIT_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")


def redact_literals(predicate: str) -> str:
    """Replace string/numeric literals in a predicate with '?'. Identifiers that merely
    contain digits (e.g. order_results_2) are preserved."""
    redacted = _STRING_LIT_RE.sub("?", predicate)
    redacted = _NUM_LIT_RE.sub("?", redacted)
    return redacted
