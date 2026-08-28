# P4-C Implementation Prompt — Market Snapshots／Universe／Candidate Funnel

完整交給P4-C實作模型。本文件只授權P4-C；完成後停止，不得開始P4-D。

## 0A. 弱模型硬性執行協議

先讀完整提示詞並建立requirement ledger。ADR-039已核准四個immutable manifests；不得再選擇其他「合理預設」：

1. Factor只能是`p4-factor-v1`，權重、公式、concept allowlist、winsorization、missing policy與tie-break以本檔6A固定。
2. Sector只能是`sec-sic-division-v1`；SEC SIC Division不是GICS，不得保留GICS欄名、enum、文件或fallback mapping。
3. Cluster只能是`p4-correlation-cluster-v1`；126 sessions、minimum pair 100、Pearson 0.75、connected components不可改。
4. 每個manifest用frozen/slots exact types、canonical JSON與domain-separated hash；env/model/source不能override。
5. 一次只做一個stage，先紅測試再最小實作；DB/query/collection ordering不得成為tie-break。
6. 不得為完成C修改A/B contracts；若前置contract不足，停止並要求重開對應Gate。

## 0B. 可動範圍

| 類別 | 路徑／內容 | 限制 |
|---|---|---|
| Market | `market_data/snapshots.py`及精確port/repository | 只normalize/validate，不fetch、不Risk |
| Universe | `universe/contracts.py`、`universe/builder.py` | 只用已Accepted A/B authority |
| Screening | `screening/contracts.py`、`screening/funnel.py` | 只實作已核准factor manifest，不自行設策略 |
| Persistence | `postgres_market_data.py`、`postgres_universe.py`、next migration | append-only/CAS所需最小變更 |
| Tests | `test_p4c_*`與PG integration | 不刪、不skip、不放寬A/B/P3 tests |
| 條件式 | clock/calendar、P3 evidence read-only public contract | 不改P3 proposal/model authority |
| 禁止 | risk/portfolio/quantity/intent、P2 orders、provider/model/broker | 不建placeholder或composition |

## 0. 任務

使用已Accepted的P4-A source records與P4-B security/quarantine authority，建立typed market snapshots、版本化
point-in-time US-equity universe與deterministic 100→30→12/5 candidate funnel。P4-C不呼叫model，不做Risk approval、
TargetPortfolio、quantity或IntentPlan，不連P2 execution。

成功只能回報：`P4-C implementation completed; pending independent acceptance`。

## 1. 前置、必讀與狀態保存

先保存pwd、git status、HEAD/origin、diff/untracked、migration清單/checksum。P4-A與P4-B都必須有fresh Accepted；
任一pending/rejected即停止。完整閱讀所有治理文件、P4計畫、A～C prompts、A/B contracts/repositories、P3
AnalysisInput/EvidencePacket/ProposalContext、market events、clock/calendar、P2 account/position snapshots及相關tests。

先畫出資料流：source records → security identity/quarantine → market snapshot → universe snapshot → quant set →
evidence set → focus symbols。不得讓dict/DB completion order決定結果。

## 2. 禁止事項

不stage/commit/push；不讀credential、不真實GET、不呼叫model/broker；不修改A/B Accepted wire/hash；不新增付費
data或以yfinance補authority；不做optimizer/Risk/quantity；不把candidate ranking寫回P3 proposal authority；不支援
short、OTC、options/crypto/fractional。未知／stale／conflict一律移除新增曝險候選並保存原因。

## 3. Owned modules

優先新增／最小修改：

```text
market_data/snapshots.py
universe/contracts.py
universe/builder.py
screening/contracts.py
screening/funnel.py
application/ports/market_data.py
application/ports/universe.py
infrastructure/postgres_market_data.py
infrastructure/postgres_universe.py
```

以及對應unit/integration tests與必要migration。服從現有package boundaries，不搬動已驗收code。

## 4. MarketSnapshot

