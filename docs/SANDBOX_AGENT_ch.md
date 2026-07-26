# 外部研究專案 → 呼叫 p1 工具組(給策略發想 agent)

> **給誰**:在 p1 之外做策略研究的 agent 或人——目前主要是
> `D:\AI_work_claude\p3_strategy`(台股日線波段,S0~S9 管線)。把本文件全文
> 交給該 agent 當開工指引即可,不需要其他文件。
>
> **2026-07-26 工具統一**:原 `D:\AI_work_claude\strategy_dev_kit` 內含 p1 引擎的
> 06-14 快照複本,**已整批刪除**——副本會漂移,它產的數字早已不等於正典引擎的數字。
> 從此**只有一套引擎**:p1。研究文件庫(想法/假設/實驗簿)留在各自的研究專案,
> 判決數字一律出自 p1。

## 角色分工

- **研究專案**(p3_strategy 等):想法、假設、資料審查、裸訊號檢驗、spec 草稿、
  實驗簿敘事——思考與證據的家。
- **p1**:引擎與關卡——回測、sweep、walk-forward、含成本雙引擎、experiments 溯源
  (帶 git hash)、生命週期、實盤執行。**判決數字只能出自這裡。**
- 落地成 p1 策略類(registry + 測試)與過關卡,由 p1 側的 agent 執行;
  研究端產出「假設 + spec + 證據草圖」。

## ⚠️ 台股尚未接通(2026-07-26 現況)

p1 目前**只有美股行為層**:yfinance/Alpaca 資料源、NYSE 交易日曆、USD 幣別、
美股成本模型(無證交稅)。台股(p3_strategy 的標的)要跑回測與實盤,需先建:
shioaji 券商類、XTAI 交易日曆、賣出 0.3% 證交稅進成本模型、TWD 幣別、
`MARKET=tw` 的 feed 註冊——完整清單見 p1 `architecture_map_ch.md` 的
「多消費者設計備忘」。快取已按市場隔離(`data/bars/us/`、未來 `data/bars/tw/`),
所以接通時不會污染既有美股資料。

**在那之前**:S0~S3(想法/假設/資料審查/裸訊號)不受影響照常做;
S4 之後需要回測工具的階段會卡住,屆時決定「先建台股層」或「先用美股標的驗證方法」。
下面的指令配方對**美股標的**現在就完全可用。

## 呼叫正典工具組(唯一正確方式)

```powershell
# 從 p1 根目錄呼叫(cwd 必須是 p1 根;PYTHONPATH 指向 src)
cd D:\AI_work_claude\p1_quantfinance
$env:PYTHONPATH = "src"
$PY = "D:\AI_work_claude\p1_quantfinance\.venv\Scripts\python.exe"

& $PY -m quant.cli info                                   # 健檢 + 可用策略清單
& $PY -m quant.cli note list --status rejected --json     # ★發想前必查:別重蹈死刑想法
& $PY -m quant.cli download GLD --start 2015-01-01        # 下載進正典快取
& $PY -m quant.cli check GLD --start 2015-01-01 --json    # 資料品質守門
& $PY -m quant.cli backtest GLD --strategy momentum --params "lookback=100" --slippage-bps 5 --json
& $PY -m quant.cli sweep GLD --strategy momentum          # 參數面(要平滑,孤峰=擬合)
& $PY -m quant.cli walkforward GLD --strategy momentum --json   # ★守門員:WF eff >= 0.5
```

- 查詢類指令都有 `--json`(stdout 只有一份 JSON,日誌走 stderr)——寫腳本用它。
- AI agent 也可走 p1 的**唯讀 MCP**(p1 的 `.mcp.json` 已提交,Claude Code 自動偵測;
  只有查詢、永無下單)。
- 市場資料正典位置:`p1\data\bars\us\`(按市場分資料夾)。**不要**在研究專案裡另存
  一份行情快取——兩份快取會被 yfinance 除權息回寫改成不同狀態,數字失去可比性。

## 回測結果去哪裡看

| 通道 | 位置 / 指令 | 內容 |
|---|---|---|
| stdout JSON | 指令加 `--json` | 指標數字(sharpe/PSR/DD/trades)、WF 各折明細——**agent 對接主通道** |
| 報表檔案 | `p1\reports\` | sweep 排名 CSV + 熱圖 HTML、`walkforward_*.csv`、`--report` 的 tear sheet HTML(K 線+進出點+benchmark+rolling Sharpe) |
| 實驗紀錄 | `quant experiments --json`(細節 `--id N`) | 每次 backtest 自動留痕:參數、資料窗、成本、指標、git hash;`git_dirty=1` 的結果不可信 |
| MCP 工具 | `list_experiments` / `get_experiment` / `list_research_notes` / `read_research_note` | 同上,唯讀 |
| 研究筆記 | `p1\research_notes\`(`quant note list --json`) | 假設與判決的敘事紀錄——想法的最終歸宿 |

## 紀律(不可協商)

1. **研究專案裡自寫的任何回測數字不可引用**——只能當初篩靈感;判決數字必須出自
   p1 引擎、記進 p1 experiments(帶 git hash)。理由:副本會漂移(strategy_dev_kit
   的下場就是這樣)。要數字就呼叫 p1,不要在研究專案裡重建引擎。
2. **發想前先查墳場**:`quant note list --status rejected`。已知死刑:momentum 加
   buffer 參數(退化成持有)、1min 高換手 ma_cross(成本吃光)、GLD momentum
   (WF 0.44 樣本外脆弱)、FOMC 事件基礎設施、MOC/OPG 對齊。
3. **不做跨標的掃描挖礦**:yfinance 無存活者偏差處理(p1 技術債 #11 明擋);
   一次一個標的、假設先行。要跨標的驗證,走 QuantConnect 的存活者無偏資料(初篩層)。
4. **想法轉正的流程**:在 p1 開假設筆記(`quant note new`,否決條件事前寫死)→
   策略類進 p1 registry → sweep → walkforward → 含成本雙引擎 → spec 或 rejected 筆記。
   流程細節見 p1 的 `walkthrough_ch.md`;策略契約(entries/exits、no look-ahead、
   warmup_bars、default_grid)見 p1 的 `src/quant/strategies/base.py` 與現成範例。

## 還成立的踩雷提醒(濃縮自舊版)

- sweep/walkforward 傳進來的參數是 float(`window=14.0`)——`__init__` 先 `int()`。
- 訊號只能用當根與過去的資料;`.shift(-n)` = 假業績製造機。
- `warmup_bars()` 要蓋住最長 lookback,否則 walk-forward 各折起頭 NaN 少交易。
- 全樣本 Sharpe/PSR 再漂亮都可能是單一 regime 扛全場——walk-forward 才是守門員。

*(舊版完整教學已隨引擎副本退役;正典知識庫= p1 的 walkthrough_ch.md、
architecture_map_ch.md、readme_ch.md 與 research_notes/。)*
