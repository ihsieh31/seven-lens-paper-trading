# 開發路線圖與驗收標準

最後更新：2026-08-28。時程是專注開發估算，不是日曆承諾；任何phase只有在獨立證據滿足後才
能Closed。完整目前狀態見`PROJECT_HANDOFF.md`與`PROGRESS.md`。

## 狀態總覽

| Phase | 狀態 | 下一個依賴 |
|---|---|---|
| P0 | Closed | — |
| P1 | Closed | — |
| P2 | Closed | 真實submit仍需P7 |
| P3 | Closed | A～F子閘及cleanup Batch A～G 已於2026-08-27完成獨立驗收；P3-4／P3-28 deferred、P3-21 false positive |
| P4 | In progress | P4-A／P4-B已fresh independent acceptance並Accepted／Closed；P4-C～F未開始 |
| P5～P8 | Not started | 依序通過前一Gate |

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

2026-08-27 P1–P3 full remediation independent acceptance：`verify_p1.sh` 為
`1386 passed, 245 deselected`；targeted P1–P3 為 `857 passed`；real PostgreSQL 16 為
`243 passed, 2 deselected, 0 skipped`。新增 runtime authority 修復經獨立 probe 驗證：direct
`control_state` UPDATE 拒絕（`42501`），未達 FULL+CLEAN 的 direct resume 拒絕（`55000`）。

完成條件「完整 graph 只產生可追溯 proposal，任何自由文字 fallback、缺來源、snapshot 缺漏或
provider 不明都無法進 P4」已滿足。子閘 prompt 與 requirement map 歸檔於 `docs/archive/`；
Transport rolling canary 義務見 OPEN-027。

## P4 — 多來源、候選與deterministic Risk（In progress；A／B Closed）

使用者已核准`P4_PROGRAM_PLAN.md`／ADR-038／ADR-039：單一Paper帳戶、long-only、保守hard limits、整股quantity、
零付費資料profile，以及exact Factor V1／SEC SIC Division／correlation cluster／gross turnover manifests。P4已分成A～F
六個依序Gate，每個Gate各有implementation／acceptance prompt。P4-A（含ADR-039 SEC SIC／Company Facts第0C節
delta）與P4-B已於2026-08-28經fresh independent acceptance，兩者verdict均為`Accepted`、Gate均為`Closed`；
P4-C～F仍未開始。P4-A／P4-B的closure不代表完整P4 Closed。

P4-B implementation scope已完成：point-in-time security identity resolver、append-only source／corporate-action／
quarantine contracts、source correction／withdrawal lineage，以及in-memory與PostgreSQL authority。公開入口為
`SecurityMasterService`，服務順序固定為validate → identity resolve → durable block → confirmation → CAS transition →
readback → bounded telemetry。範圍只到identity、forward/reverse split與三層quarantine；沒有開始P4-C，沒有Risk／
  portfolio／quantity／broker／model authority；OPEN-037的exit／P5～P7條件仍未關閉。

P4-A／P4-B acceptance evidence：P4-A focused＋invariants `372 passed`；P4-B focused＋invariants `132 passed`；
fresh PostgreSQL 16 integration `256 passed, 2 deselected, 0 skipped`，同輪non-integration `1878 passed,
256 deselected`。修復後公開入口證明blocked head維持`entry_blocked`，direct `ELIGIBLE`與未知payload均以
SQLSTATE `23514`拒絕，owner-safe eligible／extra-payload rows均為`0`；source transport／SEC／FRED adversarial
PoC均按預期通過。結論：P4-A與P4-B均`Accepted`／`Closed`，且`no actionable findings`。

- exact-host GET-only adapters 與 source-role registry：Alpaca；FRED/ALFRED；Treasury/BLS/BEA/EIA；SEC/IR；
  Alpaca Corporate Actions＋Nasdaq/NYSE；Tavily/GDELT；yfinance supplement。任何 key／真實下載需另行授權。
- point-in-time security master、CIK/CUSIP/symbol lineage、macro vintage、rights/rate-limit/schema-drift gate。
- point-in-time production universe與quant funnel。
- `market_data/events.py` 的 fail-closed event verifier 目前只有已實作契約；production composition
  明確由本 Gate 擁有，P4 前不得提前接入 P2 execution path。
- confirmed forward/reverse split 在候選、Risk與submit前三層 quarantine；候選不建立analysis run。
- 已持有 long 的 `CORPORATE_ACTION_EXIT` 只產生 no-submit、可重播 intent：跳過LLM，但不跳過來源確認、
  cancel/resolve、FULL reconciliation、tradability、regular-hours、price collar與idempotency。它不是
  `flatten_paper` 的隱性擴權；short BUY-to-cover維持未授權。
- hard limits固定long/total gross 90%、cash buffer 10%、單股5%、sector 25%、cluster 30%、turnover 20%、
  ADV 0.1%、daily-loss 1% stop與drawdown 8% freeze；`short_enabled=false`。
- 整股向零取整、minimum adjustment `max(USD100,NAV*0.25%)`、0.5% rebalance band、5秒quote、30bps
  spread與25bps初始collar的target-to-quantity translation。
- 第一次拒絕只允許一次Portfolio Manager重申；第二次固定`NO_TRADE`。
- 只有P4可產生核准targets並進既有P2 `OrderIntent` boundary。

完成條件包括：每個source family的role/rights/host/secret/schema/point-in-time/failure gate、來源衝突與silent
fallback對抗PoC、拆／合股false-positive/late-detection/identity-drift/withdrawn-announcement tests，以及
`CORPORATE_ACTION_EXIT` zero-broker-call證據。文件規劃或fake adapter不等於P4 Accepted。

## P5 — Point-in-time驗證與經濟回測（Not started）

- walk-forward、decision replay、baseline/ablation、attribution、ALFRED vintage／event-sourced security-master
  replay與economic fill model。
- future leakage、survivorship、revision、cost/latency與Paper-vs-model差異有明確evidence。
- 拆／合股回放證明：未公開事件不可見；確認後候選被排除、既有long產生exit intent；Paper operational exit
  不被歸因成thesis failure。

## P6 — Shadow（至少20交易日；Not started）

只產生決策與意圖，不送單。要求零嚴重帳務錯誤、完整reconciliation、告警與restart evidence。至少演練
confirmed/unconfirmed/withdrawn/late forward及reverse split、partial-fill投影、entry unblock與P&L/memory
lineage；short事件必須安全pause而非產生未授權BUY-to-cover。

## P7 — Supervised Paper（至少20交易日；Not started）

只允許Alpaca Paper；逐日人工檢視，完成submit/WS/control CLI與事故演練。第一次允許
`CORPORATE_ACTION_EXIT` 真實Paper submit前，須取得exact使用者授權並以獨立驗收證明來源確認、submit-time
recheck、cancel/resolve、FULL reconciliation、價格保護、idempotency、通知、realized P&L與記憶均閉合。
這不是live gate。

## P8 — Unattended Paper（至少再40交易日；Not started）

驗證uptime、reconciliation、風控、資料／provider失敗與研究品質。任何結果都不會自動授權實盤。

## 跨階段Gate規則

1. implementation、green tests、commit、push與CI都不能單獨關閉Gate。
2. authority／concurrency／migration主張必須以真實PostgreSQL與failure injection驗證。
3. source、tests、對抗PoC與exact code revision必須一致；文件不得代替程式強制。
4. 下一phase不得提前取得credential、network、broker、order、ledger或risk authority。
5. 任何scope、risk limit、external provider或不可逆資料語意變更先更新ADR並取得使用者授權。
