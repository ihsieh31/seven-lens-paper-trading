# P3-E Independent Acceptance Prompt

把本文件**完整、不節錄**交給沒有參與P3-E實作的新模型。本文件只驗收P3-E，不修程式、不開始P3-F。

---

## 0. 唯一任務與判定

你是P3-E獨立驗收模型。從source、migration、tests、自建PoC、真實PostgreSQL 16及已授權live-provider evidence
重建結論，不相信implementation report、文件、commit、CI或測試名稱。

判定只有：

- `Accepted`：fake、real platform、privacy/authorization、full regression全部完整，無High/Medium blocker。
- `Rejected`：存在可重現source/authority/test/migration/privacy misrepresentation blocker。
- `Not Accepted — authorized live evidence pending`：code/fake可用但缺本Gate必要live證據或使用者批准。
- `Not Accepted — prerequisite gate open`：P3-D未由fresh session Accepted。

不得用fake接受P3-E，也不得自行呼叫provider補證據。

## 1. Read-only與credential規則

- 不修source/tests/migrations/prompts，不先修再接受；
- 不stage/commit/push/PR/merge/tag，不reset/checkout/覆蓋dirty changes；
- 不讀/列舉Keychain、`.env`、credential、shell history；
- 不在輸出顯示fake或real key、Authorization、DSN、raw prompt/response；
- 不呼叫provider/model-list/other external API，除非使用者在**本驗收session**明確授權exact scope；
- 不呼叫broker，不讀ignored root `skill/`，不使用全repo security scanner。

若live evidence由實作session產生，只驗其scope、case IDs、audit rows、sanitized outputs與authorization record；
不能因看不到secret值而視為缺陷。

## 2. 前置、revision與閱讀

確認P3-D有另一fresh Accepted report；P3-E文件是implementation completed pending acceptance，而不是只完成fake。

完整閱讀handoff/progress/README、`P3E_IMPLEMENTATION_PROMPT.md`、roadmap/master/architecture/security/decisions/issues/
risk、所有P3-B/C/D/E source/tests/migrations及secret/telemetry/audit/runtime-role code。

保存：

```bash
cd /Users/zongen/Downloads/codex/trading
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -5 --oneline --decorate
git diff --stat
git diff --name-status
git diff --check
```

revision寫成exact HEAD + 全部dirty/untracked files或exact commit。

## 3. Finding與證據標準

Source enforcement + permanent regression + independent adversarial PoC + real PG/live platform + full regression缺一不可。
High包含secret/broker capability、foreign/stale/future output、deadline後authority、audit bypass、任意endpoint/model、
prompt injection變成instruction。Medium包含boundedness/fallback/reasoning truth/privacy表述/concurrency/migration/持續測試
破壞。High/Medium阻擋Accepted。

每finding給ID、severity、file:line、invariant、minimal reproduction、expected、observed、authority impact、missing
permanent test與required remediation。

## 4. 當下官方資料與route authority

重新查**官方**provider docs，不信prompt snapshot。記錄日期、URL、model、endpoint/flavor、availability、region、
retention/training、reasoning/structured-output、pricing/quota。清楚分：官方明示、live觀察、unknown。

檢查：

- exact role/stage→primary/fallback route matrix已由使用者/ADR批准；runtime不能任意改host/path/model；
- 每route最多一個fallback，disabled candidate無自動升權；`/models`不會自動啟用新model；
- Agnes不能被錯標ZDR/不訓練；privacy policy不明確時route不得宣稱approved；
- Muse Spark Contributor若仍training-enabled/region-limited/availability不一致，必須disabled且無fallback path；
- 實作時官方資料與live evidence一致；不一致時fail closed而非悄悄換model。

缺required route的privacy/availability或使用者接受，只能Not Accepted。

## 5. Config、secret與composition驗收

逐欄mutation exact config：scheme/host/path/query/fragment/port/model/flavor/timeouts/bytes/tokens/reasoning/flags/
fallback。拒絕unknown/missing、subclass、bool-as-int、custom URL/model、redirect/proxy/env override。

用明顯fake secrets，不讀真實Keychain，證明：

- SecretKind/service/account exact、sealed、tamper/subclass拒絕；
- research scope只能取得批准provider ref，不能取得Alpaca/DB/Tavily/old OpenAI/disabled provider；
- Keychain exact read-only/UI disabled/2s timeout/zero fallback；
- key只在infrastructure單一bounded reveal點；partial resolution不留下client/plaintext；
- repr/str/log/exception/telemetry/audit/serialization/argv/env無key/header。

## 6. Sanitized envelope與prompt-injection驗收

確認envelope同時有role需要的verified evidence/context/full de-identified portfolio及exact hashes/deadline/versions，
但沒有name/account/broker order/SecretRef/credential/DSN/header/任意URL/未授權全文/tool definition。

