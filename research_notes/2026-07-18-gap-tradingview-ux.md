---
title: tool gap scan: TradingView-style chart-first UX we lack
status: idea
strategy: 
symbols: 
experiments: 
created: 2026-07-18
updated: 2026-07-18
---
## 觀察來源

2026-07-18 實際操作 tradingview.com/chart(工具列/Indicators 對話框/Screener 實頁)+
官方 features 頁、Pine Script docs、Strategy Tester support 文件;另參考 TrendSpider 文件。

## 他們有、我們沒有(人性化缺口)

1. **K 線圖為中心,買賣點標在價格圖上**。TradingView 所有分析都在 K 線上發生;
   策略回測後每筆進出直接畫在 bar 上。我們的 tear sheet 只有 equity/drawdown/月報酬
   熱圖——「策略到底在圖上哪裡進出」這個最直觀的 sanity check 完全缺席。
2. **市場條件告警**:13 種內建條件、畫線工具告警、Pine 告警、最多 5 條件組合、
   webhook/email/app 推播。我們的 Telegram 告警只有 ops 事件(排程失敗/對帳不符),
   沒有「價格穿越 X 就通知我」。
3. **Bar replay**:9 段速度重播歷史,人肉走盤訓練/檢視策略當下情境。
4. **Screener**:400+ 欄位、70+ 交易所、預設欄組(Performance/Valuation/Dividends/
   Technicals...)、熱圖。註:我們對「跨標的**回測**」有存活者偏差技術債 #11 擋著,
   但「現時宇宙的即時篩選」只做 idea generation 不涉歷史回測,不受阻——只是產出的
   想法仍不能用 yfinance 做跨標的歷史驗證,要標注清楚。
5. **財報/經濟日曆整合**:screener 直接有 Upcoming earnings date 欄(另見 event
   calendar 筆記)。
6. **資料透明度小細節**:圖表角落常駐「Market closed - One update every 5 seconds -
   NASDAQ by Cboe One」。我們的 report/tear sheet 不說資料多新鮮、來自哪個源。
7. **參數即時重繪**:settings 改參數圖表即時更新;我們是改 CLI 參數→重跑→開新 HTML。
8. **Multi-timeframe**:同圖疊不同週期指標(request.security);TrendSpider 進一步做
   自動趨勢線/型態辨識+跨週期告警。我們的策略只吃單一 timeframe。
9. **Pine 的短**:官方文件最小策略約 7 行;我們要 Python class + spec + CLI。
   (這條對我們影響小——寫碼的人是 AI。)
10. 社群 15 萬腳本 / ideas feed / Store、內建 paper trading、圖上拖拉下單改 bracket。

## 值得做的候選(依 CP 值排序)

- A. ✅ **tear sheet 加「K 線 + 進出場標記」圖(2026-07-18 完成)**:candlestick +
  entry/exit 三角標記(兩引擎 trade 表都吃:vbt records_readable 與 backtrader log,
  後者無 exit price 用 bar close 補)。實測 SPY momentum:1643 根 K + 16 進 16 出,
  hover 帶 PnL。同批完成 benchmark 疊圖(見 gap-report-metrics 筆記)。
- B. report 印**資料來源與最後 bar 時間戳**(透明度,幾行的事)。
- C. 市場條件告警:scheduler 已有 IntervalTrigger + Telegram,加一種「條件告警」
  任務(如 close crosses MA)——屬新功能面,想清楚再做。
- D. multi-timeframe 策略支援:工程較大,等第二支正式策略真的需要再說。

## 不打算學的

社群/Store、16 圖同屏、100+ 券商整合、圖上手動下單——單人系統打不了也不需要打的仗。
