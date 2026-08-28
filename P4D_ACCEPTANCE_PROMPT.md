# P4-D Independent Acceptance Prompt

完整交給未參與實作的新模型。只驗收P4-D；read-only、不修code、不開始P4-E。

## 0A. 審查協議與判決演算法

先source/call graph，再independent Decimal oracle/PoC，再永久tests與真實PG16。不得修改檔案、連外、讀secret或呼叫
真實model/broker。Scripted provider只能計數retry，不可替代Risk oracle。

- 任一short獲准、limit/freeze fail-open、自我核准、非原子Target、authority failure仍呼叫model：High→Rejected。
- 計算/rounding/determinism/retry/ACL錯誤：Medium→Rejected。
- 缺A-C fresh Accepted、ADR-039 manifest/hash或PG evidence：Not Accepted。
- 只有無authority影響的Low可不阻擋；不得conditional pass。

## 0. 判定與前置

只允許Accepted／Rejected／Not Accepted — prerequisite/evidence pending。A/B/C需fresh Accepted。保存exact revision、
dirty/untracked、diff/check、migration狀態；禁止credential/network/model/broker。Blocker只最小重現。

## 1. Scope／purity

讀實際imports、constructors、composition。證明Risk core是純函式，無HTTP/Keychain/provider/psycopg/Alpaca/clock
backend；D只產生RiskDecision與TargetPortfolio，無quantity、IntentPlan、P2 execution/order mutation。任何model
call只可透過既有P3 scripted retry seam，authority failure時call count=0。

## 2. Contract mutation

逐欄tamper policy/input/decision/target：proposal/context/bundle、attempt、snapshot/reconciliation/control、market/
security/universe versions、limits、reason/warning、timestamps、hash。測unknown fields、bool/subclass、float、NaN、
negative zero、post-construction mutation。實際service/repository入口須在新authority前拒絕。

確認TargetPortfolio無quantity/order/credential/free text，caller不能偽造approved或remaining limits。

## 3. Hard-limit邊界矩陣

用exact Decimal自建每個limit前/等於/後：gross90%、cash10%、name5%、sector25%、cluster30%、turnover20%、ADV0.1%、
15 positions、daily loss1%、drawdown8%、quote5秒、spread30bps。特別測：

- individual都pass但aggregate breach；open orders/same-day fills造成projected breach；
- no minimum exposure：全cash合法，不得強迫trade；
- short action/side/negative weight所有路徑typed拒絕；
- freeze只允許真正降低曝險，不可用REDUCE標籤增加target；
- IEX limited是warning/P7 blocker，不因flag本身拒絕P4；quote缺/stale/wide/conflict仍拒絕；
- quarantine、ineligible security、stale versions與FULL+CLEAN要求不可繞過。

### 3A. ADR-039 sector／cluster／turnover獨立oracle

不得import production exposure/turnover helpers。至少手算並從public Risk service重現：

1. **Sector**：A-J每division、兩name合計0.25前／等於／後、current unknown OPEN/INCREASE與數值REDUCE；mapping hash/as-of
   drift、GICS/OTHER fallback都必須NO_TRADE。
2. **Cluster**：single/multiple connected components、兩name合計0.30前／等於／後、current holding加入node union、
   CLUSTER_UNKNOWN OPEN/INCREASE與數值REDUCE、stale/different cluster snapshot。Risk不得自行重算rho。
3. **Turnover arithmetic**：用不同數字分別手算actual same-day normal fills、working remaining worst-case notional、
   current projected notional與desired notional；驗證三者相加後除previous-close FULL+CLEAN NAV，**不除以2且不互抵**。
4. 測turnover 0.20前／等於／後、current NAV與base NAV不同、partial fill避免filled/remaining double count、open BUY/SELL、
   未成交SELL proceeds不支應BUY、unknown order/fill、missing/nonpositive base NAV。
5. Corporate-action/hard-risk typed exit應不阻擋於normal 20% cap但仍有gross telemetry；偽造NORMAL為exit或只改label而數值
   增加曝險必須拒絕。

