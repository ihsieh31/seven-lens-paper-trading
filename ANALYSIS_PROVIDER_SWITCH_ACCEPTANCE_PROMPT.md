# Analysis Provider Switch — Fresh Independent Acceptance Prompt

把本文件完整、不節錄地交給一個沒有參與實作的 fresh 模型。你是唯讀獨立驗收者；不得先修再驗、不得使用
實作者的結論代替證據、不得 commit/push，也不得在沒有當次精確授權時讀Keychain或呼叫B.AI模型。

---

## 0. 驗收目標與 verdict

驗收以下單一 work package：Seven-Lens 的分析 AI endpoint/model 是否已從 Agnes hard-code安全地改成 provider-neutral
設定，且操作者確實只需兩個指令：

```bash
uv run --locked python -m seven_lens.cli.analysis_provider set-endpoint https://api.b.ai/v1
uv run --locked python -m seven_lens.cli.analysis_provider set-model deepseek-v4-flash
```

新process應使用full endpoint`https://api.b.ai/v1/chat/completions`與指定model；API key不在這兩個命令
中，仍只能由 fixed macOS Keychain boundary提供。

只能選一個 verdict：

- `Accepted`：source/config/CLI/transport/audit/migration/P3-E/P3-F/full regression均符合；若本work package規定live
  是acceptance必要證據，則新B.AI P3-E/F live evidence也必須有效；無High/Medium findings。
- `Rejected`：存在可重現的安全、authority、資料庫、CLI、route identity、output contract、eval或 regression blocker。
- `Not Accepted — B.AI live evidence pending`：offline/PG實作看似完整，但缺當次合規的新route P3-E/F evidence。
- `Not Accepted — prerequisite or environment blocker`：無法建立必要 revision/PG/fixture/authorization authority；
  必須列出 exact blocker，不得猜 Accepted。

若只有舊Agnes V12 260/260 evidence，不能Accepted新B.AI route。implementation green、commit/CI、handoff文字
也都不能單獨 Accepted。

## 1. 唯讀與安全邊界

- 不修改 source/tests/migrations/prompts/docs/config；不 format；不產生或更新 frozen fixture/evidence；
- 不 stage/commit/push/merge/tag/PR；不 reset/checkout/clean/stash；不刪除 untracked；
- 不使用 subagent；不開始 P4-C；不驗收 broker/order/Risk擴權；
- 不讀或列舉 Keychain、`.env`、shell history、credential檔，不把 API key放入命令/log；
- 本 session沒有 exact live authorization時，禁止任何 provider/model/network POST；官方 docs的 read-only查閱不能替代
  product live evidence；
- 不看 raw provider prompt/response/Authorization；只接受 sanitized hashes/counts/codes/shapes；
- 不重送或修改已消耗 held-out split；不為了讓新模型過關調低 threshold或修 prompt。

允許在 temporary directory測試 CLI/config；驗收結束後不得刪使用者資料。不要讓 CLI觸及 production config path；
必須使用正式提供的 test-only config-root override或 isolated temporary HOME/XDG_CONFIG_HOME，並先證明不會讀真實
Keychain/network。

## 2. Revision、dirty worktree 與必讀資料

先執行：

```bash
cd /Users/zongen/Downloads/codex/trading
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -8 --oneline --decorate
git diff --stat
git diff --name-status
git diff --check
rg --files src/seven_lens tests migrations scripts docs | sort
```

記錄 exact HEAD＋所有 dirty/untracked files。工作樹含既有 P4-A/B changes時，區分本 work package與既有內容；不得
把整個 dirty tree都歸功於實作者。若重疊檔案無法判斷是否保留原內容，這是 acceptance uncertainty。

完整閱讀：`PROJECT_HANDOFF.md`、`PROGRESS.md`、README、roadmap、architecture、master plan、SECURITY、DECISIONS、
ISSUES、RISK_REGISTER、SOURCES、implementation prompt、所有實際改動 diff，以及 P3-E/F archived requirements。

以 `rg` 找 active hard-code，至少搜尋：

