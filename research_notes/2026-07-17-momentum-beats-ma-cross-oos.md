---
title: momentum beats ma_cross out-of-sample on SPY
status: adopted
strategy: momentum
symbols: SPY
experiments: 
created: 2026-07-17
updated: 2026-07-17
---
## 假設

單標的擇時上,長回看動量(momentum, lookback≈100)比均線交叉(ma_cross)更不容易過擬合:
動量只有一個主參數,ma_cross 的 fast/slow 組合天生就是曲線擬合的溫床。

## 做法

- 資料:SPY 日線(yfinance,2020 起)。
- 兩策略各跑 `quant walkforward`(train 504 / test 126 bars),看 WF efficiency(OOS Sharpe / IS Sharpe)。
- 佐證:`quant sweep` 的參數面是否平滑(附近參數表現接近 = 穩健;尖峰 = 擬合)。

## 結果

- momentum WF efficiency ≈ **0.98**(OOS 幾乎不衰減)。
- ma_cross WF efficiency ≈ **0.64**(OOS 明顯衰減,過擬合傾向)。
- (此為實驗記錄系統上線前的歷史結果,無 experiment id;重跑可用 `quant walkforward SPY --strategy momentum` 復現。)

## 結論(採用 / 失敗原因)

**採用** momentum 為主力擇時策略(`configs/strategies.json` 的 `spy_momentum`,state=paper);
ma_cross 降為研究對照組(`qqq_ma_cross`,state=research,lifecycle 門檻放寬)。
教訓:參數多的策略必須先過 walk-forward 才有資格談報酬。
