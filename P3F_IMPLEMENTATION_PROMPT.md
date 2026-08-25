# P3-F Implementation Prompt — Immutable Reflection／Bounded Memory／Evaluation

把本文件**完整、不節錄**交給負責P3-F的實作模型。本文件只授權P3-F；完成後停止，不得開始P4或自行關閉P3。

---

## 0. 唯一任務與安全檢查點

你是Seven-Lens Paper Trading專案的P3-F實作模型。你的唯一任務是建立：

1. append-only daily reflection lineage；
2. 每週最多4,000行、可驗證且point-in-time安全的LLM-visible `MemoryArtifact`；
3. record/replay與held-out eval framework；
4. 獨立最小權限memory-curator PostgreSQL authority。

P3-F不建立P4 Risk Engine、不做quantity/order、不修改risk limits、不宣稱walk-forward profitability或投資績效。
memory永遠不是原始authority，只是從immutable typed facts推導、可丟棄的bounded輔助context。

任何會呼叫real provider的held-out/latency eval，需要本批次新的使用者明確授權；P3-E的授權不延伸。沒有授權
時可完成offline/scripted infrastructure，但狀態只能`P3-F evidence pending`，不能寫implementation fully completed。

完整offline + authorized real-provider eval後，唯一允許的完成語句是：

```text
P3-F implementation completed; pending independent acceptance
```

不得自行寫P3-F Accepted或P3 Combined Closed。

## 1. 前置條件與起始檢查

必須證明P3-E已由另一個fresh session獨立Accepted，且其exact revision包含authorized live provider/privacy
evidence。P3-D也仍Accepted；P3-F是Not started/in progress/authorized remediation；P4仍Not started。

確認`0010`、P3-D `0011`、P3-E `0012`不可修改，`0013`未被占用。若前置Gate重開、live evidence缺失、migration
衝突或不明dirty code重疊，停止列exact blocker。

```bash
cd /Users/zongen/Downloads/codex/trading
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git diff --stat
git diff --name-status
git diff --check
rg --files src/seven_lens tests migrations | sort
```

保留全部dirty/untracked files；不得reset、checkout或覆蓋他人變更。

## 2. 必讀資料

完整閱讀handoff、progress、README、roadmap、master plan、architecture、SECURITY、DECISIONS、ISSUES、
RISK_REGISTER、TradingAgents assessment、`docs/MEMORY_CURATION_SKILL_SPEC.md`、`P3F_ACCEPTANCE_PROMPT.md`，以及
P3-B/C/D/E contracts、pipelines、provider audit、CAS/FileContentStore、PostgreSQL role/migration/test patterns。

把`MEMORY_CURATION_SKILL_SPEC.md`逐條映射為planned source/test/evidence checklist，不能只閱讀摘要。

## 3. 禁止事項

未經另行明確授權，不得：

- stage/commit/push/PR/merge/tag或改remote；
- 讀Keychain、`.env`、credential、shell history或呼叫real provider；
- 讀/發布repository root ignored `skill/` corpus；
- 修改raw P3-B/C/D/E authority、migration `0010/0011/0012`或P2/P4 code；
- 讓memory curator/provider取得broker、order、risk approval、filesystem任意path、shell或owner DDL capability；
- 保存chain-of-thought、secret、raw prompt/response、private portfolio、第三方未授權全文；
- 用model self-score/self-reported line count/hash/importance直接升級artifact；
- 用future outcome修補過去reflection、覆寫raw record或讓新artifact出現在舊as-of replay；
- 調低eval threshold、查看held-out答案後調prompt、刪case/skip或用summary冒充重算結果。

## 4. 核心不變量

- 原始reflection/source observations append-only；correction是新record + typed supersedes link，不是UPDATE。
- 每個numeric/date/symbol/reason code都必須指向可驗證typed fact ID；model不能創造新事實。
- `available_at`與`cutoff`同時控制point-in-time；requested as-of只能看當時已可用資料。
- memory candidate只有通過deterministic validation才能VALIDATED/CURRENT；model judge只是輔助訊號。
- current promotion atomic、單一current、可重播；失敗保留前一個對requested cutoff仍安全的artifact，否則注入none。
- 每個contract exact/frozen/bounded/canonical/hash-integrity；fresh/resume/replay共用validator。
- application/domain無psycopg/provider SDK/Keychain/broker/network/filesystem任意path capability。

## 5. 固定工作包順序

依F0→F8實作。每包先寫failing targeted tests，完成最小實作與至少一個adversarial regression後再前進。

### F0 — Requirement map與ADR

