---
title: tool gap scan: event calendar blindness vs Bloomberg ECO
status: idea
strategy: 
symbols: SPY,QQQ
experiments: 
created: 2026-07-18
updated: 2026-07-18
---
## 觀察來源(2026-07-18)

- **Bloomberg ECO**:經濟日曆,actual vs consensus vs previous + surprise 指數,
  宏觀交易者用來對數據發布做部位安排。
- **Bloomberg PORT**:因子曝險(growth/value/momentum/volatility)、績效歸因、
  四類壓力測試、優化器附回測。
- **TradingView**:經濟日曆 + screener 內建 Upcoming earnings date 欄。

## 我們的盲點

排程器的閘門只有:開市與否、資料新鮮度、風控、對帳。對 **FOMC/CPI/NFP 全盲**——
SPY/QQQ 擇時策略最大的單日風險正是宏觀事件日(財報日對 ETF 影響小,可忽略)。
現在的系統會在 FOMC 宣布前 10 分鐘照常進場,而我們甚至不會知道那天是 FOMC。

## 該做的事(候選,依序)

1. **先當研究題目,不是基礎設施**:回測驗證「事件日進場 vs 平常日進場」的報酬
   分佈差異。FOMC 一年只有 8 次,日程年初就公布,手工維護一個 JSON(日期清單)
   就能做實驗——不需要接任何日曆 API。
2. 若實驗證明有差:spec 加 optional `event_blackout`(事件日不開新倉、既有部位
   照常管理)。這是策略層決策,走正常研究流程(note → 實驗 → spec)。
3. 最低成本透明度:report/schedule banner 印「today is FOMC day」警示
   (同一份 JSON 餵)。
4. **不做**:全量經濟/財報日曆整合、因子歸因(PORT 級)——ETF 單標的擇時用不到。

## 結論

維持 idea。下一步是把它變成一個可回測的問題(需要:FOMC 日期 JSON + 事件日
標記的實驗),驗完數據再決定要不要進 spec。
