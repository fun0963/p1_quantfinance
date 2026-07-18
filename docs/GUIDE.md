# 使用指南 — 從安裝到上線(端到端)

這是**總入口**。系統是一條流水線:**研究 → 紙上回測 → 實盤(Alpaca paper)→ 自動化 / 部署**,
每一層都在乾淨介面後面,且**下單預設 paper-only + dry-run**(要真的送單一律得多打 `--execute`)。

各階段的細節有專門文件:
- 換標的 / 換策略 / 寫新策略 → [USAGE.md](USAGE.md)
- 自動排程(每個交易日自己跑)→ [SCHEDULING.md](SCHEDULING.md)
- TimescaleDB 儲存 / 多策略組合 / Docker → [DEPLOYMENT.md](DEPLOYMENT.md)

```
 研究                    紙上                     實盤(paper)             自動化/部署
 ┌──────────┐  survivors ┌──────────┐  same path  ┌──────────┐           ┌──────────┐
 │ download │──────────► │  paper   │ ──────────► │   live   │ ────────► │ schedule │
 │ backtest │            │ 風控閘門  │             │ Alpaca   │           │ Docker   │
 │ sweep    │            │ bracket  │             │ bracket/ │           │Timescale │
 │walkforward│           │ journal  │             │ OCO 停損  │           │portfolio │
 └──────────┘            └──────────┘             └──────────┘           └──────────┘
```

---

## 0. 安裝與設定(做一次)

```powershell
# 專案根目錄 D:\AI_work_claude\p1_quantfinance
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # 啟用 venv(之後可直接打 quant ...)
pip install -e ".[dev]"                 # 安裝套件 + 開發工具(ruff/mypy/pytest)
copy .env.example .env                  # 填 Alpaca PAPER 金鑰(只做研究/紙上可先略過)

quant info                              # 確認設定與已註冊策略
pytest -q                               # 無金鑰、無網路也能全綠
```

> **指令兩種寫法**:啟用 venv 後直接 `quant <cmd>`;若沒啟用 venv,用
> `$env:PYTHONPATH="src"; & .\.venv\Scripts\python.exe -m quant.cli <cmd>`。本文一律用前者。

---

## 1. 取得資料

```powershell
quant download SPY --start 2020-01-01   # 下載並快取(預設 parquet,在 data/)
quant check SPY                         # 資料品質:NaN / 跳空 / OHLC / 未還原分割
```
之後任何指令用到同一標的會直接讀快取(離線可用)。換標的就換代號,yfinance 自動抓。

---

## 2. 研究 — 找策略並驗證(別跳過樣本外)

```powershell
quant sweep SPY --strategy momentum                 # 參數掃描 → 排名表 + CSV + 熱力圖
quant walkforward SPY --strategy momentum           # 樣本外滾動驗證(看 WF efficiency)
quant backtest SPY --strategy momentum --params "lookback=100" --plot   # 雙引擎 + 互動圖
```
細節(換標的/策略、`--params`/`--grid`、寫新策略)見 [USAGE.md](USAGE.md)。
輸出在 `reports/`(HTML 互動圖、CSV)。

> 經驗法則:**WF efficiency** 接近 1 = 穩健;遠小於 1 = 過擬合。動量在 SPY 上 0.98,均線交叉 0.64。

---

## 3. 紙上交易 — 完整流程離線跑一遍

把研究挑定的策略,送過**完整的下單路徑**(訊號 → 風控部位 → 風控閘門 → 模擬成交),
不需金鑰、不碰網路、可重播歷史:

```powershell
quant paper SPY --strategy momentum --params "lookback=100" `
    --stop-loss 0.05 --take-profit 0.15 `
    --max-position-notional 50000 --plot
quant journal                            # 看剛才這場 session 的紀錄
quant journal --session 1                # 某場的成交/被擋明細
```
- `--stop-loss/--take-profit/--trailing-stop`:bracket 停損停利(盤中觸發)。
- `--max-position-notional/--max-daily-loss`:風控閘門(部位上限 / 單日虧損熔斷)。
- 每場都會寫進 SQLite 交易紀錄(`data/journal.db`)。

---

## 4. 實盤(Alpaca paper 帳戶)— dry-run 優先

先在 `.env` 填好 **paper** 金鑰(`ALPACA_PAPER=true`)。

