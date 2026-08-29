# Project Handoff

最後更新：2026-08-29（P1–P3 Closed；P4-A／P4-B Accepted、Closed；P4-C～F未開始；
generic analysis-provider 已整合，NVIDIA `openai/gpt-oss-120b` 為現行 route）
專案：`/Users/zongen/Downloads/codex/trading`

## 1. 目前唯一狀態

**P1–P3 full remediation 已完成獨立驗收並 Accepted；P4-A與P4-B均已完成 fresh independent acceptance，
verdict 為 Accepted、Gate 狀態為 Closed；P4-C～P8未開始。P4 overall 仍為 In progress。**
本地 `main` 以 `origin/main` 的已發布 P4-A／P4-B checkpoint 為起點，整合 generic analysis-provider、
NVIDIA V14 fixtures、migration 0022、P1～P4-B深審修復與additive migration 0023、測試及治理文件。
沒有新增broker/order authority；本輪整合與治理同步已提交並發布至`origin/main`。

2026-08-27 獨立驗收證據：`./scripts/verify_p1.sh` 為 `1386 passed, 245 deselected`；targeted
P1–P3 suites 為 `857 passed`；`./scripts/verify_p1.sh --postgres` 的 PostgreSQL 16 integration 為
`243 passed, 2 deselected, 0 skipped`；Ruff format/check、mypy、`git diff --check` 通過。
獨立 runtime authority probe 亦確認 direct `UPDATE control_state` 為 SQLSTATE `42501`、未達 FULL+CLEAN
時 direct resume 為 SQLSTATE `55000`，且受控 pause/resume 路徑正常。

2 個 deselected tests 是明確標記的 live provider tests；本次沒有在缺少新授權時呼叫 provider/model。
P3-4 與 P3-28 的 future lifecycle/event wiring 仍按設計 deferred，P3-21 維持 FALSE POSITIVE。

2026-08-27 使用者新增兩項已核准架構需求（當時 implementation 尚未開始；目前狀態見第2節）：

- 多來源資訊層：Alpaca行情authority；FRED/ALFRED＋Treasury/BLS/BEA/EIA；SEC/IR；Alpaca Corporate
  Actions＋Nasdaq/NYSE；Tavily/GDELT discovery；yfinance research supplement。來源角色不得因fallback升權。
- confirmed forward/reverse split：候選在分析前quarantine；既有long跳過LLM但經正式來源確認、cancel/resolve、
  FULL reconciliation、tradability、regular-hours、price collar與idempotency後，未來由獨立
  `CORPORATE_ACTION_EXIT`平倉；fill後記錄明確原因、realized P&L與非thesis-failure記憶。

其後使用者核准P4 runtime profile：單一Paper帳戶、long-only／`short_enabled=false`、long/total gross 90%、
現金至少10%、單股5%、15檔、sector 25%、cluster 30%、normal turnover 20%、ADV 0.1%、daily-loss 1%停止
新增曝險、drawdown 8% freeze；整股向零取整、minimum adjustment、rebalance band與quote/spread/collar
保守門檻，以及零付費資料政策。完整決策與工作包見`ADR-038`及`P4_PROGRAM_PLAN.md`。

2026-08-28 使用者另核准ADR-039：固定Factor V1九項子因子與權重、ordinary common stock-only、SEC SIC
Division sector taxonomy、126-session correlation connected-components cluster，以及不除以2且不淨額抵銷的gross
turnover公式。四項已不再待決，runtime／env／模型不得自行改公式或taxonomy。

## 2. P4-A／P4-B acceptance status

P4-A與P4-B已於2026-08-28由fresh independent session完成重新驗收；兩者verdict均為`Accepted`，Gate狀態均為
`Closed`。這只關閉A、B兩個子Gate，不代表完整P4 Closed，也不提前開啟P4-C～F的authority。

- **P4-A：Accepted／Closed。** focused P4-A＋secret／Paper-only invariants為`372 passed`；source transport與
  adapter adversarial PoC（forged request、malformed response、SEC rate-limit／unknown concept／bad date／CIK
  mismatch、FRED vintage mismatch、backdated source）均按預期fail closed或通過。
- **P4-B：Accepted／Closed。** focused P4-B＋Paper-only invariants為`132 passed`；fresh PostgreSQL 16
  authority／integration為`256 passed, 2 deselected, 0 skipped`（完整non-integration同輪為`1878 passed,
  256 deselected`）。證據涵蓋migration cycle、兩連線CAS、source correction／withdrawal、telemetry failure、
  runtime ACL與quarantine。
- 修復後的獨立公開入口重驗：blocked head維持`entry_blocked`；直接嘗試寫入`ELIGIBLE`與未知payload欄位均以
  SQLSTATE `23514`拒絕；owner-safe readback為`entry_blocked`、eligible rows=`0`、extra payload rows=`0`。
