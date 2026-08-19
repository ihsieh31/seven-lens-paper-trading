# 開發路線圖、Agent 分工與驗收標準

時程以下以「專注開發週」估算，不是保證日曆日期。整體預估 24–32 週，再加至少 60 個交易日 Paper 證據；可並行的工作仍受 gate 依賴約束。

## P0 — 規格與治理（1–2 週）

交付：

- 本文件集、ADR、risk/issue/progress/work logs；
- 投資宇宙、持有期、hard limits、資料預算基準；
- schema 草案、source rights policy、Paper-only invariant。

驗收：

- 文件間無 live/Paper、排程、權限、持有期或風控矛盾；
- 所有未驗證參數標示 provisional；
- 使用者核准 project baseline。

主責：Sol。Terra 可做文件一致性檢查；Luna 做 link/schema inventory。

## P1 — 專案骨架與權威狀態（2–3 週）

交付：

- Python 3.13 package、`uv` lock、lint/type/test/CI；
- typed config、Paper endpoint allowlist、Keychain adapter；
- PostgreSQL migrations、domain events、audit log；
- run/job lease、market calendar abstraction；
- structured logs/metrics/traces 基礎。

驗收：

- clean machine 可一鍵建立 dev 環境；
- fake/live endpoint mutation tests 證明 live URL 無法啟動；
- migration up/restore tests；
- duplicate scheduler instances 只有一個持有 lease。

主責：Terra；Sol review schema/security；Luna 建 fixtures/tests。

