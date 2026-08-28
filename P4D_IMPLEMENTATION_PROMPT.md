# P4-D Implementation Prompt — Deterministic Risk／One Retry／TargetPortfolio

完整交給P4-D實作模型。本文件只授權P4-D；完成後停止，不得開始P4-E。

## 0A. 弱模型硬性執行協議

1. A/B/C都必須fresh Accepted且current source未漂移；C的ADR-039 factor/sector/cluster manifests必須exact/hash閉合。
2. Risk只執行已核准policy，不設計投資策略、不最佳化、不裁量放寬；任何unknown必須typed fail closed。
3. 先把每條policy轉成獨立純函式＋邊界測試，再組合固定順序；不得先寫大service後補測試。
4. 所有金額、weight、rate、price用exact Decimal/cents；不得float、`round()`、truthy coercion或bool-as-int。
5. 不得猜sector/cluster；只接C已Accepted的versioned mapping snapshots並驗hash/as-of。
6. 不得在D計算shares、limit price、order side或IntentPlan；不得將P3 proposal字段直接標approved。

## 0B. 可動與禁動範圍

| 類別 | 可動 | 邊界 |
|---|---|---|
| Pure domain | `risk/contracts.py`、`risk/engine.py`、`portfolio/contracts.py` | RiskDecision/TargetPortfolio only |
| Application | `application/ports/risk.py`、`application/risk_service.py` | authority assembly與一次retry orchestration |
| Persistence | `infrastructure/postgres_risk.py`、next migration | append-only/atomic decision+target |
| Tests | `test_p4d_*`、PG integration、source invariant | 不修改P3 provider結果或P2 expectations |
| 條件式 | P3 proposal/retry public port、A-C/P2 read-only snapshots | 不改其wire/hash/state machine |
| 禁止 | quantity/intent/P2 order/broker/source HTTP/provider transport | 不建未來stub/capability |

## 0. 唯一任務

以Accepted的P4-A policy、P4-B quarantine、P4-C market/universe/candidate authority與P3 `PortfolioProposal`建立純
deterministic Risk Engine、typed `RiskDecision`、一次PM重申整合與immutable `TargetPortfolio`。P4-D止於核准的
target weights；不計quantity、不建IntentPlan、不呼叫P2/broker，也不得進行真實model call。

成功語句：`P4-D implementation completed; pending independent acceptance`。

## 1. 前置與source閱讀

保存exact repo/revision/dirty/untracked/diff/migration狀態。P4-A/B/C都需fresh Accepted；任一Open即停止。完整閱讀
P4計畫與A～D prompts、P3 proposal contracts/pipeline/repositories/model envelope/audit、P2 ledger/reconciliation/control、
C market/universe contracts、A policy config、B quarantine，以及全部相關tests/migrations。

先畫出：PortfolioProposal attempt1 → authoritative snapshots → Risk review → feedback → P3 PM retry(fake only) → attempt2
review → TargetPortfolio。不得複製或放寬P3 proposal validation。

## 2. 禁止事項

不stage/commit/push；不連外、不讀credential、不真實model/broker call；不修改ADR-038 limits；不允許env/model/source
override；不加optimizer自由鬆綁；不啟用short/margin/borrow；不計shares/price/order；不改P2 order state；不在
authority/freshness/reconciliation失敗時呼叫PM retry。

## 3. Owned modules與純核心

優先新增／最小修改：

```text
risk/contracts.py
risk/engine.py
portfolio/contracts.py
application/ports/risk.py
application/risk_service.py
infrastructure/postgres_risk.py
```

Risk core必須是純deterministic函式：只接typed values，不importHTTP、Keychain、provider/model、psycopg、Alpaca或
clock backend。Time由caller以typed `reviewed_at`傳入並驗證。

## 4. RiskPolicySnapshot與input closure

每次review建立／載入immutable policy snapshot，exact綁ADR-038 config hash、ADR-039四個manifest hashes與effective
trading date。Input closure
至少包括：proposal/context/bundle hashes、proposal attempt、FULL+CLEAN portfolio snapshot、control state、open orders/
same-day fills、market snapshot IDs、universe/security-master versions、quarantine results、sector/cluster mapping、ADV、
daily-loss/drawdown state、deadline與source coverage warnings。

任一missing/stale/future/conflict/hash/version mismatch在Risk authority前fail closed。不同snapshot不得重用舊decision。

