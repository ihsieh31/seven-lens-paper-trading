# P3-F Requirement Map

狀態：implementation evidence map；不是 Gate 通過證據。最終判定仍須重新執行 source review、
adversarial tests、真實 PostgreSQL 16、frozen offline eval，以及本批次另行授權的 real-provider eval。

| ID | 固定需求 | Source enforcement | Permanent tests / reproduced evidence |
|---|---|---|---|
| F1-01 | raw daily reflection 與 correction append-only；原 bytes/hash 不變 | `memory/contracts.py`, `memory/reflection.py`, migration `0013`, `postgres_memory.py` | `test_p3f_memory_contracts.py`; `test_p3f_memory_postgres.py` raw before/after hash 與 UPDATE/DELETE/TRUNCATE PoC |
| F1-02 | record 綁 created/available/as-of/cutoff、proposal/decision、bundle/snapshot hashes、typed facts 與版本 | `memory/contracts.py`, migration `0013` canonical payload/column closure | contract tamper、equal timestamp、timezone boundary、PG canonical mismatch PoC |
| F1-03 | observation 的數字、日期、symbol、Risk reason 必須有 exact typed fact ref；不保存 CoT/raw response | `memory/fact_closure.py`, `memory/contracts.py`, `memory/reflection.py` | invented value/date/symbol/reason、foreign/reordered fact、provider-output-before-append tests |
| F1-04 | correction 是新 record + typed supersedes；拒絕 self/unknown/cycle | `memory/contracts.py`, `memory/reflection.py`, migration `0013` | correction identity/cycle tests；PG supersedes availability/FK PoC |
| F2-01 | load approved sources → point-in-time filter → bounded provider input → strict closure → append → readback | `memory/reflection.py`, `application/ports/memory.py`, `postgres_memory.py` | resume changed source/field、future source、persist readback tests |
| F2-02 | resume 重驗 exact ordered lineage，不因 row 已存在而信任 | `memory/reflection.py`, `postgres_memory.py` | same-hash changed ID/type/time/fact、different-hash collision tests |
| F3-01 | `MemoryArtifact` 綁 source records、previous、entries、versions、state、deterministic bytes/hash/line count | `memory/contracts.py` | artifact hash/state/foreign-lineage tests |
| F3-02 | hard caps：4,000 lines、512 entries、512 KiB、單欄/evidence/total refs 有界；lineage 不截斷 | `memory/contracts.py`, `memory/selection.py`, migration `0013` | 4,001 lines、513 entries、512 KiB+1、multiline/overlong lineage tests；PG count/bytes PoC |
| F3-03 | category quota、dedup、policy importance、recency、canonical tie-break 全 deterministic，不信 model importance | `memory/selection.py` | reversed input、duplicate flood、importance spoof、quota tests |
| F3-04 | 優先保留 repeated Risk rejection、calibration、position/same-day-loss、borrow/liquidity、regime、unresolved risk | `MemoryCategory` + selector policy；frozen memory eval fixtures | selector golden retention tests；offline memory-family report denominators |
| F4-01 | validation 次序固定為 schema/type → bounds → authority/lineage → point-in-time → injection → fact-token → evidence → bytes/hash | `memory/validation.py` | stage-specific invalid result tests；production-probe offline eval |
| F4-02 | model score 只可輔助，不能單獨 VALIDATED | `memory/selection.py`, `memory/validation.py`, `memory/curation.py` | spoofed model score、invalid candidate remains INVALID tests |
| F4-03 | package-owned exact template；caller path/symlink/root `skill/` 不可載入 | `memory/template.py`, `memory/curation.py` | fixed id/version/hash/content 與無 path-loader source invariant |
| F4-04 | source/model instruction、tool/broker/order/secret/current 指令一律視為不可信資料 | `memory/fact_closure.py`, `memory/validation.py` | prompt-injection/capability-escape cases與 source import invariant |
| F5-01 | staged bytes 必須自行 exact readback，重算 SHA-256/size；不信 boolean/metadata verifier | `memory/promotion.py`, `postgres_memory.py`, migration `0013` | forged store、wrong bytes/hash/size tests；PG bytea digest PoC |
| F5-02 | append-only state/history + atomic single current；invalid candidate 不改 current | `memory/promotion.py`, migration `0013`, `postgres_memory.py` | invalid candidate/current tests；兩連線 concurrent promotion PoC |
| F5-03 | fallback 只見 created/cutoff/promoted 均不晚於 requested as-of 且 bytes 完整；否則 none + bounded alert | `memory/promotion.py`, `current_memory_artifact()` | historical replay、too-new/tampered/no-safe-memory tests |
| F5-04 | crash/retry same-hash bounded idempotent；different hash collision；無 orphan pointer/lineage | promotion repositories + migration functions | injected crash points/unit retries；PG transaction rollback/concurrency PoC |
| F6-01 | migration `0013` 管理 reflection/source/correction、artifact/source/state/history/current/audit | `0013_p3f_reflection_memory_{up,down}.sql`, `migrations.py` | up/down/up、checksum、schema inventory、PG16 zero-skip tests |
| F6-02 | runtime 與 curator 均無 UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER/DDL；PUBLIC 無 rights | `postgres_roles.py` | real-role privilege matrix、owner/function/search_path drift PoC |
| F6-03 | runtime 只 append reflection/read current；獨立 curator 只讀 approved views + register/validate/promote；無 proposal/source publish/secret/order/control/broker | `postgres_roles.py`, `application/ports/memory.py` | two-login capability tests與 startup verifier drift tests |
| F7-01 | synthetic/redistributable corpus；case/fixture/split/report hashes frozen，四 split 分離 | `evals/models.py`, `evals/corpus.py`, `tests/fixtures/p3f_evals/` | tamper、symlink/path escape、ID overlap、hash closure tests |
| F7-02 | tuning 不可讀 held-out expected outputs；final evaluation 才解封 | `evals/corpus.py` | observed-file-read anti-contamination test |
| F7-03 | safety ≥120、semantic trace ≥20、memory ≥60；每 route held-out valid ≥20 + invalid/ambiguous ≥10；normal/emergency | frozen manifests + `evals/runner.py` | runner 重新計數、canonical duplicate-material rejection、route denominator report |
| F7-04 | Offline Correctness：safety violation 0；schema/integrity/citation/lineage、record/replay、trace、invalid recall皆100%；latency含完整分母 | production probes + offline runner v2 | raw result recomputation與committed V12 frozen report byte/hash match |
| F7-05 | Live Model Quality：至少250/260 strict completions；completed正確率≥98%；response-contract violations=0；130/130 invalid/ambiguous pre-network fail-closed | authorization v4、live evidence v2、strict parser與provider-eval seam | 本批新授權後exact case/attempt/audit/latency/error evidence；transport-only failure不混作response品質，但coverage不得低於250 |
| F7-06 | Provider Transport：first-attempt≥95%、最多2 retries後eventual≥99%；只重試TIMEOUT/TRANSIENT/RATE_LIMIT；260 logical／780 attempts、backoff+jitter、連續3案exhausted circuit breaker、0 fallback | `evals/provider_eval.py` orchestrator；P3-E transport仍無hidden retry | permanent transient/non-retryable/circuit/accounting tests；P6前rolling 7日且≥200個另行授權synthetic canary calls |
| F8-01 | 每週 curation 不在交易 critical path；memory 永非 proposal/Risk/order/broker authority | capability-minimal ports/imports；無 execution composition | source import/invariant tests與 scope review |
| F8-02 | compaction 保存 requested/effective reasoning、route/input/output/report hashes 與 audit metadata | `memory_curation_audits`, provider-eval report contracts | PG audit row closure；authorized provider-eval report recomputation |
| F8-03 | forecast calibration 只建 plumbing，不宣稱 P5 profitability/economic-fill | memory categories/eval report schema | docs/source scope review；不得出現 P5 threshold claim |

## Required rerun boundary

1. 全部 P3-F targeted 加受影響 P3-B/C/D/E tests。
2. `scripts/verify_p1.sh` 全綠。
3. `scripts/run_postgres_integration.sh` 使用真實 PostgreSQL 16，P3-F zero skip。
4. committed V12 frozen offline eval由raw cases重算counts、metrics、split/report hashes。
5. 使用者針對P3-F本批次另行批准exact route/model、390 cases、260 logical requests、780 attempt cap、每案2 retries、180秒、cost/privacy/backoff/circuit/stop scope後，才可執行real-provider eval；P3-E與V1～V9授權不延伸。
6. P3-F功能驗收分別報Offline Correctness與Live Model Quality；Provider Transport另列Green/Red及完整分母。P6前再用另行授權rolling canary重驗，不得把單次成功寫成永久availability。
