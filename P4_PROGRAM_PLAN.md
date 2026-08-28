# P4 Program Plan

最後更新：2026-08-28
狀態：**A～F工作包、12份詳細prompts與全部P4設定complete；P4-A／P4-B已完成fresh independent acceptance，均Accepted／Closed；P4-C～F未開始**
範圍：P4 multi-source／candidate／deterministic Risk，Paper-only、zero-submit

## Current Gate Closure

- **P4-A：Accepted／Closed。** focused P4-A＋secret／Paper-only invariants `372 passed`。
- **P4-B：Accepted／Closed。** focused P4-B＋Paper-only invariants `132 passed`；fresh PostgreSQL 16 integration
  `256 passed, 2 deselected, 0 skipped`。
- 修復後的公開入口／對抗重驗通過；沒有Keychain、provider、model或broker呼叫。P4-C～F尚未開始，P4 overall
  仍為`In progress`，不得把A／B closure延伸為完整P4 Final Gate。

## 1. 使用者已核准設定

P4 第一版固定下列 profile；任何放寬都必須建立新 ADR、重新跑 P4 驗收，不能只改環境變數：

- 單一 Alpaca Paper 帳戶、單一策略 `seven_lens_long`。
- `short_enabled=false`；P3 schema 可解析 short proposal，但 P4 必須以 typed `SHORT_DISABLED`
  拒絕，不能產生 short target、SELL-to-open 或 BUY-to-cover intent。
- long gross 上限 90%；short gross 0%；total gross 90%；不設最低曝險，沒有合格機會時持有現金。
- 最低現金 10%；最多 15 個 long positions；單一標的上限 NAV 5%。
- SEC SIC Division sector 上限25%；高度相關cluster上限30%；正常每日gross turnover上限NAV 20%。
- 單筆預期部位不超過 20 日 ADV 的 0.1%。
- Paper NAV 較前一收盤下跌 1% 時停止新增曝險；高水位回撤 8% 時凍結組合，只允許降風險。
- 只用整股；delta quantity 一律向零取整。
- 低於 `max(USD 100, NAV * 0.25%)` 的調整不建立 intent；target drift 小於 NAV 0.5% 不再平衡。
- quote age 上限 5 秒、spread 上限 30 bps、初始 price collar 25 bps；任一證據缺失／衝突即
  `NO_TRADE`。這些是 P4 保守初值，只能由 P5 walk-forward＋ADR 校準。
- 全部資料來源零付費；免費但需註冊的 key 仍使用 typed `SecretRef`。任何申請、Keychain 寫入或真實
  API 呼叫都需要當次明確授權。
- forward/reverse split 採 ADR-037：Alpaca／discovery 可先 block，正式來源確認才可建立 long-only、
  no-submit `CORPORATE_ACTION_EXIT`。距 ex-date 少於一個完整交易日、已生效、停牌或 quantity／identity
  不閉合時固定 `REVIEW_REQUIRED`。

### 1.1 已核准的四個immutable manifests（ADR-039）

四個manifest固定為`p4-factor-v1`、`sec-sic-division-v1`、`p4-correlation-cluster-v1`與
`p4-gross-turnover-v1`。實作需從canonical wire產生domain-separated hash並以golden test固定；runtime不得override。

#### Factor V1

- Universe第一版只接受ordinary common stock；ETF與其他非普通股全部排除。
- Price inputs截至cutoff前一完整regular session；使用point-in-time、split-aware closes。`R(a,b)=P[b]/P[a]-1`。
- Trend：`T1=R(t-126,t-21)`、`T2=R(t-252,t-21)`，category=`mean(percentile(T1),percentile(T2))`。
- Quality：以cutoff前可見、四個不重疊fiscal quarters組成TTM；`avg_assets=(assets_start+assets_end)/2`，
  `ROA=TTM_net_income/avg_assets`、`CFOA=TTM_CFO/avg_assets`、
  `ACCRUAL=(TTM_CFO-TTM_net_income)/avg_assets`，category為三者percentile平均。
- Value：`market_cap=point_in_time_shares_outstanding*previous_session_close`；
  `EY=TTM_net_income/market_cap`、`FCFY=(TTM_CFO-TTM_capex_positive_outflow)/market_cap`，category為兩者平均。
- Low Risk：63個simple daily returns的population standard deviation乘`sqrt(252)`；252-session peak-to-trough maximum
  drawdown。使用`-volatility`與`-max_drawdown`的percentile平均，確保高分代表較低風險。
