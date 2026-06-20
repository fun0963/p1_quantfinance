# Quant — Modular Quantitative Trading System

[![CI](https://github.com/fun0963/p1_quantfinance/actions/workflows/ci.yml/badge.svg)](https://github.com/fun0963/p1_quantfinance/actions/workflows/ci.yml)

A modular, debuggable quantitative trading system for **US equities / ETFs**,
spanning **research → backtesting → paper execution → deployment**. Every layer
sits behind a clean interface, and order routing is paper-only + dry-run by default.

- **Language:** Python 3.11+
- **Data / broker:** [Alpaca](https://alpaca.markets/) (paper + data) · [yfinance](https://github.com/ranaroussi/yfinance) (free EOD)
- **Backtesting (dual engine):** [VectorBT](https://vectorbt.dev/) (research/sweeps) · [Backtrader](https://www.backtrader.com/) (event-driven validation)
- **Stack:** pandas · pydantic · loguru · typer · pytest

---

## Design principles

1. **Layered & decoupled** — data, strategy, backtest, execution, risk each
   depend on *interfaces*, never on a concrete vendor. Swap Alpaca↔yfinance or
   VectorBT↔Backtrader without touching strategy code.
2. **Write a strategy once** — a `BaseStrategy` produces signals from a price
   frame; the same class runs on either backtest engine and (later) live.
3. **Debuggable by construction** — every vendor lives behind an adapter (easy
   to mock), `src/` layout avoids import pollution, `loguru` gives structured
   logs, `tests/` mirror `src/`.
4. **Safe by default** — live order routing is hard-disabled in phase 1
   (`ALPACA_PAPER` must be `true`); secrets live only in `.env`.

---

## System architecture

```
                  ┌──────────────────────────────────────────┐
                  │            CLI / Config layer              │   typer + pydantic-settings
                  │     quant info | download | (backtest)     │
                  └──────────────────────────────────────────┘
                                     │
      ┌────────────┬─────────────────┼──────────────────┬───────────────┐
      ▼            ▼                 ▼                  ▼               ▼
 ┌─────────┐  ┌──────────┐    ┌────────────┐    ┌────────────┐   ┌──────────┐
 │  Data   │  │ Strategy │    │  Backtest  │    │ Execution  │   │   Risk   │
 │         │  │          │    │ (dual eng) │    │            │   │          │
 │ DataFeed│─▶│BaseStrat │─▶ │ VectorBT   │    │  Broker    │   │RiskMgr   │
 │  ├Alpaca│  │ .generate│    │ Backtrader │    │  └Alpaca   │   │ └Fixed   │
 │  └YFin  │  │  _signals│    │            │    │   (paper)  │   │   Frac   │
 │ Storage │  └──────────┘    └────────────┘    └────────────┘   └──────────┘
 │ └Parquet│        │                │                │               │
 └─────────┘        └────────────────┴────────────────┴───────────────┘
                       shared core: types · events · utils(logging)

 Research flow :  DataFeed ─▶ Strategy ─▶ VectorBT ─▶ (survivors) ─▶ Backtrader
 Live  flow    :  MarketEvent ─▶ Strategy ─▶ Risk ─▶ Order ─▶ Broker ─▶ Fill   (phase 3)
```

### Directory layout

```
p1_quantfinance/
├── config/                 # pydantic settings (single source of truth)
├── src/quant/
│   ├── core/               # domain types (Bar/Signal/Order) + events
│   ├── data/
│   │   ├── feeds/          # DataFeed abstract + Alpaca / yfinance adapters
│   │   ├── storage/        # BarStore interface: ParquetStore | TimescaleStore (get_store)
│   │   ├── loaders.py      # load_bars: cache-or-download (date-aware)
│   │   └── quality.py      # check_bars: NaNs / gaps / OHLC / splits
│   ├── strategies/         # BaseStrategy + ma_cross, momentum, registry
│   ├── backtest/           # engines (VectorBT/Backtrader) + metrics, optimize, walkforward
│   ├── portfolio/          # multi-strategy capital allocation + diversification view
│   ├── execution/          # Broker abstract, Alpaca/paper brokers, live runner, scheduler, journal
│   ├── risk/               # RiskManager + FixedFraction sizer, RiskGate, brackets
│   ├── utils/              # loguru logging
│   └── cli.py              # typer entrypoint (info/download/backtest/sweep/walkforward/paper/live/schedule/portfolio…)
├── scripts/                # one-off ops (bulk history download)
├── notebooks/              # research notebooks
├── tests/                  # pytest, mirrors src/
├── data/  logs/            # gitignored runtime output
├── pyproject.toml
└── .env.example
```

---

## Quickstart

```bash
# 1. Create venv & install (editable + dev tools)
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell
pip install -e ".[dev]"

# 2. Configure
copy .env.example .env             # then fill in Alpaca PAPER keys (optional for yfinance)

# 3. Sanity check
quant info
pytest                             # smoke tests run with no keys/network

# 4. Pull some data (free, no keys)
quant download SPY --start 2020-01-01
```

**How to change symbol / strategy / run the research workflow:** see
[docs/USAGE.md](docs/USAGE.md).

---

## Continuous integration & pushing to GitHub

Every push / PR runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml) —
**ruff** (lint + import order), **mypy** (types), and **pytest** (incl. the backtest
regression suite) across Python 3.11 / 3.12. Run the identical gate locally before
pushing:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci.ps1   # ruff -> mypy -> pytest
```

First-time setup (the repo is already initialized with a first commit on `main`):

```bash
# Create an EMPTY GitHub repo (no README/.gitignore), then wire it up & push:
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

CI triggers on that push — watch it under the repo's **Actions** tab (and the badge
above). Secrets stay local: `.env`, `data/`, `logs/`, the `.venv/`, and the bulky
`01_ref/` reference project are all git-ignored.

---

## Development Roadmap

### ✅ Phase 1 — Architecture & environment
- [x] Tool selection & layered architecture
- [x] Project skeleton, config, logging, CLI
- [x] Abstract interfaces: `DataFeed`, `BaseStrategy`, `BacktestEngine`, `Broker`, `RiskManager`
- [x] Alpaca + yfinance data adapters, parquet store
- [x] Smoke tests, paper-only safety guard

### ✅ Phase 2 — Research & backtesting
- [x] First concrete strategy: `MACrossStrategy` on `BaseStrategy`
- [x] Finish `BacktraderEngine` bridge (event-driven, cheat-on-close to match VectorBT)
- [x] Engine-agnostic metrics (`compute_metrics`: return, CAGR, Sharpe, max drawdown)
- [x] `quant backtest` CLI — runs a strategy on both engines and prints a comparison
- [x] Strategy-agnostic research funnel via a **registry** + per-strategy `default_grid()`
- [x] Second strategy: `MomentumStrategy` (time-series momentum) — same interface
- [x] VectorBT parameter sweep (`quant sweep`) — vectorized grid, ranked, CSV + heatmap
- [x] Walk-forward / out-of-sample validation (`quant walkforward`) — IS vs OOS Sharpe, WF efficiency
- [x] Data-quality checks (`quant check`) — NaNs, gaps, OHLC consistency, unadjusted splits
- [x] Interactive plots via **plotly** (`backtest --plot`, `sweep` heatmap) — equity/drawdown
      & param heatmaps as self-contained HTML (pure-Python, no native deps)

> Validation snapshots (SPY, daily):
> - `quant backtest SPY --strategy ma_cross --params fast=20,slow=50` — both engines
>   agree (Sharpe identical, returns within ~1%); residual gap is execution modeling.
> - `quant walkforward SPY --strategy ma_cross` 2015–2026 → WF efficiency **0.64**.
> - `quant walkforward SPY --strategy momentum` → WF efficiency **0.98** (OOS Sharpe
>   1.26 vs IS 1.29): momentum is markedly more robust than MA-cross here.

> Plotting uses **plotly → self-contained HTML** (open in any browser, hover &
> zoom), chosen over matplotlib to avoid its native `ft2font` DLL dependency,
> which fails to load on some Windows setups even with the VC++ runtime installed.

### ✅ Phase 3 — Live / paper execution
- [x] `RiskGate` pre-trade safety (`risk/gate.py`) — kill-switch / lock, per-order
      qty & notional caps, position cap, daily-loss limit (pattern from a production terminal)
- [x] `PaperBroker` (`execution/paper_broker.py`) — in-memory fill sim, no API keys, testable
- [x] `run_paper_session` (`execution/session.py`) — full **Signal → Risk size → Risk
      gate → Broker fill** loop, replayable on history; `quant paper SYMBOL ...`
- [x] Bracket / OCO exits (`risk/bracket.py`) — stop-loss, take-profit, trailing stop,
      armed on entry fill, intrabar checked, conservative tie-break; `--stop-loss/--take-profit/--trailing-stop`
- [x] Persistent trade journal (`execution/journal.py`, SQLite) — every session, fill &
      blocked order recorded with timestamps; review via `quant journal [--session N]`
- [x] `AlpacaBroker` connection check (`quant account`) + live runner
      (`execution/live_runner.py`, `quant live`) — **dry-run by default** (`--execute`
      to submit), reconciles position from the broker, logs every decision (`quant journal --live`)
- [x] Live runner **target-state reconciliation** (`--mode target`, default) — acts on the
      strategy's *desired* position as of the latest bar (not just the crossover edge), so a
      once-a-day run stays in sync even starting mid-trend; `--mode signal` keeps edge-only behaviour
- [x] Live protective stops via Alpaca **native bracket/OCO** (server-side) — `quant live
      --stop-loss --take-profit` brackets new entries; `quant protect SYMBOL --stop-loss
      --take-profit` attaches an OCO to an existing position. Both dry-run by default.
- [x] Scheduling (`quant schedule`, APScheduler) + Windows Task Scheduler wrapper
      (`scripts/daily_live.ps1`) — runs the live step each weekday after close; idempotent
      (target-state reconcile), dry-run default. See [docs/SCHEDULING.md](docs/SCHEDULING.md).
- [ ] (optional) websocket/intraday feed over `core/events.py`; alerting on fills

> `quant paper SPY --strategy momentum --plot` → full pipeline runs offline (paper
> broker). `--max-position-notional` / `--max-daily-loss` engage the risk gate;
> e.g. a $50k position cap blocks all entries (0 fills, equity untouched).
> `--stop-loss 0.05 --take-profit 0.15` on SPY momentum cut max drawdown from
> -13.5% to -9.1% (exit reasons are reported per run).

### ✅ Phase 4 — Hardening & scale
- [x] TimescaleDB storage backend (`data/storage/timescale_store.py`) behind a
      `BarStore` interface — `STORAGE_BACKEND=timescale` flips the whole system from
      the local parquet cache to a time-partitioned `bars` hypertable (idempotent
      upserts). `get_store()` is the config-driven factory; default stays parquet.
- [x] Portfolio-level allocation across strategies (`portfolio/`) — capital split by
      weight across (symbol × strategy) legs, equity curves combined, with a
      diversification view (leg-return correlation + blended vs weighted-avg Sharpe);
      `quant portfolio --config portfolios/example.json` (data-as-config) or `--legs`
- [x] Containerization — `Dockerfile` + `docker-compose.yml` bring up TimescaleDB +
      the `quant` CLI; one-shot `docker compose run --rm quant ...` and an opt-in
      `scheduler` service (`--profile live`, dry-run by default). See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- [x] CI — GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)): ruff
      (lint + import order) + mypy (types) + pytest across Python 3.11/3.12. Run the same
      gate locally with `scripts/ci.ps1`. Lint/type config lives in `pyproject.toml`.
