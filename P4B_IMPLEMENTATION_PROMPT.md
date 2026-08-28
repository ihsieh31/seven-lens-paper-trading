# P4-B Implementation Prompt — Point-in-time Security Master／Corporate-action Quarantine

把本文件完整交給P4-B實作模型。本文件只授權P4-B；完成後停止，不得開始P4-C。

## 0A. 弱模型硬性執行協議

「必須／不得／固定／只有」皆為Gate要求。先讀完全檔，建立requirement ledger，再修改：

1. 每一需求要有`source enforcement + unit/adversarial test + PG evidence（適用時）`；缺一即未完成。
2. 一次只實作一個contract或transition；先確認紅測試因缺少該行為失敗，再做最小修正。
3. 不得以symbol當identity、以current record回答historical query、以來源票數消解conflict，或以cache bool當authority。
4. 不得猜migration編號／table／role／現有port。先讀當下source與catalog；找不到就停止回報。
5. 不得因A的contract不方便而修改A。若A缺陷阻擋B，標`BLOCKED: reopen P4-A`。
6. 完成B後必須停止；implementation模型沒有權限自評Accepted。

## 0B. 明確可動與禁動範圍

| 類別 | 可動內容 | 邊界 |
|---|---|---|
| 主要新增 | `securities/contracts.py`、`identity.py`、`corporate_actions.py` | 只做identity、split與quarantine |
| Ports/adapters | `application/ports/securities.py`、`infrastructure/postgres_securities.py` | 不得包含broker/order/model方法 |
| DB | 當下next migration up/down與PG tests | append-only、point-in-time、ACL所需最小schema |
| Tests | `test_p4b_*`、必要source invariant | 不刪、不skip、不改舊expectation以迎合新code |
| 條件式 | A source record reader、P2 read-only reconciliation type | 只讀既有public contract，不改authority語意 |
| 禁止 | universe/screening/risk/portfolio/quantity/intent、P2 execution、P3 model | 不得建立placeholder或未使用骨架 |

若預定檔名與現有repo衝突，選擇最小現有extension point並在報告說明；不得平行建立第二套identity authority。

## 0. 任務與唯一完成語句

以已Accepted的P4-A typed source records建立event-sourced point-in-time security master，以及forward/reverse split
偵測、正式確認、撤回／衝突與三層entry quarantine的authoritative state。P4-B止於security/corporate-action
authority；不建立universe排名、RiskDecision、TargetPortfolio、quantity、IntentPlan，不呼叫broker或model。

唯一成功語句：

```text
P4-B implementation completed; pending independent acceptance
```

不得宣告Accepted／Closed或開始P4-C。

## 1. 前置與必讀

先保存pwd、git status、HEAD/origin、diff stat/name/check、untracked與migration清單。確認P4-A有fresh independent
Accepted證據；若A Rejected／pending，停止。完整閱讀治理文件、`P4_PROGRAM_PLAN.md`、ADR-036/037/038、
`P4B_ACCEPTANCE_PROMPT.md`、P4-A contracts/adapters/repositories，以及現有source/evidence、market event、P2
reconciliation/control/order state、PostgreSQL role/migration source與tests。

先映射現有`SourceRecord`、Alpaca asset identity、Symbol、MarketEvent、Postgres repositories，避免重複或以ticker
當永久identity。

## 2. 禁止事項

未經另行授權：不stage/commit/push；不讀credential或真實API；不修改P4-A已Accepted wire/hash語意；不改P2
broker submit/cancel/flatten authority；不建立可執行OrderIntent；不支援short BUY-to-cover；不把其他corporate
actions偷納入forward/reverse split；不以人工字典／今日symbol修補歷史；不修改已套用migration。

保留dirty changes，不reset/checkout。未知、late、conflict、withdrawn、identity/quantity drift一律fail closed。

## 3. Owned modules與契約

優先新增／最小修改：

```text
src/seven_lens/securities/contracts.py
src/seven_lens/securities/identity.py
src/seven_lens/securities/corporate_actions.py
src/seven_lens/application/ports/securities.py
src/seven_lens/infrastructure/postgres_securities.py
tests/test_p4b_*.py
tests/integration/test_p4b_*_postgres.py
```

實際名稱需服從現有風格。不得為整理目錄搬動P1～P3已驗收code。

### 3.1 Security identity

`SecurityIdentityRecord`至少包含stable `security_id`、symbol、exchange、asset class、CIK、CUSIP/ISIN（可用時）、
valid/available interval、source refs、producer/schema version、status與identity hash。固定要求：