- Composite=`0.35*Trend+0.25*Quality+0.15*Value+0.25*LowRisk`。Event不計directional score，只作B quarantine與
  typed evidence completeness/freshness/conflict hard gate。
- 所有9個subfactors都mandatory。Negative earnings/FCF是合法數值；missing、conflict、future、non-finite、
  `avg_assets<=0`或`market_cap<=0`使該security不進quant set。
- 每個raw subfactor在完整factor-eligible cross-section以ascending nearest-rank
  `q(p)=x[ceil(p*N)-1]`求5%/95%並clamp。Clamped ties使用midrank percentile；N=1固定0.5。排序固定composite、
  Trend、Quality、Value、LowRisk降序，最後stable security ID升序。

SEC normalized facts只能使用explicit concept allowlist及一致fiscal context：`NetIncomeLoss`、
`NetCashProvidedByUsedInOperatingActivities`、`Assets`、`PaymentsToAcquirePropertyPlantAndEquipment`、
`EntityCommonStockSharesOutstanding`。YTD facts只有能以同fiscal-year/context無歧義相減成quarter時才可用；extension、
duplicate/conflicting units/contexts或無法閉合的TTM固定missing，不以近似tag或today value補值。

#### SEC SIC Division V1

使用cutoff前最新且無衝突的EDGAR filer SIC，先zero-pad為4 digits並依前2 digits映射：01–09=A、10–14=B、15–17=C、
20–39=D、40–49=E、50–51=F、52–59=G、60–67=H、70–89=I、91–97=J。未分配gap、98、99、missing或conflict為
`SECTOR_UNKNOWN`；不得加入新增曝險候選。Sector exposure 25%以此division計，不再稱GICS。

#### Correlation Cluster V1

每日對`quant top100 ∪ current long holdings`建立snapshot。使用截至前一完整regular session的最近126個split-aware
simple close returns；每security與每pair至少100個共同finite observations。Pearson correlation `rho>=0.75`建立無向
edge，按stable security ID排序後取connected components；無edge且資料完整者為singleton。資料／pair coverage不足、
zero variance或non-finite者為`CLUSTER_UNKNOWN`，只能HOLD/REDUCE/CLOSE、不能OPEN/INCREASE。Cluster ID由policy hash、
as-of及ordered members作domain-separated hash；30% cap不變。

#### Gross Turnover V1

```text
base_nav = previous regular-session close FULL+CLEAN reconciled NAV
consumed = sum(abs(same-day NORMAL fill notional))
         + sum(abs(acknowledged NORMAL working-order remaining quantity * worst-case limit price))
proposed = sum(abs(target_weight * current_reconciled_nav
                   - projected_position_notional_after_working_orders))
turnover = (consumed + proposed) / base_nav
```

`projected_position_notional_after_working_orders`使用current reconciled positions、acknowledged remaining orders及D當下
conservative quote；不得以未成交SELL proceeds抵減BUY或turnover。等於0.20可通過，超過即`TURNOVER_LIMIT`。Base NAV
missing/nonpositive、fill/order UNKNOWN/REVIEW_REQUIRED或notional/lineage conflict固定`NO_TRADE`。Corporate-action／
hard-risk exit走獨立typed path，不受normal 20% cap但仍記入gross turnover telemetry；只有數值上不增加name與aggregate
exposure才可分類risk-reducing，不能信caller label。

## 2. P4 交付邊界

### 2.1 P4 必須交付

1. 封閉 source-role registry、exact-host GET-only adapters、rights/rate-limit/schema-drift/failure policy。
2. Point-in-time security master、symbol/CIK/CUSIP/ISIN lineage與forward/reverse split quarantine。
3. 版本化 production universe、流動性／資產型別硬篩與 deterministic candidate funnel。
4. Immutable `RiskPolicySnapshot`、`RiskDecision`、`TargetPortfolio` 與最多一次 PM 重申的整合。
5. Reconciled NAV/position/open-order基礎上的整股 target-to-quantity translation。
6. 一般 rebalance 與 `CORPORATE_ACTION_EXIT` 的 zero-submit intent planning；不得呼叫 broker submit。
7. PostgreSQL authority、migrations、runtime ACL、audit、observability、operator-readable rejection evidence。

### 2.2 P4 明確不做

