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

- Paper Trading adapter、account/asset/order/fill ports（含 `buying_power`）；
- intent/outbox/idempotency、trade update consumer（transport 中立；WS 本體留 P6/P7）；
- REST reconciliation、lots、cash/NAV ledger（以 `account_valuation` + 權威 `account_baselines` + `ReconciliationMarkPriceProvider` seam）；
- limit collar、window cutoff、control plane（application path；shell CLI 留 P6/P7 per ADR-019）；
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

## P3 — TradingAgents 完整研究、提案與記憶整合（8–12 週）

交付：

- **P3-A upstream/license/contracts**：固定上游 commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`，只直接移植需要的 analysis/data/debate/risk/Portfolio Manager/memory 模組；保留 Apache-2.0 license、attribution／NOTICE 並標示修改。定義 `AnalysisInput`、四種 `AnalystReport`、兩種 `DebateState`、`PortfolioSnapshot`、`PortfolioProposal` 與 `RiskRejectionFeedback` versioned schema；
- **P3-B point-in-time input/event verification**：沿用既有資料來源，加入 source manifest、content-addressed store、SEC/IR/public web/market/Tavily adapters、time-aware retrieval、去識別化完整 portfolio snapshot，以及雙來源／三次 fresh sample 的事件驗證器；
- **P3-C analyst/research layer**：Technical/Market、Fundamentals、News、Sentiment、兩輪 Bull/Bear、Research Manager、Trader；
- **P3-D risk/proposal layer**：兩輪 Aggressive/Conservative/Neutral Risk Debate 與 LLM Portfolio Manager；輸出 target-weight `PortfolioProposal`，不得輸出自由文字交易建議或 `OrderIntent`；
- **P3-E provider isolation**：角色可配置模型；Analysts 預設 `agnes-2.5-flash`／備援 `agnes-2.0-flash`，其餘深度角色預設 `muse-spark-1.2-contributor`／備援 `agnes-2.5-flash`；`deepseek-v4-flash` 是通過同一 eval 後才可明確指定的候選，不是第三次 failover；新增 Agnes／OpenCode exact Keychain refs 與 scoped secret mapping，要求最高可用 reasoning、一次 failover、Chat/Responses adapter 與去識別化；
- **P3-F reflection/memory/evals**：依 `docs/MEMORY_CURATION_SKILL_SPEC.md` 實作持倉期間每日反思、Risk rejection memory、週六最多 4,000 行的 memory-curation skill、immutable raw audit、record/replay、semantic-parity/golden/held-out/adversarial eval。

範圍邊界：

- 採用完整 TradingAgents 研究／risk debate／Portfolio Manager 鏈，但 LLM Portfolio Manager 只有提案權，沒有 risk approval、broker 或 order authority。
- Portfolio Manager 每次必須看去識別化的 NAV、cash、buying power、全部 positions、open orders、same-day fills、borrow status 與 remaining limits；缺任一必要 snapshot 即 `INVALID/NO_TRADE`。
- P3 用固定多 symbol／portfolio fixtures 驗證跨檔提案；production universe/quant funnel 與 deterministic Risk approval 留 P4。
- 七人蒸餾與本機 `skill/` 語料不屬 P3，保留為停用的 Future Analyst Plugin；不得因此讀取、審查或發布 corpus。

Tavily account pool 驗收：

- 未提供合規證據時只能啟用一個 key，其他六個必須保持 disabled；
- 提供 Tavily 書面確認或後台授權證據後，才允許切換 `AUTHORIZED_ACCOUNT_POOL`；
- 7 個帳號的 usage/reset/cooldown 分開計算，全域硬上限 7,000；
- 不以跨 key 併發繞過 rate limit；任何 429 遵守 `Retry-After`；
- secrets 不進 DB、log、fixture、Git 或 LLM context。

P3 Definition of Done：

- graph 順序、角色輸入／輸出與兩輪 Bull/Bear／兩輪 Risk Debate 對固定上游版本有可重現的 semantic-parity tests；
- 四份 analyst report、兩種 debate、Research Manager、Trader、Portfolio Manager 都保存獨立狀態與完整 version references；
- 每個 material claim 可回鏈 point-in-time source/data reference，future-dated input 100% 阻擋；
- PortfolioProposal 只接受 `OPEN/INCREASE/REDUCE/CLOSE/HOLD`、signed target weight、confidence、evidence ids 與短 reason codes；低於 0.65 confidence 強制 `HOLD`；
- structured-output/schema/timeout/429/provider/data/portfolio-snapshot failure 全部得到 `INVALID/NO_TRADE`，自由文字 fallback 不可進 P4；
- 相同 frozen inputs 的 record/replay 在不重呼叫模型時可重建同一 `PortfolioProposal`；
- prompt injection、缺來源、互相矛盾、stale data、模型部分失敗與 debate round overflow 有 adversarial tests；
- AnalysisProvider 沒有 broker credential、order/ledger write、shell 或任意 network 能力；
- requested/effective reasoning、一次 provider failover、Muse non-ZDR policy acceptance 與 sanitized input 有 audit evidence；未來 GPT-5.6 必須通過同一 eval gate；
- 每日 open-position reflection 可追到原始 decision/outcome/rejection；週六壓縮結果 ≤ 4,000 行且無 future leakage，immutable raw records 完整不變；
- event verifier 對雙來源分歧、舊 timestamp、單點壞價與延遲新聞產生 `DATA_CONFLICT`，不啟動 LLM 緊急交易；
- source rights/coverage 報告完成，禁止來源、付費內容和未授權第三方全文不進 repo。

實作時以檔案 ownership 分工；任何 agent 報告都需由非 owner 以 source/tests 重現。結果必須人工抽樣，不得只由同一模型自評。

## P4 — 候選篩選與 deterministic Risk approval（4–6 週）

交付：

- point-in-time universe、quant factor/evidence funnel；
- `PortfolioProposal` + quant factors + authoritative account/portfolio state 的 deterministic validation 與 target-to-quantity translation；
- source/data overlap 與 prediction-correlation haircut；
- 一次 `RiskRejectionFeedback(reason_codes, remaining_limits)` 與一次 Portfolio Manager 重申；使用同一研究＋刷新後完整 portfolio snapshot，不重跑 Analysts／debates、不加入 run 外候選；第二次拒絕固定 `NO_TRADE`；
- long gross 100%、short gross 20%、total gross 120%、net 40%–100%、最多 15 檔、單股 absolute 15%、daily turnover 40% 與 shortable/borrow hard gates；
- 同日交易 reason-code gate、開盤後 60 分鐘／收盤前 90 分鐘 target freeze，以及任何時間可用且 turnover-exempt 的 verified `RISK_EXIT`。

驗收：

- 只有 P3 `VALID` 且未過期、portfolio snapshot hash 相符的 `PortfolioProposal` 可進 Risk Engine；`INVALID/ABSTAIN` 不得新增風險；
- target/confidence 無論多高都不能直接生成委託或放寬 hard constraints；
- Risk Engine 永不突破 hard constraints；無 feasible solution 或第二次拒絕為 `NO_TRADE`；
- 同日虧損退出缺少允許的 reason/evidence 時必定拒絕；短線獲利退出、verified Risk Exit 與正常再進場各有 property tests；
- 相同 proposal/quant/holdings/constraints snapshot 可重建相同 targets；
- `TargetPortfolio → RiskDecision → OrderIntent` 只走既有 P2 application boundary。

主責：Terra；Sol 做金融邏輯、prompt/eval、optimizer review；Luna 跑大批 regression。

## P5 — Point-in-time 驗證與經濟回測（3–5 週）

交付：

- as-of backtest、walk-forward partitions；
- economic fill simulator：IEX/Paper limitations、spread/slippage/unfilled/capacity；
- baselines、ablation、regime/sector/style attribution；
- TradingAgents decision replay、per-analyst accuracy/calibration、decision attribution；
- graph/prompt/model/provider/data versioned experiment registry。

驗收：

- time-travel tests 100% 阻擋未來 source/universe constituents；
- 沒有用 test period 選參數；
- 報告含所有失敗 run、換手、drawdown、capacity 和 exposure；
- 多角色 graph 相對單一分析／純 quant baseline 有可解釋增量，否則不得增加複雜度；
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
- 每週：來源缺口、upstream/provider/prompt drift 報告、dependency/license/secret scan。
- 每月：restore drill 提醒、eval regression、Tavily usage review。

### Change control

下列變更必須新增 ADR 和重新跑對應 gate：broker adapter、hard limits、投資宇宙、持有期、TradingAgents graph/role/round、analysis-to-portfolio translation、資料供應商、模型 provider、schedule window、schema breaking change。

## 專案完成定義

只有 P8 完成且文件、runbook、restore、故障注入、source rights、eval 和 60+ Paper 交易日證據齊全，才能稱為「可穩定無人值守的 Paper Trading 系統」。報酬好但帳務不可靠，或帳務可靠但研究不可追溯，都不算完成。
