# Analysis Provider Switch — Detailed Implementation Prompt

把本文件完整、不節錄地交給負責實作的模型。這是一個獨立 remediation work package，不是新的 Phase，
也不授權模型自行呼叫外部 AI、讀取 Keychain、commit、push、開始 P4-C，或改動交易／Risk authority。

---

## 0. 唯一任務、目標介面與停止點

你要把 Seven-Lens 的分析 AI 從「Agnes endpoint/model 寫死在多層程式與資料庫」改成：操作者只需執行
下面兩個終端指令，就能持久地設定下一次新啟動之分析 provider stack 使用的 endpoint 與 model：

```bash
cd /Users/zongen/Downloads/codex/trading
uv run --locked python -m seven_lens.cli.analysis_provider set-endpoint https://api.b.ai/v1
uv run --locked python -m seven_lens.cli.analysis_provider set-model deepseek-v4-flash
```

規格中的 endpoint 是 **base URL**。對 `CHAT_COMPLETIONS` flavor，程式必須唯一、可預期地組成：

```text
https://api.b.ai/v1/chat/completions
```

不可要求操作者再改 source、環境變數、JSON、migration 或測試檔。可以另外提供唯讀 `show`／`validate`
子命令，但上述兩個 set 指令必須已足以完成 endpoint＋model 的切換。

完成 offline/fake/PG 驗證後先停止，回報等待 live authorization。只有使用者在實作當下 session 另行提供
第 13 節所列的完整精確授權，才可讀 Keychain 並執行 P3-E／P3-F live model tests。即使所有測試通過，
也只能寫：

```text
Implementation completed; pending fresh independent acceptance
```

不得自行宣稱 Accepted／Closed。

## 1. 目前架構事實；開始前必須由 source 重新確認

目前 checkout 的權威根目錄是 `/Users/zongen/Downloads/codex/trading`。開始前完整閱讀：

- `PROJECT_HANDOFF.md`、`PROGRESS.md`、`README.md`、`docs/ROADMAP_AND_ACCEPTANCE.md`；
- `docs/ARCHITECTURE.md`、`docs/MASTER_PLAN.md`、`SECURITY.md`、`DECISIONS.md`、`ISSUES.md`、
  `RISK_REGISTER.md`、`docs/SOURCES.md`；
- `docs/archive/prompts/P3E_IMPLEMENTATION_PROMPT.md`、`docs/archive/prompts/P3E_ACCEPTANCE_PROMPT.md`、
  P3-F implementation/acceptance archive；
- 本提示詞及配對的 `ANALYSIS_PROVIDER_SWITCH_ACCEPTANCE_PROMPT.md`。

不要相信本文列出的檔名就是完整清單；用 `rg` 重建引用圖。當前已知的硬編碼面至少包括：

- `src/seven_lens/config/provider.py`：`ProviderKind.AGNES`、`AgnesProviderConfig`、exact host/path/model/policy；
- `src/seven_lens/infrastructure/agnes_transport.py`：class、raw request、DNS/TLS、response parser 全部綁 Agnes；
- `src/seven_lens/infrastructure/agnes_providers.py`；
- `src/seven_lens/application/p3e_composition.py`；
- `src/seven_lens/application/model_invoker.py`：`AGNES_MODEL_ID`、provider version 與 route validation；
- `src/seven_lens/analysis/model_audit.py`：provider/model/policy exact validation；
- `src/seven_lens/analysis/model_envelope.py`、`pipeline.py`、`proposal_pipeline.py` 的 producer versions；
- `migrations/0012_p3e_provider_audit_up.sql`：provider/model/policy CHECK；
- `migrations/0013_p3f_reflection_memory_up.sql`：model ID regex 目前不接受 `/`；
- `src/seven_lens/evals/provider_eval.py`、`production_probes.py`、`fixture_factory.py`、eval CLI 與 shell scripts；
- P3-E/P3-F unit、integration、live tests，以及 V12 frozen fixtures／authorization／evidence schema；
- active governance docs 中所有「Agnes-only」current-state claim。

目前 P3 已 Closed；這次修改會改變 P3-E route identity 與 P3-F live-quality evidence applicability，因此只能
把「新 route 的重新驗收」標成 Open／pending，不能抹除或改寫舊 Agnes V12 歷史證據。

## 2. 開始前快照與 dirty-worktree 規則

先執行並保存輸出：

```bash
cd /Users/zongen/Downloads/codex/trading
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git diff --stat
git diff --name-status
git diff --check
rg --files src/seven_lens tests migrations scripts docs | sort
```

目前工作樹可能有大量 P4-A/P4-B source、migration、tests 與 docs 的 dirty/untracked changes。它們全部屬於
使用者：不得 reset、checkout、clean、stash、刪除、覆蓋或回復；不得把它們誤報成本輪新增。若本工作包必須
修改一個已有 dirty change 的檔案，先讀完整 diff，做最小相容修改，最後逐檔檢查沒有遺失既有內容。

不得 stage、commit、push、merge、tag、建立 PR。不得使用 subagent。不得順手修不相關 P4 問題。

## 3. 非目標與絕對邊界

本工作包只能改分析 provider configuration、transport/composition、model-call audit identity、P3-E/F eval wiring、
必要 migration、tests 與同步治理文件。不得：

- 改 P2 order/fill/reconciliation/control、Alpaca endpoint、broker credentials 或 submit authority；
- 實作 P4-C～F、Risk limits、universe、quantity、`OrderIntent`、Paper submit 或 live trading；
- 給 model tools、files、shell、DB、broker、callback 或任意額外 network capability；
- 儲存或輸出 API key、Authorization header、raw prompt、raw model response、account/broker identity；
- 加入 fallback model、自動 model discovery、`/models` runtime discovery、silent retry、redirect、proxy/env trust；
- 以 JSON repair、regex extraction、寬鬆 unknown-field ignore、自由文字 fallback 讓錯誤輸出取得 authority；
- 修改舊 migration `0012`／`0013` 的 bytes 或 checksums；必須用新的 additive migration；
- 修改、刪除或重送已消耗的 P3-F held-out split/evidence；
- 因 B.AI 目前限時0 Credits就推論privacy、availability、rate limit、未來價格或永久可靠性已通過。

