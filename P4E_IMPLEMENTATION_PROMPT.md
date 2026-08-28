# P4-E Implementation Prompt — Whole-share Quantity／Zero-submit IntentPlan／Corporate-action Exit Plan

完整交給P4-E實作模型。本文件只授權P4-E；完成後停止，不得開始P4-F。

## 0A. 弱模型硬性執行協議

1. A～D必須fresh Accepted；若任何Accepted revision後相關source改變，停止並要求重驗，不得在E修上游。
2. 本Gate只建立不可執行的plan。任何class/table/status被P2 worker視為OrderIntent即越界，即使沒有真的submit。
3. 所有公式先以獨立Decimal test vectors固定；不得使用float、`round()`、banker's rounding或abs後失去side。
4. 每次planning都重讀並驗FULL+CLEAN、control、open orders、same-day fills、B quarantine、D policy/decision與fresh quote；
   不得相信舊`APPROVED` bool。
5. 任何不確定預設`BLOCKED/REVIEW_REQUIRED/NO_ACTION`且published approved-like rows=0；不得fallback到UI/current price。
6. 不得替P7建立submit adapter、outbox transition、order payload、broker client或「暫時不呼叫」的可達callback。

## 0B. 可動與禁動範圍

| 類別 | 可動 | 限制 |
|---|---|---|
| Pure | `portfolio/quantity.py`、`portfolio/intent_plans.py` | whole-share translation/no-submit contract only |
| Application | `application/ports/intent_plans.py`、`application/intent_planning_service.py` | 無broker/execution port |
| Persistence | `infrastructure/postgres_intent_plans.py`、next migration | 與P2 tables/worker namespace物理隔離 |
| Tests | `test_p4e_*`、PG integration、P2 worker/source invariants | 不放寬P2 execution安全測試 |
| Read-only reuse | D Target/Risk、C quote、B event、P2 reconciliation/control public snapshots | 不修改其contract/state |
| 禁止 | P2 `order_intents`/outbox/orders、broker ports、P3 model、live HTTP | 不新增任何bridge/stub |

## 0. 任務

把Accepted的P4-D TargetPortfolio轉為保守整股delta與immutable `APPROVED_NO_SUBMIT` IntentPlan；並為P4-B已
confirmed的既有long forward/reverse split建立獨立zero-submit `CORPORATE_ACTION_EXIT` plan。P4-E不得把plan轉成
P2 OrderIntent/outbox，不得呼叫broker submit/cancel或model。

成功語句：`P4-E implementation completed; pending independent acceptance`。

## 1. 前置與必讀

保存exact git/revision/dirty/untracked/diff/migration狀態。P4-A～D都需fresh Accepted。完整閱讀P4治理、A～E prompts、
D Target/Risk contracts、C market snapshots、B corporate-action/quarantine、P2 OrderIntent/client_order_id/outbox/
reconciliation/control/ledger、flatten/RISK_EXIT安全路徑與全部相關tests/migrations。

先映射現有P2 `OrderIntent` constructor/state/repository；P4-E建立不同authority，不能藉重用class使CREATED intent被
P2 worker拾取。

## 2. 禁止事項

不stage/commit/push；不讀credential、不連外、不呼叫model/broker；不寫P2 `order_intents`/outbox/broker orders；
不注入submit/cancel port；不啟用short/BUY-to-cover/fractional/market order；不修改D RiskDecision；不使用current
broker UI quantity取代FULL+CLEAN authority；不將IEX說成NBBO。

任何quantity/identity/open-order不確定、working orders未解析、reconciliation非FULL+CLEAN、quote不合格、deadline
已過都不建plan。

## 3. Owned contracts與隔離

優先新增／最小修改：

```text
portfolio/quantity.py
portfolio/intent_plans.py
application/ports/intent_plans.py
application/intent_planning_service.py
infrastructure/postgres_intent_plans.py
```

`IntentPlan`使用獨立table/state/port，不能是P2 executable `OrderIntent`。至少包含plan/idempotency identity、type
`REBALANCE|CORPORATE_ACTION_EXIT`、symbol/security ID、BUY/SELL、whole quantity、reference/limit/collar、earliest/
cancel time、Target/Risk/policy/portfolio/market/security/reconciliation lineage、status與hash。

唯一可發布狀態：`APPROVED_NO_SUBMIT|NO_ACTION|BLOCKED|REVIEW_REQUIRED`；沒有OUTBOX/SUBMITTING轉移。

## 4. Quantity translation

輸入必須是同一decision時點最新FULL+CLEAN reconciled NAV/cash/positions/open orders/same-day fills，加D
TargetPortfolio與C fresh quote。使用exact Decimal：

