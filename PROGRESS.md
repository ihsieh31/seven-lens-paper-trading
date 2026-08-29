# Progress

最後更新：2026-08-29（P1–P3 Closed；P4-A／P4-B Accepted、Closed；P4-C～F未開始；
generic analysis-provider 已整合，NVIDIA `openai/gpt-oss-120b` 為現行 route）

## 目前 Gate

**P0／P1／P2／P3全部Closed；P4-A與P4-B均已完成fresh independent acceptance，verdict為Accepted、Gate
狀態為Closed；P4-C～P8未開始。P4 overall仍為In progress。**

P3 於 2026-08-26 完成最後子閘的獨立重新驗收後一次性關門，並以 commit `b59e466` 發布工作樹、
`d51e9a9` 修復 CI postgres service tmpfs、`660e062` 完成治理同步；exact-SHA run `32962320231`
與 `32963312426` 的 `quality-unit`＋`postgres-integration` 兩 required jobs 均成功。

## 2026-08-29 Generic analysis-provider 整合

- 分析 provider 配置已 generic 化：
  strict operator config schema/hash/loader（`config/analysis_provider.py`）、兩個 operator CLI 指令
  （`cli/analysis_provider.py`：`set-endpoint`／`set-model`，另有唯讀 `show`／`validate`）、
  generic Chat Completions transport（`infrastructure/chat_completions_transport.py`）、
  config-driven composition 與 thin adapters（`analysis_provider_composition.py`、
  `analysis_providers.py`）、generic secret kind
  `seven-lens.paper-trading.analysis-provider.api-key`、bounded generic audit route identity
  （新增 `route_config_hash`，legacy Agnes rows 完整保留）。
- additive migration `0022_analysis_route_identity_up/down.sql`：provider/model/policy CHECK 改為
  bounded union＋cross-field hash closure；`memory_curation_audits.model_id` 允許單一 `/`；
  down migration 在存在 generic route rows 時 fail closed（SQLSTATE 55000）。
- P3-E live harness 使用 current-route 六案例與 route-bound evidence；P3-F active source-only split 為
  `p3f-synthetic-v14`（616 cases，route cases 綁定 current config hash）；歷史 V12 bytes/hash unchanged。
- 現行 operator route：base `https://integrate.api.nvidia.com/v1`、model `openai/gpt-oss-120b`、
  route_config_hash `0659d8fa9b38c9e7a800ce2bdc89b14eeb76a5c83f157f6b65afcbe568162524`。

## NVIDIA `openai/gpt-oss-120b` current-code evidence

- 現行 route 為 `https://integrate.api.nvidia.com/v1`＋`openai/gpt-oss-120b`（route_config_hash
  `0659d8fa9b38c9e7a800ce2bdc89b14eeb76a5c83f157f6b65afcbe568162524`）。canonical Keychain
  service `seven-lens.paper-trading.analysis-provider.api-key` 已由使用者提供之 key 就緒。
- 因 provider 變更，重新產生綁定新 route 的 source-only split `p3f-synthetic-v14`
  （616 cases、offline 616/616 byte-match、regeneration deterministic）；V12 bytes 不變。
- Current-code P3-E final live：6/6 SUCCESS、retry=0、fallback=null，p50=`6114ms`、p95/max=`18804ms`；
  evidence：`docs/P3E_LIVE_EVIDENCE_2026-08-29_NVIDIA.json`。
- Current-code P3-F V14 final live：260/260 strict、0 errors、0 retries、0 fallback；130/130
  invalid/ambiguous pre-network rejects；first-attempt/eventual/valid-primary皆100%，live quality與transport
  gates皆通過。local immutable evidence hash `9fcc76258883365990f47783b1b5f01226d813c40d6b703ef033ee66da5b16e0`，
  file SHA-256 `c69c3d7e6ccaf78e772a503bca8d394c488c2d4ffe649f23f679dd33c81dca85`。
- Provider-drift 調整（使用者授權下）：NVIDIA/vLLM 回應含非 authority envelope metadata
  （service_tier、system_fingerprint、prompt_logprobs、prompt_token_ids、kv_transfer_params、
  choice logprobs/stop_reason/token_ids、message reasoning/reasoning_content/tool_calls/
  function_call/annotations/audio/refusal、usage *_details），strict parser 以**明列 allowlist
  ＋bounded 值驗證**接受；未知欄位仍 fail closed；content authority 路徑不變。
