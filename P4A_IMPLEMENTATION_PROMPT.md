# P4-A Implementation Prompt — Configuration／Source Platform／Zero-cost Adapters

把本文件**完整、不節錄**交給負責P4-A的實作模型。本文件只授權P4-A；完成後停止，不得開始P4-B。

---

## 0A. 弱模型硬性執行協議

本文件中的「必須／不得／只有／固定」都是驗收條件，不是建議。依下列規則工作：

1. 先完整讀完本提示詞與驗收提示詞，再讀repo；未完成前不得修改檔案。
2. 建立逐條requirement ledger，至少包含`requirement_id`、source enforcement、test、PG evidence、狀態。
3. 一次只完成一個小步：先寫失敗測試，確認失敗原因正是缺少該功能，再做最小實作使其通過。
4. 不得猜測現有type、migration編號、table、role、script或命令。先以`rg`／讀source確認；找不到就標`BLOCKED`。
5. 不得把「測試存在」「CI曾通過」「文件寫了」當作功能證據；需指出實際source enforcement與本輪結果。
6. 不得自行縮小本Gate、把必要工作留給P4-B，或順手開始B～F。任何跨Gate需求先記blocker後停止。
7. 遇到dirty user file時不得覆寫、reset、checkout或revert；先比對重疊區域，不能安全整合就停止回報。
8. 若任何禁止事項與任務衝突，禁止事項優先；不得用「為了完成」作為越界理由。

## 0B. 明確可動範圍

| 類別 | 路徑／內容 | 規則 |
|---|---|---|
| 主要可新增 | `src/seven_lens/config/p4.py`、`src/seven_lens/sources/roles.py`、`src/seven_lens/sources/adapters/` | 先確認不存在同義實作；名稱需符合現行風格 |
| 主要可修改 | `src/seven_lens/sources/contracts.py`、`src/seven_lens/infrastructure/source_http.py`及精確source ports | 只做P4-A契約／transport所需最小相容修改 |
| Persistence | 精確P4-A repository adapter、當下next migration up/down | 只有現有schema不能表達不變量時才新增 |
| Tests | `tests/test_p4a_*.py`、`tests/integration/test_p4a_*_postgres.py`與必要source-invariant test | 不得刪除、skip、xfail或放寬既有測試 |
| Docs | P4狀態、ADR／risk／issues／handoff／worklog必要段落 | 只能寫implementation pending acceptance |
| 條件式可改 | 既有secret/redaction/content-store/source repository | 先提供必要性；不得改已驗收wire/hash或擴權 |
| 絕對禁改 | P2 execution/control/ledger/order、P3 model/risk proposal語意、broker endpoint | 即使測試較容易也不得碰 |

新增dependency、通用HTTP client、SDK、背景worker或通用URL入口不在授權內。若stdlib／現有seam無法完成，先停止並
提交最小設計缺口；不得自行擴大attack surface。

## 0C. ADR-039新增的P4-A補充工作（2026-08-28）

本prompt最初實作完成後，使用者另行Accepted ADR-039。當前`sec_edgar.py`只輸出submissions的CIK/accession/form/
filing metadata，尚未輸出ADR-039所需SIC與XBRL facts；因此**不得直接交P4-A acceptance**。先在本Gate完成下列delta，
再重新跑A全部驗證：

1. 擴充SEC Manifest，分開typed endpoint policies：`data.sec.gov/submissions/CIK##########.json`與
   `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`。仍只允許HTTPS GET、exact host/path/query、redirect=reject、
   global<=5 requests/s與既有byte/timeout/resource bounds；caller不得傳任意taxonomy/concept URL。
2. Submissions parser需保存top-level exact 4-digit SIC（provider可給numeric text；僅zero-pad，不猜mapping）、CIK、
   retrieved/available time及content hash。SIC missing/invalid/conflict輸出typed failure/record quality，不自行稱sector/GICS。
3. Companyfacts parser只接受以下exact `(taxonomy, concept)` allowlist，不接受suffix、case-fold、extension或first-match：

