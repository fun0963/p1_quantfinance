# 24/7 雲端部署(Oracle Cloud Always Free)

> **為什麼**:本機不定時關機,排程器就跟著死——2026-07-20~24 因此整週沒交易。
> 4 週 paper 驗收需要連續 20 個交易日,家用機撐不住,所以把**執行層**搬上常開主機。
> **研究仍留在本機**(雲端映像刻意不裝回測引擎)。

---

## 0. 先看懂三件事(不看會踩)

**① 單寫者鐵律 —— 這是本次唯一會賠錢的風險**
兩個排程器對同一個 Alpaca 帳戶下單 = 每次進場都下兩倍部位。防呆已寫進程式:
`EXECUTE_HOST` 設定值必須等於執行機的 hostname,否則 `--execute` 直接拒絕啟動。
雲端容器 hostname 固定為 `quant-live`;**你的筆電 .env 也要設 `EXECUTE_HOST=quant-live`**
——正是這一行讓筆電從此無法下單(dry-run 與研究不受影響)。

**② Oracle Always Free 的兩個實務地雷**
- **ARM(Ampere A1)常年缺貨**:建立時常見 `Out of host capacity`。多試幾次、換
  可用區(AD)、或改用 **AMD micro(1/8 OCPU, 1GB RAM)**——本專案的輕量映像跑得動,
  但 1GB 記憶體務必加 swap(見 §2)。
- **閒置回收**:Oracle 會回收「7 天內 CPU 使用率長期低於 20%」的 Always Free 實例。
  我們的排程器大部分時間在睡,**正好符合閒置定義**。兩條路:①接受風險、被收了重建
  (資料在 volume,重建約 15 分鐘);②把帳戶升級為 Pay As You Go(Always Free 額度
  內仍不收費,但**超出就會計費**,需自行評估)。**先用①,不要為此開卡冒險。**

**③ 不要把儀表盤開到公網**
`quant web` 目前**無認證**(已知技術債)。VM 只開 SSH(22),要看儀表盤走 SSH
通道:`ssh -L 8000:localhost:8000 ubuntu@<VM_IP>` 再開本機瀏覽器。

---

## 1. 建立 VM

Oracle Cloud 主控台 → Compute → Instances → Create instance:

| 項目 | 建議 |
|---|---|
| 映像 | **Ubuntu 22.04 或 24.04**(Oracle Linux 也可,以下指令以 Ubuntu 為準) |
| Shape | 首選 `VM.Standard.A1.Flex`(ARM,2 OCPU / 12 GB 即綽綽有餘);缺貨則 `VM.Standard.E2.1.Micro`(AMD) |
| SSH 金鑰 | 上傳你自己的公鑰(或讓它產生後**立刻下載私鑰**) |
| 網路 | 預設 VCN 即可,**不要**開放任何額外 port |

> 帳號註冊需信用卡驗證(Always Free 不扣款)。這一步與金鑰保管由你操作,我不經手。

---

## 2. 主機初始化

```bash
ssh ubuntu@<VM_IP>

# Docker + compose plugin
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
sudo systemctl enable --now docker        # 開機自動啟動 → 容器 restart 政策才有意義

# 只有 1GB RAM 的 AMD micro 才需要:加 2GB swap
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 3. 部署

```bash
git clone https://github.com/fun0963/p1_quantfinance.git
cd p1_quantfinance
```

> 私有 repo 的話用 `gh auth login` 或部署金鑰;公開 repo 直接 clone。

**建立 `.env`(手動貼上,永不進 git)**:

```bash
cat > .env <<'EOF'
ALPACA_API_KEY=你的paper金鑰
ALPACA_SECRET_KEY=你的paper密鑰
ALPACA_PAPER=true
EXECUTE_HOST=quant-live
TELEGRAM_BOT_TOKEN=你的token
TELEGRAM_CHAT_ID=你的chat_id
ALERTS_ENABLED=true
EOF
chmod 600 .env
```

**把本機歷史帶過去**(保留 TCA 樣本與實驗紀錄;在**本機**執行):

```powershell
scp -r D:\AI_work_claude\p1_quantfinance\data ubuntu@<VM_IP>:~/p1_quantfinance/
```

**啟動**:

```bash
docker compose -f docker-compose.live.yml up -d --build   # ARM 首次 build 約 5-10 分鐘
docker compose -f docker-compose.live.yml ps
docker compose -f docker-compose.live.yml logs -f spy-momentum
```

看到 `scheduler up: 1 job(s) ...` 就成功。`--run-now` 會立刻做一次決策(非交易日會顯示 skip)。

---

## 4. 驗收(當場做完)

```bash
# 1. 帳戶連得上、是 paper
docker compose -f docker-compose.live.yml run --rm spy-momentum account --json

