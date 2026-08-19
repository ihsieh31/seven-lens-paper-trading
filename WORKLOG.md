# Work Log

## 2026-08-14 — P0 規劃建立

### 完成工作

- 將需求固定為全新、個人使用、七人單一策略、Alpaca Paper-only。
- 檢查 TradingAgents 上游程式，而非只依 README 推定架構。
- 檢查 Alpaca Paper、order updates、client order ID 與模擬限制。
- 檢查 Tavily 免費 credits、rate limits，將免費限制寫成 deterministic budget。
- 檢查 OpenAI 官方模型分工與 Codex scheduled tasks 邊界。
- 透過 GitHub 搜尋 Serenity 及其他投資方法論蒸餾資產。
- 建立完整企劃文件集和可持續更新的日誌。

### 重要發現

- TradingAgents 適合做研究辯論骨架，不具備券商帳本、重試一致性與硬風控。
- `WOOK98/serenity-aleabitoreddit` 比簡單 prompt skill 更重視 evidence packet，但查無 LICENSE，且其資料取得與績效校準仍需獨立驗證，不能原樣導入。
- 其他六人沒有一套同時具備完整語料、來源鏈、授權、反例與 held-out tests 的成熟技能。
- Codex Automations 適合維護與離線研究，不應是開收盤委託的關鍵排程器。

### 本輪未做

- 未建立任何 Alpaca credential 或委託。
- 未啟用任何排程或背景服務。
- 未下載／提交第三方語料。
- 未 stage、commit 或 push。

## 2026-08-14 — Tavily 容量修訂

- 使用者補充持有 7 個 Tavily 帳號，理論總額 7,000 credits／月。
- 核對 2026-05-04 更新的 Tavily Platform Terms：額外 Account 可能需要另外 Order Form／費用，且不得超越 Customer limitations。
- 規格新增 `SINGLE_ACCOUNT_UNVERIFIED` 與 `AUTHORIZED_ACCOUNT_POOL`；取得明確授權後才啟用七鍵池。
- 授權後預算：runtime 5,600、research/incident reserve 1,400、一般交易日 soft cap 250、全域月 hard cap 7,000。

## 2026-08-14 — P1-A 安全專案骨架

### 完成工作

- 建立 Python 3.13 `src/seven_lens` package、測試骨架、`.python-version`、`pyproject.toml` 與 `uv.lock`。
- 建立只含 `PAPER` 的 broker environment、Paper endpoint 精確 allowlist、exact-schema mapping parser 與啟動重驗證。
- 建立 Tavily compliance domain：未授權模式只允許一個 enabled account 與全域 1,000 credits；授權模式需 evidence reference、最多七帳號、每帳號 1,000 與全域 7,000 credits。
- Tavily schema 保存不含 secret 的 account id、enabled、usage、reset 與 cooldown；跨帳號併發能力固定禁止。
- 建立 `RunId`、`TradingDate`、`UtcTimestamp`、`SchemaVersion`；timestamp 只接受 timezone-aware UTC。
- 建立 recursive secret redactor interface/default implementation 與 redaction-first JSON structured logging。
- 建立不含真實值的 `.env.example`、`.gitignore`，並更新 README 開發指令。
- normal、boundary、invalid、fail-closed 測試共 102 個，另含 source-level Paper-only invariant 掃描。

### 實際驗證

- `uv sync --python 3.13 --locked`：Python 3.13.14；resolved 14 packages、installed 13 packages，成功。
- `uv run ruff format .`：完成；最終 `uv run ruff format --check .` 回報 `16 files already formatted`。
- `uv run ruff check .`：`All checks passed!`。
- `uv run mypy`：`Success: no issues found in 16 source files`。
- `uv run pytest -q`：最終回報 `102 passed in 0.03s`。

上述最終四項命令在 Codex sandbox 以隔離的 `UV_CACHE_DIR`、`--offline --no-sync` 執行，使用先前由 `uv sync --locked` 建立的鎖定 `.venv`；沒有對 broker、Tavily、OpenAI 或其他資料服務送出請求。

### 明確未做

- 未建立 broker/Tavily/OpenAI SDK adapter，未讀取或測試任何真實 API key。
- 未建立策略、蒸餾、行情下載、資料庫、下單、Codex automation、launchd 或背景服務。
- 未 stage、commit 或 push；P1 Core Gate 仍未完成。

## 2026-08-14 — P1-A 獨立安全驗收修復

### 修復前重現

- 獨立報告 `/private/tmp/codex-security-p1a.12ENiW/report.md` 判定 P1-A 未通過。
- 真實 `JsonFormatter` boundary 重現 fake Basic credential 與 bytes 內 fake token 出現在 JSON。
- self-referential list 重現 `RecursionError`；`x:abc` 重現可建立 7,000-credit authorized config。

### 完成修復

- redactor 改為 bounded、cycle-aware、只輸出 JSON-safe 型別；bytes、set、自訂物件不呼叫 `str/repr`，mapping key 不再經 `str(key)`。
- secret-bearing mapping key 使用不碰撞 placeholder；非字串／重複 key、cycle、超過深度與 unsafe serializer output 觸發固定 fallback audit event，且不含原始 fields。
- JSON formatter 移除 `default=str` 與 `record.getMessage()` sink；fallback 不保存 exception、repr 或未驗證欄位。
- Tavily 授權改綁 immutable evidence record、account set、verified time 與 status；目前沒有外部 verifier，因此即使本地 record 聲稱 verified，`AUTHORIZED_ACCOUNT_POOL` 仍 fail closed。
- `UtcTimestamp` wire parser 固定為 `YYYY-MM-DDTHH:MM:SS.ffffffZ`；`SchemaVersion` 每元件限制 `0..9999`。
- 新增 Basic、quoted/multi-word credential、bytes/set/object、secret key collision、non-string key、cycle/depth/fallback、假 Tavily evidence、canonical timestamp 與 oversized schema regression tests；總測試 128 個。