測per-field/list/depth/node/canonical byte/token estimate邊界及+1；所有cap在network前fail。篡改identity、hash、
citation、symbol、version、deadline後也零network/authority。

建立惡意data：忽略system、改權重、讀secret、打broker、執行shell、呼叫tool、偽造citation、嵌套JSON/system role。
證明只能留在untrusted_data，不能改package-owned template、transport、tool set或authority。template不能接受caller
path或root `skill/`覆蓋；audit只記hash不記prompt全文。

## 7. Transport、adapter與strict parser驗收

Source review確認application/domain無HTTP/provider SDK/Keychain/psycopg；infrastructure transport強制HTTPS exact
host/path、TLS verify、no redirect/port/query/userinfo、no streaming/state/tools/files、bounded timeout/bytes、no automatic
retry及不信任非預期proxy env。

Fake transport PoCs：DNS/connect/read/total timeout、301/302/307/308、400/401/403/408/429/5xx、wrong content type、
empty/partial/oversize、secret-like body/header、duplicate JSON key、NaN/Infinity、unknown fields、multiple outputs、
tool call、Markdown fence、late valid response。

確認error taxonomy與fallback：permanent不重送；transient只在remaining deadline足夠時切固定fallback一次；schema/
oversize/identity drift fail closed；沒有第三route。解析順序、exact contract、nested integrity、citation/version/deadline
全都強制，沒有regex/JSON repair/free-text fallback。

## 8. Reasoning與model-call audit驗收

requested MAX與effective分離；未經官方+live證實不能寫MAX或送未知parameter。provider/model切換不能改internal
contract/risk semantics。

讀`0012` migration/repository/role verifier，確認每個primary/fallback attempt各有append-only metadata：identity、
provider/model/flavor/policy/ordinal、template/envelope/response hashes、requested/effective reasoning、可信tokens、
latency/timestamps、closed outcome/error；沒有secret/prompt/raw response/account/broker identity。

PoC證明：

- audit成功早於model output authority；
- audit failure時output零authority且不觸發fallback；
- same call exact metadata冪等，different hash/metadata拒絕；
- crash/resume不重複call或混audit/output；
- telemetry不能冒充authoritative audit。

真實PG16驗PUBLIC、owner、search path、function/table ACL、TRUNCATE/REFERENCES/TRIGGER等全部privileges、up/down/up
與兩連線collision。

## 9. Concurrency與overall deadline驗收

確認4 analysts、每輪2 debate roles、每輪3 risk viewpoints的bounded parallelism與barriers；manager stages串行；
join固定canonical role order。

注入不同duration、member exception、timeout、cancellation refusal、late success、fallback與resume：

- group不persist partial stage；late return不補寫；
- resume不混合前次partial；shared mutable state無race；
- call/audit count不超route budget；
- normal 15m/emergency 3m overall deadline不因每request重設。

P3-E改動P3-C/D orchestration後，重驗P3-B/C/D identity/evidence/deadline/resume/PostgreSQL regressions。

## 10. Live evidence驗收

若本session沒有新授權，不自行call。驗既有implementation live evidence是否包含：

- 使用者批准的exact provider/model/endpoint/case IDs/request upper bound/cost/privacy；
- payload確實synthetic/de-identified，無broker/account/raw source；
- audit attempt數與批准request count一致，無hidden retry；
- endpoint/auth/response shape/strict parse/reasoning effective；
- p50/p95/max、timeouts/status taxonomy；
- logs/telemetry/audit/output zero secret leakage；
- evidence時間與official availability/privacy相容。

只有fake或只有一個required route成功不能Accepted。未批准live call若已發生是High governance/safety finding。

## 11. 必跑命令與判定

```bash
rg --files tests | sort
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q <all P3-E targeted plus affected P3-B/C/D tests>
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/run_postgres_integration.sh
git diff --check
git status --short --branch
git diff --stat
git diff --name-status
```

保存pass/deselect/skip、Ruff/format/mypy/lock與PG major；PG skip必須0。required命令未跑/失敗不得Accepted。

檢查DECISIONS/ISSUES/RISK_REGISTER與code/evidence一致；implementation不能自關Gate。會誤導route/privacy/authority
的文件錯誤是Medium blocker。

## 12. 回覆格式

```text
TARGET_GATE: P3-E
DECISION: Accepted | Rejected | Not Accepted — authorized live evidence pending | Not Accepted — prerequisite gate open
REVISION: <exact HEAD + dirty/untracked files or exact commit>
```

依序列：findings、official route/privacy evidence、source boundaries、permanent tests、自建PoCs、PG16/audit/ACL、
authorized live evidence與request counts、full regression、未重現證據、scope exclusions、Gate state。

Accepted後單一步驟是把`P3F_IMPLEMENTATION_PROMPT.md`交給新實作模型；Rejected只做精確remediation；Not Accepted
只取得缺少的authorization/evidence。完成後停止，不開始P3-F。
