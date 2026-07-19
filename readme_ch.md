# 量化交易系統 — 中文使用說明

一套**模組化、可除錯**的美股 / ETF 量化交易系統,涵蓋
**研究 → 回測 → 紙上交易 → 實盤(Alpaca paper)→ 部署 → 網頁儀表盤**的完整流水線。

> ⚠️ **安全聲明**:本系統為研究 / 教育用途,非投資建議。下單為 **paper-only**(`ALPACA_PAPER=true` 才能跑)、且**預設 dry-run**(每個會送單的指令都要再加 `--execute` 才會真的下單)。

> 📁 相關文件:[walkthrough_ch.md](walkthrough_ch.md)(**走查規劃表:從發想到檢討一步一步**)、[README.md](README.md)(英文總覽)、[architecture_map_ch.md](architecture_map_ch.md)(**架構地圖,接手必讀**)、[audit_and_roadmap_ch.md](audit_and_roadmap_ch.md)(進度與路線圖)、[docs/GUIDE.md](docs/GUIDE.md)(端到端指南)、[docs/USAGE.md](docs/USAGE.md)(換標的/策略)、[docs/SCHEDULING.md](docs/SCHEDULING.md)(自動排程)、[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)(TimescaleDB/組合/Docker)。

---

## 一、專案地圖:每個檔案 / 資料夾在哪、做什麼

### 目錄總覽

```
p1_quantfinance/
├── config/settings.py        # 全系統唯一設定來源(pydantic + .env)
├── configs/strategies.json   # ⭐ 具名策略規格:params/風控/生命週期規則(版控)
├── src/quant/                # 主程式(約 6,400 行)
│   ├── cli.py                # ⭐ 進入點:26 個 typer 指令
│   ├── core/                 # 共通型別(Signal/Order)
│   ├── utils/                # loguru 日誌
│   ├── data/                 # 行情抓取、快取、品質、完整性
│   ├── strategies/           # 交易策略(純訊號邏輯)
│   ├── risk/                 # 風控:部位 sizing、否決閘門、停損停利
│   ├── backtest/             # 雙引擎回測、指標、掃描、報告
│   ├── portfolio/            # 多策略資金配置
│   ├── execution/            # 下單、實盤 runner、排程、交易紀錄
│   ├── ops/                  # 營運:告警、對帳、OMS、TCA、健康、偏差
│   ├── research/             # 研究紀律:實驗記錄系統
│   └── web/                  # 唯讀網頁儀表盤(FastAPI)
├── tests/                    # 39 個測試檔、273 個測試(鏡像 src/)
├── scripts/                  # ci.ps1(本機 CI)、trading.cmd/.ps1(一鍵啟停)、daily_live.ps1
├── docs/                     # GUIDE / USAGE / SCHEDULING / DEPLOYMENT
├── research_notes/           # ⭐ 研究知識庫:一個想法一頁(假設/做法/結果/結論)
├── portfolios/example.json   # 組合設定範例(設定是資料,不是程式)
├── data/                     # (git 忽略)parquet 快取、journal.db、experiments.db
├── reports/                  # (git 忽略)回測圖表、tear sheet、CSV
├── Dockerfile + docker-compose.yml   # 容器化(含 TimescaleDB)
└── .github/workflows/ci.yml  # CI:ruff + mypy + pytest(py3.11/3.12)
```

### 研究筆記清單(完整路徑)

檔名規則 `research_notes/YYYY-MM-DD-<短slug>.md`(slug 上限 40 字元,完整標題在檔內 frontmatter);
即時清單以 `quant note list` 為準。截至 2026-07-18:

