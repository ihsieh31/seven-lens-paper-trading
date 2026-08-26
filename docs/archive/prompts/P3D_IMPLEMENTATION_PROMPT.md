# P3-D Implementation Prompt — Research Bundle／Risk Debate／Portfolio Proposal

把本文件**完整、不節錄**交給負責P3-D的實作模型。本文件只授權P3-D；完成後必須停止，不能開始P3-E。

---

## 0. 唯一任務與成功狀態

你是Seven-Lens Paper Trading專案的P3-D實作模型。你的唯一任務是把已驗收的per-symbol P3-C結果聚合為
不可混用的multi-symbol `ResearchBundle`，完成固定兩輪、三觀點的Risk Debate，再產生strict
`PortfolioProposal`與獨立InMemory／PostgreSQL proposal authority。

本Gate全程使用`ScriptedProposalProvider`，不得連線真實模型。輸出止於proposal；不建立P4 risk approval、
quantity、`TargetPortfolio`、`OrderIntent`或broker side effect。

允許的最終狀態只有：

```text
P3-D implementation completed; pending independent acceptance
```

或清楚的`partial/blocked`。你不是驗收者，不得寫P3-D Accepted／Closed，也不得開始P3-E。

## 1. 開始前必須成立的前置條件

規劃基線為：

- repository：`/Users/zongen/Downloads/codex/trading`
- baseline commit：`55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`
- P3-B Accepted、P3-C Accepted、P3-B+C Combined Gate Closed
- P3-D Not started；P3-E／F Not started

這只是規劃快照。你必須以當下source與文件重新確認：

1. `PROJECT_HANDOFF.md`與`PROGRESS.md`仍把P3-D列為Not started、implementation in progress或明確授權修復。
2. P3-B與P3-C有獨立Accepted證據；若任一Gate重新Open，停止並回報prerequisite blocker。
3. `migrations/0010_p3bc_evidence_analysis_*`存在且不可修改；`0011`尚未被其他功能占用。
4. 沒有不明業務程式dirty changes與本Gate重疊。若有，列出檔案與diff衝突後停止，不猜測所有權。

先執行並保存原始輸出：

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

untracked files也屬工作樹狀態；`git diff`看不到它們，必須逐一辨識並保留。

## 2. 必讀文件與source

依序完整閱讀：

1. `PROJECT_HANDOFF.md`
2. `PROGRESS.md`
3. `README.md`
4. `docs/ROADMAP_AND_ACCEPTANCE.md`
5. `docs/MASTER_PLAN.md`
6. `docs/ARCHITECTURE.md`
7. `SECURITY.md`
8. `DECISIONS.md`
9. `ISSUES.md`
10. `RISK_REGISTER.md`
11. `docs/TRADINGAGENTS_ASSESSMENT.md`
12. `P3D_ACCEPTANCE_PROMPT.md`
13. `src/seven_lens/analysis/contracts.py`
14. `src/seven_lens/analysis/pipeline.py`
15. `src/seven_lens/analysis/ports.py`
16. `src/seven_lens/application/ports/analysis.py`
17. `src/seven_lens/infrastructure/postgres_analysis.py`
18. `src/seven_lens/infrastructure/postgres_roles.py`
19. `migrations/0010_p3bc_evidence_analysis_up.sql`及down migration
20. 所有P3-B／C unit與PostgreSQL integration tests

先畫出實際existing call graph與persistence入口，再寫code。不得只看檔名或舊prompt推測API。

## 3. 不可違反的操作守則

未經使用者另行明確授權，不得：

- stage、commit、push、建立PR、merge、tag或改remote；
- 讀取`.env`、shell history、credential檔或任何真實Keychain item；
- 呼叫Alpaca、Tavily、Agnes、OpenCode、OpenAI或其他外部API；
- 加入live URL、live adapter、broker SDK、provider SDK、任意network client或secret capability；
- 修改P2 execution／reconciliation／control／ledger／broker authority；
- 修改migration `0010`或任何已套用migration/checksum；
- 讀取或發布repository根目錄ignored `skill/` corpus；
- 用SQLite、mock或文件敘述取代PostgreSQL authority／ACL／concurrency evidence；
- 刪除失敗測試、降低assertion、放寬schema、加入free-text/JSON repair fallback或把skip當成功；
- 因測試困難而跳過work package、合併P3-E功能或自行關Gate。

保留所有使用者既有變更。不得使用`git reset --hard`、`git checkout --`或清除整個工作樹。

