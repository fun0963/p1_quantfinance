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

from quant.execution.journal import TradeJournal
from quant.execution.live_runner import LiveDecision, run_live_step
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


def _build_broker(cfg: LiveConfig):
    if cfg.broker == "alpaca":
        from quant.execution.alpaca_broker import AlpacaBroker
        return AlpacaBroker()
    from quant.execution import PaperBroker
    return PaperBroker()


def live_and_journal(
    cfg: LiveConfig, *, dry_run: bool = True, journal: TradeJournal | None = None,
    data=None,
) -> LiveDecision:
    """Run one live decision for `cfg` and record it. The schedulable unit of work.

    `data` may be passed pre-loaded (skips the fetch) — handy for tests/reuse.
    """
    from quant.strategies.registry import get_strategy_cls

    if data is None:
        from quant.data.feeds.yfinance_feed import YFinanceFeed
        from quant.data.loaders import load_bars
        start_dt = datetime.fromisoformat(cfg.start).replace(tzinfo=UTC)
        # Live: re-download if the cache tail is stale, so we never decide on old bars.
        data = load_bars(cfg.symbol, YFinanceFeed(), start=start_dt, timeframe=cfg.timeframe,
                         max_staleness_days=cfg.max_staleness_days)
    strat = get_strategy_cls(cfg.strategy)(**cfg.params)
    broker = _build_broker(cfg)

    gate = RiskGate(RiskLimits(enabled=True, max_position_notional=cfg.max_position_notional,
                               max_daily_loss=cfg.max_daily_loss))
    # Feed today's P&L so the daily-loss breaker can actually trip in the live path.
    if hasattr(broker, "day_pnl"):
        try:
            gate.report_daily_pnl(broker.day_pnl())
        except Exception as exc:  # noqa: BLE001 - P&L read is best-effort
            log.warning(f"[schedule] could not read day P&L for {cfg.symbol}: {exc}")

    own = journal or TradeJournal()
    try:
        try:
            dec = run_live_step(
                strat, data, cfg.symbol, broker,
                risk_manager=FixedFractionRisk(fraction=cfg.fraction),
                gate=gate, dry_run=dry_run, mode=cfg.mode,
                bracket_cfg=BracketConfig(stop_pct=cfg.stop_loss, take_pct=cfg.take_profit),
                max_bar_age_days=cfg.max_bar_age_days,
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
            raise
        _safe_record(own, dec, cfg.strategy)
    finally:
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


def _is_trading_day(dt: datetime, calendar_name: str = "XNYS") -> bool:
    """Whether `dt`'s date is a trading session on the exchange calendar. Degrades
    to True (proceed) if pandas_market_calendars isn't installed."""
    try:
        import pandas_market_calendars as mcal
    except ModuleNotFoundError:  # pragma: no cover - optional dep
        log.warning("pandas_market_calendars not installed — cannot skip market holidays")
        return True
    sched = mcal.get_calendar(calendar_name).schedule(start_date=dt.date(), end_date=dt.date())
    return not sched.empty


def run_schedule(
    configs: list[LiveConfig], *,
    at: str = "16:10", days: str = "mon-fri", tz: str = "America/New_York",
    dry_run: bool = True, run_now: bool = False,
) -> None:
    """Blocking APScheduler loop firing `live_and_journal` for each config on a cron."""
    from zoneinfo import ZoneInfo

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = (int(x) for x in at.split(":"))
    sched = BlockingScheduler(timezone=tz)

    def _job(c: LiveConfig) -> None:
        # Skip market holidays — cron mon-fri still fires on them, and acting would
        # use the prior session's (now stale) bar.
        if not _is_trading_day(datetime.now(ZoneInfo(tz))):
            log.info(f"[schedule] not a trading day ({tz}) — skipping {c.symbol}/{c.strategy}")
            return
        try:
            live_and_journal(c, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - one job's failure must not kill the loop
            log.error(f"[schedule] {c.symbol}/{c.strategy} job FAILED: {type(exc).__name__}: {exc}")

    for cfg in configs:
        sched.add_job(
            _job, CronTrigger(day_of_week=days, hour=hour, minute=minute, timezone=tz),
            args=[cfg], id=f"{cfg.symbol}-{cfg.strategy}", name=f"{cfg.symbol}/{cfg.strategy}",
            misfire_grace_time=3600,    # tolerate a late wake-up by up to an hour
        )

    if run_now:
        for cfg in configs:
            _job(cfg)

    log.info(f"scheduler up: {len(configs)} job(s) at {at} {days} {tz} "
             f"[{'DRY-RUN' if dry_run else 'EXECUTE'}]. Ctrl+C to stop.")
    sched.start()
