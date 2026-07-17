"""Scheduling — run the live step automatically on a cron schedule.

`live_and_journal()` is the reusable unit of work (load bars -> decide -> journal),
shared by the `quant live` CLI and the scheduler. `run_schedule()` fires it on
weekdays at a set time via APScheduler (a blocking, in-process loop).

For reboot-survival on a personal machine, the OS scheduler (Windows Task
Scheduler) calling `quant live --execute` once a day is more robust than an
always-on process — see docs/SCHEDULING.md. This module is the always-on option.

Safety: `dry_run=True` by default — the scheduler computes & journals decisions
without submitting until explicitly run with execute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from quant.execution.journal import TradeJournal
from quant.execution.live_runner import LiveDecision, run_live_step
from quant.ops.health import record_heartbeat
from quant.ops.notify import get_notifier
from quant.ops.oms import OMS
from quant.risk import BracketConfig, FixedFractionRisk, RiskGate, RiskLimits
from quant.utils import get_logger

log = get_logger(__name__)


@dataclass
class LiveConfig:
    """Everything needed to evaluate one symbol/strategy in the live pipeline."""
    symbol: str
    strategy: str
    params: dict = field(default_factory=dict)
    start: str = "2023-01-01"
    timeframe: str = "1d"
    broker: str = "alpaca"           # alpaca | paper
    fraction: float = 0.95
    max_position_notional: float = 0.0
    max_daily_loss: float = 0.0      # daily-loss breaker (0 = off); now wired into live
    stop_loss: float = 0.0
    take_profit: float = 0.0
    mode: str = "target"
    max_bar_age_days: int = 4        # refuse to act on a bar older than this (safety net)
    max_staleness_days: int = 1      # re-download the cache if its newest bar is older than this
    # Intraday (1min/1h) equivalents, in BAR units - used instead of the day
    # fields when the timeframe is intraday (a day tolerance is meaningless there).
    max_bar_age_bars: int = 5
    max_staleness_bars: int = 2


def _build_broker(cfg: LiveConfig):
    if cfg.broker == "alpaca":
        from quant.execution.alpaca_broker import AlpacaBroker
        return AlpacaBroker()
    from quant.execution import PaperBroker
    return PaperBroker()


def live_and_journal(
    cfg: LiveConfig, *, dry_run: bool = True, journal: TradeJournal | None = None,
    data=None, notifier=None,
) -> LiveDecision:
    """Run one live decision for `cfg` and record it. The schedulable unit of work.

    `data` may be passed pre-loaded (skips the fetch) — handy for tests/reuse.
    `notifier` defaults to the configured one; inject NullNotifier in tests.
    """
    from quant.strategies.registry import get_strategy_cls

    intraday = _is_intraday(cfg.timeframe)
    if data is None:
        from quant.data.feeds import get_feed
        from quant.data.loaders import load_bars
        start_dt = datetime.fromisoformat(cfg.start).replace(tzinfo=UTC)
        # Live: re-download if the cache tail is stale, so we never decide on old
        # bars. Intraday measures staleness in bars, daily in days.
        data = load_bars(cfg.symbol, get_feed(cfg.timeframe), start=start_dt,
                         timeframe=cfg.timeframe,
                         max_staleness_days=None if intraday else cfg.max_staleness_days,
                         max_staleness_bars=cfg.max_staleness_bars if intraday else None)
    strat = get_strategy_cls(cfg.strategy)(**cfg.params)
    broker = _build_broker(cfg)
    notifier = notifier or get_notifier()

    gate = RiskGate(RiskLimits(enabled=True, max_position_notional=cfg.max_position_notional,
                               max_daily_loss=cfg.max_daily_loss))
    # Feed today's P&L so the daily-loss breaker can actually trip in the live path.
    if hasattr(broker, "day_pnl"):
        try:
            gate.report_daily_pnl(broker.day_pnl())
        except Exception as exc:  # noqa: BLE001 - P&L read is best-effort
            log.warning(f"[schedule] could not read day P&L for {cfg.symbol}: {exc}")

    own = journal or TradeJournal()
    oms = OMS(own)
    dec: LiveDecision | None = None
    try:
        # OMS: advance the state of any orders placed on a previous run (async fills
        # at the broker only become FILLED here). Best-effort — never blocks trading.
        _oms_sync(oms, broker)

        # Fail-safe: reconcile the real book before placing any live order. A CRITICAL
        # mismatch means the system is out of sync — halt and alert, don't trade (P0 #6).
        if not dry_run:
            from quant.ops.reconcile import reconcile
            rep = reconcile(broker, own)
            for issue in (i for i in rep.issues if i.severity == "WARN"):
                notifier.warn(f"{cfg.symbol} reconcile", issue.detail)
            if not rep.ok:
                detail = "; ".join(i.detail for i in rep.critical)
                notifier.critical(f"{cfg.symbol}: reconciliation FAILED — not trading", detail)
                dec = LiveDecision(
                    ts=data.index[-1], symbol=cfg.symbol, action="halt",
                    price=float(data["close"].iloc[-1]), dry_run=dry_run,
                    reason=f"reconcile halt: {detail}", blocked=f"reconcile: {detail}")
                _safe_record(own, dec, cfg.strategy)
                return dec

        try:
            dec = run_live_step(
                strat, data, cfg.symbol, broker,
                risk_manager=FixedFractionRisk(fraction=cfg.fraction),
                gate=gate, dry_run=dry_run, mode=cfg.mode,
                bracket_cfg=BracketConfig(stop_pct=cfg.stop_loss, take_pct=cfg.take_profit),
                max_bar_age_days=None if intraday else cfg.max_bar_age_days,
                max_bar_age_seconds=(cfg.max_bar_age_bars * _bar_seconds(cfg.timeframe)
                                     if intraday else None),
            )
        except Exception as exc:
            # Still leave an audit record if the live step crashed mid-flight.
            dec = LiveDecision(
                ts=data.index[-1], symbol=cfg.symbol, action="error",
                price=float(data["close"].iloc[-1]), dry_run=dry_run,
                reason=f"live step failed: {type(exc).__name__}: {exc}",
                blocked=f"error: {exc}",
            )
            _safe_record(own, dec, cfg.strategy)
            notifier.critical(f"{cfg.symbol}: live step crashed", f"{type(exc).__name__}: {exc}")
            raise
        _safe_record(own, dec, cfg.strategy)

        # OMS: track a real placed order through its lifecycle, then sync once so a
        # synchronous (paper) fill is captured immediately for TCA.
        if not dry_run and dec.order_id:
            _oms_submit(oms, dec, cfg.strategy)
            _oms_sync(oms, broker)

        # Outcome alerts: a real order placed (INFO) or a blocked order (WARN).
        if not dry_run and dec.order_id:
            notifier.info(f"{cfg.symbol}: {dec.action} order placed",
                          f"qty={dec.qty:g} @ {dec.price:.2f} -> {dec.order_id}")
        elif dec.blocked:
            notifier.warn(f"{cfg.symbol}: order blocked", dec.blocked)
    finally:
        _record_run_heartbeat(own, cfg, dec, dry_run)
        if journal is None:
            own.close()

    log.info(f"[schedule] {cfg.symbol}/{cfg.strategy}: {dec.action} qty={dec.qty:g} "
             f"{'(dry-run)' if dry_run else ''} {('BLOCKED: ' + dec.blocked) if dec.blocked else ''}")
    return dec


def _safe_record(journal: TradeJournal, dec: LiveDecision, strategy: str) -> None:
    """Journal a decision; if the write itself fails, scream it into the log so an
    order that WAS placed is never lost silently (the audit-trail last resort)."""
    try:
        journal.record_live_decision(dec, strategy=strategy)
    except Exception as exc:  # noqa: BLE001
        log.critical(f"[schedule] FAILED to journal {dec.action} {dec.symbol} "
                     f"order={dec.order_id}: {exc} — decision was: {dec}")


def _oms_sync(oms: OMS, broker) -> None:
    """Advance broker order states in the OMS. Best-effort — audit, never trading-critical."""
    try:
        oms.sync(broker)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[oms] sync failed: {exc}")


def _oms_submit(oms: OMS, dec: LiveDecision, strategy: str) -> None:
    """Record a just-placed order in the OMS. A failure here loses lifecycle/TCA data
    but not the decision itself (already journaled), so log loudly and continue."""
    try:
        oms.on_submit(symbol=dec.symbol, side=dec.action, qty=dec.qty,
                      intended_price=dec.price, broker_order_id=str(dec.order_id),
                      strategy=strategy)
    except Exception as exc:  # noqa: BLE001
        log.critical(f"[oms] FAILED to record placed order {dec.order_id} "
                     f"({dec.action} {dec.qty:g} {dec.symbol}): {exc}")


def _record_run_heartbeat(journal: TradeJournal, cfg: LiveConfig,
                          dec: LiveDecision | None, dry_run: bool) -> None:
    """Stamp a proof-of-life heartbeat for the live run (so a missed run is detectable)."""
    if dec is None:
        status, detail = "error", "run produced no decision"
    elif dec.action == "error":
        status, detail = "error", dec.reason
    elif dec.action == "halt" or dec.blocked:
        status, detail = "warn", (dec.blocked or dec.reason)
    else:
        status, detail = "ok", dec.reason
    meta = {
        "symbol": cfg.symbol, "strategy": cfg.strategy, "dry_run": dry_run,
        "action": getattr(dec, "action", None),
        "bar_ts": str(getattr(dec, "ts", "")),
        "order_id": getattr(dec, "order_id", None),
    }
    try:
        record_heartbeat(journal, "live", status, detail or "", meta)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[health] failed to record live heartbeat: {exc}")


def _is_intraday(timeframe: str) -> bool:
    from quant.data.timeframes import get_timeframe
    return get_timeframe(timeframe).intraday


def _bar_seconds(timeframe: str) -> int:
    from quant.data.timeframes import get_timeframe
    return get_timeframe(timeframe).bar_seconds


def _is_trading_day(dt: datetime, calendar_name: str = "XNYS") -> bool:
    """Whether `dt`'s date is a trading session on the exchange calendar. Degrades
    to True (proceed) if pandas_market_calendars isn't installed."""
    try:
        import pandas_market_calendars as mcal
    except ModuleNotFoundError:  # pragma: no cover - optional dep
        log.warning("pandas_market_calendars not installed - cannot skip market holidays")
        return True
    sched = mcal.get_calendar(calendar_name).schedule(start_date=dt.date(), end_date=dt.date())
    return not sched.empty