- model id `openai/gpt-oss-120b` 含 `/`：envelope/producer version 以 derived
  `route_model_version`（`openai.gpt-oss-120b`）投影；route closure 仍以 exact model id 於
  claim/audit/wire 強制。live path（live-plan/live-run/execute）補上 route snapshot 綁定。
- Offline／regression：V14 `616/616` 且 regeneration byte-identical；整合後完整 non-integration
  `2117 passed, 282 deselected`；PG16 `280 passed, 2 deselected, 0 skipped`；Ruff format/check、mypy、
  115份tracked JSON parse與243份Python compile全綠。
- 狀態：**implementation integrated; current-code NVIDIA live evidence GREEN**（單批 snapshot，不能取代
  P6 前 rolling canary 義務）。

## 2026-08-29 P1～P4-B 深度審查與修復

- 多輪 Luna Max 分區審查、fresh acceptance、cross-phase review與真PG adversarial PoC已完成；只修復可重現
  High/Medium與極小明確Low，沒有新增framework、provider特例、broker authority或P4-C能力。
- P1/P2：收緊DSN escaping、Keychain native bounds、inactive asset、fill overrun與broker FILLED一致性；
  execution/mirror/execution-id/broker-order-id衝突現在先durable `REVIEW_REQUIRED`＋pause/audit，整批fill在任何
  mutation前預檢，真PG跨order/race保持單一immutable identity與零錯誤fill。
- P3：production live route/authorization/executor/transport在Keychain、evidence path與POST前完成exact preflight；
  config URL/generation與clock failure分類fail closed。V14 offline仍616/616且provider requests=0。
- P4-A/B：深層JSON、endpoint-family、GDELT timestamp與identity append rollback收緊；additive migration
  `0023_p4b_authority_adversarial_hardening`拒絕不完整identity source closure、非canonical reasons、重複event IDs
  與typed payload drift。`0021` bytes不變；0023 up/down/up、owner/ACL/search_path/function restore已由真PG驗證。
- 發布：`74e1c23`整合generic NVIDIA provider、`eb6f214`登載P1～P4-B hardening；治理同步commit後
  `main`與`origin/main`一致。

## 2026-08-27 P1–P3 full remediation 狀態

- Batch A–G：已完成 current worktree 的獨立 source、adversarial、regression 與 real PostgreSQL 驗收，
  狀態為 Accepted。
- Batch E：P3-19/20/22/24/26 已完成 typed correction provenance、selection infra error、repository
  invalidation parity、source/projected hash 與 application memory port 修復。
- Batch F：P3-10 已以 reconciliation-specific `REPEATABLE READ` UoW boundary 修復，並以雙連線
  真實 PG 測試證明同一 run 不混合兩個 local committed snapshots。
- Batch G：移除 dead reconciliation helper／不可達分支與無 consumer 的 shutdown API；保留 sweep
  all-or-nothing 語意；補 P3-21 SQL regression；`market_data/events.py` production wiring 明確 deferred
  至 P4。
- NEW-P2-01：runtime role 對 `control_state` 的直接 UPDATE 權限已撤銷，改由固定
  `SECURITY DEFINER` control functions 提供窄化 authority；real-PG direct-update／unsafe-resume
  對抗 probe 均按預期拒絕。
- 獨立驗收證據：targeted P1–P3 `857 passed`；完整 non-integration `1386 passed, 245 deselected`；
  PostgreSQL 16 `243 passed, 2 deselected, 0 skipped`；Ruff format/check、mypy、`git diff --check`
  全綠。2 個 deselected 為明確 live provider tests，沒有 silent skip，也沒有本次 provider/model call。

## Phase 狀態

| 階段 | 狀態 | 說明 |
|---|---|---|
| P0 規格與治理 | Closed | Paper-only、投資流程、資料與安全基線 |
| P1 專案骨架與權威狀態 | Closed | Python/uv、typed config、PostgreSQL、Keychain、telemetry、CI |
| P2 Alpaca Paper 執行安全 | Closed | order/fill/reconciliation/control/NAV/runtime authority；真實下單仍未授權 |
| **P3 研究／提案／記憶** | **Closed** | upstream contracts、evidence/event、研究管線、Risk Debate／提案、provider isolation、reflection lineage、bounded memory 與 eval 治理；A～F 子閘及 cleanup Batch A～G 均已獨立驗收 |
| P4 多來源／候選／deterministic Risk | In progress | P4-A／P4-B已獨立驗收，均為Accepted／Closed；P4-C～F未開始；P4 overall仍In progress |
| P5 validation | Not started | point-in-time walk-forward、attribution、economic fills |
| P6 Shadow | Not started | 至少20交易日，零送單 |
| P7 Supervised Paper | Not started | 至少20交易日；此階段前不得送單 |
| P8 Unattended Paper | Not started | 再至少40交易日 |

