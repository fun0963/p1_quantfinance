---
title: lookback=100 survives costs and OOS - keep spy_momentum spec
status: adopted
strategy: momentum
symbols: SPY
experiments: 1, 2, 3, 4
created: 2026-07-17
updated: 2026-07-17
---
## 假設

`spy_momentum` spec 釘的 lookback=100 是研究期(零成本時代)選的;
要驗證它在(a)含成本、(b)樣本外、(c)對照鄰近參數之下仍然是對的選擇。

## 做法

- 資料:SPY 日線 2020-01-02 -> 2026-07-16(1642 bars,`quant check` 乾淨)。
- `quant sweep SPY --strategy momentum`(15 組預設網格,零成本排名)。
- `quant walkforward SPY --strategy momentum`(19 折,train 504 / test 126)。
- `quant backtest` x2,同樣 10 bps/side(fees 5 + slippage 5):
  spec 參數(lookback=100)vs 挑戰者(lookback=50)。雙引擎交叉。

## 結果

- sweep:lookback=100/buffer=0 居首(sharpe 1.11、16 筆);buffer=0 之下鄰近參數
  (50: 1.01、20: 0.94、150: 0.93)平滑遞減,非孤立尖峰。
- walk-forward:**WF efficiency 0.96**(OOS 1.24 vs IS 1.29)、OOS 勝率 0.89,
  各折選中 lookback 在 20-200 漂移仍穩 -> 是動量「家族」穩健,不是單點擬合。
  最差折 = 2022 上半熊市(-19.2%)。
- 含成本(exp #1/#2 = spec,#3/#4 = 挑戰者,git e5fa7ad、dirty=0):
  lookback=100:sharpe **1.09**、dd -14.6%、Calmar 0.88、16 筆。
  lookback=50 :sharpe 0.97、dd -22.4%、Calmar 0.53、26 筆(交易多 -> 成本拖累大)。
- `quant lifecycle spy_momentum`:近 252 bars sharpe 1.37、dd -6.3% -> HOLD。

## 結論(採用 / 失敗原因)

**採用(維持現狀)**:lookback=100 在三個維度都站得住,`configs/strategies.json`
不需改動。次選 50 在風險調整後全面落後且換手較高。
下一步:讓 paper 交易累積成交 -> 之後用 `--calibrate` 以實測 TCA 取代假設的 5 bps 滑價;
lifecycle 排程化,月頻重驗此結論。
