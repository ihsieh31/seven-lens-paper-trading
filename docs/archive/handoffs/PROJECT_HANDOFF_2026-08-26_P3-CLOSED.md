# P3-D／E／F Current Handoff

最後更新：2026-08-26
專案：`/Users/zongen/Downloads/codex/trading`

## 1. 目前唯一狀態

**P3-A/B/C/D/E/F 全部Accepted；P3 Combined Gate Closed。發布commit `d51e9a9`，exact-SHA CI run
`32962320231`的`quality-unit`與`postgres-integration`兩required jobs均成功。下一個Gate：P4（Not started，
需使用者另行授權）。**

- P3-F於2026-08-26由獨立重新驗收判定Accepted（F-A1 remediation六步深度驗證含紅→綠重注入全過；
  targeted 423、non-integration 1299、PG16整合217/0-skip、V12 evidence離線重算260/260＋0 violations＋
  130/130 fail-closed與全部hash閉合；自建對抗PoC A 53/53、B 11/11），隨後依使用者授權完成P3 Combined
  closure：工作樹以`b59e466`登載，postgres-integration因service tmpfs 512m不足失敗後由`d51e9a9`
  提升至本地驗證的1g並全綠（run `32962320231`）。詳見`WORKLOG.md`同日紀錄。

- P3-B 經最新獨立重新驗收判定 Accepted。
- P3-C 首輪 Rejected 後完成 R1～R6；R6 已由新的獨立session重新驗收為 Accepted，P3-B+C
  Combined Gate Closed。
- P3-B+C已發布於commit `55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`；本機與遠端
  `main`一致。exact-SHA GitHub Actions run `32558983841`的`quality-unit`與
  `postgres-integration`均成功。
- P3-D工作包已依`P3D_IMPLEMENTATION_PROMPT.md`完成D0～D7實作：`ResearchBundle`、
  deterministic coordinator、`ProposalContext`／兩輪三方Risk Debate、versioned
  `PortfolioProposal`、initial／retry pipeline、InMemory＋PostgreSQL authority與migration
  `0011`。全程`ScriptedProposalProvider` fake-only、止於proposal、未動P4邊界。
- 2026-08-22獨立驗收判定Rejected（F-01～F-04＋L1～L3）。Remediation session完成
  R1（focus ordered比對）、R2（retry時序下界雙層）、R3（proposal integrity重驗＋一律
  reload）、R5-L1（duplicate JSON key拒絕）與R5-L2（deadline µs邊界永久測試），各項均有
  修前失敗PoC與永久regression。R4與R5-L3當時因session內Mimosa security write-interceptor
  誤判而blocked；後續session（同日，使用者直接授權）已完成：R4＝`verify_runtime_role()`
  新增PUBLIC零權利檢查（28表×7種privilege＋11個P3 API函數）與public schema精確inventory
  比對（67函數＝31專案＋36 pgcrypto、28表），真實PG16 PoC三類drift修前ACCEPTED→修後
  DETECTED，並新增四條永久drift regressions；R5-L3＝`attempt_two_exists(bundle_id,
  context_id)`快速失敗閘門，不同refreshed snapshot的第三次retry零新row、same-hash冪等
  replay不變。
- 2026-08-22獨立驗收（fresh session，未參與實作／修復）判定**Accepted**：所有必要source/permanent test/adversarial/real-PG16證據完整，無High/Medium blocker（見`WORKLOG.md`驗收紀錄）。狀態為`P3-D Accepted`；工作包未stage／commit／push，`HEAD`仍為`55c9a16`，待授權發布。
- 2026-08-24依使用者明確允許的單session反覆審核＋修復重新驗收；補強P3-C persisted
  authority、canonical JSON/UUID、snapshot時間與nested contract、敏感文字、wall-clock deadline、
  migration ACL/up-down-up，以及同／不同hash、重複bundle、attempt-2與terminal兩連線競態。最終
  P3-B/C+D focused `104 passed`、non-integration `893 passed, 155 deselected`、真實PG16
  `141 passed, 14 deselected, 0 skipped`，Ruff／format／mypy／`git diff --check`全綠；兩次
  最終read-only source refresh均無High/Medium blocker。此證據取代較弱模型的舊驗收數字。
- P3-E E0～E8 fake範圍已完成：固定`agnes-2.5-flash` Chat Completions單一路由、無fallback／retry、
  exact Keychain ref、capability-minimal composition、sanitized typed envelope、strict prompt/output、append-only
  model audit、4／2／3 bounded barriers及migration `0012`。本session補修camel/Unicode/URI/IP/path敏感
  繞過、typed source/prior closure、P3-D retry previous-context、route version與repr capability leakage。
- 最新證據：P3-B/C/D/E focused `368 passed`；`verify_p1.sh`為Ruff／format／mypy全綠且
  non-integration `1158 passed, 162 deselected`；真實PG16 `148 passed, 14 deselected, 0 skipped`；
  `git diff --check`通過。P3-D在P3-E orchestration改動後維持Accepted。