```bash
rg -n -g '*.py' -g '*.sql' -g '*.sh' -g '*.md' \
  'agnes-2\.5-flash|apihub\.agnes-ai\.com|p3e-agnes-2\.5-flash-only-v1|AGNES_API_KEY|Agnes' \
  src tests migrations scripts docs README.md
```

歷史 migration、archive、舊 evidence出現 Agnes是正常；active production config/transport/composition/eval仍不合理
寫死才是 finding。逐筆分類，不可單憑 rg有結果就 Reject或全部忽略。

## 3. Finding 標準

每個 finding 必須包含：ID、severity、file:line、被破壞 invariant、最小重現命令/PoC、expected、observed、影響、
缺少的 permanent test、必要修復範圍。

- High：credential可送到未驗證/private endpoint、secret/raw response洩漏、未授權 live call、model output繞過 strict
  parse/audit取得 authority、DB歷史破壞、P2/P4/broker擴權、held-out contamination/偽造 live evidence。
- Medium：兩指令不能可靠生效、atomic/race/symlink/permissions問題、route/model/audit identity不閉合、migration/
  ACL/down不安全、P3-E/F output/eval threshold錯誤、重要 regression或文件 gate truth錯誤。
- Low：不影響正確性/authority的可維護性或文件小誤差。Low不自動阻擋，但必須列。

`Accepted`要求無 High/Medium actionable findings。測試名稱或 implementation report不是證據；每個核心主張要有
source enforcement＋permanent test＋驗收者獨立 PoC，PostgreSQL主張必須有真實 PG16。

## 3A. 驗收者強制工作方式

你不得用「看起來合理」「測試很多」「implementation說已完成」作判定。每個 requirement都要填以下四格：

```text
REQ_ID: <R01..R24>
SOURCE_ENFORCEMENT: <file:line; actual condition/algorithm>
PERMANENT_TEST: <test file::test name; what it proves>
INDEPENDENT_EVIDENCE: <command/PoC and exact observed result>
STATUS: PASS | FAIL | NOT PROVEN | NOT APPLICABLE(with reason)
```

規則：

- `PASS`必須三種證據都存在；只有source或test其中一種是`NOT PROVEN`；
- `NOT APPLICABLE`只能用於確實不在本需求的項目，不能拿來跳過PG、P3-E/F、secret或live gate；
- implementation建立的test不是獨立PoC；你必須自己設計至少一個不同操作/輸入；
- 讀existing JSON evidence不是重新計算；必須從raw records/cases重算hash/metrics；
- 命令因環境失敗時保留stdout/stderr摘要、exit code、原因；不能把未跑寫PASS；
- 發現第一個High後仍要完成所有安全的唯讀檢查以界定blast radius，但不得修檔或發live call；
- 不得以Low名稱淡化會影響authority、credential、migration、replay或eval denominator的問題。

## 3B. 完整 requirement map

以下24項全部要出現在最終報告：