## 3A. 弱模型強制執行協議

以下規則優先於你自己的偏好。不得省略、合併或自行替換：

1. 每次只做一個工作包；先讀相關 source與tests，再新增 failing tests，再做最小實作，再跑該包 tests。
2. 同一工作包 tests未綠，不得開始下一包；先列出第一個 root cause，不可同時大改多層「碰碰運氣」。
3. 不准用全域 search/replace把 `Agnes`全部改名；archive、舊 migration、舊 evidence必須保留歷史字樣。
4. 不准刪除 validation/check constraint來讓 B.AI通過；每個 exact literal都要改成新的 bounded invariant。
5. 不准看到既有測試失敗就修改 expected value；先證明舊 expected已被本核准需求取代。
6. 不准自行增加依賴。標準庫足夠；若認為必須新增 dependency，停止並先說明必要性、版本、風險與替代方案。
7. 不准以 mock/fake結果宣稱真實 PostgreSQL、Keychain、B.AI或模型品質已通過。
8. 不准建立「暫時寬鬆 parser」後承諾以後收緊；authority path從第一版就必須 fail closed。
9. 每個錯誤只輸出固定錯誤碼與固定訊息，不回顯未驗證 URL/model/body/header/exception。
10. 如果本文與當前 source衝突，先按第3B節停止條件分類；不可默默選一邊。

每完成一個工作包，在你的工作紀錄保留下列四行；最終回覆要彙總，不必建立額外檔案：

```text
WORK_PACKAGE: <ID/name>
FILES_TOUCHED: <exact paths>
TEST_COMMAND: <exact command>
RESULT: <passed/failed count, first failure if any>
```

## 3B. 必須立即停止並向使用者回報的條件

遇到任一條件，停止修改，不進下一包：

- 無法區分本工作包與既有 P4 dirty changes，或必須覆蓋他人未理解的修改；
- migration編號已被占用、migration inventory與檔案不一致，或舊 `0012`/`0013` checksum已漂移；
- 需要改 P2、broker、Risk、OrderIntent或P4-C～F才能讓功能運作；
- 官方 B.AI API不再提供指定model，endpoint/flavor已改變，或限時0 Credits方案已結束／帳戶實際計費非0；
- B.AI documented response schema與strict parser需要接受一個本文沒有批准、會影響authority的欄位/語意；
- live response的`model`不是exact requested model；不得自行建立alias；
- 發現 API key、Authorization、raw prompt/response已被寫入檔案、DB、log或terminal output；先停止並只回報位置/類型，
  不複製秘密內容；
- PostgreSQL migration/ACL測試破壞既有資料、無法安全down，或PG16 required suite有unexpected skip；
- 需要讀真實Keychain、發POST或建立live evidence，但沒有第13節當次精確授權；
- P3-E任一六案例 contract失敗；不得繼續P3-F；
- P3-F held-out已被看過後又要調prompt/parser；該split已消耗，必須停止並要求新的source-only split策略；
- full regression顯示與本工作包無關的既有失敗，且無法用before/after證明不是本輪造成。

停止回報固定格式：

```text
STOPPED_AT: <work package and command>
CAUSE: <one concrete cause>
EVIDENCE: <file:line or sanitized command result>
SAFE_STATE: <what was and was not changed; network/keychain/db status>
NEEDED_DECISION: <one exact user decision or authority>
```

## 3C. 固定檔案責任表

優先使用下列 exact新檔名；不要自行發明第二套平行架構。若現有檔案已提供等價generic abstraction，先證明後才可
復用，最終回覆列出一對一映射。

| 責任 | 固定目標檔案 | 允許內容 | 禁止內容 |
|---|---|---|---|
| typed route config + loader | `src/seven_lens/config/analysis_provider.py` | strict schema、URL/model validation、hash、atomic store | HTTP client、Keychain、DB |
| operator CLI | `src/seven_lens/cli/analysis_provider.py` | argparse、兩個set與唯讀show/validate | API key、network、provider call |
| generic transport | `src/seven_lens/infrastructure/chat_completions_transport.py` | DNS/TLS/HTTP、strict response parser | business contracts、DB、broker |
| thin provider adapters | `src/seven_lens/infrastructure/analysis_providers.py` | P3-C/D port adapter | route discovery、fallback |
| composition | `src/seven_lens/application/analysis_provider_composition.py` | load-once config、scoped secret、transport、audit wiring | arbitrary caller URL/model |
| legacy compatibility | 原 `agnes_*.py`、`p3e_composition.py` | 必要時只re-export/deprecation shim供舊tests/import | active production hard-code或第二條route |
| audit contracts | `src/seven_lens/analysis/model_audit.py` | generic bounded provider/model/policy/config hash closure | 放寬成任意字串 |
| invocation | `src/seven_lens/application/model_invoker.py` | config-driven exact route validation、audit-before-authority | per-request override/retry |
| secret identity | `src/seven_lens/security/secret_values.py`與composition tests | fixed generic Keychain ref | env/alias/fallback |
| migration | 下一個未占用 `migrations/NNNN_*_{up,down}.sql` | additive constraints/functions/columns | 改舊migration、刪歷史row |
| eval | `src/seven_lens/evals/*.py`、exact scripts、新fixture dir | current-route config hash、new split/evidence schema | 改V12、暴露held-out answers |
| tests | 固定新增下列四檔＋所有affected P3-E/F/PG tests | fake/offline/live-guarded evidence | ordinary suite讀真key/network |

