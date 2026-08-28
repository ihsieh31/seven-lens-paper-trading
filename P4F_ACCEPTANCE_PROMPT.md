# P4-F Independent Acceptance Prompt — P4 Final Gate

完整交給未參與P4-A～F實作的新模型。這是P4-F與P4 Combined Final Gate驗收；read-only、不修code、不開始P5。

## 0A. 最終驗收審查協議

這是從零重建證據的final Gate，不是彙總舊verdict。必須：

1. 先建立A-F requirement matrix，再讀source/diff/call graph；不得先跑full suite後因綠燈省略source review。
2. 每個mandatory claim需要current `source:file:line + independent public-entry PoC + permanent test + PG/live evidence`。
3. A-E舊Accepted只證明歷史revision；current affected files有漂移時需重驗，不能引用舊結論。
4. 不得修改任何檔、format、refresh fixtures、重建frozen evidence、連外、讀secret或呼叫model/broker，除非本次exact
   授權明列該外部GET／model evidence；授權不含修code或submit。
5. Tests/CI/docs/implementation report都不能單獨關Gate；第二actor安全需真實跨connection/process證據。

## 0B. Finding與verdict演算法

| 類型 | 例子 | Verdict |
|---|---|---|
| High | broker/P2 write可達、short/limit bypass、錯誤identity/plan、secret或未授權external call | Rejected |
| Medium | future leakage、non-determinism、resource無界、resume/race/ACL fail-open、必要warning/lineage遺失 | Rejected |
| Low | 不影響authority的局部維護性／operator文字問題 | 可Accepted但需列出 |
| Evidence gap | 前置Gate、PG16、ADR-039四manifest/hash、rights/schema或required authorized live evidence不足 | Not Accepted |

判決順序：prerequisite/evidence gap→Not Accepted；否則任一High/Medium→Rejected；否則matrix全部PASS才Accepted。
不得conditional pass、風險接受、延後修復後先關門。

## 0. 唯一角色與判定

從當下source、A～F prompts、永久tests、自建對抗PoC、真實PG16與當次authorized source evidence重建完整P4證據。
不相信implementation/acceptance舊報告、handoff、commit message、CI或測試名稱。

只允許：

- `Accepted`：A～F requirement全部在exact current revision成立，無High/Medium blocker，必要平台證據完整；
- `Rejected`：可重現High/Medium blocker；
- `Not Accepted — prerequisite/evidence pending`：任何前置Gate或必要live/source/PG/CI證據缺失。

不允許conditional pass。若A～E曾Accepted但current source已變更，必須重驗受影響requirements，不可引用過時結論。

## 1. Read-only、revision與禁止事項

不修改/format/source/tests/migration/docs，不stage/commit/push/reset，不讀credential/skill，不呼叫model/broker。真實source
GET只在本次exact授權下執行；否則不得自行補證。保存：

```bash
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -10 --oneline --decorate
git diff --stat
git diff --name-status
git diff --check
```

逐一列untracked與migration checksums。Revision必須是exact HEAD + dirty/untracked set，不能只寫branch。

## 2. 前置Gate與requirement map

完整閱讀P4計畫、12份A～F prompts、治理文件、全部P4 source/tests/migrations及P1～P3 boundaries。建立A～F requirement
map：每項對應source enforcement、permanent test、自建PoC、PG/ACL（適用時）、live source evidence（適用時）。

任何缺口判Not Accepted或Rejected；不能以F integration test取代A～E contract/adversarial evidence。

## 3. Capability與scope closure

用imports、composition constructors、runtime roles、DB grants、worker queries與spies證明：

- single account、long-only、short false、zero-cost、保守limits不可override；
- source roles不升權，IEX limited不冒充SIP/NBBO；
- Risk只有typed proposal→Target，LLM/source不能核准；
- P4只發布APPROVED_NO_SUBMIT plans；broker submit=0、cancel=0、P2 outbox/order writes=0；
- 無short/margin/BUY-to-cover、paid/live endpoint、P5 backtest、P6 Shadow或P7 execution能力。