- 使用者已批准P3-E固定Agnes route的六個synthetic／de-identified案例、最多六次POST、無費用上限，
  並確認了解Agnes非ZDR；`tests/integration/test_p3e_live_provider.py`與
  `scripts/run_p3e_live_acceptance.sh`現以三個exact opt-in flags、六次executor硬上限、失敗即停及PG16
  audit實作此checkpoint。case builder `1 passed, 1 live deselected`；affected P3-D/E `338 passed,
  1 live deselected`；真實PG16 `149 passed, 15 deselected, 0 skipped`。目前POST仍為0/6；Keychain
  項目存在，但其非敏感metadata不能證明值已rotation，因此不得使用或寫Accepted。該P3-E checkpoint
  當時P3-F依固定Gate順序尚未開始；後續P3-F狀態以本節下一點為準。
- 2026-08-25 P3-F offline checkpoint已完成：修復near-now promotion把有效的`requested_as_of`
  誤判為stale，以及P3-F regression／strict-mypy缺口；Ruff／format／mypy全綠，non-integration
  `1286 passed, 219 deselected`，真實PostgreSQL 16 `204 passed, 15 deselected, 0 skipped`，frozen
  offline eval `616/616`通過（report `a202e28c06dbc930d39171d8610e6795fca3ea371dc0be15793307a964a88c9`）。
  `P3-F offline implementation completed; real eval evidence pending`。未讀取Keychain、未呼叫provider、
  未產生POST；下一個且唯一的實作邊界是使用者針對exact frozen 390-case／260-POST、synthetic-only、
  no-retry、stop-on-first-error、Agnes無可驗證單價之no-fee-cap scope的本批次明確授權。
- 2026-08-25已依使用者本批明確授權執行該exact live batch（plan
  `708da0b7f3830a309414f78c79308d2664a95a4dc15895943c1e0be078da6a95`；payload root
  `748147b617e757d66d68d48d0a0ac1871c01d84f36da4ee2b4cd1c5f4c5c8fc1`）。真實Agnes／Keychain
  路徑在4/260 POST後因第4次`RESPONSE_CONTRACT`失敗而按規則停止；前3次`STRICTLY_PARSED`，130個
  invalid/ambiguous cases均為pre-network fail-closed。sanitized local-only evidence hash為
  `287661a504594358eb109a5138927034daddfbf49a588728cc1994f6e90ecc84`，audit root為
  `9acf17cba6b584cc391fcfbadeacf9c14b742914f9fdc91abd18d08d6e21f9f3`；不保存／不讀取raw response。
  **P3-F未完成，不能Accepted。** 已觀察的held-out split不得依此失敗調prompt或重送；若授權修復，必須
  先建立新split version、重跑offline、取得新的exact live authorization。
- 2026-08-25已完成**僅限 `RESPONSE_CONTRACT`** 的 remediation：診斷只使用sanitized
  `RESPONSE_CONTRACT` 結果、source與offline synthetic/dev/golden tests，未讀取或保存raw provider
  response。根因是現有 Agnes request 僅以文字提示要求strict JSON，沒有把parser所需的literal
  response contract 放進每個synthetic request；修復將immutable `response_contract`（exact keys、禁止
  extra properties與case/route/citation/reason literal constants）加入payload與固定prompt，parser升為
  `p3f-strict-route-decision-v4`。未修改、刪除、重跑或用於調校舊v3 held-out／plan／failed evidence；
  新generator只從source建構且拒絕既存destination，建立獨立的`p3f-synthetic-v4` split，hash為
  `99a429c897e912b580da797d279ca1f1d77fca8dabb86920ed6e0c04196c1cd2`，frozen offline report為
  `2140568d3ae9c91a7cb8531badaf287469aee4f1102ecd298ad56ff5a4ded200`（616/616）。驗證：Ruff／format／
  mypy全綠；non-integration `1288 passed, 219 deselected`（1273全套加15個P3-F provider seam）；真實
  PostgreSQL 16 `204 passed, 15 deselected, 0 skipped`。本次未讀Keychain、未呼叫provider、未送POST、未做
  P4、未commit/push。**P3-F仍未Accepted：下一步只能是對v4的全新live authorization**，其中必須逐項明確
  授權 Agnes 2.5 Flash、390個新的held-out synthetic route cases、260 POST hard cap／130 pre-network
  rejects、timeout／request-response byte cap／privacy與cost policy、零自動retry／零fallback、first-error
  stop，以及新的trusted plan/grant hashes。
- 2026-08-26依新的v4 explicit live authorization完成zero-network plan後執行：config hash
  `63574b65e00fc0867382c3c71f853f62f35e5aa63107694b9bce3d2847d21221`、plan hash
  `131b765a8673a9a882ed86412b9e8d48000da89ae90715756ddae34bbab9a10b`。一次初始grant-SHA不符在
  Keychain／POST之前被拒；正確grant後，production Agnes／Keychain path在130個pre-network rejects後，
  第1/260 POST以`TRANSIENT` transport failure停止，沒有response hash，retry/fallback均為0。sanitized
  local-only evidence hash為`ff0983925ff6f497c6241025632c27f9140749e9bc035e9cc34e83f9d1eb557e`，audit root為
  `43ead74174a15a0a535e76190d546cd9d9aa04636a04e40aac5ec3e7a55bf871`；未讀取或保存raw provider response。
  此非`RESPONSE_CONTRACT`，不在已授權remediation範圍內；**P3-F仍未Accepted，未做自動重試，任何再送POST
  必須取得新的明確live authorization。**