`config/provider.py`可保留 enums/common types，但 active route建構必須委派新的generic config。若保留舊class/function，
其docstring與命名必須明示 legacy package default，不能讓 production composition繼續呼叫它。

固定新增的test檔：

- `tests/test_analysis_provider_config.py`：schema/hash/store/permissions/symlink/concurrency；
- `tests/test_analysis_provider_cli.py`：subprocess black-box兩命令與exit/output；
- `tests/test_chat_completions_transport.py`：URL/DNS/TLS/request/response/status/deadline；
- `tests/test_analysis_provider_composition.py`：secret scope/load-once/config→audit wiring。

不要把所有內容塞進一個千行test檔；也不要刪除現有P3-E/F tests來降低回歸覆蓋。可更新舊test imports/expected route，
但必須保留其原始安全意圖。

## 3D. 固定 operator JSON schema與hash算法

operator檔案只保存可由兩個CLI命令安全修改的非秘密欄位，不把安全policy變成可任意編輯設定。exact schema：

```json
{
  "base_url": "https://api.b.ai/v1",
  "generation": 2,
  "model_id": "deepseek-v4-flash",
  "route_config_hash": "<64 lowercase hex>",
  "schema_version": "seven-lens.analysis-provider-config.v1"
}
```

JSON輸出使用UTF-8、sorted keys、separators `(',', ':')`、結尾一個LF；禁止BOM、duplicate keys、NaN/Infinity與extra
fields。`generation`是1..2^63-1的int，bool不算int；每次真正改值才+1，設定成相同canonical值不得假裝新route。

package-owned不可由operator修改的固定policy material：

```text
api_flavor=CHAT_COMPLETIONS
provider_kind=OPENAI_COMPATIBLE
policy_schema=seven-lens.analysis-route-policy.v1
connect_timeout_ms=2000
read_timeout_ms=180000
total_timeout_ms=180000
request_byte_cap=131072
response_byte_cap=131072
max_output_tokens=8192
temperature=0.0
stream=false
tools=false
state=false
files=false
follow_redirects=false
trust_env=false
proxy=false
automatic_retry=false
fallback_model_id=null
fallback_attempts=0
```

`route_config_hash`算法固定如下，不能包含generation、檔案路徑或時間：

1. 建立一個dict，恰含 canonical `base_url`、`model_id`及上列全部fixed policy material；
2. 使用UTF-8、`ensure_ascii=False`、`allow_nan=False`、sorted keys、compact separators序列化；
3. 計算SHA-256 lowercase hex；
4. `endpoint_policy_id = "analysis-route-v1:" + route_config_hash`；
5. loader重算並constant-time比較，不符即CONFIG fail closed。

runtime `AnalysisProviderConfig`包含operator fields、derived full endpoint、fixed policy與derived hashes；必須是frozen、slots、
post-init完整重驗。operator檔案不存在時，使用package-owned Agnes base/model作為generation=0的`PACKAGE_DEFAULT`，但仍
建立同一generic config type；兩個set命令以此default補齊另一欄。operator檔案存在但損壞時絕不fallback到default。

## 3E. 兩個命令的逐步算法

兩個subcommand共用同一個 `_update_config(field, raw_value)` 路徑，流程順序不可改：

1. resolve production/test config root；拒絕relative root、symlink component、unsafe permission；
2. acquire同目錄exclusive lock；lock本身必須regular/private/non-symlink；
3. 在lock內重新讀 current config，避免read-before-lock lost update；缺檔才使用package default；
4. 只validate本次raw field，產生canonical value；不要先寫檔；
5. 複製current values並只替換指定field；另一field byte-for-byte/canonical-value不變；
6. 若值沒變，保持generation與hash，輸出`changed=false`；
7. 若值改變，generation+1，重算route hash/policy，建構完整`AnalysisProviderConfig`再驗一次；
8. 在同目錄以exclusive create建立private temp regular file；寫canonical bytes、flush、fsync；
9. 再檢查target parent/file沒有被換成symlink；atomic `os.replace`；fsync parent directory（平台支援時）；
10. 釋放lock；stdout輸出一行canonical bounded JSON summary，不輸出HOME、temp path、secret；
11. 任一步失敗：固定nonzero exit、target舊bytes不變、清除本次temp（只能清楚識別本次建立者）、不發network。

固定成功summary至少包含：`changed`、`config_source=OPERATOR_FILE`、`generation`、`base_url`、`full_endpoint`、
`model_id`、`route_config_hash`、`restart_required=true`。失敗summary不要包含raw input。

## 4. 設定儲存與兩指令契約

實作一個 provider-neutral 的 frozen `AnalysisProviderConfig`；class名稱固定，不要自行改名或繼續以 Agnes 表示
generic route。runtime設定至少包含：

- schema/version；API flavor 固定 `CHAT_COMPLETIONS`；
- base scheme、host、base path；derived exact chat-completions path/full URL；
- exact model ID；provider identity；由 canonical route material 算出的 policy ID/hash；
- connect/read/total timeout、request/response byte caps、max output tokens、temperature；
- stream/tools/state/files/redirect/trust-env/proxy/automatic-retry/fallback flags；
- immutable config generation 或 content hash，供 audit、eval authorization 與 diagnostics 綁定。

### 4.1 持久化位置

使用單一、明確、非秘密、可測試覆寫根目錄的 operator config file；建議採 XDG config convention，production
預設例如：

```text
${XDG_CONFIG_HOME:-$HOME/.config}/seven-lens/analysis-provider.json
```

測試必須透過 constructor/explicit test-only path 注入 temporary root，不讀寫真實 HOME。不可用 `.env`、shell
profile、Git tracked mutable JSON 或 command-line API key。production path resolution 不得跟隨 symlink、不得接受
非 regular file、group/world-writable file/directory、超大檔、duplicate JSON keys、NaN/Infinity、unknown/missing fields。