def _is_market_open(dt: datetime, calendar_name: str = "XNYS") -> bool:
    """Whether `dt` falls inside the regular session (open..close). The intraday
    scheduler fires every N minutes around the clock; this gate keeps it from
    trading pre/post-market or on holidays. Degrades to the trading-day check if
    pandas_market_calendars is missing."""
    try:
        import pandas_market_calendars as mcal
    except ModuleNotFoundError:  # pragma: no cover - optional dep
        log.warning("pandas_market_calendars not installed - cannot check market hours")
        return _is_trading_day(dt, calendar_name)
    sched = mcal.get_calendar(calendar_name).schedule(start_date=dt.date(), end_date=dt.date())
    if sched.empty:
        return False
    ts = pd.Timestamp(dt)
    ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
    return bool(sched.iloc[0]["market_open"] <= ts <= sched.iloc[0]["market_close"])


def _parse_every(every: str) -> int:
    """'5min' / '15min' / '1h' -> interval minutes (loud on anything else)."""
    m = every.strip().lower()
    if m.endswith("min") and m[:-3].isdigit():
        return int(m[:-3])
    if m.endswith("h") and m[:-1].isdigit():
        return int(m[:-1]) * 60
    raise ValueError(f"unsupported interval {every!r}; use e.g. '5min', '15min', '1h'")