| 完整路徑 | 狀態 | 一句話 |
|---------|------|--------|
| [research_notes/2026-07-17-momentum-beats-ma-cross-oos.md](research_notes/2026-07-17-momentum-beats-ma-cross-oos.md) | adopted | SPY 上 momentum 樣本外勝過 ma_cross |
| [research_notes/2026-07-17-frictionless-backtests-overstate.md](research_notes/2026-07-17-frictionless-backtests-overstate.md) | adopted | 零摩擦回測系統性高估報酬 |
| [research_notes/2026-07-17-lookback-100-survives.md](research_notes/2026-07-17-lookback-100-survives.md) | adopted | lookback=100 過成本+OOS 檢驗,定為 spy_momentum spec |
| [research_notes/2026-07-17-buffer-degenerates-to-hold.md](research_notes/2026-07-17-buffer-degenerates-to-hold.md) | rejected | buffer 參數退化成買進持有(num_trades=1 陷阱) |
| [research_notes/2026-07-17-1min-ma-cross-cost-dominated.md](research_notes/2026-07-17-1min-ma-cross-cost-dominated.md) | rejected | 天真 1min ma_cross 被成本吃光(-81.8%/6週) |
| [research_notes/2026-07-18-qqq-slippage-near-zero.md](research_notes/2026-07-18-qqq-slippage-near-zero.md) | adopted | 實測 QQQ 小額市價單滑價 ~0 bps(19/19 成交) |
| [research_notes/2026-07-18-gap-tradingview-ux.md](research_notes/2026-07-18-gap-tradingview-ux.md) | idea | 對照 TradingView 的人性化缺口(K 線進出點圖等) |
| [research_notes/2026-07-18-gap-report-metrics.md](research_notes/2026-07-18-gap-report-metrics.md) | adopted | 報告指標補完:benchmark 疊圖、rolling Sharpe、turnover、PSR 全落地 |
| [research_notes/2026-07-18-gap-ai-interface.md](research_notes/2026-07-18-gap-ai-interface.md) | adopted | AI 友善介面三部曲:--json / status 快照 / 唯讀 MCP,全部完成 |
| [research_notes/2026-07-18-gap-event-calendar.md](research_notes/2026-07-18-gap-event-calendar.md) | rejected | FOMC 盲點量化後不成立:暴露 -27bps/yr 噪音級、blackout 6.5 年綁一次 |
| [research_notes/2026-07-18-alpaca-moc-opg-orders.md](research_notes/2026-07-18-alpaca-moc-opg-orders.md) | rejected | MOC/OPG 對齊暫緩:實測隔夜價差均值≈0(SPY),是噪音不是偏差 |

### 各資料夾細節(由下層往上層)