### 驗證結果

- 修復後原 PoC：Basic 與 bytes secret 分別變為 `[REDACTED]`／`[UNSAFE_LOG_VALUE]`；cycle 輸出 `structured_log_serialization_failed`；`x:abc` 被 `ConfigurationError` 拒絕。
- `uv sync --python 3.13 --locked`：`Resolved 14 packages in 26ms`、`Checked 13 packages in 5ms`。
- `uv lock --check --offline`：`Resolved 14 packages in 3ms`。
- `uv run ruff format --check .`：`16 files already formatted`。
- `uv run ruff check .`：`All checks passed!`。
- `uv run mypy`：`Success: no issues found in 16 source files`。
- `uv run pytest -q`：`128 passed in 0.05s`。

### 範圍

- 未開始 P1-B、資料庫、adapter、API、資料下載或排程。
- 未讀取、顯示或測試真實 API key；未 stage、commit 或 push。

## 2026-08-14 — P1-B PostgreSQL 權威狀態、events、lease 與 market clock

### 完成工作

- 建立 dependency-free `DomainEvent`／`AuditEvent` envelope、immutable canonical `JsonObject`、job/fencing value objects，以及 repository/unit-of-work ports。
- 使用 psycopg 3 direct SQL adapter；domain/application port 沒有 import PostgreSQL driver、ORM 或 migration framework。
- 建立 checksummed initial up/down migration與 runner，schema 只含 metadata、domain/audit events、job instances/leases；migration 重複執行會驗證 up+down checksum。
- `domain_events` 以 event id、aggregate unique constraint、transaction advisory lock 與 trigger 強制 sequence 從 1 連續遞增；`recorded_at` 由 PostgreSQL `statement_timestamp()` 覆寫。
- `audit_events` 以 trigger 禁止 UPDATE/DELETE，並在應用層與 PostgreSQL constraint/trigger 拒絕 API key、Authorization/Bearer、private key 等 secret-bearing payload。
- unit of work 預設 rollback、明確 commit；`transition_job_with_audit` 在同一 transaction 完成 fenced job status 與 audit append。
- lease acquire/renew/release/takeover 全在 PostgreSQL atomic function 內使用 DB clock；takeover 關閉上一筆 history、遞增 attempt/fencing token，stale owner/token 無法 renew/release/write。
- 建立 pure `MarketClock` port 與 deterministic fake，能明確表示 regular、half-day、closed/holiday 與 half-open regular-session window；未連接 broker/calendar API、未建立 scheduler。

### 真實 PostgreSQL 驗證與修復

- 使用隔離 `postgres:16-alpine` container 與 disposable database；沒有使用 SQLite。
- 第一輪實測發現並修復 PL/pgSQL JSON scalar `CASE`、lease acquire ambiguous column、status transition row-shape 三個 PostgreSQL-only 問題。
- migration tests：9/9，包含 clean apply、repeat/checksum、up/down/up restore、schema/constraint、append-only UPDATE/DELETE SQLSTATE `55000`、secret rejection與 event ordering。
- persistence/lease tests：9/9，包含 UoW rollback、狀態+audit failure rollback、兩執行者 concurrent acquire 僅一個成功、renew/release owner/token、DB-clock expiry takeover、restart、history、stale fencing 與 direct mutation guard。
- 完整 `uv run pytest -q`：`242 passed in 1.53s`（包含上述 18 個真實 PostgreSQL integration tests）。
- `uv sync --python 3.13 --locked`：`Resolved 17 packages`、`Checked 15 packages`。
- `uv lock --check --offline`：`Resolved 17 packages`。
- `uv run ruff format --check .`：`34 files already formatted`。
- `uv run ruff check .`：`All checks passed!`。
- `uv run mypy`：`Success: no issues found in 34 source files`。

### 範圍

- 未建立 broker adapter、Tavily/OpenAI client、行情、策略、蒸餾、下單、automation 或 launchd。
- 未索取、讀取或使用 Alpaca、Tavily、OpenAI API key；未 stage、commit 或 push。
- P1 尚缺 Keychain adapter、metrics/traces 與 CI，因此 P1 Core Gate 明確維持未完成。

## 2026-08-14 — 建立跨對話專案交接

- 新增 `PROJECT_HANDOFF.md`，集中記錄專案目標、核心決策、工作角色、P0/P1-A 進度、已修復問題、未決風險、驗收方法與 P1-B 完整 Prompt。
- README 新增交接入口；後續新對話先讀交接檔，不需使用者重新敘述專案。
- 交接建立時 P1-A 已通過定點驗收；下一步仍是 P1-B，尚未建立資料庫、adapter、排程或交易能力。

## 2026-08-14 — P1-B 後同步跨對話交接

- 保留原 P1-B Prompt 作歷史 scope／acceptance 紀錄，但標示為已執行，不得讓後續對話重跑。
- 同步目前狀態為 P1-A／P1-B 已驗證、P1 Core Gate 未完成；下一步只先定義 P1-C 的 Keychain、metrics/traces 與 CI 範圍。

## 2026-08-15 — P1-C1 macOS Keychain secret boundary implementation

### 完成工作