- 2026-08-26使用者授權持續修復及重試後，v5將transport policy由read 30秒／total 45秒提升為已授權的
  180秒，並以新的v6 source-only split取代已觀察v5；v6 split hash為
  `f3556ba5e31263dc4e4d163bfb59e7de8a059e33708b13ccc9d01625092f7099`，offline report為
  `d6a0816d386f7db93cfd58ebe70e890f7826ff6351bcea7fe94a7c0bd20f8f73`。v6 Ruff／format／mypy、
  non-integration `1289 passed, 219 deselected`及真實PostgreSQL 16 `204 passed, 15 deselected, 0 skipped`
  均通過。v5 live在73 strict parses後第74/260 POST `TIMEOUT`停止（evidence
  `83206f46c40aaca532085ce919308b4a6023c8c9e5bc8b9156b4f73126b0e742`）；v6 180秒 policy下仍在第1/260
  POST `TRANSIENT`停止（evidence `1142cf7508badae6904a7f6b4b24d2e0a901b39f965fe3da5d657c2cbe5e2d1c`）。
  三份evidence都只含sanitized hash／code，未讀raw response。這是重複的外部provider transport可用性阻塞，
  並非可由source安全修復的`RESPONSE_CONTRACT` defect；維持zero automatic retry／fallback，P3-F不能Accepted。
- 2026-08-26先以**不發HTTP request、不讀response body**的DNS／TCP／TLS診斷確認
  `apihub.agnes-ai.com:443`可完成TLS 1.3握手；這只證明網路傳輸可達，不能當作model服務可用性證明。
  其後建立不覆寫舊split／plan／evidence的source-only `p3f-synthetic-v7`：split hash為
  `87c312b6c9171282850a7721988c77263cb7494cc698ca48eab6d0def6b4eb4c`，offline report為
  `a5f1c8a5200143814f003403733fca36cb6f73cd8ae2632710623c9000c3bc30`（616/616）。V7的zero-network
  plan已驗證390個synthetic held-out route cases、130個pre-network rejects、260 POST cap、180秒
  deadline、zero retry／fallback。完整live batch的sanitized evidence為
  `9524ca74b7b364c21fe809e1f483a8f4ddf2d7d28eec98f06e923f4116afd50b`，audit root為
  `c85e357811198fbbc54e439639660b9be41daab6f51b3e3955158d6b585ecb78`：130個pre-network rejects與
  34個`STRICTLY_PARSED`後，第35個POST為`TIMEOUT`（180秒）；0 retry／0 fallback，剩餘225個依
  fail-fast未執行。僅讀取sanitized hash／計數／code，未保存或人工讀取raw provider response。
  V7已消耗，**P3-F仍不能Accepted**；若要再試，須保留V7並以新split（V8或之後）取得新的explicit
  live authorization，限定synthetic-only、390／260、timeout、no-fee-cap、0 retry／0 fallback與
  stop-on-first-error；不得重送V7。
- 2026-08-26使用者明確授權後，V8再以source-only factory建立，未覆寫V3～V7：split hash為
  `49b90420a75bd3b2736633e5e4863c0c2c45e5d2d17fe3c5d4269f36faae9180`，offline report為
  `f920ff57e0999b3132c399c00d812102fa50869505afd09e2301e654f45e7590`（616/616）。Ruff／format／mypy、
  non-integration `1289 passed, 219 deselected`與真實PostgreSQL 16 `204 passed, 15 deselected, 0 skipped`
  全綠；zero-network plan再次驗證390／130／260、180秒及zero retry／fallback。V8 live evidence為
  `56f15353ac160f815af123d4f42745c5357be8265ea4b06ca534af0311542abf`，audit root為
  `89132c1ebc06f17387c420054f85049abc1de0ca7cef0b77c352893daba6b60a`：28個`STRICTLY_PARSED`後，
  第29/260 POST為`TIMEOUT`，0 retry／0 fallback，231個未執行。只讀sanitized hash／計數／code，未保存
  或人工讀取raw provider response。**V8已消耗，P3-F仍不能Accepted**；下一次若授權，必須使用新的V9+
  split與新的exact live authorization，絕不可重送V8。
- 2026-08-26再以source-only生成V9：split hash為
  `58541fa7262c6bfb2e9706e3efe8496206e566ad3e46d00e1567e64dd25043a9`，offline report為
  `7ff2d49e24958f456a217b0becae8c61d81c13d0452d83dea192b65ec029c72e`（616/616）。Ruff／format／mypy、
  non-integration `1289 passed, 219 deselected`與真實PostgreSQL 16 `204 passed, 15 deselected, 0 skipped`
  全綠；zero-network plan再次驗證390／130／260、180秒及0 retry／0 fallback。
  V9未發POST、未讀Keychain、未寫trusted grant；其zero-retry／first-error policy已由ADR-033取代，因此封存為
  historical offline evidence，不再作live batch，也不覆寫或重送V1～V9。
