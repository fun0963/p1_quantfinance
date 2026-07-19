# 系統架構地圖 — p1_quantfinance

> **這份文件給誰**:接手本專案的人或 AI agent。讀完可在不翻遍原始碼的情況下,知道「東西放哪、為什麼這樣分層、已完成什麼、接下來做什麼、有哪些坑」。
> **最後更新**:2026-07(技術債清理 + 成本模型 + 實驗記錄 + 架構清理後)。維護方式:動到架構/新增 package 時更新本檔;細部進度以 [audit_and_roadmap_ch.md](audit_and_roadmap_ch.md) 為準。

---

## 1. 一句話 + 關鍵數字

美股 / ETF 的量化交易系統:**研究(雙引擎回測)→ 紙上交易 → 排程實盤(Alpaca paper)**一條龍,強調可維護、可除錯、失敗預設安全。

| 指標 | 數值 |
|---|---|
| 原始碼 | 70 個 `.py`、約 7,500 LOC(`src/quant/` + `config/`) |
| 測試 | 39 個測試檔、**288 passed / 1 skipped**;ruff + mypy 全綠 |
| Python | 3.11+(開發環境 3.13);`src/` layout,套件名 `quant` |
| 進入點 | `quant` CLI(typer,27 指令 + `note new/list` 子指令)+ FastAPI 唯讀儀表盤 |
| 架構評分 | 分層/耦合 **8/10**、測試/CI **8/10**、可維護性 **7/10**(已修正,見 §7) |

---

## 2. 架構分層圖

依賴方向**由上往下**(上層呼叫下層;下層永不 import 上層)。已驗證**無循環相依**。

```
┌─ 進入點 ────────────────────────────────────────────────────────┐
│  cli.py (typer, 20+ 指令)          web/ (FastAPI 唯讀儀表盤)        │
└───────────────┬────────────────────────────┬───────────────────┘
                │                             │
┌─ 營運 / 組合 ─▼─────────────────────────────▼───────────────────┐
│  ops/  notify · reconcile · oms · tca · health · drift · report  │
│  portfolio/  多策略權重配置                                        │
└───────────────┬─────────────────────────────────────────────────┘
     execution ⇄ ops(下單後記 OMS/heartbeat;ops 讀 journal 對帳/報告)
┌─ 執行 / 回測 ─▼─────────────────────────────────────────────────┐
│  execution/  Broker · session · live_runner · scheduler · journal │
│  backtest/   VectorBT + Backtrader 雙引擎 · metrics · sweep · wf   │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 風控 ────────────────────────────────────────────────────────┐
│  risk/  RiskManager(部位 sizing) · RiskGate(否決) · Bracket      │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 策略 ────────────────────────────────────────────────────────┐
│  strategies/  BaseStrategy · ma_cross / momentum · registry      │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 資料 ────────────────────────────────────────────────────────┐
│  data/  feeds(yfinance/Alpaca) · storage(Parquet/Timescale)     │
│         · loaders · quality · integrity                          │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 基礎 ────────────────────────────────────────────────────────┐
│  core/types(Signal·Order)      config/(settings)   utils/(log)  │
└─────────────────────────────────────────────────────────────────┘
```

**唯一的雙向耦合**:`execution ⇄ ops`(排程下單後要記 OMS/heartbeat、下單前要 reconcile;而 ops 的 reconcile/report 要讀 `execution.journal`)。這是**刻意**的——營運監控本來就緊貼執行層;無循環 import(靠函式內延遲 import 與清楚的呼叫方向)。

---

## 3. 核心設計模式(讀懂這幾個就懂全局)

| 模式 | 在哪 | 為什麼 |
|---|---|---|
| **ABC 介面 + 可抽換實作** | `DataFeed`、`BarStore`、`Broker`、`BacktestEngine`、`BaseStrategy`、`RiskManager`、`Notifier` | 換資料源 / 券商 / 回測引擎 / 儲存後端 = 改設定,不改呼叫端。已實證可換。 |
| **設定驅動工廠** | `get_store()`(Parquet/Timescale)、`get_notifier()`(Telegram/log/null)、`get_strategy_cls()`(策略登錄表)、`_build_broker()`/`_live_broker()`(Alpaca/Paper) | 具體實作由 env 設定決定,呼叫端只拿介面。 |
| **依賴注入(建構子/參數)** | `run_paper_session` / `run_live_step` / `live_and_journal` 都可注入 broker / risk / gate / notifier / data | 測試時注入 `PaperBroker` + `NullNotifier` + 合成資料,完全離線、零 API key。 |
| **provider-agnostic 型別** | `core/types`(Signal/Order)不 import 任何第三方;價格資料以 OHLCV DataFrame(`DataFeed.COLUMNS`)流動 | 策略與風控無論回測或實盤都用同一組型別。 |
| **失敗預設安全(fail-safe)** | 實盤下單前先 reconcile、過期資料拒單、無訊號不清倉、止損永遠出得掉、營運寫入 best-effort 不阻斷交易、告警永不 crash | 見 §7 的 Batch 0 安全修復。 |
| **唯讀分析物件** | `ReconcileReport` / `TCAReport` / `HealthReport` / `DriftReport` / `QualityReport` / `MutationReport`,都有 `.summary()` 與 `.ok`/`.mutated` 布林 | 分析層只回報,由呼叫端決定停/告警,不產生副作用。 |
| **狀態機 + 稽核軌跡** | `ops/oms.py`:`OrderState` + 合法轉移表 + 附加式 `order_events` | 訂單生命週期可追;非法轉移拒絕並記錄,絕不默默套用。 |

