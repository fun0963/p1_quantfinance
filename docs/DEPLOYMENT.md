# 部署與擴展 — Phase 4(TimescaleDB / 多策略組合 / Docker)

Phase 4 把系統從「單機研究」推向「可擴展、可部署」。三件事互相獨立,可分開採用:

1. **TimescaleDB 儲存** — 把 bar 資料從本地 parquet 換成時序資料庫(應付盤中/tick 量級)。
2. **多策略組合** — 在多個策略/標的之間配置資金,看分散化效果。
3. **Docker 化** — 一鍵帶起資料庫 + 整套 CLI,適合長駐主機/VM。

---

## 1. TimescaleDB 儲存後端

系統用 `BarStore` 介面把儲存層抽象掉([base.py](../src/quant/data/storage/base.py)),
`get_store()` 依設定回傳對應後端。**換後端是改設定,不是改程式**。

**切換**:在 `.env` 設(預設仍是 `parquet`,完全相容舊行為):
```ini
STORAGE_BACKEND=timescale
TIMESCALE_DSN=postgresql://quant:quant@localhost:5432/quant
```

**起一個本地 TimescaleDB**(用下面的 Docker):
```powershell
docker compose up -d timescaledb
```

**安裝驅動**(可選 extra,沒裝時其餘功能不受影響):
```powershell
pip install -e ".[timescale]"
```

之後一切照舊 —— `quant download`、`quant backtest`、`quant sweep` 會自動寫/讀 Timescale:
```powershell
$env:STORAGE_BACKEND="timescale"
& $PY -m quant.cli download SPY --start 2015-01-01   # 寫進 bars hypertable(冪等 upsert)
& $PY -m quant.cli backtest SPY --strategy momentum  # 從 Timescale 讀
```

**從 parquet 搬資料到 Timescale**(一次性,把舊快取灌進去):
```python
# migrate.py — 在 STORAGE_BACKEND=timescale 下執行
from quant.data.storage import ParquetStore
from quant.data.storage.timescale_store import TimescaleStore

src, dst = ParquetStore(), TimescaleStore()
for symbol, tf in [("SPY", "1d"), ("QQQ", "1d")]:
    df = src.load(symbol, tf)
    if df is not None:
        print(dst.save(symbol, tf, df))
```

> 設計重點:`bars` 是以 `ts` 分割的 **hypertable**,主鍵 `(symbol, timeframe, ts)`;寫入是
> `ON CONFLICT ... DO UPDATE` 的 **upsert**,所以重複下載同一段資料不會重覆、可安全重跑。

---

## 2. 多策略組合配置

把資金分配到多個「leg」(標的 × 策略 × 權重),各自回測後合併成一條組合權益曲線,
並算出**分散化效益**(組合 Sharpe ÷ 各 leg 加權平均 Sharpe)與各 leg 報酬的相關係數。

**用 JSON 設定檔**(資料即配置,新增組合不用改程式)—— 範例
[portfolios/example.json](../portfolios/example.json):
```json
{
  "name": "balanced_2strat",
  "cash": 100000,
  "start": "2020-01-01",
  "legs": [
    {"symbol": "SPY", "strategy": "momentum", "params": {"lookback": 100}, "weight": 0.5},
    {"symbol": "QQQ", "strategy": "ma_cross", "params": {"fast": 20, "slow": 50}, "weight": 0.5}
  ]
}
```
```powershell
& $PY -m quant.cli portfolio --config portfolios/example.json
```

**或用 inline 參數**快速試:
```powershell
& $PY -m quant.cli portfolio --legs "SPY:momentum:0.5:lookback=100; QQQ:ma_cross:0.5:fast=20,slow=50"
```

輸出:各 leg 的報酬/Sharpe/最大回撤、合併後組合的指標、leg 報酬相關矩陣,以及
「blended Sharpe vs 加權平均」的分散化判讀。權重會自動正規化成總和 1。

> 範例結果:SPY-momentum 與 QQQ-ma_cross 相關 0.7(都是美股 beta),組合 Sharpe 1.04 ≈ 加權
> 平均 1.0(分散化效益有限),但最大回撤 -19% 落在兩腿(-14%、-27%)之間 —— 波動被平滑了。
> 想要真正的分散化,挑**相關性低**的 leg(不同資產類別/負相關策略)。

---

## 3. Docker 化部署

[`Dockerfile`](../Dockerfile) 把 `quant` CLI + 所有引擎 + Timescale 驅動打包成一個 image;
[`docker-compose.yml`](../docker-compose.yml) 再加上 TimescaleDB 服務。

> 本機需先安裝 **Docker Desktop**。改完 compose 後可先 `docker compose config` 驗證語法。

**帶起資料庫**:
```powershell
docker compose up -d timescaledb
```

**跑一次性指令**(用 `run --rm`,跑完即清):
```powershell
docker compose run --rm quant download SPY --start 2020-01-01
docker compose run --rm quant backtest SPY --strategy momentum
docker compose run --rm quant portfolio --config portfolios/example.json
```
容器內 `STORAGE_BACKEND=timescale`,自動連到 compose 網路上的 `timescaledb`。
`data/ logs/ reports/` 以 volume 掛載到主機,輸出會留存。

**長駐排程**(每個交易日自動跑 live,**預設 dry-run**):
```powershell
# Alpaca 金鑰由你的 shell 環境(或本資料夾的 .env)帶入,不寫進 image
docker compose --profile live up scheduler
```
`scheduler` 服務放在 `live` profile,所以一般 `docker compose up` **不會**啟動它。
確認 journal 決策無誤後,再把 compose 裡 scheduler 的 `command` 末端加上 `--execute` 才會真的下單。

> 安全:image 不含任何金鑰;`.env` 已在 `.dockerignore` 排除。預設一切是 paper + dry-run。

---

## 小結:Phase 4 帶來什麼

| 能力 | 之前 | 之後 |
|------|------|------|
| 儲存 | 本地 parquet(每標的一檔) | 可選 TimescaleDB hypertable(盤中/tick 可擴展) |
| 策略 | 單一策略回測 | 多策略/多標的資金配置 + 分散化分析 |
| 部署 | 本機 venv | Docker image + compose(DB + CLI + 排程) |

三者都向後相容:不設 `STORAGE_BACKEND` 就還是 parquet,不碰 Docker 就還是本機跑。