# 2. 心跳與對帳
docker compose -f docker-compose.live.yml run --rm spy-momentum status

# 3. 單寫者閘確實生效(應該拒絕並顯示 pinned to host)
docker compose -f docker-compose.live.yml run --rm -e EXECUTE_HOST=someone-else \
  spy-momentum live SPY --execute

# 4. 告警通道
docker compose -f docker-compose.live.yml run --rm spy-momentum alert-test
```

**然後在本機**把 `EXECUTE_HOST=quant-live` 加進 `.env`,並確認筆電已無法下單:

```powershell
quant live SPY --execute      # 應該被拒:pinned to host 'quant-live'
```

---

## 5. 監控(死人不會報告自己死了)

三層,由內而外:

| 層 | 機制 | 涵蓋什麼 |
|---|---|---|
| 容器 | `restart: unless-stopped` | 行程崩潰、VM 重開機 |
| 排程器 | Telegram 告警(已內建) | 下單、被擋、對帳不符、崩潰 |
| 外部 | 下面的 cron + healthchecks.io | **排程器安靜地死掉**、VM 整台失聯 |

**心跳監視 cron**(在 VM 上 `crontab -e`)——heartbeat 逾時會發 Telegram:

```cron
0 * * * * cd ~/p1_quantfinance && docker compose -f docker-compose.live.yml run --rm --no-deps spy-momentum health --alert >/dev/null 2>&1
```

**VM 存活監視**(可選,免費):到 healthchecks.io 開一個 check,把 ping URL 填入:

```cron
*/15 * * * * curl -fsS --retry 3 https://hc-ping.com/<你的UUID> >/dev/null
```

VM 掛掉或斷網 → healthchecks 發信給你。

---

## 6. 日常操作

```bash
docker compose -f docker-compose.live.yml logs -f --tail 100 qqq-scalp   # 看即時日誌
docker compose -f docker-compose.live.yml run --rm spy-momentum status   # 一頁快照
docker compose -f docker-compose.live.yml run --rm spy-momentum tca      # 滑價量測
docker compose -f docker-compose.live.yml restart                        # 重啟兩個排程器
docker compose -f docker-compose.live.yml down                           # 停止(部位不動)

git pull && docker compose -f docker-compose.live.yml up -d --build      # 更新程式或 spec
```

**改 spec 的流程不變**:本機改 `configs/strategies.json` → commit → push → VM `git pull` +
`up -d --build`。spec 烤進映像,所以跑著的系統永遠等於 git 說的那個版本。

**把資料抓回本機分析**(研究引擎在本機):

```powershell
scp ubuntu@<VM_IP>:~/p1_quantfinance/data/journal.db D:\AI_work_claude\p1_quantfinance\data\
```

---

## 7. 遷移檢查清單

- [ ] VM 上 `.env` 建好、`chmod 600`、`ALPACA_PAPER=true`
- [ ] `data/` 已從本機複製過去
- [ ] 兩個容器 `up` 且日誌顯示 `scheduler up`
- [ ] **本機 `.env` 加 `EXECUTE_HOST=quant-live`**,並實測筆電下單被拒
- [ ] **本機不再執行 `scripts\trading.cmd`**(待辦 #32 從此作廢——那是本機模式的儀式)
- [ ] Telegram 告警測試通過
- [ ] cron 心跳監視已設
- [ ] 記下 VM 的公開 IP 與 SSH 金鑰位置

---

## 8. 已知限制

- 這個映像**不能跑回測**(`backtest`/`sweep`/`walkforward` 會 ImportError)——刻意的,
  研究留在本機。要在雲端做研究就用根目錄的完整 `Dockerfile`。
- **真錢帳戶仍然禁止**:前提不變(paper 連跑 4 週對帳零差異 + 你明確拍板)。
  金鑰上雲後,轉真錢前要重新做一次金鑰與存取控制的評估。
- Always Free 的閒置回收風險見 §0②;被回收就重建,資料在 `data/`(記得定期 scp 備份)。