寫入使用同目錄 temporary regular file、private mode、flush/fsync（在平台可用時）、atomic replace；失敗不得留下
半寫設定。並行兩個 writer 必須 lock 或 CAS generation，不能 lost update。CLI stdout/stderr 不得包含秘密。

### 4.2 `set-endpoint`

輸入是 base URL，不是任意 request URL。接受範例：`https://api.b.ai/v1`。必須拒絕：

- HTTP、userinfo、query、fragment、空 host、trailing dot、explicit non-443 port；
- IP literal、localhost、`.local`、loopback/link-local/private/multicast/unspecified/reserved target；
- encoded slash/backslash、dot segments、double slash、control/Unicode confusable host、超長 host/path；
- 已含 `/chat/completions` 時產生重複 path；規格要選擇「拒絕 full endpoint」並給固定錯誤，不要猜測；
- DNS rebinding：production executor仍須 pre-resolve，逐個 address 驗證為 public，並以原 host 做 TLS SNI/hostname
  verification；不得因設定變成 caller-controlled DNS 就失去現有防護。

CLI 成功後只印 bounded canonical base URL、derived endpoint、config hash/generation 與「new processes only」。

### 4.3 `set-model`

必須接受`deepseek-v4-flash`。為了之後仍能使用兩個CLI命令切換其他相容模型，model ID規則至少允許ASCII
alphanumeric、`.`、`_`、`-`、
`:`、單一分隔 `/`；總長 1..128，每 segment 非空，不接受 `//`、leading/trailing slash、whitespace、control、
backslash、URL、query/fragment、path traversal。不可把 model ID 當 filesystem path。

CLI 成功後只印 bounded model ID、config hash/generation 與「new processes only」。

### 4.4 lifecycle

runtime composition 每次 process/startup 只載入一次完整 validated snapshot，不 hot reload。兩個 set 指令之間若
啟動 process，最多得到一個完整但可能 endpoint/model 尚未配對的 snapshot；任何 AUTH/PERMANENT/identity mismatch
必須 fail closed，不能 fallback。文件明確要求完成兩個 set 指令後才重啟分析 worker。

切換只適用切換後建立的 **新 analysis run/input identity**。現有 P3-E `call_id` derivation不包含 provider/model；
不得因此讓同一個既有 call在新 route下重送，也不得 replay舊 Agnes output當成新 model結果。若 resumed/in-progress
run的 stored claim route與 current config不同，必須在 network前以 typed route-mismatch拒絕，要求操作者建立新 run；
不要在本 work package偷偷改既有 call-id domain，除非先完成獨立 ADR、historical compatibility與 migration批准。

提供 package-owned default bootstrap（目前 exact Agnes base/model、generation=0）；不得把「檔案不存在」變成其他
route。default必須完整通過相同 validation，且 `show` 清楚標示 source=`PACKAGE_DEFAULT`或`OPERATOR_FILE`。operator
檔案存在但invalid時禁止fallback default。

## 5. Secret 與 provider identity

endpoint/model 指令絕對不能接收、讀取或修改 API key。建立 generic typed identity：

```text
ANALYSIS_PROVIDER_API_KEY
service: seven-lens.paper-trading.analysis-provider.api-key
account: primary
```

只允許這一個 exact Keychain ref 進 research provider scope。不要 fallback 到 Agnes/OpenAI/Alpaca/Tavily key，
不要自動複製舊 key。更新 Keychain provisioning 文件與 fake tests；使用者會在 live test 前自行把 B.AI key
放進上述 canonical service。永遠不要要求 key 貼到聊天、env、CLI argument、source 或 fixture。

保留舊 `AGNES_API_KEY` service/enum供歷史相容，但新的active composition不得讀它；不得刪除、alias或fallback。

新增並固定使用`ProviderKind.OPENAI_COMPATIBLE`表達generic active route，不能永遠偽稱 Agnes。歷史 PostgreSQL rows仍
保留`AGNES`。新 provider/model/policy identity 必須進 claim/audit/evidence，且 config hash改變時route identity必須改變。

## 6. Generic Chat Completions transport

把 Agnes-specific implementation 拆成或改名為 provider-neutral OpenAI-compatible transport；必要時保留薄的
legacy compatibility import，但 production composition、docstrings、errors、types 不得再謊稱 Agnes-only。

實作前重新查官方資料，不依賴本提示詞的舊snapshot。2026-08-28唯讀查核起點：

- B.AI API reference：`https://docs.b.ai/llmservice/api/`；明列production base URL
  `https://api.b.ai/v1`，支援OpenAI-compatible Chat Completions，full endpoint為
  `https://api.b.ai/v1/chat/completions`；
- B.AI model details：`https://docs.b.ai/llmservice/models/deepseek-v4-flash/`；明列exact model ID
  `deepseek-v4-flash`、text-only、1,000,000-token context、384,000 max output；
- B.AI pricing：`https://docs.b.ai/llmservice/pricing-and-usage/`；2026-08-28文件說明此模型目前在B.AI Chat與API
  限時0 Credits，但也列出非促銷期間的standard reference pricing，實際資格、結束時間與最終計費以平台為準。

這些只支持wire/config設計，不是privacy、可用率、帳戶實際0費用或output contract通過證據；不得在未授權情況下
呼叫model或credential-bound`GET /models`。若實作時官方資料已不同，列出drift並停止請使用者決定，不可自動換model。

保留並泛化現有安全性：

- one POST per attempt；TLS certificate＋hostname verify；原 host SNI；no redirects；
- direct connection only；no proxy、no `trust_env`、no cookies/state；
- bounded separate DNS/connect/read/total deadlines；deadline 在 call 前後均檢查；
- request/response byte caps；strict `application/json`；duplicate JSON keys/NaN/Infinity 拒絕；
- `Authorization: Bearer <SecretValue>`、`Content-Type`、`Accept` only。B.AI官方也支援`x-api-key`，但本工作包固定
  使用Bearer且不得同時送兩種credential header，除非使用者另行批准；
