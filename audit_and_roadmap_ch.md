# 系統檢視報告 + 待補功能藍圖

> 產出日期:2026-06-26|檢視對象:`p1_quantfinance`(美股/ETF,日線,Alpaca+yfinance)
> 方法:五維度多代理審查 → **逐條讀碼人工複核**(每條都附檔案位置與失敗情境)
> 對照文件:[quant_system_breakdown.md](quant_system_breakdown.md)(台美股+期權完整版藍圖)

---

## 摘要:我們現在在哪裡

| 里程碑(對照 breakdown) | 狀態 | 說明 |
|------|------|------|
| **MS2 研究管線通** | ✅ **已達成** | 雙引擎回測、walk-forward、參數掃描、報告、基準策略都完備 —— 這是系統**最強**的部分 |
| **MS1 資料就緒** | 🟡 **一半** | 美股日線可用,但缺**存活者偏差處理、point-in-time、基本面、完整 ETL、品質驗證強制化** |
| **MS3 Paper 上線** | 🟡 **一半,且不可信** | paper 能跑,但**缺對帳、缺告警、缺監控** —— breakdown 對 MS3 的驗收標準(「paper 連跑 4 週、對帳零差異」)**目前無法達成** |
| **MS4 小額實盤** | ❌ **禁止** | 存在 **8 個 P0 級安全漏洞**,在修好前**不應該用 `--execute` 跑無人值守排程** |

**一句話結論**:**研究層很紮實,但「真的拿去下單」的營運/安全層有致命缺口。**
最重要的一句:**在下面 P0 修好之前,請不要開 `--execute` 排程去自動下真單(即使是 paper 也會累積錯誤資料;轉正式帳戶更是不行)。**

---

# 第一部分:已驗證的漏洞與可優化項

## 🔴 P0 — 開 `--execute` 前**必修**(安全/資金相關)

> ✅ **更新(2026-07-07):Batch 0 + Batch 1(全部)已完成 —— 130 測試綠。**
> - **Batch 0**:P0 的 #1~#5、#7、#8 全部修復 + 回歸測試;另修 journal WAL(P1 #17)。
> - **Batch 1(核心)**:**#6 對帳完成** —— 新增 `ops/` 層(告警 Notifier + 對帳 reconcile + 每日報告),
>   實盤下單前會**先對帳,發現不一致就停手 + CRITICAL 告警**(fail-safe)。CLI 新增
>   `quant reconcile / report / alert-test`。細節見第三部分「第 1 批」。
> - **Batch 1(剩餘,本次完成)**:**OMS 訂單狀態機 + TCA、系統健康 heartbeat/管線監控、
>   回測 vs 實盤決策偏差** 三項全部落地(對應 M8.3/8.8、M10.2/10.3、M11.2)。新增
>   `ops/oms.py`(SUBMITTED→FILLED/… 狀態機 + `orders`/`order_events` 入庫 + `sync(broker)`)、
>   `ops/tca.py`(訊號價 vs 成交價滑價,bps/$ + 手續費 + 成交率)、`ops/health.py`
>   (heartbeat + 漏跑偵測)、`ops/drift.py`(回測預期進出場 vs 實盤實際動作一致率)。
>   `live_and_journal` 每次跑會**先 sync 前次掛單、下單後記入 OMS、收尾打 heartbeat**;
>   排程每次觸發(含假日略過)也打 heartbeat。每日報告已把 OMS/TCA/健康全部整合進去。
>   CLI 新增 `quant oms [--sync] / tca / health [--alert] / drift [--alert]`。
>   兩邊 brokers 都加了 `order_status()`(Alpaca 查真實訂單、Paper 同步成交)。

### 1. Live 會用「過期快取資料」下單,且完全沒有新鮮度檢查
- **位置**:[loaders.py](src/quant/data/loaders.py)(`load_bars` / `_cache_covers`)+ [live_runner.py:105](src/quant/execution/live_runner.py)
- **問題**:`load_bars` 只檢查快取的**起點**夠不夠早,**從不檢查終點是否到今天**。`run_live_step` 直接拿 `data.index[-1]` 當「最新 K 棒」。
- **失敗情境**:上週抓過 SPY → 這週排程實盤跑,**默默用上週的 K 棒決策下單**,沒有任何警告。搭配下面第 7 點(假日照跑)雙重放大。
- **修法**:live 路徑強制 `use_cache=False` 或加「最後一根 K 距今 > N 天就拒絕下單並告警」的 freshness gate。