- 建立 typed、fixed-mapping `SecretKind`／`SecretRef` 與 1–4096-byte strict UTF-8 `SecretValue`；拒絕空白、邊界空白、NUL/CR/LF、invalid UTF-8 與 oversized input，固定 redacted `str/repr` 並禁止 pickle。
- 建立 persistence/platform-neutral `SecretProvider`、固定 bounded failure taxonomy、backend 前 capability allowlist 與 all-or-nothing required-secret resolution。
- 建立 macOS Security.framework `SecItemCopyMatching` adapter：generic-password exact read、return data、match all、authentication UI fail；沒有 shell、`/usr/bin/security`、write/list/wildcard 或 fallback。
- 建立預設 2 秒 spawned worker hard timeout；timeout/crash/malformed IPC 全部 fail closed，cleanup 會 terminate/join、必要時 kill/join，並關閉 IPC/process resources。
- `.env.example` 只保留 Paper base URL、Tavily compliance mode、Tavily account id 三個非秘密設定。
- 最小 dependency 只加入 macOS conditional `pyobjc-framework-Security`。本機只驗證 module/function/constants import，沒有呼叫 `SecItemCopyMatching` 或讀取真實 Keychain。

### 驗證結果

- P1-C1 新增 tests：`72 passed`，全部使用 fake secret/provider/native bridge/process。
- 既有 redaction/structured logging、broker config、Tavily config regression：`65 passed`。
- 完整 non-integration suite：`296 passed, 18 deselected`。
- `uv lock --check --offline`、Ruff format/lint、Mypy 全通過；Python 3.13.14。
- fake query 證明 exact service/account、generic password、return data、match all 與 authentication UI fail；timeout fake 證明 worker terminated、joined、無 active child且 IPC closed；capability denial 發生在 backend call 前；nested `SecretValue` structured log 輸出 `[UNSAFE_LOG_VALUE]`。

### 限制與狀態

- 未索取、讀取、驗證或使用任何真實 API key；未查詢、建立、更新或刪除任何真實 Keychain item；未呼叫外部服務。
- 未開始 metrics/traces、OpenTelemetry、CI、API client、broker/order schema、策略、資料、下單、launchd 或告警。
- P1-C1 只能標示 implementation completed、pending independent acceptance；P1-C 與 P1 Core Gate 均未完成。下一邊界是獨立 P1-C1 驗收，通過後才進入 P1-C2 telemetry。

## 2026-08-15 — P1-C2 dependency-neutral metrics/traces implementation

### 前置狀態

- 依本輪交付狀態將 P1-C1 記為已通過獨立驗收；P1-A、P1-B、P1-C1均已驗收。
- P1-C2開始前確認 `trading`仍是父repository中的untracked目錄；保留父層既有變更，未stage、commit、push或建立PR。

### 完成工作

- 建立canonical non-zero `TraceId`／`SpanId`與explicit immutable `TelemetryContext`；支援fixed IDs與child parentage，不使用ambient context。
- 建立dependency-neutral typed metric/trace recorder ports、固定五metric／兩span registry、exact attributes/enums、64字元value、每instrument 64 active series與禁止attribute清單。
- 建立fail-safe facade：injectable monotonic clock、process-local drop count、bounded drop metric、固定無detail diagnostic、metric/span start/span end獨立failure handling，且不吞`BaseException`。
- 建立deterministic metric/trace/clock/context fakes；production沒有自動fake fallback。
- 以application decorator instrument secret lookup；不修改native Keychain bridge。所有typed secret failure保留原exception，fake secret/account/native result不進telemetry。
- 修改既有public `transition_job_with_audit`強制接收context與telemetry；success只在commit與UoW正常退出後記錄，stale/audit/DB failure在rollback後記錄bounded outcome並重拋原錯誤。
- structured logging可注入validated context；沒有processing context的startup/config log不偽造IDs，invalid context拒絕。
- 新增ADR-014並同步README、architecture、operations、progress與handoff；沒有修改migration或開始CI。

### 驗證結果

- 新增telemetry tests：76個，涵蓋context、registry/cardinality、recorder failures、secret non-leak、job transaction ordering/atomicity及source dependency invariants。
- `uv sync --python 3.13 --locked`：`Resolved 20 packages`、`Checked 18 packages`。
- `uv lock --check --offline`：`Resolved 20 packages`。
- Ruff format：`54 files already formatted`；Ruff lint：`All checks passed!`；Mypy：`Success: no issues found in 54 source files`。
- 完整non-integration：`388 passed, 19 deselected`。
- 真實PostgreSQL `16.14` integration：migration `9 passed`、persistence/lease/telemetry `10 passed`，合計`19 passed, 0 skipped`。包含audit failure rollback、stale fencing、expiry takeover，以及metric與span-end recorder同時故障時state+audit仍原子提交。
- 測試使用isolated `postgres:16-alpine` disposable container；完成後已停止並移除，沒有SQLite/mock替代。

### 限制與狀態

- 未索取或使用任何真實credential，未查詢真實Keychain，未呼叫外部API、metrics backend或exporter。
- 未加入OpenTelemetry、Prometheus、Sentry或其他telemetry dependency；未建立GitHub Actions、CI workflow、clean-machine script、broker/order schema、策略、資料、下單、launchd或正式告警。
- P1-C2只能標示implementation completed、pending independent acceptance；P1-C與P1 Core Gate仍未完成。下一步只做P1-C2獨立驗收，通過後才開始P1-C3 CI。

## 2026-08-15 — P1-C2 audit/telemetry identity acceptance fix

