"""Minimal .env support and config resolution. No external dependency."""
from __future__ import annotations

import os


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from `path` into os.environ without overriding existing keys.
    Lines that are blank, start with '#', or lack '=' are ignored; surrounding single or
    double quotes are stripped from the value."""
    if not os.path.exists(path):
        return
    with open(path, "r") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)


def load_default_dotenvs() -> None:
    """Load .env from the current directory and ~/.preflight/.env (CWD wins)."""
    for path in (".env", os.path.expanduser("~/.preflight/.env")):
        load_dotenv(path)