## 4. 全Gate安全不變量

任何unknown、missing、stale、future、malformed、ambiguous、identity/hash drift、deadline crossing、DB conflict
都必須在下一個authority寫入前fail closed。不得從自由文字猜symbol、action、weight、status、citation或版本。

所有public contract與repository入口必須：

- exact type；拒絕bool冒充int、subclass、raw permissive mapping與unknown fields；
- frozen/slots或等價不可變設計；tuple取代mutable list/dict；
- 每欄、集合數量、nesting/node、canonical JSON與最終UTF-8 bytes有hard bounds；
- 使用canonical serialization與domain-separated content hash；builder自行計算hash，不信caller；
- `validate_integrity()`重跑所有nested invariants；fresh與resume共用同一validator；
- post-construction tamper在pipeline/repository實際authority入口被拒絕；
- error固定、bounded，不能回顯payload、portfolio identity、DSN、secret或raw exception。

deadline至少在create authority前、每次provider前、provider後及每次persist前重查。等於deadline的語意要在
contract、Python與SQL一致並用boundary tests固定。

## 5. 本Gate可修改範圍

優先新增或最小修改：

- `src/seven_lens/analysis/proposal_contracts.py`
- `src/seven_lens/analysis/proposal_ports.py`
- `src/seven_lens/analysis/proposal_pipeline.py`
- `src/seven_lens/application/ports/proposals.py`
- `src/seven_lens/infrastructure/postgres_proposals.py`
- `src/seven_lens/infrastructure/postgres_roles.py`
- migration loader與新`migrations/0011_p3d_*_{up,down}.sql`
- 對應unit／integration tests
- `DECISIONS.md`、`ISSUES.md`、`RISK_REGISTER.md`、handoff/progress/worklog

只有既有API需要versioned evolution時，才最小修改`analysis/contracts.py`、`analysis/pipeline.py`或
`application/ports/analysis.py`。不要為檔名整齊而搬動已驗收P3-B/C code。

## 6. 固定工作包順序

嚴格依D0→D7進行。每個工作包先寫會失敗的targeted test，再做最小完整實作，再加至少一個adversarial
regression。前一包未綠不得進下一包。

### D0 — 現況映射與ADR

- 記錄P3-C `AnalysisPipeline.run(..., symbol)`實際如何建立run/input/stage authority。
- 確認P3-C在`TraderPlan`後COMPLETE；P3-D使用獨立state machine，不把新stage塞入`0010`。
- 確認現有`PortfolioProposal.validate_against(AnalysisInput)`不能表達Risk拒絕後的refreshed snapshot。
- 若`DECISIONS.md`沒有P3-D ADR，以當時下一個編號記錄：per-symbol bundle、獨立proposal state、
  `ProposalContext`、最多一次retry及P4仍保留hard-risk approval。
- ADR只是決策紀錄，不是Gate Accepted證據；不要關閉R-29的P4部分。

### D1 — `ResearchBundleItem`與`ResearchBundle`

`ResearchBundleItem`至少承諾：

- child `analysis_run_id`、`analysis_input_id`、exact `symbol`；
- `packet_hash`、`snapshot_hash`、`trader_plan_id/hash`；
- frozen ordered evidence refs；producer/graph/prompt/data versions；
- child status必須是VALID／COMPLETE語意。

`ResearchBundle`至少承諾：

- `bundle_id`、`parent_input_id`、parent as-of/window/deadline；
- packet/portfolio snapshot/universe hashes；
- ordered `focus_symbols`、ordered items、frozen union citation set、`bundle_hash`。

固定規則：

- child run ID與input ID分別使用不同domain tag，由parent identity + canonical symbol deterministic衍生；
- domain tag、encoding、field order與hash/UUID轉換寫成單一helper與golden vectors；
- items精確一對一覆蓋focus symbols，拒絕missing/extra/duplicate/reordered/foreign；
- 每個child保留parent as-of/window/deadline/snapshot/universe/packet/data refs；
- 每個TraderPlan重新驗canonical hash與P3-C persisted identity/evidence/producer；
- 任一child非COMPLETE/VALID時不能建立bundle authority；
- union citations由ordered items deterministic推導，不信caller提供集合。

### D2 — Deterministic research batch coordinator

新增coordinator，把一個parent `AnalysisInput`依parent `focus_symbols`順序展開為per-symbol child inputs：

1. 每個child使用D1 helper衍生新的run/input identity。
2. `focus_symbols`縮成恰好該symbol；其餘portfolio universe、holdings、candidates、EvidencePacket、data refs、
   as-of/window/deadline完全保留。
