# Project Handoff

最後更新：2026-08-26（P3 收尾後）
專案：`/Users/zongen/Downloads/codex/trading`

## 1. 目前唯一狀態

**P0／P1／P2／P3 全部 Closed；P4～P8 Not started。**
`main`＝`origin/main`，工作樹乾淨。P3 以 commit `b59e466` 發布、CI tmpfs 修復 `d51e9a9`、治理同步
`660e062`；exact-SHA CI run `32962320231` 與 `32963312426` 的 `quality-unit`＋`postgres-integration`
兩 required jobs 均成功。

## 2. P3 Close 摘要

P3（TradingAgents 研究、提案與記憶）於 2026-08-26 一次性關閉。六個子閘 upstream/license contracts、
point-in-time evidence/event、研究管線、Risk Debate／Portfolio Manager、provider isolation、
reflection/memory/evals 各自經獨立驗收 Accepted 後合併關門；過程中的 rejected→remediation→re-acceptance
輪次細節見 `WORKLOG.md` 與 `docs/archive/handoffs/PROJECT_HANDOFF_2026-08-26_P3-CLOSED.md`。

關門時的最終證據基線：

- Targeted `423 passed`；non-integration `1299 passed, 232 deselected`；真實 PG16 整合
  `217 passed, 15 deselected, 0 skipped`；Ruff／format／mypy／`git diff --check` 全綠。
- Offline eval：V12 frozen report byte-match，split/report hash 不變。
- Authorized live evidence V12：260/260 strict 且全正確、violations=0、130/130 pre-network fail-closed；
  Provider Transport first-attempt/eventual 皆 100%（該批 snapshot）。

## 3. 邊界與存續義務

- Paper-only；未知／缺失／過期／矛盾狀態一律 fail closed；無 live endpoint、無自動升級實盤路徑。
- 只有 P4 deterministic Risk 能核准 target 並產生 `OrderIntent`；任何送單能力屬 P7 之後；
  walk-forward/profitability 主張屬 P5。
- Provider Transport 的 rolling canary 義務（OPEN-027）：P6 Shadow 前需另行授權 synthetic canary，
  於 rolling 7 日且 ≥200 logical calls 達 first-attempt≥95%／eventual≤3 attempts≥99%，跌破即重開。
- OPEN-002/003/004/005/006/007/025/027 等未結 issue 的關閉條件不變；詳見 `ISSUES.md`。
- CI 注意事項：postgres-integration service container 的 tmpfs 已固定為 1g（512m 會被整合套件的
  WAL churn 耗盡導致容器崩潰）；本機跑整合套件時勿與其他容器並行（1.4GB Docker VM 會 OOM）。

## 4. 下一個單一步驟

**P4 deterministic Risk（Not started）**：production universe、hard limits、source/model overlap
haircut、target-to-quantity translation、第一次拒絕一次重申／第二次 `NO_TRADE`、核准 targets 進既有
P2 `OrderIntent` boundary。開始前需使用者對 scope 明確授權，並由 fresh session 依新工作包 prompt 執行。

## 5. 文件地圖

- 現行治理：`PROGRESS.md`（gate 狀態）、`docs/ROADMAP_AND_ACCEPTANCE.md`（剩餘階段與完成條件）、
  `DECISIONS.md`、`ISSUES.md`、`RISK_REGISTER.md`、`WORKLOG.md`（逐輪歷史）。
- 設計基線：`docs/MASTER_PLAN.md`、`docs/ARCHITECTURE.md`、`SECURITY.md`、
  `docs/OPERATIONS_AND_SAFETY.md`、`docs/SOURCES.md`。
- 未來／停用：`docs/MEMORY_CURATION_SKILL_SPEC.md`、`docs/TRADINGAGENTS_ASSESSMENT.md`、
  `docs/DISTILLATION_SPEC.md`。
- 歷史歸檔：[`docs/archive/`](docs/archive/) — `prompts/`（已用完的 P3-D/E/F implementation／acceptance
  prompts）、`handoffs/`（P3 關門當下的前一版 handoff 快照）、`p3/`（P3F requirement map）。
  歸檔文件僅作歷史紀錄，不是現行授權或完成證據。
