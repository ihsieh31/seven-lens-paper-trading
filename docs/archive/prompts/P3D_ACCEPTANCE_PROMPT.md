# P3-D Independent Acceptance Prompt

把本文件**完整、不節錄**交給一個沒有參與P3-D實作的新模型。本文件只驗收P3-D，不修程式、不開始P3-E。

---

## 0. 角色、唯一任務與可用判定

你是Seven-Lens Paper Trading專案的P3-D獨立驗收模型。你必須從source、migration、tests、自己的對抗PoC與
真實PostgreSQL 16重新建立證據，不得相信implementation report、文件自述、commit message、CI綠燈或測試名稱。

只允許三種判定：

- `Accepted`：所有必要source/permanent test/adversarial/real-PG evidence完整，無High/Medium blocker。
- `Rejected`：存在可重現High或Medium blocker。
- `Not Accepted — prerequisite/evidence pending`：必要前置或平台證據缺失，尚不能判定安全。

不允許「conditional pass」。若發現blocker，不要順手修復；完成足以支持判定的最小重現後停止。

## 1. 驗收邊界

先保持read-only：

- 不修改source、tests、migration或prompt；不format整repo、不刪測試、不新增skip；
- 不stage/commit/push/PR/merge/tag，不reset/checkout/覆蓋dirty changes；
- 不讀`.env`、Keychain、credential或ignored root `skill/`；
- 不呼叫真實model、broker或其他外部API；
- 不使用SQLite/mock支持PostgreSQL authority主張；
- 不使用全repository security scanner，除非使用者另行要求。

做出判定後，只有在權限清楚時才以最小diff更新handoff/progress/worklog；不得同session修code再接受。

## 2. 前置條件與exact revision

完整閱讀`PROJECT_HANDOFF.md`、`PROGRESS.md`、`README.md`、`P3D_IMPLEMENTATION_PROMPT.md`、roadmap、master plan、
architecture、security、decisions、issues、risk register，以及所有P3-B/C/D source/tests/migrations。

先保存：

```bash
cd /Users/zongen/Downloads/codex/trading
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -5 --oneline --decorate
git diff --stat
git diff --name-status
git diff --check
```

逐一列出untracked files。驗收revision必須寫成`HEAD + exact dirty/untracked file set`或精確commit，不能只引用
HEAD掩蓋未提交實作。

前置Gate P3-B/C若沒有fresh Accepted證據，判`Not Accepted — prerequisite gate open`。P3-D文件必須是
`implementation completed; pending independent acceptance`；若仍Not started，不能驗收虛構能力。

## 3. 證據規則與finding格式

每項關鍵主張至少對應：

1. source enforcement；
2. 永久regression test且assert零/正確DB副作用；
3. 驗收者自行建立的adversarial PoC；
4. PostgreSQL主張使用真實PG16兩連線/ACL/rollback evidence；
5. full locked regression。

High：可產生foreign/stale/future proposal、deadline後authority、DB privilege bypass、重複current proposal、
P4/broker capability。Medium：破壞boundedness、replay、audit、retry/state一致性、migration rollback或持續驗證。
High/Medium都阻擋Accepted。

每個finding必須包含：ID、severity、`file:line`、被破壞invariant、最小重現、expected、observed、authority
影響、缺少的永久測試及required remediation。不要只寫抽象建議。

## 4. Scope與capability審查

證明P3-D只包含ResearchBundle、ProposalContext、兩輪三觀點Risk Debate、PortfolioProposal、fake provider及
InMemory/PostgreSQL proposal authority。

用imports、composition roots、constructors與source invariants確認：

- application/domain無network、provider SDK、Keychain/PyObjC、psycopg或broker SDK；
- fake provider無filesystem/shell/secret/DB/broker capability；
- 無P4 gross/net/turnover/borrow approval、quantity、TargetPortfolio、OrderIntent；
- 無P2 execution/reconciliation/control/ledger語意修改；
- migration `0010`內容/checksum未變，root `skill/`未讀取/發布。

名稱不存在不等於安全；檢查實際物件可收到的ports/capabilities。

## 5. Contract與mutation驗收

逐欄讀`ResearchBundleItem`、`ResearchBundle`、`ProposalContext`、`RiskRejectionFeedback`、`RiskArgument`、
`RiskDebateState`與evolved `PortfolioProposal`。確認exact/frozen/bounded/canonical/hash/integrity規則。

從合法object開始，在不只修改top-level hash的前提下逐欄竄改，呼叫實際pipeline/repository入口：

- child run/input/symbol/packet/snapshot/TraderPlan id/hash/evidence/producer/status；
- bundle parent/as-of/window/deadline/focus order/items/citation union/universe/hash；
- context attempt/previous/superseded/feedback/snapshot/timeline/allowed symbols/versions；
- risk viewpoint/round/context/bundle/evidence/argument/producer；
- proposal request symbol/action/side/weight/confidence/status/evidence/expiration/lineage。

證明在任何新authority前拒絕，且error固定不回顯payload。wire parser需拒絕unknown/missing/duplicate key、
subclass、bool-as-int、NaN/Infinity、negative zero與non-canonical decimals。

## 6. Multi-symbol coordinator與bundle驗收

source與PoC都必須證明：