### 2. 沒有「未成交掛單」感知 → 重複下單
- **位置**:[live_runner.py:107,119](src/quant/execution/live_runner.py)(`_position_qty` + `want_entry = target==1 and pos==0`)
- **問題**:對帳只看 `get_positions()`(已成交部位)。Alpaca 下單是**非同步**的,送出後尚未成交時 `pos` 仍為 0。
- **失敗情境**:排程送出買單 → 幾秒後(或下次觸發)再跑,部位還是 0 → **又買一次**,可能連買數次。
- **修法**:reconcile 時把「未成交掛單」計入目標比對(`get_orders(status=open)`),已有等量在途單就不再送。

### 3. `_target_state` 把「沒有任何訊號」當成「目標=清倉」→ 賣掉現有部位
- **位置**:[live_runner.py:54-66,117-120](src/quant/execution/live_runner.py)
- **問題**:`marks.ffill().fillna(0.0)` —— 當策略當下沒進出場訊號(資料不足、指標暖機期、策略 bug 回傳全 False),target 算出來是 0。
- **失敗情境**:某天資料只抓到一半 / 換了參數導致暖機不足 → 策略「沒訊號」被解讀成「想空手」→ **把好好的部位平掉**。
- **修法**:區分「明確想空手(有 exit 訊號)」與「沒有意見(無訊號)」;無訊號時應**維持現狀**,不是清倉。

### 4. 策略出場的市價賣單會撞到未平倉的 OCO 保護單 → Alpaca 拒單、部位關不掉
- **位置**:[live_runner.py:175-177](src/quant/execution/live_runner.py)
- **問題**:進場時掛了 bracket(伺服端 OCO 停損停利,這些賣單「佔用」了股數)。之後策略要出場時送**普通市價賣單**,Alpaca 會因「股數已被掛單佔用」拒單(或觸發 wash-trade 檢查)。
- **失敗情境**:策略發出場訊號想跑,但賣單被拒 → **部位卡住關不掉**,只能等停損停利被動觸發。
- **修法**:出場前先 `cancel` 該標的的未平倉 OCO 單,再送賣單(或直接用 OCO 的市價版收掉)。

### 5. 日虧損熔斷在 live **完全失效**,而且熔斷還會擋掉「保護性賣出」
- **位置**:[gate.py:80](src/quant/risk/gate.py) + [scheduler.py:71](src/quant/execution/scheduler.py)(`LiveConfig` 根本沒有 `max_daily_loss` 欄位)
- **問題 A(失效)**:live 路徑建的 `RiskGate` 只設了 `max_position_notional`;`report_daily_pnl()` 在 live **從來沒被呼叫**,`_daily_pnl` 永遠是 0 → 熔斷永遠不會觸發。
- **問題 B(反效果)**:`check_order` 的日虧損檢查會擋下**所有**訂單,包含**減倉/停損的賣單**。一旦熔斷觸發,連「趕快跑」的賣單也被擋 → **虧損擴大時反而無法止血**。
- **修法**:(A)live 每步先查帳戶 P&L 餵給 gate;把 `max_daily_loss` 加進 `LiveConfig`。(B)gate 對「**減少曝險的賣單**」放行,只擋「增加曝險」的單。

### 6. 完全沒有「對帳」機制(breakdown M8.6,資金安全底線)
- **問題**:系統從不比對「自己以為的部位/現金」vs「券商實際回報」。
- **失敗情境**:一次漏送/重送/部分成交,系統帳就與券商脫節,之後所有決策都建立在錯的部位上,沒人發現。
- **修法**:每次 live 前(及每日收盤後)拉 `account_summary()` + `get_positions()` 對帳,不符就**停新單 + 告警**。

