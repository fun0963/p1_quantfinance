---
title: buffer parameter degenerates to buy-and-hold
status: rejected
strategy: momentum
symbols: SPY
experiments: 
created: 2026-07-17
updated: 2026-07-17
---
## 假設

momentum 的 `buffer` 參數(進出場閾值緩衝)應該能減少雜訊換手、改善淨績效。

## 做法

- `quant sweep SPY --strategy momentum` 預設網格含 buffer ∈ {0, 0.02, 0.05}
  x lookback ∈ {20, 50, 100, 150, 200},看 num_trades 與 sharpe 的關係。

## 結果

- **所有 buffer>0 的組合在 SPY 日線上幾乎都退化成 num_trades=1**:
  進場後 close 再也沒有跌破 (1-buffer) 門檻 -> 永不出場 = 買進持有。
- 這些列的 sharpe ~0.93-1.07、dd 全是 -24.5%,其實就是 SPY 本身的 buy-and-hold
  曲線,**不是動量策略的表現**;把它們跟 buffer=0 的列放在同一張排名表比較是誤導。

## 結論(採用 / 失敗原因)

**拒絕**在 SPY 這種長期上漲的大盤 ETF 上使用目前實作的 buffer(對稱閾值緩衝):
它不是「降噪」,是「關掉出場」。教訓:
1. 看到 num_trades=1 的 sweep 列要先懷疑退化,別被 sharpe 騙。
2. 若要真的降噪,方向應是「出場需連續 N 根確認」或「不對稱緩衝」,
   而且必須先在 walk-forward 下驗證(單靠 sweep 全樣本排名會選到退化解)。