```powershell
quant account                            # ① 檢查連線(唯讀:顯示現金/部位)

# ② 評估最新一根 K、對齊目標部位 —— 預設 dry-run(只算+記錄,不下單)
quant live SPY --strategy momentum --params "lookback=100" `
    --broker alpaca --stop-loss 0.05 --take-profit 0.15

# ③ 看 dry-run 決策合理後,加 --execute 才真的送單(進場自動掛 bracket 停損停利)
quant live SPY --strategy momentum --params "lookback=100" `
    --broker alpaca --stop-loss 0.05 --take-profit 0.15 --execute

quant protect SPY --stop-loss 0.05 --take-profit 0.15        # 幫「既有部位」補掛 OCO(預設 dry-run)
quant journal --live                                          # 看每次 live 決策紀錄
```
- **`--mode target`(預設)**:對齊策略「現在想要的部位」,所以一天跑一次/漏跑都安全(冪等)。
- **`--mode signal`**:只在當根 K 的進出場交叉動作。
- Alpaca 的 bracket/OCO/停損單只吃**整股**,系統會自動把零股無條件捨去。

---

## 5. 自動排程 — 讓它每個交易日自己跑

```powershell
# 內建排程(機器需常開),先 dry-run 觀察幾天
quant schedule SPY --strategy momentum --params "lookback=100" `
    --broker alpaca --stop-loss 0.05 --take-profit 0.15 --run-now
```
**目前採用「一鍵啟停」模式**:雙擊 `scripts\trading.cmd`(`stop`/`status` 停止/查看)。
日後要常駐(重開機也活)再用 **Windows 工作排程器**,完整步驟見 [SCHEDULING.md](SCHEDULING.md)。

---

## 6. 多策略組合

```powershell
quant portfolio --config portfolios/example.json
quant portfolio --legs "SPY:momentum:0.5:lookback=100; QQQ:ma_cross:0.5:fast=20,slow=50"
```
依權重配置資金、合併權益曲線,並輸出 leg 相關性 + 分散化效益。細節見 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 7. 擴展與部署

- **換儲存後端**:`.env` 設 `STORAGE_BACKEND=timescale`,資料改存進 TimescaleDB hypertable。
- **Docker**:`docker compose up -d timescaledb` 起資料庫,`docker compose run --rm quant <cmd>` 跑任何指令。

兩者皆見 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 指令總表

| 指令 | 用途 |
|------|------|
| `info` | 列出設定與已註冊策略 |
| `download` / `check` | 下載快取 / 資料品質檢查 |
| `backtest` | 雙引擎回測 + 比較(`--plot`) |
| `sweep` / `walkforward` | 參數掃描 / 樣本外滾動驗證 |
| `paper` | 紙上交易完整流程(風控 + bracket + 紀錄) |
| `account` | 檢查 Alpaca paper 連線(唯讀) |
| `live` | 評估最新 K 並對齊部位(預設 dry-run,`--execute` 才送單) |
| `protect` | 幫既有部位掛 OCO 停損停利(預設 dry-run) |
| `schedule` | 排程每個交易日自動跑 live |
| `portfolio` | 多策略資金配置 + 分散化分析 |
| `journal` | 查交易紀錄(`--session N` / `--live`) |

共用選項:`--start YYYY-MM-DD`、`--timeframe 1d`、`--strategy`、`--params`/`--grid`。

---

## 安全守則(務必)

1. **永遠先 dry-run**:`live` / `schedule` 不加 `--execute` 就只計算+記錄,不下單。看 `quant journal --live` 確認後再開真單。
2. **paper-only 硬限制**:`AlpacaBroker` 在 `ALPACA_PAPER=true` 以外會直接拒絕執行。
3. **保護部位**:進場用 `--stop-loss/--take-profit` 自動掛 bracket;既有部位用 `quant protect`。
4. **多一層風控閘門**:`--max-position-notional` / `--max-daily-loss` 設上限/熔斷。
5. **改完程式先過閘門**:`powershell -ExecutionPolicy Bypass -File scripts\ci.ps1`(ruff + mypy + pytest),與 CI 同一套。

> ⚠️ 本系統為研究/教育用途,非投資建議。轉正式帳戶前,請務必先在 paper 跑一段時間。
