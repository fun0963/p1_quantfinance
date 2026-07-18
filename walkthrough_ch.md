# 專案走查規劃表 — 從策略發想到檢討,一步一步

> **這份文件給誰**:系統的使用者(你)。照順序走一輪,就等於把整套系統的研究紀律跑過一遍。
> 每一步都有:要跑的指令、該看什麼、過關標準、以及我們自己踩過的陷阱。
> 走查一輪的合理節奏:階段 0-4 一個下午;階段 5-6 觀察數天;階段 7-8 變成日常。

---

## 階段 0:環境健檢(5 分鐘,每次開工前)

| # | 動作 | 指令 / 位置 | 看什麼、過關標準 |
|---|------|------------|----------------|
| 0.1 | 設定與策略清單 | `quant info` | `alpaca_paper: True`、金鑰 `set`、策略至少 `ma_cross`/`momentum` |
| 0.2 | 券商連線(唯讀) | `quant account` | `is_paper: True`、看得到 equity 與現有部位 |
| 0.3 | 程式閘門 | `powershell -File scripts\ci.ps1` | ruff + mypy + pytest **全綠**(250+ 測試);紅了先修再研究 |
| 0.4 | 系統健康 | `quant status`(帳戶+對帳+heartbeat+近期決策一發看完;細查再用 `quant health`) | overall: ok;broker 區、health 區無 FAILED/STALE |

**陷阱**:終端輸出若見亂碼,是 cp950 主控台問題(輸出字串一律 ASCII 的慣例就是為此)。

---

## 階段 1:策略發想(先寫下來,再動手)

| # | 動作 | 指令 / 位置 | 看什麼、過關標準 |
|---|------|------------|----------------|
| 1.1 | 查失敗紀錄,別重蹈 | `quant note list --status rejected` | 你的點子是否已被驗死過(如:buffer 退化、1min 高換手) |
| 1.2 | **先寫假設再驗證** | `quant note new "我的想法" --status idea` | 筆記裡寫清楚:要驗什麼、為什麼覺得會有效 |
| 1.3 | **成本預算前置檢查**(心算即可) | — | 預期每筆毛利 >> 來回成本(日線 ~10-20 bps)?不是就別回測了 |

**陷阱**:先跑回測再回頭編故事=事後諸葛。筆記先行是防自欺的第一道線。
**教訓案例**:1min ma_cross 999 筆 × 16 bps ≈ 160% 名目被成本吃掉——成本預算 30 秒就能否決它,我們卻是回測完才學到。

---

## 階段 2:資料準備與守門

| # | 動作 | 指令 | 看什麼、過關標準 |
|---|------|------|----------------|
| 2.1 | 下載(自動選源:日線 yfinance、盤中 Alpaca) | `quant download SPY --start 2020-01-01`(盤中加 `--timeframe 1min`) | Saved N bars |
| 2.2 | 品質檢查 | `quant check SPY` | `QualityReport: OK`;有 ISSUE 先解決 |
| 2.3 | 歷史改寫偵測(除權息陷阱) | `quant integrity SPY --check` | 無 mutation;有的話代表 yfinance 回溯改寫了歷史,留意快取備份 `.bak` |

**陷阱**:yfinance `auto_adjust=True` 除權息後會改寫全部歷史;1min 資料只能用 Alpaca(yfinance 只留 7 天)。

---

## 階段 3:研究迴圈(sweep → walk-forward → 含成本回測)