## 5. Closed contracts

`RiskDecision`：decision/proposal/policy/snapshot identities、attempt、status `APPROVED|REJECTED|NO_TRADE`、accepted/
rejected targets、closed reason codes、warnings、remaining limits、review time/hash。

Reason codes至少：`SHORT_DISABLED`、`NAME_LIMIT`、`POSITION_COUNT_LIMIT`、`GROSS_LIMIT`、`CASH_BUFFER`、
`SECTOR_LIMIT`、`CLUSTER_LIMIT`、`TURNOVER_LIMIT`、`ADV_LIMIT`、`DAILY_LOSS_STOP`、`DRAWDOWN_FREEZE`、
`STALE_QUOTE`、`WIDE_SPREAD`、`DATA_CONFLICT`、`CORPORATE_ACTION_BLOCK`、`SECURITY_NOT_ELIGIBLE`、
`RECONCILIATION_NOT_CLEAN`、`UNRESOLVED_INTENT`、`CONTROL_PAUSED`、`DEADLINE_EXPIRED`。

`LIMITED_MARKET_COVERAGE`是mandatory warning與P7 blocker，不在P4-D單獨造成NO_TRADE；但missing/stale/wide/conflict
仍拒絕。

`TargetPortfolio`只含long target weights、NAV/policy/decision/snapshot lineage、version/hash；不能含quantity、order
type、endpoint、credential、free text。Caller不能提供constraints result或self-approved flag。

## 6. 固定Risk規則與計算順序

使用exact Decimal，不用float。順序固定且測試：

1. proposal `VALID`、attempt/context/bundle/version/deadline完整；
2. latest FULL+CLEAN reconciliation、control未pause、無UNKNOWN/REVIEW_REQUIRED intent；
3. security/universe eligible且B quarantine=`ELIGIBLE`；
4. quote≤5秒、spread≤30bps、ADV/source無conflict；
5. `short_enabled=false`：任何negative/SHORT/SELL-to-open要求拒絕；
6. long/total gross≤90%、cash≥10%、positions≤15、name≤5%、sector≤25%、cluster≤30%；
7. normal turnover≤20%、每筆預期position≤ADV 0.1%；
8. daily loss≥1%時禁止OPEN/INCREASE；drawdown≥8%只允許REDUCE/CLOSE/HOLD；
9. 所有target集合整體重算，不能逐項通過後形成aggregate breach；無可行集合時持有現金／NO_TRADE。

不設最低曝險；Risk不得為達40%/其他floor強迫投資。RISK_EXIT/corporate exit例外不在本Gate一般proposal中偷實作。

## 6A. ADR-039 exact sector／cluster／turnover計算

### Sector 25%

只接受C `sec-sic-division-v1` snapshot的A–J division與exact hash/as-of。對proposal後完整long target weights按division求和：

```text
sector_weight[division] = sum(target_weight for every long target in division)
```

等於0.25可通過，超過最小Decimal單位為`SECTOR_LIMIT`。任何target/current position在OPEN/INCREASE所需計算時是
`SECTOR_UNKNOWN`、mapping stale/conflict或taxonomy/hash非V1，整體`NO_TRADE`且不可retry。數值上REDUCE/CLOSE既有unknown
position可通過unknown taxonomy gate，但必須證明該name與aggregate gross exposure都不增加；不能用action label取代數值。

### Cluster 30%

只接受C `p4-correlation-cluster-v1` daily snapshot；target與current holdings必須位於同一snapshot node set，snapshot截至
前一完整session且未漂移。對connected-component cluster求完整proposal後weights：

```text
cluster_weight[cluster_id] = sum(target_weight for every long target in cluster)
```

等於0.30可通過；超過為`CLUSTER_LIMIT`。`CLUSTER_UNKNOWN`的OPEN/INCREASE固定NO_TRADE；只有數值上同時降低name、
cluster-known exposure（若可知）與total gross的REDUCE/CLOSE可通過。Risk不得重算correlation、改threshold、把unknown當
singleton或依symbol自建cluster。

### Normal gross turnover 20%

只接受`p4-gross-turnover-v1`。先取得：

