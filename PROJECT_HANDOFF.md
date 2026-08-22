# P3-B+C Current Handoff

最後更新：2026-08-22
專案：`/Users/zongen/Downloads/codex/trading`

## 1. 目前唯一狀態

**P3-B Accepted; P3-C Accepted; Combined Gate Closed.**

- P3-B 經最新獨立重新驗收判定 Accepted。
- P3-C 首輪 Rejected 後完成 R1；R2修復run identity、deadline與PostgreSQL authority；R3修復
  5 High＋1 Medium；R4修復3 High；R5修復snapshot身分鏈1 High；最新驗收再發現
  duplicate-input authority 1 High並完成R6。
- P3-C R6 已由新的獨立session從source、對抗PoC、完整unit與真實PostgreSQL 16重新驗收為
  Accepted；P3-B與P3-C兩個子Gate均已通過，因此Combined Gate Closed。
- 下一步由使用者決定是否提交／推送本工作包，以及是否另行授權開始P3-D；Gate Closed本身
  不授權commit、push、真實provider、Paper送單或後續phase。
- 本工作包未提交；`main`／`origin/main` 仍為
  `def706440c7dda1a61610a9ea42b42005dfe115a`。

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
| P3-B+C | Closed | 兩個子Gate均Accepted；工作包仍未提交 |

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

## 6. 必跑驗證

使用隔離 uv cache：

```bash
UV_CACHE_DIR=/private/tmp/seven-lens-uv-cache \
  uv run pytest -q \
  tests/test_p3bc_evidence_and_infrastructure.py \
  tests/test_p3bc_analysis_pipeline.py

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
- `HEAD`與`origin/main`仍同為`def706440c7dda1a61610a9ea42b42005dfe115a`；未stage、commit、
  push，未使用credential或外部API，未觸發或跳過Codex Cyber項目。

## 7. 不可擴張邊界

- Paper-only；不加入 live endpoint、live adapter 或 live switch。
- 不使用真實 Alpaca／Tavily／Agnes／OpenCode／OpenAI credential或API。
- 不改 P2 execution／reconciliation／control／broker authority。
- 不開始 P3-D Risk Debate／Portfolio Manager、P3-E provider、P3-F memory/evals或 P4 Risk。
- 不讀或發布 repository 根目錄忽略的 `skill/` corpus。
- 不因本機綠測試自行宣告 Gate Closed；也不得把 push／CI 成功等同獨立驗收。
- 未經使用者授權，不 stage、commit、push、建立 PR 或 merge。

## 8. 下一個單一步驟

P3-B+C Combined Gate已Closed。由使用者決定是否提交／推送本工作包；P3-D仍為Not started，
必須取得另行授權後才能開始。