| ID | Requirement | PASS最低證據 | FAIL範例 |
|---|---|---|---|
| R01 | 只需兩個set命令 | isolated root黑箱執行＋readback | 還要手改env/JSON/source |
| R02 | endpoint canonical正確 | base與full endpoint exact | double path、query、redirect |
| R03 | target與generic model ID正確 | exact target readback/request/audit；slash-capable generic測試 | target漂移或`/`被拒／截斷 |
| R04 | atomic/CAS persistence | crash＋two-writer PoC、old bytes保留 | partial file/lost update |
| R05 | config file安全 | strict JSON、mode、symlink/size tests | corrupt file fallback default |
| R06 | deterministic route hash | independent canonical recompute | generation/time/path進route hash |
| R07 | load-once/new-run semantics | same process unchanged＋old-run mismatch | hot reload或old Agnes replay |
| R08 | endpoint SSRF防護 | URL table＋DNS public/private mix PoC | key可送localhost/private IP |
| R09 | TLS/route closure | numeric connect＋original SNI＋redirect reject | disabled verify或proxy env |
| R10 | fixed generic Keychain ref | fake call list exact one ref | Agnes/OpenAI/env fallback |
| R11 | secret/nonpayload leakage=0 | marker掃描repr/log/DB/config/evidence | header/body/key落地 |
| R12 | production one-attempt route | executor count、retry/fallback=0 | hidden retry/model fallback |
| R13 | strict B.AI protocol | documented allowlist＋negative matrix | blanket unknown ignore/JSON repair |
| R14 | route identity end-to-end | config→envelope→claim→audit exact | caller可per-request override |
| R15 | audit-before-authority | ordering/failure PoC | audit失敗仍return output |
| R16 | historical DB compatibility | legacy before/after hashes | 修改/刪除舊Agnes rows |
| R17 | PG constraints/ACL/concurrency | real PG16、SQLSTATE、two connections | 只用fake/SQLite或direct DML |
| R18 | safe down migration | new rows存在時down拒絕且不變 | truncate/delete/coerce rollback |
| R19 | P3-E六contracts offline | 每contract success+adversarial | 只測一種generic JSON |
| R20 | P3-E B.AI live | fresh exact 6/6、6 audit rows | 舊Agnes evidence或未授權call |
| R21 | P3-F new corpus isolation | new source-only split、V12 unchanged | 改V12/answers進prompt |
| R22 | P3-F offline correctness | frozen byte-match、完整分母 | 降case count/只讀summary |
| R23 | P3-F live quality/transport | 130+260 raw recompute、caps/thresholds | 排除timeout/無限retry |
| R24 | full regression/governance | focused/full/PG/diff＋truthful docs | implementation=Accepted或P4擴權 |

R01–R19、R21、R22、R24任一`FAIL/NOT PROVEN`均不得Accepted。R20或R23缺合規live evidence時，verdict只能
`Not Accepted — B.AI live evidence pending`；若existing evidence顯示contract/quality門檻確實失敗則是`Rejected`，
不是pending。

## 3C. Verdict決策表；不得自行發明第五種結論

按下列順序判定，第一個符合者就是verdict：

| 條件 | Verdict |
|---|---|
| 任一High/Medium，或R01–R19/R21/R22/R24任一FAIL | `Rejected` |
| prerequisite revision不明、PG16不可用、required fixture缺失且不能安全重建證據 | `Not Accepted — prerequisite or environment blocker` |
| offline/source/PG均PASS，但R20或R23沒有本route合規live evidence | `Not Accepted — B.AI live evidence pending` |
| 所有R01–R24 PASS，無High/Medium，所有required commands綠 | `Accepted` |

禁止的判定方式：

- 「Accepted except live」；這應是pending；
- 「conditionally Accepted」；不存在；
- Provider Transport RED但仍Accepted；本需求明確要求換模型後穩定輸出與transport門檻；
- 以模型免費、官方catalog存在或單一smoke成功推論R20/R23；
- 以implementation同session產生的summary代替fresh驗收；
- 因問題容易修就先忽略；未修的Medium仍是Rejected。

## 3D. 驗收執行順序與停止規則

依序執行，不可先跑live來「快速看看」：

1. A0 revision/diff/authority；
2. A1 static source mapping與hard-code分類；
3. A2 isolated CLI/config黑箱；
4. A3 fake network/secret/route/audit PoCs；
5. A4 focused permanent tests；
6. A5 real PG16 migration/ACL/concurrency；
7. A6 new P3-F offline corpus/hash/metrics；
8. A7 full regression與docs truth；
9. A8 existing live evidence驗證；只有使用者另授權才可親自live run。

任一步發現會讀真實Keychain/發POST而沒有授權，立即中止該命令並記High governance risk；繼續其他不需外部authority的
唯讀驗收。任何工具輸出疑似包含secret/raw response時，不要在報告複製內容，只記檔案、欄位種類與hash/count。

## 4. 兩個 CLI 指令的黑箱驗收

先確認正式 module/subcommands存在且 help不讀 config/key/network：

```bash
uv run --locked python -m seven_lens.cli.analysis_provider --help
uv run --locked python -m seven_lens.cli.analysis_provider set-endpoint --help
uv run --locked python -m seven_lens.cli.analysis_provider set-model --help
```

