# 操作手冊 — 換標的 / 換策略

這份文件說明**不改程式碼**就能換標的、換策略、調參數,以及要寫**全新策略**時的最小步驟。

---

## 0. 前置(每個新終端機做一次)

```powershell
# 在專案根目錄 D:\AI_work_claude\p1_quantfinance
$env:PYTHONPATH = "src"
$PY = ".\.venv\Scripts\python.exe"      # 之後都用 $PY 代表 venv 的 python
```

> 之後所有指令格式為:`& $PY -m quant.cli <command> ...`
> 例:`& $PY -m quant.cli info`

查看目前可用的策略與設定:

```powershell
& $PY -m quant.cli info
```

---

## 1. 換標的 — 只要改代號

把指令裡的 `SPY` 換成任何 [Yahoo Finance](https://finance.yahoo.com/) 代號即可,
yfinance 會**自動下載並快取**到 `data/`(第二次起讀快取,離線可用)。

```powershell
& $PY -m quant.cli backtest QQQ  --strategy momentum --plot
& $PY -m quant.cli backtest AAPL --strategy ma_cross --params "fast=10,slow=50"
& $PY -m quant.cli backtest TLT  --strategy momentum
```

常見代號:`SPY`(S&P500)、`QQQ`(那斯達克100)、`IWM`(小型股)、
`AAPL`/`NVDA`/`MSFT`(個股)、`TLT`(20年期美債)、`GLD`(黃金)、`DIA`(道瓊)。

**強制重新下載**(資料疑似過期或有誤):加 `--no-cache`(僅 `backtest` 提供),
或直接 `& $PY -m quant.cli download QQQ --start 2010-01-01`。

---

## 2. 換策略 — 用 `--strategy`

目前 registry 內建:

| 名稱 | 說明 | 主要參數 |
|------|------|----------|
| `ma_cross` | 均線交叉(快線上穿慢線做多) | `fast`, `slow` |
| `momentum` | 時序動量(價格高於 N 日前做多) | `lookback`, `buffer` |

- **指定單一組參數**(`backtest` 用)— `--params "k=v,k=v"`:
  ```powershell
  & $PY -m quant.cli backtest SPY --strategy momentum --params "lookback=50,buffer=0.02"
  ```
- **掃描參數網格**(`sweep` / `walkforward` 用)— `--grid "k=v1,v2;k2=v1,v2"`:
  ```powershell
  & $PY -m quant.cli sweep SPY --strategy momentum --grid "lookback=20,50,100,200;buffer=0.0,0.05"
  ```
- 不給 `--params` / `--grid` 時,使用該策略的**預設值 / 預設網格**(見 `quant info`)。

---

## 3. 建議的完整研究流程

換**任何**標的或策略,都照這個順序走一遍:

```powershell
# (a) 先驗資料品質(NaN / 跳空 / 未還原分割)
& $PY -m quant.cli check QQQ

# (b) 參數掃描,找出候選(輸出排名表 + CSV + 熱力圖 HTML)
& $PY -m quant.cli sweep QQQ --strategy momentum

# (c) 樣本外驗證 —— 關鍵步驟,別跳過!
#     看 WF efficiency:接近 1 = 穩健;遠小於 1 = 過擬合
& $PY -m quant.cli walkforward QQQ --strategy momentum

# (d) 用挑定的參數出互動圖(權益曲線 + 回撤)
& $PY -m quant.cli backtest QQQ --strategy momentum --params "lookback=100" --plot
```

**輸出檔案**(都在 `reports/`,已 gitignore):
- `sweep_<sym>_<strategy>_<metric>.csv` — 完整掃描排名
- `heatmap_<sym>_<strategy>_<metric>.html` — 參數熱力圖(互動)
- `walkforward_<sym>_<strategy>.csv` — 每折樣本內/外結果
- `equity_<sym>_<strategy>.html` — 權益曲線 + 回撤(互動,瀏覽器開)

---

## 4. 指令速查

| 指令 | 用途 | 範例 |
|------|------|------|
| `info` | 列出設定與已註冊策略 | `info` |
| `download` | 下載並快取歷史資料 | `download QQQ --start 2010-01-01` |
| `check` | 資料品質檢查 | `check SPY` |
| `backtest` | 雙引擎回測 + 比較(可 `--plot`) | `backtest SPY --strategy momentum --plot` |
| `sweep` | 向量化參數掃描 + 熱力圖 | `sweep SPY --strategy ma_cross` |
| `walkforward` | 樣本外滾動驗證 | `walkforward SPY --strategy momentum` |

共用選項:`--start YYYY-MM-DD`、`--timeframe 1d`、`--sort-by sharpe|total_return_pct|max_drawdown_pct`。

---

## 5. 寫一個全新策略(需少量程式碼)

例如想加「RSI 均值回歸」或「布林通道」。只要 **2 步**,加完後 `backtest` /
`sweep` / `walkforward` / `--plot` **全部自動支援**。

### 步驟 1 — 新增策略檔 `src/quant/strategies/<your_strategy>.py`

```python
from __future__ import annotations
import pandas as pd
from quant.strategies.base import BaseStrategy


class MyStrategy(BaseStrategy):
    name = "my_strategy"                       # CLI 用的名稱

    def __init__(self, window: int = 14, level: float = 30.0) -> None:
        window, level = int(window), float(level)   # sweep 列會傳 float,先轉型
        super().__init__(window=window, level=level)
        self.window = window
        self.level = level

    @classmethod
    def default_grid(cls) -> dict[str, list]:       # sweep / walkforward 的預設網格
        return {"window": [7, 14, 21], "level": [20.0, 30.0]}

    def warmup_bars(self) -> int:                   # 指標暖機所需的 bar 數
        return self.window

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        close = data["close"]
        # ... 你的邏輯,算出布林的進場/出場 ...
        entries = ...   # pd.Series[bool]
        exits = ...     # pd.Series[bool]
        return pd.DataFrame(
            {"entries": entries.fillna(False), "exits": exits.fillna(False)},
            index=data.index,
        )
```

**契約**:`generate_signals` 必須回傳含 `entries`、`exits` 兩個布林欄的 DataFrame,
index 與輸入相同。這是兩個回測引擎共用的格式。

### 步驟 2 — 在 `src/quant/strategies/registry.py` 註冊一行

```python
from quant.strategies.my_strategy import MyStrategy

REGISTRY = {
    MACrossStrategy.name: MACrossStrategy,
    MomentumStrategy.name: MomentumStrategy,
    MyStrategy.name: MyStrategy,        # ← 加這行
}
```

完成!驗證:

```powershell
& $PY -m quant.cli info                                  # 應看到 my_strategy
& $PY -m quant.cli backtest SPY --strategy my_strategy --plot
```

別忘了在 `tests/` 加對應測試(可參考 `tests/test_momentum_and_registry.py`)。

---

> ⚠️ 本系統為研究/回測用途,非投資建議。實盤下單在 Phase 3 風控就緒前維持停用。
