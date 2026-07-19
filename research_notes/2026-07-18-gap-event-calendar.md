---
title: tool gap scan: event calendar blindness vs Bloomberg ECO
status: rejected
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

## 量測結果(2026-07-18,`scripts/fomc_study.py` + `configs/fomc_dates.json`)

排定 FOMC 決議日 51 個(2020-01 ~ 2026-07,SPY 日線;緊急會議依定義排除):

| | n | mean(bps) | mean\|r\|(bps) | 盤中振幅(bps) |
|---|---|---|---|---|
| FOMC 決議日 | 51 | +14.8 | 97.9 | 155 |
| 前一日(pre-FOMC drift?) | 51 | +5.4 | 68.6 | — |
| 其他日 | 1490 | +6.9 | 83.7 | 127 |

- FOMC 日只比平常**熱 ~20%**(不是想像的倍數級);教科書的 pre-FOMC drift
  在 2020 後樣本看不到。
- **momentum(100) 實際暴露**:37/51 個 FOMC 日抱著部位,合計 **-27 bps/年**
  (其他持倉日 +1180 bps/年)——噪音級,std 110。
- **blackout 綁定頻率**:31 次執行只有 **1 次**落在 FOMC 日(2026-03-18 買入;
  延後一天反而便宜 170 bps——單一樣本,純運氣)。**6.5 年綁一次的規則=死程式碼。**

## 結論(rejected——不建事件基礎設施)

對慢速日線動量,FOMC 盲點**量化後不成立**:暴露成本噪音級、blackout 幾乎永不
綁定、連 banner 都改變不了任何決策。原提案 2(spec event_blackout)與 3(banner)
都不做。

**留下的資產**:`configs/fomc_dates.json`(官方排程日,附維護方式)與可重現的
`scripts/fomc_study.py`——**復活條件**:盤中策略超出探針規模時(1min 策略會直接
穿越 14:00 公布時刻,日線量測完全蓋不到那種秒級波動),屆時用同一份 JSON 做
盤中版研究再決定。原第 4 點(不做全量日曆/因子歸因)維持不變。
