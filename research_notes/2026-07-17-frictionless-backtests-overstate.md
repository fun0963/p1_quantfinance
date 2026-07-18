---
title: frictionless backtests overstate returns
status: adopted
strategy: 
symbols: 
experiments: 
created: 2026-07-17
updated: 2026-07-17
---
## 假設

回測若不含滑價,報酬會被系統性高估;而且兩個引擎的滑價機制必須一致,否則雙引擎比對失去意義。

## 做法

- 固定種子合成資料(500 bars)+ ma_cross(10/30),兩引擎各跑 slippage=0 與 slippage=20bps,比 final equity 差額。
- 驗證點:兩引擎的報酬都應「單調」隨滑價下降,且降幅同量級。

## 結果

- 在 500 bar 固定種子資料上,ma_cross(10/30)加 20 bps 滑價:
  兩引擎 final equity 各掉約 **-3,450**(VectorBT -3,459.84、Backtrader -3,438.07),方向一致、量級相同。
- 途中發現 Backtrader 原生 `set_slippage_perc` 在 cheat-on-close 下**方向會錯**
  (加滑價反而讓報酬上升)→ 改為把 slippage 併入 commission(每側 notional 分數,P&L 等價)。

## 結論(採用 / 失敗原因)

**採用**為預設紀律:
- `quant backtest` 一律帶成本(預設 fees 5 bps;`--slippage-bps` 或 `--calibrate` 用實盤 TCA 反推)。
- 實作見 `backtest/costs.py`(CostModel + from_tca);回歸測試鎖住「滑價單調降低報酬」(兩引擎)。
教訓:別信任第三方框架的成本機制,先用已知輸入驗證方向再用。