- 本次驗收沒有Keychain讀取、provider／model／broker呼叫；P4仍維持Paper-only、zero-submit。P4-C～F的Risk、
  portfolio、quantity與`OrderIntent`尚未實作或驗收。

## 3. P3 Close 摘要

P3（TradingAgents 研究、提案與記憶）於 2026-08-26 一次性關閉。六個子閘 upstream/license contracts、
point-in-time evidence/event、研究管線、Risk Debate／Portfolio Manager、provider isolation、
reflection/memory/evals 各自經獨立驗收 Accepted 後合併關門；過程中的 rejected→remediation→re-acceptance
輪次細節見 `WORKLOG.md` 與 `docs/archive/handoffs/PROJECT_HANDOFF_2026-08-26_P3-CLOSED.md`。

關門時的最終證據基線：

- Targeted `423 passed`；non-integration `1299 passed, 232 deselected`；真實 PG16 整合
  `217 passed, 15 deselected, 0 skipped`；Ruff／format／mypy／`git diff --check` 全綠。
- Offline eval：V12 frozen report byte-match，split/report hash 不變。
- Authorized live evidence V12：260/260 strict 且全正確、violations=0、130/130 pre-network fail-closed；
  Provider Transport first-attempt/eventual 皆 100%（該批 snapshot；Agnes route 歷史證據，
  2026-08-28 起 active route 已改為 NVIDIA，見第6節）。

## 4. 邊界與存續義務

- Paper-only；未知／缺失／過期／矛盾狀態一律 fail closed；無 live endpoint、無自動升級實盤路徑。
- 只有 P4 deterministic Risk 能核准 target 並產生 `OrderIntent`；任何送單能力屬 P7 之後；
  walk-forward/profitability 主張屬 P5。
- Provider Transport 的 rolling canary 義務（OPEN-027）：P6 Shadow 前需另行授權 synthetic canary，
  於 rolling 7 日且 ≥200 logical calls 達 first-attempt≥95%／eventual≤3 attempts≥99%，跌破即重開。
  該義務現在綁定現行 NVIDIA route（單批 V14 snapshot 不構成永久可用性證明）。
- Keychain：active 分析 provider 憑證為 `seven-lens.paper-trading.analysis-provider.api-key`／
  account `primary`（已含 NVIDIA key）；舊 `seven-lens.paper-trading.agnes.api-key` 項目保留未刪、
  active composition 不讀取，建議操作者擇期刪除。
- OPEN-002/003/004/005/006/007/025/027 等未結 issue 的關閉條件不變；詳見 `ISSUES.md`。
- OPEN-036（多來源／security master）中P4-A／P4-B的implementation與獨立驗收子範圍已Closed；source rights、
  真實provider entitlement、P4-C～F production composition與P5 time-travel residual仍依issue條件保持Open。
- OPEN-037的P4-B identity／quarantine子範圍已Accepted／Closed；已持有部位的`CORPORATE_ACTION_EXIT`、P5 replay、
  P6 shadow與P7 submit authority仍未實作／驗收。架構文件不等於order authority。
- CI 注意事項：postgres-integration service container 的 tmpfs 已固定為 1g（512m 會被整合套件的
  WAL churn 耗盡導致容器崩潰）；本機 `run_postgres_integration.sh` 使用disk-backed anonymous volume，
  不再把WAL壓力放進Docker VM記憶體，但仍應避免與其他高記憶體容器並行。

## 5. 下一個單一步驟

**開始 P4-C implementation。** 本輪 NVIDIA route 的完整 regression、P3-E／P3-F current-code live
evidence 與證據同步已完成。P4-C～F仍須各自完成 implementation／acceptance；F acceptance 同時是 P4
Combined Final Gate。不得把 provider evidence 或 P4-A／P4-B 的 Accepted 擴張成 broker/order authority。

## 6. Generic Analysis Provider（NVIDIA active route）

- **Active route**：operator config 為 `https://integrate.api.nvidia.com/v1`＋`openai/gpt-oss-120b`，route_config_hash
  `0659d8fa9b38c9e7a800ce2bdc89b14eeb76a5c83f157f6b65afcbe568162524`，policy `analysis-route-v1:`+hash。
  兩個指令：`python -m seven_lens.cli.analysis_provider set-endpoint …`／`set-model …`；
  config存於`${XDG_CONFIG_HOME:-$HOME/.config}/seven-lens/analysis-provider.json`（strict schema＋SHA-256
  route hash＋atomic write＋flock）；package-owned default仍為legacy Agnes base/model（generation=0）。
