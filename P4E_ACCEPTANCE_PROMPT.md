# P4-E Independent Acceptance Prompt

完整交給未參與實作的新模型。只驗收P4-E；read-only、不修code、不開始P4-F。

## 0A. 審查協議與判決規則

不得修改檔案、連外、讀secret、呼叫model/broker或讓P2 worker執行。先讀source與P2實際worker query，再用獨立Decimal
oracle重算，最後跑tests/PG。Production helper不能當驗收oracle。

- P4 row可被P2拾取、可達broker/cancel、SELL成short、錯誤corporate exit：High→Rejected。
- rounding/cash/open-order/idempotency/atomicity/ACL fail-open：Medium→Rejected。
- 缺A-D fresh Accepted、FULL+CLEAN PG evidence或公式語意未封閉：Not Accepted。
- Low只限不影響authority的維護性問題；不得conditional pass。

## 0. 判定與前置

只允許Accepted／Rejected／Not Accepted — prerequisite/evidence pending。A～D需fresh Accepted。保存exact revision、
dirty/untracked、diff/check、migration；不讀credential、不連外、不呼叫model/broker。

## 1. Capability隔離

從imports、constructors、repositories、DB grants與worker queries證明IntentPlan不是P2 OrderIntent/outbox work item；E無
PaperBrokerPort、submit/cancel、execution repository或model capability。對normal/corporate paths使用spies assert broker
submit=0、cancel=0、P2 writes=0、model=0。任何可被現有worker拾取的row是High。

## 2. Quantity公式與rounding PoC

獨立用Decimal重算合法cases，逐欄mutationTarget/Risk/policy/NAV/cash/position/open orders/quote/ADV/lineage。測：

- exact integer與±fraction的truncate-toward-zero；不得round away from zero；
- BUY用ask、SELL用bid，cent/collar向保守方向；不得用mid掩蓋成本；
- projected shares納入open-order remainder/partial fills，SELL不越過long成short；
- minimum `max(100,NAV*0.25%)`與0.5% band前/等於/後；
- 多symbol permutation下先降風險、再買入的cash/gross結果與canonical bytes不變；
- D policy、FULL+CLEAN、pause/unresolved intents、quarantine、quote/ADV在planning當下重驗。

Quantity=0、missing/float/NaN/negative/stale/conflict必須NO_ACTION/BLOCKED且零published approved plan。

## 3. Price、time與idempotency

測5秒quote、30bps spread、25bps collar、regular-hours/half-day、earliest/cancel/deadline的±最小單位。IEX warning
必須保存但不能冒充P7 readiness。Same exact inputs bytes/hash相同；不同snapshot/target/event/type不能重用identity；
P4 plan namespace不能碰撞P2 client_order_id。

## 4. Corporate-action adversarial suite

建立forward/reverse confirmed long以及Alpaca-only、late/effective、halt、working orders、partial/unknown order、identity/
quantity mismatch、withdrawn/conflicting source、fractional residual、short position。證明：

- 只有正式confirmed、至少一完整交易日前、zero working orders、FULL+CLEAN、tradable/fresh quote才no-submit plan；
- quantity是reconciled whole long，不是舊DB/UI/intent quantity；
- P4-E不cancel、不submit、不標EXITED、不計P&L、不寫memory；
- 所有不確定進REVIEW_REQUIRED並保留entry block。

## 5. PostgreSQL與failure injection

真實PG16驗證append-only、parent/children原子publication、same/different hash、two planner race、crash每點、rollback、
runtime direct APPROVED/update/delete與P2 insert/bypass拒絕、migration up/down/up。Corrupt persisted lineage/status resume
必須fail closed。

## 5A. 強制獨立PoC流程

1. 列diff/import/constructor graph，反向搜尋PaperBrokerPort、submit、cancel、outbox、order_intents、worker selectors與
   dynamic callbacks；從composition public入口證明不可達。
2. 建立獨立Decimal oracle，逐步輸出desired value、no-trade interval、side、reference、exact delta、truncated delta、
   thresholds、collar、cash before/after；與production result逐欄比較。
3. 測current/open-order/same-day-fill組合與fractional/UNKNOWN states；每case記approved rows及P2 selected rows。
4. 對多symbol inputs做至少10種permutation，驗SELL-before-BUY policy、cash ledger、output bytes/hash不變且未使用未成交
   SELL proceeds。
5. 建corporate forward/reverse全矩陣，從B query到E publication走public seam；逐項記review reason與call/write counts。
6. 真實PG16測partial publication crash、two planners、same/different hash、corrupt resume、runtime direct state/P2 writes、
   P2 worker actual query selection=0與migration up/down/up。

## 5B. Mandatory matrix

```text
Requirement | Source file:line | Independent calculation/PoC | Test | PG evidence | Verdict
Physical/logical isolation from P2 executable intents
No-trade bid/ask interval and side selection
Truncate toward zero and no short crossing
Minimum adjustment and 0.5% drift boundaries
5s/30bps/25bps and conservative cent rounding
Projected shares from orders/fills and unknown-state closure
SELL-before-BUY deterministic cash allocation
Normal plan idempotency and atomic publication
Corporate confirmation/full-session/whole-position closure
Corporate REVIEW_REQUIRED cases retain entry block
Runtime ACL and P2 worker selected count=0
Broker submit/cancel/P2 writes/model calls all zero
```

## 6. Full regression與報告

最低read-only命令集：

```bash
uv run --locked pytest tests/test_p4e_*.py tests/test_execution_orders.py tests/test_execution_engine.py tests/test_reconciliation_and_ledger.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

另執行獨立Decimal oracle、P2 worker actual query與corporate-action public-entry PoCs。

跑focused、A～D、P2 safety、完整non-integration、完整PG16 zero-skip、Ruff、format、mypy、diff check。任何rounding/
cash/order projection錯誤、broker/P2 capability、corporate unsafe plan或High/Medium finding阻擋Accepted。

```text
P4-E VERDICT: Accepted | Rejected | Not Accepted — prerequisite/evidence pending
REVISION: <exact revision>
```

列findings、capability proof、formula vectors、price/time/idempotency、corporate cases、PG/ACL與full regression。Accepted後
只建議另開P4-F；不修改狀態或開始下一Gate。

每個finding列severity、requirement、file:line、public-entry input、independent expected arithmetic、observed plan/rows/calls、
impact與限定修復範圍。報告另列reviewed files、完整matrix、Decimal oracle、P2 worker query/result、PG server/version、
原始commands/counts/skips與unverified claims。Matrix任一PENDING/FAIL或任一High/Medium皆不得Accepted。