### 7. 排程在**市場假日照跑**、失敗**無告警**、且「先下單後記錄」會掉稽核
- **位置**:[scheduler.py:100-105](src/quant/execution/scheduler.py)(CronTrigger `mon-fri` 無交易日曆)+ [scheduler.py:76-85](src/quant/execution/scheduler.py)(先 `run_live_step` 再 `record`)
- **問題**:(A)美股假日(週一 mon-fri 也算)照樣觸發 → 對著上一個交易日的過期 K 棒動作。(B)`run_live_step`(含送單)成功後才 `record_live_decision`;若送單成功但**記錄那步失敗(如 DB 鎖住)**,就有**已下單卻無稽核紀錄**的黑洞。(C)整段沒有 try/except + 告警,失敗就靜默。
- **修法**:接 `pandas_market_calendars` 判斷是否交易日;**先寫「即將下單」意圖再送單**(write-ahead);全程包告警。

### 8. `daily_live.ps1`:失敗當成功、且範本**預設帶 `--execute`**
- **位置**:[scripts/daily_live.ps1](scripts/daily_live.ps1)
- **問題**:PowerShell 的 `$ErrorActionPreference="Stop"` **不會**攔截原生程式(python)的非零 exit code → live 跑掛了,腳本仍以「成功」結束,工作排程器顯示綠燈,你以為有跑其實沒跑。而且範本直接寫死 `--execute`,複製貼上就是真單。
- **修法**:呼叫後檢查 `$LASTEXITCODE`,非 0 就告警 + 非零離開;範本預設**拿掉 `--execute`**(要真單才手動加)。

---

## 🟠 P1 — High(正確性 / 安全 / 營運)

| # | 問題 | 位置 | 失敗情境 / 修法 |
|---|------|------|------|
| 9 | `quant protect` **繞過風控且不入帳** | [alpaca_broker.py](src/quant/execution/alpaca_broker.py) `protect_position` | 保護單不經 RiskGate、也不寫 journal → 稽核有缺口。至少要 journal。 |
| 10 | **DSN(含密碼)洩漏**到 web 500 回應 | [timescale_store.py](src/quant/data/storage/timescale_store.py) `_ensure_schema` 的 RuntimeError 帶 `self.dsn`,web 路徑把 exception detail 原樣回傳 | 錯誤訊息遮蔽密碼;web 500 不要回傳內部 exception 全文。 |
| 11 | **品質檢查只在下載時跑**,快取/Timescale 讀取從不驗,且「issue」從不真的擋下 | [loaders.py:44-51](src/quant/data/loaders.py) | 一份壞掉的快取會被永久沿用。應在每次讀取後跑 `check_bars`,嚴重問題要擋。 |
| 12 | 未知 `timeframe` **靜默退回日線**,還存錯 key | [yfinance_feed.py:31](src/quant/data/feeds/yfinance_feed.py) `_INTERVAL.get(tf,"1d")` | `--timeframe 5m` 會抓到日線卻標成 5m 快取。應對未知 timeframe 直接報錯。 |
| 13 | **任何 feed 都沒有重試** → 一次網路抖動殺掉當日排程 | [yfinance_feed.py:33](src/quant/data/feeds/yfinance_feed.py) / alpaca_feed | 加指數退避重試(tenacity),尤其排程路徑。 |
| 14 | Parquet 是**覆寫式**儲存(毀掉更早的快取歷史)+ **非原子寫入** | [parquet_store.py:24-27](src/quant/data/storage/parquet_store.py) | 先抓 2023 起、後抓 2024 起 → 2023 資料被覆蓋掉。且寫到一半當機會**毀檔**。修:寫暫存檔再 rename;合併而非覆寫。 |
| 15 | Web **無認證 + CORS `*`** + 端點會觸發下載/重運算 | [web/app.py](src/quant/web/app.py) | `--host 0.0.0.0` 時任何區網主機都能驅動它下載/吃 CPU。至少 bind 127.0.0.1(已是預設)+ 文件警告 + 之後加簡易 token。 |
| 16 | `/api/sweep`、`/api/walkforward` 的 grid **無上限** → 組合爆炸 OOM | [optimize.py:29-36](src/quant/backtest/optimize.py) `expand_grid` 無 cap | 一個請求送巨大 grid 就能打爆記憶體。加組合數上限(如 > 2000 拒絕)。 |
| 17 | SQLite journal **無 WAL/timeout**,web+CLI+排程並發會鎖死 → 掉稽核 | [journal.py:86](src/quant/execution/journal.py) | 「database is locked」會讓「已下單的紀錄」寫不進去。加 `PRAGMA journal_mode=WAL` + `timeout`。 |
| 18 | **Web / Timescale 層在 CI 完全沒被測** | [pyproject.toml](pyproject.toml)(fastapi/httpx 不在 `[dev]`,test_web 全 skip) | CI 綠燈其實沒涵蓋 web。把 web 測試依賴放進 CI,或跑一個 `[web]` job。 |