- request body固定 `model/messages/max_tokens/temperature/stream` 與既有 response_format policy；不開 tools/files/state；
- status taxonomy維持 CONFIG/AUTH/PERMANENT/RATE_LIMIT/TRANSIENT/TIMEOUT/PROTOCOL/SCHEMA/OVERSIZE/AUDIT；
- exceptions/repr/telemetry 只含 bounded enums/counts/hashes，無 host? host/base URL是非秘密可記 policy hash，
  但不要記 header/body/key。

response parser 不得寫成「接受任何 OpenAI-like JSON」。根據official B.AI Chat Completions schema建立exact
required keys＋明列 optional keys；未知 top-level/choice/message/usage 欄位預設 fail closed。`choices` 必須恰一筆、
index=0、assistant content 是單一 strict JSON object、finish reason 可接受集合需明列，非完整輸出不得 authority。

回應中的 `model` 預設必須等於 requested configured model。若 authorized live observation 顯示 B.AI回傳不同
canonical slug，停止並回報 exact sanitized shape；不可自行放寬成任意 alias。需要 alias policy時另取得使用者批准，
並以 config 中 exact bounded accepted response IDs 實作，不能從回應自動學習。

B.AI的DeepSeek V4 Flash可能產生reasoning，但application authority只取`content`的strict JSON；
reasoning 欄位不得保存或取得 authority。`reasoning_requested`／`reasoning_effective` 必須保持證據誠實：沒有明確送參數
與驗證就用 UNKNOWN，不因 model catalog 宣稱推理就寫 MAX。

## 7. Envelope、invoker、provider adapters 與 output contracts

保留 P3-C/D 的 13 個 logical roles、stage ordering、deadlines、sanitized envelope、prompt hashes、citation closure、
audit-before-authority、zero fallback 與 production no-hidden-retry。把以下 Agnes constants 改成 config snapshot 驅動：

- envelope producer/provider/model versions；
- `validate_*_route` 對 configured model/provider/policy 的 closure；
- `ModelCallClaim`/`ModelCallAuditRecord` validation；
- analysis/proposal adapter 與 composition stack types/names；
- replay collision identity。

不能只刪掉 exact checks。正確作法是把 expected route identity（provider、model、API flavor、policy ID/config hash）
從同一 immutable config snapshot傳到 envelope、claim、transport、audit，再逐層 exact 比對。caller 不得在單次 request
覆寫 endpoint/model。route ordinal仍為1，fallback=0。

必須重跑六種 P3-E output contract：

1. `ANALYST_REPORT`；
2. `DEBATE_ARGUMENT`；
3. `RESEARCH_CONCLUSION`；
4. `TRADER_PLAN`；
5. `RISK_ARGUMENT`；
6. `PORTFOLIO_PROPOSAL`。

每種都要 strict parse＋`_validate_output` identity/citation/version closure；任何 Markdown、prose、missing/extra field、
wrong symbol/hash/citation/round、NaN、duplicate key、tool call、多 choice、truncated finish reason 都 fail closed且零 authority。

## 8. PostgreSQL additive migration 與歷史相容

不要修改 `0012` 或 `0013`。先檢查目前最高 migration number 與 dirty/untracked migrations；以「當時下一個未占用」
編號新增 up/down migration（目前目視可能是 `0022`，但不得盲信）。

新 migration 必須：

- 保留所有既有 Agnes rows bytes/meaning/append-only history；
- 將 `model_call_claims`/`model_call_audits` 的 provider/model/policy CHECK 從單一 Agnes literal改成 bounded format＋
  cross-field policy，而非取消約束；
- model ID接受`deepseek-v4-flash`；provider、policy ID、hash有exact bounds；
- 在 claims/audits新增exact `route_config_hash TEXT`欄位；legacy Agnes rows以已知 canonical legacy material
  deterministic backfill，不能用 current operator config回填歷史。新 claim/audit的 provider/model/policy/config hash
  必須全數相符；
- SQL claim/persist/readback functions仍拒絕 null、unknown API flavor、route ordinal !=1、identity collision；
- 若 P3-F reflection/memory schema會保存新 model ID，新增 constraint migration讓 `/`合法，同時拒絕 whitespace、
  controls、path traversal與過長值；
- runtime role仍無 direct INSERT/UPDATE/DELETE/TRUNCATE/DDL；只可執行 exact SECURITY DEFINER functions；
- functions fixed `search_path`、schema-qualified、PUBLIC revoked；startup verifier/checksum inventory同步；
- up/down/up成功。down migration若存在新 provider rows，必須 fail closed並給明確 SQLSTATE/訊息，不可截斷、刪除或
  假裝可回到 Agnes-only。

增加真實PostgreSQL 16 tests：legacy Agnes row survives migration；new B.AI `deepseek-v4-flash` claim/audit成功；不同route
metadata相同 call_id collision拒絕；direct DML/constraint bypass/extra privilege拒絕；concurrent claim only one authority；
down-with-new-row拒絕且資料不變；clean database up/down/up。

## 9. P3-E live conformance 重建

把 `tests/integration/test_p3e_live_provider.py` 泛化為 current configured route，不再把 Agnes literal寫死。離線 unit test
仍不得讀 Keychain/network。live test必須有獨立 request counter，exact 6 request cap、zero retry/fallback，使用 fresh
de-identified synthetic envelopes，並核對六個 output contracts、audit rows與 route config hash。

live evidence建立新 schema/version/file；保留舊 Agnes evidence不改。新 evidence只可含：provider/model/policy/config
hash、case IDs/envelope hashes、timestamps/latency/token counts、outcome/error code、strict parse/validate結果、request
count與 sanitized response shape。不得含 raw prompt/response/header/key/reasoning text。