建立isolated root並執行exact黑箱流程；變數名稱不可使用`HOME`：

```bash
provider_test_root="$(mktemp -d /private/tmp/seven-lens-provider-acceptance.XXXXXX)"
XDG_CONFIG_HOME="$provider_test_root" \
  uv run --locked python -m seven_lens.cli.analysis_provider \
  set-endpoint https://api.b.ai/v1
XDG_CONFIG_HOME="$provider_test_root" \
  uv run --locked python -m seven_lens.cli.analysis_provider \
  set-model deepseek-v4-flash
XDG_CONFIG_HOME="$provider_test_root" \
  uv run --locked python -m seven_lens.cli.analysis_provider show
```

先從source證明上述commands只讀寫isolated XDG path且不碰Keychain/network，才可執行。不要在驗收中刪除temp directory；
最終回報其path，讓使用者可自行清理。

在新 temporary config root執行 exact兩指令；不能用真實 user config。核對：

- exit=0；stdout只有 bounded non-secret route/model/hash/generation；stderr無 key/path leak；
- persisted canonical base=`https://api.b.ai/v1`，derived full endpoint恰為
  `https://api.b.ai/v1/chat/completions`，沒有重複 `/chat/completions`；
- model恰為`deepseek-v4-flash`；新 process/readback使用 operator file；
- file是private regular file，非symlink，canonical strict JSON，config hash可重算；
- endpoint command不改 model、model command不改 endpoint；generation/CAS正確；
- runtime load-once：同 process已載入 snapshot後外部改檔不 hot reload；新 process才讀新值；
- help/show/validation與兩 set命令都不讀 Keychain、不發 network；
- package default或missing-file行為明確且 fail closed，不會生成任意 endpoint。

readback檔案exact fields只能是`base_url/generation/model_id/route_config_hash/schema_version`。獨立以canonical JSON重算
route hash；material要包含base/model及全部fixed安全policy，但不含generation、timestamp、檔案path。核對
`endpoint_policy_id=analysis-route-v1:<64hex>`。corrupt existing operator file必須CONFIG fail，不可退回Agnes default。

自行注入：並行 set-endpoint/set-model、kill/exception在 write/flush/replace前後、stale generation、read-only dir、
symlink file/dir、FIFO/device、oversize、empty、duplicate keys、NaN、unknown/missing fields、group/world writable。
任何 lost update、partial JSON、follow symlink或默默 fallback都是 blocker。

## 5. Endpoint／model validation 與 SSRF/TLS PoC

合法 endpoint只驗 exact B.AI base；另做 table-driven invalid inputs：HTTP、userinfo、query、fragment、explicit
non-443 port、empty/trailing-dot/Unicode-confusable host、localhost/`.local`、IPv4/IPv6 literals、127/8、0/8、10/8、
100.64/10、169.254/16、172.16/12、192.168/16、multicast/reserved/unspecified、encoded slash/backslash、`..`、`//`、
control、已含`/chat/completions`。所有錯誤固定、不回顯危險全文、不改 active config。

DNS/TLS independent fake PoC至少證明：

- DNS解析到private/link-local/loopback或public+private混合時，在 Authorization-bearing request前拒絕；
- connect使用已驗證public numeric address，但 TLS SNI/hostname仍是 configured host；
- redirect到同host/異host皆拒絕；proxy env不生效；certificate/hostname mismatch拒絕；
- final URL/host/path與 snapshot不一致拒絕；DNS failure/timeout taxonomy正確且無 secret leak。

