# Progress

## 狀態摘要

- 專案階段：P3 — TradingAgents 分析核心整合；P3-A Gate Closed（2026-08-21 remediation-R1 獨立重新驗證 Accepted）。P2 Gate Closed，這不授權真實下單。
- 完成度定義：只以路線圖的可驗證交付物計算，不以主觀百分比計算。
- 最近更新：2026-08-21
- 下一個 gate：P3-B 起的分析管線工作包（point-in-time inputs、四分析員、兩輪辯論、Research Manager、Trader、Risk Debate、Portfolio Manager，見 OPEN-024~026）；WS/CLI 與真實下單仍分別留 P6/P7，P8 無人值守門檻保留。
- 歷史：P2 曾於 2026-08-19 及 P2-CUR 修復後標示 Closed；2026-08-20 依 final remediation ACC-001~009 再次重開。歷史 Closed 紀錄保留，但不是目前狀態。

## 已完成

- [x] 確認新專案與工作區範圍。
- [x] 2026-08-14 歷史基線曾確認七人委員策略、本人使用、Paper-only；策略部分已由 2026-08-21 ADR-027／ADR-028 supersede，Paper-only 不變。
- [x] 確認零付費資料與公開來源限制。
- [x] 分析 TradingAgents 的實際 agent graph、state、portfolio manager 與訊號輸出。
- [x] 核對 Alpaca Paper Trading 的模擬限制與 order update/reconciliation 能力。
- [x] 核對 Tavily 免費額度與 rate limit，形成硬性資料預算。
- [x] 將使用者持有的 7 個 Tavily 帳號納入條件式 account-pool 架構；現行條款下先建立合規 Gate。
- [x] 搜尋七人現成 GitHub 蒸餾資產；判定沒有一套可直接作為生產依賴。
- [x] 依使用者 2026-08-16 決定，將七位更新為 Howard Marks、Muddy Waters Research、Aswath Damodaran、Serenity、Terry Smith、Michael Mauboussin與Lyn Alden；同步主企劃、蒸餾規格、來源策略與 handoff。
- [x] 確認本機 `skill/` 有七位候選語料（約 827 MB／723 個非 `.DS_Store` 檔案）；依使用者要求本輪不審查內容，且依再散布邊界排除於公開 Git repository。
- [x] 七位與語料規劃 commit `1d4d9bd31d993a5fb6803a8d08ff5deec04122e1` 已發布；GitHub Actions run [`31950919861`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31950919861) 的 `quality-unit` 與 `postgres-integration` 均成功。
- [x] 建立主企劃、架構、蒸餾、營運、安全、路線圖和來源規格。
- [x] 依使用者 2026-08-21 決定，將七人蒸餾移出核心主線並保留為 disabled Future Analyst Plugin；P3 最終改為完整 TradingAgents 四分析員→兩輪 Bull/Bear→Research Manager→Trader→兩輪 Risk Debate→LLM Portfolio Manager，P4 deterministic Risk approval，P5 validation，P6/P7/P8 安全 gate 保留（ADR-028）。
- [x] 2026-08-21 重新核對 TradingAgents upstream `main` 仍為固定 commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`，並檢查現行 analyst、debate、structured Research Manager/Trader、risk debate 與 Portfolio Manager 邊界。
- [x] 建立獨立 Decision、Progress、Issue、Worklog、Risk 日誌。
- [x] 完成 P1-A：Python 3.13 `src` package、`uv.lock`、ruff/mypy/pytest 嚴格基線。
- [x] 完成 Paper-only typed config、精確 endpoint allowlist 與啟動 fail-closed 驗證。
- [x] 完成 Tavily compliance domain、可稽核 evidence-record schema、per-account/global quota 與 usage/reset/cooldown schema；外部 verifier 尚不存在，因此 `AUTHORIZED_ACCOUNT_POOL` 固定 fail closed。
- [x] 完成 RunId、TradingDate、UtcTimestamp、SchemaVersion，以及 redaction-first JSON logging 骨架。
- [x] 修復獨立安全驗收發現：Basic／quoted credentials、非 JSON-safe values、mapping keys、cycle/depth 與安全 fallback；canonical UTC wire format 與 SchemaVersion 上限。
- [x] P1-A 重新驗證：formatter、lint、mypy 全通過；128 個 normal/boundary/invalid/adversarial/fail-closed tests 通過。
- [x] 完成 P1-B persistence-neutral repository/unit-of-work ports 與 psycopg 3 PostgreSQL adapter；domain/application ports 不依賴 PostgreSQL、psycopg、SQLAlchemy、Alembic 或 SQLite。
- [x] 完成 checksummed initial up/down migration：`schema_metadata`、`schema_migrations`、`domain_events`、`audit_events`、`job_instances`、`job_leases`；沒有建立 broker/order/fill/position 表。
- [x] 完成 database-stamped event envelope、aggregate contiguous sequence/idempotency、append-only domain/audit triggers，以及應用層＋DB 層 audit secret rejection。
- [x] 完成 rollback-by-default unit of work 與 job status + audit 同 transaction service；audit insert 失敗時狀態同步 rollback。
- [x] 完成 DB-clock atomic lease acquire/renew/release/expiry takeover、attempt count、fencing token、stale-owner write rejection、lease history 與 direct-field mutation guard。
- [x] 完成 pure market clock port 與 deterministic fake，覆蓋 regular session、half-day、holiday/closed-day 及 open/close boundaries；沒有連接 Alpaca 或建立排程。
- [x] P1-B 真實 PostgreSQL 16 驗證：18 個 migration/constraint/transaction/concurrency/restart integration tests 通過；完整 suite 242 tests 通過，formatter/lint/mypy/lock checks 全通過。
- [x] 完成 P1-C1 implementation：typed `SecretRef`／`SecretValue`、persistence-neutral `SecretProvider`、capability-scoped exact lookup、Security.framework read-only macOS adapter 與 2 秒 spawned hard-timeout worker；沒有 env/argv/DB/fake fallback。
- [x] P1-C1 agent 當時自測：72 個新增 fake-only tests、65 個既有 redaction/structured logging/broker/Tavily regression tests，以及 296 個完整 non-integration tests 通過；Ruff/Mypy/lock checks 通過，未查詢真實 Keychain；之後另行通過獨立驗收。
- [x] P1-C1 已通過獨立驗收；secret boundary 狀態不再是 pending，但仍不代表整個 P1-C 或 P1 Core Gate 完成。
- [x] 完成 P1-C2 implementation：canonical non-zero trace/span IDs、explicit immutable context、dependency-neutral typed recorder ports、封閉五 metric／兩 span registry、64-series guard、fail-safe facade、deterministic fakes，以及 secret lookup／transactional job transition instrumentation。
- [x] P1-C2 agent 自測與單一驗收修復：79 個 telemetry tests；完整 non-integration `391 passed, 19 deselected`，真實 PostgreSQL 16 integration `19 passed, 0 skipped`；Ruff/Mypy通過。job transition現在於任何span/UoW/DB操作前綁定audit與telemetry的run/correlation identity；之後已通過獨立驗收。
- [x] 完成 P1-C3 implementation：兩個 Ubuntu 24.04 GitHub Actions jobs、完整 SHA/digest pin、read-only/zero-secret policy、static integration marker、required PostgreSQL 16 preflight 與 skip-to-failure session gate。
- [x] 完成 `verify_p1.sh` 與 `run_postgres_integration.sh`：uv-only bootstrap、locked checks、random localhost port、tmpfs、bounded readiness，以及 container ID/name/ownership label 三重核對 cleanup。
- [x] P1-C3 agent 本機自測：workflow/gate/script 對抗測試 `16 passed`；non-integration `407 passed, 19 deselected`；真實 digest-pinned PostgreSQL `16.15` integration `19 passed, 0 skipped`；Ruff/Mypy/lock checks 通過，uv.lock hash 未變且沒有殘留 owned container 或 volume。狀態仍為 `implementation completed, pending independent acceptance`。
- [x] P1-C3 clean-machine evidence：在排除專案 `.venv` 且使用全新空 uv cache 的隔離副本依序執行兩個一鍵命令；兩次 non-integration 均 `407 passed, 19 deselected`，PostgreSQL `19 passed, 0 skipped`，lock SHA-256 前後皆為 `79809edba36965084b7561d616b0f95902e28e8fd4da6b07f35c409b6b34626b`；volume set未變、owned container為空，隔離副本已精確移除。
- [x] P1-C3 已通過定點獨立驗收：官方 release／commit 與本機 RepoDigest 查核吻合；16 個對抗測試、Ruff、Mypy、`407 passed, 19 deselected`、真實 PostgreSQL 16.15 `19 passed, 0 skipped` 均通過。required mode 的 missing URL 與 SQLite 各自於 collection 前以 exit 4 fail closed；另一份排除 `.venv` 且使用全新空 uv cache 的副本也通過兩個一鍵命令，lock／volume hash 不變且 owned container 為空。
- [x] 建立公開且獨立的 [`ihsieh31/seven-lens-paper-trading`](https://github.com/ihsieh31/seven-lens-paper-trading) repository；初始遠端 workflow 的 invalid-context 問題在獨立驗收中被發現並修正。
- [x] GitHub Actions run [`31868962828`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31868962828) 在 commit `4e795ff1dc6d5b6bc51d4bd0e55149fda3e4cc61` 上通過：`quality-unit` 為 `407 passed, 19 deselected`，Ruff/Mypy/lock checks 全通過；`postgres-integration` 使用 PostgreSQL 16.15 且 `19 passed, 0 skipped`。P1 Core Gate 因此關閉。
- [x] 完成P1 authority hardening：migration/runtime PostgreSQL roles分離、`SECURITY DEFINER`
  `pg_catalog, public, pg_temp`與完整schema qualification、PUBLIC CREATE/TEMP/EXECUTE revoke、typed
  domain/audit payload registry、persisted JSON resource budgets、`SECURITY.md`與Risk Register lifecycle。
  真實PostgreSQL已驗證runtime正常repository path、direct DML／trigger／function／TEMP denial、temp
  shadowing、stale fencing、catalog ACL與migration up/down/up；code commit
  `e8543b69bfc6a6d2dd9a87837d9d46bb11afc406`的GitHub Actions run
  [`31891905869`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31891905869)
  亦通過：`quality-unit`為`440 passed, 33 deselected`，`postgres-integration`為`33 passed`。
  後續handoff/evidence commit `5b3cd501c7ef415cbb27c3e0b5762ecdb7a609ea`亦由GitHub Actions run
  [`31892024588`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31892024588)驗證，
  最終已發布`main`的兩個required jobs均為`success`。

## 尚未開始

- [x] P2 獨立驗收（新 session 定點驗收：雙邊狀態機、0003–0005 對抗重現、mismatch 自動
      暫停、runtime role 權限）。已於 2026-08-19 完成並 Closed；2026-08-20 依 P2-CUR-001~006
      remediation 重開並再次 Closed（見下方「P2 Remediation 2026-08-20」節）。P2-E 真實 read-only 連線驗證已完成首次執行（2026-08-17，見下方 P2-E 證據）。
- [ ] WebSocket 傳輸本體與 control shell CLI（ADR-019 範圍聲明，P6/P7 bring-up）。
- [x] P3-A：implementation 與 remediation-R1 均已完成；2026-08-21 獨立 acceptance session 完成重新驗證，判定 remediation-R1 Accepted，**P3-A Gate Closed**（證據見下方「P3-A 獨立重新驗證」節）。已固定 upstream SHA／Apache-2.0 inventory，建立 strict contracts、canonical wire、golden／adversarial／source-invariant tests，並完成驗收後五項修復。
- [ ] P3-B~F：point-in-time inputs/event verification、四分析員、兩輪 Bull/Bear、Research Manager、Trader、兩輪 Risk Debate、Portfolio Manager、Agnes/OpenCode provider isolation、daily reflection、weekly 4,000-line memory skill、record/replay 與 adversarial evals。
- [ ] Future Analyst Plugin 七人 corpus 審查／蒸餾：`DEFERRED/DISABLED`；未經使用者重新核准，不讀取 `skill/`、不排入主線。
- [ ] 取得 Tavily 對同一 Customer 彙總使用 7 個免費帳號的書面／後台授權證據。
- [ ] P4 實作量化 long/short 預篩、`PortfolioProposal` validation／target-to-quantity translation、一次駁回重申與 deterministic Risk。
- [ ] 建立回測／walk-forward／shadow／paper 驗證鏈。
- [ ] 啟用本機 launchd 與告警。

P1-C3 沒有建立 hosted macOS job、OpenTelemetry/exporter/backend、broker adapter、Tavily/OpenAI
client、資料下載、策略、下單、排程或 launchd。P1 Core Gate 已有遠端 CI 證據並關閉，但沒有
擴張交易權限或進入 P2 implementation。

本次hardening沒有修改native Keychain query、查詢／建立／刪除Keychain item、加入coverage threshold或
第三個CI job，也沒有實作P2 config/DB credential composition；這些分別是未證實建議、需另行授權的
native evidence gap，或後續quality/P2 composition工作包，詳見ADR-016與`ISSUES.md`。

## Gate 規則

任何 gate 只有在對應測試、報告與證據檔已保存時才算通過；「程式可以跑」不等於可無人值守。

## P2-A — 執行 domain 契約與 fake broker harness：implementation completed, pending independent acceptance

2026-08-17 依使用者核准之 P2 方向（P2-E 僅 read-only 真實連線、金鑰需要時才提供、P2-A 先行、
P2 手動觸發 job）完成第一個工作包：

- `src/seven_lens/execution/orders.py`：封閉 typed domain（Symbol/Side/Quantity/Price/
  UsdAmount/PriceCollar/ClientOrderId/OrderIntent/BrokerOrder/Fill）與雙狀態機
  （內部 lifecycle 含 UNKNOWN 解析語意、broker mirror lifecycle）；詳見 ADR-017。
- `src/seven_lens/application/ports/broker.py`：網路中立 PaperBrokerPort、封閉
  RejectionReason、BrokerTransportError/QueryError/ConflictError 分類、PaperAccount
  強制 PAPER 斷言。
- `src/seven_lens/execution/fake_broker.py`：決定性 fake Paper broker 與一次性 fault plans
  （timeout before/after accept、reject、partial/full fill 腳本、cancel/expire、
  同 id 不同參數 fail-closed）。
- `migrations/0003_execution_orders_{up,down}.sql`：order_intents（client_order_id UNIQUE +
  組合 CHECK + identity immutable + 狀態轉移 guard）、broker_orders（FK 鏡像 + guard）、
  fills（append-only trigger）；`verify_schema` 與 `provision/verify_runtime_role` 同步擴充。
- 未建立任何網路 client、真實 adapter、outbox worker、reconciliation 或 launchd；未使用
  Alpaca/Tavily/OpenAI 憑證；未 stage/commit/push。

實作自測證據（2026-08-17 本機）：

- `scripts/verify_p1.sh` 全綠：Ruff format/lint、Mypy strict 66 source files、
  non-integration `501 passed, 54 deselected`、offline lock check 通過。
- 真實 disposable PostgreSQL 16 container（`scripts/run_postgres_integration.sh`）：
  `54 passed, 0 skipped`，含新增 21 個 execution schema 對抗測試（非法轉移、identity
  不可變、組合鍵偽造、append-only UPDATE/DELETE、FK/UNIQUE、up/down/up restore）。
- 過程中發現並修正：0003 down migration 初版漏列 `DELETE FROM schema_migrations WHERE
  version = 3`，導致 fixture teardown 的 rollback 迴圈無限重複；補列後整合測試由懸死
  轉為 5.93 秒完成。此缺陷已以真實 PostgreSQL 重跑關閉。

依 gate 規則，上述為實作方自測證據；P2-A 需獨立驗收後才可標記關閉。

## P2-B~E — 執行引擎、對帳、控制平面、Paper adapter：implementation completed, pending independent acceptance

2026-08-17 同日完成（皆在 P2-A 契約之上，無網路呼叫、無真實憑證）：

- P2-B：`OrderRepository` port + psycopg adapter（guard-backed 轉移、fills 冪等 ON CONFLICT）、
  `ExecutionEngine`（SUBMITTING 先持久化、timeout→UNKNOWN、同 id 解析/重送、cancel/expire/recover）；
  單元 18 案 + 真實 PostgreSQL 端到端。
- P2-C：`execution/ledger.py`（cash delta + FIFO lots，fail closed）、`Reconciler.collect/run`
  （mismatch 自動暫停）、migration 0004（reconciliation_runs，append-only、kinds CHECK）。
- P2-D：`execution/control.py` + `ControlPlane`（pause/resume(CLEAN 門檻)/cancel/flatten/
  shutdown）、migration 0005（control_commands/control_state）、`application/composition.py`
  （exact-schema 設定、execution secret allowlist、Alpaca 憑證 all-or-nothing）；新增
  POSTGRES_RUNTIME_PASSWORD SecretKind（含 telemetry attribute 與 service mapping）；
  DSN 組合在 infrastructure（RuntimeDsn 不洩漏）。
- P2-E：`infrastructure/alpaca_paper.py`（injectable transport、嚴格解析、408/429/5xx→
  outcome-unknown、limit day order body）；測試全程零網路。

對抗式審查（同日）發現並修復：Reconciler 缺持久化與自動暫停編排（補 `run()`）、resume 以字串
比較（改 enum）、ledger 賣出現金無上界（補檢查）、broker 端不可表示狀態裸抛 DB 錯誤（改為
ExecutionStateError 且零副作用）；各補對抗測試。已知邊界記錄於 ADR-018。

最終自測證據（2026-08-17 本機）：`scripts/verify_p1.sh` 全綠（Ruff、Mypy strict 82 檔、
non-integration `563 passed, 60 deselected`、offline lock check）；真實 disposable
PostgreSQL 16 `60 passed, 0 skipped`；容器由 script trap 精確清理。未讀取 Keychain、未使用
任何 API 憑證、未 stage/commit/push。依 gate 規則，P2-B~E 需獨立驗收後才可關閉。

## P2 收尾（第二輪）：trade update consumer、NAV、整合驗證與第二輪對抗審查

2026-08-17 第二輪以 ROADMAP P2 交付/驗收清單逐項比對後補齊：

- `execution/trade_updates.py`：TradeUpdateConsumer（duplicate/out-of-order/unknown/
  外部取消路由），驗收標準「duplicate/out-of-order WebSocket events idempotent」於本機
  事件流層完成（WS 傳輸本體依 ADR-019 延後至 P6/P7）。
- `execution/ledger.py` 新增 `account_valuation`（NAV = 期初現金 + fill 效果 + 市值；
  缺價 fail closed）。
- Engine 補 CANCEL_PENDING crash 恢復測試；repository 新增 `get_broker_order_by_id`。
- 真實 PostgreSQL 整合新增：reconciliation run 持久化與 latest 排序、mismatch→自動
  pause_entries 於真實 DB、control_commands append-only 對抗（UPDATE/DELETE 被 55000
  拒絕）、control_state 單例與暫停-原因配對 CHECK。
- 第二輪對抗審查修復：同狀態重播事件由 APPLIED 改判 DUPLICATE（零寫入）；外部取消經
  CANCEL_PENDING 路由（consumer 與 engine 語意一致）；FakeOrderRepository 的
  updated_at 隨變更單調前進以鏡像 DB trigger。
- 最終 gate：`verify_p1.sh` EXIT=0（Ruff、Mypy strict 85 檔、non-integration
  `575 passed, 63 deselected`）；真實 PostgreSQL 16 `63 passed, 0 skipped`；無容器殘留。
  P2 全部工作包維持 implementation completed, pending independent acceptance。

## P2-E 首次真實 read-only 驗證（2026-08-17，operator 授權執行）

新增交付（不改任何核心程式碼以外的檔案；三個授權最小修復見下）：

- `src/seven_lens/cli/p2e_readonly_verify.py`：GET-only stdlib HTTPS transport
  （bounded 429 retry 尊重 Retry-After、timeout/5xx/畸形 body 一律
  `BrokerTransportError` fail-closed、URL allowlist = Paper endpoint、request
  journal 只記 method+path）；CLI 以 `ExecutionStackConfig.from_mapping` 解析設定、
  `ScopedSecretProvider(execution_secret_refs())` + Keychain 解析憑證、
  `compose_runtime_dsn(RuntimeDatabaseConfig)` 組 DSN；讀取 account/positions/
  open orders/fills 後以 `Reconciler.run` 將 CLEAN/MISMATCH 結果寫入
  `reconciliation_runs` 作為持久化證據；stdout 輸出人讀報告 + JSON，不洩漏 secret。
- `tests/integration/test_p2e_readonly_live.py`：新增 `live` marker；offline
  fail-closed transport 測試（in-process HTTP server 注入 429/503/畸形 body/
  斷線/timeout，7 案零網路）＋ 需 `SEVEN_LENS_P2E_LIVE=1` 的真實驗證測試。
- `pyproject.toml` 僅註冊 `live` marker。

執行證據（2026-08-17，真實 Alpaca Paper 帳戶 + TLS-enabled disposable
PostgreSQL 16.15）：

- CLI exit=0；僅 GET：`/v2/account`、`/v2/positions`、
  `/v2/orders?limit=500&status=open`（多次），無任何 POST/DELETE。
- 帳戶 `PA3I3A3G8N70`（PAPER）：cash 100000.00、equity 100000.00；positions 0、
  open orders 0、known fills 0；reconciliation CLEAN（run_id
  `feda0ec8-b947-44d2-897c-29668b7d2453`，checked_orders=0、checked_fills=0）已
  持久化於 `reconciliation_runs`（verified via psql）。
- `pytest tests/integration -m integration`（TLS PG + live env）：
  **72 passed, 0 skipped**；non-integration 578 passed；Ruff/Mypy strict 全綠。

授權最小修復（P2-E 真實驗證暴露的既有缺陷；均經 operator 逐項授權）：

1. `infrastructure/macos_keychain.py`：query `kSecMatchLimitAll` → `kSecMatchLimitOne`
   （此 macOS 上 All+ReturnData 回 errSecParam -50；P1-C1 驗收僅 fake-based，從未
   觸及真實 `SecItemCopyMatching`）；`_normalize_native_items` 支援 PyObjC NSData
   （buffer protocol）正規化為 plain bytes。
2. `infrastructure/alpaca_paper.py`：`_usd`/`_price` 以 `_two_decimal_decimal` 正規化
   至恰好兩位小數（exponent<-2 或量化不相等仍 fail closed）；真實 API 回傳
   `cash: "100000"`（整數）而 `UsdAmount`/`Price` 要求恰 2dp。
3. 首次真實驗證執行後，macOS 曾彈 Keychain 授權視窗（operator 選 Always Allow），
   後續 spawn child 讀取即時成功。

執行後狀態：Keychain 已存入 Alpaca Paper key/secret 與 disposable runtime
password（service 名同 `_SERVICES`，account=primary）；disposable PG 容器已清理。
待 operator 決定：證據（本節）與程式碼變更是否 commit/push。

## P2 補強輪（2026-08-17）：獨立對抗審查五缺陷修復——implementation completed, pending independent acceptance 維持不變

依 ROADMAP 驗收要求對 P2 交付進行獨立對抗審查（第二 session、以 ISSUES.md A–N 缺陷清單逐項
重現），五個真實缺陷先 red reproduction 證明、後修復並補防回歸測試：

- **A（pause bypass，Critical，ISSUES CLOSED-017）**：`ExecutionEngine` 新增 `control`
  state source；`submit_from_outbox` 在 SUBMITTING 轉移／commit／broker 呼叫前檢查
  `entries_paused`，違反時抛 `ExecutionPausedError`（零副作用）；RISK_EXIT（緊急離場）放行、
  risk-reduction（cancel/expire/fills）不受影響、resume 不需重建 engine。composition 以
  `build_execution_stack(..., control=...)` 注入同一 control repository，reconciliation
  mismatch 自動暫停立即生效。測試：`tests/test_execution_pause_remediation.py`（先 red：
  缺依賴 TypeError／paused 下仍回 ACKNOWLEDGED，後 5/5 綠）。
- **E（broker_orders 時鐘混用，High，ISSUES CLOSED-018）**：migration 0006 新增
  `broker_orders.broker_updated_at`（broker 時間；`guard_broker_updated_at` trigger 強制單調
  不倒退）與本地 `updated_at`（statement_timestamp，稽核）分工；repository／fake 同步；
  `TradeUpdateConsumer` 的 STALE 基準改 broker 時間。測試：
  `tests/integration/test_broker_order_timestamps_postgres.py`（先 red：roundtrip 回讀本地
  時間／clock-skew 事件被 STALE 丟棄，後 2/2 綠）。
- **F（重複 client_order_id 誤分類 REJECTED）**：Alpaca submit 遇 400/422 以
  `GET /v2/orders:by_client_order_id` 解析：參數一致回 `SubmitAccepted`（冪等 recovery），
  矛盾或查不到回 rejection；follow-up GET 非 2xx fail-soft。測試：`test_alpaca_paper_adapter.py`
  `TestDuplicateClientOrderId`（先 red：重複回 `SubmitRejected`，後綠）。
- **H（fills 分頁只取一頁）**：`list_fills` 改以 `after` cursor 循環取滿（`limit=100`、
  execution_id 去重、non-advancing 拋 `BrokerTransportError`）。測試：
  `test_alpaca_paper_adapter.py` `TestFillPagination`（先 red：101 fills 只回 100，後綠）。
- **G（reconciler 漏報終態分歧）**：新增 `MismatchKind.INTENT_STATUS_MISMATCH`；terminal
  intent（FILLED/CANCELED/EXPIRED/REJECTED）而 broker 仍開單→mismatch；第二趟掃
  `list_all_broker_orders` 捕捉 terminal mirror vs broker 開單。測試：
  `test_reconciliation_and_ledger.py`（先 red 兩案，後 17/17 綠）。
- **N（CI 缺 PostgreSQL integration job）**：`.github/workflows/ci.yml` 新增 postgres job
  （`uv run --locked pytest tests/integration -m "integration and not live"`）；本機
  `scripts/run_postgres_integration.sh` 同步；live 只能經 P2-E CLI 手動執行。測試：
  `test_p1_c3_ci.py::test_workflow_commands_match_p1_c3_contract`（先 red，後綠）。

最終 gate（2026-08-17）：Ruff format/check 與 Mypy strict 90 檔全綠；non-integration
`589 passed, 74 deselected`；真實 disposable PostgreSQL 16 integration
`66 passed, 8 deselected`（live marker 排除，`SEVEN_LENS_P2E_LIVE` 未設）；`verify_p1.sh
--postgres` EXIT=0、無容器殘留。未讀取 Keychain、未使用任何 API 憑證、未 stage/commit/push。
依 gate 規則，P2 全部工作包維持 implementation completed, pending independent acceptance。

## P2 second remediation（2026-08-18）：broker 真值未知即不宣告終態——implementation completed, pending independent re-acceptance

依第二輪規劃（ADR-022、ISSUES CLOSED-020/021）完成執行安全硬化；migration 0007
（up/down 成對）承載全部持久化變更：

1. **引擎語意**（`execution_service.py`）：`resolve()` 重寫——deadline 後 GET 無單 →
   SUBMITTING 轉 UNKNOWN（已 UNKNOWN 保持），絕不自行宣告終態；`expire_overdue()` 只對
   從未到過 broker 的 CREATED/RISK_APPROVED/OUTBOX_PENDING 本地 EXPIRED，SUBMITTING/
   UNKNOWN 一律 resolve，ACKNOWLEDGED/PARTIALLY_FILLED/CANCEL_PENDING 一律先取消、
   transport/config 錯誤保留 CANCEL_PENDING 交 recovery/reconciliation；`recover()` 修正
   同一 sweep 對同一 id 重複 resolve 的缺陷（snapshot both statuses、each id resolve 一次）。
2. **watermark 保守化**（0007 + `postgres.py` + `trade_updates.py`）：0006 本地 backfill 的
   broker_updated_at 全部清 NULL（unknown），domain 以 submitted_at 為 lower bound（永不
   隱藏 broker 事件）；trade updates 回放同值 → DUPLICATE、同 timestamp 不同值 →
   TradeUpdateError 明確衝突；stale 基準維持 < mirror.updated_at（NULL 時永不 STALE）。
3. **broker_orders SQL guard 完整化**（0007）：filled_quantity 永不倒退、FILLED 恰等
   quantity（INSERT+UPDATE 側）、身份欄位 immutable、`guard_broker_order_insert` 新增；
   status CHECK 完整 15 態；`REVIEW_REQUIRED` 納入 intent 狀態機與六個 review broker
   狀態收斂；0006 的「絕對單調不倒退」改為「僅當兩端皆非 NULL 才禁倒退」。
4. **flatten 六步**（`control_service.py`）：確認 FLATTEN_PAPER → 已 paused → resolve
   SUBMITTING/UNKNOWN → 取消 ACK/PARTIALLY/CANCEL_PENDING → apply_fills 收斂 →
   **broker position view vs 本地 ledger 逐符號一致，否則 abort（零新單）** → 價格經
   `FlattenPriceProvider` seam（預設 LedgerFlattenPriceProvider：本地最後成交價，零外部
   依賴）→ `control_state.flatten_generation` 同交易原子遞增為 target_version（重複
   flatten 永不碰撞 client order id）。
5. **資產閘**（`execution_service.py`）：submit 前 `get_asset` 驗證 symbol 已知且
   tradable，fail-closed（含 RISK_EXIT）；flatten 對全部部位預檢後才進 generation 與下單。
6. **詳情對帳**（0007 + `reconciliation_service.py` + `postgres.py`）：新增 append-only
   `reconciliation_mismatches`（run_id+ordinal+kind+detail，200 char bounded）；
   closed-history pass 以 `list_recent_orders(since=前一輪 observed_at；無前輪則
   trading_date 00:00 UTC)` 重掃已關閉 broker 單，補 UNKNOWN_BROKER_ORDER/
   STATUS_MISMATCH/MISSING_LOCAL_FILL 三類漏報，逐單去重不上報已報告者；
   `INTENT_STATUS_MISMATCH` 納入 SQL kinds CHECK；runtime role 僅增 INSERT/SELECT，
   仍無 DDL。
7. **P2-E 補強**：trading date 改 `America/New_York` 會話日（`domain/session.py`，
   p2e CLI 同步）；`p2e_readonly_verify.py` 維持 GET-only（transport 非 GET 即失敗）。

測試（本輪新增/改寫）：`tests/test_execution_engine.py`（TestPendingCancelCutoff 4 案、
TestBrokerTerminalRecovery、TestDuplicateDelayedVisibility、TestAssetGate 3 案）、
`tests/test_control_plane.py`（flatten abort/disagreement/price seam/generation 三連等
5 案）、`tests/test_reconciliation_and_ledger.py`（closed-history 4 案）、
`tests/test_session.py`（單元 5 案）；fake repos 與 `tests/fakes/orders.py` 同步
filled/identity invariants；`tests/integration/test_execution_schema.py` 依完整狀態機
改寫（ACCEPTED→REJECTED 現為合法、倒退 RECEIVED 仍禁）、`test_control_and_reconciliation_
postgres.py` 新增明細表 roundtrip + append-only 驗證、`test_migrations.py` 版本循環
6/7、`test_postgres_runtime_role.py` 驗證 reconciliation_mismatches 權限。

最終 gate（2026-08-18）：`uv sync --python 3.13 --locked`/`uv lock --check` 綠；
Ruff format/check 與 Mypy strict 92 檔全綠；non-integration `621 passed, 74 deselected`；
真實 disposable PostgreSQL 16 integration `66 passed, 8 deselected`（live marker 排除，
`SEVEN_LENS_P2E_LIVE` 未設）；`verify_p1.sh --postgres` EXIT=0、無容器殘留。
未讀取 Keychain、未使用任何 API 憑證、未 stage/commit/push。
依 gate 規則：**P2 second remediation implementation completed; pending independent
re-acceptance.**

## P2 獨立驗收與 Codex 回遷（2026-08-19）：歷史基線；gate 已重新 Open

- 重跑 `scripts/verify_p1.sh` 與真實 disposable PostgreSQL 16 gate，先確認既有 621/66
  基線，再以官方 Alpaca API 規格與對抗場景審查 recovery、flatten、asset、reconciliation。
- 發現並修復五類缺陷：pause 後 recovery 仍可能重送、orders/fills 分頁參數錯誤、flatten
  在 CANCEL_PENDING 未收斂時仍繼續、可交易非 US-equity 資產可通過、REVIEW_REQUIRED
  可被下一次 clean reconciliation 靜默清除。
- 新增 6 個回歸案例；最終 `verify_p1.sh` 為 627 passed / 74 deselected，Ruff、mypy、
  lock 全綠；PostgreSQL 16 integration 為 66 passed / 8 deselected。
- 已把 zcode 的新程式、migration、測試與 P2 文件同步回
  `/Users/zongen/Downloads/codex/trading`，並恢復 Codex 路徑/工具文字。未讀 Keychain、未跑
  live test、未 stage/commit/push；本輪遠端 CI 尚未執行。

## P2 全面再驗收（2026-08-19）：重新 Open 後已 Closed

- 使用者要求撤銷先前關門判定，使用已存於 macOS Keychain 的 Alpaca Paper credentials
  執行真實 GET-only 驗證，並由 Luna worker 進行獨立對抗審核。
- 關門條件：執行安全、PostgreSQL、控制平面、reconciliation、Paper endpoint read-only、
  credential/路徑隔離與所有新增對抗案例均通過；發現問題必須修復後重跑完整 gate。
- 明確排除：不送真實委託、不使用 POST/DELETE transport、不 commit/push。

### 全面再驗收結論：Gate Closed

- 真實 Keychain 的 Alpaca Paper key/secret 解析成功；只對 Paper endpoint 發出 GET。新增
  `run_p2e_live_acceptance.sh`，以獨立非 owner runtime role 在 disposable PostgreSQL 16
  持久化 reconciliation；live test 1/1 通過，未送單或取消。
- 官方契約核對修復單一 asset 路徑、open orders 500 筆分頁、fill bounded pagination／
  cursor cycle／order identity；未知 `held` 狀態維持 fail-closed。
- Luna 首輪重現 pause TOCTOU、control partial failure、broker query failure、open/history
  snapshot race 與 fill integrity；當時修復為 PostgreSQL `FOR SHARE` submission guard（後由 ADR-026 升級為 `FOR UPDATE`）、partial
  command `applied_at=NULL`、`BROKER_QUERY_FAILURE` 持久化並自動 pause，以及以 broker
  timestamp+status 合併快照。第二輪發現 equal-timestamp 不同狀態仍可漏報，第三輪確認已
  收斂為 `STATUS_MISMATCH`，原四組 blocker 全部 Closed。
- 最終證據：Ruff format/check、mypy strict 92 檔全綠；non-integration 637 passed / 77
  deselected；真實 PostgreSQL 16 integration 69 passed / 8 deselected；live acceptance
  1 passed。P2 gate **Closed**；未 stage/commit/push，遠端 CI 尚未執行，真實下單仍留 P7。

## P2 Remediation 2026-08-20：P2-CUR-001~006 修復後重驗——Gate 再次 Closed

- 依據 `SEVEN_LENS_P1_P2_CODEX_REMEDIATION_HANDOFF.md`（3bac368）重開 P2 gate，完成 5 個執行／帳務缺陷與 1 個 P2 規格缺口修復：
  1. P2-CUR-001 `latest()` 讀取時以 `reconciliation_mismatches` child rows 重建 `detail`，驗證 `mismatch_count`/`kinds`/`CLEAN 空`一致性，否則拋 `PersistenceInvariantError`（`src/seven_lens/infrastructure/postgres.py`）。
  2. P2-CUR-002 `LedgerInvariantError` 轉 durable `LOCAL_LEDGER_INVARIANT` mismatch + 自動 pause + `PAUSE_ENTRIES` 命令（`src/seven_lens/application/reconciliation_service.py`，migration 0008 擴 mismatch kinds）。
  3. P2-CUR-003 `TradeUpdateConsumer._apply_fill` 完整處理亂序：`filled_quantity = max(mirror, local_total)`、`broker_updated_at` 不倒退、已 terminal/review 不回退、PENDING_CANCEL 中可收 fills、衝突時保留 fill 並 fail closed（`src/seven_lens/execution/trade_updates.py`，`FakeOrderRepository` 同步）。
  4. P2-CUR-004 `project_ledger` 以 `(occurred_at, execution_id)` 為 canonical 回放序，與 DB arrival order 解耦；`ordered_lots` 改以 `opened_at.value` 排序（`src/seven_lens/execution/ledger.py`）。
  5. P2-CUR-005 UNKNOWN 全域閘（歷史實作，lock 後由 ADR-026 升級為 `FOR UPDATE`）：submit timeout → `UNKNOWN` 同時持久化 `entries_paused` + `PAUSE_ENTRIES`（`src/seven_lens/application/execution_service.py`）；當時 `submission_guard` 在 `FOR SHARE` 內檢查 `UNKNOWN`/`REVIEW_REQUIRED`；`Reconciler.collect` 對兩者產生 `INTENT_STATUS_MISMATCH`；`ControlPlane.resume_entries` 做 defense-in-depth 阻擋。
  6. P2-CUR-006 帳務對帳：`PaperAccount.buying_power` 嚴格解析、`account_baselines` 權威基線表（migration 0008）、`AccountReconciliationPolicy` / `ReconciliationMarkPriceProvider` seam、tolerance 內的 `CASH`/`NAV`/`ACCOUNT_ID`/`BUYING_POWER` 檢查與 `ACCOUNT_RECONCILIATION_UNAVAILABLE` 失效閉環（`src/seven_lens/application/reconciliation_service.py` 擴 6 種新 mismatch）。
- 治理同步：R-24 重標 `Mitigated` 並引用 read round-trip 測試、`ISSUES.md` CLOSED-021 superseded、README/SECURITY/DEFERRED-013/015 與 ROADMAP/ADR-019 的 P2/WS/CLI 範圍一致化（本輪不實作 WS transport / control CLI）。
- 最終證據（2026-08-20 本機）：Ruff format/check、mypy strict 92 檔全綠；non-integration 637 passed / 77 deselected（新增 `TestLateFill` / `TestFifo` / `TestLedgerInvariant` / `TestUnknownGate` / `TestAccountReconciliation` 等，見 `tests/test_*`）；真實 disposable PostgreSQL 16 `69 passed / 8 deselected`（`verify_p1.sh --postgres` EXIT=0，含 `account_baselines` 權限與 `0008 up/down/up`）；`RISK_REGISTER` R-24、`ISSUES` SUPERSEDED-021 與本文件已同步。P2 gate **再次 Closed**；不新增 live endpoint，P7 真實下單仍留後續 supervised gate。

## P2 最終修復（2026-08-20）：Gate Closed — ACC-001~009

- 原始重開判定：依據 `SEVEN_LENS_P2_FINAL_REMEDIATION_AGENT_PROMPT.md`（HEAD `0f8281b`）重開 P2 gate；當時 ACC-001~009 尚未完成，因此不得宣告 P2 Complete：
  1. ACC-001 併發新單可同時越過 broker 邊界（`FOR SHARE` 可共存）；
  2. ACC-002 baseline cutoff 的 NAV 錯誤丟失 cutoff 前已建倉且仍持有的部位；
  3. ACC-003 runtime role 可任意 INSERT 新 baseline revision 取得權威；
  4. ACC-004 合法 0008 mutated baseline 使 0009 `effective_at <= created_at` 失敗；
  5. ACC-005 genesis 僅文件宣稱「fill 前才允許」，實際無強制；
  6. ACC-006 `AttributeError`/`TypeError` 被降級為普通 unavailable，掩蓋程式缺陷；
  7. ACC-007 衝突遲到 fill 僅拋例外，未持久化 reconciliation-required / pause 證據；
  8. ACC-008 治理文件仍稱 P2 Closed、描述仍停留 0008；
  9. ACC-009 缺完整回歸、真實 PG 併發／重啟／遷移／權限等證據。
- 本輪依建議順序修復 001→007，最後執行完整 Ruff/mypy/pytest 非整合 + `run_postgres_integration.sh` + `verify_p1.sh --postgres` 並同步治理；完成前不宣告 P2 Closed。
- 本機 fresh regression（2026-08-20）：`uv lock --check --offline`、Ruff format/check、mypy strict、non-integration `672 passed, 90 deselected`、PostgreSQL 16 `82 passed, 8 deselected`、`verify_p1.sh` 與 `verify_p1.sh --postgres` 均 exit 0。新 PG evidence 包含 race A timeout→UNKNOWN 時 B broker call count=0、success serialization、restart UNKNOWN gate、RISK_EXIT bypass、runtime baseline INSERT denial、0008 mutated baseline→latest、genesis/revision invariants 與 conflicting fill restart pause。
- 本輪補齊 ACC-004 語意：legacy 0008 compatibility revision 的 `created_at` 等於 legacy `effective_at`／authority-effective timestamp；source `account_baselines.created_at` 原值於遷移後還原。0009 migration 與 checksum 均未修改。
- 本輪補齊 ACC-005 真實競態：`test_genesis_baseline_creation_race_with_first_fill_is_serialized` 使用兩個 `PostgresUnitOfWork`、兩條 thread、Events、bounded timeout；證明 genesis transaction 持 lock 時 first-fill INSERT 阻塞，genesis commit 後 fill 才 commit。
- 本輪補齊 ACC-006 typed taxonomy：新增 `MarkPriceUnavailableError`；只有此 expected absence 轉 `ACCOUNT_RECONCILIATION_UNAVAILABLE`。unexpected `ValueError`、`AttributeError`、`TypeError`、`PersistenceInvariantError` 均向外傳播，missing baseline 保持 fail-closed mismatch。
- Fresh 本機證據：`uv lock --check --offline` exit 0；Ruff format/check、mypy exit 0；non-integration `676 passed, 91 deselected`；PostgreSQL 16 `83 passed, 8 deselected, 0 skipped`；`verify_p1.sh` 與 `verify_p1.sh --postgres` 均 exit 0。
- 遠端證據：exact code-bearing SHA `488f170` 的 GitHub Actions [`32360443947`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/32360443947) 中 `quality-unit`（19s）與 `postgres-integration`（1m8s）均成功。ACC-009 Closed，P2 Gate **Closed**。

## P3 最終需求凍結（2026-08-21）：歷史規劃基線；P3-A 後續已實作

- 使用者確認完整 TradingAgents 鏈、兩種 debate 各兩輪、完整持倉輸入、target-weight proposal、deterministic Risk 一次駁回／一次重申／第二次 `NO_TRADE`。
- 使用者確認 long/short、15 檔、單股 15%、long/short/total/net gross limits、40% turnover、無最短持有期、同日退出 reason gate 與 verified Risk Exit 例外。
- 使用者確認開盤後 60 分鐘與收盤前 90 分鐘兩個正常分析窗口、全部持倉 + 12／5 候選、一般 15 分鐘與緊急 3 分鐘 deadline。
- 使用者確認 Agnes／Muse 角色路由、最高可用 reasoning、一次 failover、去識別化 portfolio snapshot、Muse training/non-ZDR acceptance，未來 GPT-5.6 需同一 eval gate。
- 使用者確認每日持倉 reflection、Risk rejection memory、週六壓縮與最多 4,000 行的專用 memory-curation skill；raw records immutable。
- 突發事件採 deterministic 二次確認；source/timestamp conflict 為 `DATA_CONFLICT`，不把可疑事件交給 LLM。只有 hard-risk 可在驗證失敗時獨立減倉。
- 本輪只更新規劃／治理文件；未修改 production code、未使用 credential、未呼叫 broker/data/model API、未 stage/commit/push。

## P3-A 實作（2026-08-21）：completed，待獨立驗收

- 固定 TradingAgents `a33fd4c0f134485a43553a2c23a63cb14adbd88f`；保存 exact Apache-2.0
  LICENSE（SHA-256 `c71d239d...d0ab4`）、23-path planned-source manifest、無 NOTICE 與
  `runtime_code_vendored: false` 證據。
- 新增 dependency-free frozen/slots contracts：三窗口 input、四 analyst roles、Bull/Bear 與
  Risk debate、Research/Trader、完整去識別化 snapshot、signed target proposal、一次 rejection
  feedback；wire 採 exact fields/types、固定小數字串、bounded canonical JSON、重算 content/universe
  hash，proposal 以 executable boundary 驗證 input identity/universe。
- targeted `70 passed`；`verify_p1.sh` exit 0（Ruff、mypy strict 100 files、non-integration
  `746 passed, 91 deselected`）；真實 PostgreSQL 16 `83 passed, 8 deselected, 0 skipped`；
  `git diff --check` 通過。
- 未新增 dependency、未改 `uv.lock`／migration／P2／CI；只做固定 SHA 的無 credential read-only
  GitHub retrieval，未使用 credential/API、未讀 `skill/`、未 stage/commit/push。

## P3-A remediation-R1（2026-08-21）：completed，待獨立重新驗證

- 依獨立 acceptance session 後續深度對抗審查之已實證發現，完成五項範圍內修復：
  A 六個 decimal 欄位 typed constructor 負零拒絕（共用 `_reject_negative_zero` helper，
  `nav` 語意不變）；B OPEN/HOLD 帶 same-day exit reason 的 ctor+wire 拒絕測試；
  C `validate_against` 身份比對五案例；D 八條已實證規則測試（proposal status/requests、
  borrow located_quantity、entry band 順序、focus universe、report/conclusion status-
  confidence 規則、debate viewpoints、emergency INCREASE、跨 enum 混淆＋六欄位負零）；
  E source-invariant 掃描解析相對／alias 匯入並附 snippet 自我測試。
- 變更僅限 `src/seven_lens/analysis/contracts.py` 與兩個對抗／source-invariant 測試檔。
- 本機證據：targeted 三模組 `83 passed`（原 70，+13）；`uv lock --check --offline` exit 0；
  Ruff format/check exit 0；mypy strict exit 0（100 files）；non-integration
  `759 passed, 91 deselected`（原 746）；真實 disposable PostgreSQL 16
  `83 passed, 8 deselected, 0 skipped` exit 0、無容器殘留；`git diff --check` exit 0。
- 未新增 dependency、未改 `uv.lock`／migration／P2 execution/application/infrastructure/
  broker/Keychain/CI；未使用 credential/API、未讀 `skill/`、未 stage/commit/push。
  狀態固定為 `remediation completed; pending independent re-verification`，不自行宣告
  P3-A Gate 變更。

## P3-A 獨立重新驗證（2026-08-21）：remediation-R1 Accepted — Gate Closed

- 獨立 acceptance session 完成 remediation-R1 定點重新驗證，判定 **Accepted**；P3-A 維持
  Accepted、五項修復結案，P3-A Gate **Closed**。
- 範圍：`git diff 45f3c6b..工作樹` 僅含 `contracts.py`（+11）、兩個 P3-A 對抗／
  source-invariant 測試檔與三份治理文件；golden fixture、主測試檔、`third_party/**`、
  `uv.lock`、`pyproject.toml` 零 diff；HEAD 未變、未 stage/commit/push。
- 修復 A：六處 `_reject_negative_zero` 呼叫精讀確認；31 項親自 PoC 全過（ctor 負零全拒、
  wire 層未放鬆、合法零值無過度拒絕、nav 語意不變、未動類別行為不變、21 golden 實例
  round-trip 成立）。
- 修復 B~D：13 個新案例逐條對照 source 規則通過；基線還原實驗（SHA-256 逐位元驗證後原樣
  恢復）證明負零 ctor 測試確實釘住代碼修復——未修復碼上 `1 failed, 54 passed`，其餘新測試
  釘住既有正確行為且非空殼。
- 修復 E：新掃描器抓到全部六種匯入寫法（含相對匯入與 alias 匯入），舊邏輯實證漏掉三種；
  自我測試真實執行四種 snippet。
- 命令證據（全 exit 0）：`uv lock --check --offline`；Ruff format/check（100 files）；mypy
  strict（100 files）；targeted 三模組 `83 passed`；non-integration `759 passed,
  91 deselected`；真實 disposable PostgreSQL 16（digest-pinned 16.15-alpine）
  `83 passed, 8 deselected, 0 skipped`，owner-label 容器清點 0；`git diff --check`。
- 殘留：未追蹤 `.mimosa/` hook-state 目錄（工具產物）；wire 層非負欄位負零字串以
  canonical-string 訊息拒絕（既有 fail-closed 設計）。均為 Low／資訊性，不阻斷。

## P3-A remediation-R1 發布（2026-08-21）：main + exact-SHA CI 成功

- 使用者明確授權直接 push；remediation-R1 與驗收／治理同步提交為
  `9037dacc589690101ea60901a3f34991480a70e1` 並推送 `origin/main`。
- GitHub Actions run `32488368972` 對 exact code-bearing SHA 完成：`quality-unit` 與
  `postgres-integration` 均為 `success`。direct-main push 的 required-check bypass 訊息不列為
  驗證；以該自動 exact-SHA run 為 Gate 遠端證據。
- `.mimosa/` 已加入 `.gitignore`，工具 hook-state 未發布。下一個 gate 維持 P3-B。