六個live cases固定如下；不要只挑容易成功的role，也不要用一個generic schema代替：

| Case ID | Stage | Role/round | Required output contract | PASS條件 |
|---|---|---|---|---|
| `P3E-CURRENT-ANALYST` | `ANALYST` | `TECHNICAL`, round 0 | `ANALYST_REPORT` | exact parse、input/symbol/citations closure |
| `P3E-CURRENT-DEBATE` | `INVESTMENT_DEBATE` | `BULL`, round 1 | `DEBATE_ARGUMENT` | side/round/packet/evidence closure |
| `P3E-CURRENT-MANAGER` | `RESEARCH_MANAGER` | manager, round 0 | `RESEARCH_CONCLUSION` | bundle/claims/conclusion closure |
| `P3E-CURRENT-TRADER` | `TRADER` | trader, round 0 | `TRADER_PLAN` | plan identity/citations/targets contract |
| `P3E-CURRENT-RISK` | `RISK_DEBATE` | `AGGRESSIVE`, round 1 | `RISK_ARGUMENT` | viewpoint/round/proposal snapshot closure |
| `P3E-CURRENT-PORTFOLIO` | `PORTFOLIO_MANAGER` | manager, round 0 | `PORTFOLIO_PROPOSAL` | exact requests/weights/reasons/citations closure |

case payload必須由現有typed fixture builders建立fresh timestamps/deadlines，不能手寫簡化JSON繞過production prompt builder。
每case恰一POST；前一case完成strict parse＋audit persist後才進下一case。保留case order便於fail-fast與證據重算。

2026-08-28 B.AI API reference把`response_format`列為Chat Completions可選參數，但沒有證明每個model/帳戶都支援相同
JSON Schema能力。先用fake transport驗request shape；只有當時官方model文件與已授權live conformance都證明可用時才啟用。
否則維持現有prompt-requested JSON＋local strict parser。若模型無法穩定遵守contract，正確結果是P3-E/P3-F不通過，
不是JSON repair、Markdown抽取、補欄位或降低驗收門檻。

## 10. P3-F offline/live eval 重建

route change使 V12 的 Agnes route literals、authorization policy與 live evidence不能直接證明新 route。不得修改 V12
或把舊260/260改標成B.AI。建立新的source-only split/version（例如下一個V13，實際名稱依allowlist與
現況），並遵守：

- deterministic factory從公開 synthetic source產生；training/dev/golden/held-out hash closed；
- expected answers永不送 provider；held-out answer不進 prompt/tuning；
- route cases使用 current config identity/config hash，invalid cases涵蓋 foreign provider/model/policy/hash；
- offline scripted report 616/616（或若 case count因必要 route matrix改變，完整說明分母；不可降低原覆蓋）；
- parser/prompt change必須重算 template/split/report hashes；舊 artifacts append-only保留；
- `run_p3f_offline_evals.sh` 與 `run_p3f_live_evals.sh` 明確選 current split，不靠模糊 glob；
- live authorization schema綁 exact provider/model/base/full endpoint policy/config hash、split hash、prompt/parser IDs、
  390 cases、260 logical requests、最多780 attempts、每案最多2 retry、2s/4s＋deterministic jitter、連續3案 exhausted
  circuit breaker、180秒 attempt timeout、fallback=0；
- production P3-E transport仍 one attempt/no retry；P3-F orchestrator只對 synthetic eval 的 TIMEOUT/TRANSIENT/
  RATE_LIMIT做顯式、逐attempt audited retry；不得把 retry帶入正常分析 pipeline。

Live Model Quality門檻不得降低：

- invalid/ambiguous `130/130` pre-network fail closed；
- 260 logical valid calls中至少250 strict completions；
- completed-case accuracy >=98%；response-contract violations=0；
- Provider Transport first-attempt >=95%、eventual-within-three >=99%；
- fallback=0；attempt<=780；所有分母、retry/exhausted/timeout、p50/p95/max均可重算。

metrics公式固定，不能另選分母：

```text
pre_network_rate = pre_network_reject_count / 130
strict_completion_rate = strict_completed_logical_cases / 260
completed_accuracy = correct_strict_completed_cases / strict_completed_logical_cases
first_attempt_success_rate = logical_cases_succeeded_on_attempt_1 / 260
eventual_success_rate = logical_cases_succeeded_within_attempts_1_to_3 / 260
contract_violation_count = count(provider responses that reached parser but violated response contract)
```

transport timeout/exhaustion留在260分母；不可從accuracy/transport分母刪除。只有strict completed case進accuracy分子/分母，
但必須另外滿足strict_completed>=250。每個logical case最多一個final decision；retry不能當新case灌分母。ABSTAIN只有expected
answer也是ABSTAIN且strict contract完全正確時才算correct；provider拒答/prose/empty不等於typed ABSTAIN。

B.AI限時0 Credits模型的rate limit/availability可能造成transport gate RED；這不允許降低quality門檻、排除失敗case或
無限重試，也不得因促銷期間結束而自動切換模型。

## 11. 必寫 permanent tests

至少涵蓋：

- CLI help/exit codes、exact two commands、atomic writes、concurrent writers、crash/no partial file；
- config missing/default/operator source、strict JSON、permissions/symlink/size/unknown/missing/duplicate fields；
- endpoint canonicalization與所有 SSRF/URL adversarial cases；model slash/colon及 traversal/control adversarial cases；
- config hash/generation變更、runtime load-once、caller per-request override不存在；
- Keychain generic exact ref/scope、foreign secret denial、no fallback、repr/log/telemetry leakage；
- generic transport DNS/TLS/SNI/public-address revalidation、redirect/proxy/status/timeout/oversize/content-type；
- B.AI request shape、optional response fields allowlist、unknown fields、model mismatch、usage/reasoning shape；
- 六個 P3-E model output contracts strict parse/validate、audit-before-authority、Postgres route identity；
- P3-F offline byte-match、anti-contamination、authorization tamper、route/config mismatch pre-network rejection、retry caps；
- paper-only/source/secret invariants及所有受影響 P3-B/C/D/E/F regressions。