任何隱性port、dynamic import、SQL bypass或P2 worker可見plan都是High。

## 4. End-to-end adversarial scenarios

自行建立至少下列完整scenario並檢查每stage DB副作用：

1. clean long proposal→Risk approve→whole-share no-submit plan；
2. short proposal→一次typed retry移除short→approve；attempt2仍short→NO_TRADE；
3. source outage／role fallback attack／future ALFRED revision→零新增authority；
4. symbol reuse＋split discovery→candidate block；official confirm→仍不進analysis；
5. confirmed held long且無working orders/FULL+CLEAN→corporate exit no-submit plan；
6. late/halt/working order/quantity drift/withdrawal→REVIEW_REQUIRED，零cancel/submit；
7. stale quote、wide spread、IEX warning、ADV/name/sector/cluster/gross/cash/turnover/daily-loss/drawdown邊界；
8. control pause、UNKNOWN intent、non-FULL reconciliation、version drift between Risk and publish；
9. crash at everystage、restart/resume、same/different hash、two-process same-window race；
10. audit/telemetry/DB failure後安全block仍durable、另一actor不能publish plan。

每個expected/observed包含exact rows/counts/call counts，不只看exception。

## 5. Source evidence與零付費邊界

核對當前官方schemas/terms/limits與SourceManifest。若implementation聲稱production-ready adapter，至少需per-family bounded
authorized GET-only evidence或明確acceptance policy所允許的官方fixture/contract證據；沒有當次授權不能自行GET，必要
evidence缺失則Not Accepted。

確認所有sources cost=0；免費key仍scoped SecretRef。IEX`LIMITED_MARKET_COVERAGE`與OPEN-038保持P7 blocker；
yfinance/Tavily/GDELT不得支撐material price/security/corporate-action authority。Live/model/provider transport與P4
functional correctness分開報告。

## 6. 真實PostgreSQL與authority

使用PG16與runtime roles驗證全部P4 migrations up/down/up、legacy preflight、checksum、FK/CHECK/UNIQUE/exclusion、
append-only、publication CAS、same/different hash、corrupt readback、rollback/crash、兩連線競態與ACL negative probes。

特別證明runtime不能直接解除quarantine/control、APPROVE Risk、publish Target/plan、UPDATE immutable records、insert P2
orders/outbox、執行owner DDL/functions。第二actor安全性需跨connection/process evidence，不接受fake boolean。

## 7. Deterministic replay與resource bounds

對config/source/security/market/universe/candidate/proposal/Risk/Target/Intent全鏈重播：相同inputs、不同collection/DB/
thread order必須canonical byte/hash一致。逐contract測unknown/missing/subclass/bool/NaN/negative zero/post-construction
tamper、max/max+1 items/bytes/nesting。Future/as-of/deadline前/等於/後語意跨Python/SQL一致。

## 8. Full regression

執行A～F focused tests、P1～P3完整non-integration、完整real PG16 zero-skip、migration/provisioning/ACL scripts、Ruff
format/check、mypy、lock/source invariants、`git diff --check`。若有exact-SHA remote CI，只作補充；不能取代local source/
PoC/PG evidence。任何unexpected skip、flake未解、baseline drift都列finding。

## 8A. 強制final review順序

1. **Revision inventory**：保存exact HEAD＋dirty/untracked；逐檔標A-F/使用者既有/越界，核對migration checksums。
2. **Requirement trace**：把12 prompts每個MUST/MUST NOT映射到current source/test/PG/live evidence；不接受抽樣。
3. **Static capability attack**：搜尋imports、constructors、dynamic import/callable、SQL functions/grants、worker queries、
   environment flags、URLs/methods，證明zero-submit與roles不可繞過。
