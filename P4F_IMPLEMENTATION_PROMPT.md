# P4-F Implementation Prompt — Composition／PostgreSQL Authority／Operations／P4 Integration

完整交給P4-F實作模型。本文件只授權P4-F integration；完成後停止，交由fresh P4-F/P4 Final驗收。

## 0A. 弱模型硬性執行協議

1. A～E每個Gate都需fresh Accepted，且acceptance revision/diff lineage涵蓋目前code；缺一立即停止，不得由F補做。
2. F只能組裝已Accepted public contracts與補integration metadata/operations；不得新增factor、limit、reason語意或改wire/hash。
3. 先建立capability map與stage requirement map，再寫composition。任何broker/P2 writer可達即停止。
4. 每個stage先測failure-before-side-effect，再測happy path；不能只寫一個E2E happy test。
5. Network/model都不是本prompt自動授權。無exact授權時只用fixtures/scripted providers，external calls=0。
6. Implementation完成不等於P4 Accepted；不得更新Closed、開始P5或把zero-submit寫成execution-ready。
7. Startup與每個run必須核對ADR-039四個exact manifest ID/hash：`p4-factor-v1`、
   `sec-sic-division-v1`、`p4-correlation-cluster-v1`、`p4-gross-turnover-v1`；缺失或漂移立即fail closed，
   F不得重算、補預設值或接受env override。

## 0B. 可動與禁動範圍

| 類別 | 可動 | 限制 |
|---|---|---|
| Composition | `application/p4_composition.py`、P4 application services | 只注入A-E approved ports |
| Jobs | P4 job/run contracts、scheduler adapters、lease metadata | 重用clock/lease，無submit authority |
| DB/roles | P4 integration run/stage tables、role verifier、next migration | 不合併owner/runtime、不碰P2 worker grants |
| Observability | bounded P4 metrics/audit/operator report | 不含symbol/account/raw body/secret高基數資料 |
| Tests/scripts | `test_p4f_*`、PG integration、offline verify script | scripts預設零network/零model/零broker |
| Docs | operations/security/requirement map/status | 只標pending final acceptance |
| 禁止 | A-E投資語意、P2 order/outbox/broker、P3 live provider、P5-P7 | 不建立bridge、feature flag或dead code |

## 0. 唯一任務

把已Accepted的P4-A～E透過capability-minimal composition、PostgreSQL transaction/roles、jobs、telemetry與restart
流程整合為完整P4 zero-submit pipeline：sources → security/quarantine → market/universe/funnel → P3 proposal →
deterministic Risk → TargetPortfolio → APPROVED_NO_SUBMIT IntentPlan。補齊cross-module failure enforcement與操作證據。

不得新增新的投資語意、放寬limits、呼叫broker submit/cancel、執行真實model，或開始P5/P6/P7。

成功只能回報：`P4-F implementation completed; pending independent P4 Final acceptance`。

## 1. 前置與現況

保存pwd、git status、HEAD/origin、diff/untracked、migration/checksum、已存在composition/jobs/roles。P4-A～E每個都
必須有fresh Accepted report且revision lineage與目前source一致；若後續改動使先前acceptance失效，停止並要求重開
對應Gate，不得由F吞掉。

完整閱讀全部P4 prompts/計畫、A～E source/tests/migrations、P1 jobs/leases/telemetry/secret、P2 reconciliation/control/
execution composition、P3 analysis/proposal/model audit，以及operations/security/CI scripts。

## 2. 禁止事項

未經另行授權：不stage/commit/push；不讀/寫Keychain、註冊key或真實GET；不呼叫model/broker；不注入
PaperBrokerPort、submit/cancel/outbox writer到P4 composition；不把P4 IntentPlan轉P2 OrderIntent；不啟用short／paid
data；不修P5回測、P6 Shadow或P7 submit；不因integration方便合併DB owner/runtime角色。