測試不得依賴真實 HOME/Keychain/network，除明確 `@pytest.mark.live` 且有多重 opt-in guard者。沒有 opt-in時 live tests
必須明確 deselect/skip並報原因，不能在一般 suite偷偷連線。

## 11A. 固定工作包順序與每包完成條件

不得跳號。WP0～WP9是offline implementation；WP10是需另授權的live checkpoint。

### WP0 — Inventory與before baseline（只讀）

操作：

1. 執行第2節快照命令；
2. 用 `rg`列出active Agnes literals及imports；
3. 用 `git diff -- <path>`檢查每個預計修改且已dirty檔；
4. 跑現有P3-E/F focused tests與offline V12，取得before結果；不要先改檔。

輸出：一張`literal → current owner → future owner → historical/active`清單，以及before pass/fail counts。

完成條件：知道所有active production/composition/audit/eval入口；任何baseline failure已分類。否則停止。

### WP1 — Config model、strict loader與atomic store

先新增tests，再實作第3D節exact schema/hash/default/permissions/symlink/bounds。此包不得新增CLI、transport或Keychain。

最少測試：valid default、valid operator file、hash mismatch、duplicate/unknown/missing fields、bool generation、unsafe mode、
file/parent symlink、FIFO、oversize、non-UTF8、BOM、NaN、concurrent read/write、operator corrupt no-default-fallback。

完成條件：new config tests全綠；現有config/secret tests仍綠；network/keychain call counters皆0。

### WP2 — CLI兩指令

實作第3E節共用update path。使用temporary config roots做subprocess black-box tests。不得用mock直接呼叫private function取代CLI。

最少測試：exact兩命令、任一先執行、same value no generation bump、另一欄不變、lost-update race、write/replace crash、
read-only dir、help/show不寫檔、固定exit codes、stdout JSON無secret/path。

完成條件：從package default開始依序執行本文兩命令，readback exact B.AI route；不需第三個寫入命令。

### WP3 — Secret與generic composition skeleton

新增generic secret kind/service與scope；建立generic config-driven composition，但transport先使用fake。保留舊secret歷史，
active composition不可讀它。測fake secret backend call list恰一個generic ref；foreign/tampered全部拒絕。

完成條件：composition只接收typed config/audit/executor/clock，不接受base_url/model/API key字串；fake stack能建立，真Keychain=0。

### WP4 — Generic transport

先把既有Agnes transport security tests抽成provider-neutral matrix，再實作generic raw request/executor/parser。不要同時改audit或eval。

測試至少分成：URL/DNS、TLS/SNI、request shape、status taxonomy、response outer shape、choice/message/usage、inner contract
content、deadline/oversize、repr/redaction。每個negative test要assert executor/network count與error code。

完成條件：B.AI fake exact response成功；所有negative cases fail closed；production retry=0/fallback=0；舊transport安全
regression仍綠或由一對一generic tests取代並在final report說明。

### WP5 — Envelope／invoker／provider adapters／audit contracts

由同一config snapshot建立producer versions、claim與transport；新增`route_config_hash` closure。不要刪route validation。
測new run成功、old run/new route pre-network拒絕、same route replay、different route collision、audit persist failure零authority。

完成條件：六種output contracts在fake transport全部成功；每種至少一個malformed與identity-drift case失敗；audit-before-
authority可由call ordering test證明。

### WP6 — Additive PostgreSQL migration

先讀migration runner/checksum/role verifier。建立下一個未占用migration與integration tests；不可先改舊migration。

順序：clean up → insert legacy fixture → apply new up → verify legacy hashes → insert/read B.AI row → ACL/adversarial/concurrency
→ attempted down with new row must reject/no change → clean DB up/down/up。每一步記SQLSTATE與counts。

完成條件：focused PG16 zero skip；runtime無direct DML/DDL；legacy history不變；`deepseek-v4-flash`與generic slash-capable
model validation均合法；route hash closure由DB強制。

### WP7 — P3-E current-route conformance harness

先只泛化live harness與permanent fake tests；不要發POST。建立新的evidence schema，request counter固定6，六case順序固定。
`@pytest.mark.live`之外的collection/import/help不得載入Keychain或建立socket。

完成條件：六case fake success＋六種failure injection全綠；沒有live flags時明確skip/deselect；舊evidence未改。

### WP8 — P3-F new source-only split與offline report

由fixture factory產生下一版，不複製後手改JSON。先更新supported-version allowlist，再一次產生完整training/dev/golden/
held-out/manifest/report；產生後不得為了測試通過手改fixture。重新從source產生第二份temporary corpus，逐檔hash比對
determinism。確認provider payload builder無法import/readanswers loader。

完成條件：offline全數通過、frozen report byte-match、V12 bytes/hash unchanged、case counts不低於既有門檻、scripts exact指向
new version、authorization tamper/config mismatch均pre-network拒絕。

### WP9 — Full regression與governance

依第12節跑focused→offline→full non-integration→PG16→diff checks。修復只能限本工作包root cause；每次修復重跑受影響
focused後再重跑full。同步docs但不改舊歷史證據。

完成條件：全部required commands綠；若無live授權，狀態固定`offline completed, live authorization pending`並停止。

### WP10 — Authorized B.AI P3-E/F live evidence

只有第13節exact授權後可開始。不得把先前對話、本文或API key已存在當授權。先把canonical live plan完整顯示給使用者並
取得Yes；接著執行zero-network plan validation。P3-E 6/6後才可P3-F。任何停止條件立即保存sanitized partial evidence並停止。

