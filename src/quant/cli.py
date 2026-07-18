"""Command-line entrypoint (typer).

    quant info                    - resolved settings + registered strategies
    quant download SYMBOL         - fetch history into the parquet store
    quant backtest SYMBOL         - run a strategy on both engines, compare
    quant sweep SYMBOL            - vectorized parameter sweep (ranked + CSV/heatmap)
    quant walkforward SYMBOL      - out-of-sample walk-forward validation

All strategy-driven commands take `--strategy NAME` (see `quant info`) plus
optional `--params k=v,...` / `--grid k=v1,v2;...` overrides.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from config import get_settings
from quant.readapi import df_records as _df_records
from quant.readapi import json_default as _json_default
from quant.utils import get_logger, setup_logging

app = typer.Typer(help="Quant trading system CLI", no_args_is_help=True)
log = get_logger(__name__)


# --- small parsing helpers --------------------------------------------------
def _coerce(v: str):
    """Coerce a CLI string to int, then float, else leave as str."""
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            continue
    return v


def _parse_params(spec: str | None) -> dict:
    """'fast=20,slow=50' -> {'fast': 20, 'slow': 50}."""
    if not spec:
        return {}
    out = {}
    for part in spec.split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = _coerce(v.strip())
    return out


def _parse_grid(spec: str | None) -> dict | None:
    """'fast=5,10,20;slow=50,100' -> {'fast': [5,10,20], 'slow': [50,100]}."""
    if not spec:
        return None
    out = {}
    for axis in spec.split(";"):
        k, _, vals = axis.partition("=")
        out[k.strip()] = [_coerce(v.strip()) for v in vals.split(",") if v.strip()]
    return out


def _parse_legs(spec: str) -> list:
    """'SPY:momentum:0.5:lookback=100; QQQ:ma_cross:0.5:fast=20,slow=50'
    -> [PortfolioLeg(...), ...]. Per leg: symbol:strategy:weight[:k=v,...]."""
    from quant.portfolio import PortfolioLeg

    out = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) < 3:
            raise typer.BadParameter(f"leg {chunk!r} must be symbol:strategy:weight[:params]")
        params = _parse_params(parts[3]) if len(parts) > 3 else {}
        out.append(PortfolioLeg(symbol=parts[0].strip(), strategy=parts[1].strip(),
                                weight=float(parts[2]), params=params))
    return out


# --- machine-readable output (--json) ----------------------------------------
# Payload builders live in quant.readapi, SHARED with the read-only MCP server
# so the two surfaces can never drift apart.
_JSON_HELP = "machine-readable JSON on stdout (one document; for scripts/AI agents)"


def _emit_json(command: str, data, ok: bool | None = None) -> None:
    """The --json contract: exactly ONE JSON document on stdout, nothing else.

    Top-level keys: `command`, `data`, plus `ok` for commands with pass/fail
    semantics (their exit codes are unchanged). ensure_ascii keeps cp950
    consoles safe.
    """
    import json

    doc: dict = {"command": command}
    if ok is not None:
        doc["ok"] = ok
    doc["data"] = data
    typer.echo(json.dumps(doc, ensure_ascii=True, default=_json_default))


def _null_echo(*_args, **_kwargs) -> None:
    """typer.echo stand-in for --json mode: human chatter must not pollute stdout."""


def _load(symbol: str, start: str, timeframe: str, no_cache: bool = False):
    from quant.data.loaders import fetch_bars

    return fetch_bars(symbol, start, timeframe, use_cache=not no_cache)


def _live_broker(broker: str):
    """Build a broker for the ops commands (alpaca real-paper, or the sim)."""
    if broker == "paper":
        from quant.execution import PaperBroker
        return PaperBroker()
    from quant.execution.alpaca_broker import AlpacaBroker
    return AlpacaBroker()


def _engine_cls(name: str):
    """Resolve an engine name to its class for backtest-driven commands."""
    from quant.backtest.backtrader_engine import BacktraderEngine
    from quant.backtest.vectorbt_engine import VectorBTEngine

    engines = {"vectorbt": VectorBTEngine, "backtrader": BacktraderEngine}
    if name not in engines:
        raise typer.BadParameter(f"engine must be one of {sorted(engines)}")
    return engines[name]


def _cfg_from_spec(spec_name: str, *, symbol: str = "", broker: str, mode: str,
                   fraction: float):
    """LiveConfig from a named spec: strategy identity (symbol/strategy/params/
    start/timeframe) and the risk block come from the reviewed spec file;
    operational knobs (broker/mode/fraction) stay CLI-side. `execute` can NEVER
    come from a spec - going live is always an explicit human flag."""
    from quant.execution.scheduler import LiveConfig
    from quant.strategies.spec import get_spec

    sp = get_spec(spec_name)
    r = sp.risk
    return LiveConfig(
        symbol=symbol or sp.symbol, strategy=sp.strategy, params=sp.params,
        start=sp.start, timeframe=sp.timeframe, broker=broker, mode=mode,
        fraction=fraction,
        max_position_notional=float(r.get("max_position_notional", 0.0)),
        max_daily_loss=float(r.get("max_daily_loss", 0.0)),
        stop_loss=float(r.get("stop_loss", 0.0)),
        take_profit=float(r.get("take_profit", 0.0)),
    )


# --- commands ---------------------------------------------------------------
@app.callback()
def _init() -> None:
    s = get_settings()
    setup_logging(level=s.log_level, log_dir=s.log_dir)


@app.command()
def info(as_json: bool = typer.Option(False, "--json", help=_JSON_HELP)) -> None:
    """Show resolved settings and registered strategies."""
    from quant.strategies.registry import REGISTRY, available

    s = get_settings()
    if as_json:
        _emit_json("info", {
            "env": s.env,
            "data_dir": str(s.data_dir),
            "log_dir": str(s.log_dir),
            "alpaca_paper": s.alpaca_paper,
            "alpaca_key": "set" if s.alpaca_api_key else "MISSING",  # never the key itself
            "strategies": {name: REGISTRY[name].default_grid() for name in available()},
        })
        return
    typer.echo(f"env         : {s.env}")
    typer.echo(f"data_dir    : {s.data_dir}")
    typer.echo(f"log_dir     : {s.log_dir}")
    typer.echo(f"alpaca_paper: {s.alpaca_paper}")
    typer.echo(f"alpaca_key  : {'set' if s.alpaca_api_key else 'MISSING'}")
    typer.echo("strategies  :")
    for name in available():
        typer.echo(f"  - {name:<10} grid={REGISTRY[name].default_grid()}")


@app.command()
def live(
    symbol: str = typer.Argument("", help="ticker (optional when --spec provides one)"),
    spec: str = typer.Option("", help="named spec from configs/strategies.json - fills "
                                      "symbol/strategy/params/start/timeframe AND risk settings"),
    strategy: str = typer.Option("ma_cross", help="strategy name"),
    params: str = typer.Option("", help="e.g. 'lookback=100'"),
    start: str = typer.Option("2023-01-01", help="history start for signals (YYYY-MM-DD)"),
    timeframe: str = typer.Option("1d"),
    broker: str = typer.Option("alpaca", help="alpaca | paper"),
    mode: str = typer.Option("target", help="target (reconcile to desired position) | signal (edge only)"),
    cash: float = typer.Option(100_000, help="starting cash for the paper broker"),
    fraction: float = typer.Option(0.95, help="fraction of cash per entry"),
    max_position_notional: float = typer.Option(0, help="position value cap (0=off)"),
    max_daily_loss: float = typer.Option(0, help="daily-loss breaker (0=off); blocks NEW risk, not exits"),
    stop_loss: float = typer.Option(0, help="bracket stop %% below entry, e.g. 0.05 (needs --take-profit)"),
    take_profit: float = typer.Option(0, help="bracket take %% above entry, e.g. 0.15 (needs --stop-loss)"),
    execute: bool = typer.Option(False, "--execute",
                                 help="ACTUALLY submit the order (default: dry-run, no order)"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Evaluate the LATEST bar and reconcile the position (signal->risk gate->broker). Dry-run by default."""
    from quant.execution.scheduler import LiveConfig, live_and_journal

    if spec:
        cfg = _cfg_from_spec(spec, symbol=symbol, broker=broker, mode=mode, fraction=fraction)
    else:
        if not symbol:
            raise typer.BadParameter("give a SYMBOL or --spec NAME")
        cfg = LiveConfig(symbol=symbol, strategy=strategy, params=_parse_params(params),
                         start=start, timeframe=timeframe, broker=broker, mode=mode,
                         fraction=fraction, max_position_notional=max_position_notional,
                         max_daily_loss=max_daily_loss, stop_loss=stop_loss, take_profit=take_profit)
    dec = live_and_journal(cfg, dry_run=not execute)

    if as_json:
        _emit_json("live", {
            "symbol": cfg.symbol, "strategy": cfg.strategy, "spec": spec or None,
            "broker": broker, "mode": mode, "dry_run": not execute,
            "bar_ts": dec.ts, "price": dec.price,
            "position_before": dec.position_before, "target_state": dec.target_state,
            "action": dec.action, "qty": dec.qty, "reason": dec.reason,
            "blocked": dec.blocked, "order_id": dec.order_id,
        })
        return

    label = "EXECUTE" if execute else "DRY-RUN"
    typer.echo(f"\n[LIVE {label}] {cfg.strategy} on {cfg.symbol}  (broker={broker}, mode={mode}"
               + (f", spec={spec}" if spec else "") + ")")
    typer.echo(f"  bar              : {dec.ts}  close={dec.price:.2f}")
    typer.echo(f"  position before  : {dec.position_before:g}")
    if dec.target_state:
        typer.echo(f"  target state     : {dec.target_state}")
    typer.echo(f"  action           : {dec.action}  qty={dec.qty:g}")
    typer.echo(f"  reason           : {dec.reason or '(no signal)'}")
    if dec.blocked:
        typer.echo(f"  BLOCKED          : {dec.blocked}")
    if dec.order_id:
        typer.echo(f"  order id         : {dec.order_id}")
    typer.echo("  logged (view: quant journal --live)")

    if not execute and dec.action in ("buy", "sell") and not dec.blocked:
        typer.echo("\n  ^ this was a DRY-RUN. Re-run with --execute to place the order.")


