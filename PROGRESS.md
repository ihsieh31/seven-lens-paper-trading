# Progress

最後更新：2026-08-22

## 目前 Gate

**P3-B Accepted; P3-C Accepted; Combined Gate Closed.**

P3-B與P3-C均已通過最新獨立驗收。下一步由使用者決定是否提交／推送目前未提交工作包；
P3-D仍為Not started，這次關門不授權真實模型、Paper送單或任何live trading。

## Phase 狀態

| Phase | 狀態 | 已交付／下一邊界 |
|---|---|---|
| P0 規格與治理 | Closed | Paper-only、投資流程、資料與安全基線 |
| P1 專案骨架與權威狀態 | Closed | Python/uv、typed config、PostgreSQL、Keychain、telemetry、CI |
| P2 Alpaca Paper 執行安全 | Closed | order/fill/reconciliation/control/NAV/runtime authority；真實下單仍未授權 |
| P3-A upstream/license/contracts | Closed | 固定 upstream、license inventory、immutable strict contracts |
| P3-B evidence/event | Accepted | 最新獨立重新驗收無blocker |
| P3-C analyst/research | Accepted | R6獨立驗收無blocker；InMemory/PostgreSQL duplicate-input authority一致 |
| P3-D risk/proposal | Not started | 三方Risk Debate、Portfolio Manager、`PortfolioProposal` |
| P3-E provider isolation | Not started | 真實模型adapter、Keychain refs、failover/capability audit |
| P3-F memory/evals | Not started | reflection、bounded memory、record/replay、held-out evals |
| P4 deterministic Risk | Not started | hard limits、target-to-quantity、`OrderIntent` boundary |
| P5 validation | Not started | point-in-time walk-forward、attribution、economic fills |
| P6 Shadow | Not started | 至少20交易日，零送單 |
| P7 Supervised Paper | Not started | 至少20交易日；此階段前不得送單 |
| P8 Unattended Paper | Not started | 再至少40交易日 |

## 已關閉證據

### P1

- P1-A/B/C1/C2/C3 均完成獨立驗收。
- public repository exact-SHA CI run `31868962828` 的 `quality-unit`／
  `postgres-integration` 成功。
- 主要能力：strict typed config、Paper endpoint allowlist、canonical JSON／UTC、PostgreSQL
  authority、macOS Keychain exact read、dependency-neutral telemetry、zero-skip CI。

### P2

- 最終 remediation ACC-001～009 已關閉。
- code-bearing commit `488f170`；exact-SHA CI run `32360443947` 兩個 required jobs 成功。
- 主要能力：exclusive new-entry linearization、durable UNKNOWN/conflicting-fill pause、
  reconciliation、cash checkpoint + full-ledger NAV、runtime baseline read-only、migration
  compatibility與typed expected-failure taxonomy。
- Alpaca Paper GET-only evidence 已執行；不包含真實 submit、WebSocket transport本體或control CLI。

### P3-A

- upstream固定 `a33fd4c0f134485a43553a2c23a63cb14adbd88f`；Apache-2.0 inventory完成。
- strict immutable contracts與golden/adversarial/source-invariant tests完成。
- remediation commit `9037dacc589690101ea60901a3f34991480a70e1`；exact-SHA CI run
  `32488368972` 成功。

## P3-B+C 現況

### 交付內容

- P3-B：source／fragment／claim／frozen packet contracts、SHA-256 CAS、injected GET-only
  adapter、去識別化input assembly、price/news event verifier、migration 0010 evidence metadata。
- P3-C：capability-minimal provider port、scripted fake、固定四analyst join、兩輪Bull/Bear、
  Research Manager、Trader、monotonic/idempotent InMemory＋PostgreSQL stage authority。

### Remediation R1

- persisted ANALYSTS/DEBATE套用完整identity/evidence/producer重驗。
- application與PostgreSQL共同強制相鄰transition whitelist與terminal sinks。
- bounded same-hash retries、strict provider hashes、fragment/source availability交叉檢查。

### Remediation R2

