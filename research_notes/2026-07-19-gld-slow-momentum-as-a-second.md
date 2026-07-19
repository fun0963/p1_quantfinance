---
title: GLD slow momentum as a second, diversifying return stream
status: rejected
strategy: momentum
symbols: GLD
experiments: 7,8
created: 2026-07-19
updated: 2026-07-19
---
## 假設

**要驗證什麼**:既有 momentum 策略類(絕對動量:close > close[t-lookback] 做多,否則空手)
疊在 GLD(黃金 ETF)上,能否成為與 spy_momentum 低相關的**第二條報酬流**。

**為什麼覺得會有效**:黃金是文獻上經典趨勢資產、與股票低相關;spy_momentum 的價值
形態是「犧牲報酬換淺回撤」(DD -14.6% vs B&H -33.7%),GLD 版若同型態即符合預期。

**事前寫死的否決條件**:WF efficiency < 0.5、或參數面孤峰、或退化成持有、
或含成本後明顯不如 B&H。教訓引用:momentum 別加 buffer([[2026-07-17-buffer-degenerates-to-hold]])。

## 做法

yfinance GLD 日線 2015-01-01 起(2901 根,涵蓋 2015-18 盤整/2019-20 走升/2022 震盪/
2024-26 大多頭),`quant check` 零 issue。sweep 預設網格 → walkforward
(`--grid "lookback=20,50,100,150,200"`,buffer 固定 0,train 504/test 126,19 折)→
雙引擎 `--slippage-bps 5` 回測(實驗 #7 vbt、#8 backtrader)。

## 結果

- **sweep(buffer=0 列)**:lookback 100 山頂 Sharpe 0.77(50→0.69、150→0.63、200→0.54、
  20→0.21),面平滑無孤峰,39 筆/11.5 年。**buffer>0 全部 num_trades=1 退化**——
  舊筆記的 buffer 陷阱在第二個標的上再度重現。
- **walk-forward:WF efficiency 0.44 < 0.5,verdict fragile/overfit(否決條件命中)**。
  mean IS Sharpe 0.747 → mean OOS 0.325;OOS 勝率 63%(12/19)。各折選中參數亂跳
  (20~200 全出現過)。災難折集中 2015-2022 盤整段(OOS Sharpe -2.83/-1.56/-1.52/-1.51);
  亮眼折全擠在 2024-2026 金牛(+1.49/+2.43/+3.07)——**全樣本數字是單一 regime 扛全場**。
  最近一折(2026H1)OOS -0.19、DD -19.3%:即使身在金牛也剛挨了一刀。
- **含成本全樣本(lookback=100,5+5 bps)**:雙引擎緊密一致——198.9%/196.6%、
  CAGR ~9.9%、Sharpe 0.75、**PSR 99.4%**、DD -24.2%、39 筆。
- **GLD B&H 同窗**:222.9%、CAGR 10.70%、Sharpe 0.71、DD -26.4%。
  → momentum 疊加**少賺 24 個百分點,回撤只淺 2.2 個百分點**。

## 結論(失敗原因)

**Rejected,三重獨立理由**:
1. 樣本外脆弱:WF 0.44 未達 0.5 門檻,OOS 表現靠 2024-26 金牛集中撐起。
2. **SPY 的價值形態沒有遷移**:spy_momentum 用報酬換到「腰斬級→-14.6%」的回撤保護;
   GLD 版付一樣的代價(少賺)卻幾乎沒買到保護(-24.2 vs -26.4)。黃金 2015-2022 的
   長震盪把慢速動量鋸得體無完膚,而它的深回撤(2020-2022)動量也沒躲掉多少。
3. 全樣本 Sharpe 0.75 + PSR 99.4% 看起來很美——**這正是教訓:全樣本統計會被 regime
   集中度撒謊,walk-forward 才是守門員**。管線照設計運作,拒收即交付。

**復活條件**:①結構不同的新假設(如跨資產 dual momentum、波動 regime 過濾)——
新想法新筆記新關卡,不做參數挖礦;②數年後資料涵蓋一輪完整金熊再重測;
③絕不因「金價又創高了」回頭翻案——那是 FOMO 不是證據。