建立versioned immutable snapshot，至少包含security_id/symbol/feed/entitlement、bid/ask/mid/last（適用時）、
bar refs、20日ADV美元值、as_of/received_at、freshness、coverage、source/version/hash。規則：

- 最新P4 quote只接受Alpaca IEX record，mandatory `LIMITED_MARKET_COVERAGE` warning；
- historical bars/ADV需保存exact feed；delayed SIP unavailable不能靜默改feed；
- bid≤ask、positive exact Decimal、spread bps deterministic重算；caller不能自報spread/ADV；
- quote age≤5秒；spread≤30bps；stale/missing/conflict不能進可交易候選；
- ADV需exact 20個合格交易日、split-aware且不使用future corporate-action adjustment；不足即ineligible；
- timestamps與NYSE calendar一致；future、closed-market誤標fresh、duplicate/out-of-order均拒絕。

Coverage warning不阻止P4 zero-submit candidate計算，但必須一路傳到後續Risk/Intent lineage並阻擋P7 readiness。

## 5. UniverseSnapshot

每月從as-of Alpaca active+tradable US assets與B security master建立immutable snapshot。只保留：

- 只接受ordinary common stock；ETF（包含unlevered ETF）與所有其他instrument type固定排除；
- price≥USD5；20日平均美元成交額≥USD20M；至少252個有效交易日；
- 排除OTC、preferred、warrant、unit、closed-end fund、ETN、leveraged/inverse ETF；
- 排除halt、inactive/untradable、identity ambiguous、data quality unknown；
- P4-B query非ELIGIBLE者排除，並保存event/reason lineage；
- 全部whole-share feasibility：5% name cap／minimum adjustment下無合理一股配置者排除或typed標記。

Snapshot包含as_of、security-master version、market/source snapshot refs、ordered eligible/ineligible entries、每項reason、
policy hash與universe hash。Order固定canonical security identity，不以provider/DB回傳順序。

## 6. Candidate funnel

固定階段與上限：

```text
eligible universe (~2,000–4,000)
-> quant candidates <=100
-> evidence candidates <=30
-> open+60m focus <=12 / close-90m focus <=5
```

Quant factors固定使用本檔6A `p4-factor-v1`；不得加、刪、重權重或以LLM/free text排序。

Evidence screen只使用P3/P4 typed evidence coverage/freshness/conflict metadata，不呼叫model。Material authority缺失、
future/stale/conflict、prompt-injection flag、corporate-action block都不能成focus candidate。

Tie-break固定：composite、Trend、Quality、Value、LowRisk依序降序，最後stable security ID升序；不得依symbol、dict
insertion、thread completion或DB row order。所有cutoff与deadline前後±1µs固定測試。

現有全部持倉可作分析context，但「持倉」不等於新增曝險候選；P4-C不自行創造CLOSE/REDUCE action。

## 6A. ADR-039 exact manifests與實作公式

### 6A.1 `p4-factor-v1`

Cutoff以`as_of`表示；所有price inputs止於cutoff前一個完整NYSE regular session。Split adjustment只能使用B中
`available_at<=cutoff`的confirmed actions。令`P(t-k)`為往前第k個completed-session close，simple return：

```text
trend_126_21 = P(t-21) / P(t-126) - 1
trend_252_21 = P(t-21) / P(t-252) - 1
daily_return[d] = P[d] / P[d-1] - 1
```

Trend兩欄都mandatory；最長公式因此需能解析`P(t-252)`，不足、duplicate、gap/conflict即`FACTOR_INPUT_MISSING`。

Fundamentals只接受P4-A normalized SEC facts的exact allowlist：

```text
us-gaap:NetIncomeLoss
us-gaap:NetCashProvidedByUsedInOperatingActivities
us-gaap:Assets
us-gaap:PaymentsToAcquirePropertyPlantAndEquipment
dei:EntityCommonStockSharesOutstanding
```

