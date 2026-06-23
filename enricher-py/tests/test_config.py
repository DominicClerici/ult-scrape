from pathlib import Path

from app.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.output_dir == Path("../output")
    assert s.max_concurrency == 2
    assert s.max_attempts == 5
    assert "lesson" in s.reject_keyword_list()


def test_env_override(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENCY", "7")
    monkeypatch.setenv("REJECT_KEYWORDS", "live, remix ,cover")
    s = Settings(_env_file=None)
    assert s.max_concurrency == 7
    assert s.reject_keyword_list() == ("live", "remix", "cover")