- `base_nav`：前一regular session close的single-account FULL+CLEAN reconciled NAV，必須>0且hash閉合；
- `current_nav`：本次Risk review的FULL+CLEAN reconciled NAV；
- same-day fills：只計trusted intent lineage為`NORMAL`的actual absolute fill notional；
- working orders：只計acknowledged NORMAL orders的remaining quantity×worst-case limit/collar price；任何UNKNOWN/
  REVIEW_REQUIRED order或fill lineage使整體NO_TRADE；
- projected position：current reconciled position加acknowledged signed remaining order exposure；同一order不得同時算filled與
  remaining quantity。

對union(current positions, working orders, proposal targets)以stable security ID排序：

```text
consumed_fill = sum(abs(actual_same_day_normal_fill_notional))
consumed_working = sum(abs(remaining_quantity * worst_case_limit_price))
projected_notional_i = reconciled_position_notional_i + signed_working_remaining_notional_i
desired_notional_i = target_weight_i * current_nav
proposed_i = abs(desired_notional_i - projected_notional_i)
turnover = (consumed_fill + consumed_working + sum(proposed_i)) / base_nav
```

Current/reconciled position notional與`proposed_i`使用D當下conservative quote：預計增加用ask，預計降低用bid；方向無法從
bid/ask no-trade interval唯一決定時NO_TRADE。不得用mid、未成交SELL proceeds、買賣互抵或除以2。等於0.20可通過，
超過為`TURNOVER_LIMIT`。Corporate-action與hard-risk exit不進normal cap，但只接受trusted typed lineage且必須數值上不
增加name與total gross；其gross notional仍寫telemetry，caller自由文字／label不能排除。

## 7. Rejection與一次PM重申

區分：

- 可修正portfolio-policy rejection（如short/name/sector/gross/turnover）可產生typed feedback；
- authority、stale、conflict、deadline、reconciliation/control/database failure直接NO_TRADE，不呼叫model。

Attempt1 rejection必須先authoritatively persist，再由現有P3 retry seam使用同一ResearchBundle、同一evidence/universe/
window，刷新完整portfolio/remaining-limits snapshot，只呼叫`PORTFOLIO_MANAGER_RETRY`一次。實作與測試全用scripted
provider；真實P3-E provider未獲授權。

Attempt2必須exact supersede attempt1；不能加入新symbol/evidence/research。第二次仍不合格固定NO_TRADE；不得第三次
proposal。Retry途中snapshot變更時重新全量Risk review，不沿用attempt1局部pass。

## 8. Persistence與transaction

Append-only policy snapshots、risk decisions、target portfolios、decision-target children與lineage。唯一性保護同一
proposal attempt/policy/snapshot只有一個canonical decision；same input same hash可idempotent readback，different hash
conflict fail closed。Target與APPROVED decision同transaction發布；audit失敗不得留下無decision target或self-approved
target。Runtime無UPDATE/DELETE、無直接APPROVED/Target insert bypass；migration up/down與ACL完整。

## 9. 必測矩陣

- 每個limit前/等於/後最小Decimal單位；aggregate vs individual、0/1/15/16 positions；
- short各action/side/weight組合、negative zero、close/reduce semantics；
- cash/gross/name/sector/cluster/turnover/ADV與open-order projected exposure；
- daily-loss 1%、drawdown 8%、paused、UNKNOWN/REVIEW_REQUIRED、reconciliation scopes；
- quote 5秒/30bps、IEX warning、quarantine/stale versions；
- attempt1→feedback→attempt2 exact trace、no analyst/debate rerun、no third attempt；
- authority failure provider call count=0；same/different hash replay；post-construction tamper；
- PG rollback/crash/concurrent decisions/ACL/migration；零quantity/Intent/broker side effect。

## 9A. 固定實作演算法

依序完成，不能換序或短路後遺漏更高優先級authority failure：

1. **D1 Input validator**：驗canonical hashes、versions、as-of/deadline、single account/strategy、FULL+CLEAN、control與
   unresolved state；失敗回`NO_TRADE`，provider/model call count=0。
2. **D2 Proposal normalizer**：只接受P3 `VALID` attempt1/2；展開targets後以stable security ID排序。重複symbol/security、
   contradictory action/weight、negative zero、short語意先typed reject。
3. **D3 Projected baseline**：以authoritative positions＋working-order remainder＋same-day fills計算current/projected
   exposure。不得使用UI、cached proposal snapshot或caller提供的limit totals。
