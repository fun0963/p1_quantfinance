---
title: gap scan: 24h AI trader article (555.pdf) vs our loop
status: adopted
strategy: 
symbols: 
experiments: 
created: 2026-07-19
updated: 2026-07-19
---
## 觀察來源

01_ref/555.pdf =「我用 Fable 5 打造了一個 24 小時 AI 交易員」(等號, denghao.substack.com,
2026-07-19)。17 頁影像 PDF(網頁列印)。內容是 6 步閉環的**概念教學文**:
掃描(Scan) -> 訊號(Signal) -> 計畫(Plan) -> 風控(Risk) -> 執行(Execution) -> 監控(Monitor)。
注意:全文無程式碼、無回測、無實際績效數據,後 3 頁是 NT$1,680 指令包廣告;
語境偏 crypto 永續合約(24h 市場、穿倉/斷頭)。參考價值在「檢查清單」,不在實作。

## 逐項對照(他們 30 個子項 vs 我們)

**已有、且多半更嚴謹**:
- 價格資料源(yfinance 日線/Alpaca 1min,輪詢制)、趨勢判定(momentum 策略本body)、
  部位大小(fraction + max_position_notional cap)、自動下單、部位追蹤(OMS/journal/web)、
  執行紀錄(journal + experiments + TCA + git 溯源,比他們的「截圖留存」強)、
  下單/被擋/崩潰即時推播(scheduler notifier)、狀態監控(health heartbeat + reconcile)、
  定期覆盤(lifecycle/drift/tca,退場門檻事前寫死——他們只有「週末看看勝率」)、
  失效條件(我們的新鮮度閘:stale bar 不交易)。
- 滑價:他們「事前限制」,我們「事後量測」(TCA)——流動性 ETF 小單實測 ~0 bps,
  量測比限制誠實([[2026-07-18-qqq-slippage-near-zero]])。

**已有但沒開(本次最有價值的發現)→ ✅ 2026-07-19 已啟用**:
- **當日虧損斷路器 `max_daily_loss`**:gate.py 已實作(擋新倉、放行減倉,設計正確)、
  scheduler 已接 day P&L(= audit P0 #5 的程式面早已修完)——但 configs/strategies.json
  的 risk 都沒設值,**斷路器裝了沒通電**。
- 啟用時的關鍵發現:`AlpacaBroker.day_pnl()` 是**帳戶級**(equity − last_equity),
  不是策略級——per-spec 數值必須用「帳戶當日波動」的尺來設。原本口頭提的
  「scalper 設 60」是錯的(SPY ~45k 部位跌 0.15% 就會鎖住 scalper)。
- 最終設定:三個 spec 統一 `max_daily_loss: 1000`(≈ 權益 1%;SPY 跌 ~2.2% 才觸發)。
  語意:「帳戶當日 -1000 → 全系統停止加新倉,減倉照常」。只擋異常日,不擋正常紅盤。
  research 態的 qqq_ma_cross 也設,日後排程自帶保險。

**有數據地拒絕過(不因這篇文章復活)**:
- 新聞/事件監控、事件日暫停開倉:FOMC 研究量化過,慢速日線動量事件盲視成本 ~0
  ([[2026-07-18-gap-event-calendar]]);MOC/OPG 進場時點對齊亦然
  ([[2026-07-18-alpaca-moc-opg-orders]])。復活條件寫在各筆記。
- Watchlist/screener 跨標的掃描:存活者偏差技術債擋回測;單人 2 標的也不需要。

**真沒有、但屬「等策略需要」的緩議項**(同 multi-timeframe 的事前緩議邏輯):
- 訊號評分/多訊號共振(突破 3 分+爆量 2 分>4 分才進場)——任何組合訊號都得先過
  sweep/walk-forward,否則是偽嚴謹;等第二支策略家族。
- 進場區間(limit/stop 進場)、多階段止盈(TP1 出一半)、追蹤止損/保本移動——
  現行策略都是訊號翻轉出場,用不到;等有策略在回測中證明需要。
- 波動度閘(ATR 過高暫停)——要做就得先做研究,不拍腦袋。

**真沒有、便宜、可選**:
- `quant watch` 加成交量異常條件(如「單根量 > 20 根均量 3 倍」)——無狀態一次性
  設計可直接容納,idea generation 用途,不涉回測。
- 券商端保護單成交(stop 觸發)目前要等下一個 tick 的 OMS sync 才看得到,
  無即時推播——盤中策略規模化時再補。
- 跨策略總曝險閘:今天 2 策略 fraction 0.45+0.5≈0.95 隱性安全;第 3 支 spec 或
  fraction 總和 >1.0 時必須做(trading.ps1 啟動時檢查 fraction 總和是便宜版)。

## 結論

架構清單上我們沒有系統性缺口;30 子項中絕大多數是「已建且更嚴謹」「有數據拒絕」
或「資產類別不適用」(穿倉保護:現貨 ETF 多頭無此風險)。可執行的產出:
① ✅ 三個 spec 設 risk.max_daily_loss=1000(2026-07-19 完成,零新碼,見上);
② watch 加 volume 條件——可選,未做;③ 其餘全部掛觸發條件緩議(見各段)。

**反向觀察更重要**:該文的迴圈是 掃描->直接實盤,完全沒有研究層——沒有回測、
沒有樣本外、沒有成本模型、沒有實驗紀錄、沒有 paper 階段、沒有對帳。我們系統的
護城河恰恰是那條研究生產線;這篇文章不該把我們往「多做 live 花樣、少做驗證」拉。
