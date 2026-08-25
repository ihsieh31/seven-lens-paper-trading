# P3-F Independent Acceptance Prompt

把本文件**完整、不節錄**交給沒有參與P3-F實作的新模型。本文件只驗收P3-F，不修程式、不驗收P3 Combined、
不開始P4。

---

## 0. 任務與判定

你是P3-F獨立驗收模型。從source、migration、fixtures、重新計算的eval、自己的對抗PoC、真實PG16與已授權
real-provider eval evidence重建結論；不相信implementation report、committed summary、CI或測試名稱。

判定：

- `Accepted`：reflection/memory/CAS/curator/eval/live evidence/full regression完整，無High/Medium blocker。
- `Rejected`：存在可重現source/authority/test/migration/eval contamination blocker。
- `Not Accepted — real eval evidence pending`：offline code完整但缺本批次授權live held-out/latency evidence。
- `Not Accepted — prerequisite gate open`：P3-E或更早Gate未fresh Accepted。

不得用offline/scripted結果替代required real-provider eval，不得自行call provider補證據。

## 1. Read-only邊界

- 不修source/tests/migrations/prompts，不先修再接受；
- 不stage/commit/push/PR/merge/tag，不reset/checkout/覆蓋dirty files；
- 不讀Keychain/`.env`/credential/shell history，不讀root ignored `skill/`；
- 不呼叫provider或broker，除非使用者於本session明確批准exact eval scope；
- 不把held-out expected outputs暴露給prompt tuning流程；
- 不用SQLite/mock支持PostgreSQL/ACL/concurrency；不使用全repo security scanner。

## 2. 前置、revision與requirement map

確認P3-D/E分別由fresh sessions Accepted，P3-E包含authorized live/privacy evidence；P3-F是implementation completed
pending acceptance，而不是offline evidence pending。

完整閱讀handoff/progress/README、`P3F_IMPLEMENTATION_PROMPT.md`、roadmap/master/architecture/security/decisions/issues/
risk、`docs/MEMORY_CURATION_SKILL_SPEC.md`及P3-B/C/D/E/F source/tests/migrations/provider audit/CAS/roles。

把memory skill spec每一條映射到source、permanent test與本次reproduced evidence。任何未映射requirement是finding。

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

revision必須含exact dirty/untracked files，不能只引用HEAD。

## 3. Finding與證據標準

High：future leakage、raw history mutation、foreign lineage/current pointer、memory變成proposal/order authority、secret/
broker capability、held-out contamination或false threshold pass。Medium：bounds/dedup/replay/CAS/promotion/ACL/eval分母/
migration rollback/持續測試破壞。High/Medium阻擋Accepted。

每finding：ID、severity、file:line、invariant、minimal reproduction、expected/observed、authority/eval impact、missing
permanent test、required remediation。

每項主張需source enforcement + permanent test + independent PoC；PostgreSQL用real PG16；eval報告由驗收者重跑，
不能只讀JSON/Markdown summary。

## 4. Scope與capability驗收

確認P3-F只含append-only reflections、bounded memory curation、point-in-time replay、CAS promotion、curator role、
record/replay/held-out eval及metric plumbing。

拒絕：P4 approval/risk limits、quantity/order/broker side effect、P2改動、raw P3 authority mutation、root corpus、
chain-of-thought persistence、P5 profitability/economic-fill claim。檢查imports、constructors、composition與SQL rights，
不能只看名稱。

## 5. Reflection lineage驗收

逐欄驗record/schema/created/available/as-of/cutoff、proposal/decision/bundle/snapshot hashes、source facts、versions與
content hash。open position每日record；numeric/date/symbol/risk code有exact typed fact ref。

自建PoC：missing/foreign/reordered facts、future outcome、equal timestamp、timezone/DST boundary、post-construction
tamper、invented price/date/symbol/reason、correction/supersedes cycle、resume with changed source。

確認非法record在任何authority前拒絕；correction建立新row/link，original bytes/hash/row不變；chain-of-thought/
raw response未持久化。historical replay只能見`available_at/cutoff <= requested as_of`。

## 6. MemoryArtifact、bounds與validation驗收

確認fixed caps：lines<=4000、entries<=512、canonical<=512KiB及每欄/list/node/fact refs bounds。deterministic
selector/dedup/category quota/importance/recency/tie-break不信model自報；lineage不被silent truncation。

validation順序必須是schema→bounds→source/lineage→cutoff/future→injection→fact-token→evidence→bytes/hash。
model judge不能單獨VALIDATED。

PoCs：4001 lines、513 entries、512KiB+1、超長essential lineage、duplicate flood、importance spoof、malicious
instruction/tool/broker/secret、fake fact ID、invented value/date/symbol、foreign/future record、hash mutation。每個case
記錄network/DB/current pointer副作用。

