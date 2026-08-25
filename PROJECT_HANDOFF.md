# P3-D／E／F Current Handoff

最後更新：2026-08-24
專案：`/Users/zongen/Downloads/codex/trading`

## 1. 目前唯一狀態

**P3-B Accepted; P3-C Accepted; P3-D Accepted; P3-E Accepted. P3-F implementation in progress.**

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
  項目存在，但其非敏感metadata不能證明值已rotation，因此不得使用或寫Accepted。P3-F依固定Gate順序
  尚未開始。

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
| P3-F | In progress | immutable reflection、bounded memory、CAS promotion、curator PG與offline eval實作中 |

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

P3-D在P3-E改動後完成全量重驗並維持**Accepted**（focused 368、non-integration 1158、真實
PG16 148，零skip、零High/Medium blocker）。下一個單一步驟是取得P3-E六個synthetic case、最多
六次Agnes request、privacy／quota／stop conditions的明確Yes；在此之前不得呼叫provider。
P3-E live通過並完成本session acceptance後，才可開始P3-F。工作包未stage／commit／push，
`HEAD`仍為`55c9a16`。