- 修復單一驗收問題：`transition_job_with_audit`現在在span、clock、UoW與任何repository/DB call前，要求`audit_event.run_id`存在，並與`TelemetryContext`的run/correlation identity完全一致。
- mismatch一律拋出固定、bounded且不含ID的`AuditTelemetryContextMismatchError`；不啟動span、不記metric、不進UoW、不讀寫repository，也不改變state/audit。
- 正常unit與真實PostgreSQL fixtures改為從實際`AuditEvent`建立matching context；沒有以放寬assert掩蓋問題。
- 新增3個adversarial cases：run mismatch、correlation mismatch、missing audit run ID；三者均證明telemetry/UoW/backend calls為零。P1-C2 telemetry tests增至79個。
- 驗證：Ruff format/lint、Mypy通過；P1-C2 tests `79 passed`；完整non-integration `391 passed, 19 deselected`；真實PostgreSQL `16.14` integration `19 passed, 0 skipped`，audit rollback、telemetry failure atomic commit、stale fencing與expiry takeover全部重跑通過。
- 未修改migration、未開始P1-C3 CI、未使用真實Keychain/credential、未stage/commit/push。P1-C2仍為implementation completed、pending independent re-acceptance。

## 2026-08-15 — P1-C3 CI、zero-skip PostgreSQL gate 與 clean-machine implementation

### 前置與範圍

- P1-C2已通過獨立驗收；本輪只完成P1-C3，沒有開始P2。
- 確認`trading`仍是父ICTM repository的untracked目錄；未stage、commit、push、建立repository或PR，
  未修改migration或`trading`外檔案。

### 完成工作

- 建立只有Ubuntu 24.04 `quality-unit`與`postgres-integration`兩個jobs的待啟用workflow；read-only
  permission、checkout不保留credential、PR-only cancel concurrency，無secret/OIDC/deploy token/
  `pull_request_target`/hosted macOS job。
- 依官方release資料固定：checkout v7.0.1 SHA
  `3d3c42e5aac5ba805825da76410c181273ba90b1`、setup-python v7.0.0 SHA
  `5fda3b95a4ea91299a34e894583c3862153e4b97`、setup-uv v10.0.1 SHA
  `20cfd1bf945f4377ade1205e4dbc17946fc9a30d`、uv `0.12.5`。
- PostgreSQL固定Docker Official Image `postgres:16.15-alpine` OCI index digest
  `sha256:ab5c955e9e57ae9879d4411ab49a912be9d162455676f7bf56e951b11ac73785`；CI與本機皆先
  assert server major 16。
- integration marker移到pyproject；移除兩個integration module的`pytest.importorskip`。required
  mode於collection前驗證URL/driver/connection/version，任何integration skip在session finish轉為失敗。
- 建立uv-only `verify_p1.sh`與disposable PostgreSQL script；後者使用fake credentials、random localhost
  port、tmpfs、60秒bounded readiness及container ID/name/owner label三重核對cleanup。
- 新增16個workflow/gate/script測試；cleanup測試使用fake Docker，不觸碰使用者既有container。

### 本機與 clean-machine 證據

- `verify_p1.sh`：Ruff format/lint、Mypy、lock check全通過；`407 passed, 19 deselected`。
- 真實digest-pinned PostgreSQL `16.15`：`19 passed, 0 skipped`；完成後owned container清單為空，
  Docker volume set與執行前一致。
- clean-machine副本排除原專案`.venv`並使用全新空uv cache；兩個一鍵命令皆成功，non-integration
  兩次均`407 passed, 19 deselected`，`--postgres`另得`19 passed, 0 skipped`。
- `uv.lock`執行前後SHA-256皆為
  `79809edba36965084b7561d616b0f95902e28e8fd4da6b07f35c409b6b34626b`；clean run後volume set
  未變、owned container為空，精確臨時副本已移除。
- workflow/static gate對抗測試：`16 passed`；未讀取Keychain或`.env`，未使用repository secret、
  broker/model/data API或真實credential。

### 狀態與限制

- P1-C3只能標記`implementation completed, pending independent acceptance`；下一步是P1-C3定點獨立驗收。
- workflow尚未在獨立Seven-Lens repository真正執行，沒有遠端CI成功證據；P1 Core Gate保持Open。
- 未建立hosted macOS job、OpenTelemetry/exporter、broker/API client、策略、行情、下單、scheduler、
  launchd、告警或任何live path。

## 2026-08-15 — P1-C3 定點獨立驗收

### 驗收結論

- P1-C3通過本機獨立驗收；P1-C本機交付完成。
- workflow尚未在獨立Seven-Lens repository真正執行，因此P1 Core Gate維持Open；未進入P2。

### 獨立證據

- 逐檔審查workflow、required PostgreSQL gate、兩個shell scripts與16個對抗測試；未發現需退回修復的問題。
- 官方release／commit頁面確認checkout v7.0.1、setup-python v7.0.0、setup-uv v10.0.1 pin；本機Docker RepoDigest確認PostgreSQL OCI index digest吻合。
- P1-C3對抗測試`16 passed`；Ruff format/lint、Mypy通過；完整non-integration為`407 passed, 19 deselected`。
- required mode缺少`TEST_DATABASE_URL`與SQLite URL各自於collection前以exit 4 fail closed；真實digest-pinned PostgreSQL 16.15 integration為`19 passed, 0 skipped`。
- 另建排除原專案`.venv`且使用全新空uv cache的隔離副本，依序執行`verify_p1.sh`與`verify_p1.sh --postgres`；兩次non-integration均`407 passed, 19 deselected`，PostgreSQL為`19 passed, 0 skipped`。
- `uv.lock` SHA-256前後皆為`79809edba36965084b7561d616b0f95902e28e8fd4da6b07f35c409b6b34626b`；Docker volume集合hash未變，owned container為空。

### 範圍