完成條件：P3-E六contract 6/6；P3-F達第10節全部門檻；request/attempt/cost/privacy不超授權；仍只標pending acceptance。

## 11B. Test assertion最低標準

每個安全測試不能只寫`raises Exception`。至少assert：

- exact exception type或typed error code；
- network/keychain/DB write call count；
- target config bytes、audit rows、authority result在失敗前後是否不變；
- error/repr/log不含fake secret、raw input、Authorization或response marker；
- 成功時exact canonical value/hash/count，不用`is not None`等弱斷言；
- race tests使用兩個真正獨立process/connection（依被驗層級），不是同一fake object循序呼叫；
- live guard tests patch socket/Keychain constructor為fail-if-called，證明pre-network/pre-secret拒絕。

若測試需要sleep來猜race，改用barrier/event/advisory lock建立deterministic interleaving；不得靠不穩定timing。

## 12. Offline／static／PG 驗證順序

先用 `rg --files tests` 建立實際 selector，不得原樣執行 placeholder：

```bash
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run --locked pytest -q \
  tests/test_p3e_provider_config_and_secrets.py \
  tests/test_p3e_agnes_transport.py \
  tests/test_p3e_model_invoker.py \
  tests/test_p3e_route_closure.py \
  tests/test_p3e_agnes_providers.py \
  tests/test_p3e_envelope_and_prompt.py \
  tests/test_p3f_evals_corpus_and_runner.py \
  tests/test_p3f_evals_provider_seam.py \
  tests/test_analysis_provider_config.py \
  tests/test_analysis_provider_cli.py \
  tests/test_chat_completions_transport.py \
  tests/test_analysis_provider_composition.py

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

記錄 exact pass/deselect/skip、PG16版本、Ruff format/check、mypy、uv lock、offline case count與所有 hashes。PG必須
zero unexpected skip。若完整 PG 因環境資源失敗，先區分 product failure與runner/OOM；不可把未跑完寫成綠。

## 13. Live checkpoint：沒有這份新授權就停止

本文不是 live call 授權。完成第12節後，向使用者提供 canonical plan並等待當下 session 的 exact Yes。授權至少包含：

- provider=`B.AI`、base URL=`https://api.b.ai/v1`、full endpoint=`https://api.b.ai/v1/chat/completions`、
  model=`deepseek-v4-flash`、config/policy hash；
- exact Keychain ref（不含 value），且使用者確認已換成B.AI key；
- P3-E 6 synthetic/de-identified case IDs/hashes，request cap=6、retry=0、fallback=0；
- P3-F fresh split hash、390 case IDs、130 pre-network rejects、260 logical request cap、780 attempt cap；
- retry codes、每案2 retries、2s/4s+jitter、circuit breaker、attempt timeout=180s；
- cost cap：B.AI文件目前顯示限時0 Credits，但使用者仍須批准exact USD/Credits cap；實際產生任何非0計費、促銷失效
  或帳戶不適用時立即停止；
- privacy：只送 synthetic/de-identified data；不送portfolio/account/order/raw source/secret；
- evidence path、expiry、stop conditions（AUTH、PERMANENT、contract violation、cap、cost、grant/hash mismatch等）。

取得授權後順序固定：先 `live-plan` zero-network驗證 → P3-E 6 cases → 檢查六種 contract/audit → P3-F fresh live run。
P3-E失敗時停止，不進P3-F。任何 response-contract violation先保存 sanitized evidence並停止，不讀/輸出 raw response，
不針對 held-out調 prompt。AUTH/PERMANENT不重試；P3-F only按已授權 retry policy。

## 14. 文件同步與 Gate truth

更新 current docs、commands、Keychain service、route/evidence狀態、risk/issues/decisions/operations/sources。必須清楚分開：

- 舊 Agnes V12 historical Accepted evidence；
- 新 generic configuration implementation evidence；
- 新B.AI P3-E conformance；
- 新B.AI P3-F Offline Correctness、Live Model Quality、Provider Transport snapshot；
- P6前仍需 rolling 7-day/≥200 logical synthetic canary；
- P4與broker/order gate完全未因換模型而擴權。

若只有offline完成，寫`B.AI live evidence pending`。若live完成，仍寫`pending fresh independent acceptance`。
不得由實作者重關 P3 gate。

## 15. Definition of Done

只有同時滿足以下才算 implementation completed：

1. 兩個指定CLI指令可持久、原子、安全地設定base endpoint與`deepseek-v4-flash`，並保留generic slash model ID能力；
   新process使用exact snapshot；
2. generic transport/composition/audit不再有 active Agnes hard-code，且安全不變式未弱化；
3. additive migration保留舊 rows，接受新 identity，真實 PG16 authority/concurrency/up-down-up全綠；
4. 六種 P3-E contracts及新 P3-F offline eval全綠；
5. 若有當次 exact授權，新 route P3-E/P3-F live門檻均有 sanitized可重算證據；若無則誠實停在 pending；
6. full non-integration/static/PG regression全綠、diff clean of whitespace、所有既有 dirty work被保留；
7. governance文件沒有把 implementation、舊證據或單批 transport snapshot誤寫成 fresh acceptance。

## 16. 最終回覆格式

```text
RESULT: implementation completed | offline completed, live authorization pending | partial | blocked
REVISION: <HEAD + exact dirty/untracked scope>
COMMANDS:
  <exact set-endpoint command>
  <exact set-model command>
ACTIVE_ROUTE: <base, full endpoint, model, provider, config hash; no secret>
```

接著依序列：改動檔案與責任、保留的安全不變式、CLI/atomic config證據、migration/PG證據、P3-E六案例、P3-F
offline/live metrics、full regression、未執行的 live/Keychain/network、findings/remaining risks、Gate state。

最後單一步驟：把 `ANALYSIS_PROVIDER_SWITCH_ACCEPTANCE_PROMPT.md` 交給未參與實作的 fresh model。完成後停止。
