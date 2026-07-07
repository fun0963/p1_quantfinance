"""Ops layer: alerting (notify), reconciliation, and the daily report."""
from __future__ import annotations

import sys
from datetime import date

from quant.execution.base import Position
from quant.execution.journal import TradeJournal
from quant.execution.live_runner import LiveDecision
from quant.ops.notify import (
    LogNotifier,
    NullNotifier,
    TelegramNotifier,
    get_notifier,
)
from quant.ops.reconcile import reconcile
from quant.ops.report import daily_report


class _Brk:
    """Duck-typed broker for reconcile/report tests."""
    def __init__(self, positions, open_orders=None):
        self._pos = positions
        self._orders = open_orders or []

    def get_positions(self):
        return self._pos

    def get_open_orders(self, symbol=None):
        return self._orders

    def get_cash(self):
        return 1000.0


def _journal_with_buy(tmp_path, symbol="SPY"):
    tj = TradeJournal(db_path=tmp_path / "j.db")
    dec = LiveDecision(ts="2024-01-02", symbol=symbol, action="buy", price=100.0,
                       qty=10, order_id="ord-1", dry_run=False)
    tj.record_live_decision(dec, strategy="momentum")
    return tj


# --- notify ------------------------------------------------------------------
def test_log_and_null_notifiers_never_raise():
    assert LogNotifier().critical("boom", "detail") is True
    assert NullNotifier().info("x") is True


def _fake_settings(**kw):
    base = {"alerts_enabled": True, "telegram_bot_token": "", "telegram_chat_id": ""}
    base.update(kw)
    return type("S", (), base)()


def test_get_notifier_selects_by_config(monkeypatch):
    import quant.ops.notify as notify
    monkeypatch.setattr(notify, "get_settings", lambda: _fake_settings())
    assert isinstance(get_notifier(), LogNotifier)                      # no telegram -> log
    monkeypatch.setattr(notify, "get_settings", lambda: _fake_settings(alerts_enabled=False))
    assert isinstance(get_notifier(), NullNotifier)                     # disabled -> null
    monkeypatch.setattr(notify, "get_settings",
                        lambda: _fake_settings(telegram_bot_token="t", telegram_chat_id="c"))
    assert isinstance(get_notifier(), TelegramNotifier)                 # configured -> telegram


def test_telegram_send_never_crashes_on_network_error(monkeypatch):
    class _R:
        @staticmethod
        def post(*a, **k):
            raise RuntimeError("network down")
    monkeypatch.setitem(sys.modules, "requests", _R)
    assert TelegramNotifier("t", "c").send("CRITICAL", "boom") is False   # returns False, no raise


# --- reconcile ---------------------------------------------------------------
def test_reconcile_clean(tmp_path):
    brk = _Brk([Position("SPY", 10, 100.0)],
               open_orders=[{"symbol": "SPY", "side": "sell", "qty": 10}])
    with _journal_with_buy(tmp_path) as tj:
        rep = reconcile(brk, tj)
    assert rep.ok and rep.issues == []


def test_reconcile_untracked_position_is_critical(tmp_path):
    brk = _Brk([Position("TSLA", 5, 200.0)])          # journal only ever bought SPY
    with _journal_with_buy(tmp_path, "SPY") as tj:
        rep = reconcile(brk, tj)
    assert not rep.ok
    assert any(i.kind == "untracked_position" and i.severity == "CRITICAL" for i in rep.issues)


def test_reconcile_unprotected_position_is_warn_not_fatal(tmp_path):
    brk = _Brk([Position("SPY", 10, 100.0)], open_orders=[])   # held, no stop/OCO
    with _journal_with_buy(tmp_path) as tj:
        rep = reconcile(brk, tj)
    assert rep.ok                                              # WARN doesn't flip ok
    assert any(i.kind == "unprotected_position" for i in rep.issues)


def test_reconcile_orphan_order_is_warn(tmp_path):
    brk = _Brk([], open_orders=[{"symbol": "QQQ", "side": "sell", "qty": 1}])
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        rep = reconcile(brk, tj)
    assert rep.ok
    assert any(i.kind == "orphan_order" for i in rep.issues)


# --- report ------------------------------------------------------------------
def test_daily_report_has_sections(tmp_path):
    brk = _Brk([Position("SPY", 10, 100.0)],
               open_orders=[{"symbol": "SPY", "side": "sell", "qty": 10}])
    with _journal_with_buy(tmp_path) as tj:
        text = daily_report(brk, tj, on=date(2024, 1, 2))
    assert "Daily report" in text and "SPY" in text and "reconcile" in text
