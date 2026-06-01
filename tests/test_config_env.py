import os

from preflight.config import load_dotenv


def test_load_dotenv_populates_environ_without_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# comment\n'
        'PREFLIGHT_DIALECT=tsql\n'
        'PREFLIGHT_CATALOG="clarity"\n'
        "PREFLIGHT_PG_DSN='postgresql://u@h/db'\n"
        "\n"
    )
    monkeypatch.delenv("PREFLIGHT_DIALECT", raising=False)
    monkeypatch.setenv("PREFLIGHT_CATALOG", "omop")  # pre-existing wins
    load_dotenv(str(env))
    assert os.environ["PREFLIGHT_DIALECT"] == "tsql"          # set from file
    assert os.environ["PREFLIGHT_CATALOG"] == "omop"          # not overridden
    assert os.environ["PREFLIGHT_PG_DSN"] == "postgresql://u@h/db"  # quotes stripped


def test_load_dotenv_missing_file_is_noop(tmp_path):
    load_dotenv(str(tmp_path / "nope.env"))  # must not raise