- symbol不是identity；同symbol不同security與同security換symbol都可表示；
- `valid_from/valid_to`描述真實有效期，`available_at`描述系統何時可知；兩者不能互換；
- interval不可重疊或倒退；future record在historical cutoff不可見；
- identity closure policy明確指出各事件所需ID；缺ID不能猜補；
- source撤回／correction以append-only supersession表示，不UPDATE歷史row。

### 3.2 Corporate action

只接受`FORWARD_SPLIT|REVERSE_SPLIT`。Record至少包含event/security identity、ratio、declaration/effective/ex date、
available time、source refs、confirmation state與hash。Ratio使用exact positive Decimal/rational representation，禁止
float、0、negative、NaN、ambiguous numerator/denominator。

State machine：

```text
DETECTED -> ENTRY_BLOCKED -> CONFIRMED
DETECTED/ENTRY_BLOCKED/CONFIRMED -> REVIEW_REQUIRED
CONFIRMED -> EFFECTIVE_PENDING_RECONCILIATION
```

P4-B不得標EXITED；那需要P4-E/P7 evidence。

## 4. Confirmation policy

事件一經任一合格discovery/confirmation source發現，立即對該stable security `ENTRY_BLOCKED`。自動`CONFIRMED`
必須同時：

1. exact security identity closure；
2. event type、ratio、effective/ex-date完整；
3. 至少一個SEC、issuer IR或listing exchange正式公告；
4. 所有已讀source `available_at <= decision_at`；
5. source彼此不矛盾、未撤回；
6. 原始content hash／provider ID／URL identity可稽核。

只有Alpaca/Tavily/GDELT/yfinance時可block，不能confirm。Alpaca「查無事件」不是反證。Ratio/date/type/identity
任何衝突、正式公告撤回、晚於cutoff或unsupported action固定`REVIEW_REQUIRED`；不得投票或依來源數量自動解決。

## 5. 三層quarantine API

提供同一authoritative query給後續三個位置：

- candidate creation前；
- P4 Risk approval前；
- future submit-time recheck前。

輸入必須包含security_id、symbol-as-of、decision time與security-master version；輸出closed decision：

```text
ELIGIBLE | ENTRY_BLOCKED | REVIEW_REQUIRED
```

不能只回bool；需reason、event IDs、source lineage、as-of/version hash。未知security、current symbol mismatch、
stale master、future source、multiple active identities都不是ELIGIBLE。三個caller不得各自重寫判定邏輯。

## 6. PostgreSQL authority與併發

建立append-only identities、symbol lineage、corporate actions、source links、state transitions／current projection所需
schema；具體migration編號依當下最高版本分配。要求：

- up/down＋legacy preflight；不改既有migration；
- exclusion/UNIQUE/FK/CHECK保護identity interval、single current event head、ratio/date/state transitions；
- runtime只可經固定repository/function append合法transition；不可UPDATE/DELETE immutable rows或直接解除block；
- 同一provider event same-hash bounded idempotent，different hash需correction/supersession；
- concurrent confirm/conflict/withdrawal只允許一個canonical結果；任何serialization/unique loser需typed重讀或
  fail closed，不能last-write-wins；
- transaction在非必要audit失敗時仍必須durably保留ENTRY_BLOCKED/REVIEW_REQUIRED安全狀態。

## 7. Application service與故障順序

固定流程：validate P4-A record → resolve as-of identity → append discovery/block → evaluate official confirmation →
append transition → commit safety state → best-effort telemetry/audit。任何identity/source/repository uncertainty不得
解除既有block。Restart/resume從DB重建並重驗source hashes，不信cached bool。

P4-B不讀broker positions、不取消orders、不建立exit plan；只提供後續所需的confirmed event與quarantine authority。

## 8. 必測矩陣

- identity：ticker reuse、symbol change、CIK/CUSIP缺漏/衝突、overlapping interval、future availability、as-of ±1µs；
- ratio：forward/reverse、fraction、極端值、zero/negative/float/rounding、ratio/date correction；
- sources：Alpaca-only block、official confirm、two official conflict、withdrawal、late discovery、duplicate/reordered；
- state：非法跳轉、post-construction tamper、unsupported event、confirm後撤回；
- quarantine三caller相同決策，stale version/symbol drift/unknown identity全fail closed；
- PG：up/down/up、legacy bad rows、same/different hash、rollback、crash、兩連線confirm-vs-withdraw、ACL；
- 安全阻擋在audit/telemetry失敗時仍durable；
- 無model、broker、OrderIntent、Risk/quantity import/call的source invariant。

## 8A. 詳細實作步驟

前一步未完成不得進下一步：

1. **B1 Inventory**：畫出P4-A `SourceRecord`→identity/corporate-action入口、現有Symbol/Asset/MarketEvent與PG roles；
   列出可重用與不能重用的原因。
2. **B2 Identity contracts**：建立closed enums、exact identifiers、valid/available intervals、canonical wire/hash、resource
   bounds。先做pure validators及time-travel tests，不連DB。