```text
us-gaap / NetIncomeLoss
us-gaap / NetCashProvidedByUsedInOperatingActivities
us-gaap / Assets
us-gaap / PaymentsToAcquirePropertyPlantAndEquipment
dei / EntityCommonStockSharesOutstanding
```

4. 每筆normalized fact至少保存CIK、taxonomy、concept、unit、exact Decimal value、start?、end、fiscal year/period、form、
   accession、filed date、matched submission acceptance/available time、retrieved_at、source/content hash、schema version。
   Companyfacts沒有足夠accession/context或無法與accepted submission閉合時不得猜available time。
5. P4-A只normalize provider facts與lineage，不計TTM、quarter decomposition、market cap、factor、SIC Division或Risk。
   `PaymentsToAcquirePropertyPlantAndEquipment`需保存provider value與manifest sign convention；不得任意`abs()`。
6. Tests必須涵蓋top-level SIC 0100/1000/缺失/非數字/長度、五concept valid records、unknown/extension concept、duplicate
   units/contexts、decimal bool/float/NaN、YTD/quarter metadata、accession join missing/conflict、future acceptance、oversize及
   canonical replay。無本次外部GET授權，全部使用offline official-shape fixtures，network call count=0。

此delta仍屬P4-A source normalization，不授權P4-C factor/SIC mapping或任何model/broker能力。

## 0. 唯一任務與成功狀態

你是Seven-Lens Paper Trading專案的P4-A實作模型。唯一任務是建立P4 immutable設定、封閉source-role registry、
SourceManifest、capability-minimal GET-only transport，以及P4核准的零付費source-family adapters。輸出只能是
typed、point-in-time、可hash／可稽核的source/market records；不能建立security master、universe、RiskDecision、
TargetPortfolio、quantity、IntentPlan或任何broker side effect。

允許的最終狀態只有：

```text
P4-A implementation completed; pending independent acceptance
```

或清楚的`partial/blocked`。不得寫Accepted／Closed，不得開始P4-B。

## 1. 前置條件與現況保存

目前快照是P0～P3 Closed、P4 planning complete、P4-A初版source/tests已存在但ADR-039第0C節delta待完成；
仍必須以當下repo重驗，不得假定初版正確。先執行：

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

逐一保存dirty/untracked檔；它們屬使用者，不能覆蓋。確認：

1. `P4_PROGRAM_PLAN.md`、ADR-038與ADR-039仍是現行權威。
2. P1～P3仍Closed；若任何前置Gate重新Open，停止。
3. 逐檔識別既有P4-A初版與第0C節需修改的重疊；保留使用者／其他輪次變更，無法安全整合才停止。
4. 現行migration最高版本與checksum必須重新讀取，不能相信prompt中的舊編號。

## 2. 必讀文件與source

完整閱讀：`PROJECT_HANDOFF.md`、`PROGRESS.md`、`README.md`、`P4_PROGRAM_PLAN.md`、本檔、
`P4A_ACCEPTANCE_PROMPT.md`、roadmap、master plan、architecture、operations、security、sources、decisions、
issues、risk register，以及現有：

- `src/seven_lens/config/`、`security/`、`sources/`、`market_data/`；
- `application/secret_service.py`與全部secret/source/content-store ports；
- `infrastructure/source_http.py`、`macos_keychain.py`、`alpaca_paper.py`；
- P3 source/evidence contracts、PostgreSQL source authority與相關tests/migrations；
- `.env.example`、`pyproject.toml`、`uv.lock`與source-invariant tests。

先畫出existing type/import/composition/persistence call graph；不得按規劃檔名盲建重複抽象。

## 3. 操作與安全限制

未經使用者另行明確授權，不得：