def run_schedule(
    configs: list[LiveConfig], *,
    at: str = "16:10", days: str = "mon-fri", tz: str = "America/New_York",
    every: str | None = None,
    dry_run: bool = True, run_now: bool = False,
) -> None:
    """Blocking APScheduler loop firing `live_and_journal` for each config.

    Two trigger modes:
    - daily cron (default): once per day at `at` on `days` — the daily-bar cadence.
    - `every` (e.g. '5min'): an interval trigger for intraday timeframes; each
      firing is gated on the exchange being OPEN (regular session), so the loop
      can run around the clock without trading pre/post-market or on holidays.
    """
    from zoneinfo import ZoneInfo

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    sched = BlockingScheduler(timezone=tz)
    notifier = get_notifier()
    cadence = f"every {every}" if every else f"at {at} {days}"

    def _tick_heartbeat(status: str, detail: str) -> None:
        """Prove the scheduler process itself is alive (distinct from the live run)."""
        try:
            with TradeJournal() as tj:
                record_heartbeat(tj, "scheduler", status, detail, {"tz": tz, "cadence": cadence})
        except Exception as exc:  # noqa: BLE001 - monitoring must never break the loop
            log.warning(f"[health] failed to record scheduler heartbeat: {exc}")

    def _job(c: LiveConfig) -> None:
        # Session gates: cron mon-fri still fires on holidays, and an interval
        # trigger fires around the clock — only act during a real session.
        now = datetime.now(ZoneInfo(tz))
        if every is not None:
            if not _is_market_open(now):
                log.debug(f"[schedule] market closed ({tz}) - skipping {c.symbol}/{c.strategy}")
                _tick_heartbeat("ok", f"market-closed skip: {c.symbol}/{c.strategy}")
                return
        elif not _is_trading_day(now):
            log.info(f"[schedule] not a trading day ({tz}) - skipping {c.symbol}/{c.strategy}")
            _tick_heartbeat("ok", f"holiday skip: {c.symbol}/{c.strategy}")
            return
        try:
            live_and_journal(c, dry_run=dry_run)
            _tick_heartbeat("ok", f"ran {c.symbol}/{c.strategy}")
        except Exception as exc:  # noqa: BLE001 - one job's failure must not kill the loop
            log.error(f"[schedule] {c.symbol}/{c.strategy} job FAILED: {type(exc).__name__}: {exc}")
            notifier.critical(f"scheduled job {c.symbol}/{c.strategy} FAILED",
                              f"{type(exc).__name__}: {exc}")
            _tick_heartbeat("error", f"{c.symbol}/{c.strategy}: {type(exc).__name__}: {exc}")

    if every is not None:
        minutes = _parse_every(every)
        trigger = IntervalTrigger(minutes=minutes, timezone=tz)
        grace = max(60, minutes * 30)          # tolerate a late wake-up, cap well under a day
    else:
        hour, minute = (int(x) for x in at.split(":"))
        trigger = CronTrigger(day_of_week=days, hour=hour, minute=minute, timezone=tz)
        grace = 3600                           # tolerate a late wake-up by up to an hour

    for cfg in configs:
        sched.add_job(
            _job, trigger,
            args=[cfg], id=f"{cfg.symbol}-{cfg.strategy}", name=f"{cfg.symbol}/{cfg.strategy}",
            misfire_grace_time=grace,
        )

    if run_now:
        for cfg in configs:
            _job(cfg)

    log.info(f"scheduler up: {len(configs)} job(s) {cadence} {tz} "
             f"[{'DRY-RUN' if dry_run else 'EXECUTE'}]. Ctrl+C to stop.")
    sched.start()