真實source probes是獨立evidence activity，只有當次使用者明列family/query/request cap/timeout/credential/privacy/
保存與stop scope才可做；禁止自動重試或擴張family。

## 3. Composition root

新增／完成`application/p4_composition.py`及必要services/ports，明確分離：

- source fetch capability（per-family exact GET policy）；
- read/append repositories；security/universe/risk/intent authority；
- clock/calendar；telemetry；scoped secrets；
- P3 proposal port（正常production route仍需另行model授權，tests只scripted）；
- P2 authoritative read-only reconciliation/portfolio snapshot seam。

P4 composition constructor不得接受broker submit/cancel、execution worker、outbox mutation或live endpoint。Startup列印／
audit effective policy/source manifests/schema versions與capability proof，但不印secret/account/portfolio/raw source。
四個ADR-039 manifest需以canonical bytes/hash列入startup、run identity、stage input lineage與operator report；hash只能
從A-E已Accepted artifact讀取。F不得以名稱相同、hash不同的runtime設定繼續。

## 4. End-to-end workflow與transactions

建立closed job/stage machine，至少：

```text
PLANNED -> SOURCES_READY -> SECURITY_READY -> UNIVERSE_READY -> CANDIDATES_READY
-> PROPOSAL_READY -> RISK_COMPLETE -> INTENT_PLANS_PUBLISHED -> COMPLETE
any nonterminal -> NO_TRADE | REVIEW_REQUIRED | FAILED_SAFE
```

每stage綁input/output hashes、as-of/window/deadline、policy/source/security/universe/portfolio versions與audit context。
Resume逐stage讀回bytes/hash並重跑validator；不能因status COMPLETE跳驗證。

Authority順序固定：

1. startup config/schema/ACL/capability proof；
2. latest FULL+CLEAN reconciliation與control/unresolved-intent gate；
3. source records與point-in-time security/quarantine；
4. market/universe/candidate publication；
5. 既有P3 proposal（production model call需另授權；offline integration用scripted）；
6. D Risk/optional one retry；
7. E zero-submit IntentPlans；
8. audit/metrics/report；無任何P2/broker write。

跨stage不能用一個超長transaction包住network/model；每個authority transaction需明確claim/commit/readback。Unknown
outcome不得重送side-effectful operation。P4來源GET是read-only，但production adapter仍無hidden retry。

## 5. Scheduling與single-account concurrency

使用existing jobs/leases/NYSE calendar：monthly universe、daily premarket source/screen、open+60m 12 candidates、close-90m
5 candidates；half-day cutoff相對close計算。Single account/strategy在同一window只允許一個authority run；兩個process/
restart不能發布兩個Risk/Target/IntentPlan heads。

submission guard概念延伸為P4 planning guard，但不得取得submit authority。Risk review前重新讀control/reconciliation/
corporate quarantine；publish plan前再讀versions，任何漂移使本runNO_TRADE/REVIEW_REQUIRED。

## 6. PostgreSQL roles與migration closure

檢查A～E migrations及新增integration metadata所需migration：up/down、checksums、legacy preflight、FK/CHECK/UNIQUE、
schema qualification、fixed search_path、PUBLIC revoke。Runtime role只獲每個service必要SELECT/INSERT/EXECUTE，不能：

- 更新immutable source/security/universe/policy/risk/target/plan；
- 直接APPROVE、解除quarantine/control pause；
- insert/update P2 outbox/order intents/broker orders/fills；
- owner DDL、role grant、trigger disable、CAS publication bypass。

Startup用catalog＋negative probes fail closed驗ACL；owner DSN不進long-running process。

## 7. Observability與operations

增加bounded metrics/traces/audit：stage duration/outcome、source family/error/age、coverage warning、universe/candidate counts、
quarantine counts、Risk reason counts、NO_ACTION/plan counts、restart/replay、deadline與DB conflicts。禁止symbol/account/raw
portfolio/source body/secret進低基數labels或error。