每個case列exact numerator components、denominator、expected Decimal、observed decision/reasons/provider call count與DB rows。

## 4. Retry state machine PoC

記錄exact provider trace與persisted rows：

- 可修正attempt1拒絕→typed feedback→刷新完整snapshot→只一次PM_RETRY→attempt2 review；
- analysts/risk debate/bundle不重跑，attempt2不新增symbol/evidence/research；
- attempt2 rejection固定NO_TRADE；第二attempt2、第三proposal、foreign feedback、different bundle拒絕；
- reconciliation/stale/conflict/deadline/DB failure直接NO_TRADE且provider call=0；
- retry期間snapshot/limits變動必須全量重算，不能沿用舊partial approval。

## 5. Determinism與PostgreSQL

同inputs不同iteration/target order應canonical byte-identical；same hash idempotent，different hash conflict。真實PG16
驗證APPROVED decision與TargetPortfolio原子性、crash points、two-reviewer race、rollback、runtime direct insert/update/
delete拒絕、migration up/down/up與legacy preflight。Corrupt persisted approved row不得resume成target。

## 5A. 強制獨立重算與審查順序

1. 列diff/import graph，證明pure core沒有HTTP/provider/DB/clock/broker，且D沒有quantity/Intent/P2 write。
2. 從合法input逐欄mutation，從public risk service入口觀察decision、rows與provider call count。
3. 以獨立Decimal oracle重算current/projected portfolio及每個limit；不要import production計算helper當oracle。
4. 建混合理由cases：retryable only、non-retryable only、兩者並存；驗provider恰0或1次及attempt lineage。
5. 做10種target/collection/DB order permutation，對decision/Target canonical bytes/hash逐byte比較。
6. 真實PG16做crash每點、two reviewers、same/different hash、corrupt readback、runtime direct approve/insert/update/delete。
7. 最後跑focused/full regression、lint/type/diff並記全部skip/deselect。

## 5B. Mandatory matrix

```text
Requirement | Formula/policy version | Source file:line | Independent oracle/PoC | Test | PG | Verdict
Input/hash/version/deadline closure
Projected positions/open orders/same-day fills
Long-only and all short encodings rejected
Gross/cash/name/count/SEC-SIC sector/connected-component cluster
Gross turnover V1 fills+working+proposed/base NAV, no half/netting
ADV 0.1% exact approved definition
Daily loss and drawdown numerical reduction
Quote/spread/quarantine/data conflict
Retry allowlist and one PM retry
Decision/Target determinism and atomicity
Runtime cannot self-approve or mutate
No quantity/Intent/broker capability
```

邊界至少測`limit - smallest unit / == limit / limit + smallest unit`；percent用exact Decimal，時間用`-1µs/= /+1µs`。

## 6. Full regression與報告

最低read-only命令集：

```bash
uv run --locked pytest tests/test_p4d_*.py tests/test_p3d_proposal_contracts.py tests/test_reconciliation_and_ledger.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

Production Risk helper不得作oracle；需另用獨立Decimal計算與public-entry PoCs。

跑focused、P3 proposal、A～C、P2 safety、完整non-integration、完整PG16 zero-skip、Ruff、format、mypy、diff check。
High/Medium finding、non-determinism、short/quantity/broker越界或skip漂移阻擋Accepted。

```text
P4-D VERDICT: Accepted | Rejected | Not Accepted — prerequisite/evidence pending
REVISION: <exact revision>
```

依序列findings、purity/scope、contract mutation、limits、retry trace、PG/ACL、full regression。Accepted後只建議另開
P4-E；不修改檔案或狀態。

每個finding列severity、requirement、file:line、public-entry input、independent expected calculation、observed decision/
rows/provider calls、impact與限定修復範圍。報告另列review coverage、完整matrix、Decimal oracle vectors、retry trace、
PG server/version/two-connection steps、原始commands/counts/skips及unverified claims。Matrix任何PENDING/FAIL或任一
High/Medium均不得Accepted。