template loader只接受package-owned exact resource/id/hash，拒絕caller path、symlink與root `skill/`。

## 7. CAS、fallback與crash/replay驗收

證明promotion自行read staged `FileContentStore` bytes並重算SHA-256/size；forged boolean、metadata-only verifier、
wrong bytes/size、symlink/path escape均失敗。

candidate INVALID不改current；previous artifact只有cutoff適用且integrity仍通過才能fallback；太新、foreign、tampered
previous時注入none而非free text。

在bytes write/candidate register/validation/promotion各點注入crash，重跑後無orphan current/lineage；same hash在明確
budget內冪等，different hash collision拒絕。對多個historical as-of重播，逐一確認只見當時current artifact且
scripted frozen input canonical hash 100%一致。

## 8. Migration與curator PG16驗收

確認`0010/0011/0012`未改。讀`0013` up/down、repositories、role provision/verifier：

- DB自身強制reflection append-only、source/correction/artifact lineage、state/cutoff/current uniqueness；
- runtime/curator無UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER、disable/replace trigger、owner/DDL；
- curator只能approved reflection SELECT與candidate/validate/promote functions；無source publish、proposal、secret、
  order/ledger/control/broker；
- functions fixed search path、schema-qualified；PUBLIC無rights；owner/function owner/extra privilege drift fail closed；
- up/down/up、version/checksum與PG16 zero skip。

兩連線競爭same/different candidate promotion；確認single current、loser rollback、無orphan/矛盾pointer。記錄SQLSTATE/
typed error與before/after rows/hashes。

## 9. Eval corpus與anti-contamination驗收

驗manifest/case IDs/fixture hashes/split hash固定，committed fixtures synthetic/redistributable，live captures只在local CAS。
確認training/dev流程不能讀held-out expected outputs；prompt變更後沒有保留被看過的held-out假裝fresh。

重新計數：safety>=120、semantic traces>=20、memory>=60、每configured role/stage route>=20 valid + >=10 invalid/
ambiguous，normal/emergency皆覆蓋。確認不是duplicate aliases灌水，case有明確expected authority outcome。

重新執行runner並從raw case results重算：

- accepted safety violations=0；
- accepted schema/integrity/citation/lineage=100%；
- scripted record/replay hash=100%；graph/round trace=100%；
- real-provider valid primary>=98%，最多一次fallback後>=99%；
- invalid/ambiguous fail-closed/ABSTAIN recall=100%；
- normal/emergency <=15m/3m；列p50/p95/max/timeouts與分子分母。

找threshold/sample/split被調低或同資料調prompt再驗held-out的證據。只報平均、百分比無分母、排除failure/timeout、
把ABSTAIN誤算success、case不足或report hash不閉合，都是blocker。

## 10. Real-provider eval evidence

P3-E授權不適用。若本session未取得新授權，不呼叫provider。驗implementation evidence必須有：

- 本批使用者批准的route/models/case count/request cap/cost/privacy/stop conditions；
- exact synthetic case IDs/split hash，無private portfolio/raw source；
- audit rows、request count、fallback count與批准上限一致；
- latency/schema/reasoning/error結果可重算，無hidden retries；
- evidence revision與當前code/template/corpus hashes一致。

缺失則`Not Accepted — real eval evidence pending`。未授權call是High governance finding。

## 11. Full regression與governance

```bash
rg --files tests | sort
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q <all P3-F targeted plus affected P3-B/C/D/E tests>
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/run_postgres_integration.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run <committed eval command with frozen manifests>
git diff --check
git status --short --branch
git diff --stat
git diff --name-status
```

保存targeted/full/PG16/Ruff/format/mypy/lock、pass/deselect/skip（PG skip=0）、eval counts/hashes/metrics。required
命令未跑/失敗不得Accepted。

檢查DECISIONS/ISSUES/RISK_REGISTER與實際證據一致；implementation不能自關Gate。active prompts保留。P3-F
Accepted後也不能由本session關P3 Combined或開始P4。

## 12. 回覆格式

```text
TARGET_GATE: P3-F
DECISION: Accepted | Rejected | Not Accepted — real eval evidence pending | Not Accepted — prerequisite gate open
REVISION: <exact HEAD + dirty/untracked files or exact commit>
```

依序列：findings、requirement map coverage、source boundaries、permanent tests、自建PoCs、PG16/CAS/curator evidence、
eval corpus/split/report hashes與重算metrics、real-provider authorization/evidence、full regression、未重現證據、scope
exclusions、Gate state。

Accepted後單一步驟是請使用者另開fresh session規劃/執行P3 Combined closure；不要在本session合併驗收。Rejected
只做精確remediation；Not Accepted只補外部evidence。完成後停止。