---

## 🟡 P2 — Medium(健壯性 / 效能 / 縱深防禦)

| # | 問題 | 位置 |
|---|------|------|
| 19 | IPO/晚上市標的:首根 K 距請求起點 > 7 天 → **每次呼叫都全量重抓** | [loaders.py](src/quant/data/loaders.py) `_cache_covers`(我加的容差的副作用) |
| 20 | TimescaleStore:不保證回傳 UTC、**每次呼叫開新連線**、用 `iterrows` 逐列組資料 | [timescale_store.py](src/quant/data/storage/timescale_store.py)(這後端本來就是為 intraday 量級存在的,效能反而不行) |
| 21 | Dashboard 用 `innerHTML` 塞 API 字串(journal/sweep)**未跳脫** → 潛在 stored-XSS | [static/index.html](src/quant/web/static/index.html) `tableHTML`/`metrics` |
| 22 | Dashboard 從**第三方 CDN 載 Plotly、無 SRI** → 破壞「本機優先」承諾 + 供應鏈風險 | [static/index.html](src/quant/web/static/index.html) |
| 23 | Docker:以 **root 執行**、**硬編 quant/quant 密碼**、對外 publish **5432**、quant 服務**無 healthcheck** | [Dockerfile](Dockerfile) / [docker-compose.yml](docker-compose.yml) |
| 24 | `quant live --cash` 是**死選項**(`LiveConfig` 沒有 cash 欄位,paper broker 永遠 100k) | [cli.py](src/quant/cli.py) `live` |
| 25 | **成本/滑價模型過簡**:固定 `fees=0.0005`、**無滑價模型**、無稅費;回測會偏樂觀 | [optimize.py](src/quant/backtest/optimize.py) / vectorbt_engine |

---

## 🟢 P3 — Low / 一般優化

- 結構化 **JSON log**(breakdown 0.4 建議);目前 loguru 是純文字。
- 績效指標補 **Sortino / Calmar / 月報酬分佈**(breakdown 5.6)。
- `check_bars` 的跳空門檻對 `1wk/1mo` 會誤報。
- `_slice_from` 在 DST 不存在/模糊時刻可能出錯。
- AlpacaFeed 只驗 API key 沒驗 secret。

---

# 第二部分:對照 `quant_system_breakdown.md` 的差距分析

> ⚠️ **範圍提醒**:breakdown 是**台股+美股+期權**的完整藍圖;我們目前是**美股/ETF 日線**子集。
> 所以很多模組(M2 公司庫、M3 供應鏈、M9 事件層、台股/期權)不是「漏洞」,而是**「要不要擴」的選擇**。下表的 ❌ 要這樣理解。