Operator report清楚區分：offline correctness、source transport/coverage、model not-authorized、zero-submit planning與P7
readiness。IEX limited永遠顯示OPEN-038；不能把P4綠寫成Paper execution-ready/profitable。

Failure matrix至少：source outage/schema drift、security conflict、market stale、reconciliation非FULL+CLEAN、control pause、
provider/model未授權/timeout、DB serialization/audit/telemetry failure、clock deadline、process crash/restart。每個case固定
NO_TRADE/REVIEW_REQUIRED且零broker/P2 side effects。

## 8. Optional authorized GET-only source probes

若當次使用者有exact授權，為每個production family建立小型bounded probe plan，先顯示sanitized query、request cap、
timeout、secret refs、保存內容、cost=0、stop-on-first-error、retry=0，再執行。只保存schema/status/timestamp/hash/count/
latency，不保存不必要全文或credential。未授權family保持`LIVE_SOURCE_EVIDENCE_PENDING`，不得偷跑。

Probe失敗不自動retry、不改fixture、不降role；分類transport/schema/rights/auth/coverage並停止該family。

## 9. Integration必測矩陣與故障測試

- happy path 0/1/12/5 candidates，scripted P3 proposal，Risk approve/reject/retry，IntentPlan publication；
- 每stage crash before/after claim/persist/commit/readback，restart byte-identical；
- two processes same window、two Risk reviewers、security version drift、reconciliation/control race；
- source/model/DB/audit/telemetry failures，安全block先durable；
- import/spy/DB/query證明broker submit=0、cancel=0、P2 outbox/order writes=0；
- migration from repository baseline→head→down/up，runtime ACL/provision/startup proof；
- P1～P3 regression與A～E accepted requirement maps全數重放。

## 9A. 固定composition與job演算法

1. **F1 Acceptance lineage preflight**：讀A-E verdict artifacts/exact revisions，將每Gate requirement hash與current source
   mapping保存；任一affected file漂移即停止並標需重驗的Gate。
2. **F2 Capability manifest**：列每個constructor參數、port methods、DB grants、network family與side effect。以denylist test
   assert沒有broker submit/cancel、P2 mutation、owner DSN、live endpoint、unscoped secret或任意URL。
3. **F3 Startup preflight**：依序驗config hash→source manifests→schema/migration checksums→runtime ACL→clock/calendar→
   single account/strategy→control/reconciliation。任一步失敗，run只能FAILED_SAFE且stage/network/model/plan writes=0。
4. **F4 Run claim**：以`strategy+account_ref_hash+trading_date+window+policy_hash`做lease/CAS。account只能保存sanitized
   stable ref hash；兩process只有一winner，loser readback後停止，不另建run。
5. **F5 Stage execution**：每stage驗parent terminal success、input hashes、deadline，再claim；produce→validate→transactional
   append/readback→terminal transition。不得以status或cached output跳過readback validation。
6. **F6 P3 seam**：offline/CI只注入scripted proposal。Production model未授權時在PROPOSAL stage明確
   `NO_TRADE/MODEL_NOT_AUTHORIZED`，不能用fake結果冒充production completed。
7. **F7 Risk/plan**：D review與E publication前各重新讀control/reconciliation/security/market versions；任何漂移整run
   NO_TRADE/REVIEW_REQUIRED，P4 approved plan set=0。
8. **F8 Completion**：只有所有required stage readback valid且P2/broker writes=0才COMPLETE。Telemetry/report failure不得
   抹掉先前durable safety state，也不得讓另一actorpublish。
9. **F9 Resume**：從第一個非validated terminal stage開始；逐parent重新hash。UNKNOWN DB outcome先readback，不重做可能
   產生新authority的append；不同hash conflict固定FAILED_SAFE。
10. **F10 Closure**：完整failure matrix、PG races、resource bounds、operator runbook、A-E regressions與handoff。

## 9B. Closed stage狀態與副作用表

