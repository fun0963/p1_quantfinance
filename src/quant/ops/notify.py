"""Alerting — the ops layer's notification channel.

An automated trading system that can't tell you when it fails is worse than none:
a dead scheduled job, a blocked order, a reconciliation mismatch, or a crash must
reach a human. Every such event routes through a `Notifier`. Telegram is used when
configured (a CRITICAL alert can buzz your phone); otherwise alerts fall back to
the structured log so nothing is ever silently dropped.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from config import get_settings
from quant.utils import get_logger

log = get_logger(__name__)


class Notifier(ABC):
    """Deliver alerts. `send` must NEVER raise — alerting can't crash the caller."""

    @abstractmethod
    def send(self, level: str, title: str, message: str = "") -> bool:
        """Send an alert (level: INFO | WARN | CRITICAL). Returns True on success."""

    def info(self, title: str, message: str = "") -> bool:
        return self.send("INFO", title, message)

    def warn(self, title: str, message: str = "") -> bool:
        return self.send("WARN", title, message)

    def critical(self, title: str, message: str = "") -> bool:
        return self.send("CRITICAL", title, message)


class LogNotifier(Notifier):
    """Always-available fallback — writes the alert to the structured log."""

    def send(self, level: str, title: str, message: str = "") -> bool:
        lvl = level.upper()
        text = f"[ALERT {lvl}] {title}" + (f" — {message}" if message else "")
        (log.critical if lvl == "CRITICAL" else log.warning if lvl == "WARN" else log.info)(text)
        return True


class NullNotifier(Notifier):
    """Discards everything (alerts disabled)."""

    def send(self, level: str, title: str, message: str = "") -> bool:
        return True


class TelegramNotifier(Notifier):
    """Push alerts to a Telegram chat; also logs, and falls back cleanly on failure."""

    _EMOJI = {"INFO": "ℹ️", "WARN": "⚠️", "CRITICAL": "\U0001f6a8"}

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._log = LogNotifier()

    def send(self, level: str, title: str, message: str = "") -> bool:
        self._log.send(level, title, message)  # always keep a log copy
        text = f"{self._EMOJI.get(level.upper(), '')} <b>{title}</b>" + (f"\n{message}" if message else "")
        try:
            import requests

            resp = requests.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            return bool(resp.ok)
        except Exception as exc:  # noqa: BLE001 - alerting must never crash the caller
            log.warning(f"telegram alert failed: {exc}")
            return False


def get_notifier() -> Notifier:
    """Config-driven notifier: Telegram when enabled + configured, else log-only."""
    s = get_settings()
    if not s.alerts_enabled:
        return NullNotifier()
    if s.telegram_bot_token and s.telegram_chat_id:
        return TelegramNotifier(s.telegram_bot_token, s.telegram_chat_id)
    return LogNotifier()
