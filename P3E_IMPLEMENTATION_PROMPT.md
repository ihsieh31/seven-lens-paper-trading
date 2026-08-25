# P3-E Implementation Prompt — Provider Isolation／Sanitized Transport／Live Conformance

把本文件**完整、不節錄**交給負責P3-E的實作模型。本文件只授權P3-E；完成後必須停止，不能開始P3-F。

---

## 0. 唯一任務與兩個安全檢查點

你是Seven-Lens Paper Trading專案的P3-E實作模型。你的唯一任務是在不給analysis workers broker、DB、
filesystem或shell能力的前提下，建立typed model transport、sanitized provider envelope、sealed secret refs、
provider adapters、requested/effective reasoning audit、append-only model-call authority、最多一次fallback及bounded
stage concurrency。

P3-E有兩段，但仍是同一Gate：

1. **E-fake**：只用fake secret resolver/transport/provider，完成所有source、migration、concurrency與對抗測試。
2. **E-live**：只在使用者於當下session明確批准exact provider/model/payload/request count/費用後，執行sanitized
   conformance smoke。

沒有live授權時，完成E-fake後必須停止，狀態只能：

```text
P3-E fake conformance completed; live checkpoint blocked by authorization
```

fake不能替代live evidence。只有fake與已授權live evidence均完成，才可寫：

```text
P3-E implementation completed; pending independent acceptance
```

不得自行Accepted/Closed，不得開始P3-F。

## 1. 前置條件

開始前必須從文件與source證明：

- P3-D由另一個fresh session獨立Accepted；只有implementation completed或CI綠不夠。
- P3-E是Not started、implementation in progress或明確授權remediation。
- P3-F仍Not started，P4～P8仍未授權。
- `0010`與P3-D `0011`存在且不可修改；`0012`沒有被別的migration占用。

若P3-D未Accepted、文件矛盾、migration編號衝突或有不明重疊業務dirty changes，停止並列exact blocker。

先執行：

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

保存untracked files；不得reset、checkout、覆蓋或刪除他人的dirty changes。

## 2. 必讀資料

完整閱讀：handoff、progress、README、roadmap、master plan、architecture、SECURITY、DECISIONS、ISSUES、
RISK_REGISTER、TradingAgents assessment、`P3E_ACCEPTANCE_PROMPT.md`，以及所有P3-B/C/D contracts、pipelines、ports、
composition、secret boundary、telemetry/audit、PostgreSQL roles/migrations/tests。

特別找出：

- 既有sealed `SecretRef`、Keychain exact lookup與research secret scope；
- P3-C/D provider ports與每個logical stage/role的request/output contract；
- normal 15m與emergency 3m deadline如何傳遞；
- P3-D fake deterministic ordering與P3-C resume/persist validation；
- runtime role verifier的exact object/function allowlist pattern。

## 3. 官方provider資料不是永久常數

規劃時（2026-08-22）官方可驗證snapshot：

- Agnes國際base URL公開為`https://apihub.agnes-ai.com/v1`；`agnes-2.5-flash`使用
  `POST /v1/chat/completions`，`agnes-2.0-flash`是legacy fallback候選。
- Agnes privacy policy允許某些User Content用於服務/model improvement，retention依資料類型與目的變動；因此在
  使用者接受前不得把Agnes標成ZDR或privacy-approved。
- OpenCode Go公開base為`https://opencode.ai/zen/go/v1`，不同model分別使用`/responses`、
  `/chat/completions`或`/messages`；模型清單與privacy表會變動。
- 舊規劃候選`muse-spark-1.2-contributor`明確允許用prompts/completions訓練，且當前不同語言官方頁面的
  availability不一致；保持disabled，不得自動route/fallback。

開始實作時必須重新核對官方docs/platform，保存查詢日期與URL。不可從`/models`結果自動啟用新model，不可因
舊prompt寫過model id就視為批准。若`DECISIONS.md`沒有使用者已核准的exact role→primary/fallback route matrix，
先產出候選表（model、endpoint、flavor、region、retention/training、reasoning、價格/配額），停止請使用者選擇；
不得替使用者做會改變privacy/cost的決定。

## 4. 操作與安全邊界

在E-live明確批准前不得：

- 讀取/列舉真實Keychain、`.env`、shell history、credential檔；
- 呼叫任何real provider、model-list endpoint或網路probe；
- 要求使用者把key貼進chat、source、command line、log或測試fixture；
- 建立任意base URL/model override、自動model discovery、第三route或跨service key rotation。

整個P3-E都不得：

- stage/commit/push/PR/merge/tag；
- 使用Alpaca live/paper broker endpoint或改P2/P4/order authority；
- 把provider SDK/HTTP/Keychain/psycopg傳入application/domain；
- 讓model得到tool、file、shell、DB、broker、network callback能力；
- 保存Authorization、secret、raw prompt、raw response、DSN、account/broker identity；
- 使用regex/Markdown-fence/free-text/JSON-repair fallback取得structured output；
- 把telemetry當作authoritative model-call audit；
- 用fake conformance宣稱provider/model/reasoning/privacy已證實。

