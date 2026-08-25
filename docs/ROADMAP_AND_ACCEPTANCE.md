# 開發路線圖與驗收標準

最後更新：2026-08-24。時程是專注開發估算，不是日曆承諾；任何phase只有在獨立證據滿足後才
能Closed。完整目前狀態見`PROJECT_HANDOFF.md`與`PROGRESS.md`。

## 狀態總覽

| Phase | 狀態 | 下一個依賴 |
|---|---|---|
| P0 | Closed | — |
| P1 | Closed | — |
| P2 | Closed | 真實submit仍需P7 |
| P3-A | Closed | — |
| P3-B+C | Closed | — |
| P3-D | Accepted | 2026-08-24授權單session重審：focused 104、non-integration 893、PG16 141；無High/Medium blocker |
| P3-E | Accepted | authorized live 6/6、PG audit 6 rows、full/PG16通過 |
| P3-F | In progress | reflection／memory／CAS／curator／eval實作中 |
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

## P3 — TradingAgents研究、提案與記憶

### P3-A — upstream/license/contracts（Closed）

- 固定upstream `a33fd4c0f134485a43553a2c23a63cb14adbd88f`與Apache-2.0 inventory。
- strict immutable `AnalysisInput`、reports、debates、snapshot、proposal與feedback contracts。
- remediation commit `9037dacc`／CI `32488368972`成功。

### P3-B — point-in-time evidence/event（Accepted）

交付strict source/evidence contracts、SHA-256 CAS、time eligibility、GET-only source boundary、
去識別化input assembly、price/news event verifier與PostgreSQL evidence authority。

驗收至少證明：future/stale/conflict/contradiction/missing/citation drift fail closed；CAS bytes與DB
publication一致；runtime無publish/direct-DML；亂序與來源冒充被拒；真實PG16 privilege/concurrency
adversarial tests通過。

### P3-C — analyst/research pipeline（Accepted）

交付capability-minimal provider port、scripted fake、四分析員、兩輪Bull/Bear、Research Manager、
Trader與run/stage authority。

驗收至少證明：fresh/resume identity一致、graph/round固定、citation closure、deadline前後重驗、
相鄰transition/terminal sink、bounded retries、packet/snapshot binding與不同hash concurrency。

P3-B與P3-C均已獨立驗收Accepted；Combined Gate Closed。P3-D於2026-08-24依使用者明確允許的單session反覆審核／修復重新驗收為Accepted（最新focused 104、non-integration 893、PG16 141；無High/Medium blocker），工作包待授權發布。

P3-D／E／F各有獨立implementation與acceptance prompt：`P3D_*`、`P3E_*`、`P3F_*`。固定順序為每個Gate
先實作、再由不同fresh session驗收，前Gate Accepted後才進下一Gate。六份prompt是active scope文件，
不是Gate證據。

### P3-D — Risk Debate／Portfolio Manager（Accepted；2026-08-24重審）

- 兩輪Aggressive／Conservative／Neutral Risk Debate。
- LLM Portfolio Manager只輸出strict target-weight `PortfolioProposal`。
- 每次必須讀完整去識別化NAV/cash/buying power/positions/open orders/same-day fills/borrow/
  remaining limits；缺一即`INVALID/NO_TRADE`。
- 無risk approval、quantity、`OrderIntent`、broker或ledger authority。

### P3-E — Provider isolation（Accepted）

- 唯一route固定Agnes `agnes-2.5-flash` Chat Completions exact endpoint；無fallback、retry、model
  discovery或runtime override。
- fixed Keychain ref、sanitized typed envelope、strict output、requested/effective reasoning與append-only
  model-call audit；timeout/429/schema/provider failure全部fail closed。
- 使用者確認rotation後，final六案例6/6與6筆PG audit rows成功；零retry／fallback，完整證據見
  `docs/P3E_LIVE_EVIDENCE_2026-08-24.json`。

### P3-F — Reflection／memory／evals（In progress）

- 每日持倉reflection與Risk rejection lineage；immutable raw audit。
- 每週LLM-visible memory≤4,000行且無future leakage。
- record/replay、semantic parity、golden、held-out、ablation與prompt-injection tests。

P3完成條件：A～F全Closed，完整graph只產生可追溯proposal，任何自由文字fallback、缺來源、
snapshot缺漏或provider不明都無法進P4。

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