不得使用模糊suffix、company extension、近似tag或任意first-match。四個non-overlapping fiscal quarters必須相同entity、
currency、consolidation scope、fiscal year/quarter lineage且每筆`available_at<=cutoff`。YTD fact只有能以同fiscal-year、unit、
context的current YTD減prior YTD無歧義得到單季時才可用；否則missing。CapEx在normalizer中以positive cash-outflow表示，
不得在factor層用`abs()`修正未知sign。

```text
ttm_net_income = sum(4 quarterly NetIncomeLoss)
ttm_cfo = sum(4 quarterly CFO)
ttm_capex = sum(4 quarterly positive CapEx outflow)
average_assets = (assets_at_ttm_start + assets_at_ttm_end) / 2
roa = ttm_net_income / average_assets
cfo_to_assets = ttm_cfo / average_assets
accrual_quality = (ttm_cfo - ttm_net_income) / average_assets
market_cap = point_in_time_shares_outstanding * P(t-1)
earnings_yield = ttm_net_income / market_cap
fcf_yield = (ttm_cfo - ttm_capex) / market_cap
```

`average_assets<=0`、`market_cap<=0`、shares<=0、conflicting contexts/units或任何missing使該security排除；negative NI/CFO/
FCF是合法低值，不能改成missing或0。

Low Risk固定：取截至`P(t-1)`的最近63個finite simple returns，
`vol63=sqrt(sum((r-mean)^2)/63)*sqrt(252)`（population denominator=63）；取最近252個completed-session closes，
`max_drawdown252=max_j(1-P[j]/max_{k<=j}P[k])`，範圍[0,1)。兩者都mandatory；zero variance合法，非finite拒絕。

對全部9個raw subfactors先建立完整factor-eligible cross-section。每欄ascending sort後，N>=1時：

```text
q05 = x[ceil(0.05*N)-1]
q95 = x[ceil(0.95*N)-1]
winsorized = min(max(raw, q05), q95)
midrank = ((first_zero_based_index + last_zero_based_index) / 2) / (N-1)   # N>1
midrank = 0.5                                                              # N=1
```

Indices在winsorized ascending values上計算；ties必須相同percentile。對vol/max drawdown先取負值再percentile，使高分為低風險。

```text
Trend = mean(pct(trend_126_21), pct(trend_252_21))
Quality = mean(pct(roa), pct(cfo_to_assets), pct(accrual_quality))
Value = mean(pct(earnings_yield), pct(fcf_yield))
LowRisk = mean(pct(-vol63), pct(-max_drawdown252))
Composite = 0.35*Trend + 0.25*Quality + 0.15*Value + 0.25*LowRisk
```

Event不是alpha score。只有B quarantine=`ELIGIBLE`，且本次typed evidence沒有material authority missing/stale/conflict、
沒有unresolved prompt-injection flag，才通過event hard gate。沒有新事件本身不扣分；discovery snippet不得創造方向。

Quant top100在event gate前依固定tie-break截取；Evidence top30從top100中移除event/evidence不合格者後依原順序取前30；
open+60取前12，close-90取前5。不得為補滿數量越過top100或重新計分。

### 6A.2 `sec-sic-division-v1`

從cutoff前最新無衝突EDGAR filer SIC取exact 4-digit string；3 digits需左補0，其他型別／長度拒絕。依前2 digits映射：

```text
01-09 A  Agriculture, Forestry, Fishing
10-14 B  Mining
15-17 C  Construction
20-39 D  Manufacturing
40-49 E  Transportation, Communications, Electric/Gas/Sanitary Services
50-51 F  Wholesale Trade
52-59 G  Retail Trade
60-67 H  Finance, Insurance, Real Estate
70-89 I  Services
91-97 J  Public Administration
```

18-19、68-69、90、98、99、missing、future或同cutoff conflict固定`SECTOR_UNKNOWN`，不可進新增曝險候選。Snapshot保存
CIK、SIC、division、source record/accession、available_at、taxonomy version/hash；sector cap在D使用division，不得稱GICS。