## 5. 固定工作包順序

依E0→E8實作。每包先寫targeted failing tests，再最小實作，再加入adversarial regression。需要live authority時
停止，不跨過checkpoint。

### E0 — Provider decision與capability matrix

- 重讀官方docs，記錄日期、URL、model id、endpoint、API flavor、region、retention/training、quota/pricing、
  structured output/reasoning/tool/streaming能力。
- 把「官方明示」「live已觀察」「未知」分欄；unknown不能推論成supported。
- 建立versioned exact route matrix：每個role/stage一個primary、至多一個fallback；disabled candidates另列。
- 不允許runtime傳入任意URL/model，不自動讀`/models`升權。
- 若route/privacy/cost尚未經使用者決定，先停止；這是必要authority，不是實作失敗。

### E1 — Exact config與sealed secret boundary

新增exact frozen config，至少包含：provider kind、API flavor、exact scheme/host/path/model IDs、connect/read/total
timeout、request/response byte caps、max output tokens、reasoning requested、stream/tools/state/files/redirect flags、
primary/fallback policy id。拒絕unknown/missing、explicit port、userinfo、fragment、query、path traversal、非HTTPS、
caller custom host/model與bool-as-int。

新增sealed secret identities：

```text
AGNES_API_KEY   -> service seven-lens.paper-trading.agnes.api-key   / account primary
OPENCODE_API_KEY -> service seven-lens.paper-trading.opencode.api-key / account primary
```

若現有Keychain naming convention不同，沿用現有canonical convention並在ADR記錄，不建立兼容alias。更新secret
enum、exact identity、serialization prohibition、redaction、source invariants及research scope allowlist。

research composition只能取得本route所需ref，不能取得Alpaca、PostgreSQL、Tavily、舊OpenAI或另一未使用provider
secret。Keychain保持exact read-only、UI disabled、2秒hard timeout、zero fallback。E-fake只用fake resolver。

### E2 — `SanitizedProviderEnvelope`

建立frozen/exact/bounded/canonical envelope，至少包含：

- stage/role/viewpoint/round與run/input/context/bundle/packet/snapshot hashes；
- deadline、ordered allowed symbols/citations；
- role必要的verified claims/fragments/data summaries；
- 完整但去識別化的PortfolioSnapshot；
- prior structured stage outputs；Risk retry的typed feedback與same research identity；
- prompt template id/hash、graph/data/memory versions。

明確禁止：name/account id/broker order id、SecretRef/service/account、credential/DSN/header、任意URL、未授權全文、
shell command、tool definition。任何web/model文字都放在`untrusted_data`，不能進system instructions。

resource caps必須在network前執行：per string/list/map/depth/node、canonical bytes、token estimate、allowed symbol/
citation count。canonical envelope hash承諾所有material fields。超限、malformed、foreign identity或future data
直接fail closed。

### E3 — Versioned local prompt builder

- system/developer template是package-owned approved resource，固定id/hash/version；不接受caller path或root `skill/`。
- 把instructions與untrusted data用typed sections分開；model資料中的「忽略規則」「呼叫工具」「讀secret」「下單」
  永遠是data。
- template只要求exact JSON contract，不暴露broker/order/API/tool能力。
- prompt全文不進logs/telemetry/model-call audit；只記template與envelope hashes。

### E4 — `JsonModelTransport`與infrastructure adapters

application-neutral port只接受typed request並回typed bounded response。HTTP/TLS與provider wire shape只存在
infrastructure。若新增dependency，先證明必要、pin版本、更新`uv.lock`，不得順手升級其他dependency。

transport強制：HTTPS、exact host/path、TLS verify、no redirect、no streaming、no state/tools/files、bounded DNS/
connect/read/total timeout、request/response bytes、JSON content type、一次request無automatic retry。禁止HTTP proxy/
env override悄悄改route；若library預設信任proxy env，必須明確關閉或在ADR說明受控行為。

typed error taxonomy至少區分CONFIG/AUTH/PERMANENT/RATE_LIMIT/TRANSIENT/TIMEOUT/PROTOCOL/SCHEMA/OVERSIZE/
DEADLINE/AUDIT。錯誤固定且不含body/header/key/query/raw exception。

status政策：400/401/403 permanent；408/429/5xx/network只有remaining deadline足夠才切一次fixed fallback；
malformed/oversize/tool call/multiple output/identity drift fail closed；fallback失敗不選第三model。

### E5 — Strict response parsing與reasoning truth

依序執行bounded bytes→strict JSON（duplicate keys/NaN/Infinity拒絕）→exact top-level selection→exact contract parse→
nested integrity→identity/version/citation closure→deadline。禁止regex extraction、code fence、JSON repair、unknown-field
ignore、自由文字猜測或自動補欄位。