- 2026-08-26依使用者決策完成ADR-033 Gate redesign：P3-F功能拆為Offline Correctness與Live Model Quality，
  Provider Transport另為會隨時間變動的P6 readiness Gate。V10 source-only split hash為
  `237620d1faefaa797f16a4c5e784ef113491cbaa8859a88977dae9c19c56ae63`，offline report v2為
  `aea1b77c94e2482b62b0fc40209f216f7629fa77a719679ce1008c3489622c38`（616/616）。Live policy只對
  `TIMEOUT`／`TRANSIENT`／`RATE_LIMIT`每案最多retry兩次，2s／4s backoff＋deterministic jitter；260 logical
  requests的attempt cap為780，連續三案耗盡retry即開circuit，fallback=0。Live Model Quality門檻為至少
  250/260 strict completions、completed正確率>=98%、response-contract violations=0及130/130 pre-network
  fail-closed；Transport另報first-attempt>=95%、eventual<=3 attempts>=99%。focused provider/corpus tests
  `33 passed`；`verify_p1.sh`全綠（Ruff／format／mypy 177 source files，non-integration `1295 passed,
  219 deselected`）。本次未讀Keychain、未發POST，且未因無DB變更而冒充重跑PG16；**P3-F仍未Accepted，
  下一步需V10新的exact live authorization。**

- 2026-08-26依使用者明確授權執行V10 live batch。事前全部重跑：P3-F targeted `120 passed`；
  `verify_p1.sh`全綠（non-integration `1295 passed, 219 deselected`）；真實PostgreSQL 16
  `204 passed, 15 deselected, 0 skipped`；`git diff --check`通過。zero-network plan（config hash
  `26cecbbe2c30cdcd5e2cf048e96a441990490df439ddb4df05b9b0a827bb79ec`、plan hash
  `4edb1994bcbcc31289271c8e828ee149b7dd77f364836fc13746ee521a835da0`）再次驗證390 cases／130
  pre-network rejects／260 POST cap／780 attempt cap、180秒、只對`TIMEOUT`／`TRANSIENT`／`RATE_LIMIT`
  每案2 retries、backoff＋jitter、三案exhausted circuit breaker、0 fallback。授權一次通過後，
  production Agnes／Keychain path在130個pre-network rejects後連續11次`STRICTLY_PARSED`且11/11
  正確（p50約2.6秒、max 8.1秒、零timeout、零retry）；第12/260 POST
  （case `p3f.v10.route.analyst.technical_analyst.11`）為`RESPONSE_CONTRACT`，屬非可重試錯誤，
  依政策停止並寫出sanitized evidence。token使用量13,376 prompt＋3,167 completion；sanitized
  local-only evidence hash為`fb005d83ec08d1cbcc0e8c4d483b2fd3f46278822b445bd85fccac277666d72a`，
  audit root為`42d6f031da37b369e5948ff06d115a6a70975dcc4fca9f2054bac66cb0bf45ba`；未讀取或保存raw
  response。**V10已消耗，P3-F仍不能Accepted**（violations=0門檻下本批已不可能過quality gate）。
  診斷僅使用sanitized evidence、source與committed fixtures：失敗case與siblings結構完全相同；
  P3-E live路徑已驗收「單一exact JSON code fence normalization」（Agnes實際會偶發輸出fence），
  但P3-F `StrictLiveDecisionParser`直接`json.loads`無此處理，疑似根因。若授權修復，必須先建立
  新split version（V11或之後）、重跑offline，並取得該批新的exact live authorization。