- stage、commit、push、PR、merge、tag或改remote；
- 讀寫Keychain、`.env`、shell history、credential檔或ignored `skill/`；
- 呼叫任何Alpaca、SEC、FRED、BEA、BLS、EIA、Treasury、Tavily、GDELT、yfinance、model或broker API；
- 執行真實下載、註冊key、付費、寫broker state或送單；
- 新增任意URL、redirect-following通用client、POST、browser、shell或provider SDK capability；
- 修改P2 execution/control/reconciliation/ledger authority或已套用migration；
- 以fixture、mock或文件敘述冒充live source／rights evidence；
- 讀取raw secrets或在error/log/repr保存URL query中的key、header、payload/body、portfolio/account identity。

使用stdlib transport或現有最小transport seam；新增dependency前必須證明必要性、鎖版、license與attack surface。

## 4. P4-A固定設定契約

建立immutable、versioned、canonical `P4PolicyConfig`（名稱可依現有風格調整），固定：

- single account、strategy=`seven_lens_long`、`short_enabled=false`；
- long/total gross 0.90、short 0、cash buffer 0.10、15 positions、name 0.05、sector 0.25、cluster 0.30；
- normal turnover 0.20、ADV 0.001、daily-loss stop 0.01、drawdown freeze 0.08；
- whole shares、minimum adjustment `max(USD100,NAV*0.0025)`、rebalance band 0.005；
- quote age 5秒、spread 30bps、collar 25bps；
- zero-cost only、P4 zero-submit、IEX coverage warning mandatory。

設定必須exact type、fixed-scale Decimal、frozen/slots、canonical wire與domain-separated hash；拒絕bool-as-int、
NaN/Infinity、negative zero、unknown fields、subclass、env/runtime override。不能將上限分散成可漂移常數。

## 5. Source registry與Manifest

source role只允許`AUTHORITY|CONFIRMATION|DISCOVERY|RESEARCH_SUPPLEMENT`。Coverage是獨立維度，IEX不得創造
第五種role；以`LIMITED_MARKET_COVERAGE`標記。每個family建立固定policy：

```text
family, role, exact scheme/host/path template, GET-only
redirect policy, auth/SecretRef?, query allowlist
request/response byte budget, timeout, pagination
rate/burst budget, content types, schema version
timestamp semantics, rights/storage policy, producer version
```

至少涵蓋：Alpaca assets／historical bars／IEX quotes／corporate actions、SEC EDGAR、issuer IR／exchange official
pages、FRED/ALFRED、Treasury、BLS、BEA、EIA、Tavily、GDELT、yfinance。不得因outage或設定把discovery／
supplement升權。Manifest本身需canonical hash、version與source tests；rights=`UNKNOWN`時禁止production use。

## 6. GET-only transport boundary

建立或強化單一transport seam，要求：

- request必須由typed family policy建構；caller不能傳任意URL/header/query；
- HTTPS exact-host/path；拒絕userinfo、fragment、IP literal、port drift、IDNA/confusable、encoded path escape；
- redirect固定拒絕，或只在該family Manifest明列exact target時接受；
- DNS/TLS/HTTP timeout、429/Retry-After、408、5xx、oversize、wrong MIME、compression bomb、malformed body
  轉為bounded typed failure；production adapter不做hidden retry/fallback；
- response streaming先執行compressed/uncompressed byte budget，再parse；
- credential只來自exact typed `SecretRef`，不進URL、repr、log、audit、exception；
- audit只保存family、sanitized endpoint id、status class、latency、byte count、content hash與error code。

Application/domain不得import urllib/http client、Keychain、psycopg、Alpaca SDK或其他backend。

## 7. Family adapters與normalized records

每個adapter只做schema驗證與normalized record建構，不做Risk判定：