- **Offline implementation**：`config/analysis_provider.py`、`cli/analysis_provider.py`、
  `infrastructure/chat_completions_transport.py`、`infrastructure/analysis_providers.py`、
  `application/analysis_provider_composition.py`、generic secret kind
  `seven-lens.paper-trading.analysis-provider.api-key`、bounded generic audit route identity（`route_config_hash`）、
  additive migration `0022_analysis_route_identity_{up,down}.sql`（down在generic rows存在時fail closed 55000）、
  model id允許單一`/`（envelope version以`route_model_version`投影，route closure仍綁exact model id）。
- **P3-F split**：`p3f-synthetic-v14`綁定現行 route（616 cases、offline 616/616 byte-match、regeneration
  deterministic）；歷史 V12 bytes 不變。live path（plan/run/request hash）綁定當次 route snapshot。
- **Current-code P3-E live**：6/6 SUCCESS、retry=0、fallback=null，p50=`6114ms`、p95/max=`18804ms`；
  tracked sanitized evidence為`docs/P3E_LIVE_EVIDENCE_2026-08-29_NVIDIA.json`。
- **Current-code P3-F live**：V14 260/260 strict、0 errors/retries/fallback，130/130 invalid/ambiguous
  pre-network rejects，first-attempt/eventual/valid-primary皆100%；local immutable evidence hash
  `9fcc76258883365990f47783b1b5f01226d813c40d6b703ef033ee66da5b16e0`，file SHA-256
  `c69c3d7e6ccaf78e772a503bca8d394c488c2d4ffe649f23f679dd33c81dca85`。這是單批snapshot，
  不取代P6前rolling canary義務。
- **Provider-drift調整**：NVIDIA/vLLM回應的非authority envelope metadata以明列allowlist＋bounded值驗證接受
  （未知欄位仍fail closed；content authority路徑不變）。
- **回歸**：V14 offline `616/616` 且 regeneration byte-identical；non-integration
  `2117 passed, 282 deselected`；PG16 `280 passed, 2 deselected, 0 skipped`；Ruff format/check、mypy、
  tracked JSON parse、Python compile與`git diff --check`全綠。

## 7. P1～P4-B post-integration deep review（2026-08-29）

- 多輪 Luna Max 以P1/P2、P3、P4-A/B分區後再fresh/cross-phase驗收；所有production修復均有先失敗後通過的
  focused regression，最後由官方完整non-integration與PostgreSQL 16 gates統一驗證。
- 主要authority修復：P2 broker mirror/execution/broker-order identity衝突durable review/pause、batch fill
  preflight與immutable PG identity；P3 live route在Keychain/evidence/POST前exact preflight；P4-B migration 0023
  收緊runtime SECURITY DEFINER的source closure、canonical arrays與payload scalar contract。
- `0021_p4b_authority_hardening` up/down SHA-256仍為`e871c353…b19b9`／`759d3fc6…a00b`；0023 down後
  function、owner、ACL、search_path與constraints精確還原0021，up/down/up通過。最新schema version=23。
- 最終證據：`2117 passed, 282 deselected`；真PG16 `280 passed, 2 deselected, 0 skipped`；新增P2 identity
  6 tests與P4-B adversarial 4 tests均由official collection執行。沒有live/provider/source/broker/Keychain call。
- 發布鏈：`74e1c23`（generic NVIDIA provider）→`eb6f214`（P1～P4-B hardening）→治理同步commit；
  `main`與`origin/main`已同步。

## 8. 文件地圖

- 現行治理：`PROGRESS.md`（gate 狀態）、`docs/ROADMAP_AND_ACCEPTANCE.md`（剩餘階段與完成條件）、
  `DECISIONS.md`、`ISSUES.md`、`RISK_REGISTER.md`、`WORKLOG.md`（逐輪歷史）、`P4_PROGRAM_PLAN.md`
  （已核准P4設定、A～F分配與prompt map）。
- 設計基線：`docs/MASTER_PLAN.md`、`docs/ARCHITECTURE.md`、`SECURITY.md`、
  `docs/OPERATIONS_AND_SAFETY.md`、`docs/SOURCES.md`。
- 未來／停用：`docs/MEMORY_CURATION_SKILL_SPEC.md`、`docs/TRADINGAGENTS_ASSESSMENT.md`、
  `docs/DISTILLATION_SPEC.md`。
- 歷史歸檔：[`docs/archive/`](docs/archive/) — `prompts/`（已用完的 P3-D/E/F implementation／acceptance
  prompts）、`handoffs/`（P3 關門當下的前一版 handoff 快照）、`p3/`（P3F requirement map）。
  歸檔文件僅作歷史紀錄，不是現行授權或完成證據。
