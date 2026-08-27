# Progress

最後更新：2026-08-27（P1–P3 full remediation independent acceptance 後）

## 目前 Gate

**P0／P1／P2／P3 全部 Closed；P4～P8 Not started。**

P3 於 2026-08-26 完成最後子閘的獨立重新驗收後一次性關門，並以 commit `b59e466` 發布工作樹、
`d51e9a9` 修復 CI postgres service tmpfs、`660e062` 完成治理同步；exact-SHA run `32962320231`
與 `32963312426` 的 `quality-unit`＋`postgres-integration` 兩 required jobs 均成功。

## 2026-08-27 P1–P3 full remediation 狀態

- Batch A–G：已完成 current worktree 的獨立 source、adversarial、regression 與 real PostgreSQL 驗收，
  狀態為 Accepted。
- Batch E：P3-19/20/22/24/26 已完成 typed correction provenance、selection infra error、repository
  invalidation parity、source/projected hash 與 application memory port 修復。
- Batch F：P3-10 已以 reconciliation-specific `REPEATABLE READ` UoW boundary 修復，並以雙連線
  真實 PG 測試證明同一 run 不混合兩個 local committed snapshots。
- Batch G：移除 dead reconciliation helper／不可達分支與無 consumer 的 shutdown API；保留 sweep
  all-or-nothing 語意；補 P3-21 SQL regression；`market_data/events.py` production wiring 明確 deferred
  至 P4。
- NEW-P2-01：runtime role 對 `control_state` 的直接 UPDATE 權限已撤銷，改由固定
  `SECURITY DEFINER` control functions 提供窄化 authority；real-PG direct-update／unsafe-resume
  對抗 probe 均按預期拒絕。
- 獨立驗收證據：targeted P1–P3 `857 passed`；完整 non-integration `1386 passed, 245 deselected`；
  PostgreSQL 16 `243 passed, 2 deselected, 0 skipped`；Ruff format/check、mypy、`git diff --check`
  全綠。2 個 deselected 為明確 live provider tests，沒有 silent skip，也沒有本次 provider/model call。

## Phase 狀態

| 階段 | 狀態 | 說明 |
|---|---|---|
| P0 規格與治理 | Closed | Paper-only、投資流程、資料與安全基線 |
| P1 專案骨架與權威狀態 | Closed | Python/uv、typed config、PostgreSQL、Keychain、telemetry、CI |
| P2 Alpaca Paper 執行安全 | Closed | order/fill/reconciliation/control/NAV/runtime authority；真實下單仍未授權 |
| **P3 研究／提案／記憶** | **Closed** | upstream contracts、evidence/event、研究管線、Risk Debate／提案、provider isolation、reflection lineage、bounded memory 與 eval 治理；A～F 子閘及 cleanup Batch A～G 均已獨立驗收 |
| P4 deterministic Risk | Not started | hard limits、target-to-quantity、`OrderIntent` boundary |
| P5 validation | Not started | point-in-time walk-forward、attribution、economic fills |
| P6 Shadow | Not started | 至少20交易日，零送單 |
| P7 Supervised Paper | Not started | 至少20交易日；此階段前不得送單 |
| P8 Unattended Paper | Not started | 再至少40交易日 |

## 已關閉證據摘要

- **P1**：P1-A/B/C1/C2/C3 獨立驗收完成；commit 發布＋exact-SHA CI 成功。能力：strict typed
  config、Paper endpoint allowlist、canonical JSON／UTC、PostgreSQL authority、macOS Keychain
  exact read、dependency-neutral telemetry、zero-skip CI。
- **P2**：ACC-001～009 remediation 關閉；code-bearing commit `488f170`／CI `32360443947`。
  能力：exclusive new-entry linearization、durable UNKNOWN/conflicting-fill pause、reconciliation、
  cash checkpoint＋full-ledger NAV、runtime baseline read-only、migration compatibility。Alpaca
  GET-only evidence 已執行；不含真實 submit。
- **P3**：六個子閘各自經 fresh-session 獨立驗收 Accepted（含多輪 rejected→remediation→re-acceptance，
  逐輪細節見 `WORKLOG.md`）。最終關門證據基線：
  - Targeted `423 passed`；non-integration `1299 passed, 232 deselected`；真實 PG16 整合
    `217 passed, 15 deselected, 0 skipped`；Ruff／format／mypy／`git diff --check` 全綠。
  - Offline eval：V12 frozen report byte-match，split/report hash 不變。
  - Authorized live evidence V12：260/260 strict 且全正確、violations=0、130/130 pre-network
    fail-closed；Provider Transport first-attempt/eventual 皆 100%（該批 snapshot）。
  - 發布鏈：`b59e466`（工作樹）→ `d51e9a9`（CI tmpfs 512m→1g 修復）→ `660e062`（治理同步）；
    exact-SHA run `32962320231`／`32963312426` 兩 jobs 成功。

## 尚未完成／不得提前宣告

- Provider Transport rolling reliability evidence：V12 批次 snapshot 為 GREEN，但 P6 前仍需另行
  授權的 synthetic canary 在 rolling 7 日且 ≥200 logical calls 重驗，跌破即重開。
- P4 production universe、deterministic Risk approval、quantity 與 `OrderIntent` boundary。
- P5～P8 回測、Shadow、Supervised Paper 與 Unattended Paper。
- Tavily 七帳號 pool；沒有外部授權證據時固定 `SINGLE_ACCOUNT_UNVERIFIED`。

## 文件與歸檔

- 現行文件：`PROJECT_HANDOFF.md`、本檔、`docs/ROADMAP_AND_ACCEPTANCE.md` 與治理 ledgers。
- P3 各子閘的 implementation／acceptance prompt、requirement map 與關門當下 handoff 快照已歸檔於
  [`docs/archive/`](docs/archive/)（`prompts/`、`p3/`、`handoffs/`）；歸檔文件僅作歷史紀錄。
- 後續每個 gate 由使用者授權後建立新的 work-package prompt，由未參與實作的 fresh session 驗收；
  prompt 存在不代表實作開始或 gate 通過。

## Gate 規則

1. 實作完成、綠測試、commit、push或CI成功都不能單獨關閉Gate。
2. PostgreSQL authority主張必須以真實PostgreSQL、runtime role與failure/concurrency injection驗證。
3. 獨立驗收只接受當下source、focused tests、對抗PoC與完整regression證據。
4. 未知或矛盾狀態維持Open；不得以文件敘述取代程式強制。
5. 未經使用者明確授權，不commit、push、merge或擴張至下一phase。