| 位置 | 功能 | 重點檔案 |
|------|------|---------|
| `config/` | 設定唯一來源:Alpaca 金鑰、儲存後端、Telegram 告警、路徑,全部從 `.env` 讀 | `settings.py`(`get_settings()` 單例) |
| `src/quant/core/` | 跨層共用的領域型別,不依賴任何第三方 | `types.py`:`Signal`/`Order` + 三個 Enum |
| `src/quant/utils/` | 集中式日誌 | `logging.py`(`setup_logging`/`get_logger`) |
| `src/quant/data/feeds/` | 行情來源(可抽換):yfinance(研究預設)、Alpaca | `base.py`(`DataFeed` 介面)、`yfinance_feed.py`、`alpaca_feed.py`、`retry.py`(網路重試+退避) |
| `src/quant/data/storage/` | 歷史資料儲存(可抽換):本機 Parquet(預設,原子寫入+備份)或 TimescaleDB | `base.py`(`BarStore` 介面)、`parquet_store.py`、`timescale_store.py`、`__init__.py`(`get_store()` 工廠) |
| `src/quant/data/` | 載入與守門 | `loaders.py`(`load_bars` 快取優先下載;`fetch_bars` 各進入點共用)、`quality.py`(NaN/跳空/OHLC 檢查)、`integrity.py`(**偵測歷史被回溯改寫**,除權息陷阱) |
| `src/quant/strategies/` | 策略=純訊號邏輯,引擎無關;加新策略只要一個類別+註冊一行 | `base.py`(`BaseStrategy` 介面)、`ma_cross.py`、`momentum.py`、`registry.py`、`spec.py`(**讀 `configs/strategies.json` 的具名規格**) |
| `src/quant/risk/` | 下單前的三道防線 | `base.py`(`RiskManager` 部位 sizing)、`gate.py`(`RiskGate`:部位/日虧上限、kill-switch;**降險賣單永遠放行**)、`bracket.py`(停損停利) |
| `src/quant/backtest/` | 雙引擎回測與研究工具 | `base.py`(`BacktestEngine` 介面)、`vectorbt_engine.py`(向量化,快)、`backtrader_engine.py`(事件驅動,擬真)、`costs.py`(**成本/滑價模型,可用實盤 TCA 校準**)、`metrics.py`(Sharpe/Sortino/Calmar/月報酬…)、`optimize.py`(參數掃描)、`walkforward.py`(樣本外驗證)、`plots.py`、`report.py`(**一鍵 HTML tear sheet:K 線+進出點標記、權益疊 buy-and-hold+超額報酬列、rolling Sharpe(與 lifecycle 同窗同法)、PSR、年化換手**) |
| `src/quant/portfolio/` | 多策略資金配置、相關矩陣、分散化效益 | `portfolio.py`(`run_portfolio`,引擎可注入) |
| `src/quant/execution/` | 下單與實盤生命週期 | `base.py`(`Broker` 介面)、`paper_broker.py`(離線模擬)、`alpaca_broker.py`(paper 端點+bracket/OCO)、`session.py`(紙上交易)、`live_runner.py`(單根 K 決策+新鮮度閘)、`scheduler.py`(APScheduler 排程)、`journal.py`(SQLite 交易紀錄:sessions/fills/orders/heartbeats) |
| `src/quant/ops/` | 無人值守的營運後盾(全部 best-effort,絕不阻斷交易) | `notify.py`(Telegram/log 告警)、`reconcile.py`(帳 vs 紀錄對帳)、`oms.py`(訂單狀態機+稽核)、`tca.py`(滑價分析)、`health.py`(heartbeat 監控)、`drift.py`(回測 vs 實盤偏差)、`report.py`(每日報告) |
| `src/quant/research/` | 研究紀律層 | `experiments.py`(**實驗記錄系統**:每次回測自動存 git-hash/參數/資料窗/成本/指標到 `data/experiments.db`,防過擬合)、`lifecycle.py`(**事前寫死的晉升/退場規則**:trailing 視窗 Sharpe/回撤/活動度檢查)、`notes.py`(**研究知識庫**:`research_notes/` 一個想法一頁,frontmatter 可連回實驗 id) |
| `src/quant/web/` | 唯讀儀表盤,不放下單按鈕 | `app.py`(app 工廠)、`routes.py`(7 個 JSON 端點)、`schemas.py`(請求驗證)、`static/index.html`(單檔前端,plotly) |
| `tests/` | 測試鏡像 src/:單元+整合+golden 回歸(固定種子鎖數字)+雙引擎一致性;實盤路徑全部可離線測 | `test_regression.py`(改壞指標會被抓)、`test_scheduler.py`(離線實盤演練)等 28 檔 |

### 三條關鍵資料流(看懂就懂全系統)

```
研究:  quant backtest → load_bars(快取+品質+改寫偵測) → 策略訊號
        → 雙引擎(含成本/滑價) → 指標比較 → 自動記錄到實驗庫

紙上:  quant paper → 逐根 K:策略 → RiskManager sizing → RiskGate 否決
        → PaperBroker 成交 → Bracket 停損停利 → journal 紀錄

實盤:  quant schedule → 每交易日:OMS 同步 → 對帳(不符即停) →
        live runner(過期資料拒單) → RiskGate → Alpaca 下單
        → journal + heartbeat + Telegram 告警
```

---

## 二、目前已完成的事項

