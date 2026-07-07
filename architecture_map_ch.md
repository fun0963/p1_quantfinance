# 系統架構地圖 — p1_quantfinance

> **這份文件給誰**:接手本專案的人或 AI agent。讀完可在不翻遍原始碼的情況下,知道「東西放哪、為什麼這樣分層、已完成什麼、接下來做什麼、有哪些坑」。
> **最後更新**:2026-07-07(Batch 1 完成、Batch 2 開工後)。維護方式:動到架構/新增 package 時更新本檔;細部進度以 [audit_and_roadmap_ch.md](audit_and_roadmap_ch.md) 為準。

---

## 1. 一句話 + 關鍵數字

美股 / ETF 的量化交易系統:**研究(雙引擎回測)→ 紙上交易 → 排程實盤(Alpaca paper)**一條龍,強調可維護、可除錯、失敗預設安全。

| 指標 | 數值 |
|---|---|
| 原始碼 | 59 個 `.py`、約 5,500 LOC(`src/quant/`) |
| 測試 | 26 個測試檔、**143 passed / 1 skipped**;ruff + mypy 全綠 |
| Python | 3.11+(開發環境 3.13);`src/` layout,套件名 `quant` |
| 進入點 | `quant` CLI(typer,20+ 指令)+ FastAPI 唯讀儀表盤 |
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
│  core/types(Bar·Signal·Order)   config/(settings)   utils/(log)  │
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
| **provider-agnostic 型別** | `core/types`(Bar/Signal/Order)不 import 任何第三方 | 策略與風控無論回測或實盤都用同一組型別。 |
| **失敗預設安全(fail-safe)** | 實盤下單前先 reconcile、過期資料拒單、無訊號不清倉、止損永遠出得掉、營運寫入 best-effort 不阻斷交易、告警永不 crash | 見 §7 的 Batch 0 安全修復。 |
| **唯讀分析物件** | `ReconcileReport` / `TCAReport` / `HealthReport` / `DriftReport` / `QualityReport` / `MutationReport`,都有 `.summary()` 與 `.ok`/`.mutated` 布林 | 分析層只回報,由呼叫端決定停/告警,不產生副作用。 |
| **狀態機 + 稽核軌跡** | `ops/oms.py`:`OrderState` + 合法轉移表 + 附加式 `order_events` | 訂單生命週期可追;非法轉移拒絕並記錄,絕不默默套用。 |

---

## 4. 資料夾地圖(逐 package)

> 慣例:每個 package 的 `__init__.py` 通常 re-export 公開 API;測試在 `tests/test_<主題>.py`。