- 不做真實 Alpaca Paper submit、WebSocket execution、P7 operator CLI 或 unattended trading。
- 不啟用 short、margin、locate、BUY-to-cover、fractional shares、options、crypto、OTC 或 live endpoint。
- 不做 P5 walk-forward／profitability主張、P6 Shadow或任何實盤準備聲明。
- 不新增付費資料；不把 IEX 當成完整 NBBO/SIP。
- 不讓 LLM、source adapter、environment variable 或 outage fallback 修改 Risk policy。

## 3. 零付費來源設定

| 類別 | P4來源與角色 | P4用途 | Fail-closed邊界 |
|---|---|---|---|
| 歷史行情／ADV | Alpaca historical delayed SIP（可用時）`AUTHORITY` | 日線、20日ADV、流動性篩選 | feed/entitlement必須記錄；不可退回yfinance後升權 |
| 最新行情 | Alpaca IEX `AUTHORITY`（P4 zero-submit範圍） | P4 quote、spread、quantity planning | snapshot另標`LIMITED_MARKET_COVERAGE`；不能推導P7完整市場authority |
| Asset/tradability | Alpaca Assets `AUTHORITY` | active/tradable/exchange/asset class | 缺值、schema drift或halt即block |
| 公司基本面 | SEC EDGAR submissions/filings/XBRL `AUTHORITY` | filing、company facts、CIK identity | exact host、可識別User-Agent、全域≤5 req/s |
| 公司補充 | issuer IR `CONFIRMATION` | press release、拆合股公告 | 不可取代SEC/交易所identity closure |
| 宏觀vintage | FRED/ALFRED `AUTHORITY` | as-of macro observations/revisions | 顯式real-time period；不得使用今日revision回填歷史 |
| 官方宏觀 | Treasury/BLS/BEA/EIA `AUTHORITY|CONFIRMATION` | 利率、CPI、GDP、就業、能源 | 各family獨立schema/time/rights gate |
| 公司行動發現 | Alpaca Corporate Actions `CONFIRMATION` | forward/reverse split detection | 官方承認可能延遲；不能單獨auto-exit |
| 公司行動確認 | SEC／issuer IR／listing exchange `AUTHORITY` | identity、ratio、ex/effective date closure | 至少一個正式公告、全部已讀來源無衝突 |
| 新聞發現 | Tavily／GDELT `DISCOVERY` | 找到原始publisher | snippet／event score不是material truth |
| 研究補充 | yfinance `RESEARCH_SUPPLEMENT` | 顯示／異常比對 | 永不填補價格、identity或corporate-action authority缺口 |

來源上線前各自提交 SourceManifest：host/path、HTTP method、redirect、auth/secret、request/response byte
budget、pagination、rate limit、rights、timestamps、schema version、fixtures、failure matrix與保存政策。

## 4. 模組與依賴方向

```text
src/seven_lens/
  config/p4.py                 immutable P4 profile and source policies
  sources/roles.py             closed source roles and registry
  sources/adapters/            exact-host GET-only source-family adapters
  market_data/snapshots.py     quote/bar/ADV snapshots and quality flags
  securities/contracts.py      stable identity and symbol lineage
  securities/corporate_actions.py
  universe/contracts.py        versioned universe snapshot
  universe/builder.py          hard eligibility filters
  screening/contracts.py       deterministic candidate set
  screening/funnel.py          quant/evidence ranking without model authority
  portfolio/contracts.py       RiskPolicySnapshot/TargetPortfolio
  risk/contracts.py            RiskDecision/reason codes
  risk/engine.py               pure deterministic approval
  portfolio/quantity.py        target-to-whole-share delta translation
  application/ports/           source/security/universe/risk repositories
  application/p4_composition.py
  infrastructure/postgres_*.py adapters and runtime authority
```

依賴固定為 `domain contracts -> application ports/services -> infrastructure adapters`。Risk Engine是純函式核心，
不得import HTTP、Alpaca、Keychain、provider/model或PostgreSQL driver。Adapter只產生typed snapshots，不能核准
target。P4只持久化`APPROVED_NO_SUBMIT` intent plan，不呼叫P2 execution；未來Gate若要轉換，必須以exact
plan lineage建立新`OrderIntent`並重新驗證。P4 composition不得注入broker submit/cancel capability。

## 5. 核心資料契約

