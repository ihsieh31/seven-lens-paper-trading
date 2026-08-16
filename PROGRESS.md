# Progress

## 狀態摘要

- 專案階段：P1 — 專案骨架與權威狀態（P1-A、P1-B、P1-C1、P1-C2、P1-C3 與遠端 CI 均已通過獨立驗收；P1 Core Gate 已關閉）
- 完成度定義：只以路線圖的可驗證交付物計算，不以主觀百分比計算。
- 最近更新：2026-08-16
- 下一個 gate：P2 Safety Design Gate；尚未開始，且 P1 通過仍不得視為可連線或可交易系統。

## 已完成

- [x] 確認新專案與工作區範圍。
- [x] 確認只做七人委員策略、本人使用、Paper-only。
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

- [ ] 實作 Paper broker adapter、reconciliation 與故障注入測試。
- [ ] 審查本機七位候選語料，建立 SourceManifest、quarantine／coverage 報告與七套 doctrine cards。
- [ ] 取得 Tavily 對同一 Customer 彙總使用 7 個免費帳號的書面／後台授權證據。
- [ ] 實作量化預篩、committee workflow、portfolio optimizer 與風控。
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