- parent input + symbol以不同domain tags deterministic衍生child run/input IDs；有golden vectors且無拼接歧義；
- 每個child的`focus_symbols`恰好是該symbol，其餘parent packet/snapshot/universe/data/as-of/window/deadline保留；
- items與parent focus symbols一對一，canonical order不受執行或DB讀取順序影響；
- 每個child P3-C COMPLETE、TraderPlan VALID且persisted identity/evidence/producer/hash重新驗證；
- 0/1/max、similar symbol、duplicate、missing、extra、reordered、foreign與tampered child均fail closed；
- partial child成功可以resume，但所有child完成前沒有bundle/risk/proposal authority；
- resume不能因payload已存在就跳過validation，也不能混入另一parent/run的child。

特別排除用單一fixture或把完整focus set重複當每個symbol結果的假聚合。

## 7. Risk Debate、proposal與retry驗收

initial exact call trace必須為：

```text
AGGRESSIVE r1 -> CONSERVATIVE r1 -> NEUTRAL r1 ->
AGGRESSIVE r2 -> CONSERVATIVE r2 -> NEUTRAL r2 ->
PORTFOLIO_MANAGER once
```

六個argument必須完整、順序固定、每組恰好一次，citation屬frozen bundle set。任一missing/extra/foreign/late
argument時不得呼叫PM或persist partial debate authority。

proposal驗證至少挑戰：foreign symbol/citation、duplicate request、>27 requests、invalid action/side/weight、
abs(weight)>0.15、negative zero、low-confidence非HOLD、CLOSE非FLAT/zero、emergency exposure increase、
INVALID/ABSTAIN帶requests、expiration超deadline及version/context drift。

retry PoC必須證明：

- typed feedback指向同一COMPLETE attempt 1；refreshed snapshot時序/remaining limits一致；
- research bundle與六個risk arguments不重跑，只多一次PM_RETRY；
- attempt 2精確supersede attempt 1，不加symbol/evidence/research；
- foreign feedback、retrograde/future timestamp、第二個attempt2、第三次proposal、different hash retry均零authority；
- same-hash retry只在明確budget內冪等，fresh與resume語意相同。

## 8. Deadline與state machine驗收

在create run前、每provider前、provider後、每persist前注入fake clock。逐點測deadline前1微秒、等於deadline、
後1微秒；確認Python、contract、SQL邊界一致。late但內容合法的result也不得成為下一stage authority。

state只允許：

```text
PLANNED -> RISK_DEBATE -> PROPOSAL -> COMPLETE
non-terminal -> INVALID | EXPIRED
```

測skip、regress、self-transition、foreign run/result、terminal resurrection、same-hash超budget與different-hash retry。
InMemory與PostgreSQL必須同義。

## 9. Migration、ACL與真實PG16驗收

讀`0011` up/down、loader、repository、provisioner與runtime verifier，確認DB自身強制FK/unique/check/bytes/attempt/
state/lineage，不依賴Python善意。

檢查所有privilege種類：SELECT、INSERT、UPDATE、DELETE、TRUNCATE、REFERENCES、TRIGGER、EXECUTE、ownership、
schema CREATE、database TEMP與function replace。PUBLIC無rights；runtime只允許exact tables SELECT與approved
functions EXECUTE。functions fixed search path且所有objects schema-qualified。

用兩個真實PG16 connections競爭：

- same context/same hash；same context/different hash；
- duplicate bundle/child identity；
- two attempt-2 proposals；transition versus terminal；
- owner drift、function owner drift、PUBLIC EXECUTE、extra function及逐table privilege drift。

每個case記錄winner/loser、SQLSTATE或typed error、row counts、orphan查詢與rollback結果。驗up/down/up、version row、
checksum及`0010`不變；skip必須0。

## 10. 必跑命令

先找出實際test selectors，不能把placeholder原樣執行：

```bash
rg --files tests | sort
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q <all P3-D targeted tests plus affected P3-B/C regressions>
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/run_postgres_integration.sh
git diff --check
git status --short --branch
git diff --stat
git diff --name-status
```

保存targeted/full non-integration/PG16 pass、deselect、skip、Ruff、format、mypy與lock結果。任一required命令未跑、
失敗或PG skip非0，都不得Accepted。

## 11. Governance與最終判定

檢查`DECISIONS.md`與code一致；`ISSUES.md`/`RISK_REGISTER.md`不得因implementation自測擅自關閉。R-29跨P3/P4，
P3-D不能把P4 hard-risk部分Closed。active prompts保留。會誤導authority/Gate的文件錯誤是Medium blocker。

Accepted時只把P3-D寫Accepted，P3-E仍Not started。Rejected/Not Accepted時記錄exact blockers與下一步，不修code。

## 12. 最終回覆格式

```text
TARGET_GATE: P3-D
DECISION: Accepted | Rejected | Not Accepted — ...
REVISION: <exact HEAD + dirty/untracked files or exact commit>
```

接著依序列：

1. Findings（High→Medium→Low；沒有就寫`No blocking findings`）
2. Source boundaries reproduced
3. Permanent tests reproduced
4. Independent adversarial PoCs（input/expected/observed）
5. Real PostgreSQL 16/ACL/concurrency evidence
6. Full regression精確數字
7. Evidence not reproduced及原因
8. Scope exclusions
9. Gate state與單一步驟：Accepted才可把`P3E_IMPLEMENTATION_PROMPT.md`交給新實作模型；否則只做精確remediation

完成判定後停止。不要開始P3-E，也不要在同一session先修再重新接受。