- Alpaca historical bars：保存requested/effective feed；delayed SIP不可在entitlement失敗時靜默退IEX。
- Alpaca IEX quote：保存bid/ask/as-of/received/feed並標`LIMITED_MARKET_COVERAGE`；不聲稱NBBO。
- Alpaca assets：active/tradable/exchange/class；unknown/new enum fail closed。
- Alpaca corporate actions：只parse forward/reverse split及原始identity/date/ratio；P4-A不確認／退出。
- SEC：data.sec.gov exact paths、CIK/accession/XBRL、可識別User-Agent、全域≤5 req/s；HTML／JSON分開。
- SEC ADR-039 delta：submissions SIC與companyfacts五concept依0C exact allowlist輸出typed point-in-time facts；不計factor。
- FRED/ALFRED：顯式real-time period與vintage；禁止default-today污染historical record。
- Treasury/BLS/BEA/EIA：各自獨立schema、release/observation/available/retrieved timestamps；不能普通fallback。
- Tavily/GDELT：只產生discovery records；snippet/event score不能成material claim。
- yfinance：永遠`RESEARCH_SUPPLEMENT`；不得填補price/security/corporate-action authority缺口。

共用record必须保存適用的observation/published/discovered/available/retrieved/effective/vintage時間、provider record
ID、content hash、role、family、rights、schema/producer version、supersession refs。未知時間不能猜成retrieved_at。

## 8. Persistence與ACL

優先重用P3 `source_objects/source_records/evidence_packets`，只有schema無法表示P4不變量時才新增migration。
不得修改既有migration。新增表／欄位需up/down、legacy preflight、CHECK/UNIQUE/FK、canonical bytes/hash、runtime
最小權限與owner/runtime/curator capability matrix。Runtime可append已驗證record，不可改role/policy/hash或更新
immutable raw record；相同provider identity same-hash可bounded idempotent，different-hash需explicit supersession。

## 9. 必測矩陣

每個work item先紅測試、再最小實作、再adversarial regression：

- config每欄±最小單位、bool/subclass/negative-zero/NaN/unknown/tamper/hash；
- host/path/query/redirect/port/userinfo/Unicode/encoded escape；
- timeout、DNS/TLS、408/429/5xx、wrong MIME、truncated/duplicate-key/oversize/compression bomb；
- secret absence/multiple/denied/malformed，並掃repr/log/error無secret；
- 各family valid/min/max/schema drift/pagination cycle/duplicate/out-of-order/future timestamps；
- silent fallback與role escalation必須零authority；
- FRED historical request沒有explicit realtime時拒絕；IEX永遠帶coverage warning；
- InMemory/PostgreSQL parity、rollback、crash-resume、same/different hash、ACL與migration cycle。

不授權真實網路時，live probe保持明確pending，不能把offline adapter test寫成production source accepted。

## 9A. 逐步實作順序與每步退出條件

嚴格依序執行；前一步未綠不得開始下一步：

1. **A1 Inventory／requirement map**：列出現有可重用types、ports、transport、tables、roles、tests與next migration。
   輸出mapping後才可改code；發現同義contract時重用，不能另建平行authority。
2. **A2 Immutable config**：先完成config wire、parser、validator、canonical bytes/hash及mutation tests。不得在此步加入
   HTTP、DB或environment override。
3. **A3 Roles／Manifest registry**：先建立closed enums與每個family的完整manifest；registry startup需驗證family唯一、
   role不升權、rights不是UNKNOWN、resource budgets非零且有上限。
4. **A4 Transport**：以injected fake executor完成所有URL／method／redirect／timeout／byte-budget／secret-redaction測試。
   在此步不得連真實host；觀測到一次未授權request即停止並修正。
5. **A5 Adapters**：一次只做一個family，順序為Alpaca→SEC/IR/exchange→FRED/ALFRED→Treasury/BLS/BEA/EIA→
   Tavily/GDELT→yfinance。每個family需valid fixture、schema drift、timestamp、pagination、role與resource-bound測試全綠
   才進下一family。不得以一個generic JSON adapter代替family-specific validation。
   SEC family只有0C的submissions＋companyfacts兩個policies與SIC／五concept tests全綠才算完成。
6. **A6 Persistence**：先證明既有schema不足才建migration；完成in-memory／PG parity、append-only、idempotency、
   supersession、rollback、ACL與up/down/up。