3. **B3 Identity resolver**：輸入`source refs + as_of + known_at cutoff`，輸出唯一resolved identity或typed
   `UNKNOWN/AMBIGUOUS/CONFLICT/STALE`；不得回`None`或任選第一筆。測symbol reuse/change與late correction。
4. **B4 Corporate-action records/state machine**：先parse exact ratio/date/type，再實作合法transition table。每次transition
   都驗前一head、source hash、decision time與identity version；非法transition不得只在service層阻擋。
5. **B5 Confirmation evaluator**：實作「任一發現立即block；一個正式authority且所有已讀來源無衝突才confirm」。把每項
   prerequisite變成closed reason code，不得用自由文字決定流程。
6. **B6 Unified quarantine query**：只建一個authoritative evaluator和三個用途標記；輸出完整lineage。三個caller seam
   不得複製if/else或以default ELIGIBLE處理timeout。
7. **B7 PostgreSQL**：先legacy preflight，再tables/constraints/functions/grants。測two connections、confirm-vs-withdraw、
   crash/audit failure與runtime負面權限；safe block需先durable。
8. **B8 Closure**：做import/source invariant、A regression與完整驗證，更新文件為pending acceptance。

每個service的failure ordering必須明列：validate input→resolve identity→durably block→evaluate confirmation→CAS append
transition→readback validate→bounded telemetry。從第3步起任一failure都不能把狀態變回ELIGIBLE。

## 8B. Closed reason與輸出要求

至少建立並測試下列typed reasons（名稱可服從現有enum，但語意不可合併成`INVALID`）：

```text
UNKNOWN_SECURITY
AMBIGUOUS_IDENTITY
SYMBOL_AS_OF_MISMATCH
IDENTITY_INTERVAL_CONFLICT
SOURCE_NOT_YET_AVAILABLE
STALE_SECURITY_MASTER
SPLIT_DETECTED
FORMAL_CONFIRMATION_MISSING
SPLIT_RATIO_CONFLICT
SPLIT_DATE_CONFLICT
SPLIT_IDENTITY_CONFLICT
SOURCE_WITHDRAWN_OR_CORRECTED
UNSUPPORTED_CORPORATE_ACTION
EFFECTIVE_OR_LATE_EVENT_REVIEW
```

每個decision需保存`security_id, symbol_as_of, master_version/hash, decision_at, state, reasons, event_ids, source refs/hash`。
自由文字可作operator detail，但不能控制state或取代reason enum。

## 8C. Definition of Done

- symbol reuse/change及late correction能以point-in-time查詢重播，沒有current-data leakage；
- split discovery、formal confirmation、conflict、withdrawal與late/effective所有transition封閉；
- candidate/Risk/future-submit三個用途對相同inputs輸出canonical-identical decision；
- audit/telemetry failure後ENTRY_BLOCKED/REVIEW_REQUIRED仍durable，第二connection可見；
- runtime不能UPDATE/DELETE、直接confirm、解除block或偽造projection；
- B沒有任何universe、Risk、Target、quantity、Intent、broker/model capability；
- migration up/down/up、legacy-invalid preflight、完整tests與static checks全綠且無unexpected skip；
- report內requirement ledger沒有TODO/UNKNOWN。

任一項缺失只能`partial/blocked`，不得使用成功語句。

## 9. 驗證與交付

最低命令集：

```bash
uv run --locked pytest tests/test_p4b_*.py tests/test_paper_only_source_invariants.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

PG命令之外仍需另列two-connection confirm/withdraw與runtime ACL PoC；full suite綠不等於那些PoC已完成。

跑focused、nearby P3 source/event、完整non-integration、真實PG16 zero-skip、migration cycle、runtime ACL、Ruff、
format、mypy、`git diff --check`。更新governance但只寫implementation completed/pending acceptance。報告exact
revision、檔案、commands/results、PoC與blockers；不得關OPEN-037或開始P4-C。

最終報告固定包含：

```text
P4-B IMPLEMENTATION STATUS: completed pending independent acceptance | partial | blocked
REVISION / DIRTY SET:
P4-A ACCEPTANCE LINEAGE:
SCOPE FILES / USER FILES PRESERVED:
REQUIREMENT LEDGER:
IDENTITY TIME-TRAVEL VECTORS:
CORPORATE-ACTION TRANSITION MATRIX:
QUARANTINE THREE-SEAM PROOF:
MIGRATION / PG16 / ACL / CONCURRENCY:
COMMANDS AND EXACT COUNTS:
EXTERNAL/MODEL/BROKER CALLS: 0
BLOCKERS:
NEXT ACTION: fresh P4-B independent acceptance
```