target model `deepseek-v4-flash`及合法generic slash/colon ID必須接受。拒絕 empty、>128、space/control/non-ASCII、leading/trailing slash、`//`、`\`、
`.`/`..` segment、URL、query/fragment。model只能當 opaque ID，不能用作 filesystem path或URL。

## 6. Secret boundary 驗收

確認 endpoint/model CLI完全不碰 secret。production research composition只能讀：

```text
SecretKind.ANALYSIS_PROVIDER_API_KEY
service seven-lens.paper-trading.analysis-provider.api-key
account primary
```

驗 forged/tampered `SecretRef`、foreign refs、Agnes legacy ref、OpenAI/Alpaca/Postgres/Tavily refs全部拒絕；無 service alias、
無 fallback、無 env讀取。repr/str/pickle/errors/log/telemetry/audit/config file都不含 fake marker、service string或 secret。

除非有 live authorization，不可用真 Keychain驗證；fake provider即足以驗 boundary source correctness，但不能證明真 key存在。

## 7. Generic transport 與 B.AI protocol 驗收

從 source確認 production active path不再依賴 Agnes exact constants/class identity。provider-neutral transport仍強制：

- HTTPS、public DNS、TLS verify/SNI、direct/no proxy、no redirect；
- one POST/no hidden retry/no fallback；bounded connect/read/total deadline與bytes；
- exact three headers（若沒有另行批准，不能送 `HTTP-Referer`/`X-Title`）；
- fixed request keys、stream=false、temperature policy、no tools/files/state；
- strict JSON/content-type/status/error taxonomy；error/repr無 body/header/key。

用 fake raw responses逐一驗：200 exact shape、401/403 AUTH、408 TIMEOUT、429 RATE_LIMIT、5xx TRANSIENT、其他4xx
PERMANENT、redirect PROTOCOL、empty/oversize/wrong content-type、duplicate JSON/NaN、多choices、wrong index/role、tool call、
missing/extra outer/choice/message/usage keys、wrong usage arithmetic、non-stop/truncated finish、model mismatch、Markdown/
prose/malformed inner JSON。

對 B.AI可出現的 documented optional fields，必須是明列、bounded、非authority；不能 blanket ignore unknown fields。
response.model 必須exact current configured model，除非使用者批准且 config明列 alias。任何 runtime自動學 alias為 blocker。

## 8. Route identity、envelope、audit-before-authority

追蹤單一 immutable config snapshot如何流經：CLI file → loader → composition → envelope producer versions → request →
transport → claim → PostgreSQL audit → replay。每一層應 exact比對 provider/model/flavor/policy/config hash；caller不能單次
覆寫 endpoint/model。config/material任一位元改變應改 policy/config hash並造成舊 envelope/claim fail closed。

特別驗證既有 `call_id` domain沒有 provider/model：切換只適用新 process建立的新 run/input。用已有 Agnes claim模擬
route切換後 resume；必須 pre-network typed rejection，不得 replay舊 Agnes authority、不得用新 key重送相同 call，也不得
無 migration/ADR地改 call-id domain破壞歷史 idempotency。

自行 PoC：foreign provider、old Agnes model、right model/wrong policy、right policy/wrong config hash、route ordinal=2、
response model drift、audit persist failure、same call same metadata replay、same call different route collision、late deadline。
觀察 network count、audit rows與 output authority：所有不合法case必須 zero or bounded expected network、無 successful authority。

確認 sanitized envelope/prompt/citation/portfolio de-identification沒有被切換功能弱化，raw prompt/response仍不落地。

## 9. PostgreSQL 16 migration／ACL／歷史相容

確認實作者沒有修改舊 `0012`/`0013` bytes；新 migration編號在當時沒有衝突並已進 migration inventory/checksum。
讀 up/down/function/role verifier，不接受「刪除 CHECK」作為 generic化。

真實 PG16 independent tests/PoCs必須證明：

- clean up/down/up；migration version/checksum一致；zero unexpected skips；
- legacy Agnes claim/audit/reflection rows在 up後保持 exact values與可讀性；
- legacy `route_config_hash`只能由 fixed canonical legacy material deterministic backfill，不能取 current operator config；
- B.AI provider/model/policy/config hash row合法，`deepseek-v4-flash`及generic slash-capable model ID可保存/readback；
- malformed/overlong/control/path-traversal model與unknown provider/flavor/policy拒絕；
- runtime direct INSERT/UPDATE/DELETE/TRUNCATE/DDL、trigger disable、function replace、extra privilege皆拒絕；
- SECURITY DEFINER fixed search path/schema qualification/PUBLIC revoke；startup verifier遇 drift fail closed；
- same call concurrent claim只有一個 authority，different route collision拒絕；
- down在存在新-route rows時拒絕且 rows/count/hash不變；不能刪資料後假裝 rollback成功。

報告 SQLSTATE、before/after counts/hashes、兩連線結果。SQLite/fake repository不能支持以上結論。

## 10. P3-E 六種真實模型輸出驗收

先驗 permanent fake tests確實涵蓋：

1. Analyst report；2. Bull/Bear debate argument；3. Research conclusion；4. Trader plan；
5. Aggressive/Conservative/Neutral risk argument；6. Portfolio proposal。

live六case必須固定覆蓋：TECHNICAL analyst round0、BULL debate round1、Research Manager、Trader、AGGRESSIVE Risk
round1、Portfolio Manager。逐case核對case ID、stage/role/round、envelope hash、contract kind、network request ordinal、
audit call ID與SUCCESS/NONE。不得用同一response fixture或同一contract複製成六筆。

每種 output必須 exact contract parse＋identity/hash/symbol/round/citation/version closure；unknown/missing/extra、free text、
Markdown、tool call、wrong citation/route、NaN/duplicate key一律零 authority。audit success row必須先 durable後才return output。

新 B.AI live evidence若要支持 Accepted，必須是 current code/config/template上的 fresh 6-case synthetic run：exact 6
requests、retry=0、fallback=0、六種 contract都 parse+validate OK、六筆 authoritative audit route identity一致，且 evidence
不含 raw content。舊 `docs/P3E_LIVE_EVIDENCE_2026-08-24.json`只證明 Agnes historical route。

B.AI API reference雖把`response_format`列為Chat Completions可選參數，但在尚未以當前model/帳戶相容性證據確認前，
implementation維持prompt-only JSON是允許的；驗收重點是local strict parser與live穩定性門檻。禁止為了此model接受JSON
repair、code-fence抽取以外的新寬鬆路徑、missing-field defaults或prose fallback。

沒有符合第13節授權時不得自行補跑；verdict用 `Not Accepted — B.AI live evidence pending`。

## 11. P3-F corpus、offline與 live quality 驗收

確認 V12完全未被修改或重標。新 route必須有新的 source-only hash-closed split/report/authorization schema；檢查：

- fixture factory deterministic；case IDs/content/split/report/template/parser/config hashes閉合；
- training/dev/golden/held-out隔離，expected answers不進入 provider payload/prompt；
- route valid/invalid cases使用 current provider/model/policy/config hash；舊 Agnes是 invalid/tamper case而非 current；
- safety>=120、semantic traces>=20、memory>=60、每 configured role/stage route >=20 valid + >=10 invalid/ambiguous；
  normal/emergency均覆蓋；不得靠語義重複灌分母；
- 獨立重跑 offline frozen report byte-match，原有要求至少616/616或有未降低覆蓋的明確新分母；
- scripts/CLI選擇 exact current split，不會誤跑 V12後聲稱 B.AI passed。

從 raw records獨立重算 live 門檻，不能只信 summary：

- 130/130 invalid/ambiguous pre-network fail closed；
- 260 logical valid calls，strict completions >=250；completed正確率 >=98%；contract violations=0；
- first-attempt success >=95%；eventual within 3 >=99%；fallback=0；
- 每案最多2 retry，僅 TIMEOUT/TRANSIENT/RATE_LIMIT；2s/4s+jitter；連續3 exhausted circuit break；attempt<=780；
- authorization exact匹配 provider/model/base/full endpoint/config/split/prompt/parser/case IDs/cost/privacy/expiry/grant；
- latency p50/p95/max、logical/attempt/retry/exhausted/timeout分母可重算；失敗/ABSTAIN/timeout未被排除。

使用以下exact公式，並在報告顯示分子、分母、小數值與門檻：

```text
pre_network_rate = pre_network_reject_count / 130 = 1.0 required
strict_completion_rate = strict_completed_logical_cases / 260; numerator >= 250
completed_accuracy = correct_strict_completed_cases / strict_completed_logical_cases >= 0.98
first_attempt_success_rate = attempt_1_success_logical_cases / 260 >= 0.95
eventual_success_rate = success_within_3_attempts_logical_cases / 260 >= 0.99
contract_violation_count = parser-reached contract violations = 0 required
```

確認260個logical case ID唯一且每個最多三attempt；attempt ordinals連續1..N；總attempt<=780；retry records沒有變成新
logical cases。timeout/exhausted留在transport的260分母。ABSTAIN僅在expected typed answer確為ABSTAIN且contract正確時算correct；
empty/prose/refusal不能算ABSTAIN。若summary與你重算不同，至少Medium finding並以raw重算為準。

B.AI `deepseek-v4-flash`目前限時0 Credits，但rate limit、availability或方案變更導致 Provider Transport RED時，不得降低
門檻、無限重試或把 transport failure算 correctness success。live quality或 P3-E contract失敗即不能 Accepted。

## 12. 必跑 regression matrix

先用 `rg --files tests`確認實際檔案，必要時擴大受影響 selectors：

```bash
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run --locked pytest -q \
  tests/test_analysis_provider_config.py \
  tests/test_analysis_provider_cli.py \
  tests/test_chat_completions_transport.py \
  tests/test_analysis_provider_composition.py \
  tests/test_p3e_provider_config_and_secrets.py \
  tests/test_p3e_agnes_transport.py \
  tests/test_p3e_model_invoker.py \
  tests/test_p3e_route_closure.py \
  tests/test_p3e_agnes_providers.py \
  tests/test_p3e_envelope_and_prompt.py \
  tests/test_p3e_concurrency.py \
  tests/test_p3f_evals_corpus_and_runner.py \
  tests/test_p3f_evals_provider_seam.py \
  tests/test_p3e_model_audit.py \
  tests/test_p3e_repr_redaction.py \
  tests/test_secret_source_invariants.py

UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run --locked python -m seven_lens.evals offline \
  --fixtures tests/fixtures/p3f_evals_v13 \
  --frozen-report tests/fixtures/p3f_evals_v13/reports/offline-scripted-v13.json

UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh --postgres
git diff --check
git status --short --branch
git diff --stat
git diff --name-status
```

若實作者依supported-version衝突使用的正式新version不是V13，先從source/scripts/manifest解析exact current version並在報告
說明，不可盲跑V13或自行改fixture。記錄 exact pass/deselect/skip、PG16、Ruff format/check、mypy、lock、fixture/report
hashes。任何 required command失敗、PG unexpected skip、live test被普通suite意外執行，都阻擋 Accepted。

此外確認 paper-only/source secret invariants：沒有 Alpaca live endpoint、沒有 broker submit、沒有 model→Risk/order authority、
沒有 source/provider credential交叉讀取。

## 13. Live evidence 的授權邊界

本提示詞本身不授權 live call。若使用者希望驗收者親自補跑，必須在本 session另給 exact授權，至少包含：

- B.AI exact base/full endpoint、model、config/policy hash、Keychain ref並確認key已輪換；
- P3-E 6 exact synthetic case IDs/hashes、request=6、retry=0、fallback=0；
- P3-F fresh split/hash、390 case IDs、130 pre-network、260 logical、780 attempts cap；
- retry taxonomy/backoff/jitter/circuit breaker/180秒 timeout；
- cost cap（官方目前寫限時0 Credits仍需批准exact USD/Credits cap；非0計費或方案失效立即停止）、privacy/data fields、
  evidence path、expiry、stop conditions。

沒有完整授權：只驗 existing sanitized evidence，一次 POST也不能發。授權存在時先做 zero-network live-plan；hash/grant/
expiry/config任一 mismatch立即停止。P3-E未6/6不得開始P3-F。AUTH/PERMANENT/contract violation按 plan立即停止；不可讀 raw
response調 prompt，不可重送 consumed split。

## 14. Governance truth 與 scope exclusions

核對 current docs明確區分：

- historical Agnes P3-E/V12 evidence；
- generic switch implementation；
- current B.AI P3-E conformance；
- current P3-F offline/live quality；
- Provider Transport只是單批 snapshot，P6前仍需 rolling 7日/≥200 logical canary；
- P3重新驗收狀態與 P4-A/B狀態；
- 本變更沒有授權 P4-C、broker、Paper submit、live trading、commit/push。

不得讓 docs聲稱「模型可任意切換」卻沒有 endpoint credential-exfiltration防護，也不得把 old Agnes Closed文字直接當
new route acceptance。

## 14A. 最終證據表；缺欄即不得Accepted

最終回覆必須包含下列四張表，不能只寫敘述摘要。

表一：revision與變更歸屬

| 欄位 | 必填內容 |
|---|---|
| HEAD/origin | exact SHA |
| pre-existing dirty | 開始驗收前已存在的exact paths |
| provider-switch files | 本work package exact paths |
| overlapping dirty files | 如何證明舊內容未遺失 |
| forbidden-scope diff | P2/P4-C+/broker/order是否為0；若非0逐檔解釋 |

表二：測試證據

| Suite | Exact command | Passed | Failed | Deselected | Skipped | Duration/notes |
|---|---|---:|---:|---:|---:|---|
| focused config/CLI | ... | | | | | |
| focused transport/audit | ... | | | | | |
| P3-E contracts | ... | | | | | |
| P3-F offline | ... | | | | | |
| full non-integration | ... | | | | | |
| PG16 integration | ... | | | | | |

表三：P3-E live（若未授權，每欄寫NOT RUN與原因）

| Case | Stage/role/round | Contract | Request ordinal | Parse | Validate | Audit outcome | Error |
|---|---|---|---:|---|---|---|---|
| analyst | | | | | | | |
| debate | | | | | | | |
| manager | | | | | | | |
| trader | | | | | | | |
| risk | | | | | | | |
| portfolio | | | | | | | |

表四：P3-F live重算

| Metric | Numerator | Denominator | Observed | Required | PASS/FAIL |
|---|---:|---:|---:|---:|---|
| pre-network rejects | | 130 | | 130/130 | |
| strict completions | | 260 | | >=250 | |
| completed accuracy | | | | >=98% | |
| contract violations | | n/a | | 0 | |
| first attempt success | | 260 | | >=95% | |
| eventual success | | 260 | | >=99% | |
| attempts | | 780 cap | | <=780 | |
| fallback | | n/a | | 0 | |

另外列exact authorization/config/split/prompt/parser/grant/evidence hashes，但不得列secret或raw response hash以外的內容。

## 14B. `no actionable findings`的嚴格含義

只有同時符合下列條件才可寫`no actionable findings`：R01–R24全PASS、所有required commands已實際執行且綠、沒有
unexplained skip/deselect、沒有未分類active Agnes hard-code、沒有live evidence缺口、沒有scope或dirty-worktree不確定性。
若只是沒有找到source bug但live未授權，應寫「offline review無actionable source finding；live evidence pending」，不得寫
無條件`no actionable findings`或Accepted。

## 15. 最終回覆格式

```text
TARGET: Analysis Provider Switch + current-route P3-E/P3-F revalidation
DECISION: Accepted | Rejected | Not Accepted — B.AI live evidence pending | Not Accepted — prerequisite or environment blocker
REVISION: <exact HEAD + dirty/untracked scope>
ACTIVE_ROUTE_OBSERVED: <config source, base, full endpoint, model, provider, config hash; never secret>
```

依序列出：

1. findings（先 High/Medium；沒有則明確 `no actionable findings`）；
2. 兩指令黑箱結果與 atomic/config safety PoCs；
3. endpoint/model/SSRF/TLS/secret boundary；
4. route/envelope/audit authority closure；
5. migration/PG16/ACL/concurrency/history evidence；
6. P3-E六種 output contract及 live 6-case證據；
7. P3-F split/offline hashes與 live metrics全部精確分母；
8. full regression exact結果；
9. 未執行或無法重現的證據、scope exclusions、Gate state。

Rejected時只給最小 remediation邊界，不修。Pending時單一步驟是取得/執行合規 live evidence。Accepted時單一步驟是
由使用者決定是否 commit/push；驗收者不得自行發布。完成後停止。