@app.command()
def schedule(
    symbol: str = typer.Argument("", help="ticker (omit when using --spec)"),
    spec: list[str] = typer.Option([], "--spec",
                                   help="named spec(s) from configs/strategies.json; repeat the "
                                        "flag to schedule several strategies in this one process"),
    strategy: str = typer.Option("ma_cross", help="strategy name"),
    params: str = typer.Option("", help="e.g. 'lookback=100'"),
    start: str = typer.Option("2023-01-01", help="history start for signals"),
    timeframe: str = typer.Option("1d"),
    broker: str = typer.Option("alpaca", help="alpaca | paper"),
    mode: str = typer.Option("target"),
    fraction: float = typer.Option(0.95),
    max_position_notional: float = typer.Option(0),
    max_daily_loss: float = typer.Option(0, help="daily-loss breaker (0=off); blocks NEW risk, not exits"),
    stop_loss: float = typer.Option(0, help="bracket stop %% (needs --take-profit)"),
    take_profit: float = typer.Option(0, help="bracket take %% (needs --stop-loss)"),
    at: str = typer.Option("16:10", help="HH:MM to run each day (daily mode)"),
    days: str = typer.Option("mon-fri", help="cron day_of_week (e.g. mon-fri)"),
    every: str = typer.Option("", help="intraday mode: run every N ('5min', '15min', '1h') "
                                       "while the market is OPEN, instead of daily at --at"),
    tz: str = typer.Option("America/New_York", help="timezone for the schedule"),
    run_now: bool = typer.Option(False, "--run-now", help="also run once immediately on start"),
    execute: bool = typer.Option(False, "--execute", help="ACTUALLY submit (default: dry-run)"),
) -> None:
    """Run `live` automatically on a cron schedule (blocking process). Dry-run by default.

    One-click start/stop/status wrapper: scripts/trading.cmd (does NOT survive
    reboot - by choice). For reboot-survival see docs/SCHEDULING.md. Ctrl+C stops.
    """
    from quant.execution.scheduler import LiveConfig, run_schedule

    if spec:
        if symbol:
            raise typer.BadParameter("give either a SYMBOL or --spec name(s), not both")
        cfgs = [_cfg_from_spec(s, broker=broker, mode=mode, fraction=fraction) for s in spec]
    else:
        if not symbol:
            raise typer.BadParameter("give a SYMBOL or --spec NAME")
        cfgs = [LiveConfig(symbol=symbol, strategy=strategy, params=_parse_params(params),
                           start=start, timeframe=timeframe, broker=broker, mode=mode,
                           fraction=fraction, max_position_notional=max_position_notional,
                           max_daily_loss=max_daily_loss, stop_loss=stop_loss,
                           take_profit=take_profit)]
    label = "EXECUTE" if execute else "DRY-RUN"
    jobs = ", ".join(f"{c.strategy} on {c.symbol}" for c in cfgs)
    cadence = f"every {every} (market hours only)" if every else f"at {at} {days}"
    typer.echo(f"[SCHEDULE {label}] {jobs} (broker={broker}) {cadence} {tz}")
    if execute:
        # ASCII only: this line crashes cp950 consoles if it carries U+26A0 etc.
        typer.echo("  WARNING: live order routing is ON. Ctrl+C to stop.")
    else:
        typer.echo("  dry-run: decisions are computed & journaled, no orders. Ctrl+C to stop.")
    run_schedule(cfgs, at=at, days=days, tz=tz, every=every or None,
                 dry_run=not execute, run_now=run_now)


@app.command()
def protect(
    symbol: str,
    stop_loss: float = typer.Option(..., help="stop %% below avg entry, e.g. 0.05"),
    take_profit: float = typer.Option(..., help="take %% above avg entry, e.g. 0.15"),
    execute: bool = typer.Option(False, "--execute", help="ACTUALLY submit the OCO (default: dry-run)"),
) -> None:
    """Attach a protective OCO (stop-loss + take-profit) to an EXISTING Alpaca position."""
    from quant.execution.alpaca_broker import AlpacaBroker
    from quant.risk.bracket import bracket_prices

    brk = AlpacaBroker()
    pos = next((p for p in brk.get_positions() if p.symbol == symbol), None)
    if pos is None:
        typer.echo(f"No open position in {symbol} - nothing to protect.")
        raise typer.Exit(code=1)

    stop, take = bracket_prices(pos.avg_price, stop_loss, take_profit)
    if stop is None or take is None:
        typer.echo("OCO needs both --stop-loss and --take-profit > 0.")
        raise typer.Exit(code=1)
    whole = int(pos.qty)   # Alpaca OCO needs whole shares
    typer.echo(f"\n{symbol}: {pos.qty:g} sh @ avg {pos.avg_price:.2f}")
    typer.echo(f"  OCO protect {whole} sh (whole shares) -> stop-loss {stop}  /  take-profit {take}")
    if whole < pos.qty:
        typer.echo(f"  note: {pos.qty - whole:.4f} fractional share left unprotected (OCO needs whole shares)")
    if whole < 1:
        typer.echo("  position < 1 whole share - cannot place an OCO.")
        raise typer.Exit(code=1)

    if not execute:
        typer.echo("\n  ^ DRY-RUN (no order). Re-run with --execute to place the OCO.")
        raise typer.Exit(code=0)

    oid = brk.protect_position(symbol, stop, take)
    typer.echo(f"  OCO submitted -> order {oid}")


