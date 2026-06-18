"""Typed application settings loaded from environment / .env.

Single source of truth for config. Import `get_settings()` anywhere instead of
reading os.environ directly — keeps secrets and paths in one validated place.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of this config/ directory.
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Runtime ---
    env: str = "dev"
    log_level: str = "INFO"
    data_dir: Path = ROOT_DIR / "data"
    log_dir: Path = ROOT_DIR / "logs"

    # --- Alpaca ---
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(default="", alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(default=True, alias="ALPACA_PAPER")

    # --- Storage ---
    # 'parquet' (local cache, default) or 'timescale' (TimescaleDB hypertable).
    storage_backend: str = Field(default="parquet", alias="STORAGE_BACKEND")
    timescale_dsn: str = Field(
        default="postgresql://quant:quant@localhost:5432/quant",
        alias="TIMESCALE_DSN",
    )

    def ensure_dirs(self) -> None:
        """Create runtime directories if missing."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