| 階段 | 內容 | 狀態 |
|------|------|------|
| **Phase 1 — 架構** | 模組化 `src/` 佈局、7 個抽象介面(DataFeed / BarStore / BaseStrategy / BacktestEngine / Broker / RiskManager / Notifier)、pydantic 設定、loguru 日誌、typer CLI | ✅ |
| **Phase 2 — 研究/回測** | 策略註冊表、**雙引擎回測**、引擎無關指標、**參數掃描**、**Walk-forward 樣本外驗證**、資料品質檢查、plotly 互動圖 | ✅ |
| **Phase 3 — 紙上/實盤** | 風控閘門、PaperBroker、bracket/OCO 停損停利、**SQLite 交易紀錄**、Alpaca paper、live runner(**預設 dry-run**)、自動排程 | ✅ |
| **Phase 4 — 強化/擴展** | TimescaleDB 後端、多策略組合、Docker 化、CI、回歸測試 | ✅ |
| **Phase 5 — 易用性/網頁** | 唯讀網頁儀表盤(Backtest / Portfolio / Sweep / Walk-forward / Journal 五分頁) | ✅ |
| **實盤安全修復(Batch 0)** | 8 個 P0 安全漏洞全修:新鮮度閘、假日略過、無訊號不清倉、出場前先取消 OCO、熔斷放行降險賣單…等 | ✅ |
| **營運層(Batch 1)** | Telegram 告警、下單前對帳、OMS 訂單狀態機、TCA 滑價分析、heartbeat 健康監控、回測vs實盤偏差、每日報告 | ✅ |
| **資料完整性(Batch 2)** | point-in-time 歷史改寫偵測、資料源重試/退避、timeframe 白名單、parquet 原子寫入+備份 | ✅ |
| **研究強化(近期)** | **成本/滑價模型**(可用實盤 TCA 校準)、**實驗記錄系統**(git-hash/參數/結果全留痕)、Sortino/Calmar、**一鍵 HTML tear sheet**、儀表盤滑價旋鈕 | ✅ |

**核心設計原則**:
- **分層解耦**:每個 vendor 都躲在抽象介面後面,換掉不影響上層(依賴單向、零循環 import)。
- **策略寫一次,到處跑**:同一個策略類別跑兩個回測引擎,也接實盤。
- **失敗預設安全**:過期資料拒單、對帳不符即停、止損永遠出得掉、告警永不 crash。
- **研究可稽核**:每次回測自動記錄(沒記錄的實驗等於沒做過);**201 個測試**全綠 + ruff/mypy 雙閘。

---

## 三、如何使用 + 有哪些功能

### 0. 安裝(做一次)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # 啟用 venv(之後可直接打 quant ...)
pip install -e ".[dev]"                 # 安裝套件 + 開發工具
pip install -e ".[web]"                 # 想用網頁儀表盤再加這個
copy .env.example .env                  # 填 Alpaca PAPER 金鑰(只做研究可先略過)

