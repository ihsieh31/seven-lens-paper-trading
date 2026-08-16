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