3. 呼叫既有P3-C pipeline，不複製其stage實作。
4. crash-resume可載入已COMPLETE child，但必須重新跑完整persisted validation。
5. 前面child成功、後面child失敗時，可保留合法P3-C child authority供resume；不得建立partial bundle、risk或proposal。
6. 所有child完成後才依parent symbol order join；不得依task completion、dict/set或DB row順序。

P3-D保持deterministic serial execution；bounded parallelism留到P3-E。

### D3 — `ProposalContext`與Risk contracts

`ProposalContext`至少包含：

- `context_id`、attempt exact `1|2`；
- bundle id/hash；current full sanitized PortfolioSnapshot與hash；
- window/deadline/universe hash、ordered allowed symbols、frozen citations；
- graph/prompt/model/provider/data/memory versions；
- attempt 2時的previous context、superseded proposal id/hash、typed `RiskRejectionFeedback`；
- domain-separated `context_hash`。

attempt 1禁止previous/superseded/feedback；attempt 2三者必須全有。attempt 2只可刷新snapshot、remaining limits
與feedback，不可改research bundle、universe、window或evidence。固定時序：

```text
initial context/proposal < Risk review <= refreshed snapshot/context <= deadline
```

`RiskArgument`至少含context/bundle identity、viewpoint、round、bounded argument、ordered evidence refs與producer
versions。viewpoint只允許AGGRESSIVE／CONSERVATIVE／NEUTRAL，round只允許1／2。

`RiskDebateState`承諾六個完整argument，固定順序為round 1三觀點、round 2三觀點；每一組恰好一次，citation
全部屬於frozen bundle set，六個全通過才COMPLETE。

### D4 — `PortfolioProposal` versioned evolution

proposal改為綁`ProposalContext` identity/hash，並精確限制：

- attempt/superseded lineage；unique requests最多27 symbols；
- action只允許OPEN／INCREASE／REDUCE／CLOSE／HOLD；side只允許LONG／SHORT／FLAT；
- signed target weight canonical fixed-scale，absolute <= 0.15；拒絕negative zero；
- confidence scale-4，`<0.6500`只能HOLD；
- bounded evidence refs/reason codes/invalidators/same-day exit reason；
- graph/prompt/model/provider/data/memory versions與expiration <= context deadline；
- status VALID／INVALID／ABSTAIN；非VALID不得含requests。

驗證symbol屬allowed universe；emergency禁止OPEN/INCREASE；CLOSE必須FLAT/zero；LONG/SHORT與weight符號一致；
same-day exit reason只用於REDUCE/CLOSE；proposal/context/bundle/snapshot/evidence/version完整閉合。

絕對不要在P3-D加入gross/net/turnover/borrow approval、quantity、TargetPortfolio或OrderIntent。

### D5 — Provider port、initial與retry pipeline

新增與P3-C provider分離的`ProposalProvider`。stage只允許：AGGRESSIVE、CONSERVATIVE、NEUTRAL、
PORTFOLIO_MANAGER、PORTFOLIO_MANAGER_RETRY。request帶exact hashes、deadline、allowed symbols/citations與round；
output只允許exact `RiskArgument|PortfolioProposal`。

`ScriptedProposalProvider`只持有caller提供的deterministic outputs；沒有network/filesystem/shell/secret/DB/broker
capability。script key必須包含stage/viewpoint/round，missing、extra或重複消費固定失敗。

initial call trace固定：

```text
AGGRESSIVE r1 -> CONSERVATIVE r1 -> NEUTRAL r1 ->
AGGRESSIVE r2 -> CONSERVATIVE r2 -> NEUTRAL r2 ->
PORTFOLIO_MANAGER once
```

每次call前後重驗deadline；每個result重驗exact type、identity、hash、evidence、version與status。六個arguments
完整persist後才可呼叫PM；proposal驗證與model-independent audit/persist成功後才COMPLETE。

retry只能由typed Risk rejection + refreshed snapshot啟動：載入同一bundle與COMPLETE attempt 1；不重跑P3-C或
risk debate；只呼叫PM_RETRY一次。attempt 2必須精確supersede attempt 1，不能新增symbol/evidence/research。
相同attempt 2 same-hash只允許bounded idempotency；different hash、第二個attempt 2或第三次proposal永遠拒絕。

### D6 — Proposal authority與migration `0011`

使用獨立`ProposalStage`：