quant info                              # 確認設定與已註冊策略
```

> 沒啟用 venv 時的等效寫法:`& .\.venv\Scripts\python.exe -m quant.cli <指令>`

### 1. 指令總表(26 個)

**研究:**

| 指令 | 用途 |
|------|------|
| `quant info` | 列出設定與已註冊策略 |
| `quant download SYMBOL` | 下載並快取歷史資料 |
| `quant check SYMBOL` | 資料品質檢查(NaN / 跳空 / OHLC / 未還原分割) |
| `quant backtest [SYMBOL]` | 雙引擎回測比較;`--spec NAME` 從規格檔帶入全部參數、`--slippage-bps` 滑價、`--calibrate` 用實盤 TCA 校準成本、`--report` 出 tear sheet、預設自動記錄實驗(`--no-log` 關) |
| `quant sweep SYMBOL` | 向量化參數掃描 + 排名 + 熱力圖 |
| `quant walkforward SYMBOL` | 樣本外滾動驗證(`--engine backtrader` 可換引擎) |
| `quant portfolio` | 多策略資金配置 + 分散化分析 |
| `quant experiments` | **查詢實驗記錄**(`--strategy/--symbol` 過濾、`--id N` 看單筆) |
| `quant lifecycle NAME\|--all` | **策略健康檢查**:用規格檔裡事前寫死的規則(rolling Sharpe / 回撤 / 活動度)評估 trailing 視窗,breach 時 exit 1(可排程當閘門) |
| `quant note new/list` | **研究知識庫**:一個想法一頁(假設/做法/結果/結論),`--experiments` 連回實驗 id;`list --status rejected` 看失敗紀錄 |

**交易:**

| 指令 | 用途 |
|------|------|
| `quant paper SYMBOL` | 紙上交易完整流程(風控 + bracket + 紀錄) |
| `quant account` | 檢查 Alpaca paper 連線(唯讀) |
| `quant live [SYMBOL]` | 評估最新 K 並對齊部位(預設 dry-run,`--execute` 才送單);`--spec NAME` 從規格檔帶入策略+風控 |
| `quant protect SYMBOL` | 幫既有部位掛 OCO 停損停利(預設 dry-run) |
| `quant schedule [SYMBOL]` | 排程自動跑 live;`--spec` 可重複(一程序多策略);預設每日 `--at 16:10`,**盤中模式 `--every 5min`**(只在開市時段觸發,分鐘級策略用) |

**營運 / 監控:**

| 指令 | 用途 |
|------|------|
| `quant status` | **一發聚合快照**:帳戶+對帳+health+近期決策/訂單+TCA+specs(分區降級,broker 掛了也照出本地狀態;`--offline` 跳過網路) |
| `quant journal` | 查交易紀錄(`--session N` / `--live`) |
| `quant reconcile` | 對帳:券商實際部位 vs 交易紀錄(`--alert` 不符時告警) |
| `quant oms` | 訂單狀態機檢視(每筆單的生命週期與稽核軌跡) |
| `quant tca` | 滑價分析:訊號價 vs 實際成交價(回饋給回測成本模型) |
| `quant health` | heartbeat 健康檢查(漏跑/時鐘偏移偵測) |
| `quant drift` | 回測預期 vs 實盤實際的決策偏差 |
| `quant integrity` | point-in-time 檢查:歷史是否被資料源回溯改寫 |
| `quant report` | 每日營運報告(`--alert` 推播 Telegram) |
| `quant alert-test` | 測試告警管線(驗證 Telegram 設定) |
| `quant web` | **啟動網頁儀表盤** |

共用選項:`--strategy`、`--params "k=v,k=v"`、`--start YYYY-MM-DD`、`--timeframe 1d`。

**機器可讀輸出 `--json`**(給腳本 / AI agent 用,16 個查詢類指令支援):
`status`、`info`、`account`、`backtest`、`walkforward`、`check`、`experiments`、`lifecycle`、
`note list`、`live`、`journal`、`oms`、`tca`、`health`、`reconcile`、`drift`。
契約:**stdout 只有一份 JSON 文件**(日誌走 stderr,可安心 `| jq`);頂層鍵 `command` + `data`
(有過/不過語意的指令再加 `ok`,exit code 不變);數字保持數字(不會變字串);純 ASCII(cp950 安全)。
例:`quant tca --json` 直接取 `data.avg_slippage_bps`,不必解析人類表格。

**唯讀 MCP server**(`quant mcp`,AI agent 的原生介面):與 `--json` 共用同一層
[src/quant/readapi.py](src/quant/readapi.py)(兩面永不漂移)。10 個工具:`get_status` /
`get_health` / `get_live_decisions` / `get_orders` / `get_tca` / `list_experiments` /
`get_experiment` / `list_research_notes` / `read_research_note` / `list_specs`。
**鐵律:只有查詢,永無下單**(與「spec 不可含 execute」同族,AST 掃描測試釘死)。
Claude Code 開本專案會自動偵測 [.mcp.json](.mcp.json)(首次需同意);其他 client 跑
`quant mcp`(缺依賴先 `pip install -e ".[mcp]"`)。

### 2. 研究流程(找策略 → 驗證 → 留痕)

```powershell
quant download SPY --start 2020-01-01               # 抓資料(會快取,離線可用)
quant check SPY                                      # 驗資料品質
quant sweep SPY --strategy momentum                  # 參數掃描 → 排名 + CSV + 熱力圖
quant walkforward SPY --strategy momentum            # 樣本外驗證(別跳過!)
quant backtest SPY --strategy momentum --params "lookback=100" `
    --slippage-bps 5 --report                        # 含滑價的雙引擎回測 + tear sheet
quant backtest --spec spy_momentum                   # 或:全部參數來自版控的規格檔
quant experiments                                    # 回顧所有跑過的實驗
quant lifecycle --all                                # 用事前寫死的規則檢查策略健康度
quant note new "我的想法" --strategy momentum         # 有結論就寫進知識庫(失敗最值錢)
```
> 經驗法則:**WF efficiency** 接近 1 = 穩健;遠小於 1 = 過擬合。
> 回測請**加上滑價**(`--slippage-bps`)或用 `--calibrate` 直接套實盤實測成本,別看零摩擦的數字。

