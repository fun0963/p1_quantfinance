---
title: tool gap scan: AI-friendly interface - MCP and json are table stakes
status: idea
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

## 候選動作排序(未承諾)

1. 主要查詢命令加 `--json`(半天級;受益者=之後所有 AI 協作與自動化)。
2. `quant status`:單命令聚合快照(建立在 1 之上)。
3. 唯讀 MCP server(把 --json 包起來;等 1、2 成熟再說)。
4. 零成本順手項:Alpaca 官方 MCP 可直接掛進 Claude 做盤中查詢,不用自己寫。