| # | 動作 | 指令 | 看什麼、過關標準 |
|---|------|------|----------------|
| 3.1 | 參數掃描 | `quant sweep SPY --strategy momentum` | ①參數面**平滑**(鄰近參數表現接近,孤峰=擬合)②**`num_trades=1` 的列直接跳過**(退化成買進持有) |
| 3.2 | 樣本外驗證(**不可跳過**) | `quant walkforward SPY --strategy momentum` | **WF efficiency ≥ 0.5**(≈1 穩健;<0.5 過擬合;<0 壞掉)、OOS 勝率、各折選中參數是否亂跳 |
| 3.3 | 含成本雙引擎回測 + tear sheet | `quant backtest SPY --strategy momentum --params "..." --slippage-bps 5 --report` | ①雙引擎數字接近(差異=執行建模)②Sharpe/Sortino/Calmar、月報酬熱圖有沒有「單一年份扛全場」③**成本行**有印出來 |
| 3.4 | 回顧實驗紀錄 | `quant experiments`(細節 `--id N`) | 每次回測自動留痕;`git_dirty=1` 的結果不可信(程式碼沒 commit) |
| 3.5 | 報告目測 | `reports/report_SYMBOL_STRATEGY.html` | 瀏覽器開;權益曲線是否靠少數大跳、回撤形狀、逐月是否穩定 |

**陷阱**:零摩擦回測系統性樂觀——**永遠帶 `--slippage-bps`**;sweep 只有全樣本排名,倖存的參數必須過 walk-forward 才算數。

---

## 階段 4:落成 spec + 事前寫死退場規則

| # | 動作 | 指令 / 位置 | 看什麼、過關標準 |
|---|------|------------|----------------|
| 4.1 | 寫 spec(params/風控/生命週期一次進版控) | 編輯 `configs/strategies.json` | `risk` 上限合理(部位名目 × fraction 要過得了自己的 cap!);`lifecycle` 的退場門檻**現在**寫死(rolling Sharpe 下限、回撤下限、最低活動度) |
| 4.2 | 驗 spec 正確 | `quant backtest --spec 我的spec --no-log` | 跑出來的參數與你想的一致 |
| 4.3 | 健康基線 | `quant lifecycle 我的spec` | HOLD;現在的視窗數字就是日後比較的基線 |
| 4.4 | 進版控 | `git add configs/ && git commit` | spec 是 reviewed diff,不是 shell 歷史 |

**鐵律**:spec **永遠不能**含 `execute` 欄位(載入直接報錯)——上實盤是人在命令列的明確動作。
**陷阱**:fraction × equity > max_position_notional 會讓每次進場都被風控擋掉(我們踩過:0.95×99k > 50k cap)。

---

## 階段 5:紙上與 dry-run 驗證(免費的排練)

| # | 動作 | 指令 | 看什麼、過關標準 |
|---|------|------|----------------|
| 5.1 | 離線全管線 | `quant paper SPY --strategy ... --stop-loss 0.05 --take-profit 0.15 --plot` | 有成交、有被擋(如果你設了緊的風控)、exit_reasons 合理 |
| 5.2 | 真 broker dry-run(不下單) | `quant live --spec 我的spec --broker alpaca` | 決策合理:bar 新鮮、target state 對、想下的 qty 過得了風控 |
| 5.3 | 連續觀察數日 | 每天跑 5.2 或 `schedule`(不加 --execute) | `quant journal --live`:決策序列連貫、無 stale-data block |

---

## 階段 6:paper 實盤(--execute)與排程

| # | 動作 | 指令 | 看什麼、過關標準 |
|---|------|------|----------------|
| 6.1 | 首單人工確認 | `quant live --spec 我的spec --broker alpaca --execute` | 下單後 `quant oms`:訂單 FILLED、價格合理 |
| 6.2 | 對帳 | `quant reconcile --broker alpaca` | **clean**;WARN 無保護單就補 `quant protect` |
| 6.3 | 排程化 | 日線:`quant schedule --spec X --broker alpaca --execute`;盤中:加 `--every 5min --fraction ...` | 啟動 banner 正確;`quant health` 出現 scheduler heartbeat |
| 6.4 | 一鍵啟停(✅ 已做) | 雙擊 `scripts\trading.cmd`(= start);`trading.cmd stop` / `status` | OK 兩行 + PID + log 路徑;防重複啟動(SKIP);**不會在重開機後自動恢復——每次開機想交易就再點一次**(刻意選擇:機器不定時關機,先不常駐;要常駐再走 docs/SCHEDULING.md 的工作排程器) |