所有契約使用exact enum、fixed-scale Decimal、canonical JSON、UTC、schema/producer version、content hash與
resource bounds。未知欄位與非canonical數值一律拒絕。

### 5.1 `SecurityIdentityRecord`

```text
security_id, symbol, exchange, asset_class, cik?, cusip?, isin?
valid_from, valid_to?, available_at, source_record_ids[]
status: ACTIVE|INACTIVE|HALTED|REVIEW_REQUIRED
identity_hash
```

### 5.2 `MarketSnapshot`

```text
security_id, symbol, feed, as_of, received_at
bid, ask, midpoint, spread_bps, last?, bar_refs[], adv20_usd?
coverage: COMPLETE|LIMITED_MARKET_COVERAGE
freshness: FRESH|STALE|MISSING|CONFLICT
snapshot_hash
```

### 5.3 `RiskPolicySnapshot`

包含第1節全部核准設定、effective trading date、policy version與hash。每次Risk review綁定exact policy hash；
runtime不能以環境變數覆寫。

### 5.4 `RiskDecision`

```text
decision_id, proposal_id, proposal_attempt, policy_hash
portfolio_snapshot_hash, market_snapshot_ids[], security_master_version
status: APPROVED|REJECTED|NO_TRADE
accepted_targets[], rejected_targets[]
reason_codes[], warnings[], remaining_limits, reviewed_at, decision_hash
```

Reason code使用封閉enum，至少涵蓋：`SHORT_DISABLED`、`NAME_LIMIT`、`GROSS_LIMIT`、`CASH_BUFFER`、
`SECTOR_LIMIT`、`CLUSTER_LIMIT`、`TURNOVER_LIMIT`、`ADV_LIMIT`、`DAILY_LOSS_STOP`、`DRAWDOWN_FREEZE`、
`STALE_QUOTE`、`WIDE_SPREAD`、`DATA_CONFLICT`、`CORPORATE_ACTION_BLOCK`、
`RECONCILIATION_NOT_CLEAN`、`UNRESOLVED_INTENT`與`DEADLINE_EXPIRED`。

`LIMITED_MARKET_COVERAGE`是P4 zero-submit decision的mandatory warning與P7 readiness blocker，不是P4
自動拒絕理由；IEX quote本身缺失、過期、spread超限或衝突仍按上述reason code拒絕。

### 5.5 `TargetPortfolio`／`IntentPlan`

`TargetPortfolio`只保存核准後long target weights及Risk lineage。Quantity translator使用最新
FULL+CLEAN portfolio snapshot重新計算：

```text
desired_value = min(target_weight * reconciled_nav, name_cap_value)
desired_shares_exact = desired_value / conservative_reference_price
delta_shares_exact = desired_shares_exact - projected_position_after_open_orders
delta_shares = truncate_toward_zero(delta_shares_exact)
```

BUY與SELL都向零縮小，不能因rounding增加曝險。低於minimum adjustment或rebalance band即`NO_ACTION`。
`IntentPlan`包含quantity、limit/collar、earliest/cancel time、target/policy/snapshot lineage及
`APPROVED_NO_SUBMIT`狀態；本階段沒有送單轉移。

## 6. 決策順序與失敗語意

固定順序：

1. 取得FULL+CLEAN reconciliation與single-account authoritative snapshot。
2. 驗證control state、未解`UNKNOWN/REVIEW_REQUIRED`、daily-loss/drawdown freeze。
3. 驗證security identity、asset/tradability、corporate-action quarantine。
4. 驗證quote freshness/spread/coverage、ADV、source conflict與deadline。
5. 套用long-only、cash/name/sector/cluster/gross/turnover/ADV hard limits。
6. 第一次proposal若只有可修正portfolio-policy拒絕，回傳typed feedback並允許一次既有P3 PM重申。
7. Authority、freshness、reconciliation、deadline或資料完整性失敗直接`NO_TRADE`，不呼叫模型重申。
8. attempt 2仍不合格固定`NO_TRADE`；通過才凍結TargetPortfolio並產生zero-submit IntentPlan。

同一proposal/policy/snapshot重播必須byte-identical；不同snapshot不得沿用舊RiskDecision。任何持久化失敗不得
留下可被P2視為已核准的半成品。

## 7. Corporate-action 路徑

```text
DETECTED -> ENTRY_BLOCKED -> CONFIRMED -> EXIT_PLANNED_NO_SUBMIT
                              |              |
                              +-> REVIEW_REQUIRED <- late/conflict/halt/identity drift
```