```text
desired_value = min(target_weight * reconciled_nav, 0.05 * reconciled_nav)
reference_price = ask for BUY, bid for SELL
desired_shares_exact = desired_value / reference_price
projected_shares = position + acknowledged/open-order signed remainder
delta_exact = desired_shares_exact - projected_shares
delta = truncate_toward_zero(delta_exact)
```

先從delta方向選bid/ask後重新閉合；不得用mid降低保守成本。BUY需驗cash buffer、gross與ADV；SELL不得超過projected
long且不得變short。每次translation重新驗D policy與B quarantine，不信舊approved flag。

若`abs(delta)*reference_price < max(USD100,NAV*0.25%)`或target drift<NAV0.5%，輸出NO_ACTION。Quantity=0不建
executable-like plan。Price collar固定25bps並以exact cents向保守方向round；quote age≤5秒、spread≤30bps。

所有symbols整體計算，先處理風險降低SELL再評估BUY cash，但output order canonical；不能因iteration order改cash結果。

## 5. Idempotency與時間窗

Plan identity由strategy/trading date/window/TargetPortfolio version/security/side/type及domain tag deterministic產生；
不能與P2 `slv1` client_order_id命名空間碰撞。Same inputs same bytes/hash；different snapshot/version必須new plan或conflict，
不能覆寫。

Only regular-hours planned windows；earliest<cancel≤proposal/deadline/cutoff。Half-day由market calendar相對計算。P4-E只
規劃，不保證P7 submit；IEX limited warning必須保存。

## 6. Corporate-action exit planning

只支援已持有long的confirmed forward/reverse split，且：

- exact event/security identity、ratio、ex/effective date與source lineage重驗；
- 距ex-date至少一個完整交易日；未生效、未halt、tradable；
- 目前無該symbol working orders；P4-E不得cancel；
- latest FULL+CLEAN reconciliation證明broker/local projected whole quantity一致；
- quantity直接等於reconciled long position，side=SELL，type=`CORPORATE_ACTION_EXIT`；不經LLM、不冒充一般Risk；
- quote/collar/regular-hours/deadline/idempotency全部通過。

Late/effective、halt、working orders、identity/quantity drift、source withdrawal/conflict、fractional residual或short position
固定REVIEW_REQUIRED並保留entry block；不得使用舊quantity。P4-E不標EXITED、不計realized P&L、不寫memory；這些需
future fills＋FULL reconciliation。

## 7. Persistence與zero-capability proof

Append-only plan parent/children/state events，publication單transaction。Runtime role可建立validated no-submit plan，但
不能直接改APPROVED、轉outbox、insert P2 order_intents或執行broker functions。Composition constructor不得接受
`PaperBrokerPort`、cancel/submit callable或execution repository。

建立永久source-invariant／spy tests：所有P4-E normal/corporate paths broker submit=0、cancel=0、P2 outbox writes=0、
model calls=0。DB trigger/ACL需防止將plan table視為P2 work queue。

## 8. 必測矩陣

- rounding：正負delta、0、exact integer、±fraction、high-price、minimum USD/NAV threshold、0.5% band；
- prices：bid/ask方向、cent rounding、5秒/30bps/25bps邊界、missing/IEX warning；
- projected position：open BUY/SELL、partial fill、same-day fill、UNKNOWN/REVIEW_REQUIRED、cash/gross recheck；
- ordering：多symbol SELL/BUY、ties/permutation，cash結果與bytes deterministic；
- idempotency：same/different target/snapshot/window/type，namespace與P2不碰撞；
- corporate action：forward/reverse、one full day boundary、late/effective/halt/working order、identity/quantity conflict、
  withdrawal、fractional、short；
- crash/rollback/race/ACL/migration；所有外部/P2/model call count=0。

## 8A. 精確quantity／price實作演算法

每個normal target依stable security ID處理，先建立全portfolio immutable input snapshot，再依下列步驟：

1. 驗證`reconciled_nav>0`、cash/positions/open-order remainder閉合，position與remainder使用exact shares。Normal rebalance
   出現fractional current/projected shares固定`REVIEW_REQUIRED`；不得round掉。
2. `desired_value=min(target_weight*nav, name_cap*nav)`；target weight需`0<=w<=0.05`且來自同一D Target hash。
3. 對long projected whole shares建立no-trade interval：`lower=projected_shares*bid`、`upper=projected_shares*ask`。
   若`desired_value`落在`[lower, upper]`，方向不確定，輸出`NO_ACTION`，不得用mid選side。
4. 若`desired_value>upper`，side=BUY、reference=ask；若`desired_value<lower`，side=SELL、reference=bid。
5. `desired_shares_exact=desired_value/reference`；`delta_exact=desired_shares_exact-projected_shares`；
   `delta=truncate_toward_zero(delta_exact)`。assert BUY時`delta>0`，SELL時`delta<0`；不一致即BLOCKED。
