---
title: measured intraday slippage on QQQ is ~0 bps avg
status: adopted
strategy: 
symbols: QQQ
experiments: 
created: 2026-07-18
updated: 2026-07-18
---
## 假設

(整個 Batch 4 的終點)回測用的 5 bps 滑價假設,對「超高流動性 ETF 的小額市價單」
可能過度保守;要用真實成交量測取代猜測。

## 做法

- `qqq_scalp_1min` TCA 探針:2026-07-17 美股盤中,`schedule --every 5min --execute`
  (Alpaca paper,~2.6k/單,fraction 0.5)。
- 決策價 = 最新完成 1min bar 收盤;下單在 bar 收後 ~30-90 秒;`quant tca` 彙總
  intended vs avg_fill。

## 結果

- **19/19 全成交**;avg slippage **-1.0 bps**(中位數 0.0,最差 +7.5,單筆分佈約 ±5-13 bps)。
- 總成本 **-$4.99 / $49,267 名目**(负=價格改善);commission $0(Alpaca 免佣)。
- 解讀:QQQ 這種深度,小額市價單的執行成本均值 ≈ 0,單筆雜訊 ±數 bps
  (含 bar 收到成交之間 ~1 分鐘的漂移)。

## 結論(採用 / 失敗原因)

**採用**為校準基準:
1. 小額 QQQ/SPY 市價單:真實執行成本 ~0-2 bps,遠低於原本假設的 5 bps。
2. `CostModel.from_tca` 會把負滑價 floor 到 0(價格改善不當紅利)——`--calibrate`
   現在會給出 fees~0 + slippage~0;**日線回測仍建議手動保守給 3-5 bps**
   (樣本只有一天、一檔、小額;且日線的真實成本是隔夜漂移不是點差)。
3. 樣本要繼續累積:探針每逢開盤日重啟即可(`schedule --spec qqq_scalp_1min
   --broker alpaca --every 5min --fraction 0.5 --execute`)。