**鐵律**:同一帳戶**一個 symbol 只給一個策略**(兩策略共用 SPY 會互賣對方部位);每次觸發自動走:開市檢查 → OMS 同步 → 對帳(不符即停+告警)→ 新鮮度閘 → 風控 → 下單。

---

## 階段 7:日常營運監控(每天 5 分鐘)

| # | 動作 | 指令 | 看什麼、過關標準 |
|---|------|------|----------------|
| 7.1 | 每日報告(可推 Telegram) | `quant report`(`--alert` 推播) | 部位/當日下單/被擋/對帳狀態一頁看完 |
| 7.2 | 健康 | `quant health` | 該跑的 heartbeat 都 ok、不 stale;沉默=有事 |
| 7.3 | 決策軌跡 | `quant journal --live` | 每個交易日都有記錄(冪等設計,漏跑一天也安全,但要知道) |
| 7.4 | 儀表盤總覽 | `quant web` → http://127.0.0.1:8000 | Backtest/Portfolio/Journal 分頁核對數據 |
| 7.5 | 告警通道 | `quant alert-test`(設定後跑一次) | Telegram 有收到;CRITICAL 要能吵醒你 |

**自動化提示**:查詢類指令都支援 `--json`(stdout 只有一份 JSON、日誌走 stderr、exit code 不變)——寫腳本或讓 AI 代查時用它,別解析人類表格。

---

## 階段 8:量測 → 校準 → 檢討(每週/每月)

| # | 動作 | 指令 | 看什麼、過關標準 |
|---|------|------|----------------|
| 8.1 | 滑價量測 | `quant tca` | fill rate、avg/median/worst slippage bps;樣本累積中(首批:QQQ 小單 ~0±5 bps) |
| 8.2 | 用實測成本重跑回測 | `quant backtest --spec X --calibrate` | 對照假設成本的版本差多少;**樣本少時仍手動給 3-5 bps 保守值** |
| 8.3 | 回測 vs 實盤偏差 | `quant drift SPY --strategy momentum` | 實盤有沒有漏掉回測想做的交易(agreement 比率) |
| 8.4 | 退場紀律執行 | `quant lifecycle --all`(可排程,breach 時 exit 1) | 任何 RETIRE-REVIEW 都要處理:降額/下架/寫筆記,**不跟自己談判** |
| 8.5 | 結論落地 | `quant note new "..." --status adopted/rejected --experiments N,M` | 每輪研究至少一篇筆記;**失敗紀錄最值錢** |
| 8.6 | 月度回顧 | `quant experiments --strategy X` + `note list` | 這個月試了什麼、留下什麼、殺掉什麼 |

---

## 快速對照:一輪完整走查的最小指令序列

```powershell
# 0 健檢
quant info; quant account; quant health
# 1 發想
quant note list --status rejected
quant note new "動量加波動率過濾" --status idea --strategy momentum
# 2 資料
quant download SPY; quant check SPY
# 3 研究
quant sweep SPY --strategy momentum
quant walkforward SPY --strategy momentum
quant backtest SPY --strategy momentum --params "lookback=100" --slippage-bps 5 --report
quant experiments
# 4 落成 spec(編輯 configs/strategies.json 後)
quant backtest --spec my_spec --no-log; quant lifecycle my_spec
# 5-6 排練與上線
quant live --spec my_spec --broker alpaca          # dry-run 觀察數日
quant live --spec my_spec --broker alpaca --execute
quant schedule --spec my_spec --broker alpaca --execute
# 7-8 日常
quant report; quant health; quant tca; quant drift SPY --strategy momentum
quant lifecycle --all
quant note new "結論" --status adopted --experiments 7,8
```

> 權威文件:架構與踩雷 [architecture_map_ch.md](architecture_map_ch.md);使用手冊 [readme_ch.md](readme_ch.md);進度 [audit_and_roadmap_ch.md](audit_and_roadmap_ch.md)。