| 資料夾 | 職責 | 關鍵檔案與公開 API | 主要依賴 | 測試 |
|---|---|---|---|---|
| **config/** | 單一設定來源(pydantic-settings + `.env`) | `settings.py`:`Settings`、`get_settings()`(lru_cache 單例)。含 Alpaca 金鑰、`STORAGE_BACKEND`、Telegram 告警、路徑 | — | 無專屬(被各層間接測到) |
| **quant/core/** | 全系統共通型別與事件 | `types.py`:`Bar`/`Signal`/`Order` + `OrderSide`/`OrderType`/`SignalType`(frozen dataclass + Enum);`events.py`:live 事件信封(已備妥、尚未接 dispatcher) | 無 | 間接(smoke/gate/paper) |
| **quant/utils/** | 集中式 loguru 日誌 | `logging.py`:`setup_logging()`(一次性)、`get_logger()` | loguru | 間接 |
| **quant/data/** | 行情擷取 + 儲存 + 快取 + 品質 + 完整性 | `feeds/`(`DataFeed` ABC → `YFinanceFeed`/`AlpacaFeed`);`storage/`(`BarStore` ABC → `ParquetStore`/`TimescaleStore` + `get_store()` 工廠);`loaders.py`:`load_bars()`(快取+新鮮度+品質+改寫偵測);`quality.py`:`check_bars()`;`integrity.py`:`detect_history_mutation()`(point-in-time 護欄) | core、config | `test_storage/test_loaders/test_quality/test_integrity` |
| **quant/strategies/** | 純訊號邏輯(引擎無關) | `base.py`:`BaseStrategy` ABC(`generate_signals→entries/exits`、`default_grid`、`params_valid`、`warmup_bars`);`ma_cross.py`、`momentum.py`;`registry.py`:`get_strategy_cls()`/`available()` | core | `test_strategies/test_momentum_and_registry` |
| **quant/risk/** | 下單前 sizing + 否決 + 出場保護 | `base.py`:`RiskManager` ABC → `FixedFractionRisk`;`gate.py`:`RiskGate`(部位/名目/日虧上限 + kill-switch;**降險賣單永遠放行**);`bracket.py`:`Bracket`/`BracketConfig`(停損停利、可 trailing) | core | `test_gate`(4)、`test_bracket`(8) |
| **quant/backtest/** | 雙引擎回測 + 指標 + 掃描 + walk-forward | `base.py`:`BacktestEngine` ABC + `BacktestResult`;`vectorbt_engine.py`(向量化,研究/掃描)、`backtrader_engine.py`(事件驅動,擬真);`metrics.py`:`compute_metrics`/`trade_stats`/`alpha_beta`/`yearly_returns`;`optimize.py`:`sweep()`;`walkforward.py`;`plots.py`(plotly) | strategies、data、core | `test_metrics/test_optimize/test_walkforward/test_plots/test_regression` |
| **quant/execution/** | 下單、部位生命週期、稽核軌跡 | `base.py`:`Broker` ABC + `Position`;`paper_broker.py`(同步成交模擬 + `order_status`)、`alpaca_broker.py`(paper 端點 + bracket/OCO + `day_pnl` + `order_status`);`session.py`:`run_paper_session`;`live_runner.py`:`run_live_step`(單根 K 決策 + 新鮮度閘 + target/signal 模式);`scheduler.py`:`live_and_journal`(可排程單元)+ `run_schedule`(APScheduler);`journal.py`:`TradeJournal`(SQLite WAL,sessions/fills/blocked/live_log/orders/order_events/heartbeats) | risk、strategies、data、ops、core | `test_paper/test_live_runner/test_live_brackets/test_journal/test_scheduler` |
| **quant/ops/** | 無人值守營運後盾(Batch 1) | `notify.py`(`Notifier` ABC + Telegram/log/null + `get_notifier`);`reconcile.py`(帳 vs journal,CRITICAL 停手);`oms.py`(訂單狀態機 + `sync(broker)`);`tca.py`(訊號價 vs 成交價滑價);`health.py`(heartbeat + 漏跑/時鐘偏移偵測);`drift.py`(回測預期 vs 實盤實際);`report.py`(每日整合報告) | execution.journal/base、data、core | `test_ops/test_oms/test_tca/test_health/test_drift` |
| **quant/portfolio/** | 多策略權重配置與混合回測 | `portfolio.py`:`PortfolioLeg`/`PortfolioResult`/`run_portfolio()`/`load_portfolio_config()`;算混合 vs 加權平均、腿間相關、分散化比率 | backtest、strategies、data | `test_portfolio` |
| **quant/web/** | 唯讀結果儀表盤(FastAPI) | `app.py`:`create_app()`(App Factory);`routes.py`(7 端點:backtest/portfolio/sweep/walkforward/journal;薄包裝呼叫既有函式);`schemas.py`(pydantic 請求模型);`static/index.html`(plotly.js CDN,無 Node 建置) | backtest、portfolio、execution、data | `test_web`(optional-dep skip) |
| **quant/cli.py** | typer 進入點,串起所有層 | 20+ 指令:研究(`download/backtest/sweep/walkforward/portfolio/check`)、交易(`paper/live/schedule/protect/account`)、營運(`journal/reconcile/report/oms/tca/health/drift/integrity/alert-test/web`);解析輔助 `_parse_params/_parse_grid/_parse_legs` | 全部 | 間接(經 scheduler/paper 等) |
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
   - 存活者偏差(M1.14):需下市標的名單 / as-of 宇宙 → **卡在資料源決策**(yfinance 抓不到下市資料,可能要換付費源)。
   - 原始價 + 調整因子分離存放:才能真正重建 as-of 價格(動到 storage schema)。
   - 成本 / 滑價模型(M5.2/5.3):真實化回測,用 paper 的 TCA 校準。
2. **技術債清理**(見 §8,多為 P1/P2,價值高、風險低)。
3. **Batch 3 — 研究深化**:因子庫 + 因子檢定(IC/RankIC)、策略生命週期(晉升/退場)、機會掃描器。
4. **範圍外(暫不做)**:台股、選擇權、M2 公司庫、M3 供應鏈、M9 事件層。

---

## 8. 已知技術債清單(給後續 agent 的待辦池)

依「價值 / 風險」排序,審查交叉確認過:

| # | 項目 | 位置 | 影響 |
|---|---|---|---|
| ~~1~~ | ✅ **資料源重試 / 退避**(`8df365b`) | `feeds/retry.py` + 兩個 feed | 已修:`with_retries` 有界重試+指數退避,`ValueError`(無資料/不支援 tf)視為 fatal 不重試 |
| ~~2~~ | ✅ **timeframe 白名單**(`0e42de3`) | `yfinance_feed.py`、`alpaca_feed.py` | 已修:未知 tf 改嚴格查表 `raise`(檢查移到 lazy import 前);兩個 feed 都修 |
| ~~3~~ | ✅ **parquet 原子寫入 + 備份**(`1bb9027`) | `storage/parquet_store.py` | 已修:temp→`os.replace` 原子覆蓋 + 覆蓋前複製 `.bak`;寫入失敗清 `.tmp` 保原檔 |
| 4 | **web 500 可能洩漏 Timescale DSN、sweep grid 無上限** | `web/routes.py` | 錯誤 traceback 露密碼;超大 grid → OOM(僅 local-only 緩解) |
| 5 | **20 處廣捕例外未分類** | 8 個檔的 `noqa: BLE001` | 多屬 ops best-effort(合理),但實盤路徑宜分類:網路(重試)/券商(告警停手)/資料(失敗) |
| 6 | **Backtrader 引擎未填 per-trade 記錄** | `backtest/backtrader_engine.py`(`trades=None`) | TCA / 逐筆分析在該引擎缺資料 |
| 7 | **`live_log` 無索引** | `execution/journal.py` | 大量歷史查詢慢;高併發寫入即使 WAL 仍可能鎖 |
| 8 | **AlpacaBroker 每次呼叫都建新 client** | `execution/alpaca_broker.py` | 高頻排程下可能觸發 rate-limit |
| 9 | **測試缺口** | — | 無 `test_config.py`、無 CLI 端到端測、pytest `asyncio_mode` 警告 |
| 10 | **`walk_forward` / `portfolio` 寫死 VectorBT** | `backtest/walkforward.py`、`portfolio/portfolio.py` | 無法用 Backtrader 做 OOS/組合(引擎該可注入) |

---

## 9. 給後續 agent 的上手須知(踩雷紀錄)

- **跑測試 / lint**:本機 venv 在 `.venv/`。`./.venv/Scripts/python.exe -m pytest -q`、`... -m ruff check src config tests`、`... -m mypy`。或 `scripts/ci.ps1` 一次跑三關(CI 鏡像)。
- **`quant` CLI**:`./.venv/Scripts/python.exe -m quant.cli <cmd>`。`quant info` 看設定與策略;實盤 `quant live/schedule` **預設 dry-run**,要真下單得加 `--execute`(且 `ALPACA_PAPER=true` 硬性守門)。
- **⚠️ committed-tree 驗證(重要教訓)**:曾因 `.gitignore` 的 `data/` 誤傷 `src/quant/data/` 整包沒進版控,CI 全掛。**驗 CI 失敗要用 `git archive HEAD` 匯出「已提交的樹」再跑測試**,不要只跑 working tree。新增檔案 commit 前先 `git check-ignore` 確認不被吃掉。詳見 memory `verify-against-committed-tree`。
- **⚠️ Windows cp950 主控台**:印到終端 / 推 Telegram 的字串**避免 em-dash(—)等 Unicode**,會變亂碼甚至 encode 錯誤。慣例:**docstring 可用 em-dash,但 `summary()` 等會被印出的字串一律用連字號 `-`**。用 Python 讀 JSON 印中文時設 `PYTHONIOENCODING=utf-8`。
- **⚠️ yfinance `auto_adjust=True` 會回溯改寫歷史**:除權息後全部歷史價變樣,`load_bars` 重抓會覆蓋快取。`data/integrity.py` 會**偵測 + 告警 + 記 `integrity_events.csv`**,但目前**不阻止覆蓋**(見技術債 #3)。
- **ops 寫入是 best-effort**:OMS / heartbeat / 對帳 / 告警的失敗**絕不能阻斷交易**,都包在 try/except 並大聲記 log。改這些路徑時保持此不變式。
- **失敗預設安全不變式**(勿破壞):過期資料拒單、無訊號 hold 不清倉、止損賣單永遠放行(即使日虧熔斷)、實盤下單前先對帳、告警 `send()` 永不 raise。
- **測試怎麼離線**:注入 `PaperBroker` + `NullNotifier` + 合成 OHLCV(見 `test_scheduler.py`);optional 相依(web/timescale)用 `pytest.importorskip` 跳過。
- **權威文件**:進度看 [audit_and_roadmap_ch.md](audit_and_roadmap_ch.md);使用手冊 [readme_ch.md](readme_ch.md) 與 `docs/`;藍圖對照 `quant_system_breakdown.md`。
