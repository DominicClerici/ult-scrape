from pathlib import Path

from app import __version__
from app.config import get_settings


def test_version_is_string():
    assert __version__ == "0.1.0"


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("UG_EMAIL", "a@b.com")
    monkeypatch.setenv("UG_PASSWORD", "secret")
    monkeypatch.setenv("HEADLESS", "false")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("OUTPUT_DIR", "/tmp/out")
    s = get_settings()
    assert s.ug_email == "a@b.com"
    assert s.ug_password == "secret"
    assert s.headless is False
    assert s.max_attempts == 5
    assert s.output_dir == Path("/tmp/out")


def test_defaults():
    s = get_settings()
    assert s.api_host == "127.0.0.1"
    assert s.max_attempts == 3
    assert s.headless is False
