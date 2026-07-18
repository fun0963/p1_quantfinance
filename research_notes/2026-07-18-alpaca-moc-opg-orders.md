---
title: alpaca hidden gems: MOC/OPG auction orders align live fills with backtest assumptions
status: idea
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

## 該做的事(候選,未承諾)

- 研究題目:spy_momentum 換 `cls` 或 `opg` 執行的差異——可先用歷史資料離線估
  (close vs next-open 成交價差的分佈我們的 bars 就能算),值得的話再改 executor。
- executor 支援 TIF 參數(cls/opg)是小改動;但**先算再改**,照研究紀律走。
- trailing stop:等有策略真的需要再接,不為接而接。

## 結論

維持 idea。這一條是本輪工具掃描裡「馬上可研究、直接影響回測真實度」的最高價值發現;
與 [2026-07-18-qqq-slippage-near-zero](2026-07-18-qqq-slippage-near-zero.md) 的結論(日線的真實成本是隔夜漂移)互為表裡。