## 已關閉證據摘要

- **P1**：P1-A/B/C1/C2/C3 獨立驗收完成；commit 發布＋exact-SHA CI 成功。能力：strict typed
  config、Paper endpoint allowlist、canonical JSON／UTC、PostgreSQL authority、macOS Keychain
  exact read、dependency-neutral telemetry、zero-skip CI。
- **P2**：ACC-001～009 remediation 關閉；code-bearing commit `488f170`／CI `32360443947`。
  能力：exclusive new-entry linearization、durable UNKNOWN/conflicting-fill pause、reconciliation、
  cash checkpoint＋full-ledger NAV、runtime baseline read-only、migration compatibility。Alpaca
  GET-only evidence 已執行；不含真實 submit。
- **P3**：六個子閘各自經 fresh-session 獨立驗收 Accepted（含多輪 rejected→remediation→re-acceptance，
  逐輪細節見 `WORKLOG.md`）。最終關門證據基線：
  - Targeted `423 passed`；non-integration `1299 passed, 232 deselected`；真實 PG16 整合
    `217 passed, 15 deselected, 0 skipped`；Ruff／format／mypy／`git diff --check` 全綠。
  - Offline eval：V12 frozen report byte-match，split/report hash 不變。
  - Authorized live evidence V12：260/260 strict 且全正確、violations=0、130/130 pre-network
    fail-closed；Provider Transport first-attempt/eventual 皆 100%（該批 snapshot）。
  - 發布鏈：`b59e466`（工作樹）→ `d51e9a9`（CI tmpfs 512m→1g 修復）→ `660e062`（治理同步）；
    exact-SHA run `32962320231`／`32963312426` 兩 jobs 成功。

## 尚未完成／不得提前宣告

- Provider Transport rolling reliability evidence：V12 批次 snapshot 為 GREEN，但 P6 前仍需另行
  授權的 synthetic canary 在 rolling 7 日且 ≥200 logical calls 重驗，跌破即重開。
- P4-C～F的production universe、deterministic Risk approval、quantity與zero-submit `OrderIntent` boundary
  尚未實作／驗收；P4-A／P4-B已各自fresh independent acceptance並Closed。
- Confirmed forward/reverse split的持倉退出尚未實作：P4只規劃intent，P5 replay、P6 shadow，P7首次Paper
  submit需fresh acceptance與使用者exact授權；short BUY-to-cover目前不在auto authority。
- P5～P8 回測、Shadow、Supervised Paper 與 Unattended Paper。
- Tavily 七帳號 pool；沒有外部授權證據時固定 `SINGLE_ACCOUNT_UNVERIFIED`。

## 2026-08-27～28 P4規劃、實作與歷史狀態紀錄

- ADR-036：來源分為`AUTHORITY/CONFIRMATION/DISCOVERY/RESEARCH_SUPPLEMENT`，涵蓋Alpaca、yfinance、
  FRED/ALFRED、Treasury/BLS/BEA/EIA、SEC/IR、Corporate Actions、Nasdaq/NYSE、Tavily/GDELT；禁止silent
  fallback升權，要求point-in-time時間、security identity、rights與hash lineage。
- ADR-037：forward/reverse split候選在analysis前、Risk與submit前quarantine；既有long正式確認後不經LLM，
  未來走獨立`CORPORATE_ACTION_EXIT`。退出需cancel/resolve、FULL reconciliation、regular-hours、price collar、
  idempotency；fill後記錄拆／合股原因、realized P&L、通知與`OPERATIONAL_EXIT_NOT_THESIS_FAILURE`記憶。
- 2026-08-27先完成docs/prompt packaging；其後另一實作輪完成P4-A初版source/tests。沒有migration、source/model/broker
  call、Keychain讀取、commit或push；不得標為P4-A Accepted。
- ADR-038與`P4_PROGRAM_PLAN.md`固定單一Paper帳戶、long-only、保守hard limits、整股quantity／價格保護及
  零付費來源profile；P4拆為A～F，每個Gate各有獨立implementation／acceptance prompt，共12檔。這是
  prompt packaging complete，不是code-bearing implementation授權或Gate證據。12檔已擴寫為弱模型專用規格，逐檔
  包含可動／禁動範圍、逐步算法、停止條件、Definition of Done、獨立PoC／PG審查與verdict matrix。