每個stage都需在requirement map列出：allowed predecessor、input/output contract、timeout/deadline、DB transaction、allowed
external calls、allowed writes、failure code、resume rule。最低矩陣：

```text
STARTUP: external=0, writes=sanitized run preflight only
SOURCES: only exact authorized family GET or fixture reader; no model/broker/P2 writes
SECURITY: source/security append only; no unblock bypass
MARKET_UNIVERSE_CANDIDATES: snapshot publication only; no model before candidates complete
PROPOSAL: scripted offline or separately authorized model seam; no Risk/plan write on failure
RISK: decision/Target authority only; no quantity/order
PLAN: P4 APPROVED_NO_SUBMIT only; P2 selected rows=0
REPORT: bounded sanitized metadata; failure cannot change authority
```

所有exception與timeout需closed typed code；不得generic catch後繼續下一stage。

## 9C. Scheduling明確規則

- Monthly universe job：以交易所calendar決定該月第一個核准執行日；同month/policy/source/security version只一head。
- Daily source/screen job：04:30 source snapshot、06:00 universe/quant、06:30 evidence top30是規劃時刻；若環境無scheduler，
  以typed requested window測試，不用sleep或wall-clock polling。
- Open window：NYSE regular open+60m，最多12個new candidates＋全部持倉context；deadline為核准的15分鐘。
- Close window：regular close-90m，最多5個new candidates＋全部持倉context；half-day以當日actual close計算。
- 時區先轉IANA NYSE calendar再轉UTC保存；DST、holiday、half-day與`-1µs/= /+1µs`必測。
- Missed/deadline-expired window不補跑、不滾到下一window、不啟動model/Risk；建立typed NO_TRADE audit。

若04:30/06:00/06:30時刻與部署timezone/market calendar在現行Accepted文件有衝突，停止要求決策，不自行猜UTC/local。

## 9D. Definition of Done

- A-E acceptance lineage與current source閉合，沒有F偷補或改寫上游contract；
- capability manifest、imports、constructors、DB grants與actual P2 worker query皆證明broker/P2 execution不可達；
- 所有stage happy/failure/crash/resume/race有expected rows/calls/terminal state證據；
- same window two-process只有一authority head，safe state在audit/telemetry failure後跨connection可見；
- offline與live/source/model evidence清楚分層，未授權calls=0且不冒充production-ready；
- migrations/ACL/provision/startup負測試、A-F full regressions、PG16/static checks全綠，unexpected skip=0；
- operator runbook能從typed reason定位stage，但不能提供submit命令；
- docs仍明列IEX/P7 blocker、P5未開始與pending independent final acceptance。

## 10. Verification與handoff

最低命令集：

```bash
uv run --locked pytest tests/test_p4f_*.py tests/test_composition.py tests/test_job_service_observability.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

另跑stage crash/resume、two-process lease、runtime ACL、P2 worker selection與side-effect accounting PoCs；預設全程零network。

執行focused integration、完整non-integration、完整real PG16 zero-skip、migration cycle、ACL/concurrency/failure probes、
Ruff format/check、mypy、lock/source invariants、`git diff --check`。若有remote CI需exact SHA且使用者另授權push；本prompt
不授權。

同步全部相關docs、operations/security/ADR/issues/risk/handoff/progress/worklog及P4 requirement map（可放入現有plan，
不要新增超出使用者12個prompt檔限制的prompt）。狀態只能implementation completed; pending independent P4 Final
acceptance。報告exact revision、A～E acceptance lineage、commands/results、source live evidence/pending、blockers後停止。

最終報告固定包含status、exact revision/dirty set、A-E lineage/requirement hashes、changed/forbidden files、capability
manifest、stage matrix、schedule vectors、E2E/failure/crash/race results、P2 worker selection與all side-effect call counts、
PG16/migration/ACL、source/model authorization ledger、完整commands/counts/skips、unverified claims與blockers。唯一下一步
是fresh P4-F/P4 Combined acceptance。
