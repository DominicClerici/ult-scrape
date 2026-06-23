from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ug_email: str = ""
    ug_password: str = ""
    ug_proxy: str = ""

    output_dir: Path = Path("./output")
    db_path: Path = Path("./scraper.db")
    profile_dir: Path = Path("./camoufox-profile")
    fingerprint_path: Path = Path("./camoufox-fingerprint.json")

    headless: bool = False
    max_attempts: int = 3
    backoff_base_seconds: float = 30.0
    inter_job_delay_min: float = 5.0
    inter_job_delay_max: float = 20.0
    cloudflare_timeout_ms: int = 120_000
    capture_window_ms: int = 10_000
    poll_interval_seconds: float = 5.0

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_key: str = ""


def get_settings() -> Settings:
    return Settings()
