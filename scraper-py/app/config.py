from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ug_email: str = ""
    ug_password: str = ""
    ug_proxy: str = ""
    ug_proxy_username: str = ""
    ug_proxy_password: str = ""

    output_dir: Path = Path("../output")
    db_path: Path = Path("./scraper.db")
    profile_dir: Path = Path("./camoufox-profile")
    fingerprint_path: Path = Path("./camoufox-fingerprint.json")

    headless: bool = False
    max_attempts: int = 3
    backoff_base_seconds: float = 30.0
    inter_job_delay_min: float = 5.0
    inter_job_delay_max: float = 20.0
    cloudflare_timeout_ms: int = 120_000
    capture_window_ms: int = 30_000
    poll_interval_seconds: float = 5.0
    # Auto-pause the worker after this many consecutive non-successful jobs.
    circuit_breaker_threshold: int = 5
    # Cool-off applied after a 403/429 rate-limit before the next job.
    rate_limit_delay_seconds: float = 300.0
    # Delay before re-attempting a job whose session expired (no retry consumed).
    session_expiry_backoff_seconds: float = 60.0

    discovery_sort_orders: str = "date_desc,artistname_asc,artistname_desc,songname_asc"
    discovery_facet_ladder: str = "genres,decade,tonality"
    discovery_page_delay_min: float = 2.0
    discovery_page_delay_max: float = 6.0
    discovery_max_slices: int = 0
    discovery_target_cap: int = 0
    discovery_request_timeout_ms: int = 30_000
    discovery_untagged_sweep: bool = True

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_key: str = ""


def get_settings() -> Settings:
    return Settings()