7. **A7 Capability closure**：source scan與constructor tests證明domain/application沒有HTTP／secret backend，P4-A沒有
   broker/model/Risk/Target/Intent capability，所有failure零authority persist。
8. **A8 Full verification／docs**：跑完整命令、記錄exact counts與skips；只在全部本Gate證據完成後更新狀態。

每一步的完成證據必須同時包含：changed files、核心source symbol、先紅後綠測試名稱、執行命令、exit code與仍未完成項。

## 9B. 各family最低實作完成表

每個family都必須逐欄填入實作報告；任一欄空白即`partial/blocked`：

```text
family:
role / coverage:
exact scheme + host + path template:
query/header allowlist:
auth SecretRef or none:
request/response/decompression byte limits:
connect/read/total timeout and retry=0:
rate/burst/pagination bounds:
accepted MIME + schema version:
observation/published/available/retrieved semantics:
rights/storage policy:
valid fixture + malformed/schema-drift fixtures:
typed failures:
canonical record + hash:
offline tests:
live evidence: NOT AUTHORIZED | AUTHORIZED result
```

不得把同一行複製給不同family；Treasury、BLS、BEA、EIA必須各自完成。

## 9C. Definition of Done

只有下列全部為真，才可使用成功語句：

- A1～A8全部完成，requirement ledger沒有`TODO/UNKNOWN`；
- ADR-039 SEC delta完成：SIC與五concept normalized records、accession/available-time lineage及adversarial tests閉合；
- 4種source role封閉，全部核准family都有唯一manifest與adapter；
- 任意URL／method／redirect／fallback／role escalation均不可達；
- config與records可canonical round-trip且tamper必拒絕；
- secrets與raw body未出現在repr/log/audit/error/test snapshot；
- 若有DB變更，真實PG16 migration／ACL／concurrency／rollback證據完整且zero unexpected skip；
- P1～P3既有contracts、migrations checksums與tests未被放寬；
- diff只落在本Gate範圍，文件仍明列zero-submit與live evidence狀態；
- 已產生可供fresh acceptance重跑的精確命令與fixture，不需要實作者口頭解釋。

缺任何一項只能回報`partial/blocked`，並列出缺口、原因、已完成證據與唯一建議下一步。

## 10. 驗證與交付

最低命令集（先確認檔名存在；不得刪測試或加skip來取得綠燈）：

```bash
uv run --locked pytest tests/test_p4a_*.py tests/test_secret_source_invariants.py tests/test_paper_only_source_invariants.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

若P4-A integration檔名不是核准pattern，列出實際檔名並補跑；`--postgres`必須是disposable PG16且unexpected skip=0。

至少執行focused tests、source/security invariants、non-integration full suite、Ruff format/check、mypy、
`git diff --check`；若有PostgreSQL變更，必須跑真實PG16 zero-skip、migration up/down/up、runtime ACL與故障注入。

同步`P4_PROGRAM_PLAN.md`、decisions/issues/risk、handoff/progress/worklog，但狀態只能寫implementation completed;
pending independent acceptance。列出exact revision、dirty/untracked set、changed files、commands/results、未執行的
live evidence與remaining blockers。完成後停止。

最終報告固定格式：

```text
P4-A IMPLEMENTATION STATUS: completed pending independent acceptance | partial | blocked
REVISION: <HEAD + 完整dirty/untracked set>
SCOPE CHECK: <in-scope files / out-of-scope files / user files preserved>
REQUIREMENT LEDGER: <逐項PASS/FAIL/PENDING及source:test:PG證據>
FAMILY MATRIX: <每個family完整表>
MIGRATION/ACL: <not needed，或exact migration與結果>
COMMANDS: <原命令、exit code、passed/failed/deselected/skipped>
EXTERNAL CALLS: 0，或列出本次exact授權與bounded結果
CAPABILITY COUNTS: broker=0, model=0, POST=0, unapproved GET=0
BLOCKERS/REMAINING EVIDENCE: <none或精確項目>
NEXT ACTION: run a fresh P4-A independent acceptance session
```
