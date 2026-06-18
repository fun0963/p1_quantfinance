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
    stop_loss: float = 0.0
    take_profit: float = 0.0
    mode: str = "target"


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
        data = load_bars(cfg.symbol, YFinanceFeed(), start=start_dt, timeframe=cfg.timeframe)
    strat = get_strategy_cls(cfg.strategy)(**cfg.params)

    dec = run_live_step(
        strat, data, cfg.symbol, _build_broker(cfg),
        risk_manager=FixedFractionRisk(fraction=cfg.fraction),
        gate=RiskGate(RiskLimits(enabled=True, max_position_notional=cfg.max_position_notional)),
        dry_run=dry_run, mode=cfg.mode,
        bracket_cfg=BracketConfig(stop_pct=cfg.stop_loss, take_pct=cfg.take_profit),
    )

    own = journal or TradeJournal()
    try:
        own.record_live_decision(dec, strategy=cfg.strategy)
    finally:
        if journal is None:
            own.close()

    log.info(f"[schedule] {cfg.symbol}/{cfg.strategy}: {dec.action} qty={dec.qty:g} "
             f"{'(dry-run)' if dry_run else ''} {('BLOCKED: ' + dec.blocked) if dec.blocked else ''}")
    return dec


def run_schedule(
    configs: list[LiveConfig], *,
    at: str = "16:10", days: str = "mon-fri", tz: str = "America/New_York",
    dry_run: bool = True, run_now: bool = False,
) -> None:
    """Blocking APScheduler loop firing `live_and_journal` for each config on a cron."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = (int(x) for x in at.split(":"))
    sched = BlockingScheduler(timezone=tz)
    for cfg in configs:
        sched.add_job(
            lambda c=cfg: live_and_journal(c, dry_run=dry_run),
            CronTrigger(day_of_week=days, hour=hour, minute=minute, timezone=tz),
            id=f"{cfg.symbol}-{cfg.strategy}", name=f"{cfg.symbol}/{cfg.strategy}",
            misfire_grace_time=3600,    # tolerate a late wake-up by up to an hour
        )

    if run_now:
        for cfg in configs:
            live_and_journal(cfg, dry_run=dry_run)

    log.info(f"scheduler up: {len(configs)} job(s) at {at} {days} {tz} "
             f"[{'DRY-RUN' if dry_run else 'EXECUTE'}]. Ctrl+C to stop.")
    sched.start()