6. `quantity=abs(delta)`且必須positive int。SELL後`projected_shares-quantity>=0`，否則拒絕，不能成short。
7. `drift_value=abs(desired_value-projected_shares*reference)`。若`drift_value < nav*0.005`或
   `quantity*reference < max(100,nav*0.0025)`，輸出NO_ACTION；等於門檻可繼續。
8. Collar：BUY raw limit=`ask*(1+0.0025)`並向上取到USD0.01；SELL raw limit=`bid*(1-0.0025)`並向下取到USD0.01。
   BUY cash/gross與minimum checks需用最壞limit notional重新驗；SELL proceeds不得用於同批BUY直到該排序政策明確保存。
9. 全symbols先計算risk-reducing SELL plans，再以canonical security ID逐一分配BUY cash budget；輸出仍canonical排序，
   每個BUY保存cash-before/after。若SELL只是假設尚未成交，預設不可把其proceeds當可用cash。
10. publication前重讀control/reconciliation/quarantine/quote/Target versions；任一漂移整個plan set fail closed，不部分發布。

若現行核准policy對「SELL預期收入能否支應同批BUY」另有明文，以該Accepted policy為準；沒有時固定**不能**使用預期收入。

## 8B. Corporate-action演算法

Corporate exit與normal rebalance完全不同namespace/service branch：

1. 只接受B `CONFIRMED` forward/reverse split及stable security/event identity；再次查詢若不是CONFIRMED即REVIEW_REQUIRED。
2. 使用calendar計算從decision time到ex-date前是否至少剩一個**完整 regular session**；half-day不算完整session。
3. 要求目前long position為positive whole shares、working/unknown/review orders=0、FULL+CLEAN、tradable/not halted、
   identity與symbol-as-of一致、event未effective/withdrawn/conflict。
4. quantity恰等於reconciled whole long shares；side=SELL；不得套D target weight、不得呼叫PM/Risk retry。
5. 使用fresh bid、spread/collar/deadline/regular-hours；保存event/identity/reconciliation/market lineage。
6. 只發布`CORPORATE_ACTION_EXIT + APPROVED_NO_SUBMIT`。不cancel、不mark EXITED、不計P&L、不寫fills/memory。

任一條失敗輸出typed REVIEW_REQUIRED並保持entry block，approved no-submit child rows=0。

## 8C. Persistence與capability檢查順序

先以source scan/import test證明沒有broker/P2 writer，再建table。Table/port不得共享P2 enum、client order ID prefix、worker
query或state names。DB需以schema/FK/CHECK/ACL保證quantity positive integer、closed status/type、immutable lineage、
single canonical plan set；runtime不能自行從BLOCKED轉APPROVED或寫P2 tables/functions。

以P2 worker的實際selection query對P4-E rows做負測試，observed selected count必須0；只assert不同table name不夠。

## 8D. Definition of Done

- normal formula全部正負／整數／fraction／threshold／cash-order cases有獨立vectors；
- BUY/SELL reference與cent rounding方向正確，cash以worst-case BUY collar重驗；
- open-order remainder、partial fill與same-day fills納入projected shares；unknown state零approved plan；
- corporate exit所有前置與late/halt/fractional/conflict cases封閉；
- normal/corporate所有路徑broker submit=0、cancel=0、P2 writes/worker pickups=0、model=0；
- plan set原子、競態單head、corrupt resume fail closed、runtime ACL有效；
- A-D/P2 regression、PG16/static checks全綠且requirement ledger無UNKNOWN。

## 9. 驗證與交付

最低命令集：

```bash
uv run --locked pytest tests/test_p4e_*.py tests/test_execution_orders.py tests/test_execution_engine.py tests/test_reconciliation_and_ledger.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

另以實際P2 worker selection query驗P4 rows selected=0，並保存broker/cancel/P2/model spy counts。

跑focused、A～D、P2 execution/reconciliation/control nearby、完整non-integration、PG16 zero-skip、migration/ACL/
concurrency、Ruff、format、mypy、diff check。只標implementation completed/pending acceptance；不能關OPEN-037/038，
不能開始F。回報exact revision、files、formulas、commands/results與blockers。

最終報告固定包含status、revision/dirty set、A-D acceptance lineage、scope files、normal Decimal vectors、cash allocation
ledger、corporate-action matrix、P2 worker selection count、broker/cancel/P2/model call counts、PG16/ACL/concurrency、完整
commands/results/skips與blockers。成功後唯一下一步是fresh P4-E acceptance。