---

## 4. 資料夾地圖(逐 package)

> 慣例:每個 package 的 `__init__.py` 通常 re-export 公開 API;測試在 `tests/test_<主題>.py`。

| 資料夾 | 職責 | 關鍵檔案與公開 API | 主要依賴 | 測試 |
|---|---|---|---|---|
| **config/** | 單一設定來源(pydantic-settings + `.env`) | `settings.py`:`Settings`、`get_settings()`(lru_cache 單例)。含 Alpaca 金鑰、`STORAGE_BACKEND`、Telegram 告警、路徑 | — | 無專屬(被各層間接測到) |
| **quant/core/** | 全系統共通型別 | `types.py`:`Signal`/`Order` + `OrderSide`/`OrderType`/`SignalType`(frozen dataclass + Enum)。~~`events.py`~~ 與 ~~`Bar`~~ 已於架構清理移除(零引用死碼;實際管線以 DataFrame 流動) | 無 | 間接(smoke/gate/paper) |
| **quant/utils/** | 集中式 loguru 日誌 | `logging.py`:`setup_logging()`(一次性)、`get_logger()` | loguru | 間接 |
| **quant/data/** | 行情擷取 + 儲存 + 快取 + 品質 + 完整性 + **timeframe 註冊表** | `timeframes.py`(**Batch 4 單一真相來源**:bar 秒數/年化週期/vbt freq/預設 feed/intraday 旗標;未知 timeframe 一律 raise);`feeds/`(`DataFeed` ABC → `YFinanceFeed`/`AlpacaFeed` + `get_feed(timeframe)` 工廠:日級 yfinance、盤中 Alpaca);`storage/`(`BarStore` ABC → `ParquetStore`/`TimescaleStore` + `get_store()` 工廠);`loaders.py`:`load_bars()`(快取+新鮮度[日級 days / 盤中 bars]+品質+改寫偵測)、`fetch_bars()`;`quality.py`;`integrity.py`(point-in-time 護欄) | core、config | `test_storage/test_loaders/test_quality/test_integrity/test_timeframes/test_intraday` |
| **quant/strategies/** | 純訊號邏輯(引擎無關)+ 具名規格 | `base.py`:`BaseStrategy` ABC(`generate_signals→entries/exits`、`default_grid`、`params_valid`、`warmup_bars`);`ma_cross.py`、`momentum.py`;`registry.py`:`get_strategy_cls()`/`available()`;`spec.py`:`StrategySpec`/`load_specs()`(**參數外部化 M6.3**,讀 `configs/strategies.json`:params/risk/lifecycle 規則全進版控) | core、config | `test_strategies/test_momentum_and_registry/test_lifecycle` |
| **quant/risk/** | 下單前 sizing + 否決 + 出場保護 | `base.py`:`RiskManager` ABC → `FixedFractionRisk`;`gate.py`:`RiskGate`(部位/名目/日虧上限 + kill-switch;**降險賣單永遠放行**);`bracket.py`:`Bracket`/`BracketConfig`(停損停利、可 trailing) | core | `test_gate`(4)、`test_bracket`(8) |
| **quant/backtest/** | 雙引擎回測 + 指標 + 掃描 + walk-forward + 成本模型 + 報告 | `base.py`:`BacktestEngine` ABC(`cash/fees/slippage`)+ `BacktestResult`;`vectorbt_engine.py`(向量化,研究/掃描)、`backtrader_engine.py`(事件驅動,擬真;slippage 併入 commission);`costs.py`:`CostModel`(fees+slippage,`from_tca()` 用實盤 TCA 校準);`metrics.py`:`compute_metrics`(含 Sharpe/**Sortino/Calmar**)/`trade_stats`/`alpha_beta`/`yearly_returns`/**`monthly_returns`**;`optimize.py`:`sweep()`;`walkforward.py`;`plots.py`(plotly);**`report.py`:`build_report()` 一鍵 HTML tear sheet(指標表+淨值+回撤+月報酬熱圖,自包含)** | strategies、data、core | `test_metrics/test_optimize/test_walkforward/test_plots/test_regression/test_costs/test_report` |
| **quant/execution/** | 下單、部位生命週期、稽核軌跡 | `base.py`:`Broker` ABC + `Position`;`paper_broker.py`(同步成交模擬 + `order_status`)、`alpaca_broker.py`(paper 端點 + bracket/OCO + `day_pnl` + `order_status`);`session.py`:`run_paper_session`;`live_runner.py`:`run_live_step`(單根 K 決策 + 新鮮度閘 + target/signal 模式);`scheduler.py`:`live_and_journal`(可排程單元)+ `run_schedule`(APScheduler);`journal.py`:`TradeJournal`(SQLite WAL,sessions/fills/blocked/live_log/orders/order_events/heartbeats) | risk、strategies、data、ops、core | `test_paper/test_live_runner/test_live_brackets/test_journal/test_scheduler` |
| **quant/ops/** | 無人值守營運後盾(Batch 1) | `notify.py`(`Notifier` ABC + Telegram/log/null + `get_notifier`);`reconcile.py`(帳 vs journal,CRITICAL 停手);`oms.py`(訂單狀態機 + `sync(broker)`);`tca.py`(訊號價 vs 成交價滑價);`health.py`(heartbeat + 漏跑/時鐘偏移偵測);`drift.py`(回測預期 vs 實盤實際);`report.py`(每日整合報告) | execution.journal/base、data、core | `test_ops/test_oms/test_tca/test_health/test_drift` |
| **quant/portfolio/** | 多策略權重配置與混合回測 | `portfolio.py`:`PortfolioLeg`/`PortfolioResult`/`run_portfolio()`/`load_portfolio_config()`;算混合 vs 加權平均、腿間相關、分散化比率 | backtest、strategies、data | `test_portfolio` |
| **quant/research/** | 研究紀律層(M4)— 實驗記錄 + 生命週期 | `experiments.py`:`ExperimentStore`(SQLite `data/experiments.db`,WAL)記錄每次回測(git-hash/dirty、參數、資料窗、成本 bps、指標);`lifecycle.py`:`LifecycleRules`/`check_lifecycle()`(**M6.5 事前寫死的晉升/退場規則**:trailing 視窗 rolling Sharpe / 回撤 / 活動度,唯讀 `LifecycleReport`);`notes.py`:**研究知識庫(M4.6)**——`research_notes/` 一個想法一頁 Markdown(假設/做法/結果/結論),極簡 frontmatter(無 YAML 依賴),`experiments:` 連回實驗 id,CLI `quant note new/list`。未來因子框架也放這 | config、utils、backtest.metrics(純函式) | `test_experiments/test_lifecycle/test_notes` |
| **quant/web/** | 唯讀結果儀表盤(FastAPI) | `app.py`:`create_app()`(App Factory);`routes.py`(7 端點:backtest/portfolio/sweep/walkforward/journal;薄包裝呼叫既有函式);`schemas.py`(pydantic 請求模型;backtest 含 slippage_bps);`static/index.html`(plotly.js CDN,無 Node 建置) | backtest、portfolio、execution、data | `test_web`(optional-dep skip) |
| **quant/cli.py** | typer 進入點,串起所有層 | 27 指令:研究(`download/backtest/sweep/walkforward/portfolio/check/experiments/lifecycle`;`backtest` 含 `--spec/--slippage-bps/--calibrate/--report/--log`)、交易(`paper/live/schedule/protect/account`;`live`/`schedule` 支援 `--spec`,`schedule --spec` 可重複=一程序多策略;**spec 不可含 `execute`,上實盤永遠是 CLI 明確旗標**)、營運(`status`(**聚合快照,分區降級**)`/journal/reconcile/report/oms/tca/health/drift/integrity/watch(一次性條件告警)/alert-test/mcp/web`);**17 個查詢指令支援 `--json`**(stdout 僅一份 JSON、日誌走 stderr、exit code 不變);解析輔助 `_parse_params/_parse_grid/_parse_legs/_engine_cls/_cfg_from_spec/_emit_json` | 全部 | 間接(經 scheduler/paper 等)+ `test_cli` |
| **quant/readapi.py** | **唯讀查詢層**:`--json` 與 MCP server 共用的 payload 建構(兩面永不漂移);全部回傳 plain JSON-safe 資料 | `status_snapshot()`(聚合+分區降級+可注入 broker 工廠)、`health_snapshot/live_decisions/orders_snapshot/tca_snapshot`、`experiments_list/experiment_get`、`notes_list/note_read`(**basename-only 防路徑穿越**)、`specs_list`、`plain/df_records/json_default`;**鐵律:不呼叫任何下單/改單/同步方法**(AST 掃描測試釘死) | execution/ops/research/strategies(函式內延遲 import) | `test_cli`(--json 契約)+ `test_mcp_server` |
| **quant/mcp_server.py** | **唯讀 MCP server**(stdio):AI agent 的原生查詢介面 | FastMCP + 10 工具(`get_status/get_health/get_live_decisions/get_orders/get_tca/list_experiments/get_experiment/list_research_notes/read_research_note/list_specs`),薄包裝 readapi;`TOOLS` 註冊表由測試釘死;`quant mcp` 啟動、`.mcp.json` 讓 Claude Code 自動偵測;optional dep `.[mcp]`(dev 已含,CI 有測) | readapi、mcp SDK | `test_mcp_server`(註冊表 pin、AST 唯讀掃描、離線煙霧、**真協定 in-memory 握手**) |
| **根目錄基建** | 打包 / 容器 / CI / 腳本 / 文件 | `pyproject.toml`(ruff line=120、mypy、pytest、extras `[timescale]`/`[web]`);`Dockerfile` + `docker-compose.yml`;`.github/workflows/ci.yml`(ruff+mypy+pytest);`scripts/`(`ci.ps1` 本機鏡像、`daily_live.ps1`);`docs/`(GUIDE/USAGE/DEPLOYMENT/SCHEDULING) | — | `scripts/ci.ps1` |

---

## 5. 三大關鍵資料流

**A. 回測(研究)**
```
CLI backtest → _load(load_bars: feed+快取+品質+改寫偵測)
            → get_strategy_cls(name)(...) → strategy.generate_signals(df) → entries/exits
            → {VectorBTEngine, BacktraderEngine}.run() → BacktestResult
            → compute_metrics(equity) → 兩引擎並排比較(+ 可選 plotly HTML)
```

**B. 紙上交易(離線可測)**
```
run_paper_session：逐根 K → strategy 訊號 → RiskManager.size → RiskGate.check_order
                → PaperBroker 同步成交(含 fee/slippage) → Bracket 停損停利
                → PaperSessionResult(equity/fills/blocked/exit_reasons) → TradeJournal.record_session
```

**C. 實盤排程(失敗預設安全)**
```
run_schedule(APScheduler) → 每次觸發 _job：
  _is_trading_day? 否 → 略過 + scheduler heartbeat
  是 → live_and_journal(cfg, dry_run 預設 True)：
     ① OMS.sync(broker)       推進前次掛單狀態
     ② 非 dry_run → reconcile(broker, journal);CRITICAL → 停手 + 告警
     ③ run_live_step：新鮮度閘(過期拒單)→ target/signal 模式 → RiskGate → Broker 下單
     ④ _safe_record(journal) + OMS.on_submit + 立即 sync + heartbeat + 告警(下單/被擋/crash)
```

---

## 6. 軟體工程品質評估(獨立審查)

三個獨立的架構審查 agent(讀交叉切面、以資深架構師視角評分):

### 🟢 分層與耦合 — 8/10
- **優點**:單向依賴、零循環 import(跨 ~5,500 LOC 驗證);7 個 ABC 介面乾淨可抽換;設定驅動工廠實證可換後端;安全層(OMS/RiskGate/reconcile/heartbeat)乾淨地包在核心邏輯外圍。
- **待補**:`portfolio` 直接 import `VectorBTEngine`(理想上該注入 `BacktestEngine` 介面);工廠函式散落(`get_store`/`get_notifier`/`_build_broker`)可集中;延遲 import 讓靜態依賴圖看不全(為了 optional 相依,取捨合理)。

### 🟢 測試 / CI / 程式品質 — 8/10
- **優點**:143 測試涵蓋 單元 / 整合 / golden-master 回歸(固定種子鎖指標)/ 雙引擎一致性;optional 相依用 `importorskip` 跳過;DI 讓實盤路徑可離線測;ruff + mypy 雙閘;pydantic-settings 集中設定。
- **待補**:實盤層錯誤路徑覆蓋偏淺;合成資料 fixture 各檔重複(可收進 `conftest.py` 參數化);缺 `test_config.py`;缺 CLI 端到端測;CI 只跑 Linux;可加 pytest-cov。

### 🟡 可維護性 / 風險 — 7/10(**已更正**)
> ⚠️ **重要更正**:此審查 agent 讀了 [audit_and_roadmap_ch.md](audit_and_roadmap_ch.md) 裡「8 個 P0 安全漏洞」的**問題描述**,誤判為**尚未修復**而給 6/10。事實上**8 個 P0 已在 Batch 0 全部修復 + 回歸測試 + 推送(commit `0e3eecb`)**:新鮮度閘、未成交掛單感知、無訊號不清倉、出場前先取消 OCO、日虧熔斷放行降險賣單、實盤下單前 reconcile、假日略過、`daily_live.ps1` 檢查 `$LASTEXITCODE`。扣除這點,實際可維護性約 **7/10**。
- **優點**:研究層(策略/回測/最佳化)分離乾淨、介面驅動;失敗預設安全模式到位;docstring/註解密度高、命名一致。
- **真正待補(見 §8 技術債)**:資料源無重試/退避;`yfinance` 未知 timeframe 靜默降級成日線;parquet 覆蓋非原子、無備份;20 處 `noqa: BLE001` 廣捕例外(多屬 ops best-effort,但可分類化);web 500 可能洩漏 Timescale DSN;`live_log` 缺索引;Backtrader 引擎未填 per-trade 記錄。

---

## 7. 完成項目 vs 待辦(進度總覽)

### ✅ 已完成(可運作 + 已測)

| 階段 | 內容 | 狀態 |
|---|---|---|
| **Phase 1–3** | 資料層、雙引擎回測、掃描/walk-forward、風控(gate/bracket)、紙上交易 pipeline、SQLite journal | ✅ 已推送 |
| **Phase 4** | TimescaleDB 儲存後端、多策略組合配置、Docker 化 | ✅ 已推送 |
| **CI** | GitHub Actions(ruff + mypy + pytest)+ `scripts/ci.ps1` 本機鏡像 + 回歸測試 | ✅ 已推送 |
| **Web v1+v2** | FastAPI 唯讀儀表盤:backtest/portfolio/journal + sweep/walkforward 分頁 + finlab 風格指標(勝率/盈虧比/Alpha·Beta/逐年報酬) | ✅ 已推送 |
| **Batch 0** | **8 個 P0 實盤安全漏洞全修** + 回歸測試 + journal WAL | ✅ `0e3eecb` |
| **Batch 1(核心)** | ops 層:告警 Notifier + 對帳 reconcile + 每日報告;實盤下單前 fail-safe 對帳 | ✅ `8a91982` |
| **Batch 1(剩餘)** | OMS 訂單狀態機 + TCA 滑價 + 健康 heartbeat + 回測/實盤偏差;經對抗式審查修 7 個 bug | ✅ `c225d22` |
| **Batch 2 增量#1** | **point-in-time 歷史改寫偵測**(`data/integrity.py` + CLI `quant integrity`) | ✅ `baad351` |

對照 `quant_system_breakdown.md` 里程碑:**MS2(研究)完成;MS1/MS3 過半;MS4(真錢)在 P0 修復後解除封鎖,但仍建議先跑 paper 觀察**。

### ⬜ 接下來(依優先序)

1. **Batch 2 剩餘 — 資料完整性**(回測可信度命門)
   - 存活者偏差(M1.14):**已做範圍決策 — 圈定不做**(見 §8 #11)。目前聚焦「單標的/ETF 技術面擇時」,此範圍幾乎無存活者偏差;維持 yfinance。**待日後做「跨標的技術面選股/掃描」時才需回頭處理**(換 survivorship-free 資料源)。
   - 原始價 + 調整因子分離存放:才能真正重建 as-of 價格(動到 storage schema)。
   - ✅ **成本 / 滑價模型(M5.2/5.3,已完成)**:`backtest/costs.py` `CostModel`(fees+slippage);兩引擎 + sweep 支援 slippage;CLI `backtest --slippage-bps / --fees-bps / --calibrate`(`--calibrate` 讀 journal TCA 反推成本,打通 量測→校準→回測 閉環);web 儀表盤 backtest 分頁亦接上 slippage 旋鈕並顯示成本行。預設 slippage=0 保住 golden 回歸。
2. **技術債清理**(見 §8,多為 P1/P2,價值高、風險低)。
3. **Batch 3 — 研究深化**:✅ 實驗記錄系統(M4.5)、✅ 進階指標+一鍵報告(M5.6/5.7)、✅ **參數外部化(M6.3)**——`strategies/spec.py` + `configs/strategies.json`(params/risk/lifecycle 全進版控,`quant backtest --spec NAME`)、✅ **策略生命週期(M6.5)**——`research/lifecycle.py` 事前寫死的晉升/退場規則,`quant lifecycle --all` 健康檢查(breach 時 exit 1,可排程當閘門)。✅ **spec 接進實盤路徑**——`live --spec` / `schedule --spec`(可重複,一程序多策略),策略身分+風控來自版控規格檔,`execute` 硬性 CLI-only。✅ **研究知識庫(M4.6)**——`research/notes.py` + `research_notes/`,`quant note new/list`,已有兩篇種子筆記(momentum vs ma_cross OOS、零摩擦回測高估)。**Batch 3 研究紀律骨架至此完整**(實驗記錄→成本校準→生命週期→spec 營運→知識庫)。之後:因子庫/檢定與機會掃描器(卡存活者偏差)暫緩。
4. ✅ **Batch 4 — 分鐘級/盤中(完成,含首批實測)**:timeframe 註冊表(4-1)、bar 級新鮮度閘(4-2)、盤中 interval 排程 + 開市閘(4-3)、Alpaca 1min 端到端(4-4)。**2026-07-17 盤中實戰**:`qqq_scalp_1min` 探針每 5 分鐘決策、19/19 成交,`quant tca` 量得 **avg slippage -1.0 bps**(遠低於假設的 5 bps;樣本僅一日一檔,校準先保守)。途中逮到並修掉 3 個只有真實營運才現形的 bug(enum side 正規化、cp950 banner、SIP 15 分鐘延遲→改 IEX)。
5. **接下來(依優先序)**:
   - ✅ **一鍵啟停工具(2026-07-18,取代 Task Scheduler)**:`scripts/trading.cmd`(雙擊=start;`stop`/`status`;防重複啟動、`-u` 即時日誌到 logs/)。**刻意不做常駐**:機器不定時關機,使用者選擇「想跑再點一下」模式;重開機後要交易就再點一次。日後要常駐再走 docs/SCHEDULING.md 的工作排程器。
   - ⬜ **開盤日記得啟動**:每個交易日開機後雙擊 `scripts\trading.cmd`(或叫 Claude 跑 `trading.ps1 start`),用 `quant health` 驗 heartbeat。
   - ✅ **TradingView UX 收尾(2026-07-18,gap 筆記轉 adopted)**:tear sheet 加**資料出處行**(feed/timeframe/bars/最後 bar 時間戳/生成時間);新指令 **`quant watch`** 一次性條件告警(`--above/--below/--cross-ma` 三擇一、無狀態、`--alert` 推播、`--json`;刻意不做常駐 watcher,與不常駐營運模式一致)。實測 `watch SPY --cross-ma 100` 直接給出「距動量翻轉的緩衝」。multi-timeframe 維持事前緩議(等第二支策略真的需要)。
   - ✅ **FOMC 事件日研究(2026-07-18,結論=不建事件基礎設施)**:`configs/fomc_dates.json`(官方排定決議日 2020-2026,緊急會議依定義排除)+ `scripts/fomc_study.py`。量測:FOMC 日只比平常熱 ~20%、momentum 暴露成本 **-27 bps/yr 噪音級**、31 次執行僅 1 次落在 FOMC 日(6.5 年綁一次的 blackout=死程式碼)→ 不做 blackout 也不做 banner;JSON 留給未來盤中策略(筆記 status: rejected 附復活條件)。
   - ✅ **MOC/OPG 執行對齊研究(2026-07-18,結論=暫不換)**:`scripts/gap_analysis.py` 離線量測 close→next-open 價差——SPY momentum 進出場日來回漂移 **-6 bps ≈ 0**(統計上為零),真正效應是**單次 ±57 bps 的噪音**(年化 tracking error ~125 bps,非虧損)。維持收盤後市價單;復活條件見 research_notes/2026-07-18-alpaca-moc-opg-orders.md(status: rejected)。
   - ✅ **CLI `--json` 機器可讀輸出(2026-07-18)**:15 個查詢類指令(info/account/backtest/walkforward/check/experiments/lifecycle/note list/live/journal/oms/tca/health/reconcile/drift)。契約:stdout 僅一份 JSON(日誌在 stderr)、頂層 `command`+`data`(+`ok`)、exit code 不變、numpy 數字不變字串、ensure_ascii。動機與後續見 research_notes/2026-07-18-gap-ai-interface.md。
   - ✅ **`quant status` 聚合快照(2026-07-18)**:帳戶+對帳+health+近期決策/訂單+TCA+specs 一發到位(原本 5 個指令);**分區降級**(broker 掛只標該區 error,不遮本地狀態)、`--offline` 跳網路、lifecycle 只列不評估(保持秒回)。
   - ✅ **tear sheet 升級(2026-07-18)**:`--report` 新增 **K 線+進出場標記**面板(兩引擎 trade 表通吃;backtrader 無 exit price 用 bar close 補)與**權益疊自身標的 buy-and-hold** + Benchmark/Excess 指標列(單標的擇時的誠實 null hypothesis)。首跑即見效:SPY momentum 116.8% vs B&H 151.0%(excess -34.2%)——策略價值在淺回撤,以前的報告看不見。無 `data` 參數時退化為原三面板(向下相容)。同批加 **rolling Sharpe 面板**(252-bar 窗=lifecycle 預設、timeframe 感知年化、零線參考;**最後一點=lifecycle 檢查的數字**,收斂一致性有測試;短序列自動省略)——SPY momentum 現值 1.26、歷史 -1.26~2.88,衰退趨勢可視。再補 **PSR**(`compute_metrics` 全域鍵 `psr_pct`,Bailey-López de Prado 偏態/峰度修正;SPY momentum 99.7%=Sharpe 統計上為真)與**年化換手** `Turnover (annual)`(成交名目/平均權益/年;5.18x → 年成本拖累 ≈ turnover×單邊 bps,成本預算實數化;需 vbt trade 表的 Size 欄)。report-metrics gap 筆記就此轉 adopted。
   - ✅ **唯讀 MCP server(2026-07-18,AI 友善三部曲完成)**:`quant.readapi` 共用查詢層(--json 與 MCP 同源)+ `quant.mcp_server`(FastMCP stdio,10 工具)+ `.mcp.json`(Claude Code 自動偵測,首次需同意)+ `quant mcp` 指令。**鐵律:只有查詢、永無下單**——AST 掃描測試禁止兩模組呼叫任何下單/改單/同步方法;真協定 in-memory 握手測試。細節見 research_notes/2026-07-18-gap-ai-interface.md。
   - ✅ **555.pdf 架構比對 + 啟用當日虧損斷路器(2026-07-19)**:逐頁視覺讀外部「24h AI 交易員」教學文(6 步閉環、30 子項,無程式碼無回測的概念文),對照結論=**無系統性缺口**——絕大多數子項「已建且更嚴謹」「有數據拒絕過」或「資產類別不適用」。唯一高價值產出:發現 `max_daily_loss` 斷路器**程式早已修好(audit P0 #5)但 spec 沒設值=裝了沒通電**;`AlpacaBroker.day_pnl()` 是**帳戶級**(equity−last_equity),數值必須用帳戶波動的尺設——三 spec 統一 1000(≈權益 1%,SPY 跌 ~2.2% 觸發;擋新倉、放行減倉)。緩議項與觸發條件見 research_notes/2026-07-19-gap-scan-24h-ai-trader-article-555-pdf.md(status: adopted)。同日補完可選項②:`quant watch --volume-spike`(最後一根量 ≥ 前 N 根均量 × MULT,`--volume-window` 預設 20、基準排除爆量 bar 自身;實測 QQQ 收盤分鐘 x3.24 觸發)。
   - ⬜ **累積 TCA 樣本**:探針多跑幾個交易日、換 SPY 也量,樣本夠了再回頭定案 `--calibrate` 的使用準則。
   - ⬜ **走查驗收**:照 [walkthrough_ch.md](walkthrough_ch.md) 從發想到檢討完整走一輪(使用者主導)。
   - ⬜ 第二支正式策略(用走查流程孵化);(遠期)存活者偏差解鎖後才做掃描器。
6. **範圍外(暫不做)**:台股、選擇權、M2 公司庫、M3 供應鏈、M9 事件層。

---

## 8. 已知技術債清單(給後續 agent 的待辦池)

依「價值 / 風險」排序,審查交叉確認過:

| # | 項目 | 位置 | 影響 |
|---|---|---|---|
| ~~1~~ | ✅ **資料源重試 / 退避**(`8df365b`) | `feeds/retry.py` + 兩個 feed | 已修:`with_retries` 有界重試+指數退避,`ValueError`(無資料/不支援 tf)視為 fatal 不重試 |
| ~~2~~ | ✅ **timeframe 白名單**(`0e42de3`) | `yfinance_feed.py`、`alpaca_feed.py` | 已修:未知 tf 改嚴格查表 `raise`(檢查移到 lazy import 前);兩個 feed 都修 |
| ~~3~~ | ✅ **parquet 原子寫入 + 備份**(`1bb9027`) | `storage/parquet_store.py` | 已修:temp→`os.replace` 原子覆蓋 + 覆蓋前複製 `.bak`;寫入失敗清 `.tmp` 保原檔 |
| ~~4~~ | ✅ **web 500 遮蔽 DSN + grid 上限** | `web/routes.py` | 已修:`_safe_detail` regex 遮蔽 URL 憑證(`user:***@`)、完整訊息只記 server log;`MAX_GRID_COMBOS=5000` 展開前擋超大 grid 回 400 |
| ~~5~~ | ⚪ **won't-fix(已評估)** | 8 個檔的 `noqa: BLE001` | 20 處經逐一盤點後全屬刻意 best-effort:告警/對帳/報告/OMS/排程監控/heartbeat 皆為「側線」,不變式硬性要求永不 raise(否則會阻斷主線交易與止損)。技術債想要的分類其實已達成:網路重試在 #1(feed 層)、券商真失敗由 `scheduler` job 總 handler 告警停手。把這些廣捕捉改窄=在安全護欄戳洞,風險 > 價值。**唯一可選微幅收斂**:`alpaca_broker.py:144` 改捕 Alpaca 特定例外(純美化,未做) |
| ~~6~~ | ✅ **Backtrader per-trade 記錄** | `backtest/backtrader_engine.py` | 已修:`notify_trade` 攔截每筆平倉,產出逐筆表(entry/exit time+price、bars_held、gross/net PnL、commission),欄位對齊 VectorBT 的 `PnL`,`trade_stats`/TCA 兩引擎可比 |
| ~~7~~ | ✅ **`live_log` 加索引 + 過濾下推** | `execution/journal.py`、`cli.py` | 已修:加 `ix_live_log_symbol(symbol,strategy,id)`;`live_log()` 新增 symbol/strategy 過濾下推 SQL(EXPLAIN 驗證用到索引),drift 呼叫端改帶 symbol。順帶修正潛在正確性 bug:原本抓最新 1 萬列再 pandas 過濾,多標的混雜時可能漏掉目標標的較舊的 bar |
| ~~8~~ | ✅ **AlpacaBroker 快取 client** | `execution/alpaca_broker.py` | 已修:`_client()` 改每個 broker 實例 lazy 快取一個 `TradingClient`(保留 HTTP 連線池),不再每次呼叫重建 |
| ~~9~~ | ✅ **補測試缺口** | `tests/test_config.py`、`tests/test_cli.py` | 已修:新增 `test_config.py`(Settings 預設/env 覆蓋/lru_cache/ensure_dirs,hermetic)+ `test_cli.py`(parse 輔助單元 + `info`/`backtest` 經 CliRunner 端到端)。`asyncio_mode` 警告早已在 pyproject 設 `auto` 解決(該註記過時) |
| ~~10~~ | ✅ **引擎可注入** | `backtest/walkforward.py`、`portfolio/portfolio.py`、`cli.py` | 已修:兩者新增 `engine_cls: type[BacktestEngine]=VectorBTEngine`;CLI `walkforward`/`portfolio` 接 `--engine`。walk_forward OOS 切片前正規化 tz(Backtrader 回 tz-naive);ABC `BacktestEngine.run` 補上 `timeframe` 契約。sweep 最佳化仍固定 VectorBT(向量化本是它的強項) |
| 11 | 🟠 **存活者偏差 — 已知並圈定範圍(不做)** | 資料源(`feeds/yfinance_feed.py`);未來的宇宙/掃描層 | **範圍決策(2026-07-07)**:維持 yfinance,聚焦單標的/ETF 技術面擇時,此範圍幾乎無存活者偏差,故**刻意不做**校正。⚠️ **觸發條件**:一旦要做「跨標的技術面選股 / 機會掃描器」(橫截面選股),存活者偏差 + point-in-time 宇宙成分偏差會讓那類回測**系統性高估、結論不可信** → 屆時必須(a)換 survivorship-free 資料源(Norgate / Sharadar 等,含下市標的 + 歷史成分),並(b)先建 `Universe`/`as_of_universe(date)` 抽象。**在此之前不得建任何跨標的掃描回測**。詳細分析見對話紀錄 2026-07-07 |

> **§8 進度**:#1–4、6–10 已修;#5 won't-fix(安全設計);#11 已知並圈定範圍(依產品方向刻意不做,附觸發條件)。

---

## 9. 給後續 agent 的上手須知(踩雷紀錄)

- **跑測試 / lint**:本機 venv 在 `.venv/`。`./.venv/Scripts/python.exe -m pytest -q`、`... -m ruff check src config tests`、`... -m mypy`。或 `scripts/ci.ps1` 一次跑三關(CI 鏡像)。
- **`quant` CLI**:`./.venv/Scripts/python.exe -m quant.cli <cmd>`。`quant info` 看設定與策略;實盤 `quant live/schedule` **預設 dry-run**,要真下單得加 `--execute`(且 `ALPACA_PAPER=true` 硬性守門)。
- **⚠️ committed-tree 驗證(重要教訓)**:曾因 `.gitignore` 的 `data/` 誤傷 `src/quant/data/` 整包沒進版控,CI 全掛。**驗 CI 失敗要用 `git archive HEAD` 匯出「已提交的樹」再跑測試**,不要只跑 working tree。新增檔案 commit 前先 `git check-ignore` 確認不被吃掉。詳見 memory `verify-against-committed-tree`。
- **⚠️ 「CI 綠」= GitHub Actions 綠,不是本機 ci.ps1 綠**:兩者環境有差。實例(2026-07-18 修):rich 偵測到 `GITHUB_ACTIONS` 環境變數會**強制輸出 ANSI 色碼**,typer 錯誤訊息被色碼與面板打斷,`"..." in r.output` 純文字斷言只在 CI 掛(本機重現法:`GITHUB_ACTIONS=true pytest`)。對策:`test_cli.py` 的 `CliRunner(env=...)` 拔掉所有色彩觸發(NO_COLOR/TERM=dumb/刪 CI 變數)。**push 後要 `gh run watch` 盯到綠燈才能回報綠**。
- **⚠️ Windows cp950 主控台**:印到終端 / 推 Telegram 的字串**避免 em-dash(—)等 Unicode**,會變亂碼甚至 encode 錯誤。慣例:**docstring 可用 em-dash,但 `summary()` 等會被印出的字串一律用連字號 `-`**。用 Python 讀 JSON 印中文時設 `PYTHONIOENCODING=utf-8`。
- **⚠️ alpaca-py 的 str-Enum 序列化陷阱**:`str(OrderSide.SELL)` 是 `"OrderSide.SELL"` 不是 `"sell"`(同 pyproject 記載的 UP042 議題)。任何要跟 `"buy"/"sell"` 比對的地方必須用 `str(x).split(".")[-1].lower()` 正規化。曾讓 reconcile 誤報「無保護單」+ live 的重複進場防線(`_has_open_buy`)在真 broker 上靜默失效——**離線測試抓不到**(PaperBroker 開放單恆為空),是第一次真實營運跑才逮到的。修在 `alpaca_broker.get_open_orders`。
- **⚠️ yfinance `auto_adjust=True` 會回溯改寫歷史**:除權息後全部歷史價變樣,`load_bars` 重抓會覆蓋快取。`data/integrity.py` 會**偵測 + 告警 + 記 `integrity_events.csv`**,但目前**不阻止覆蓋**(見技術債 #3)。
- **ops 寫入是 best-effort**:OMS / heartbeat / 對帳 / 告警的失敗**絕不能阻斷交易**,都包在 try/except 並大聲記 log。改這些路徑時保持此不變式。
- **失敗預設安全不變式**(勿破壞):過期資料拒單、無訊號 hold 不清倉、止損賣單永遠放行(即使日虧熔斷)、實盤下單前先對帳、告警 `send()` 永不 raise。
- **測試怎麼離線**:注入 `PaperBroker` + `NullNotifier` + 合成 OHLCV(見 `test_scheduler.py`);optional 相依(web/timescale)用 `pytest.importorskip` 跳過。
- **權威文件**:進度看 [audit_and_roadmap_ch.md](audit_and_roadmap_ch.md);使用手冊 [readme_ch.md](readme_ch.md) 與 `docs/`;藍圖對照 `quant_system_breakdown.md`。