### 3. 紙上交易(離線跑完整下單路徑)

```powershell
quant paper SPY --strategy momentum --params "lookback=100" `
    --stop-loss 0.05 --take-profit 0.15 --max-position-notional 50000 --plot
quant journal                    # 看這場 session
```

### 4. 實盤(Alpaca paper)— dry-run 優先

```powershell
quant account                                        # ① 檢查連線(唯讀)
quant live --spec spy_momentum --broker alpaca       # ② dry-run:策略+風控全部來自版控規格檔
# (等效的全參數寫法:quant live SPY --strategy momentum --params "lookback=100" --stop-loss 0.05 ...)
# ③ 確認決策合理後再加 --execute 才真的送單
quant protect SPY --stop-loss 0.05 --take-profit 0.15          # 幫既有部位補掛 OCO
quant journal --live                                           # 看每次 live 決策
```
- `--mode target`(預設)對齊「策略現在想要的部位」,一天跑一次/漏跑都安全(冪等)。
- **`--execute` 永遠是 CLI 旗標**:規格檔裡不允許 `execute` 欄位(載入直接報錯),上實盤永遠是人的明確動作。
- Alpaca 的 bracket/OCO 只吃整股,系統會自動把零股無條件捨去。

### 5. 自動排程

```powershell
quant schedule --spec spy_momentum --spec qqq_ma_cross --broker alpaca --run-now
# 一個程序排多個策略;每個 spec 的參數與風控都來自版控的規格檔
```
**目前採用「一鍵啟停」模式**:雙擊 [`scripts/trading.cmd`](scripts/trading.cmd) 啟動(`stop`/`status` 停止/查看;防重複啟動;日誌在 `logs/`);機器不定時關機所以刻意不常駐,重開機後想交易再點一次。日後要常駐再用 **Windows 工作排程器**,步驟見 [docs/SCHEDULING.md](docs/SCHEDULING.md)。
排程每次觸發都會走:假日略過 → OMS 同步 → 對帳(不符即停+告警)→ 決策 → 紀錄 + heartbeat。

### 6. 多策略組合

```powershell
quant portfolio --config portfolios/example.json
quant portfolio --legs "SPY:momentum:0.5:lookback=100; QQQ:ma_cross:0.5:fast=20,slow=50"
```
輸出各 leg 指標、合併指標、**相關矩陣**與分散化效益;`--engine backtrader` 可換引擎。

### 7. 🌟 網頁儀表盤(看結果 / 核對數據)

```powershell
quant web                        # → 瀏覽器開 http://127.0.0.1:8000
```

| 分頁 | 內容 |
|------|------|
| **Backtest** | 指標卡 + 權益曲線 + 回撤圖 + 勝率/盈虧比/Alpha/Beta/逐年報酬;**含滑價(bps)輸入欄** |
| **Portfolio** | 合併指標 + 各 leg 表 + 相關矩陣 + 分散化比值 |
| **Sweep** | 參數網格掃描 → 排名表 |
| **Walk-forward** | 樣本外驗證 → WF efficiency 判讀 + 每折表格 |
| **Journal** | 一鍵載入 paper/live 交易紀錄 |
| **API** | `http://127.0.0.1:8000/docs` 自動生成的互動 REST API |

> 唯讀設計:不放下單按鈕,實盤一律走 CLI。關伺服器:前景按 `Ctrl+C`;背景則
> `Get-NetTCPConnection -LocalPort 8000 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }`。

### 8. 進階:TimescaleDB / Docker

```powershell
# .env 裡切換儲存後端
STORAGE_BACKEND=timescale

docker compose up -d timescaledb
docker compose run --rm quant backtest SPY --strategy momentum
```
完整說明見 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