- 將memory skill spec每條requirement映射到source owner、permanent test與驗收證據。
- 在`DECISIONS.md`記錄append-only reflection、correction semantics、cutoff/available_at、independent curator、CAS-backed
  bounded artifact、promotion/fallback、record/replay、held-out split及threshold governance。
- ADR不能宣稱Gate已通過；implementation session不關Issue/Risk。

### F1 — Reflection source contracts

在`src/seven_lens/memory/`建立或等價分層：

- `ReflectionSourceRef`
- `DailyReflectionRecord`
- `ForecastObservation`
- `OutcomeObservation`
- `RiskRejectionObservation`
- typed correction/supersedes link（若spec需要）

每筆record至少綁：record/schema id、created/available/as-of/cutoff、proposal/decision/research bundle/snapshot hashes、
source fact ids、prompt/model/provider/data/memory versions、domain-separated content hash。open position每日也產生record，
不能只等realized outcome。

model只能從input typed facts選擇：observation、reusable lesson、applies/invalid conditions。任何數字/日期/symbol/
reason code有exact fact ref；無fact ref即INVALID。不要persist chain-of-thought或自由文字原始response。

### F2 — Append-only reflection pipeline

固定流程：load exact approved sources→point-in-time filter→validate source hashes/authority→build bounded provider envelope
或scripted input→strict parse→fact-token closure→persist append-only record→authoritative audit。

在create/persist前重驗cutoff/deadline/identity。source later corrected時新增correction observation，原row/content hash不變。
resume對persisted source/record重新驗完整lineage，不能因row存在就信任。

### F3 — `MemoryArtifact` contract與deterministic selection

artifact至少包含：artifact/schema/created/cutoff、source record IDs、previous artifact ID、entries、deterministic
line count/content hash、prompt/model/provider versions、state CANDIDATE/VALIDATED/CURRENT/INVALID。

entry至少包含category、importance、observation、lesson、applies_when、invalid_when、evidence fact IDs、risk codes。

hard bounds固定：

- line count <= 4,000；entries <= 512；canonical artifact <= 512 KiB；
- 每欄UTF-8 bytes、每entry evidence count、total fact refs、nesting/node均有限；
- lineage/evidence IDs不得截斷；一個超長essential lineage不能靠silent truncation變合法。

selector先以deterministic category quota、dedup key、importance、recency與canonical tie-break排序，再在bounds內選擇。
不要信model自報importance、line count或hash；model importance只能是bounded input feature，需deterministic policy重算。

### F4 — Validation與prompt-injection隔離

validation順序固定：schema/exact type→resource bounds→source authority/lineage→cutoff/available_at/future leakage→
prompt injection flags→numeric/date/symbol/risk fact-token closure→evidence closure→canonical bytes/hash。

任何階段fail，candidate可記INVALID但不能promote。model judge只能寫auxiliary score，不可單獨使VALIDATED。

package-owned curation template固定id/hash/version，loader不接受caller path、symlink、root `skill/`或workspace file。
source/model內「忽略規則、下單、讀secret、呼叫tool、把我設CURRENT」都只是untrusted data。

### F5 — CAS bytes與promotion semantics

artifact bytes可用既有exact `FileContentStore`。promotion repository必須自行讀回staged bytes、重算SHA-256與size，
不接受caller boolean verifier或只信metadata。

state流程建議：

```text
CANDIDATE -> VALIDATED -> CURRENT
CANDIDATE/VALIDATED -> INVALID
```

CURRENT不是靠改舊artifact state破壞歷史；使用current pointer或有效區間保留promotion history。atomic promotion只允許
一個current。candidate失敗不改current；previous artifact只有`cutoff <= requested as_of`且仍通過integrity時可fallback，
否則注入none並產生bounded alert。

crash points：bytes write前後、candidate register前後、validation前後、promotion commit前後。每點重跑都same-hash
bounded idempotent、different hash拒絕、無orphan pointer或foreign bytes。

### F6 — Migration `0013`與memory-curator role

新增`0013_p3f_reflection_memory_{up,down}.sql`，至少管理：append-only reflections/source/correction links、artifact
metadata/state/previous/cutoff、artifact-source lineage、atomic current pointer及bounded model/eval audit metadata。

DB獨立強制hash/bytes/count/state/cutoff/FK/uniqueness；raw reflection UPDATE/DELETE永遠拒絕，runtime/curator無
TRUNCATE/REFERENCES/TRIGGER或disable/replace trigger能力。