```text
PLANNED -> RISK_DEBATE -> PROPOSAL -> COMPLETE
PLANNED/RISK_DEBATE/PROPOSAL -> INVALID | EXPIRED
```

只允許相鄰forward transition；COMPLETE／INVALID／EXPIRED是sink。Python InMemory與PostgreSQL共用同一legal
transition whitelist與retry budget。

新增`0011_p3d_proposals_{up,down}.sql`或同語意名稱，至少包含research bundles/items、proposal runs、risk debate
results、portfolio proposals及feedback lineage。DB獨立強制：

- FK、hash/payload/bytes/attempt/state bounds；
- `(bundle_id,symbol)`唯一且child run/input不能跨bundle重用；
- context與authority run唯一；attempt只1/2，attempt 2精確指向attempt 1與feedback；
- proposal hash immutable；same-hash retry bounded；different hash collision拒絕；
- transition adjacency、terminal sink、row lock + unique constraint線性化；
- functions使用fixed `search_path=pg_catalog, public, pg_temp`並schema-qualify objects；
- PUBLIC無table/function rights；runtime只SELECT及EXECUTE exact approved functions；
- runtime不是owner，且無INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER/CREATE/TEMP/function replace。

更新migration loader、`provision_runtime_role()`與`verify_runtime_role()` exact allowlists。測function/table owner drift、
PUBLIC EXECUTE、extra function及每一種table privilege drift。

### D7 — 永久測試、文件與交接

最低永久測試矩陣：

- 0/1/max focus symbols；bundle missing/extra/duplicate/reordered/foreign；
- child identity derivation golden/collision/tamper；
- packet/snapshot/universe/data refs/producer/TraderPlan hash drift；
- top-level與nested post-construction mutation；fresh/resume parity；
- exact 6+1 call trace；retry只增加1 PM_RETRY且不重跑research/debate；
- context attempt/superseded/feedback/timeline/refreshed snapshot drift；
- invalid action/side/weight/confidence/status/expiration/symbol/citation/emergency increase；
- deadline create前、每call前後、每persist前及late return零下一stage authority；
- transition skip/regress/resurrect、same/different hash retry、retry budget；
- InMemory/PostgreSQL parity；real PG two-connection concurrency與loser rollback無orphan；
- migration up/down/up、`0010` checksum unchanged、zero-skip ACL tests；
- source invariant證明無provider SDK/network/secret/broker/P2/P4 capability。

更新文件時只能寫`P3-D implementation completed; pending independent acceptance`，精確記錄revision、dirty files、
命令數字、adversarial cases與未執行項目。P3-E維持Not started。

## 7. 必跑驗證命令

```bash
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q <all P3-D targeted unit and integration selectors>

UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/run_postgres_integration.sh
git diff --check
git status --short --branch
git diff --stat
git diff --name-status
```

不得把placeholder原樣執行；先用`rg --files tests`找出實際selectors。PG主張必須使用disposable PostgreSQL 16，
integration skip必須0。命令未跑或失敗時不得寫completed。

## 8. 停止前自我審查

逐項回答Yes/No；任何No都只能報partial/blocked：

1. 只修改P3-D及必要治理文件。
2. 沒有覆蓋既有dirty changes、舊migration或checksum。
3. 沒有credential、network、provider、broker、order、P2或P4 capability。
4. 每個fresh/resume/DB authority入口重驗nested integrity、identity、hash、evidence與deadline。
5. bundle只在所有child COMPLETE後發布，順序deterministic。
6. debate精確六個argument，retry最多一次且不重跑research/debate。
7. InMemory與真實PG16的state/idempotency/concurrency/ACL語意一致。
8. targeted、full、PG16與diff checks完整通過且數字已記錄。
9. 文件沒有把P3-D寫成Accepted／Closed，P3-E仍Not started。

## 9. 最終回覆格式

依序提供：

1. `P3-D RESULT: implementation completed | partial | blocked`
2. exact `HEAD`與完整dirty/untracked file set
3. 每個改動檔案及責任
4. 已強制的不變量與authority boundaries
5. targeted/full/PG16/quality/diff的精確pass/deselect/skip結果
6. adversarial PoCs的input／expected／observed
7. 未執行項目與原因
8. `GATE STATE: pending independent acceptance | Open`
9. 單一步驟：把`P3D_ACCEPTANCE_PROMPT.md`交給新的獨立模型

不要使用「應該」「大概」「看起來」。完成P3-D後立即停止，不讀P3-E credential、不建立P3-E code。