- 2026-08-28使用者核准ADR-039：`p4-factor-v1`、`sec-sic-division-v1`、
  `p4-correlation-cluster-v1`、`p4-gross-turnover-v1`。四項已完整寫入P4-C／D實作與驗收prompts，不再是待決設定。
- 2026-08-28完成P4-A prompt第0C節ADR-039 delta：SEC manifest移除任意concept URL（只留submissions與
  companyfacts兩個exact endpoint）、submissions新增top-level四位數SIC point-in-time observation（僅
  zero-pad、不guess mapping）、companyfacts只接受五個exact (taxonomy,concept) XBRL concepts並以
  submission acceptance closure決定available time（accn未join即typed failure，不猜時間）；capex保留
  provider原值與sign convention（無abs）。P4-A僅normalization，無TTM/factor/market cap/SIC Division/Risk。
  全部offline fixtures（network=0）；focused P4-A＋invariants 361 passed、non-integration 1738 passed、
  Ruff format/check與mypy全綠。以上為實作當時證據；其後fresh independent acceptance closure見下節。
- 2026-08-28依`P4B_IMPLEMENTATION_PROMPT.md`完成P4-B：新增point-in-time security identity resolver、
  append-only source／event／quarantine contracts與in-memory／PostgreSQL authority；公開入口固定為
  `SecurityMasterService`，流程為validate→identity resolve→durable block→confirmation→CAS transition→
  readback→bounded telemetry。涵蓋forward/reverse split、source correction與withdrawal、三層quarantine、
  identity／ratio／effective-date／source-lineage fail-closed規則；沒有P4-C、Risk／portfolio／quantity／
  broker或model authority。
- P4-B implementation evidence：focused P4-B＋source invariants `131 passed`；真實PostgreSQL 16 P4-B suite `7 passed`，
  含up/down/up、兩連線CAS、confirm-vs-withdraw race、telemetry failure與runtime ACL。先前完整PG套件長跑的
  `oom_killed=true`已定位為WAL churn寫入tmpfs的本機資源問題；本機整合腳本改用disk-backed anonymous volume
  後，fresh `verify_p1.sh --postgres`為`254 passed, 2 deselected`。以上為實作當時證據；其後P4-B已完成fresh
  independent acceptance並Closed。

## 2026-08-28 P4-A／P4-B independent acceptance closure

- P4-A verdict：`Accepted`、Gate：`Closed`。focused P4-A＋secret／Paper-only invariants `372 passed`。
- P4-B verdict：`Accepted`、Gate：`Closed`。focused P4-B＋Paper-only invariants `132 passed`；fresh PostgreSQL
  16 integration `256 passed, 2 deselected, 0 skipped`；同輪non-integration `1878 passed, 256 deselected`。
- 修復後公開入口與對抗重驗：blocked head維持`entry_blocked`；direct `ELIGIBLE`與未知payload均以SQLSTATE
  `23514`拒絕；owner-safe state為`entry_blocked`，eligible與extra-payload rows均為`0`；source transport／SEC／
  FRED adversarial PoC均按預期通過。
- 驗收結論：`no actionable findings`。本輪未讀Keychain、未呼叫provider／model／broker；P4仍Paper-only、
  zero-submit，P4-C～F未開始，完整P4尚未Closed。

## 文件與歸檔

- 現行文件：`PROJECT_HANDOFF.md`、本檔、`docs/ROADMAP_AND_ACCEPTANCE.md` 與治理 ledgers。
- P3 各子閘的 implementation／acceptance prompt、requirement map 與關門當下 handoff 快照已歸檔於
  [`docs/archive/`](docs/archive/)（`prompts/`、`p3/`、`handoffs/`）；歸檔文件僅作歷史紀錄。
- 後續每個 gate 由使用者授權後建立新的 work-package prompt，由未參與實作的 fresh session 驗收；
  prompt 存在不代表實作開始或 gate 通過。

## Gate 規則

1. 實作完成、綠測試、commit、push或CI成功都不能單獨關閉Gate。
2. PostgreSQL authority主張必須以真實PostgreSQL、runtime role與failure/concurrency injection驗證。
3. 獨立驗收只接受當下source、focused tests、對抗PoC與完整regression證據。
4. 未知或矛盾狀態維持Open；不得以文件敘述取代程式強制。
5. 未經使用者明確授權，不commit、push、merge或擴張至下一phase。
