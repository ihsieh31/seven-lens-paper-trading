# 開發路線圖與驗收標準

最後更新：2026-08-24。時程是專注開發估算，不是日曆承諾；任何phase只有在獨立證據滿足後才
能Closed。完整目前狀態見`PROJECT_HANDOFF.md`與`PROGRESS.md`。

## 狀態總覽

| Phase | 狀態 | 下一個依賴 |
|---|---|---|
| P0 | Closed | — |
| P1 | Closed | — |
| P2 | Closed | 真實submit仍需P7 |
| P3 | Closed | A～F子閘全部驗收後於2026-08-26合併關門；發布`b59e466`→`d51e9a9`→`660e062`，CI `32962320231`／`32963312426` 兩required jobs成功 |
| P4～P8 | Not started | 依序通過前一Gate |

## P0 — 規格與治理（Closed）

交付Paper-only、投資流程、來源／rights、hard limits、ADR／issue／risk／handoff治理基線。

## P1 — 專案骨架與權威狀態（Closed）

交付Python 3.13／uv、typed config、canonical values、PostgreSQL migrations與leases、Keychain、
telemetry、locked CI與zero-skip PostgreSQL。exact-SHA CI `31868962828`成功。

## P2 — Alpaca Paper執行安全（Closed）

交付Paper adapter、order/fill authority、idempotency、reconciliation、NAV/ledger、control path與
runtime role。final remediation commit `488f170`／CI `32360443947`成功。

P2 Closed只代表程式安全基線；真實submit、WebSocket transport與operator CLI仍受P6/P7 gate約束。

## P3 — TradingAgents研究、提案與記憶（Closed）

**P3 已於 2026-08-26 一次性關門。** 六個子閘各自經未參與實作的 fresh session 獨立驗收 Accepted
（含多輪 rejected→remediation→re-acceptance 輪次，細節見 `WORKLOG.md`）：

- **P3-A** upstream 固定 `a33fd4c0…`、Apache-2.0 inventory、strict immutable contracts。
- **P3-B** point-in-time source/evidence contracts、SHA-256 CAS、event verifier、PostgreSQL
  evidence authority；future/stale/conflict/CAS/runtime-privilege 對抗全數 fail closed。
- **P3-C** capability-minimal provider port、四分析員、兩輪 Bull/Bear、Research Manager、Trader
  與 run/stage authority；identity/graph/citation/deadline/transition 全部驗收。
- **P3-D** 兩輪三觀點 Risk Debate 與 strict target-weight `PortfolioProposal`；完整去識別化
  snapshot 缺一即 `INVALID/NO_TRADE`；無 risk approval／quantity／`OrderIntent` authority。
- **P3-E** 唯一 Agnes route 固定 endpoint、sanitized envelope、append-only model-call audit；
  authorized live 六案例 6/6（證據 `docs/P3E_LIVE_EVIDENCE_2026-08-24.json`）。
- **P3-F** immutable reflection lineage、bounded curated memory、synthetic eval 治理；
  Offline Correctness 100%、Live Model Quality 260/260＋violations=0＋130/130 pre-network
  fail-closed、Provider Transport first-attempt/eventual 皆 100%（V12 批次 snapshot）。

關門證據基線：targeted `423 passed`、non-integration `1299 passed, 232 deselected`、真實 PG16 整合
`217 passed, 15 deselected, 0 skipped`、offline byte-match 不變。發布鏈：`b59e466`（工作樹）→
`d51e9a9`（CI tmpfs 512m→1g）→ `660e062`（治理同步）；exact-SHA run `32962320231`／`32963312426`
兩 required jobs 成功。

完成條件「完整 graph 只產生可追溯 proposal，任何自由文字 fallback、缺來源、snapshot 缺漏或
provider 不明都無法進 P4」已滿足。子閘 prompt 與 requirement map 歸檔於 `docs/archive/`；
Transport rolling canary 義務見 OPEN-027。

## P4 — 候選與deterministic Risk（Not started）

- point-in-time production universe與quant funnel。
- hard limits、source/model overlap haircut、target-to-quantity translation。
- 第一次拒絕只允許一次Portfolio Manager重申；第二次固定`NO_TRADE`。
- 只有P4可產生核准targets並進既有P2 `OrderIntent` boundary。

## P5 — Point-in-time驗證與經濟回測（Not started）

- walk-forward、decision replay、baseline/ablation、attribution、economic fill model。
- future leakage、survivorship、revision、cost/latency與Paper-vs-model差異有明確evidence。

## P6 — Shadow（至少20交易日；Not started）

只產生決策與意圖，不送單。要求零嚴重帳務錯誤、完整reconciliation、告警與restart evidence。

## P7 — Supervised Paper（至少20交易日；Not started）

只允許Alpaca Paper；逐日人工檢視，完成submit/WS/control CLI與事故演練。這不是live gate。

## P8 — Unattended Paper（至少再40交易日；Not started）

驗證uptime、reconciliation、風控、資料／provider失敗與研究品質。任何結果都不會自動授權實盤。

## 跨階段Gate規則

1. implementation、green tests、commit、push與CI都不能單獨關閉Gate。
2. authority／concurrency／migration主張必須以真實PostgreSQL與failure injection驗證。
3. source、tests、對抗PoC與exact code revision必須一致；文件不得代替程式強制。
4. 下一phase不得提前取得credential、network、broker、order、ledger或risk authority。
5. 任何scope、risk limit、external provider或不可逆資料語意變更先更新ADR並取得使用者授權。