- [x] Backtest regression suite ([tests/test_regression.py](tests/test_regression.py)) —
      golden-master metrics on a fixed-seed synthetic series (no network/data files), plus a
      VectorBT↔Backtrader agreement check, so a refactor or library bump that silently moves
      results fails CI instead of slipping through.

> `quant portfolio --config portfolios/example.json` (SPY momentum + QQQ ma_cross,
> 50/50) → blended Sharpe 1.04 vs 1.0 weighted-avg, max drawdown -19% between the
> legs' -14%/-27%: legs correlate 0.7, so the blend smooths drawdown more than it
> lifts Sharpe — pick lower-correlation legs for real diversification.

---

## Conventions
- **Type-safe config** via `config.get_settings()` — never read `os.environ` directly.
- **Logging** via `quant.utils.get_logger(__name__)`; call `setup_logging()` once at entry.
- **New vendor?** Implement the relevant abstract base in its layer — nothing upstream changes.
- **Tests** mirror `src/` and must pass offline (mock vendors at the adapter boundary).
- **Before pushing**, run `scripts/ci.ps1` (ruff + mypy + pytest) — the same gate CI enforces.

> ⚠️ This is research/educational software, not financial advice. Order routing
> is **paper-only** (`ALPACA_PAPER` must be `true`) and **dry-run by default** —
> every live/schedule command needs an explicit `--execute` to submit anything.
```