- 未修改workflow、程式、tests、`uv.lock`、migration或`ISSUES.md`；只同步README、Roadmap、ADR、Progress、Worklog與Handoff的驗收狀態。
- 未stage、commit、push、建立repository或PR，未使用Keychain、broker/model/data API、repository secret或真實credential。

## 2026-08-15 — Public GitHub publication 與 P1 Core Gate 遠端驗收

### 發布與公開前檢查

- 移除公開文件中的本機絕對路徑，確認`.env`、cache、virtualenv、archive、private-key檔與大檔未進入tracked內容；唯一private-key字串命中是對抗測試fixture。
- 以隔離uv cache重跑完整本機gate：Ruff format/lint、Mypy、`407 passed, 19 deselected`與真實PostgreSQL 16.15 `19 passed, 0 skipped`均通過；`uv.lock` hash及Docker volume集合未變，owned container為空。
- 建立公開且獨立的[`ihsieh31/seven-lens-paper-trading`](https://github.com/ihsieh31/seven-lens-paper-trading) repository，default branch為`main`；初始commit為`f0a328169a116b1bab9392be8c0566b386f297d6`。

### 遠端驗收與修復

- 首次workflow run `31868874046`在建立jobs前失敗；GitHub annotation指出job-level`env`中的`job.services.postgres.ports[5432]` context無效。
- 將動態`TEST_DATABASE_URL`移到integration test step；P1-C3對抗測試仍為`16 passed`，修復commit為`4e795ff1dc6d5b6bc51d4bd0e55149fda3e4cc61`。
- GitHub Actions run [`31868962828`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31868962828) 的兩個jobs均成功：`quality-unit`完成Ruff、Mypy、lock checks及`407 passed, 19 deselected`；`postgres-integration`使用PostgreSQL 16.15並完成`19 passed, 0 skipped`。

### 狀態與邊界

- P1 Core Gate已有獨立repository遠端證據並正式關閉。
- 未使用repository secrets、Keychain、broker/model/data API或真實credential；未建立broker adapter、order/fill schema、策略、行情、排程、launchd或live path，也未開始P2 implementation。

## 2026-08-15 — P1 foundation hardening報告查核與修復

### 查核結論

- 確認為目前P1/P2-entry authority問題：runtime/schema-owner未分離、privileged function search-path
  defense不完整、audit/domain payload可任意擴張、persisted JSON無資源budgets、缺統一安全邊界文件與
  Risk Register狀態規則。
- 確認為P2-entry契約但非現存可利用path：config composition與runtime DB credential composition；
  目前沒有service composition root或長駐runtime，先固定fail-closed契約，不虛構實作。
- 未確認為漏洞：Keychain必須改為persistent-reference兩段查詢。Apple contract與現行exact query未
  提供必須改寫的證據，因此保持現有fail-closed ambiguity detection。
- Deferred：native Keychain smoke需建立／刪除disposable item且未獲該mutation授權；coverage與新增
  security-static CI job屬獨立quality/supply-chain gate，不是本次可重現P1 exploit。

### 修復內容

- 新增migration 0002與runtime role provision/verify；owner與runtime分離，PUBLIC schema CREATE、database
  TEMP與protected function EXECUTE撤銷，lease/status functions固定trusted search path並schema qualify。
- event/audit只接受closed typed payload，event type由payload衍生；job service在telemetry/UoW前核對
  target，PostgreSQL constraints獨立執行同一registry。
- `JsonObject`加入depth、nodes、object/list width、key/string UTF-8與canonical serialized byte limits。
- 新增`SECURITY.md`、ADR-016、risk lifecycle與fixed/deferred/assessed issue evidence；同步README、
  architecture、operations、progress與handoff。

### 本機證據

- 完整locked gate：Ruff format/lint、Mypy `59 source files`、non-integration
  `440 passed, 33 deselected`。
- 真實digest-pinned PostgreSQL 16.15：第一輪32 tests中只有4個舊fixture/version expectation不相容；
  更新為typed registry與migration version 2後通過。加入catalog ACL/proconfig斷言後最終為
  `33 passed, 0 skipped`；runtime-role 13 cases一次通過。
- Code commit `e8543b69bfc6a6d2dd9a87837d9d46bb11afc406`已直接push到public `main`；GitHub
  Actions run [`31891905869`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31891905869)
  兩jobs均成功，遠端quality為`440 passed, 33 deselected`、PostgreSQL為`33 passed`。push使用既有
  repository admin bypass等待checks，沒有force push且完成後required checks實際全綠。
- Handoff/evidence descendant `5b3cd501c7ef415cbb27c3e0b5762ecdb7a609ea`隨後直接push；GitHub
  Actions run [`31892024588`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31892024588)
  再次讓`quality-unit`與`postgres-integration`全綠，因此它是本輪已發布的最終CI baseline。
- 未讀取或修改Keychain，未使用broker/model/data API或真實credential，未加入P2交易能力。

## 2026-08-16 — 七位蒸餾對象與本機候選語料規劃更新

- 依使用者決定，七位改為 Howard Marks、Muddy Waters Research、Aswath Damodaran、Serenity / `@aleabitoreddit`、Terry Smith / Fundsmith、Michael Mauboussin、Lyn Alden。
- 同步 README、Master Plan、Distillation Spec、Sources、Risk Register、Progress 與 Handoff；保留 doctrine-only、來源可追溯、反例／失效條件、held-out eval 與 `ABSTAIN` 邊界。
- 只以檔案系統確認 `skill/` 約 827 MB、723 個非 `.DS_Store` 檔案及七位路徑存在；依使用者要求未開啟或審查語料內容，未判定來源、授權、完整性、重複、時效或可蒸餾性。
- 因 repository 為公開且 `OPEN-002` 禁止未審核第三方全文再散布，`.gitignore` 排除 `skill/`；本輪只發布規劃與 handoff，正式 P3 先做 SourceManifest、quarantine、授權與 coverage 審查。
- 規劃 commit `1d4d9bd31d993a5fb6803a8d08ff5deec04122e1` 已直接 push 到 `main`；GitHub Actions run [`31950919861`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31950919861) 的 `quality-unit` 與 `postgres-integration` 均成功。

## 2026-08-17 — 工作環境由 Codex 遷移至 ZCode

- 使用者當時建立 ZCode 工作副本作為後續開發來源；Codex 原目錄未做任何修改。
- 全文掃描確認文字檔中沒有指向 codex 目錄的絕對路徑；實際需要校正的是前瞻性文件將開發／自動化工具寫為 Codex 的說明。
- 已將 `docs/MASTER_PLAN.md`（§1.2、§10）、`docs/OPERATIONS_AND_SAFETY.md`（§12）、`docs/ROADMAP_AND_ACCEPTANCE.md`（Automations 候選）、`docs/FIRST_IMPLEMENTATION_PROMPT.md`（使用方式與 approval 流程）與 `PROJECT_HANDOFF.md` §3.5 的工具引用由 Codex 改為 ZCode。
- ADR-008 僅附加 2026-08-17 補充記錄遷移，原決策本文未改；WORKLOG 既有條目、`docs/SOURCES.md` 的 OpenAI／Codex 外部文件連結、`PROJECT_HANDOFF.md` 第 10 節 P0 歷史紀錄、git 歷史中的 `codex/p1-foundation-hardening` 分支名與 `skill/serenity-aleabitoreddit-data/sync_state.json` 的 automation note 均保留原文。
- 未修改任何程式、測試、migration、workflow 或 CI；未 stage、commit 或 push；未觸碰 Keychain 或任何外部 API。

## 2026-08-19 — P2 獨立驗收完成並回遷 Codex

- 在 zcode 副本重跑 locked gate 與真實 PostgreSQL 16，進行對抗審查並修復 ADR-023 五類缺陷。
- 最終證據：627 passed / 74 deselected；PostgreSQL 16 66 passed / 8 deselected；Ruff、mypy、
  lock 全綠。
- 將新程式、migration、測試及 P2 紀錄同步回 `/Users/zongen/Downloads/codex/trading`；
  `.git`、`.env`、Keychain、`.venv`、cache 與 `skill/` 語料均未複製或讀取。
- 本輪未 stage/commit/push，遠端 CI 尚未執行。

## 2026-08-19 — 使用者重新打開 P2 Gate

- 撤銷先前本機 gate closed 結論；627/66 測試結果降為歷史基線。
- 啟動全面再驗收：Luna 對抗審核、真實 Alpaca Paper GET-only、控制/對帳/runtime role、
  locked gate 與真實 PostgreSQL 16。全部缺陷修復並重驗前保持 Open。

## 2026-08-19 — P2 全面再驗收完成，Gate Closed

- Keychain 兩個 Alpaca Paper credential 可用；真實 endpoint 僅發 GET account/positions/
  open-orders，回應為 PAPER、0 positions、0 open orders。另以 disposable PostgreSQL 16
  與獨立非 owner runtime role 完成 GET-only reconciliation persistence，live 1/1 通過。
- 官方契約與 Luna 對抗審核發現並修復：asset endpoint、open/fill pagination、錯單 fill、
  pause TOCTOU、cancel/flatten partial audit、broker query failure fail-open、open/history
  snapshot（含 equal timestamp 狀態衝突）。Luna 第三輪確認原四組 blocker 全部關閉。
- 最終本機證據：Ruff/mypy 92 檔全綠；637 non-integration passed / 77 deselected；69
  PostgreSQL 16 integration passed / 8 deselected；live acceptance 1 passed。
- 權威工作副本與所有前瞻性路徑均為 `/Users/zongen/Downloads/codex/trading`。未送出或取消
  真實委託，未 stage/commit/push，遠端 CI 尚未執行。

## 2026-08-17 — P2-A 執行 domain 契約與 fake broker harness 實作

- 依使用者核准方向開始 P2：P2-A（domain 契約）先行；P2-B/C/D 依賴序推進；P2-E 真實
  Alpaca Paper 連線僅 read-only 驗證，真實下單留 P7；金鑰到需要時才由使用者存入 Keychain；
  P2 階段以手動觸發 job 驗證，launchd 常駐留 P6 前。
- 新增 `execution/orders.py`（封閉 typed domain、雙狀態機、deterministic
  `slv1-…` client_order_id、collar bps 1..500 provisional）、`application/ports/broker.py`
  （PaperBrokerPort 與錯誤分類）、`execution/fake_broker.py`（fault injection harness）、
  `migrations/0003`（order_intents/broker_orders/fills 與 guard/append-only 強制），
  並擴充 `verify_schema` 與 runtime role grants/verification；ADR-017 記錄決策。
- 單元/行為測試覆蓋狀態機完整矩陣、exact-type 邊界、collar 數學、id 決定性、
  timeout before/after accept、idempotent replay、conflict、partial fill 與 cancel/expire。
- 本機自測：`verify_p1.sh` 全綠（Ruff、Mypy strict、non-integration `501 passed,
  54 deselected`、offline lock check）；真實 disposable PostgreSQL 16 整合
  `54 passed, 0 skipped`（含 21 個新增對抗測試）。
- 發現並修復一個自我造成的安全缺陷：0003 down migration 初版漏列
  `DELETE FROM schema_migrations WHERE version = 3`，使 `migrated_postgres` teardown 的
  `while current_version: rollback` 無限循環（DROP IF EXISTS 冪等不報錯）。補列後重跑，
  54 個整合測試 5.93 秒完成；容器由 script trap 精確清理，無殘留。
- 未建立網路 client、真實 adapter、outbox worker、reconciliation、launchd 或 composition
  root；未讀取 Keychain、未使用任何 API 憑證；未 stage、commit 或 push。P2-A 狀態為
  implementation completed, pending independent acceptance。

## 2026-08-17 — P2-B/C/D/E 實作、對抗式審查與修復

- 完成 P2-B（repository/引擎）、P2-C（帳本投影/對帳/migration 0004）、P2-D（控制平面/
  composition root/migration 0005/新增 POSTGRES_RUNTIME_PASSWORD secret kind）、P2-E
  （Alpaca Paper adapter，injectable transport、零網路測試）。
- 狀態機修正：SUBMITTING 新增 PARTIALLY_FILLED/FILLED 出邊（crash 後恢復時 broker 可能已成交）；
  Python 與 SQL 兩側同步。
- 對抗式審查發現並修復五項：reconciliation 缺 collect→persist→auto-pause 編排；resume 字串
  比较；ledger 賣出現金上界；不可表示的 broker 終態竞態改為 typed fail-closed 零副作用；上述
  各補對抗測試（mismatch 自動暫停、clean 不暫停、terminal race）。
- 亦修復開發中自查問題：application 層誤引 urllib（P1 源不變量擋下，DSN 組合移至
  infrastructure）、runtime role placeholder 計數、多處測試冪等/commit 順序錯誤。
- 最終 gate：`verify_p1.sh` EXIT=0（non-integration 563 passed）；真實 PostgreSQL 16
  `60 passed, 0 skipped`；`uv.lock` 未變；無容器殘留。ADR-018/PROGRESS 已更新。
- 未接觸網路、Keychain 或任何真實憑證；P2-E 真實 endpoint 連線驗證依使用者決策待金鑰提供後
  另行執行；未 stage/commit/push。P2-B~E 狀態為 implementation completed, pending
  independent acceptance。

## 2026-08-17 — P2 第二輪：ROADMAP 逐項比對補缺與第二輪對抗審查

- 以 ROADMAP P2 驗收清單重新稽核，發現三項缺口並補齊：trade update consumer（含
  duplicate/out-of-order 冪等、STALE/DUPLICATE/UNKNOWN_ORDER 分類、外部取消
  CANCEL_PENDING 路由）、NAV valuation、reconciliation/control 的真實 PostgreSQL
  整合測試（含 mismatch 自動暫停與 append-only 對抗）。
- 新增 ADR-019：WS 傳輸與 control shell CLI 延後至 P6/P7 的範圍聲明（DSN 禁入 argv/env，
  CLI 需完整 runtime credential 路徑）。
- 第二輪對抗審查修復：重播事件分類、外部取消路由、fake repo updated_at 單調性；
  各補對應測試。
- 最終 gate 全綠：`verify_p1.sh` EXIT=0（575 passed non-integration）；真實 PostgreSQL 16
  `63 passed, 0 skipped`；無殘留容器；未接觸網路、Keychain 或任何真實憑證；
  未 stage/commit/push。

## 2026-08-17 — P2 第三/四輪審查：window cutoff 安全語意修復與不變量補強

- 確認 codex 原目錄工作樹乾淨（零修改）；所有編輯僅在 zcode 工作副本內。
- 第三輪對抗審查發現真實缺陷：`expire_overdue` 盲目把逾期 ACKNOWLEDGED/UNKNOWN 本地
  EXPIRED，不查 broker 也不取消，會留下活單與本地終態分歧。修復為 ADR-020 的四步截止
  順序（解析→取消→僅無單過期→transport error 不過期），並新增四個窗口截止測試
  （取消已接受單、無單過期、UNKNOWN 先解析、取消 transport failure 停於 CANCEL_PENDING）。
- 新增永久不變量整合測試：對所有 (current,target) 狀態對，SQL transition 函數與 Python
  封閉映射逐對相等（intent 與 broker 兩套映射），防止未來漂移。
- 第四輪掃描：P2 檔案無 `del`/`type: ignore`/TODO/NotImplemented 殘留；PROGRESS 現況
  段落與「尚未開始」清單更新至 P2 實作完成狀態。
- 最終 gate：`verify_p1.sh` EXIT=0（Ruff、Mypy strict 85 檔、non-integration
  `578 passed, 64 deselected`）；真實 PostgreSQL 16 `64 passed, 0 skipped`；無容器殘留。
  未 stage/commit/push（等待使用者指示）。

## 2026-08-17 — Handoff 與治理文件同步至 P2 實作完成狀態

- 更新 `PROJECT_HANDOFF.md`： header 現況（P2 五包實作完成待獨立驗收、工作副本位置與
  codex 唯讀最高指令）、§8 工作區（ZCode 副本、43 檔未 stage、push 待使用者同意、最後
  已發布 commit `374d121`）、§10 新增 P2 完整交付與自測證據段落、§12 增列 R-15/R-16、
  §17 一句話狀態改寫、§18 新 session Prompt 改為 P2 獨立定點驗收流程（含三個必重現
  安全不變量）。P0/P1 歷史段落未改寫。
- 更新 `SECURITY.md`：composition root 段落由「尚未存在、P2 需定義」改為現行契約
  （exact-schema 解析邊界、POSTGRES_RUNTIME_PASSWORD exact ref、單一 bounded reveal、
  application 層禁 urllib/網路 SDK）。
- 更新 `docs/ROADMAP_AND_ACCEPTANCE.md`：P2 段落後新增 gate 現況（自測證據、待獨立
  驗收、P2-E 待金鑰、WS/CLI 延後範圍）。
- `RISK_REGISTER.md` 依規則新增 R-15（adapter 未經真實 endpoint 驗證，Deferred）與
  R-16（order 事件軌跡方式，Accepted）；既有列未改寫。
- 本輪僅文件變更；未動程式、未 stage/commit/push。

## 2026-08-17 — P2 獨立對抗審查補強輪：五缺陷 red→green 修復與治理文件同步

- 第二 session 以 ISSUES.md A–N 缺陷清單對 P2 交付做獨立對抗審查；先寫 reproduction、
  確認 red、記錄證據後修復。五個真實缺陷全部修復並加防回歸測試（詳見
  `PROGRESS.md`「P2 補強輪」與 `DECISIONS.md` ADR-021）：
  A：engine 內嵌 pause 檢查（`ExecutionPausedError`、`build_execution_stack(control=...)`）；
  E：migration 0006 broker_orders 雙時鐘（broker_updated_at + trigger 單調）＋consumer
  STALE 基準修正；F：重複 client_order_id 以 by_client_order_id GET 解析回 SubmitAccepted；
  H：fills after-cursor 分頁循環（100+ 全數回傳）；G：reconciler 補 terminal intent/
  terminal mirror vs broker 開單兩盲點（`INTENT_STATUS_MISMATCH`＋第二趟全單掃描）；
  N：CI postgres job（`-m "integration and not live"`）與本機 script 同步。
- 先 red 後 green：pause 套件（收集期缺依賴、5/5）、timestamps（PG roundtrip 回讀本地
  時間、2/2）、duplicate（重複回 SubmitRejected）、pagination（101 只回 100）、reconciler
  （兩案 red）、CI 契約（red）——各確認 red 原因即缺陷本身。
- PostgreSQL gate 除錯：0006 down migration 缺 `DELETE FROM schema_migrations`（rollback
  後版本卡 6）與 NOT NULL 新欄位之 test fixture 未回填——修正後整合全綠。
- 最終 gate：`verify_p1.sh --postgres` EXIT=0；Ruff format 90 檔、Ruff check、Mypy strict
  90 檔全綠；non-integration `589 passed, 74 deselected`；真實 PostgreSQL 16
  `66 passed, 8 deselected`；無容器殘留。未接觸網路、Keychain 或任何真實憑證。
- 治理文件同步：DECISIONS.md 新增 ADR-021（pause 內嵌引擎、雙時鐘、重複解析、終態對帳，
  附證據）；ISSUES.md OPEN-017→CLOSED-017、OPEN-018→CLOSED-018（附修復前證據與驗證），
  ASSESSED-019 更新為「全部修復」；RISK_REGISTER.md R-15（adapter）以 P2-E 真實驗證 +
  本輪整合證據更新為 Mitigated；PROGRESS.md 增補強輪段落（pending independent acceptance
  維持不變）。
- 未 stage/commit/push（等待使用者指示）。

## 2026-08-18 — P2 second remediation：執行安全硬化（實際完成）

- 第一輪規劃確認的核心不變量「broker truth 未知時不得自行宣告終態」全部落為實作
  （詳 `PROGRESS.md`「P2 second remediation」與 `DECISIONS.md` ADR-022）：
  1. 引擎：`resolve()` 重寫（deadline 後查無單→UNKNOWN）、`expire_overdue()` 只對未達
     broker 的狀態 EXPIRED、取消路徑 transport 錯誤保留 CANCEL_PENDING、`recover()`
     消除同 sweep 重複 resolve。
  2. watermark：0007 清 NULL broker_updated_at、submitted_at lower bound、
     DUPLICATE／equal-timestamp 衝突語意。
  3. SQL guard：0007 完整 15 態 CHECK、filled 不倒退／FILLED exact／身份 immutable
     （INSERT+UPDATE 側）、REVIEW_REQUIRED 收斂。
  4. flatten 六步＋FlattenPriceProvider seam＋`flatten_generation`（同交易原子遞增）；
     position 對帳不符即 abort（零新單）。
  5. asset gate（submit 前 get_asset，含 RISK_EXIT；flatten 預檢）。
  6. `reconciliation_mismatches` 明細表（append-only、ordinal+kind+detail）＋
     closed-history pass（list_recent_orders since horizon）。
  7. `domain/session.py`：America/New_York trading date；p2e CLI 同步、維持 GET-only。
- PG integration 兩次紅燈均修正：SQL `chr(0)` 檢查違反 PG「null character not permitted」
  （改用 domain 已驗證的 bounded text 檢查）；`_assert_runtime_privileges` placeholder
  計數（2＋18 而非 2＋19）。
- 最終 gate：`uv sync --python 3.13 --locked`/`uv lock --check` 綠；Ruff format/check
  92 檔、Mypy strict 92 檔全綠；non-integration `621 passed, 74 deselected`；
  `run_postgres_integration.sh` 與 `verify_p1.sh --postgres` 皆 `66 passed, 8 deselected`
  （live marker 排除，`SEVEN_LENS_P2E_LIVE` 未設）；無容器殘留；未讀取 Keychain、
  未使用任何 API 憑證。
- 治理文件：DECISIONS.md ADR-022；ISSUES.md CLOSED-020/021；RISK_REGISTER.md
  R-21～R-24（Mitigated）；PROGRESS.md 追加段落，gate 陳述為唯一寫法
  "P2 second remediation implementation completed; pending independent re-acceptance."。
- 未 stage/commit/push（等待使用者指示）。