### 6A.3 `p4-correlation-cluster-v1`

Nodes固定為當日quant top100與current FULL+CLEAN long holdings的union，stable security ID排序。使用截至前一完整regular
session的最近126個point-in-time split-aware simple returns。每security至少100 finite returns；每pair按共同session inner
join後至少100 observations。Pearson：

```text
rho(i,j) = sum((ri-mean_i)*(rj-mean_j)) /
           sqrt(sum((ri-mean_i)^2) * sum((rj-mean_j)^2))
```

Denominator zero、non-finite或pair coverage<100時，涉及的security標`CLUSTER_UNKNOWN`，不得當singleton繞過。其餘pair
在`rho>=0.75`（等於通過edge）建立undirected graph；按ordered nodes做connected components。無edge且資料完整為合法
singleton。Cluster ID=`domain_hash(policy_hash, as_of, ordered_member_ids)`。每日第一個window前發布；第二window只有
price/security/policy inputs完全相同才可重用。UNKNOWN持倉可作context並允許數值上REDUCE/CLOSE，不可OPEN/INCREASE。

## 7. Persistence、jobs與resume

Market/universe/candidate snapshots append-only，保存config/source/security version與canonical bytes/hash。Same input
same hash bounded idempotent；same job/window different hash是conflict，不能覆蓋current。DB constraints保護一個
month/as-of/window的single authority、ordered members與parent-child completeness。

Crash在partial children時不得發布current universe/candidate set；publication需單transaction/CAS。Restart必須讀回
bytes/hash、重算features/ordering/quarantine，不信`COMPLETE`旗標。Runtime無UPDATE/DELETE historical snapshots權限。

## 8. 必測矩陣

- market：bid/ask inversion、zero/negative/float、5秒±1µs、30bps±最小單位、IEX warning、feed fallback；
- ADV：19/20/21日、missing/duplicate/out-of-order、split前後、future bar、holiday/half-day；
- universe：每個asset type、USD5與USD20M邊界、251/252日、halt/tradable/identity/quarantine；
- funnel：0/1/max/max+1、ties、permutation、duplicate security/symbol、missing feature、NaN、outlier、future evidence；
- two windows 12/5、deadline等於/前後、持倉與候選去重；
- crash/publish/CAS、same/different hash、兩連線publication race、runtime ACL、migration cycle；
- source invariant證明零model/Risk/quantity/broker capability。

## 8A. 詳細實作順序

1. **C0 Prerequisite audit**：確認ADR-039與本檔6A完全一致；建立三個manifest的frozen exact contract、canonical wire、
   domain tag與golden hash test。任何差異停止，不得挑選版本。
2. **C1 Market contract**：建立exact Decimal、time/coverage/freshness enums、canonical bytes/hash與resource bounds。所有
   derived spread/ADV由trusted constructor計算，wire caller不得輸入計算結果冒充authority。
3. **C2 Snapshot assembler**：只接A normalized records與B identity version，按`as_of/known_at`拒絕future、duplicate、
   out-of-order、feed drift。完成IEX warning、20-session ADV、NYSE calendar與split-aware tests。
4. **C3 Universe hard filters**：每條eligibility rule獨立closed reason；先依stable security ID canonical排序，再發布
   eligible/ineligible全集。不得先truncate再filter，也不得丟掉排除理由。
5. **C4 Factor feature builder**：只有C0三個manifest均存在才開始。每個feature保存raw refs、formula version、inputs、
   missing reason與value；不得將missing設0、以今天基本面回填或把supplement變authority。
6. **C5 Quant top 100**：依approved formula重算score；在所有eligible names計算完成後才排序／truncate。NaN/conflict/
   missing mandatory factor為ineligible，不能被低分默默掩蓋。
7. **C6 Evidence top 30**：只按closed evidence coverage/freshness/conflict policy；不得讀free text、snippet sentiment或
   LLM output作分數。Corporate-action non-ELIGIBLE在此再次拒絕。
