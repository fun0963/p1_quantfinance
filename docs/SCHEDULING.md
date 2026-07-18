# 自動排程 — 讓 live 每個交易日自己跑

> **目前採用的模式(2026-07-18 決策):不常駐。** 機器不定時關機,所以改用一鍵啟停工具
> [`scripts/trading.cmd`](../scripts/trading.cmd)——雙擊=啟動兩個排程器(spy_momentum 日線
> + qqq_scalp_1min TCA 探針),`trading.cmd stop`/`status` 停止/查看;防重複啟動;日誌在
> `logs/`。**重開機後不會自動恢復,想交易就再點一次。** 下面的工作排程器方式留作日後
> 要常駐時的選項。

兩種方式,擇一即可。**先用 dry-run 跑幾天確認決策合理,再開 `--execute`。**

> 何時跑:本系統用**日線**,Yahoo 的「當日」K 棒在**美股收盤後**才完整。所以排程時間要設在**美股收盤(美東 16:00)之後**。下面的設定已考量這點。
>
> 為什麼重複跑很安全:live 預設 `--mode target`,會把帳戶**對齊到策略的目標部位**。已經對齊就什麼都不做(冪等)。所以漏跑一天、或同一天跑兩次,都不會出問題。

---

## 方式 A:Windows 工作排程器(推薦,重開機也活)

最穩健——不需長駐程式,關機重開後排程仍在。它會呼叫 [`scripts/daily_live.ps1`](../scripts/daily_live.ps1)(裡面已是你的 live 設定,可自行編輯標的/策略/停損)。

**1. 先確認 wrapper 能手動跑通**(此時若含 `--execute` 會真的下單,先拿掉 `--execute` 測):
```powershell
powershell -ExecutionPolicy Bypass -File "D:\AI_work_claude\p1_quantfinance\scripts\daily_live.ps1"
```

**2. 註冊每個交易日的排程**(下例設**本機時間 16:15**;若你在台灣,美股收盤約為台灣隔日清晨,請改成你方便、且在美股收盤後的本機時間):
```powershell
schtasks /Create /TN "QuantDailyLive" /SC WEEKLY /D MON,TUE,WED,THU,FRI `
  /TR "powershell -ExecutionPolicy Bypass -File `"D:\AI_work_claude\p1_quantfinance\scripts\daily_live.ps1`"" `
  /ST 16:15
```

**3. 管理**:
```powershell
schtasks /Run    /TN "QuantDailyLive"     # 立即手動觸發一次
schtasks /Query  /TN "QuantDailyLive" /V  # 查看狀態/上次結果
schtasks /Delete /TN "QuantDailyLive"     # 移除
```

每次執行的決策都會寫進交易紀錄,事後用 `quant journal --live` 查。

---

## 方式 B:內建 `quant schedule`(APScheduler,機器需常開)

長駐的前景程式,適合一台一直開著的機器/VM。它用 `--tz` 做**市場時區感知**(預設美東),所以可以直接設 16:10 ET。

```powershell
$env:PYTHONPATH="src"; $PY=".\.venv\Scripts\python.exe"

# 先 dry-run(計算+記錄,不下單),--run-now 讓它啟動時先跑一次便於觀察
& $PY -m quant.cli schedule SPY --strategy momentum --params "lookback=100" `
    --broker alpaca --stop-loss 0.05 --take-profit 0.15 `
    --at 16:10 --days mon-fri --tz America/New_York --run-now

# 確認幾天沒問題後,加 --execute 開啟真實下單
& $PY -m quant.cli schedule SPY --strategy momentum --params "lookback=100" `
    --broker alpaca --stop-loss 0.05 --take-profit 0.15 `
    --at 16:10 --days mon-fri --tz America/New_York --execute
```
`Ctrl+C` 停止。程式關掉/機器重開就不會再跑——這是它和方式 A 的主要差別。

---

## 安全建議

- **先 dry-run**:兩種方式都先不要 `--execute`,看 `quant journal --live` 的決策對不對,再開真單。
- **保護部位**:進場用 `--stop-loss/--take-profit` 會自動掛 Alpaca bracket(伺服端停損停利);既有部位用 `quant protect`。
- **風控閘門**:加 `--max-position-notional` 設部位上限,多一層保險。
- **它是 paper**:目前 `.env` 為 `ALPACA_PAPER=true`,送的是模擬單。轉正式前請務必先在 paper 跑一段時間。