### 9. 開發:測試 / CI

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci.ps1   # ruff + mypy + pytest(與 CI 同一套)
```
每次 push 會在 GitHub Actions 自動跑同一套閘門(py3.11 / 3.12)。**201 個測試**含 golden 回歸(固定種子鎖指標數字,改壞會被抓)。

---

## 四、尚未完成的事項

| 項目 | 說明 | 屬於 |
|------|------|------|
| **存活者偏差** | **刻意圈定不做**(2026-07 決策):目前聚焦單標的/ETF 技術面擇時,此範圍幾乎無此偏差。⚠️ 觸發條件:未來要做「跨標的選股/掃描」時,必須先換 survivorship-free 資料源(Norgate/Sharadar)+ 建 as-of 宇宙,否則那類回測不可信 | 範圍決策 |
| **原始價+調整因子分離存放** | yfinance 只能當原型;要真正重建 as-of 價格需換正式資料源(動到 storage schema) | Batch 2 剩餘 |
| **IBKR 券商** | 用 `ib_async` 寫 `IBKRBroker` 接在 `Broker` 介面後面;IBKR 需本機跑 TWS/Gateway,比 Alpaca 重 | 未來 |
| **盤中 / 即時行情** | websocket 串流 + 成交通知;目前以**日線**為主 | 選配 |
| **更多策略 / 因子檢定** | 內建 `ma_cross`、`momentum`;因子庫與 IC/RankIC 檢定尚未動工 | 持續擴充 |

---

## 五、可延伸的部分 + 安全守則

### 為什麼這套架構好擴充

每一條「軸」都被介面隔開,**加東西不用動上層**:

- **加策略**:在 `strategies/` 寫一個類別 + 在 `registry.py` 註冊一行 → `backtest`/`sweep`/`walkforward`/儀表盤**全部自動支援**(範本見 [docs/USAGE.md](docs/USAGE.md))。
- **加券商**(如 IBKR):實作 `Broker` 介面寫一支 adapter(比照 `alpaca_broker.py`)。
- **換儲存**:實作 `BarStore`(已有 Parquet 與 TimescaleDB 兩種,`.env` 切換)。
- **換回測引擎**:實作 `BacktestEngine`;walk-forward 與 portfolio 都吃 `engine_cls` 注入。
- **改網頁**:後端是 JSON API、前端是單檔 `index.html`,小改容易、重做(換 React)也安全。

### 安全守則(務必)

1. **永遠先 dry-run**:`live`/`schedule` 不加 `--execute` 就只計算+記錄、不下單。
2. **paper-only 硬限制**:`AlpacaBroker` 在 `ALPACA_PAPER=true` 以外會直接拒絕啟動。
3. **保護部位**:進場用 `--stop-loss/--take-profit` 自動掛 bracket;既有部位用 `quant protect`。
4. **多一層風控**:`--max-position-notional` / `--max-daily-loss` 設上限/熔斷(**降險賣單即使熔斷也放行**)。
5. **回測要含成本**:`--slippage-bps` 或 `--calibrate`,零摩擦回測系統性樂觀。
6. **改完先過閘門**:push 前跑 `scripts\ci.ps1`。

### 已知小限制

- 系統以**日線**為主;Yahoo 當日 K 棒要美股收盤後才完整(排程時間已考量)。
- yfinance `auto_adjust=True` 除權息後會回溯改寫歷史 → `quant integrity` 會偵測+告警+留紀錄(parquet 覆蓋前也自動備份 `.bak`)。
- Alpaca 的 bracket/OCO/停損單**只支援整股**,零股會被無條件捨去。
- 網頁前端是**單檔 vanilla JS**,分頁大量增加時值得搬框架(後端 API 不動)。

### 參考專案的取捨(`01_ref/`,git 忽略)

- `daily_stock_analysis`(**MIT**):儀表盤架構樣板參考其 `api/app.py`(create_app 工廠)。
- `FinceptTerminal`(**AGPL-3.0**):授權有傳染性,**只看概念、不抄程式碼**。
- IBKR 生態:結論是只考慮 `ib_async` 當函式庫;大框架(Nautilus/LEAN)只當設計參考。

---

> 專案在 GitHub:`github.com/fun0963/p1_quantfinance`(私有)。CI 狀態看 repo 的 **Actions** 分頁。
> 接手開發前請先讀 [architecture_map_ch.md](architecture_map_ch.md)(分層、設計模式、技術債、踩雷紀錄都在那)。
