---
title: tool gap scan: AI-friendly interface - MCP and json are table stakes
status: adopted
strategy: 
symbols: 
experiments: 
created: 2026-07-18
updated: 2026-07-18
---
## 觀察來源(2026-07-18)

- **Alpaca 官方 MCP server**(alpacahq/alpaca-mcp-server):65 個工具——帳戶/部位/
  下單(market/limit/stop/**trailing stop**/bracket/多腿選擇權)/歷史與即時行情/
  screener/新聞;明文支援 Claude Code、Claude Desktop、Cursor 等。
- **QuantConnect Mia V2**:agentic「AI quant developer」——發想→寫碼→自動跑回測→
  抓 runtime error 自動修→再回測的閉環。
- **OpenBB**:「AI agents get access to the same governed data, context, tools, and
  interface」——agent 是平台的一等公民使用者,不是外掛聊天框。
- **Composer / TrendSpider**:自然語言→策略/測試。
- **Bloomberg**(補充 2026-07-18):終端主打之一是 **ASKB**——「agentic AI built
  for the speed of the markets」對話式介面;PORT 另有「AI Portfolio Commentary」
  自動從歸因數據產生報告敘事。連最老牌的機構終端都把 agentic AI 當一級功能。

業界共識已清楚:AI 友善 = 給 agent 結構化、可程式呼叫的介面(MCP / JSON),
和給人的 UI 平行存在。

## 我們的缺口(有實戰證據)

1. **CLI 沒有 --json**:所有輸出是人類表格。實戰教訓 2026-07-17:我(AI)為了等
   成交,對 pandas 表格寫 grep,欄寬假設錯→白等 400 秒。人類可讀表格對 agent 是
   脆的介面;當時正確做法(查 DB 結構化狀態)之所以繞路,就是因為沒有現成的
   機器可讀出口。
2. **沒有單一狀態快照**:health/account/positions/open orders/lifecycle 要跑
   5 個命令自己拼。`quant status --json` 一發到位會改變 AI 協作效率。
3. **沒有 MCP server**:agent 操作本系統只能走 shell。Alpaca 已示範標準做法。
   風險邊界必須先想清楚:**MCP 只暴露唯讀查詢**,--execute 類動作永遠不進去
   (與 web 唯讀同一哲學、與「spec 永不含 execute」同一鐵律)。
4. exit code 紀律只做了一半:lifecycle breach exit 1(好),其他命令未盤點。

## 反向優勢(已經很 AI 友善的部分)

architecture_map/walkthrough 的文件密度;spec/experiments/journal 底層全是
JSON/SQLite——資料早就 machine-readable,缺的只是**出口**。另:cp950 限制只管
主控台,--json 輸出與 HTML 檔案走 UTF-8 不受限(HTML 報告要中文化也因此可行)。

## 候選動作排序

1. ✅ **主要查詢命令加 `--json`(2026-07-18 完成)**:15 個查詢類指令。契約=
   stdout 僅一份 JSON(日誌走 stderr)、`command`+`data`(+`ok`)、exit code 不變、
   數字不變字串、ensure_ascii;每個指令都有 json.loads round-trip 測試釘住。
   驗收:`quant tca --json` 一行取回 19 筆成交 avg -1.0 bps——上次要 grep 表格
   白等 400 秒的查詢,現在是結構化一發。
2. ✅ **`quant status` 聚合快照(2026-07-18 完成)**:單命令聚合 帳戶+部位+對帳+
   health+近期決策/訂單+TCA 彙總+specs 清單(原本要跑 5 個指令)。設計重點:
   **分區降級**——broker 掛掉只標記該區 error,絕不遮蔽本地狀態;`--offline` 跳過
   網路;lifecycle 只列 spec 不跑評估(保持秒回,verdicts 用 `quant lifecycle --all`);
   overall ok = 各檢查區 AND,exit code 一致。
3. ✅ **唯讀 MCP server(2026-07-18 完成,三部曲收官→本筆記轉 adopted)**:
   `quant.readapi` 共用查詢層(--json 與 MCP 同一來源,永不漂移)+ FastMCP stdio
   10 工具 + `.mcp.json`(Claude Code 自動偵測)+ `quant mcp`。鐵律「只有查詢、
   永無下單」用 AST 掃描測試釘死(禁止兩模組呼叫 submit/cancel/protect/sync 等);
   另有真協定 in-memory 握手測試。
4. 零成本順手項(仍開放):Alpaca 官方 MCP 可直接掛進 Claude 做盤中行情查詢,
   與本系統 MCP 互補(他們管市場,我們管自家帳本與研究庫)。
