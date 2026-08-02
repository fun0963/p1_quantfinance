# 專案慣例(給 Claude Code)

## 溝通與版控

- **回覆用繁體中文**;**commit message 也用繁體中文**(2026-07-26 起改用;
  首行仍照 `type(scope): 摘要` 慣例,type/scope 保持英文,如
  `fix(ops): 排程器改用 WMI 生成,不再隨啟動它的 session 死亡`)。
- 向使用者回報 commit 時用「**中文說明(hash)**」格式,hash 只當附註——
  裸 hash 會被誤讀成檔名。
- **「CI 綠」= GitHub Actions 綠**,不是本機 `scripts\ci.ps1` 綠。每次 push 後
  `gh run watch` 盯到綠燈才能回報綠;本機閘門只是預檢。

## 營運鐵律(違反會賠錢或毀資料)

- **單寫者鐵律**:同一個券商帳戶**只能有一個**在跑 `--execute` 的排程器——兩個會
  讓每次進場下兩倍部位。防呆:`EXECUTE_HOST` 必須等於執行機 hostname,否則 CLI 拒絕
  下單(雲端容器固定叫 `quant-live`)。搬上雲後,本機 `.env` 也要設同一個值。
- **本機模式**:交易排程器只能由使用者親手雙擊 `scripts\trading.cmd` 啟動——Claude
  啟動的行程會隨 session 收攤而死(2026-07 三次實證,曾害整週沒交易)。Claude 只做
  查核回報。**雲端模式**見 `docs/CLOUD_DEPLOY_ch.md`(容器 restart 政策接手,不需要人點)。
- **`--execute` 永遠是人在命令列的明確動作**;spec 檔**永遠不能**含 `execute` 欄位。
- **真錢帳戶(非 paper)禁止**,直到 paper 連跑 4 週對帳零差異 + 使用者明確拍板。
- Claude **不代下單、不代改部位**——需要下單時給使用者指令,由使用者執行。
- 主控台輸出字串一律 ASCII(Windows cp950);em-dash 只在 docstring 用。

## 研究紀律

- 假設筆記**先行**(`quant note new`,否決條件事前寫死)→ sweep(參數面要平滑)
  → **walk-forward efficiency ≥ 0.5**(守門員,不可跳過)→ 含成本雙引擎回測
  → spec 或 rejected 筆記。失敗紀錄最值錢,拒收也是合法交付。
- 全樣本 Sharpe/PSR 會被 regime 集中度撒謊——只有 walk-forward 算數。
- 發想前先查墳場:`quant note list --status rejected`。

## 權威文件

架構與踩雷 `architecture_map_ch.md`(§9 陷阱必讀)|使用手冊 `readme_ch.md`
|走查流程 `walkthrough_ch.md`|進度 `audit_and_roadmap_ch.md`
|外部研究專案呼叫配方 `docs/SANDBOX_AGENT_ch.md`
