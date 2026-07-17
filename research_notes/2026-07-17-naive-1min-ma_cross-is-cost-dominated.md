---
title: naive 1min ma_cross is cost-dominated
status: rejected
strategy: ma_cross
symbols: QQQ
experiments: 5, 6
created: 2026-07-17
updated: 2026-07-17
---
## 假設

(Batch 4 分鐘級架構的第一次實測)短均線交叉(5/20)搬到 1min 也許能捕捉盤中趨勢;
就算不行,也要量化「為什麼不行」。

## 做法

- QQQ 1min(Alpaca IEX),2026-06-01 起 ~30,483 bars(約 6 週)。
- `quant backtest --spec qqq_scalp_1min --slippage-bps 3 --engine both`
  (成本 8 bps/side、16 bps 來回;雙引擎交叉;exp #5/#6)。

## 結果

- **-81.8% / 6 週**,999 筆交易,分鐘級年化 sharpe -36.8;雙引擎一致(-81.83 vs -81.26)。
- 算術即死刑:999 筆 x 16 bps 來回 ≈ **160% 名目被成本吃掉**——訊號品質根本無關緊要,
  換手率先把你殺了。

## 結論(採用 / 失敗原因)

**拒絕**天真高換手 1min 均線交叉。教訓:
1. 分鐘級策略的第一道檢查是「**成本預算**」:預期每筆毛利若沒有明顯大於來回成本,不用回測了。
2. 但此 spec(`qqq_scalp_1min`)**保留為 TCA 探針**:它的用途是穩定產生成交來量測真實滑價,
   不是賺錢。其 lifecycle 規則因此只看活動度(min_trades),獲利門檻形同關閉——事前寫明,避免誤讀。
3. 之後若真做盤中策略,方向是低換手(持有數小時)+ 成本預算前置。