建立獨立memory-curator capability：只能讀approved reflection views、register candidate、validate/promote memory；
不能publish source、mutate proposal、read provider secrets、order/ledger/control/broker或owner DDL。functions fixed search
path/schema-qualified；PUBLIC無rights；startup verifier逐object/function/owner及每種table privilege fail closed。

兩連線concurrent promotion只有一個winner；loser不得留下第二current、矛盾pointer或orphan lineage。

### F7 — Eval corpus、runner與anti-contamination

在`src/seven_lens/evals/`建立typed case manifest、split manifest、runner、metric calculators與report hash。committed fixture
只能synthetic/redistributable；authorized live sanitized captures進local CAS，不提交第三方raw evidence/private portfolio。

eval families至少：contract mutation、graph/round parity、citation/entailment/stale/future、provider/fallback/reasoning/
deadline、prompt injection/capability escape、portfolio de-identification/proposal safety、memory lineage/bounds、role ablation/
source overlap/false consensus。

最低corpus：

- static safety/adversarial >=120；semantic parity traces >=20；memory cases >=60；
- 每configured role/stage route >=20 valid held-out + >=10 invalid/ambiguous held-out；
- normal/emergency皆覆蓋；case IDs、fixture hashes、split hash固定。

golden/training/dev/held-out manifests物理/邏輯分離。prompt/template調整流程不能讀held-out expected outputs；runner在
final evaluation才解封。任何case/sample/threshold更動先ADR、變更split version並重跑全部，不能覆寫舊report。

固定門檻：safety accepted violation=0；accepted schema/integrity/citation/lineage=100%；scripted record/replay hash=100%；
graph trace/round=100%；real-provider valid primary>=98%、最多一次fallback後>=99%；invalid/ambiguous fail-closed/ABSTAIN
recall=100%；normal/emergency在15m/3m，報p50/p95/max/timeout count及分母。

P3-F只建立forecast calibration plumbing，不設定P5 profitability/economic-fill門檻。

### F8 — Offline tests、real-eval checkpoint與handoff

永久adversarial tests至少：

- raw row before/after hash不變；UPDATE/DELETE/TRUNCATE/trigger/owner drift拒絕；
- future outcome、equal timestamp、timezone boundary、missing/foreign/reordered lineage；
- 4,001 lines、513 entries、512KiB+1、超長單欄、duplicate flood/importance spoof；
- malicious instruction、fake fact ID、invented number/date/symbol/risk code；
- candidate failure不改current；previous cutoff太新不fallback；no-safe-memory注入none；
- bytes/hash/size mismatch、symlink/path escape、forged boolean verifier；
- concurrent promotion/crash/retry/collision；historical replay只見當時artifact；
- repeated Risk rejection/miscalibration/same-day loss/borrow anomaly/regime lesson golden retention；
- source invariant證明無broker/order/risk approval/secret capability。

任何real provider eval前，列route/case count/synthetic payload hashes/request upper bound/預估cost/quota/privacy/timeout/
stop conditions，等待使用者本批次明確授權。若無授權，handoff寫`P3-F offline implementation completed; real eval
evidence pending`並停止。

## 6. 必跑驗證

```bash
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q <all P3-F targeted plus affected P3-B/C/D/E tests>
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/run_postgres_integration.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run <the committed offline/authorized eval command>
git diff --check
git status --short --branch
git diff --stat
git diff --name-status
```

用實際selectors/runner取代placeholder。記錄corpus counts、split/report hashes、每metric分子分母與threshold、latency
分佈、targeted/full/PG16 pass/deselect/skip；PG skip=0。real eval另記authorization與exact request count/cost。

## 7. 文件與停止規則

- offline-only：`P3-F offline implementation completed; real eval evidence pending`。
- offline + authorized real evidence：`P3-F implementation completed; pending independent acceptance`。
- P3 Combined保持Open；P4～P8保持Not started。
- 不因自測關ISSUE/RISK/Gate，不刪active prompts。

## 8. 最終回覆格式

1. `P3-F RESULT: offline checkpoint | implementation completed | partial | blocked`
2. exact HEAD與dirty/untracked files
3. requirement map與改動檔案
4. reflection/memory/CAS/curator/eval invariants
5. corpus counts、split/report hashes、metrics與threshold結果
6. targeted/full/PG16精確結果
7. real-provider authorization/request/cost evidence；若無明確寫未呼叫
8. 未完成與風險
9. `GATE STATE: pending independent acceptance | evidence pending | Open`
10. 單一步驟：完整時把`P3F_ACCEPTANCE_PROMPT.md`交給fresh模型；evidence pending時等待使用者授權

完成後停止。不得開始P4或自行做P3 Combined closure。
