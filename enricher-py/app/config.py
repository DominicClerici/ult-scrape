from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    output_dir: Path = Path("../output")
    enricher_db: Path = Path("./enricher.db")

    max_concurrency: int = 2
    search_results: int = 5

    min_duration_s: int = 60
    confidence_threshold: float = 0.5
    reject_keywords: str = (
        "lesson,tutorial,how to play,cover,karaoke,backing track,"
        "instrumental,live,remix,8-bit,8 bit,reaction"
    )

    ytdlp_format: str = "bestaudio"

    max_attempts: int = 5
    backoff_base_seconds: float = 30.0
    rate_limit_min_interval_s: float = 1.0

    def reject_keyword_list(self) -> tuple[str, ...]:
        return tuple(
            k.strip().lower() for k in self.reject_keywords.split(",") if k.strip()
        )


def get_settings() -> Settings:
    return Settings()
