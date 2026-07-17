# 研究知識庫(M4.6)

**一個想法一頁**:假設 → 做法 → 結果 → 結論(採用 / 失敗原因)。
失敗紀錄比成功紀錄有價值——沒寫下來的死路,每一季都會有人再走一次。

## 用法

```powershell
quant note new "我的想法" --strategy momentum --symbols SPY --experiments 12,13
quant note list                     # 全部,最新在前
quant note list --status rejected   # 只看失敗的(最值錢的一疊)
```

## 慣例

- 檔名 `YYYY-MM-DD-slug.md`(由 `quant note new` 自動產生);本 README 不算筆記。
- frontmatter 的 `status`:`idea`(還沒驗)→ `testing`(驗證中)→ `adopted` / `rejected`(有結論)。
  有結論後手動更新 `status:` 與 `updated:` 即可。
- `experiments:` 填實驗記錄系統的 id(`quant experiments` 查),讓每個主張都指向可復現的證據。
- 寫給三個月後的自己與 AI 助手看:結論要含「為什麼」,不是只有數字。
