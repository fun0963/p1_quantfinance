---
title: alpaca hidden gems: MOC/OPG auction orders align live fills with backtest assumptions
status: rejected
strategy: 
symbols: SPY, QQQ
experiments: 
created: 2026-07-18
updated: 2026-07-18
---
## 觀察來源

docs.alpaca.markets「Orders at Alpaca」,2026-07-18。我們用了 Alpaca 這麼久,
只用過 market + OCO bracket;文件裡還有一整層沒動過的東西。

## 發現(依價值排序)

1. **TIF `cls`(market-on-close)/ `opg`(market-on-open)= 回測-實盤對齊的鑰匙**。
   我們的結構性問題(TCA 已量測證實):日線策略 16:10 收盤後下市價單→隔天開盤才成交,
   TCA 量到的是**隔夜跳空**而不是執行成本;回測(cheat-on-close)卻假設「以決策 bar
   的收盤價成交」。兩條對齊路線:
   - **MOC 路線**:決策提前到 ~15:45(用 99% 完成的日 bar 算訊號,對 lookback=100 的
     momentum 幾乎不影響訊號),掛 `cls` 單→**在收盤集合競價成交**,拿到的就是回測
     假設的那個收盤價。代價:訊號用的是未完成 bar(要誠實記錄這個差異)。
   - **OPG 路線**:維持 16:10 決策(完整 bar),掛 `opg` 單→在**開盤集合競價**成交。
     這對齊的是「next-open 成交」的回測慣例(Backtrader 不開 cheat-on-close 的預設),
     且集合競價深度最好。
   - 共同重點:**挑一個回測慣例,讓實盤執行方式去匹配它**,而不是讓市價單漂在
     兩種慣例之間。
2. **Trailing stop 原生支援**(`trail_percent`/`trail_price`,day/gtc)——我們的風控
   只有固定 stop/take bracket;移動停損是常見需求,券商端原生有,不用自己輪詢。
3. **盤前盤後/隔夜交易**:limit 單 + `extended_hours=true`(盤前 4:00-9:30、盤後
   16:00-20:00、隔夜 20:00-4:00 ET)。目前用不到,知道就好。
4. 限制條款確認(我們踩過的坑有官方出處):bracket **只收整股**、day/gtc、不支援
   extended hours;fractional 只有 `day`;notional 單不能 replace 只能取消重下。

## 量測結果(2026-07-18,`scripts/gap_analysis.py`,離線用快取日線)

close→next-open 價差,方向調整為「不利=正」(同 TCA 慣例),bps/次:

| | n | mean | median | std |
|---|---|---|---|---|
| SPY 無條件(全部日) | 1642 | +3.6 | +7.7 | 84 |
| SPY 進場日買入成本 | 16 | **-10.3** | +14.0 | 60 |
| SPY 出場日賣出成本 | 15 | +4.3 | +21.0 | 55 |
| QQQ 進場日買入成本 | 19 | +11.1 | +0.9 | 94 |
| QQQ 出場日賣出成本 | 18 | +13.2 | +10.0 | 67 |

- SPY 來回漂移成本 **-6.0 bps ≈ 0**(SE≈±14,統計上為零)→ 年化 **-15.5 bps/yr**,噪音級。
- 「動量慣性讓進出兩邊都吃虧」的假說在 SPY **不成立**;QQQ 來回 +24 bps(年化 +70)
  方向相符但 SE±20,證據薄弱。
- **真正的效應是變異不是偏差**:單次執行 std 57-81 bps(= 實測點差成本 ~1 bps 的
  50 倍),年化 tracking error ≈ 57×sqrt(4.8) ≈ **125 bps/yr 的隨機偏離**——
  不是虧損,是實盤權益 vs 回測的追蹤誤差。這把 qqq-slippage 筆記的
  「日線真實成本是隔夜漂移」修正為「隔夜漂移的**均值**≈0,實質是**噪音**」。

## 結論(rejected——現階段不換 executor)

期望值上換 `cls`/`opg` 賺不到東西(漂移均值≈0),買到的只有貼近回測的低變異,
還要付出「MOC 需在 ~15:45 用 99% 完成 bar 決策」的**未量化訊號翻轉風險**與
executor 複雜度。**維持現行收盤後市價單。**

**復活條件**(任一成立再回頭):
1. `quant drift` 或月度檢討顯示 tracking error 實際造成困擾(如回測/實盤權益偏離
   累積到影響 lifecycle 判讀);
2. 用 1min 資料量測「15:45 vs 收盤」的 momentum 訊號翻轉率——若 <1%/年,MOC 的
   代價近零,屆時為了對帳乾淨可以換;
3. 策略換手率大幅上升(執行次數多 → 變異年化累積變快)。

Alpaca TIF/訂單型態的參考資訊(trailing stop、opg/cls、整股限制)仍然有效,留在上文。