`reasoning_requested=MAX`與`reasoning_effective=MAX|REDUCED|UNSUPPORTED|UNKNOWN`分開。只有官方documented parameter
加上authorized live observation才能寫effective MAX；未證實時不送該parameter並記UNKNOWN/UNSUPPORTED。

### E6 — Authoritative model-call audit與migration `0012`

新增`0012_p3e_provider_audit_{up,down}.sql`及typed `ModelCallAuditPort`。每次primary或fallback attempt各有一筆
bounded append-only metadata：

- call/run/input/context/stage/role/round identity；
- provider/model/flavor/endpoint policy id、route ordinal；
- prompt template hash、request envelope hash、response hash（若有）；
- requested/effective reasoning；可信token counts、latency與timestamps；
- closed outcome/error code；不含secret/prompt/raw response/account/broker identity。

model output成為analysis/proposal authority前，audit row必須成功persist。audit寫入失敗：output零authority，且
不能誤當provider failure切fallback。same call exact metadata/hash冪等；different metadata/hash collision拒絕。

SQL使用exact SECURITY DEFINER function/fixed search path/schema-qualified objects。runtime無direct DML，只SELECT與
exact EXECUTE；PUBLIC/owner/extra function/table privileges都由startup verifier與真實PG tests檢查。

### E7 — Bounded concurrency與cancellation

保留logical barriers：

- 4 Analysts同stage最多4並行，固定ROLE_ORDER join；
- 每輪Bull/Bear最多2並行，round 2等round 1；
- 每輪Aggressive/Conservative/Neutral最多3並行，round 2等round 1；
- Research Manager、Trader、Portfolio Manager串行。

每request timeout=`min(stage cap, remaining overall deadline)`。worker只持immutable envelope + provider port，不共享
mutable result dict。group任一失敗即取消pending；無法取消的late return discard，不能補寫。group完整後才按固定
順序canonicalize/persist；partial results不與resume混用。

normal 15m/emergency 3m是overall deadline，不是每call重新計時。provider attempt及fallback總數必須可由audit
精確重建。

### E8 — Tests、live checkpoint與交接

E-fake最低測試：exact config、secret forgery/tamper/scope、de-identification、resource bounds、prompt injection、
redirect/port/proxy/TLS/timeout/status/body/content-type/oversize、strict JSON、primary/fallback budget、reasoning truth、
deterministic concurrency、partial failure/late return零authority、audit-before-authority、audit failure不fallback、
secret/log/telemetry/audit leakage及P3-B/C/D完整regression。

E-live前向使用者列出並等待明確Yes：

```text
provider/model/endpoint/API flavor
synthetic payload case IDs and hashes
request count upper bound and no hidden retry
estimated cost/quota and timeout
exact Keychain refs without values
current privacy/retention/training/region evidence
stop conditions and data that will not be sent
```

取得授權後只送synthetic/de-identified payload；不讀broker/account/raw source，不送交易意圖。逐route記錄response
shape、strict parse、reasoning effective、latency p50/p95/max、status taxonomy、request count與zero secret leakage。
不要故意洩漏key測redaction。

## 6. 必跑驗證

```bash
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q <all P3-E targeted tests and affected P3-B/C/D regressions>
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/run_postgres_integration.sh
git diff --check
git status --short --branch
git diff --stat
git diff --name-status
```

先以`rg --files tests`找實際selectors，不能執行placeholder。migration需up/down/up；PG16 skip=0；記錄exact
pass/deselect/skip、Ruff/format/mypy/lock。E-live evidence另記request count與cost/quota。

## 7. 文件與Gate規則

`DECISIONS.md`需記exact approved route matrix、capability/privacy snapshot、secret scopes、fallback、concurrency、
reasoning truth及model-call audit authority。implementation不能自行關issue/risk/Gate。

- 只有E-fake：handoff寫`fake conformance completed; live checkpoint blocked by authorization`。
- fake+authorized live完成：寫`implementation completed; pending independent acceptance`。
- P3-F保持Not started；P3 Combined保持Open。

## 8. 最終回覆格式

1. `P3-E RESULT: fake checkpoint | implementation completed | partial | blocked`
2. exact HEAD與dirty/untracked files
3. approved route matrix與官方evidence日期/URL；unknown/disabled項目
4. 改動檔案與責任
5. secret/envelope/transport/audit/concurrency invariants
6. targeted/full/PG16精確結果
7. live authorization與request evidence；若無，明確寫未呼叫
8. 未決privacy/cost/capability問題
9. `GATE STATE: pending independent acceptance | evidence/authorization pending | Open`
10. 單一步驟：若完整則交`P3E_ACCEPTANCE_PROMPT.md`給fresh模型；若停在live checkpoint則等待使用者授權

完成P3-E後停止，不開始P3-F。