4. **D4 Eligibility/data gates**：逐target重驗C universe、B quarantine、quote/ADV與version lineage；任一authority缺口
   直接`NO_TRADE`，不進retry。
5. **D5 Freeze gates**：daily loss `>=1%`禁止OPEN/INCREASE；drawdown `>=8%`只允許每個name與aggregate exposure
   不增加。action label不能取代數值比較。
6. **D6 Portfolio limits**：由完整proposed portfolio重新計算position count、name、sector、cluster、gross、cash、
   turnover與ADV；每項先算exact observed/limit/remaining，再決定reason。等於上限可通過，超過最小單位拒絕。
7. **D7 Decision**：收集closed ordered reasons/warnings，canonical建`REJECTED/NO_TRADE/APPROVED`。APPROVED只能在全部
   authority及hard limits通過時建立TargetPortfolio。
8. **D8 Retry**：只有allowlisted policy reasons可retry。Attempt1 decision先commit/readback，刷新完整snapshot，呼叫既有
   `PORTFOLIO_MANAGER_RETRY`恰一次，再從D1全量重跑。Attempt2或任何authority reason禁止再呼叫。
9. **D9 Persistence**：policy、decision、children、Target同transaction/CAS；crash/readback/two-reviewer race與runtime
   direct APPROVED/Target insert負測試。
10. **D10 Closure**：pure/import invariants、A-C/P2/P3 regressions、完整驗證與pending-acceptance handoff。

## 9B. Reason分類不可含糊

建立明確`retryable_policy_reasons` allowlist。只允許long-only/weight/portfolio composition可由PM移除或降低的理由，
例如`SHORT_DISABLED, NAME_LIMIT, POSITION_COUNT_LIMIT, GROSS_LIMIT, CASH_BUFFER, SECTOR_LIMIT, CLUSTER_LIMIT,
TURNOVER_LIMIT, ADV_LIMIT`。下列永不retry：reconciliation/control/unresolved state、stale/missing/conflict、quarantine、
identity/universe/version、daily-loss/drawdown freeze、deadline、DB/persistence/integrity failure。

同時出現retryable與non-retryable reason時，整體不得retry。Reason ordering需closed/canonical，不依target或檢查完成順序。

## 9C. 計算證據要求

每個limit decision需保存或可從canonical inputs重算：

```text
metric, numerator, denominator, observed_value, hard_limit, remaining_before, remaining_after,
included_security_ids, source_snapshot_ids, formula_version
```

Sector/cluster/turnover只依6A與C的exact manifest hashes重算；missing mapping不是`OTHER` fallback。Turnover不得除以2、
買賣互抵或使用未成交SELL proceeds。ADV 0.1%使用position expected notional的核准語意；不得在D偷用share rounding。

## 9D. Definition of Done

- 所有limits前/等於/後、aggregate、freeze與short路徑有獨立Decimal vectors；
- authority failure model/provider calls=0；retry allowlist、一次attempt2與no-third-attempt有trace/DB證據；
- same inputs/permutations byte-identical，不同snapshot不能重用decision；
- Target只含long weights與完整lineage，無quantity/order/price/broker fields；
- decision+Target publication原子、runtime無self-approve/bypass、two-reviewer只有一head；
- A-C/P2/P3 regression與PG16/static checks全綠，requirement ledger無UNKNOWN。

ADR-039任何manifest、公式、taxonomy、threshold或hash drift只能`partial/blocked`，不得自行兼容。

## 10. 驗證與交付

最低命令集：

```bash
uv run --locked pytest tests/test_p4d_*.py tests/test_p3d_proposal_contracts.py tests/test_reconciliation_and_ledger.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

另保存每個limit的independent Decimal vectors、retry provider call counts及two-reviewer PG PoC。

跑focused、P3 proposal、A～C、P2 reconciliation/control nearby、完整non-integration、PG16 zero-skip、migration/ACL/
concurrency、Ruff、format、mypy、diff check。文件只標implementation completed/pending acceptance；R-29/R-30不可在
獨立驗收前關閉。回報exact revision、files、commands/results與blockers後停止。

最終報告固定包含status、exact revision/dirty set、A-C acceptance lineage、可動/越界檔案、每個limit formula與boundary
vectors、retry trace/provider call counts、Target atomicity、PG16/ACL/concurrency、原始commands/counts/skips及blockers。
成功後唯一下一步是fresh P4-D acceptance；不得開始E。