| 模組 | 目前狀態 | 已有 | 主要缺口(對我們的範圍) |
|------|:---:|------|------|
| **M0 基礎建設** | 🟢 ~90% | git、venv/lock、.env、loguru、Parquet+Timescale、APScheduler+Task Scheduler、CI | **交易日曆(`pandas_market_calendars`)** 缺 → 直接造成 P0#7 假日 bug;log 非 JSON |
| **M1 資料收集** | 🟡 ~45% | 美股日線(yfinance+Alpaca)、OHLCV schema、品質檢查、快取、**point-in-time 歷史改寫偵測** | **存活者偏差、原始/調整價分離、基本面、總經、完整每日 ETL、品質強制化** |
| **M2 公司資料庫** | ❌ | — | 全缺(公司主檔/財務指標/同業比較)—— 目前非我們路線 |
| **M3 供應鏈** | ❌ | — | 全缺 —— 目前非我們路線 |
| **M4 因子庫/研究框架** | 🟡 ~30% | journal 記 session、**實驗記錄系統(M4.5:`research/experiments.py`,git-hash/參數/資料窗/成本/指標)** | **因子計算框架、因子檢定(IC/RankIC)、研究知識庫(M4.6)** |
| **M5 回測引擎** | 🟢 ~85% | 雙引擎、walk-forward、compute_metrics(+勝率/Alpha/Beta/**Sortino/Calmar**)、參數掃描、成本/滑價模型(M5.2/5.3 + TCA 校準)、**一鍵 HTML tear sheet(M5.7:`report.py`,含月報酬熱圖)** | 期權(範圍外) |
| **M6 策略/組合** | 🟢 ~60% | BaseStrategy 介面、ma_cross/momentum、portfolio 權重配置、**參數外部化(M6.3:`strategies/spec.py` + `configs/strategies.json`,回測/實盤/排程同一份 spec)**、**策略生命週期(M6.5:`research/lifecycle.py` 事前寫死晉升/退場規則 + `quant lifecycle`)** | **機會掃描器(卡存活者偏差)、波動率倒數/風險平價配置** |
| **M7 風險管理** | 🟢 ~65%(P0 已修) | RiskGate、部位上限、paper 的 bracket 熔斷、**live 熔斷已修(P0#5)、對帳(P0#6)** | **曝險彙總、風控事件完整日誌** |
| **M8 執行系統** | 🟡 ~55% | Alpaca paper、PaperBroker、market/bracket 下單、live_runner、**OMS 狀態機、每日對帳(P0#6)、TCA 滑價分析** | **斷線重連/災難復原、部分成交精修** |
| **M9 事件/資訊** | ❌ 0% | —(先前的 `core/events.py` 骨架屬零引用死碼,已於架構清理移除) | **事件行事曆(財報/除權息/FOMC)、新聞、事件驅動風控** |
| **M10 監控/告警** | 🟡 ~45% | **Web 儀表盤(唯讀)、告警通道(Telegram/log)、heartbeat + 漏跑偵測、管線監控、每日營運報告** | **備份/還原、log 轉 JSON、外部 uptime 監控** |
| **M11 績效歸因** | 🟡 ~25% | **回測 vs 實盤決策偏差追蹤(`ops/drift.py`)** | **PnL 歸因、成本歸因、人工干預日誌** |

---

# 第三部分:建議新增功能(排序後的行動藍圖)

> 排序原則:**先讓現有的東西安全可信,再擴功能**。不要在漏水的船上加新艙。

## 🚨 第 0 批:安全加固包(**在任何 `--execute` 之前**)

對應上面 P0 #1–#8。這批不做完,實盤(甚至認真的 paper)都不該開。

1. **資料新鮮度閘門** — live 拒絕用過期 K 棒(#1)。
2. **在途掛單感知** — reconcile 計入未成交單,防重複下單(#2)。
3. **無訊號 = 維持現狀**,不清倉(#3)。
4. **出場先撤 OCO 再賣**(#4)。
5. **live 熔斷修復** — 餵 P&L、`max_daily_loss` 進 LiveConfig、且熔斷放行減倉單(#5)。
6. **每日對帳** — 系統帳 vs 券商,不符即停新單+告警(#6)。
7. **交易日曆 + write-ahead 稽核 + 排程 try/except 告警**(#7)。
8. **daily_live.ps1 檢查 exit code、範本拿掉 --execute**(#8)。

## 🛠️ 第 1 批:營運骨架(才能真的達成 MS3「paper 4 週零對帳差異」)

- ✅ **告警通道**(M10.1):`ops/notify.py` —— `Notifier`(INFO/WARN/CRITICAL),設定好
  Telegram 就推播、否則落到 log。排程失敗、對帳不符、下單/被擋都會告警。`quant alert-test` 驗證。
- ✅ **每日對帳**(M8.6,補完 P0 #6):`ops/reconcile.py` —— 比對券商實際部位/掛單 vs journal:
  未追蹤部位(CRITICAL)、無保護部位、孤兒掛單(WARN)。**live 下單前先對帳,不一致就停手**。`quant reconcile`。
- ✅ **每日營運報告**(M10.5):`ops/report.py` —— 部位/權益/當日下單/被擋/對帳狀態。`quant report [--alert]`。
- ✅ **OMS 訂單狀態機 + TCA**(M8.3/8.8):訂單生命週期入庫(`orders`/`order_events`);訊號價 vs 成交價滑價分析。**(Batch 1 完成)** → `ops/oms.py`、`ops/tca.py`
- ✅ **系統健康 heartbeat + 管線監控**(M10.2/10.3):每次跑打 heartbeat + 漏跑/錯誤偵測。**(Batch 1 完成)** → `ops/health.py`
- ✅ **回測 vs 實盤偏差追蹤**(M11.2):回測預期進出場 vs 實盤實際動作一致率。**(Batch 1 完成)** → `ops/drift.py`

## 📈 第 2 批:資料完整性(讓研究結論可信)

- 🟡 **point-in-time 歷史改寫偵測**(M1.6,**本次完成**):`auto_adjust=True` 除權息後會回溯改寫全部歷史,而 `load_bars` 重抓時會**默默覆蓋快取**。新增 `data/integrity.py`:重抓前比對「已結算的重疊區間」,若歷史被改寫就**告警 + 記錄**(`integrity_events.csv`),不再無聲無息。CLI `quant integrity [SYMBOL --check]`(`--check` 只比對不覆蓋)。→ `data/integrity.py`、接進 `data/loaders.py`
- 🟠 **存活者偏差**(M1.14):**已做範圍決策 — 刻意不做**。聚焦單標的/ETF 技術面擇時,此範圍幾乎無偏差;維持 yfinance。觸發條件:日後做「跨標的技術面選股/掃描」時才需換 survivorship-free 資料源 + 建 as-of 宇宙。詳見 `architecture_map_ch.md` §8 #11。
- ⬜ **原始 + 調整價分離存放 / 正式資料源**:yfinance 只能當原型;上線前換付費源或存原始價 + 調整因子,才能真正重建 as-of 價格。**(第 2 批剩餘)**
- 🟢 **成本/滑價模型**(M5.2/5.3,**本次完成**):`backtest/costs.py` 新增 `CostModel`(fees+slippage,皆 notional 分數);兩引擎 + sweep 支援 slippage(Backtrader 把 slippage 併入 commission,避開 COC 下 `set_slippage_perc` 不可靠的問題,兩引擎成本一致);CLI `backtest --fees-bps/--slippage-bps/--calibrate`。`--calibrate` 讀 journal TCA 反推 fees+slippage 回饋回測,打通 量測→校準→回測 閉環。預設 slippage=0 保 golden 回歸。→ `backtest/costs.py`、`base/vectorbt_engine/backtrader_engine/optimize`、`cli.py`

## 🔬 第 3 批:研究深化(擴張期)

- **因子庫框架 + 實驗記錄系統**(M4.1/4.5):「沒記錄的實驗等於沒做過」——防過擬合的制度防線。
- **策略生命週期 + 機會掃描器**(M6.5/6.6)。
- **更多策略 / 資料源 / (可選)期權**。

## 🧭 明確「暫不做」的(範圍外,非漏洞)

- **台股**(shioaji、三大法人、漲跌停撮合)、**期權**、**M2 公司庫**、**M3 供應鏈**、**M9 新聞/LLM 抽取** —— 這些是 breakdown 的完整版才需要,以「先窄後寬」原則,**美股日線這條路先打穩再說**。

---

## 補充說明

1. **好消息**:漏洞集中在**執行/營運層**,研究層(回測/指標/掃描)紮實。而且因為架構分層,這些洞**大多是局部修補**,不是打掉重練。
2. **最該記住的一件事**:breakdown 的全域原則第 3 條「**失敗預設安全**」——目前系統多處違反(資料過期照跑、feed 失敗靜默、排程失敗當成功)。第 0 批的核心就是把這條補起來。
3. **建議下一步**:我先做**第 0 批的安全加固**(P0 #1–#8),做完你才敢真的讓它自動跑。要的話我現在就開工,一項一項修 + 補測試。
