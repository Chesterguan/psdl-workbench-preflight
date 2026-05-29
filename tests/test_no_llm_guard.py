import sys

import preflight  # noqa: F401
import preflight.pipeline  # noqa: F401


def test_no_llm_or_http_client_imported():
    forbidden = {"openai", "anthropic", "requests", "httpx", "urllib3"}
    loaded = set(sys.modules)
    leaked = forbidden & loaded
    assert not leaked, f"core path must not import LLM/network clients, found: {leaked}"