P1 目前 gate 狀態（2026-08-15）：P1-A、P1-B、P1-C1、P1-C2、P1-C3 均已通過獨立驗收；
公開且獨立的 [`ihsieh31/seven-lens-paper-trading`](https://github.com/ihsieh31/seven-lens-paper-trading)
repository 已建立。GitHub Actions run [`31868962828`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31868962828)
的 `quality-unit` 與 `postgres-integration` jobs 均成功，P1 Core Gate 已關閉。這不授權或代表 P2
broker/order implementation 已開始。

## P2 — Alpaca Paper 執行安全（3–4 週）

交付：

- Paper Trading adapter、account/asset/order/fill ports；
- intent/outbox/idempotency、trade update consumer；
- REST reconciliation、lots、cash/NAV ledger；
- limit collar、window cutoff、control CLI；
- fake broker simulator 與 fault-injection harness。

驗收：

- submit timeout before/after broker accept 都不重複下單；
- duplicate/out-of-order WebSocket events idempotent；
- process 在每個 order state crash 後可安全恢復；
- partial fill/cancel/reject/expire 正確；
- broker mismatch 自動 pause entries；
- 100% Paper account assertion。

主責：Terra；Sol 負責 safety model與 release review；Luna 大量 event-sequence tests。

P2 目前 gate 狀態（2026-08-19）：五個工作包（A~E）與 trade update consumer 已於 Codex
工作副本實作完成並經多輪對抗式審查修復（ADR-017 ~ ADR-021）。補強輪以 ISSUES A–N 清單
重現並修復五個真實缺陷（A pause bypass→CLOSED-017、E broker_orders 雙時鐘/migration
0006→CLOSED-018、F 重複 client_order_id、H fills 分頁、G reconciler 終態對帳）並補
N CI postgres job。P2-E 真實 read-only 驗證已於同日由 operator 授權執行（CLI 僅 GET、
reconciliation CLEAN 持久化）。實作方自測證據：`verify_p1.sh` EXIT=0（non-integration
589 passed, 74 deselected）與真實 PostgreSQL 16 整合 66 passed / 8 deselected
（live 排除，含狀態機全對等價、mismatch 自動暫停、append-only 對抗與 0003–0007
up/down/up）。2026-08-19 獨立驗收另發現並修復 pause recovery 重送、Alpaca 官方分頁
參數、flatten 未收斂取消、非 US-equity asset gate 與 REVIEW_REQUIRED clean-run 五項缺陷；
最終 locked gate 為 627 passed / 74 deselected，真實 PostgreSQL 16 為 66 passed /
8 deselected。2026-08-19 使用者重新打開 P2 gate 後，完成真實 Alpaca Paper GET-only、
獨立非 owner runtime role 持久化與 Luna 三輪對抗驗收；修復 pause TOCTOU、控制部分失敗
稽核、reconciliation 失敗／快照競態、asset/open-order/fill 契約等缺陷。最終 Ruff/mypy
全綠、non-integration 637 passed / 77 deselected、PostgreSQL 16 integration 69 passed /
8 deselected、live acceptance 1 passed；P2 gate **Closed**。真實下單仍留 P7；WS 傳輸本體
與 control shell CLI 依 ADR-019 延後至 P6/P7。

## P3 — 資料與七人蒸餾（6–10 週，可與 P2 部分並行）

交付：

- source manifest、content-addressed store、Tavily budget manager 與條件式 account pool；
- SEC/IR/public web adapters、time-aware retrieval；
- 七套 doctrine v1 candidate、counterexamples、golden/held-out/adversarial eval；
- source rights/coverage report。

Tavily account pool 驗收：

- 未提供合規證據時只能啟用一個 key，其他六個必須保持 disabled；
- 提供 Tavily 書面確認或後台授權證據後，才允許切換 `AUTHORIZED_ACCOUNT_POOL`；
- 7 個帳號的 usage/reset/cooldown 分開計算，全域硬上限 7,000；
- 不以跨 key 併發繞過 rate limit；任何 429 遵守 `Retry-After`；
- secrets 不進 DB、log、fixture、Git 或 LLM context。

每位 doctrine 的 Definition of Done：

- 至少一份核心長文或等價高上下文 primary set；
- 至少 50 個可驗證 source fragments，若確實不足則記錄搜尋證據和縮小 domain；
- 至少 10 個反例／立場反轉／框架失效案例；
- Distillation Spec 的量化 gate 通過；
- 禁止來源、付費內容和未授權第三方全文不進 repo。

主責：Luna 批次 discovery/label；Terra pipeline/evals；Sol 定義 doctrine、抽樣與發布 gate。蒸餾結果必須人工抽樣，不得只由同一模型自評。

## P4 — 篩選、委員會、組合與風控（4–6 週）

交付：

- point-in-time universe、factor/evidence funnel；
- blinded doctrine runner、evidence verifier、targeted rebuttal、chair；
- source overlap/correlation haircut；
- deterministic optimizer 和 hard risk rules；
- no-day-trade lots、開盤／收盤前 target freeze。

驗收：

- 每個 verdict material claim 都能回鏈 evidence；
- 未引用、過時、衝突未解的 case 正確 abstain；
- optimizer 永不突破 hard constraints；無 feasible solution 持現金；
- 同日信號反轉 property tests 不產生 round trip；
- 相同 snapshot/version 可重建相同 targets。

主責：Terra；Sol 做金融邏輯、prompt/eval、optimizer review；Luna 跑大批 regression。

## P5 — Point-in-time 驗證與經濟回測（3–5 週）

交付：

- as-of backtest、walk-forward partitions；
- economic fill simulator：IEX/Paper limitations、spread/slippage/unfilled/capacity；
- baselines、ablation、regime/sector/style attribution；
- model/data/doctrine versioned experiment registry。

驗收：

- time-travel tests 100% 阻擋未來 source/universe constituents；
- 沒有用 test period 選參數；
- 報告含所有失敗 run、換手、drawdown、capacity 和 exposure；
- 七人委員相對簡單 baseline 有可解釋增量，否則不得增加複雜度；
- 結果在更保守成交假設下仍不出現明顯邏輯崩壞。

主責：Terra；Sol 方法論審查；Luna experiment runner/report fixtures。

## P6 — Shadow mode（至少 20 個交易日）

行為：完整跑資料、研究、target、risk、模擬 orders 和 broker comparison，但不 submit。

驗收：

- 交易日 job 完成率 ≥ 98%，missed job 全部正確 fail closed；
- 零 live call、零 duplicate intent、零 critical ledger defect；
- 100% orders 可解釋到 target/risk/evidence；
- Tavily 未授權模式不超過 1,000；已授權 pool 模式不超過全域 7,000 或任一帳號 1,000；
- 每種重大故障至少演練一次；
- 人工逐日 review 記錄所有 false positive/negative。

## P7 — Supervised Alpaca Paper（至少 20 個交易日）

行為：送 Paper orders；使用者觀察但不在正常狀況逐筆修改，以免測不到自主系統。

驗收：

- 100% window 後 reconciliation 完成；
- 零不明 broker order、零 duplicate、零 hard-limit breach；
- quote-to-fill/slippage 與模型差異有報告和校準；
- 告警、pause/cancel/restart/restore 演練成功；
- Paper-only assertion 每次啟動有 audit evidence。

## P8 — Unattended Alpaca Paper（至少再 40 個交易日）

行為：正常日不介入；只對 critical alert 或事先安排的演練處置。

驗收：

- 零需靠人工阻止的危險 order；
- 零 unresolved reconciliation mismatch 跨日；
- job SLO、資料品質和 alert delivery 達標；
- drawdown/turnover/exposure 均依政策；
- 研究引用與 abstention 抽查維持 gate；
- 完成 go/no-go review。此 gate 只代表「穩定 Paper」，不代表可實盤。

## 跨階段工作規則

### 多 Agent ownership

- 每個 agent 任務明列 owned files/modules、輸入、輸出、驗收命令。
- 同一檔案同時只由一位 worker 編輯；其他 agent 用 message 提議。
- Sol 負責 cross-cutting architecture 和高風險 review，不承接所有重複實作。
- Terra 負責主要產品程式；Luna 負責邊界明確的大量資料與測試工作。
- agent 完成不等於 merge；必須通過 owner tests、integration 和 reviewer gate。

### Codex Automations 候選

- 每晚：lint/type/unit/integration（不碰 broker）。
- 每交易日收盤後：讀取已生成的 report，更新 PROGRESS/ISSUES candidate；不得自行關閉 Critical。
- 每週：來源缺口與 doctrine drift 報告、dependency/license/secret scan。
- 每月：restore drill 提醒、eval regression、Tavily usage review。

### Change control

下列變更必須新增 ADR 和重新跑對應 gate：broker adapter、hard limits、投資宇宙、持有期、doctrine weight、資料供應商、模型 provider、schedule window、schema breaking change。

## 專案完成定義

只有 P8 完成且文件、runbook、restore、故障注入、source rights、eval 和 60+ Paper 交易日證據齊全，才能稱為「可穩定無人值守的 Paper Trading 系統」。報酬好但帳務不可靠，或帳務可靠但研究不可追溯，都不算完成。