- 只有forward/reverse split；其他event type固定unsupported並轉人工review。
- 自動`CONFIRMED`需要identity、ratio、ex/effective date、point-in-time visibility、一個正式公告且無衝突。
- 距ex-date不足一個完整交易日不建立exit plan。
- P4不得呼叫broker cancel；若既有long仍有working orders，固定block/review。只有在fake或既有權威狀態證明
  orders已解析且FULL+CLEAN reconciliation後，才以projected whole shares建立no-submit
  `CORPORATE_ACTION_EXIT` intent plan。真實cancel/resolve sequence留P7另行授權與驗收。
- P4證據必須證明broker submit port呼叫數永遠是0；P5 replay、P6 shadow、P7 submit各自另行驗收。

## 8. P4-A～P4-F工作包與prompts

| Gate | 範圍 | Implementation prompt | Independent acceptance prompt |
|---|---|---|---|
| P4-A | Immutable P4 config、source roles/Manifest、GET-only transport、全部零付費family adapters | `P4A_IMPLEMENTATION_PROMPT.md` | `P4A_ACCEPTANCE_PROMPT.md` |
| P4-B | Point-in-time security master、symbol identity lineage、forward/reverse split confirmation與三層quarantine | `P4B_IMPLEMENTATION_PROMPT.md` | `P4B_ACCEPTANCE_PROMPT.md` |
| P4-C | Market snapshots、monthly universe、hard filters、deterministic 100→30→12/5 candidate funnel | `P4C_IMPLEMENTATION_PROMPT.md` | `P4C_ACCEPTANCE_PROMPT.md` |
| P4-D | Pure deterministic Risk、closed reason codes、一次PM重申與immutable TargetPortfolio | `P4D_IMPLEMENTATION_PROMPT.md` | `P4D_ACCEPTANCE_PROMPT.md` |
| P4-E | Whole-share quantity、cash/open-order projection、zero-submit IntentPlan與corporate-action exit plan | `P4E_IMPLEMENTATION_PROMPT.md` | `P4E_ACCEPTANCE_PROMPT.md` |
| P4-F | Capability-minimal composition、jobs、PG roles、operations、end-to-end integration與P4 Combined Final Gate | `P4F_IMPLEMENTATION_PROMPT.md` | `P4F_ACCEPTANCE_PROMPT.md` |

固定順序為A→A驗收→B→B驗收→C→C驗收→D→D驗收→E→E驗收→F→F/P4 Final驗收。後一Gate不得在前一Gate
fresh Accepted前開始；F驗收不得用integration test取代A～E的source/contract/PG/adversarial證據。

每個code-bearing Gate先由implementation session完成並停在`pending independent acceptance`，再由未參與實作的
fresh session按對應acceptance prompt驗收。Migration從現行最高版本之後依實作順序配發，每個都必須有up/down、
checksum、legacy preflight與runtime ACL測試，不能在規劃階段預占錯誤編號。

## 9. P4 Final Gate

P4只能在以下全部同時成立時Closed：

- 所有source family的role/host/rights/schema/time/failure contract已驗收；真實GET-only probe需當次授權。
- Security master、split quarantine與Risk/submit前重驗具real PostgreSQL concurrency/failure evidence。
- Risk Engine對全部limits、freeze、stale/conflict、attempt 1/2與replay具有對抗測試。
- Quantity與intent planning證明整股、向零取整、cash/open-order projection及broker call=0。
- Non-integration、real PostgreSQL zero-skip、Ruff、format、mypy、migration cycle、docs links與`git diff --check`
  全綠。
- Fresh independent acceptance引用exact source revision；implementation、測試、commit、push或CI不能單獨關門。

## 10. 仍需延後驗證

- IEX只有有限即時市場覆蓋。P4可用於zero-submit planning，但P7前若沒有零付費且可驗收的完整報價authority，
  真實Paper submit Gate維持Blocked；不得用yfinance補權。
- FRED/BEA/EIA等免費API key、SEC/Alpaca真實GET、Tavily/GDELT下載都不由本計畫自動授權。
- 25 bps collar、30 bps spread、5秒quote、0.5% rebalance band與各風控初值須由P5 walk-forward驗證；P5只能
  收緊或透過新ADR提案變更，不能回寫P4歷史policy snapshots。