8. **C7 Focus 12/5**：使用approved final score與stable identity tie-break；window/deadline由calendar輸入。持倉context與
   new-candidate list分開，持倉不得占用12/5名額，也不得因缺資料被視為可加碼。
9. **C8 Publication/PG**：parent＋ordered children單transaction/CAS；restart readback validation；two-connection race；
   runtime UPDATE/DELETE拒絕。
10. **C9 Closure**：做source invariant、A/B/P3 nearby regression與完整驗證；文件只標pending acceptance。

## 8B. Closed records與reason codes

`MarketSnapshot`、`UniverseSnapshot`、`FeatureVector`、`CandidateSet`都必須有schema/producer/policy versions、as-of、
known-at cutoff、ordered parent refs、canonical hash與最大items/bytes。至少使用下列closed reasons（可改名不可合併語意）：

```text
UNSUPPORTED_ASSET_CLASS
OTC_OR_EXCLUDED_INSTRUMENT
NOT_ACTIVE_OR_TRADABLE
PRICE_BELOW_MINIMUM
ADV_BELOW_MINIMUM
INSUFFICIENT_TRADING_HISTORY
IDENTITY_NOT_CLOSED
CORPORATE_ACTION_QUARANTINE
QUOTE_MISSING_OR_STALE
SPREAD_TOO_WIDE
MARKET_DATA_CONFLICT
FACTOR_INPUT_MISSING
FACTOR_MANIFEST_NOT_APPROVED
SECTOR_TAXONOMY_NOT_AUTHORIZED
CLUSTER_POLICY_NOT_APPROVED
EVIDENCE_INSUFFICIENT_OR_CONFLICTING
WINDOW_OR_DEADLINE_INVALID
```

不得用單一`INVALID`讓驗收者無法判斷排除原因。

## 8C. Definition of Done

- 20-session ADV、USD5、USD20M、252 sessions及所有asset/quarantine hard filters有精確邊界證據；
- factor／sector／cluster三個manifests與ADR-039 exact一致並有canonical golden hash；
- 100→30→12/5每層輸入、分數、排除理由與ordered members均可重播；
- permutation、thread/DB ordering、ties與same inputs產生byte-identical結果；
- 沒有future data、current classification leakage、supplement升權、LLM/model或Risk/quantity capability；
- PG publication原子、競態單head、corrupt resume fail closed、runtime ACL有效；
- 完整tests/static checks全綠、unexpected skips=0，requirement ledger無UNKNOWN。

## 9. 驗證與交付

最低命令集：

```bash
uv run --locked pytest tests/test_p4c_*.py tests/test_market_clock.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

Manifest任何欄位、公式、weight、threshold或taxonomy與6A不符時，命令全綠仍不能完成。

跑focused、A/B/P3 evidence/clock nearby、完整non-integration、PG16 zero-skip、migration/ACL/concurrency、Ruff、format、
mypy、diff check。同步文件但只標implementation completed/pending acceptance。列exact revision、changed files、
公式／tie-break、test commands/results與未決source/live evidence；不得開始P4-D。

最終報告固定包含：

```text
P4-C IMPLEMENTATION STATUS: completed pending independent acceptance | partial | blocked
REVISION / DIRTY SET / A-B ACCEPTANCE LINEAGE:
PREREQUISITE MANIFESTS: factors / sector / cluster <exact version+hash or MISSING>
SCOPE CHECK:
MARKET/ADV BOUNDARY VECTORS:
UNIVERSE EXCLUSION MATRIX:
100->30->12/5 REPLAY AND TIE-BREAK:
PG16 PUBLICATION/ACL/CONCURRENCY:
MODEL/BROKER/EXTERNAL CALL COUNTS: 0
COMMANDS AND EXACT COUNTS:
BLOCKERS:
NEXT ACTION: fresh acceptance only if completed; otherwise obtain the missing user decision
```