@app.command()
def account(as_json: bool = typer.Option(False, "--json", help=_JSON_HELP)) -> None:
    """Verify the Alpaca paper connection: prints account cash/equity & positions (read-only)."""
    from quant.execution.alpaca_broker import AlpacaBroker

    try:
        broker = AlpacaBroker()
        summary = broker.account_summary()
        positions = broker.get_positions()
    except Exception as exc:
        if as_json:
            _emit_json("account", {"error": f"{type(exc).__name__}: {exc}"}, ok=False)
            raise typer.Exit(code=1) from None
        typer.echo(f"Alpaca connection FAILED: {type(exc).__name__}: {exc}")
        typer.echo("  - check ALPACA_API_KEY / ALPACA_SECRET_KEY in .env (paper keys)")
        typer.echo("  - check ALPACA_PAPER=true")
        raise typer.Exit(code=1)

    if as_json:
        _emit_json("account", {
            "summary": summary,
            "positions": [{"symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price}
                          for p in positions],
        }, ok=True)
        return

    typer.echo("\nAlpaca paper account - connected OK")
    for k, v in summary.items():
        typer.echo(f"  {k:<16}: {v}")
    typer.echo("  open positions  : "
               + (", ".join(f"{p.symbol} {p.qty:g}@{p.avg_price:.2f}" for p in positions) or "none"))


@app.command()
def status(
    broker: str = typer.Option("alpaca", help="alpaca | paper"),
    offline: bool = typer.Option(False, "--offline",
                                 help="skip broker calls (account/reconcile) - local state only"),
    max_silence_minutes: int = typer.Option(1500, help="health: flag components silent longer than this"),
    limit: int = typer.Option(5, help="recent decisions/orders to include"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """One-screen system snapshot: account, reconcile, health, recent decisions,
    orders, TCA rollup, configured specs - what would otherwise take five commands.

    Sections degrade independently: a broker outage marks that section failed but
    never hides local state. Exit 1 when any checked section is not ok.
    Note: specs are LISTED from config only; run `quant lifecycle --all` for verdicts.
    """
    from quant.readapi import status_snapshot

    sections, overall = status_snapshot(broker=broker, offline=offline,
                                        max_silence_minutes=max_silence_minutes,
                                        limit=limit, broker_factory=_live_broker)
    if as_json:
        _emit_json("status", sections, ok=overall)
        raise typer.Exit(code=0 if overall else 1)

    typer.echo(f"\n[STATUS] broker={broker}  checked {sections['checked_at']}")
    b = sections["broker"]
    if "skipped" in b:
        typer.echo("  broker    : (skipped - offline)")
    elif "error" in b:
        typer.echo(f"  broker    : FAILED - {b['error']}")
    else:
        acct = b["account"]
        acct_line = "  ".join(f"{k}={v}" for k, v in list(acct.items())[:4]) or "(no summary)"
        typer.echo(f"  account   : {acct_line}")
        typer.echo("  positions : "
                   + (", ".join(f"{p['symbol']} {p['qty']:g}@{p['avg_price']:.2f}"
                                for p in b["positions"]) or "none"))
        typer.echo(f"  reconcile : {b['reconcile']['summary']}")
        for i in b["reconcile"]["issues"]:
            typer.echo(f"              [{i['severity']}] {i['detail']}")
    h = sections["health"]
    if "error" in h:
        typer.echo(f"  health    : FAILED - {h['error']}")
    else:
        typer.echo(f"  health    : {h['summary']}")
        for c in h["components"]:
            age = f"{c['age_minutes']:.0f}m ago" if c["age_minutes"] is not None else "never"
            flag = "  <<< STALE" if c["stale"] else ""
            typer.echo(f"              {c['component']:<12} {c['status']:<8} {age}{flag}")
    if "journal_error" in sections:
        typer.echo(f"  journal   : FAILED - {sections['journal_error']}")
    else:
        for d in sections["recent_decisions"]:
            typer.echo(f"  decision  : {d.get('logged_at', '?')}  {d.get('symbol')} "
                       f"{d.get('action')} qty={d.get('qty')}")
        if not sections["recent_decisions"]:
            typer.echo("  decision  : (none yet)")
        typer.echo(f"  tca       : {sections['tca']['summary']}")
    spec_rows = sections["specs"]
    if isinstance(spec_rows, dict):
        typer.echo(f"  specs     : FAILED - {spec_rows['error']}")
    else:
        for sp in spec_rows:
            typer.echo(f"  spec      : {sp['name']:<16} {sp['symbol']}/{sp['strategy']} "
                       f"{sp['timeframe']} [{sp['state']}]")
    typer.echo(f"  overall   : {'ok' if overall else 'NOT OK'}")
    raise typer.Exit(code=0 if overall else 1)


@app.command()
def download(
    symbol: str,
    start: str = typer.Option("2020-01-01", help="YYYY-MM-DD"),
    timeframe: str = typer.Option("1d"),
    source: str = typer.Option("", help="force a feed (yfinance | alpaca); "
                                        "default: the timeframe's registry default"),
) -> None:
    """Download historical bars into the local parquet store."""
    from quant.data.feeds import DataFeed, get_feed
    from quant.data.storage import get_store

    feed: DataFeed
    if source == "alpaca":
        from quant.data.feeds.alpaca_feed import AlpacaFeed
        feed = AlpacaFeed()
    elif source == "yfinance":
        from quant.data.feeds.yfinance_feed import YFinanceFeed
        feed = YFinanceFeed()
    else:
        feed = get_feed(timeframe)   # registry default (alpaca for 1min, yfinance daily+)
    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    df = feed.get_history(symbol, start=start_dt, timeframe=timeframe)
    handle = get_store().save(symbol, timeframe, df)  # parquet or timescale per config
    typer.echo(f"Saved {len(df)} bars -> {handle}")


@app.command()
def journal(
    session: int = typer.Option(0, help="show fills/blocks for this session id (0 = list all)"),
    live: bool = typer.Option(False, "--live", help="show the live-runner decision log instead"),
    limit: int = typer.Option(20, help="how many recent rows to list"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Review the SQLite trade journal: paper sessions, a session's detail, or live decisions."""
    from quant.execution import TradeJournal

    with TradeJournal() as tj:
        if live:
            rows = tj.live_log(limit=limit)
            if as_json:
                _emit_json("journal", {"mode": "live", "db": str(tj.path),
                                       "records": _df_records(rows)})
                return
            typer.echo(f"\nRecent live decisions (newest first), db: {tj.path}\n")
            typer.echo(rows.to_string(index=False) if not rows.empty
                       else "no live decisions yet - run `quant live ...`")
        elif session > 0:
            fills = tj.fills(session)
            blocks = tj.blocked(session)
            if as_json:
                _emit_json("journal", {"mode": "session", "session": session,
                                       "db": str(tj.path), "fills": _df_records(fills),
                                       "blocked": _df_records(blocks)})
                return
            typer.echo(f"\n=== session #{session} - {len(fills)} fills ===")
            typer.echo(fills.to_string(index=False) if not fills.empty else "(no fills)")
            typer.echo(f"\n=== {len(blocks)} blocked orders ===")
            typer.echo(blocks.to_string(index=False) if not blocks.empty else "(none)")
        else:
            sessions = tj.sessions(limit=limit)
            if as_json:
                _emit_json("journal", {"mode": "sessions", "db": str(tj.path),
                                       "records": _df_records(sessions)})
                return
            if sessions.empty:
                typer.echo("journal is empty - run `quant paper ...` first")
            else:
                typer.echo(f"\nRecent sessions (newest first), db: {tj.path}\n")
                typer.echo(sessions.to_string(index=False))


@app.command()
def check(
    symbol: str,
    start: str = typer.Option("2020-01-01", help="YYYY-MM-DD"),
    timeframe: str = typer.Option("1d"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Run data-quality checks on a symbol's bars (NaNs, gaps, OHLC, splits)."""
    from quant.data.quality import check_bars

    data = _load(symbol, start, timeframe)
    report = check_bars(data)
    if as_json:
        _emit_json("check", {
            "symbol": symbol, "timeframe": timeframe,
            "start": str(data.index[0].date()), "end": str(data.index[-1].date()),
            "n_bars": report.n_bars, "issues": report.issues, "warnings": report.warnings,
        }, ok=report.ok)
        raise typer.Exit(code=0 if report.ok else 1)
    typer.echo(f"\n{symbol}  ({data.index[0].date()} -> {data.index[-1].date()})")
    typer.echo(str(report))
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def backtest(
    symbol: str = typer.Argument("", help="ticker (optional when --spec provides one)"),
    spec: str = typer.Option("", help="named spec from configs/strategies.json - fills "
                                      "symbol/strategy/params/start/timeframe"),
    strategy: str = typer.Option("ma_cross", help="strategy name (see `quant info`)"),
    params: str = typer.Option("", help="e.g. 'fast=20,slow=50' (default: strategy defaults)"),
    start: str = typer.Option("2020-01-01", help="YYYY-MM-DD"),
    timeframe: str = typer.Option("1d"),
    cash: float = typer.Option(100_000),
    engine: str = typer.Option("both", help="both | vectorbt | backtrader"),
    fees_bps: float = typer.Option(5.0, help="commission in basis points per side"),
    slippage_bps: float = typer.Option(0.0, help="adverse slippage in basis points per side"),
    calibrate: bool = typer.Option(False, "--calibrate",
                                   help="derive fees+slippage from the live journal's TCA history"),
    no_cache: bool = typer.Option(False, "--no-cache", help="force re-download"),
    plot: bool = typer.Option(False, "--plot", help="save an interactive equity/drawdown HTML"),
    report: bool = typer.Option(False, "--report",
                                help="save a full HTML tear sheet (metrics + equity + drawdown + monthly)"),
    log_experiment: bool = typer.Option(True, "--log/--no-log",
                                        help="record this run to the experiment store"),
    note: str = typer.Option("", help="free-text note attached to the logged experiment"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Backtest a strategy and compare engines side by side."""
    from quant.backtest.backtrader_engine import BacktraderEngine
    from quant.backtest.costs import CostModel
    from quant.backtest.vectorbt_engine import VectorBTEngine
    from quant.strategies.registry import get_strategy_cls

    # --json: same computation and side effects (experiment log / plot / report),
    # human chatter muted, one JSON document at the end.
    echo = _null_echo if as_json else typer.echo
    payload: dict = {}

    parsed = _parse_params(params)
    if spec:
        from quant.strategies.spec import get_spec

        sp = get_spec(spec)
        symbol = symbol or sp.symbol       # explicit ticker still wins
        strategy, parsed = sp.strategy, sp.params
        start, timeframe = sp.start, sp.timeframe
    if not symbol:
        raise typer.BadParameter("give a SYMBOL or --spec NAME")

    data = _load(symbol, start, timeframe, no_cache)
    strat = get_strategy_cls(strategy)(**parsed)

    if calibrate:
        # Close the loop: charge the backtest what live execution actually cost.
        from quant.execution import TradeJournal
        from quant.ops.tca import tca_report

        with TradeJournal() as tj:
            cost = CostModel.from_tca(tca_report(tj, strategy=strategy))
    else:
        cost = CostModel(fees=fees_bps / 1e4, slippage=slippage_bps / 1e4)

    engines = {"vectorbt": VectorBTEngine, "backtrader": BacktraderEngine}
    chosen = engines if engine == "both" else {engine: engines[engine]}
    results = {n: cls(cash=cash, fees=cost.fees, slippage=cost.slippage).run(strat, data, timeframe=timeframe)
               for n, cls in chosen.items()}

    payload = {
        "symbol": symbol, "strategy": strategy, "params": parsed, "spec": spec or None,
        "start": start, "timeframe": timeframe, "bars": len(data),
        "window": [str(data.index[0].date()), str(data.index[-1].date())],
        "cost": {"fees_bps": round(cost.fees * 1e4, 4),
                 "slippage_bps": round(cost.slippage * 1e4, 4),
                 "calibrated": calibrate},
        "engines": {n: r.metrics for n, r in results.items()},
    }
    echo(f"\n{strat}  on  {symbol}  "
         f"({len(data)} bars, {data.index[0].date()} -> {data.index[-1].date()})")
    echo(cost.summary())
    keys = ["final_equity", "total_return_pct", "cagr_pct", "sharpe", "sortino", "calmar",
            "max_drawdown_pct", "num_trades"]
    header = f"{'metric':<20}" + "".join(f"{n:>14}" for n in results)
    echo(header)
    echo("-" * len(header))
    for k in keys:
        row = f"{k:<20}" + "".join(f"{str(r.metrics.get(k)):>14}" for r in results.values())
        echo(row)

    if log_experiment:
        from quant.research import ExperimentStore, log_backtest

        with ExperimentStore() as store:
            ids = [log_backtest(store, r, symbol=symbol, strategy=strategy, params=parsed,
                                start=start, timeframe=timeframe, cost=cost, data=data, notes=note)
                   for r in results.values()]
        payload["experiment_ids"] = ids
        echo(f"\nlogged experiment(s) {ids} (quant experiments to review)")

    if plot:
        from quant.backtest.plots import plot_equity

        path = plot_equity(results, out_path=f"reports/equity_{symbol}_{strategy}.html",
                           title=f"{strat.name} on {symbol}")
        payload["plot_path"] = str(path)
        echo(f"\nEquity/drawdown chart -> {path}")

    if report:
        from quant.backtest.metrics import trade_stats
        from quant.backtest.report import build_report

        # Report the primary engine's result (prefer vectorbt if it was run).
        name = "vectorbt" if "vectorbt" in results else next(iter(results))
        res = results[name]
        full = {**res.metrics, **trade_stats(res.trades)}
        path = build_report(res, symbol=symbol, strategy=strategy, metrics=full, data=data,
                            out_path=f"reports/report_{symbol}_{strategy}.html",
                            title=f"{strat.name} on {symbol}",
                            subtitle=f"{name} · {data.index[0].date()} -> {data.index[-1].date()} · {cost.summary()}")
        payload["report_path"] = str(path)
        echo(f"\nReport -> {path}")

    if as_json:
        _emit_json("backtest", payload)


@app.command()
def experiments(
    strategy: str = typer.Option("", help="filter by strategy"),
    symbol: str = typer.Option("", help="filter by symbol"),
    limit: int = typer.Option(20, help="how many recent experiments to show"),
    show: int = typer.Option(0, "--id", help="show the full record for one experiment id"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Review logged backtest experiments (the anti-overfitting research log)."""
    from quant.research import ExperimentStore

    if as_json:
        from quant.readapi import experiment_get, experiments_list

        if show:
            out = experiment_get(show)
            if "error" in out:
                _emit_json("experiments", out, ok=False)
                raise typer.Exit(code=1)
            _emit_json("experiments", {"mode": "one", **out})
            return
        _emit_json("experiments", {"mode": "list",
                                   **experiments_list(strategy=strategy or None,
                                                      symbol=symbol or None, limit=limit)})
        return

    with ExperimentStore() as store:
        if show:
            rec = store.get(show)
            if rec is None:
                typer.echo(f"no experiment #{show}")
                raise typer.Exit(code=1)
            dirty = " (dirty)" if rec["git_dirty"] else ""
            typer.echo(f"\nexperiment #{rec['id']}  {rec['kind']}  {rec['symbol']}/{rec['strategy']}")
            typer.echo(f"  run_at   : {rec['run_at']}")
            typer.echo(f"  git      : {rec['git_hash']}{dirty}")
            typer.echo(f"  engine   : {rec['engine']}   cost: {rec['fees_bps']}+{rec['slippage_bps']} bps")
            typer.echo(f"  params   : {rec['params']}")
            typer.echo(f"  data     : {rec['data_bars']} bars {rec['data_start']} -> {rec['data_end']}")
            typer.echo(f"  metrics  : {rec['metrics']}")
            if rec["notes"]:
                typer.echo(f"  notes    : {rec['notes']}")
            return
        df = store.recent(limit=limit, strategy=strategy or None, symbol=symbol or None)
        if df.empty:
            typer.echo("no experiments logged yet (run `quant backtest ...`)")
            return
        typer.echo(f"\n{len(df)} recent experiment(s):\n")
        typer.echo(df.to_string(index=False))


@app.command()
def lifecycle(
    name: str = typer.Argument("", help="spec name (blank + --all = every spec)"),
    all_specs: bool = typer.Option(False, "--all", help="evaluate every spec in the file"),
    config: str = typer.Option("", help="spec file (default: configs/strategies.json)"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Pre-committed promote/retire health check for named strategy specs.

    Runs each spec on its recent data window and evaluates the lifecycle rules
    written IN the spec (rolling-Sharpe floor, drawdown floor, min activity).
    Exit code 1 if any spec breaches - scriptable as a scheduled health gate."""
    from quant.backtest.vectorbt_engine import VectorBTEngine
    from quant.research import LifecycleRules, check_lifecycle
    from quant.strategies.registry import get_strategy_cls
    from quant.strategies.spec import load_specs

    specs = load_specs(config or None)
    if all_specs:
        chosen = list(specs.values())
    elif name:
        if name not in specs:
            if as_json:
                _emit_json("lifecycle", {"error": f"no spec named {name}",
                                         "available": sorted(specs)}, ok=False)
                raise typer.Exit(code=1)
            typer.echo(f"no spec named {name!r}; available: {sorted(specs)}")
            raise typer.Exit(code=1)
        chosen = [specs[name]]
    else:
        raise typer.BadParameter("give a spec NAME or --all")

    any_breach = False
    evaluated = []
    for sp in chosen:
        data = _load(sp.symbol, sp.start, sp.timeframe)
        strat = get_strategy_cls(sp.strategy)(**sp.params)
        res = VectorBTEngine().run(strat, data, timeframe=sp.timeframe)
        rules = LifecycleRules.from_dict(sp.lifecycle)
        # Activity is judged on the same trailing window as the risk rules.
        window_start = data.index[max(0, len(data) - rules.eval_bars)]
        trades = int(strat.generate_signals(data)["entries"].loc[window_start:].sum())
        rep = check_lifecycle(sp.name, state=sp.state, equity=res.equity_curve,
                              num_trades=trades, rules=rules, timeframe=sp.timeframe)
        evaluated.append({
            "name": rep.name, "state": rep.state, "ok": rep.ok, "verdict": rep.verdict,
            "window_bars": rep.window_bars, "rolling_sharpe": rep.rolling_sharpe,
            "window_return_pct": rep.window_return_pct,
            "window_drawdown_pct": rep.window_drawdown_pct, "trades": rep.trades,
            "breaches": rep.breaches,
        })
        if not as_json:
            typer.echo(rep.summary())
            for b in rep.breaches:
                typer.echo(f"  breach: {b}")
        any_breach = any_breach or not rep.ok

    if as_json:
        _emit_json("lifecycle", {"specs": evaluated}, ok=not any_breach)
    raise typer.Exit(code=1 if any_breach else 0)


note_app = typer.Typer(help="Research knowledge base - one Markdown note per idea "
                            "(hypothesis / approach / result / verdict).")
app.add_typer(note_app, name="note")


@note_app.command("new")
def note_new(
    title: str = typer.Argument(..., help="one-line name of the idea"),
    status: str = typer.Option("idea", help="idea | testing | adopted | rejected"),
    strategy: str = typer.Option("", help="related strategy name (optional)"),
    symbols: str = typer.Option("", help="comma-separated tickers (optional)"),
    experiments: str = typer.Option("", help="comma-separated experiment ids as evidence"),
    notes_dir: str = typer.Option("", "--dir", help="notes directory (default: research_notes/)"),
) -> None:
    """Create a templated note; fill in hypothesis/approach/result, then commit it."""
    from quant.research import create_note

    exp_ids = [int(e) for e in experiments.split(",") if e.strip()]
    syms = tuple(s.strip().upper() for s in symbols.split(",") if s.strip())
    path = create_note(title, status=status, strategy=strategy, symbols=syms,
                       experiments=exp_ids, notes_dir=notes_dir or None)
    typer.echo(f"note created -> {path}")
    typer.echo("fill in the sections, then commit it (failed ideas are worth the most)")


@note_app.command("list")
def note_list(
    status: str = typer.Option("", help="filter: idea | testing | adopted | rejected"),
    notes_dir: str = typer.Option("", "--dir", help="notes directory (default: research_notes/)"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """List knowledge-base notes, newest first."""
    from quant.research import list_notes

    if as_json:
        from quant.readapi import notes_list

        _emit_json("note.list", notes_list(status=status or None,
                                           notes_dir=notes_dir or None))
        return
    notes = list_notes(notes_dir or None, status=status or None)
    if not notes:
        typer.echo("no notes yet - start one with: quant note new \"my idea\"")
        return
    typer.echo(f"\n{len(notes)} note(s):\n")
    for n in notes:
        exp = f" exp={','.join(map(str, n.experiments))}" if n.experiments else ""
        strat = f" [{n.strategy}]" if n.strategy else ""
        typer.echo(f"  {n.created}  {n.status:<8} {n.title}{strat}{exp}")
        typer.echo(f"             -> {n.path.name}")


@app.command()
def paper(
    symbol: str,
    strategy: str = typer.Option("ma_cross", help="strategy name"),
    params: str = typer.Option("", help="e.g. 'lookback=100'"),
    start: str = typer.Option("2020-01-01", help="YYYY-MM-DD"),
    timeframe: str = typer.Option("1d"),
    cash: float = typer.Option(100_000),
    fraction: float = typer.Option(0.95, help="fraction of cash per entry"),
    max_position_notional: float = typer.Option(0, help="position value cap (0=off)"),
    max_daily_loss: float = typer.Option(0, help="daily loss kill-switch (0=off)"),
    stop_loss: float = typer.Option(0, help="stop-loss %% below entry, e.g. 0.05 (0=off)"),
    take_profit: float = typer.Option(0, help="take-profit %% above entry, e.g. 0.10 (0=off)"),
    trailing_stop: bool = typer.Option(False, "--trailing-stop", help="stop trails the high"),
    plot: bool = typer.Option(False, "--plot", help="save equity/drawdown HTML"),
    journal: bool = typer.Option(True, help="record the session to the SQLite journal"),
) -> None:
    """Paper-trade a strategy through the full Signal->Risk->Order->Broker pipeline."""
    from quant.execution import PaperBroker, TradeJournal, run_paper_session
    from quant.risk import BracketConfig, FixedFractionRisk, RiskGate, RiskLimits
    from quant.strategies.registry import get_strategy_cls

    parsed_params = _parse_params(params)
    data = _load(symbol, start, timeframe)
    strat = get_strategy_cls(strategy)(**parsed_params)

    broker = PaperBroker(cash=cash)
    risk = FixedFractionRisk(fraction=fraction)
    gate = RiskGate(RiskLimits(
        enabled=True,
        max_position_notional=max_position_notional,
        max_daily_loss=max_daily_loss,
    ))
    bracket_cfg = BracketConfig(stop_pct=stop_loss, take_pct=take_profit, trailing=trailing_stop)
    res = run_paper_session(strat, data, symbol, broker=broker, risk_manager=risk,
                            gate=gate, bracket_cfg=bracket_cfg, timeframe=timeframe)

    typer.echo(f"\n[PAPER] {strat}  on  {symbol}  "
               f"({len(data)} bars, {data.index[0].date()} -> {data.index[-1].date()})")
    if bracket_cfg.active:
        typer.echo(f"  bracket             : stop={stop_loss or '-'} take={take_profit or '-'}"
                   f"{' trailing' if trailing_stop else ''}")
    for k in ["final_equity", "total_return_pct", "cagr_pct", "sharpe", "max_drawdown_pct", "num_trades"]:
        typer.echo(f"  {k:<20}: {res.metrics.get(k)}")
    typer.echo(f"  fills               : {len(res.fills)}")
    typer.echo(f"  exits by reason     : {res.exit_reasons}")
    typer.echo(f"  blocked by risk gate: {len(res.blocked)}")
    for ts, reason in res.blocked[:5]:
        typer.echo(f"    - {ts.date()} {reason}")
    if len(res.blocked) > 5:
        typer.echo(f"    ... and {len(res.blocked) - 5} more")
    pos = res.final_positions
    typer.echo("  open positions      : "
               + (", ".join(f"{p.symbol} {p.qty:.2f}@{p.avg_price:.2f}" for p in pos) or "none"))

    if journal:
        with TradeJournal() as tj:
            sid = tj.record_session(res, symbol=symbol, strategy=strategy,
                                    params=parsed_params, init_cash=cash, mode="paper")
        typer.echo(f"  journaled as session: #{sid}  (view: quant journal --session {sid})")

    if plot:
        from quant.backtest.base import BacktestResult
        from quant.backtest.plots import plot_equity

        wrapped = BacktestResult(equity_curve=res.equity_curve, engine="paper")
        path = plot_equity({"paper": wrapped},
                           out_path=f"reports/paper_{symbol}_{strategy}.html",
                           title=f"[paper] {strat.name} on {symbol}")
        typer.echo(f"\nEquity/drawdown chart -> {path}")


@app.command()
def sweep(
    symbol: str,
    strategy: str = typer.Option("ma_cross", help="strategy name"),
    grid: str = typer.Option("", help="override, e.g. 'fast=5,10;slow=50,100' (default: strategy grid)"),
    start: str = typer.Option("2020-01-01", help="YYYY-MM-DD"),
    timeframe: str = typer.Option("1d"),
    sort_by: str = typer.Option("sharpe", help="sharpe | total_return_pct | max_drawdown_pct"),
    top: int = typer.Option(10, help="rows to print"),
    heatmap: bool = typer.Option(True, help="save a heatmap PNG under reports/"),
) -> None:
    """Vectorized parameter sweep over a strategy's grid; prints the top combos."""
    from quant.backtest.optimize import sweep
    from quant.backtest.plots import plot_heatmap
    from quant.strategies.registry import get_strategy_cls

    strat_cls = get_strategy_cls(strategy)
    data = _load(symbol, start, timeframe)
    results = sweep(strat_cls, data, grid=_parse_grid(grid), sort_by=sort_by, timeframe=timeframe)

    typer.echo(f"\n{strategy} sweep on {symbol}: {len(results)} combos, ranked by {sort_by}\n")
    typer.echo(results.head(top).to_string())
    best = results.iloc[0]
    param_cols = [c for c in results.columns
                  if c not in {"total_return_pct", "sharpe", "max_drawdown_pct", "num_trades"}]
    best_desc = " ".join(f"{c}={best[c]}" for c in param_cols)
    typer.echo(f"\nBest: {best_desc} sharpe={best.sharpe} "
               f"return={best.total_return_pct}% dd={best.max_drawdown_pct}%")

    csv_path = Path("reports") / f"sweep_{symbol}_{strategy}_{sort_by}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(csv_path)
    typer.echo(f"Full results saved -> {csv_path}")

    if heatmap:
        try:
            path = plot_heatmap(results, metric=sort_by,
                                out_path=f"reports/heatmap_{symbol}_{strategy}_{sort_by}.html",
                                title=f"{symbol} {strategy} - {sort_by}")
            typer.echo(f"Heatmap saved -> {path}")
        except Exception as exc:  # plotting is a bonus; never fail the sweep on it
            typer.echo(f"(heatmap skipped: {type(exc).__name__}: {exc})")


@app.command()
def walkforward(
    symbol: str,
    strategy: str = typer.Option("ma_cross", help="strategy name"),
    grid: str = typer.Option("", help="override grid (default: strategy grid)"),
    start: str = typer.Option("2015-01-01", help="YYYY-MM-DD (longer history = more folds)"),
    timeframe: str = typer.Option("1d"),
    train_bars: int = typer.Option(504, help="train window length (bars)"),
    test_bars: int = typer.Option(126, help="test window length (bars)"),
    sort_by: str = typer.Option("sharpe"),
    engine: str = typer.Option("vectorbt", help="OOS engine: vectorbt | backtrader "
                                                 "(optimization always uses vectorbt)"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Walk-forward validation: optimize on train, score on unseen test, roll forward."""
    from quant.backtest.walkforward import summarize, walk_forward
    from quant.strategies.registry import get_strategy_cls

    strat_cls = get_strategy_cls(strategy)
    data = _load(symbol, start, timeframe)
    wf = walk_forward(strat_cls, data, grid=_parse_grid(grid),
                      train_bars=train_bars, test_bars=test_bars, sort_by=sort_by,
                      timeframe=timeframe, engine_cls=_engine_cls(engine))

    s = summarize(wf)
    eff = s["wf_efficiency"]
    verdict = ("robust" if eff >= 0.5 else "fragile/overfit" if eff >= 0 else "broken OOS")
    csv_path = Path("reports") / f"walkforward_{symbol}_{strategy}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    wf.to_csv(csv_path, index=False)

    if as_json:
        _emit_json("walkforward", {
            "symbol": symbol, "strategy": strategy,
            "train_bars": train_bars, "test_bars": test_bars,
            "folds": _df_records(wf), "summary": s, "verdict": verdict,
            "csv_path": str(csv_path),
        })
        return

    typer.echo(f"\nWalk-forward [{strategy}] on {symbol}: {len(wf)} folds "
               f"(train={train_bars}, test={test_bars} bars)\n")
    typer.echo(wf.to_string(index=False))
    typer.echo("\n--- robustness summary ---")
    for k, v in s.items():
        typer.echo(f"{k:<22}: {v}")
    typer.echo(f"\nVerdict: WF efficiency {eff} -> {verdict} "
               f"(OOS Sharpe {s['mean_oos_sharpe']} vs IS {s['mean_is_sharpe']})")
    typer.echo(f"Folds saved -> {csv_path}")


@app.command()
def portfolio(
    config: str = typer.Option("", help="portfolio JSON file (see portfolios/example.json)"),
    legs: str = typer.Option("", help="inline legs: 'SPY:momentum:0.5:lookback=100; QQQ:ma_cross:0.5:fast=20,slow=50'"),
    cash: float = typer.Option(100_000),
    start: str = typer.Option("2020-01-01", help="YYYY-MM-DD"),
    timeframe: str = typer.Option("1d"),
    engine: str = typer.Option("vectorbt", help="backtest engine: vectorbt | backtrader"),
    plot: bool = typer.Option(False, "--plot", help="save a combined+legs equity HTML"),
) -> None:
    """Allocate capital across several strategies and report the blended result.

    Provide either --config (a JSON file) or --legs (inline). Shows combined
    metrics, per-leg metrics, the leg-return correlation, and how much the blend
    beat the weighted-average of its legs (the diversification benefit).
    """
    from quant.portfolio import load_portfolio_config, run_portfolio

    if config:
        cfg = load_portfolio_config(config)
        name, leg_list = cfg["name"], cfg["legs"]
        cash, start, timeframe = cfg["cash"], cfg["start"], cfg["timeframe"]
    elif legs:
        name, leg_list = "inline", _parse_legs(legs)
    else:
        raise typer.BadParameter("give --config FILE or --legs '...'")

    res = run_portfolio(leg_list, cash=cash, start=start, timeframe=timeframe,
                        engine_cls=_engine_cls(engine))

    typer.echo(f"\n[PORTFOLIO {name}]  {len(res.legs)} legs, init cash {res.init_cash:,.0f}, "
               f"from {start} ({timeframe})")
    typer.echo("\nper-leg (weight, on its own capital share):")
    typer.echo(f"  {'leg':<22}{'weight':>8}{'return%':>10}{'sharpe':>9}{'maxDD%':>9}")
    for leg in res.legs:
        label = f"{leg.symbol}:{leg.strategy}"
        m = res.leg_metrics[label]
        typer.echo(f"  {label:<22}{leg.weight:>8.2f}{str(m.get('total_return_pct')):>10}"
                   f"{str(m.get('sharpe')):>9}{str(m.get('max_drawdown_pct')):>9}")

    m = res.metrics
    typer.echo("\ncombined portfolio:")
    for k in ["final_equity", "total_return_pct", "cagr_pct", "sharpe", "max_drawdown_pct"]:
        typer.echo(f"  {k:<20}: {m.get(k)}")

    typer.echo("\nleg-return correlation:")
    typer.echo(res.correlation.to_string())

    dr = res.diversification_ratio
    if dr is not None:
        verdict = ("diversification helped" if dr > 1.05
                   else "no real benefit" if dr >= 0.95 else "blend underperformed parts")
        typer.echo(f"\nblended Sharpe {m.get('sharpe')} vs weighted-avg leg Sharpe "
                   f"{res.weighted_avg_sharpe}  ->  ratio {dr} ({verdict})")

    if plot:
        from quant.backtest.base import BacktestResult
        from quant.backtest.plots import plot_equity

        curves = {name: BacktestResult(equity_curve=res.equity_curve, engine="portfolio")}
        path = plot_equity(curves, out_path=f"reports/portfolio_{name}.html",
                           title=f"portfolio: {name}")
        typer.echo(f"\nEquity/drawdown chart -> {path}")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="bind address (use 0.0.0.0 to expose on LAN)"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, "--reload", help="auto-reload on code changes (dev)"),
) -> None:
    """Launch the read-only results dashboard (backtest / portfolio / journal in the browser)."""
    try:
        import uvicorn
    except ModuleNotFoundError:
        typer.echo('web deps not installed - run:  pip install -e ".[web]"')
        raise typer.Exit(code=1) from None

    typer.echo(f"dashboard : http://{host}:{port}")
    typer.echo(f"API docs  : http://{host}:{port}/docs")
    typer.echo("read-only - no order routing here (live trading stays in the CLI). Ctrl+C to stop.")
    uvicorn.run("quant.web.app:app", host=host, port=port, reload=reload)


@app.command()
def mcp() -> None:
    """Launch the READ-ONLY MCP server (stdio) so AI agents can query the system.

    Monitoring & research tools only - deliberately NO trading actions exist
    there (see src/quant/mcp_server.py). Claude Code picks it up via the
    committed .mcp.json; other clients can run this command directly.
    """
    try:
        from quant.mcp_server import main
    except ModuleNotFoundError:
        typer.echo('mcp deps not installed - run:  pip install -e ".[mcp]"')
        raise typer.Exit(code=1) from None
    main()


@app.command()
def reconcile(
    broker: str = typer.Option("alpaca", help="alpaca | paper"),
    alert: bool = typer.Option(False, "--alert", help="also send an alert on any issue"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Reconcile the broker's book against the journal (untracked/unprotected/orphan)."""
    from dataclasses import asdict

    from quant.execution import TradeJournal
    from quant.ops.notify import get_notifier
    from quant.ops.reconcile import reconcile as run_reconcile

    brk = _live_broker(broker)
    with TradeJournal() as tj:
        rep = run_reconcile(brk, tj)
    if as_json:
        _emit_json("reconcile", {
            "summary": rep.summary(), "checked_at": rep.checked_at,
            "positions": rep.positions, "issues": [asdict(i) for i in rep.issues],
        }, ok=rep.ok)
    else:
        typer.echo(f"\n{rep.summary()}  (checked {rep.checked_at})")
        typer.echo("positions: " + (", ".join(f"{k} {v:g}" for k, v in rep.positions.items()) or "none"))
        for i in rep.issues:
            typer.echo(f"  [{i.severity}] {i.detail}")
    if alert and rep.issues:
        get_notifier().send("CRITICAL" if not rep.ok else "WARN", "Reconcile", rep.summary())
    raise typer.Exit(code=0 if rep.ok else 1)


@app.command()
def report(
    broker: str = typer.Option("alpaca", help="alpaca | paper"),
    alert: bool = typer.Option(False, "--alert", help="also push the report via the notifier"),
) -> None:
    """Daily operations report: positions, today's orders, blocked, reconcile status."""
    from quant.execution import TradeJournal
    from quant.ops.notify import get_notifier
    from quant.ops.report import daily_report

    brk = _live_broker(broker)
    with TradeJournal() as tj:
        text = daily_report(brk, tj)
    typer.echo(text)
    if alert:
        get_notifier().info("Daily report", text)


@app.command("alert-test")
def alert_test() -> None:
    """Send a test alert through the configured notifier (verify Telegram wiring)."""
    from quant.ops.notify import get_notifier

    n = get_notifier()
    ok = n.info("alert test", "if you see this in Telegram, alerts are wired correctly")
    typer.echo(f"sent via {type(n).__name__}: {'ok' if ok else 'failed (see log)'}")


@app.command()
def oms(
    broker: str = typer.Option("alpaca", help="alpaca | paper (only needed with --sync)"),
    sync: bool = typer.Option(False, "--sync", help="poll the broker and advance order states first"),
    limit: int = typer.Option(20, help="how many recent orders to list"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Order lifecycle: list tracked orders and their state (optionally sync from the broker)."""
    from quant.execution import TradeJournal
    from quant.ops.oms import OMS

    with TradeJournal() as tj:
        synced = None
        if sync:
            synced = OMS(tj).sync(_live_broker(broker))
            if not as_json:
                typer.echo(f"synced: {synced} order(s) advanced\n")
        if as_json:
            from quant.readapi import orders_snapshot

            _emit_json("oms", {"synced": synced, **orders_snapshot(limit=limit)})
            return
        rows = tj.orders(limit=limit)
        cols = ["id", "symbol", "side", "qty", "status", "intended_price",
                "avg_fill_price", "filled_qty", "broker_order_id"]
        if rows.empty:
            typer.echo("no orders tracked yet - place one via `quant live --execute`")
            return
        typer.echo(f"Tracked orders (newest first), db: {tj.path}\n")
        typer.echo(rows[cols].to_string(index=False))


@app.command()
def tca(
    strategy: str = typer.Option("", help="filter to one strategy (blank = all)"),
    limit: int = typer.Option(1000, help="how many recent orders to analyse"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Transaction cost analysis: slippage (intended vs actual fill) + commissions."""
    from quant.execution import TradeJournal
    from quant.ops.tca import tca_report

    if as_json:
        from quant.readapi import tca_snapshot

        _emit_json("tca", tca_snapshot(strategy=strategy or None, limit=limit))
        return
    with TradeJournal() as tj:
        rep = tca_report(tj, strategy=strategy or None, limit=limit)
    cols = ["symbol", "side", "filled_qty", "intended_price", "avg_fill_price",
            "slippage_bps", "total_cost_usd"]
    avail = [c for c in cols if c in rep.per_order.columns]
    typer.echo("\n" + rep.summary())
    if rep.n_filled:
        typer.echo("\nper-order:")
        typer.echo(rep.per_order[avail].to_string(index=False))


@app.command()
def health(
    max_silence_minutes: int = typer.Option(1500, help="flag a component silent longer than this (~25h)"),
    alert: bool = typer.Option(False, "--alert", help="send an alert if health is degraded"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """System health: per-component heartbeats and missed-run detection."""
    from quant.ops.notify import get_notifier
    from quant.readapi import health_snapshot

    h = health_snapshot(max_silence_minutes=max_silence_minutes)
    ok = bool(h["ok"])
    if as_json:
        data = {k: v for k, v in h.items() if k != "ok"}
        _emit_json("health", data, ok=ok)
    else:
        typer.echo("\n" + h["summary"])
        for c in h["components"]:
            age = f"{c['age_minutes']:.0f}m ago" if c["age_minutes"] is not None else "never"
            flag = "  <<< STALE" if c["stale"] else ""
            typer.echo(f"  {c['component']:<12} {c['status']:<8} {age}{flag}  {c['detail']}")
    if alert and not ok:
        get_notifier().critical("Health degraded", "; ".join(h["problems"]))
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def drift(
    symbol: str,
    strategy: str = typer.Option("ma_cross", help="strategy name"),
    params: str = typer.Option("", help="e.g. 'lookback=100'"),
    start: str = typer.Option("2023-01-01", help="window start for the expected signals"),
    timeframe: str = typer.Option("1d"),
    min_agreement: float = typer.Option(0.8, help="flag drift below this backtest/live agreement"),
    alert: bool = typer.Option(False, "--alert", help="send an alert if drift is detected"),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),
) -> None:
    """Backtest-vs-live drift: do the live runner's actions match what the backtest expected?"""
    from quant.execution import TradeJournal
    from quant.ops.drift import decision_drift
    from quant.ops.notify import get_notifier
    from quant.strategies.registry import get_strategy_cls

    data = _load(symbol, start, timeframe)
    strat = get_strategy_cls(strategy)(**_parse_params(params))
    with TradeJournal() as tj:
        live_log = tj.live_log(limit=10_000, symbol=symbol, strategy=strategy)
    rep = decision_drift(data, strat, live_log, symbol=symbol, strategy_name=strategy,
                         min_agreement=min_agreement)
    if as_json:
        _emit_json("drift", {
            "summary": rep.summary(), "symbol": rep.symbol, "strategy": rep.strategy,
            "n_bars": rep.n_bars, "n_expected": rep.n_expected, "n_live": rep.n_live,
            "n_matched": rep.n_matched, "agreement": rep.agreement,
            "min_agreement": rep.min_agreement,
            "missed": [[str(d), a] for d, a in rep.missed],
            "extra": [[str(d), a] for d, a in rep.extra],
        }, ok=rep.ok)
        if alert and not rep.ok:
            get_notifier().warn("Backtest/live drift", rep.summary())
        raise typer.Exit(code=0 if rep.ok else 1)
    typer.echo("\n" + rep.summary())
    if rep.missed:
        typer.echo(f"\nmissed (backtest would trade, live didn't): {len(rep.missed)}")
        for d, a in rep.missed[:10]:
            typer.echo(f"  {d} {a}")
    if rep.extra:
        typer.echo(f"\nextra (live traded, backtest wouldn't): {len(rep.extra)}")
        for d, a in rep.extra[:10]:
            typer.echo(f"  {d} {a}")
    if alert and not rep.ok:
        get_notifier().warn("Backtest/live drift", rep.summary())
    raise typer.Exit(code=0 if rep.ok else 1)


@app.command()
def integrity(
    symbol: str = typer.Argument("", help="symbol to check (blank = list recorded events)"),
    start: str = typer.Option("2020-01-01", help="history start for the comparison download"),
    timeframe: str = typer.Option("1d"),
    check: bool = typer.Option(False, "--check",
                               help="re-download and compare to the cache (non-destructive)"),
) -> None:
    """Point-in-time integrity: detect when a data refresh rewrote settled history
    (splits/adjustments). `--check SYMBOL` compares a fresh pull to the cache WITHOUT
    overwriting it; with no symbol, lists mutation events the loader has recorded."""
    from quant.data.integrity import (
        detect_history_mutation,
        read_mutation_events,
        record_mutation_event,
    )

    if check and symbol:
        from quant.data.feeds.yfinance_feed import YFinanceFeed
        from quant.data.storage import get_store

        store = get_store()
        old = store.load(symbol, timeframe) if store.exists(symbol, timeframe) else None
        if old is None or old.empty:
            typer.echo(f"no cache for {symbol} {timeframe} - download it first (quant download)")
            raise typer.Exit(code=1)
        start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
        fresh = YFinanceFeed().get_history(symbol, start=start_dt, timeframe=timeframe)
        rep = detect_history_mutation(old, fresh, symbol=symbol, timeframe=timeframe)
        typer.echo("\n" + rep.summary())
        for d, oldc, newc in rep.samples:
            typer.echo(f"  {d}: {oldc:.4f} -> {newc:.4f}")
        if rep.mutated:
            record_mutation_event(rep)
            typer.echo("  (recorded to integrity_events.csv; cache NOT overwritten)")
        raise typer.Exit(code=1 if rep.mutated else 0)

    events = read_mutation_events()
    if events.empty:
        typer.echo("no history-mutation events recorded yet "
                   "(the loader records them when a re-download rewrites settled bars)")
    else:
        typer.echo(f"\nRecorded history-mutation events ({len(events)}):\n")
        typer.echo(events.to_string(index=False))


if __name__ == "__main__":
    app()