4. **Contract mutation/resource bounds**：逐contract unknown/missing/type/tamper/max+1/canonical/permutation。
5. **Independent end-to-end PoCs**：按第4節scenario逐stage記expected/observed rows、hashes、calls、terminal state。
6. **PG authority**：新disposable PG16，baseline→head→down/up、legacy preflight、runtime/owner roles、crash/race/readback。
7. **External evidence**：只有exact授權才執行；offline/source transport/model quality/P7 readiness分欄，不互相替代。
8. **Regression**：最後執行full non-PG/PG/lint/type/diff；解釋每個skip/deselect、flake、baseline change。

## 8B. P4 Combined mandatory matrix

```text
ID | Requirement | Current source:file:line | Independent PoC | Permanent test | PG/live | PASS/FAIL/PENDING
A1 immutable single-account long-only conservative config
A2 closed source roles/manifests and zero-cost policy
A3 exact-host GET-only bounded transport; secrets redacted
A4 every source-family point-in-time parser/rights/failure closure
B1 stable identity and historical symbol lineage
B2 split discovery/formal confirmation/conflict/withdrawal
B3 three-seam quarantine and durable safe state
C1 market snapshot/ADV/calendar/freshness/coverage
C2 ordinary-common-stock hard filters; exact sec-sic-division-v1 and p4-correlation-cluster-v1 hashes/oracles
C3 exact p4-factor-v1 hash/formulas and deterministic 100->30->12/5
D1 pure Risk and complete authoritative inputs
D2 all limits/freeze/short/quote/quarantine boundaries; exact p4-gross-turnover-v1 hash/oracle
D3 exactly one PM retry and atomic Target
E1 independent whole-share/collar/cash oracle matches
E2 P4 plan cannot enter P2 worker/outbox; all calls zero
E3 corporate-action no-submit plan and review cases
F1 capability-minimal startup/composition/roles
F2 stage failure/crash/resume/deadline semantics
F3 single-account/window cross-process authority
F4 observability/privacy/resource bounds/operator report
X1 migrations/ACL/checksums/legacy/full regression
X2 live source evidence and IEX/P7 blocker correctly separated
```

Matrix每列需實際expected/observed；若一個PoC支撐多列，要說明不同assertions，不得只貼同一test名稱。

## 8C. Side-effect accounting

每個E2E/adversarial case都列：HTTP GET by family、POST、model calls、broker submit/cancel、P2 outbox/order/fill writes、P4
records by table、telemetry/audit rows。未授權情況預期external/model/broker/P2 counts全0。不能觀測的count是evidence gap，
不是推定0。

## 9. Final report與狀態

最低read-only命令集：

```bash
uv run --locked pytest tests/test_p4{a,b,c,d,e,f}_*.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

若shell brace/glob無matched files，先用`rg --files tests | sort`列出實際P4 tests並逐檔執行；不得把unmatched glob當0 tests
通過。另需完成第8A/8B/8C的independent PoCs與matrix，不能只執行此命令集。

```text
P4-F / P4 COMBINED VERDICT: Accepted | Rejected | Not Accepted — prerequisite/evidence pending
REVISION: <exact HEAD + dirty/untracked set or exact commit>
```

依序列：findings、A～F requirement map、scope/capability、end-to-end PoCs、source evidence、PG/ACL/concurrency、deterministic
replay/resource bounds、full regression、remaining open risks與未重現證據。

只有Accepted且使用者允許文件更新時，才把P4標Closed；仍不得開始P5。Rejected只交精確remediation，Not Accepted只列
缺失證據。任何結果都不授權P5、P6、P7、model/source新呼叫或Paper submit。

每個finding固定列ID/severity/requirement/file:line/public-entry PoC/expected/observed rows+calls/impact/test gap/限定修復
範圍。報告另列review coverage、完整matrix、A-E current revalidation、PG server/version/cross-process steps、external
authorization ledger、side-effect accounting、原始commands/counts/skips、unverified claims與remaining P5/P7 blockers。
無finding時寫`no actionable findings`，但不可省略matrix與證據。
