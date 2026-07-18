---
title: tool gap scan: report metrics vs QuantConnect and QuantStats
status: idea
strategy: 
symbols: 
experiments: 
created: 2026-07-18
updated: 2026-07-18
---
## 觀察來源

QuantConnect docs(backtesting/results、optimization、live-trading/results)、
QuantStats(ranaroussi/quantstats,Python 圈 tear sheet 事實標準)、
TradingView Performance Summary 文件。2026-07-18。

## 他們的報告有、我們沒有

**QuantConnect backtest 結果頁**:PSR(Probabilistic Sharpe Ratio)、estimated strategy
capacity、portfolio turnover、long/short exposure 圖、benchmark 疊圖、rolling statistics
(整套統計的時序版)、alpha/beta/information ratio/Treynor/tracking error、expectancy、
**asset plot 上標注 order events**、Orders/Trades/Logs/Code 分頁(每次回測存 code
snapshot——我們的 experiments.db 記 git hash + dirty flag,精神相同,已有)。
**QC live 頁**:實盤 equity 與「Out of Sample Backtest」**疊圖對帳**——我們的 drift
是文字版 agreement 比率,沒有視覺疊圖。

**QuantStats tear sheet**:profit factor、payoff ratio、VaR/CVaR、ulcer index、
recovery factor、Kelly criterion、tail ratio、common sense ratio、skew/kurtosis、
連勝連敗數、Monte Carlo(bust/goal 機率)、整份報告 vs benchmark 對照。

**TradingView**:Performance Summary 全指標拆 All/Long/Short 三欄。

我們現有:total return、CAGR、Sharpe、Sortino、Calmar、MaxDD、num_trades、win rate、
月報酬熱圖、equity/drawdown 圖、雙引擎對照、成本行。

## 缺口排序(對單人 ETF 擇時真正有用的)

1. ✅ **benchmark 疊圖(2026-07-18 完成)**:tear sheet 權益圖疊**自身標的** buy-and-hold
   (單標的擇時的誠實 null hypothesis;同起始資金),指標表加 Benchmark 與
   Excess vs benchmark 兩列。首次實測立刻見效:SPY momentum 總報酬 116.8% vs
   B&H 151.0% → 超額 **-34.2%**(2020 起、5bps 成本)——策略價值在淺回撤
   (-14.6%)不在絕對報酬,以前的 tear sheet 看不見這件事。
2. **rolling Sharpe 時序圖**:lifecycle 內部已算 trailing sharpe 卻沒畫出來;
   畫出來就能「看見」策略衰退,退場討論會具體很多。
3. **profit factor + payoff ratio**:交易員通用語言,計算成本趨近零。
4. **turnover(年化換手)**:直接接上成本預算檢查(walkthrough 階段 1.3 的心算
   可以變成報告裡的實數)。
5. **PSR**:把「這個 Sharpe 是不是運氣」量化,樣本短時特別誠實。
6. Monte Carlo bust/goal 機率:錦上添花。

**跳過**:capacity(我們的規模無意義)、Treynor/IR/tracking error(機構指標)、
Long/Short 拆欄(目前 long-only)。

## 另:資料面觀察(存活者偏差解方的具體選項)

QC 的 **US Equity Security Master**:~27,500 檔美股、1998 年起、point-in-time 的
下市/併購/換代碼事件流——技術債 #11(存活者偏差)若日後要解,這是 Norgate/Sharadar
之外的第三個具體選項。限制:它只是 metadata,價格資料要另購(AlgoSeek),且綁 QC
生態。維持 scoped-out 不變,只是把選項記下來。

## 反向發現(我們有、他們沒有)

QC 雲端優化文件明說:最多 3 參數 grid、**沒有內建 walk-forward / 防過擬合機制**,
只給一句警語。我們的 sweep→walkforward→WF efficiency 流程在這點上反而更嚴。

## 結論

先不動手。若動手,從 1-4 開始(全是 report.py/metrics.py 的增量,不碰交易路徑)。
