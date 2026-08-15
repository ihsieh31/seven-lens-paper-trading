# 第一個開發 Prompt：P1-A 安全骨架

> 歷史紀錄：P1-A 已完成並通過獨立驗收。本 Prompt 只保留作 scope／acceptance 依據，不得重跑。

## 使用方式

1. 在 Codex 開啟本 repository root。
2. 主模型選 `gpt-5.6-sol`。
3. 將下方 Prompt 完整貼上，不需要再附七把 Tavily 或 Alpaca 金鑰。
4. 本步只建立安全骨架，不連外、不建立排程、不呼叫 Alpaca/Tavily/OpenAI。

## 可直接貼上的 Prompt

```text
你現在是本專案的 lead architect，請從 P1-A「安全專案骨架」開始實作。

工作目錄固定為：
<repository-root>

這是全新專案，不得匯入或沿用其他交易專案的程式碼與架構。開始前請完整閱讀：
- README.md
- docs/MASTER_PLAN.md
- docs/ARCHITECTURE.md
- docs/DISTILLATION_SPEC.md
- docs/TRADINGAGENTS_ASSESSMENT.md
- docs/OPERATIONS_AND_SAFETY.md
- docs/ROADMAP_AND_ACCEPTANCE.md
- docs/SOURCES.md
- DECISIONS.md
- PROGRESS.md
- ISSUES.md
- RISK_REGISTER.md
- WORKLOG.md

先檢查 AGENTS.md、目前 Git 狀態與既有檔案。這段歷史 Prompt 執行時，本資料夾位於一個可能有其他使用者變更的上層 repository；只能修改 `<repository-root>` 內本任務需要的檔案，不得清理、還原、stage、commit 或 push 任何內容。

本次只完成 P1-A，不要開始研究策略、蒸餾、行情下載、下單、Codex automation 或 launchd。交付範圍：

1. 建立 Python 3.13 的 `src` package 骨架與測試骨架，使用 `uv` 管理依賴；加入 ruff、mypy、pytest 的最小嚴格設定。
2. 建立 typed configuration domain，但只允許 `PAPER` broker environment。程式碼中不得存在 Alpaca live endpoint、live adapter 或可切換為 live 的布林設定。
3. 建立 Paper endpoint allowlist 與啟動驗證：任何未知 endpoint、空值、不符合 allowlist 的 URL 都必須 fail closed。
4. 建立不含 secret 的 `.env.example`、合理 `.gitignore`，以及 secret-redaction utility 的 interface/test；不得要求或讀取真實 API key。
5. 建立 `TavilyComplianceMode`：
   - `SINGLE_ACCOUNT_UNVERIFIED`：只能 enable 一個 account，global monthly hard cap 1,000。
   - `AUTHORIZED_ACCOUNT_POOL`：最多七個 account，每個 hard cap 1,000，global hard cap 7,000；只有提供明確的 authorization evidence reference 才能啟用。
   - 本步只實作 domain/config validation 和單元測試，不實作 Tavily API client、不輸入七把 key。
   - 禁止以多帳號跨 key 併發繞過 rate limit；預留 per-account usage/reset/cooldown schema。
6. 建立核心 ID/time/value-object 骨架：RunId、TradingDate、UtcTimestamp、SchemaVersion；所有時間 DB/domain 儲存語意為 UTC。
7. 建立最小 structured logging 設計，證明 secret-like values 會被遮蔽。
8. 寫 normal、boundary、invalid、fail-closed tests。至少涵蓋：live/unknown endpoint 被拒、未授權模式啟用第二帳號被拒、已授權模式超過七帳號被拒、單帳號或全域 quota 超限被拒、缺少 authorization evidence 被拒、secret redaction。
9. 新增或更新開發 README/commands；實際執行 formatter、lint、typecheck 和 tests，保存結果。
10. 完成後更新 PROGRESS.md、WORKLOG.md；如果發現問題則追加 ISSUES.md，不得把未驗證項目標為完成。

架構原則：
- LLM 永遠沒有 broker credential 或下單權。
- 本專案只做 Alpaca Paper Trading，沒有 live path。
- 所有未知、缺失、schema error 都 fail closed。
- domain 不依賴 Alpaca、Tavily 或 OpenAI SDK；未來由 adapters 實作 ports。
- 不要建立過度複雜的微服務；維持 modular monolith。

模型／多 agent 分工：
- 你（gpt-5.6-sol）負責架構、schema、安全不變量、整合與最後 review。
- 如果環境允許，你可以把「package/tooling 骨架」交給 Terra，把「邊界與失敗測試」交給 Luna；必須明列各自檔案 ownership，告知彼此不是單獨工作、不得覆蓋他人變更。若不能指定模型或安全委派，就由你完成，不要因此阻塞。

執行要求：
- 先提出一份精簡且可驗證的工作計畫，然後直接實作，不要重寫整份企劃書。
- 使用 apply_patch 修改檔案；保留使用者既有變更。
- 若安裝或下載依賴需要額外權限，依 Codex 正常 approval 流程請求，不得繞過。
- 不要索取、顯示或測試任何真實 API key。
- 不要建立交易排程或送出任何網路請求。

最後回報：
- 實際建立／修改的檔案；
- formatter、lint、typecheck、tests 的精確結果；
- Paper-only 與 Tavily compliance invariants 如何被測試；
- 尚未完成、假設與下一個最小步驟。
```

## 完成判斷

這個 Prompt 完成後，應只有「安全可測試的專案骨架」，不應出現任何真實資料收集、模型呼叫或 Paper order。下一個 Prompt 才進入 P1-B：PostgreSQL schema、audit events、job lease 與 market clock abstraction。