- 保留event輸入順序並拒絕倒序；official news要求精確kind/family binding。
- VERIFIED packet要求fresh、complete、contradiction-free；pipeline入口再次驗證。
- canonical URL與source adapter拒絕explicit port。
- CAS publication要求實際hash verifier；runtime不能直接publish。
- runtime-role verifier覆蓋P3 objects/functions與精確least-privilege set。
- DB綁定packet/snapshot；InMemory綁定run/input/packet/snapshot identity。
- provider返回後與每次權威advance前重查deadline。
- 新增runtime drift、CAS denial、snapshot mismatch與不同hash concurrency regressions。

### Remediation R3

- packet hash覆蓋完整source／fragment／claim內容；pipeline重驗nested integrity與hash。
- CAS publication綁定exact `FileContentStore`並核對真實bytes/size，拒絕caller verifier。
- runtime P3 ACL proof覆蓋所有table privilege種類，包括TRUNCATE／REFERENCES／TRIGGER。
- persisted debate重驗frozen evidence closure。
- 過期input在首次`create_run()`之前拒絕，零權威副作用。

### Remediation R4

- point-in-time source eligibility新增`retrieved_at`與`published_at <= as_of`。
- analyst evidence closure同時覆蓋`evidence_refs`與`counterevidence_refs`，fresh/resume共用檢查。
- pipeline在任何run authority前重驗完整`AnalysisInput`與nested portfolio snapshot contracts。

### Remediation R5

- `AnalysisInput`要求自身與portfolio snapshot的`as_of`完全相等。
- pipeline要求input／packet的`data_snapshot_refs`tuple完全相等，拒絕foreign、missing與reordered。
- stale/future snapshot與refs drift均在`create_run()`前拒絕，零權威副作用。

### Remediation R6

- InMemory新增`input_id → run_id`唯一索引，拒絕同一input建立第二個authority run。
- 相同run＋完整相同identity仍冪等；不同run不論packet/snapshot相同或不同都fail closed。
- PostgreSQL `UNIQUE(input_id)`新增相同兩種案例對照測試，兩個repository語意一致。

### R6 獨立驗收

- source review確認InMemory反向唯一索引在寫入前拒絕第二個run；PostgreSQL維持相同authority。
- 獨立PoC覆蓋相同／不同packet-snapshot的duplicate input、零第二run副作用與相同identity冪等。
- P3-C Accepted；P3-B+C Combined Gate Closed。

### 最新驗證

| Gate | 結果 |
|---|---|
| P3-B+C targeted | `48 passed` |
| lock／format／lint／mypy | 全綠；113 source files通過mypy |
| non-integration | `809 passed, 102 deselected` |
| PostgreSQL 16 | `94 passed, 8 deselected, 0 skipped` |
| whitespace | `git diff --check` exit 0 |

以上命令已由獨立acceptance session重跑並通過。工作樹未stage／commit／push；`HEAD`與
`origin/main`仍是`def706440c7dda1a61610a9ea42b42005dfe115a`。

## 尚未開始／不得提前宣告

- P3-D Risk Debate／Portfolio Manager與`PortfolioProposal` runtime。
- P3-E Agnes／OpenCode等真實provider、正式Keychain refs與模型failover。
- P3-F reflection、memory curation、record/replay與模型eval。
- P4 production universe、deterministic Risk approval、quantity與`OrderIntent`。
- P5～P8回測、Shadow、Supervised Paper與Unattended Paper。
- Tavily七帳號pool；沒有外部授權證據時固定`SINGLE_ACCOUNT_UNVERIFIED`。

## Gate 規則

1. 實作完成、綠測試、commit、push或CI成功都不能單獨關閉Gate。
2. PostgreSQL authority主張必須以真實PostgreSQL、runtime role與failure/concurrency injection驗證。
3. 獨立驗收只接受當下source、focused tests、對抗PoC與完整regression證據。
4. 未知或矛盾狀態維持Open；不得以文件敘述取代程式強制。
5. 未經使用者明確授權，不commit、push、merge或擴張至下一phase。