- 2026-08-26依使用者授權完成RESPONSE_CONTRACT remediation並建立V11。`StrictLiveDecisionParser`
  升為`p3f-strict-route-decision-v5`：僅新增「恰好一組完整```json fence才剝除」的單一exact
  normalization（與P3-E live路徑已驗收語意一致）；大小寫變體、缺換行、prose外圍、雙fence與fenced
  內容的duplicate key／語意closure檢查全部維持fail closed，並新增永久regression（含executor端到端
  fenced回應案例）。source-only `p3f-synthetic-v11` split hash為
  `ee8141b042921ee457aec98ca542a5d055e9e9bf201044cb38dc3e9324c0a24d`，frozen offline report為
  `3a92f8dcc67fec00fe87496e0ab40709990a792aa1c2f8c89ea9a77bf884bc4a`（616/616）。evals腳本、活躍
  prompt與requirement map已切至V11；corpus allowlist新增v11。V1～V10保持immutable不重送。
  本次未讀Keychain、未發POST。

- 2026-08-26依使用者授權執行V11 live batch（plan hash
  `946cefae2d040f2da848062b292299980630a13e23e24992433f599e178e0362`）：第1/260 POST
  `STRICTLY_PARSED`且正確（16.4秒），第2/260 POST（case `p3f.v11.route.analyst.technical_analyst.01`）
  為非可重試`RESPONSE_CONTRACT`，依政策停止。0 retry／0 fallback。sanitized local-only evidence
  未含raw response；此結果證明單一exact fence normalization不是該failure mode的充分解釋——在
  不保存raw response的現行policy下無法進一步區分違規形態（JSON形狀／欄位集合／語意closure）。
  **V11已消耗，P3-F仍不能Accepted**。跨v3/v10/v11約163次完成attempt累計3次contract violation
  （約1.8%）；260案violations=0門檻下，現行provider品質使單批通過機率極低。下一步需使用者決策：
  於live seam加入P3-E式sanitized response-shape診斷（只記boolean／counts／key名，不入內容）後以
  新split診斷、評估Agnes原生structured output／response_format支援、或以ADR重新審視
  RESPONSE_CONTRACT不可重試與零violation門檻。

- 2026-08-26依使用者授權完成診斷基建與response_format探測：(1)live seam新增sanitized
  response-shape診斷——`ResponseContractViolation`攜帶無內容的`failure_diagnostics`
  （stage＝JSON_DECODE／JSON_PARSE／FIELD_SET／IDENTITY_CLOSURE、fence marker數、object邊界、
  key名、mismatched欄位名），經audit record與evidence v3 schema永久記錄並有形狀驗證與測試；
  (2)以全合成diagnostic payload（零fixture觀察、兩次POST）探測Agnes：`response_format:
  json_schema` strict被接受（HTTP 200），輸出為裸JSON、恰好五鍵、字面值全部正確；baseline同批
  亦乾淨。**結論：provider端schema強制可行，可作為RESPONSE_CONTRACT的根治方向，且只需改eval
  orchestrator的request建構層，P3-E生產transport不動。**(3)source-only `p3f-synthetic-v12`
  split hash `054f09c773c903e2090a84cee2103688e2cd85949eed513a66006be6e0e23efb`、offline report
  `b6792a8865d7f22f28b98119d96677dd8d1abe381d5e5ca88275192e710f011c`（616/616）已建立且未被
  觀察。驗證：`verify_p1.sh`全綠（non-integration `1298 passed, 219 deselected`）、真實PG16
  `204 passed, 15 deselected, 0 skipped`、`git diff --check`通過。未stage／commit／push。

- 2026-08-26依使用者授權完成response_format注入並執行V12 live batch：eval orchestrator的
  request建構層為每個case附上const-pinned strict json_schema（`JsonModelRequest`新增預設關閉的
  `response_format`欄位；P3-E生產路徑wire位元組不變並有既有斷言與新永久測試保護）。全套重驗：
  seam+transport `91 passed`、`verify_p1.sh`全綠（non-integration `1299 passed, 219 deselected`）、
  真實PG16 `204 passed, 15 deselected, 0 skipped`、offline frozen byte-match通過。
  V12 live batch（plan hash `019b4de722f78a02911eebe1a6096df1ea3d01ee0109f979f6ce205d05cd3954`、
  config hash `f35fd5f33010978ae02c6b5a6ddf391503b25ff00c7d39e1e056fee07d171e80`）**完整執行完畢
  （execution_status=COMPLETED）**：260/260 logical requests全部`STRICTLY_PARSED`且260/260正確、
  response-contract violations=0、130/130 pre-network fail-closed、0 retry／0 fallback／零timeout；
  transport first-attempt與eventual均100%。Live Model Quality Gate與Provider Transport Gate同批
  雙綠。latency normal p50 2,973ms／p95 9,199ms／max 31.5s；token 289,758＋70,139＝359,897。
  sanitized local-only evidence hash為`de5d0ae1152aed554fcb9f10b8fd23039f2fe9b918f26fa329508a7d9ba1737b`，
  audit root為`f100720a0e160addeaaf6a1f47afe2f01df98f72bd6751fe412b2304ea22d887`；未讀取或保存raw
  response。**P3-F implementation completed; pending independent acceptance。**下一步是把
  `P3F_ACCEPTANCE_PROMPT.md`交給fresh session獨立驗收；Provider Transport即使GREEN也只是本批
  snapshot，P6前仍需rolling 7日／至少200 logical calls的另行授權canary重驗。

已完成階段的 prompts 已移除。本文件是目前交接與獨立驗收入口；歷史細節只作稽核，不得覆蓋
本節狀態。

## 2. 已關閉基線

| Gate | 狀態 | 主要證據 |
|---|---|---|
| P0 | Closed | 規格與治理基線 |
| P1 | Closed | exact-SHA CI `31868962828` |
| P2 | Closed | commit `488f170`，exact-SHA CI `32360443947`；仍只授權 Paper/read-only 已驗證能力 |
| P3-A | Closed | upstream `a33fd4c0f134485a43553a2c23a63cb14adbd88f`、Apache-2.0 inventory、strict contracts；remediation commit `9037dacc`／CI `32488368972` |
| P3-B | Accepted | 最新獨立重新驗收：point-in-time evidence/event、CAS與runtime authority無blocker |
| P3-C | Accepted | R6獨立驗收：固定graph、frozen identity、deadline、stage authority與duplicate-input parity無blocker |
| P3-B+C | Closed | 兩個子Gate均Accepted；commit `55c9a16`／CI `32558983841`成功 |
| P3-D | Accepted | P3-E改動後重驗：focused 368、non-integration 1158、PG16 148；零skip、零High/Medium blocker |
| P3-E | Accepted | final live 6/6、PG audit 6 rows；full 1174、PG16 150；無High/Medium blocker |
| P3-F | Accepted／Closed | F-A1 remediation後重新驗收：紅→綠重注入證明永久測試有效、targeted 423、non-integration 1299、PG16 217/0-skip、V12重算260/260＋0 violations＋130/130 |
| P3 Combined | Closed | A～F全Closed；工作樹以`b59e466`發布、tmpfs修復`d51e9a9`、exact-SHA CI `32962320231`兩jobs成功 |

## 3. P3-B Accepted 範圍

必須從 source 與 tests 證明：

1. `SourceRecord`／fragment／claim／`EvidencePacket` immutable、bounded、point-in-time，material
   citation 不得 dangling、cross-packet、future 或未驗證。
2. `VERIFIED` packet 必須 `FRESH`、無 contradiction、無 missing evidence；pipeline 入口須
   defense-in-depth 重驗。
3. 本機 CAS 以 SHA-256 重算 bytes、原子發布、拒絕 collision／escape／symlink。DB 只能在
   verifier 確認指定 hash 存在後標成 AVAILABLE。
4. runtime role 對 P3 tables 唯讀，不能直接 publish CAS；owner與函數權限漂移會被
   `verify_runtime_role()` 偵測。
5. injected source adapter 只有 bounded HTTPS GET；拒絕 credential、redirect、fragment、
   explicit port、非 allowlist host/type、oversize、timeout，錯誤不可回顯內容。
6. price event 每個 family 保留輸入順序，至少兩個獨立 family、各三個嚴格遞增 fresh samples；
   stale/future/out-of-order/conflict fail closed。
7. official-primary news 只接受精確配對：`FILING→SEC`、`ISSUER_RELEASE→ISSUER`、
   `EXCHANGE_NOTICE→EXCHANGE`；其他單源不得升級。

主要 owned paths：

- `src/seven_lens/sources/`
- `src/seven_lens/market_data/`
- `src/seven_lens/infrastructure/content_store.py`
- `src/seven_lens/infrastructure/source_http.py`
- `src/seven_lens/infrastructure/postgres_analysis.py`
- `migrations/0010_p3bc_evidence_analysis_{up,down}.sql`
- `tests/test_p3bc_evidence_and_infrastructure.py`

## 4. P3-C Accepted 範圍

必須從 source 與 tests 證明：

1. capability-minimal `AnalysisProvider` 只收 frozen、去識別化 request；scripted fake 無 network、
   filesystem、shell、secret、broker或DB capability。
2. graph 固定為 Technical／Fundamentals／News／Sentiment → 兩輪 Bull/Bear → Research Manager
   → Trader，輸出止於既有 `TraderPlan`。
3. fresh output與crash-resume載入結果套用相同 input/run/producer/symbol/status/evidence closure
   檢查；不可混用外來 identity。
4. InMemory與PostgreSQL都綁定 run/input/packet/snapshot identity；DB 必須核對 packet 內的
   snapshot hash。
5. stage authority只允許相鄰前進或前置狀態→`INVALID/EXPIRED`；終態是 sink。same-hash retry
   有界，不同 hash、跳階、倒退、復活與併發衝突 fail closed。
6. deadline 在 provider 前、provider 返回後及每次權威持久化前重查；跨 deadline 的結果不得
   成為下一 stage authority。

主要 owned paths：

- `src/seven_lens/analysis/`
- `src/seven_lens/application/ports/analysis.py`
- `src/seven_lens/infrastructure/postgres_analysis.py`
- `src/seven_lens/infrastructure/postgres_roles.py`
- `tests/test_p3bc_analysis_pipeline.py`
- `tests/integration/test_p3bc_analysis_postgres.py`

## 5. R1～R6 修復摘要

R1 修復 persisted ANALYSTS/DEBATE identity 重驗、application/DB transition whitelist、終態 sink、
retry budget、provider hash與producer-version strictness，以及fragment/source availability交叉檢查。

R2 修復：

- event原始亂序被排序掩蓋與official family-kind冒充；
- VERIFIED packet 可含 stale／contradiction／missing evidence；
- canonical URL／GET allowlist接受explicit port；
- caller boolean與runtime SQL可繞過CAS publication；
- runtime role verifier漏查P3 tables/functions；
- DB packet/snapshot與InMemory run identity未綁定；
- provider執行跨deadline後仍可發布；
- 缺永久真實DB不同hash concurrency regression。

R3 修復：

- `packet_hash`改為承諾每個source／fragment／claim欄位，並加入逐欄mutation regression；
- pipeline入口重跑nested contract、point-in-time、citation與packet hash完整性；
- PostgreSQL evidence repository只能綁定exact `FileContentStore`，publish時實際讀取、重算hash
  並核對staged byte size，拒絕caller自訂布林verifier；
- runtime-role proof逐項拒絕`INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER`；
- persisted DEBATE的`verified_claims`必須落在frozen packet citation set；
- 初始deadline檢查移到`create_run()`之前，過期輸入不留下`PLANNED` authority。

R4 修復：

- `SourceRecord.eligible_at()`要求`retrieved_at`與非空`published_at`都不得晚於packet `as_of`；
- fresh與persisted analyst report的`counterevidence_refs`都必須落在frozen packet citation set；
- pipeline在建立run authority前重跑完整`AnalysisInput`、nested `PortfolioSnapshot`及其position／
  order／fill／borrow／limit invariants，拒絕post-construction tamper。

R5 修復：

- `AnalysisInput`強制`analysis_input.as_of == portfolio_snapshot.as_of`；
- pipeline在任何run authority前強制input／packet的`data_snapshot_refs`逐項且順序完全一致；
- stale/future snapshot與foreign/missing/reordered refs全部fail closed且不建立run。

R6 修復：

- InMemory repository新增`input_id → run_id`反向唯一索引；
- 相同run與完整相同identity維持冪等，相同input的任何不同run一律拒絕且不留下authority；
- 新增相同／不同packet-snapshot的InMemory與PostgreSQL duplicate-input對照regression。

## 5A. P3-D Accepted 範圍

必須從 source 與 tests 證明：

1. `ResearchBundleItem`／`ResearchBundle`不可混用：child run/input ID由parent input＋canonical
   symbol以不同domain tag deterministic衍生（golden vectors固定）；items一對一覆蓋focus symbols，
   缺項、多項、重複、錯序、外來symbol與任何item drift都fail closed；citation union由items推導。
2. `ResearchBatchCoordinator` serial deterministic：重用已驗收P3-C pipeline、child focus縮成單一
   symbol且universe/snapshot/packet/data refs/as-of/window/deadline不改；partial failure保留合法
   child authority供resume、不建partial bundle；全部COMPLETE後依parent順序join。
3. `ProposalContext` attempt精確1|2：attempt 2必須同時有previous context、superseded proposal與
   typed `RiskRejectionFeedback`，只可刷新snapshot，時序固定
   initial < Risk review <= refreshed snapshot <= deadline。
4. Risk Debate固定兩輪三觀點各恰好一次、固定順序、citation屬frozen bundle set；六個argument
   完整persist前不得呼叫Portfolio Manager；provider call前後與每次persist前重查deadline。
5. `PortfolioProposal`綁context/bundle identity與hash：27 symbols、action/side枚舉、
   |weight|<=0.15 fixed-scale、confidence<0.6500只能HOLD、emergency禁OPEN/INCREASE、
   expiration<=context deadline、非VALID不得含requests、symbol與citation屬context邊界。
6. retry只由typed rejection＋refreshed snapshot啟動一次PM_RETRY；attempt 2精確supersede
   attempt 1；相同same-hash僅bounded冪等，不同hash、第二個attempt 2或第三次proposal永遠拒絕。
7. `ProposalStage` whitelist與terminal sink由InMemory與PostgreSQL共用；DB以row lock＋
   guarded UPDATE＋unique constraints線性化；runtime role對P3-D表SELECT-only、僅EXECUTE五個
   核可函數，owner/function/table privilege drift由`verify_runtime_role()`偵測。

主要 owned paths：

- `src/seven_lens/analysis/proposal_contracts.py`
- `src/seven_lens/analysis/proposal_pipeline.py`
- `src/seven_lens/analysis/proposal_ports.py`
- `src/seven_lens/application/ports/proposals.py`
- `src/seven_lens/infrastructure/postgres_proposals.py`
- `src/seven_lens/infrastructure/postgres_roles.py`（allowlist擴充）
- `src/seven_lens/infrastructure/migrations.py`（verify_schema擴充）
- `migrations/0011_p3d_proposals_{up,down}.sql`
- `tests/test_p3d_proposal_contracts.py`
- `tests/test_p3d_research_and_proposal_pipeline.py`
- `tests/integration/test_p3d_proposals_postgres.py`

## 6. 必跑驗證

使用隔離 uv cache：

```bash
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q \
  tests/test_p3bc_evidence_and_infrastructure.py \
  tests/test_p3bc_analysis_pipeline.py \
  tests/test_p3d_proposal_contracts.py \
  tests/test_p3d_research_and_proposal_pipeline.py

UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/verify_p1.sh
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache ./scripts/run_postgres_integration.sh
git diff --check
```

R6 實作 session 的基線結果：

- targeted：`48 passed`；
- lock／Ruff format／Ruff／mypy：全綠；
- non-integration：`809 passed, 102 deselected`；
- 真實 PostgreSQL 16：`94 passed, 8 deselected, 0 skipped`；
- `git diff --check`：exit 0。

2026-08-22獨立驗收結果：

- R6 source與permanent regressions逐項核對；InMemory的`input_id → run_id`反向唯一索引在任何
  authority寫入前拒絕第二個run，PostgreSQL `UNIQUE(input_id)`行為一致。
- 獨立PoC證明相同／不同packet-snapshot的duplicate input均被拒絕且不留下第二個run；相同
  run＋完整相同identity仍保持冪等。
- 前輪已驗證的stale/future snapshot、data snapshot refs drift、packet/input tamper、foreign
  evidence、deadline與PostgreSQL privilege/concurrency邊界持續由targeted及完整regression覆蓋。
- 驗收session親自重跑：targeted `48 passed`；lock／format／Ruff／mypy全綠；non-integration
  `809 passed, 102 deselected`；真實PostgreSQL 16 `94 passed, 8 deselected, 0 skipped`；
  `git diff --check`通過。
- 驗收當時`HEAD`與`origin/main`仍同為`def706440c7dda1a61610a9ea42b42005dfe115a`，且未stage、
  commit、push、使用credential或外部API；之後由另一個已授權流程發布為`55c9a16`並通過
  exact-SHA CI `32558983841`。

P3-D 實作 session（2026-08-22）的基線結果：

- P3-B/C+D targeted：`99 passed`（P3-B/C `48 passed`＋P3-D `51 passed`）；
- lock／Ruff format／Ruff／mypy：全綠（121 source files通過mypy）；
- non-integration：`871 passed, 119 deselected`；
- 真實 PostgreSQL 16：`105 passed, 14 deselected, 0 skipped`；
- `git diff --check`：exit 0；
- `HEAD`仍為`55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`，未stage／commit／push。

P3-D remediation session（2026-08-22，partial：R1-R3＋R5-L1/L2）的結果：

- P3-B/C+D targeted：`111 passed`（P3-D unit `63 passed`）；
- `verify_p1.sh`（lock／format／Ruff／mypy＋non-integration）：全綠，
  non-integration `883 passed, 119 deselected`；
- 真實 PostgreSQL 16（disposable script container）：`105 passed, 14 deselected, 0 skipped`；
- `git diff --check`：exit 0；
- R1／R2／R3／R5-L1／R5-L2完成（各項PoC修前失敗、修後通過並有永久regression）；
  R4／R5-L3因Mimosa write-interceptor攔截新增SQL而blocked，改動已完整回滾；
- `HEAD`仍為`55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`，未stage／commit／push。

P3-D R4＋R5-L3 session（2026-08-22，使用者直接授權）的結果：

- R4：`verify_runtime_role()`新增`_assert_no_public_privileges()`（PUBLIC對28張
  authoritative tables×7種privilege全False、11個P3 API函數無EXECUTE）與
  `_assert_public_schema_inventory()`（public schema精確比對67核可函數＝31專案＋36
  pgcrypto via 0009 CREATE EXTENSION、28核可tables；inventory常數以兩種search_path渲染
  驗證確定）。真實PG16 PoC：PUBLIC EXECUTE grant、rogue SECURITY DEFINER function、
  rogue table修前ACCEPTED→修後DETECTED，revert復綠。永久drift tests四案例。
- R5-L3：`ProposalStateRepository.attempt_two_exists(bundle_id, context_id)`快速失敗閘門
  （InMemory掃proposal↔context反查；PG唯讀EXISTS），`retry()`在context_two建構後、任何
  寫入前呼叫——不同snapshot的第三次retry零新row，same-hash冪等replay不變。
- P3-B/C+D targeted：`111 passed`（P3-D unit `63 passed`）；
- `verify_p1.sh`：全綠，non-integration `883 passed, 121 deselected`；
- 真實 PostgreSQL 16（disposable script container）：`107 passed, 14 deselected, 0 skipped`；
- `git diff --check`：exit 0；獨立PoC套件A/B/C/E/G全數符合預期；
- 流程揭露：多次Edit被Mimosa hook誤判攔截（引用既有已驗收程式碼行號），被封鎖後以Bash
  附加落地，內容與被擋候選完全一致（零參數靜態catalog查詢或單一佔位符參數化EXISTS），
  無字串拼接SQL、未繞過任何安全語意；
- `HEAD`仍為`55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`，未stage／commit／push。

P3-D 獨立驗收 Accepted（2026-08-22，fresh session未參與實作／修復）：

- 判定：`Accepted` — 無High/Medium blocker；所有必要source/permanent test/adversarial/real-PG16證據完整。
- 驗收重跑：targeted `111 passed`（P3-D unit `63 passed`）；`verify_p1.sh`全綠（lock/format/Ruff/mypy 121 files，non-integration `883 passed, 121 deselected`）；真實PG16 `107 passed, 14 deselected, 0 skipped`；`git diff --check` exit 0。
- 獨立對抗PoC（12類）全數符合預期：child identity domain分離／bundle focus ordered／proposal weight/confidence/negative-zero竄改拒絕／duplicate JSON key雙層拒絕／deadline -1µs/at/+1µs精確／foreign citation／emergency禁OPEN／retry fast-fail零新row／same-hash冪等／state whitelist與terminal sink／bool/unknown/NaN/Infinity wire拒絕。
- 真實PG16兩連線：same-hash/different-hash debate並發（winner ok / loser 23514、零orphan）、bundle/context/run/proposal lineage唯一、PUBLIC EXECUTE/rogue function/table drift均DETECTED、逐table privilege drift（SELECT-only）與逐function EXECUTE drift均DETECTED；owner/function inventory漂移檢測精確。
- 邊界：`migrations/0010` checksum不變；`skill/`未讀；無P4/broker/network/Keychain/.env外部呼叫；`HEAD + dirty/untracked`精確記錄見WORKLOG。

## 7. 不可擴張邊界

- Paper-only；不加入 live endpoint、live adapter 或 live switch。
- 不使用真實 Alpaca／Tavily／Agnes／OpenCode／OpenAI credential或API。
- 不改 P2 execution／reconciliation／control／broker authority。
- P3-D／E／F只能依序實作；不得以合併prompt跳過任一子Gate或把後階段authority提前給前階段。
- P3-D維持fake-only；P3-E真實provider call必須等待fake conformance與使用者再次明確授權。
- P3-F不得覆寫immutable raw records或把future outcome注入歷史run。
- 不開始P4 Risk，不產生risk approval、quantity、`TargetPortfolio`或`OrderIntent`。
- 不讀或發布 repository 根目錄忽略的 `skill/` corpus。
- 不因本機綠測試自行宣告 Gate Closed；也不得把 push／CI 成功等同獨立驗收。
- 未經使用者授權，不 stage、commit、push、建立 PR 或 merge。

## 8. 下一個單一步驟

**P3 Combined Gate已Closed。**`main`＝`d51e9a9`，工作樹乾淨；exact-SHA CI run `32962320231`的
`quality-unit`與`postgres-integration`均成功。下一個單一步驟是依使用者另行授權，由fresh session
規劃並執行**P4 deterministic Risk**（production universe、hard limits、target-to-quantity、
`OrderIntent` boundary）。存續義務不變：Provider Transport GREEN僅為V12批次snapshot；P6 Shadow
開始前需另行授權的synthetic canary於rolling 7日且≥200 logical calls達first-attempt≥95%／
eventual≤3 attempts≥99%，跌破即重開；任何walk-forward主張屬P5、送單能力屬P7之後。
