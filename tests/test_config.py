"""Settings contract: defaults, env overrides, caching, dir creation.

Hermetic — every Settings() here passes _env_file=None so a developer's local
.env can't change the result, and env overrides go through monkeypatch."""
from __future__ import annotations

from config.settings import Settings, get_settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.env == "dev"
    assert s.storage_backend == "parquet"
    assert s.alpaca_paper is True
    assert s.alerts_enabled is True


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "timescale")
    monkeypatch.setenv("ALPACA_PAPER", "false")
    s = Settings(_env_file=None)
    assert s.storage_backend == "timescale"
    assert s.alpaca_paper is False


def test_get_settings_is_cached_singleton():
    assert get_settings() is get_settings()  # lru_cache


def test_ensure_dirs_creates_runtime_dirs(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d", log_dir=tmp_path / "l")
    assert not s.data_dir.exists()
    s.ensure_dirs()
    assert s.data_dir.is_dir() and s.log_dir.is_dir()
