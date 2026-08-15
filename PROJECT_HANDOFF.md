# Seven-Lens Paper Trading 專案交接文件

最後更新：2026-08-15
專案路徑：本 repository root
目前階段：`P1 — Core foundation` 與後續 authority hardening 已完成；本機、clean-machine 與遠端 CI 均通過，`P1 Core Gate` 保持關閉
下一個最小步驟：依第 18 節的新 session Prompt，先討論並定義 `P2 — Alpaca Paper 執行安全` 的最小安全工作包與 acceptance；不得直接假設已授權 broker/order implementation

---

## 1. 這份文件的用途

這是新對話的主要交接入口。新的 AI 應先完整閱讀本檔，不需要使用者重新解釋專案。

新 AI 在這個專案中的主要工作不是無限制地自行開發，而是：

1. 清楚理解整體企劃、不可變更的安全邊界與目前階段。
2. 接收其他 implementation agent 的完成報告。
3. 不相信完成報告本身，必須檢查實際檔案並重現重要驗收證據。
4. 依變更風險選擇「定點驗收」或完整驗收；使用者明確要求不要不必要的大量掃描。
5. 若驗收失敗，清楚列出根因與可直接貼給修復 agent 的 Prompt；除非使用者明確要求，不自行修復。
6. 若驗收通過，更新判斷並規劃下一個最小階段，提供完整、可直接貼給下一個 agent 的 Prompt。
7. 保持 `PROGRESS.md`、`WORKLOG.md`、`ISSUES.md`、`DECISIONS.md` 與實際證據一致。

回覆使用者時使用繁體中文、先講結論，再列證據與下一步。金融系統的「測試通過」不等於可交易或可實盤。

---

## 2. 專案目標

建立一套只供使用者本人使用、只做 Alpaca Paper Trading、可無人值守運行的美股中期持有系統：

- 每個交易日開盤前完成分析與選股。
- 開盤後進行主要再平衡。
- 收盤前再評估持倉與少數高順位候選，允許第二次受限再平衡。
- 不做當沖；正常持有 10–60 個交易日，除風控退出外至少持有 5 個交易日。
- 研究由七套公開投資方法論組成辯論委員會。
- LLM 只產生研究 assessment；確定性的 portfolio、risk、execution 才能產生委託。
- 正常運行不需人工逐筆批准，但必須有 fail-closed、自動停止、告警、對帳與人工緊急控制。

這是全新專案。不得匯入或沿用其他舊交易專案的程式或架構。

---

## 3. 不可變更的核心決策

### 3.1 Paper-only

- 第一版程式中完全不存在 Alpaca live adapter、live endpoint 或 `live=true/false` 切換。
- `BrokerEnvironment` 只能有 `PAPER`。
- Alpaca endpoint 必須精確等於 `https://paper-api.alpaca.markets`。
- 未知、空白、HTTP、live URL、look-alike host、尾端 path 全部 fail closed。
- 無論 Paper 表現多好，都沒有自動升級實盤的 gate。

### 3.2 LLM 沒有下單權

- LLM worker 不得取得 broker credentials。
- LLM 不得寫 order/position/ledger DB。
- LLM 輸出 schema、model、timeout、429、citation 或資料驗證失敗時，結果為 `INVALID/NO_TRADE`。
- 只有 deterministic Portfolio/Risk Engine 可以核准 `OrderIntent`。
- 只有 Execution adapter 可以呼叫 Alpaca Paper。

### 3.3 七人是唯一策略

七套 doctrine：

1. Serenity / `@aleabitoreddit`：AI、半導體、供應鏈瓶頸、多跳 BOM。
2. Citrini Research：主題、敘事週期、第二／第三階影響。
3. SemiAnalysis / Dylan Patel：AI infra、半導體技術經濟、產能與 capex。
4. Edwin Dorsey / The Bear Cave：forensic accounting、治理、激勵與商模風險。
5. Aswath Damodaran：story-to-numbers、估值、風險與 terminal assumptions。
6. Andy Constan / Damped Spring：流動性、利率、positioning、跨資產資金流。
7. Lyn Alden：財政／貨幣 regime、能源、美元與長週期資產負債表。

只蒸餾分析框架、證據偏好、反例、失效條件與 domain boundary；不模仿人格、語氣，不聲稱本人背書。

七人不是一人一票：使用 domain relevance、evidence quality、historical calibration、source overlap haircut。每位可 `SUPPORT`、`OPPOSE` 或 `ABSTAIN`。

### 3.4 TradingAgents 的定位

- 已檢查 `TauricResearch/TradingAgents` main commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`。
- 採用其多角色研究、辯論、共享 state graph 的概念。
- 不使用其 LLM Portfolio Manager 作為交易核心。
- 若未來直接使用其 code，只能作隔離、可替換的 `AnalysisProvider`，沒有券商、DB write、shell 或直接 order path。

### 3.5 架構風格

- Python 3.13 modular monolith。
- PostgreSQL：權威 run/job/audit/ledger/order/fill/reconciliation 狀態。
- DuckDB/Parquet：point-in-time 研究快照與回測資料。
- 本機 content-addressed store：必要的 raw evidence。
- macOS Keychain：API secrets。
- `launchd`：未來保持 runtime supervisor；交易時鐘仍由程式、Alpaca calendar 和 DB lease 控制。
- Codex Automations 只做測試、報告、離線蒸餾與維護，不作盤中關鍵 scheduler。

---

## 4. 資料與費用限制

- LLM 費用沒有上限。
- 除 LLM 外不付任何費用。
- 只用免費公開內容、免費 API、SEC/IR/政府資料與 Alpaca Paper/免費行情。
- 不買 X Developer API、不買研究訂閱、不繞過登入／付費牆／robots／條款。
- X 資料以離線、逐來源 discovery 為主；runtime 不依賴即時 X。缺資料時委員棄權，系統持有現金。

### Tavily

使用者持有 7 個帳號，理論容量 7,000 credits／月，但現行 Tavily 條款是否允許同一 Customer 彙總七個免費帳號尚未證實。

目前安全狀態：

- `SINGLE_ACCOUNT_UNVERIFIED`：可使用，只有一個 enabled account，全域 1,000 credits／月。
- `AUTHORIZED_ACCOUNT_POOL`：程式中存在 schema，但在可信外部 verifier 完成前固定 fail closed。
- 即使本地 evidence record 自稱 `VERIFIED`，也不能啟用七帳號。
- 取得 Tavily 書面／後台明確授權後，才可建立外部驗證流程並啟用：每帳號 1,000、全域 7,000、runtime 5,600、research/incident reserve 1,400、一般交易日 soft cap 250。
- 不跨 key 併發繞過 rate limit、不自動建立帳號、不開 PAYGO。

`OPEN-007` 必須保持 Open，直到外部授權證據與 verifier 真的完成。

---

## 5. 預設股票與風控範圍

目前是規劃／校準值，不是永久參數：

- 美國上市普通股與未槓桿 ETF。
- Long-only、無槓桿、正常交易時段、整股。
- 排除 options、short、crypto、OTC、preferred、warrant、ETN、槓桿／反向 ETF、盤前盤後。
- 價格至少 USD 5；20 日平均美元成交額至少 USD 20M。
- 最多 10 個持倉；單股 8%；sector 25%；高度相關主題 30%。
- 現金至少 20%；每日換手上限 NAV 20%；部位不超過 ADV 0.1%。
- 日內跌 1.0% 停止新增部位；1.5% 取消 entry orders；高水位回撤 8% portfolio freeze。
- 同日買入不可因 alpha 反轉賣出；同日賣出不可因 alpha 反轉買回。

任何參數調整都需要 ADR、walk-forward 與 Paper evidence。

---

## 6. 每日作業設計摘要

使用 `America/New_York` 與 Alpaca market calendar，不寫死假日或半日市：

- 04:30：資料 ingestion。
- 06:00：universe／quant screen。
- 06:30：前 30 EvidencePacket。
- 07:00–09:00：前 12 七人 assessment、verification、rebuttal、chair。
- 09:10：凍結 TargetPortfolio。
- 09:35：主要 Paper 再平衡。
- 10:00：reconciliation。
- 15:15：持倉與少數候選 refresh。
- 15:35：凍結第二份 target。
- 15:40：受限收盤前再平衡。
- 收盤後：最終 reconciliation、日報與歸因。

錯過 deadline 不追單；過期 target 不復活。

---

## 7. 開發模型與多 Agent 分工

- `gpt-5.6-sol`：架構、金融安全、schema、release gate、重大 review、下一階段 Prompt。
- `gpt-5.6-terra`：主要模組實作、integration tests、一般重構。
- `gpt-5.6-luna`：批次資料、fixtures、大量 boundary/property tests、重複工作。

若使用多 agent：

- 每位明列 owned files/modules。
- 告知彼此不是獨自工作，不得覆蓋或還原他人修改。
- 同一檔案同時只由一位 worker 修改。
- agent 回報完成不等於驗收通過。

---

## 8. 工作區與 Git 注意事項

- 專案目錄：本 repository root。
- 獨立 public repository：[`ihsieh31/seven-lens-paper-trading`](https://github.com/ihsieh31/seven-lens-paper-trading)。
- default branch 為 `main`；P1 authority hardening code commit為
  `e8543b69bfc6a6d2dd9a87837d9d46bb11afc406`，遠端run `31891905869`兩個required jobs均成功。
  後續可有只同步handoff/evidence的descendant commit；新session應以`git status`、`git log`與
  `origin/main`現況為準，不把舊hash當固定checkout要求。
- `main` branch protection 採 strict required checks：`quality-unit`、`postgres-integration`；禁止
  force push 與 branch deletion，保留 repository admin 緊急 bypass。
- 本次交接更新是使用者明確要求的本機變更；若新 session 開始時唯一 dirty file 是
  `PROJECT_HANDOFF.md`，必須保留，不得當成未知修改還原。
- 建立獨立 repository 前，它曾是另一個本機 repository 裡的未追蹤子目錄；歷史工作不得混入本專案。
- 上層 repository 可能仍有其他使用者變更；不得清理、還原或修改本 repository 外的內容。
- 未經使用者針對新工作包明確要求，不 stage、commit、push、建立 PR、改 repository visibility
  或變更 branch protection。
- 專案現有 `.venv`、Python 3.13 與 `uv.lock`；一律以 locked commands 和 CI 定義為準，不依賴
  特定 patch 版本已存在於新機器。

---

## 9. 文件地圖

本交接檔足以理解專案。需要深入時依序讀：

1. `README.md`
2. `docs/MASTER_PLAN.md`
3. `docs/ARCHITECTURE.md`
4. `docs/OPERATIONS_AND_SAFETY.md`
5. `docs/ROADMAP_AND_ACCEPTANCE.md`
6. `docs/DISTILLATION_SPEC.md`
7. `docs/TRADINGAGENTS_ASSESSMENT.md`
8. `docs/SOURCES.md`
9. `DECISIONS.md`
10. `PROGRESS.md`
11. `ISSUES.md`
12. `WORKLOG.md`
13. `RISK_REGISTER.md`

日誌規則：不得覆寫歷史；新增事件、決策與問題。已關閉問題仍需保留。

---

## 10. 已完成進度

### P0 — 規劃與治理：完成

已完成：

- 需求固定與範圍界定。
- TradingAgents 實際程式架構評估。
- Alpaca Paper、Tavily、OpenAI/Codex 官方能力與限制核對。
- 七人公開來源與 GitHub 蒸餾候選初審。
- 主企劃、架構、蒸餾、安全、營運、roadmap、sources、ADR、進度／問題／工作／風險日誌。

### P1-A — 安全專案骨架：完成並通過驗收

已實作：

- Python 3.13 `src/seven_lens` package、`uv` lock、ruff、mypy、pytest。
- Paper-only broker config、精確 endpoint allowlist、exact mapping schema。
- Tavily compliance／quota schema 與 fail-closed authorized pool。
- `RunId`、`TradingDate`、`UtcTimestamp`、`SchemaVersion`。
- bounded secret redactor、JSON-safe structured logging、固定安全 fallback。
- `.env.example` 與 `.gitignore`；沒有真實 secrets。

實作 agent 完整驗證結果：

- Python 3.13.14。
- `uv sync --python 3.13 --locked` 成功。
- `uv lock --check --offline` 成功。
- Ruff format／lint 通過。
- Mypy 16 source files 通過。
- 完整 pytest：`128 passed`。

最後一次獨立定點驗收：

- 只驗證之前發現的問題，沒有重新做大量掃描。
- `tests/test_redaction_and_structured_logging.py`、`tests/test_tavily_config.py`、`tests/test_value_objects.py`：`100 passed`。
- 修正相關 7 檔 Ruff format、lint、Mypy 全通過。
- 原始 PoC 全部關閉：Basic、多字 password、bytes、自訂物件、cycle、`x:abc`、noncanonical UTC、超長 SchemaVersion。
- 結論：`P1-A ACCEPTED`。

### P1-B — PostgreSQL 權威狀態：完成並通過真實 PostgreSQL 驗收

已實作：

- persistence-neutral repository/unit-of-work ports 與 psycopg 3 direct-SQL adapter。
- checksummed initial up/down migration：metadata、domain/audit events、job instances/leases。
- database-stamped event envelope、contiguous aggregate sequence、append-only trigger 與 audit secret rejection。
- rollback-by-default UoW、job state + audit 同 transaction。
- DB-clock atomic lease acquire/renew/release/takeover、history、attempt count 與 fencing guard。
- pure market clock port 與 deterministic regular/half-day/closed fake。

實際驗收：

- PostgreSQL `16.14`（`postgres:16-alpine`）integration：18/18。
- migration 9/9：clean/repeat/checksum、up/down/up restore、constraints、append-only、secret/event ordering。
- persistence/lease 9/9：rollback、concurrency、renew/release、expiry takeover/restart、fencing、DB clock。
- 完整 pytest：`242 passed`；Ruff format/lint、mypy、uv lock checks 全通過。
- 沒有新增 broker/order/fill/position 表或任何 API client／排程／交易路徑。

### P1-C1 — macOS Keychain secret boundary：已通過獨立驗收

已實作：

- typed fixed-mapping `SecretRef`、non-disclosing `SecretValue` 與 persistence-neutral `SecretProvider`。
- execution/research capability allowlist，在 backend call 前拒絕越權 exact ref。
- Security.framework/PyObjC generic-password exact read-only adapter，禁止 authentication UI，沒有 `/usr/bin/security`、shell 或 write/list/export 能力。
- 預設 2 秒 spawned hard-timeout worker；timeout/crash/malformed IPC fail closed 並清理 child/IPC。
- `.env.example` 移除所有 secret value input names；沒有 env、argv、DB、第二 provider 或 production fake fallback。

Implementation agent 自測：

- 72 個 P1-C1 fake-only tests 通過。
- 65 個既有 redaction/structured logging、broker config、Tavily config regression tests 通過。
- 完整 non-integration：296 passed、18 integration deselected。
- Ruff format/lint、Mypy、offline lock check 通過；Security native module/function/constants 可 import。
- 從未呼叫真實 `SecItemCopyMatching`、讀取使用者 Keychain 或驗證真實 credential。

獨立驗收曾發現並關閉兩個固定 mapping 繞過：

- `SecretRef` subclass 可覆寫 service/account；已以 runtime sealing、exact-type trust boundary 與 adversarial tests 關閉。
- `_kind`／`_account_id` 建立後可被一般 assignment 改寫；已改為 immutable semantics、sealed identity 與每次 trust-boundary revalidation。

最後獨立定點驗收：P1-C1 `88 passed`，原始 subclass／mutation PoC 均被阻擋，
runner calls 為空，Ruff、Mypy、offline lock check通過。這不代表 P1-C／P1 Core Gate完成。

### P1-C2 — dependency-neutral metrics/traces：已通過獨立驗收

已實作：

- canonical non-zero `TraceId`／`SpanId` 與 explicit immutable `TelemetryContext`；child保留 run/correlation/trace並保存 parent，不使用 ambient context。
- dependency-neutral typed `MetricRecorder`／`TraceRecorder` contracts，封閉五個 metrics與兩個 spans；沒有任意 name/attributes API。
- registry強制 exact keys／enum values、每筆最多4 attributes（正式最多2）、value 64字元、每 instrument 64 active series，以及禁止 identifier、account/job、URL/DSN/Authorization、payload、exception material。
- fail-safe facade使用 injectable monotonic clock；recorder `Exception`只形成 process-local drop count與固定 diagnostic，`BaseException`不吞，diagnostic不遞迴呼叫 backend。
- application-layer secret provider decorator與既有 public `transition_job_with_audit` instrumentation；native Keychain bridge不含 telemetry。job path在span/UoW/repository前要求audit具有run ID並與context的run/correlation identity一致，mismatch固定typed error且零副作用；success只在commit及UoW正常退出後記錄。
- structured logging可安全注入validated context；沒有 context的startup/config log不偽造 IDs。
- deterministic telemetry fakes與79個telemetry tests；完整 non-integration `391 passed, 19 deselected`，真實 PostgreSQL 16 integration `19 passed, 0 skipped`。

獨立驗收第一次發現 `transition_job_with_audit` 未強制 telemetry context 與 AuditEvent
使用相同 `run_id`／`correlation_id`；當時正常 fixture實際為兩組不同ID仍可commit。已新增
`AuditTelemetryContextMismatchError`，在clock/span/UoW/repository前固定、無ID地fail closed，
並修正unit/integration fixtures及三個mismatch adversarial cases。

最後獨立定點驗收：

- P1-C2 tests：`79 passed`。
- 真實 PostgreSQL `16-alpine` integration：`19 passed, 0 skipped`。
- audit failure rollback、telemetry failure下state+audit atomic commit、stale fencing、expiry takeover通過。
- Ruff、Mypy、offline lock check通過；專用PostgreSQL container已停止並移除。

明確沒有開始 OpenTelemetry/exporter/backend、API client、broker/order/fill schema、策略、資料、
下單、launchd或正式告警。P1-C2已接受；這仍不代表P1-C或P1 Core Gate完成。

### P1-C3 — CI／zero-skip／clean-machine gate：已通過本機、clean-machine 與遠端獨立驗收

已實作：

- `.github/workflows/ci.yml` 只有 `quality-unit` 與 `postgres-integration` 兩個 Ubuntu 24.04 jobs；
  read-only permission、checkout不保留credential、PR-only cancel concurrency，沒有 secret、OIDC、
  deploy token、`pull_request_target` 或 hosted macOS job。
- action 固定 reviewed release full SHA；uv 固定 `0.12.5`；PostgreSQL official image 固定
  `16.15-alpine` 與 OCI index digest。
- pytest integration marker 靜態定義；psycopg保持正式 dependency；required mode在collection前以固定
  bounded error驗證URL、driver、連線與server major 16，且任何 integration skip令session失敗。
- `verify_p1.sh` 以uv作唯一bootstrap prerequisite；`run_postgres_integration.sh`使用fake credentials、
  random localhost port、tmpfs與60秒bounded readiness，只在container ID、exact name及ownership
  label皆相符後精確清理。
- 新增16個對抗測試，涵蓋required gate、skip failure、普通unit run、prerequisite、workflow pinning/
  permissions/commands與fake Docker exact cleanup。

Implementation agent目前證據：non-integration `407 passed, 19 deselected`；真實 digest-pinned
PostgreSQL `16.15` integration `19 passed, 0 skipped`；Ruff、Mypy、lock checks通過；執行前後
`uv.lock` SHA-256皆為 `79809edba36965084b7561d616b0f95902e28e8fd4da6b07f35c409b6b34626b`；
owned container清單為空且Docker volume set未變。

2026-08-15定點獨立驗收：官方release與commit頁面確認三個action pin；Docker本機RepoDigest確認
PostgreSQL image digest。16個P1-C3對抗測試、Ruff、Mypy、`407 passed, 19 deselected`與真實
PostgreSQL 16.15 `19 passed, 0 skipped`均通過。required mode的missing URL與SQLite各自於collection
前以exit 4失敗；clean-machine隔離副本排除`.venv`並使用全新空uv cache，兩個一鍵命令均成功。
`uv.lock`前後SHA-256不變，Docker volume集合hash不變，owned container為空。P1-C3因此通過本機
獨立驗收，P1-C本機交付完成。其後建立公開且獨立的
[`ihsieh31/seven-lens-paper-trading`](https://github.com/ihsieh31/seven-lens-paper-trading) repository；
首次遠端 workflow 因 job-level `env` 不允許 `job.services` context 而在建立 jobs 前失敗。DSN
expression 移到 integration test step 後，GitHub Actions run
[`31868962828`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31868962828) 在 commit
`4e795ff1dc6d5b6bc51d4bd0e55149fda3e4cc61` 上通過兩個 jobs：`quality-unit` 為
`407 passed, 19 deselected`，Ruff/Mypy/lock checks 通過；`postgres-integration` 驗證 PostgreSQL
16.15 且 `19 passed, 0 skipped`。P1 Core Gate 因此關閉，仍未進入 P2。

Gate closure 文件 commit `2982c0d6a911036a150245e6f408f064d3d8f5df` 另由最終 GitHub Actions
run [`31869097859`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31869097859)
再次驗證；兩個 jobs 均為 `success`。其後 `main` 已設定 strict required checks：`quality-unit` 與
`postgres-integration`，並禁止 force push／branch deletion；repository visibility 經 GitHub API
核對為 `public`，default branch 為 `main`。

Clean-machine evidence來自 `/private/tmp/seven-lens-p1c3-clean-evidence-20260815/repo` 隔離副本：
copy時排除原專案 `.venv`、使用全新空 uv cache；第一次 `verify_p1.sh` 與第二次
`verify_p1.sh --postgres` 的 non-integration 均為 `407 passed, 19 deselected`，第二次另有
PostgreSQL `19 passed, 0 skipped`。lock hash前後一致、volume set未變、owned container為空；
驗證完成後只刪除該精確臨時副本，目前已不存在。

---

## 11. P1-A 曾發現並已解決的問題

### CLOSED-008：Structured logging 洩漏 secret／audit 中斷

舊問題：

- Basic Authorization、quoted/multi-word credential 未完整遮蔽。
- bytes、set、自訂物件在 redaction 後被 `default=str` 序列化，可能洩漏 token。
- secret-bearing mapping key 可洩漏；非字串 key 可碰撞。
- self-referential list 造成 `RecursionError`，沒有 audit event。

修復：

- redactor 只輸出 JSON-safe primitives/containers。
- unsupported object 變成 `[UNSAFE_LOG_VALUE]`，不呼叫 `str/repr`。
- Basic／Bearer、quoted/multi-word secrets 完整遮蔽。
- mapping key 驗證與不碰撞 placeholder。
- cycle/depth guard。
- 移除 `default=str` 與 `record.getMessage()` sink。
- 失敗時輸出不含原始 fields 的 `structured_log_serialization_failed`。

### CLOSED-009：UtcTimestamp／SchemaVersion 邊界過寬

舊問題：接受替代分隔、compact/week date、`-00:00`；SchemaVersion 可接受數千位數字後延遲失敗。

修復：

- wire format 固定 `YYYY-MM-DDTHH:MM:SS.ffffffZ`。
- SchemaVersion 每個 component 限制 `0..9999`，constructor 立即拒絕。

### Tavily evidence 假證據問題

舊問題：`x:abc` 或任意 scheme-like 字串可啟用 7,000-credit 模式。

修復：

- 建立 immutable evidence-record metadata、source、source record id、account set、verified time、status。
- placeholder/fake id 被拒。
- 最重要：外部 verifier 尚不存在，因此 `AUTHORIZED_ACCOUNT_POOL` 無條件 fail closed。
- 這只關閉程式 fail-open；Tavily 是否允許七帳號仍是 `OPEN-007`。

---

## 12. 目前未解決問題

- `OPEN-001`：七人公開語料完整性不均。
- `OPEN-002`：公開來源授權與再散布邊界。
- `OPEN-003`：免費行情／基本面資料品質。
- `OPEN-004`：無人值守依賴單台 Mac。
- `OPEN-005`：蒸餾與歷史回測前視偏差，Critical。
- `OPEN-006`：免費告警通道尚未選定。
- `OPEN-007`：Tavily 七帳號彙總使用權尚未證實。

P1-B 不應假裝解決這些問題；只在其範圍真的建立控制時更新。

---

## 13. 驗收其他 Agent 的標準流程

使用者偏好精準驗收，不要每輪都做大型 Deep Scan。

### 一般流程

1. 讀 agent 回報，但只把它當成待驗證主張。
2. 檢查 `git status` 與實際修改檔案；保留使用者其他變更。
3. 讀所有本輪 owned source、migration 與測試檔案。
4. 將 agent 宣稱的安全不變量對應到真正的 DB constraint、程式控制與測試。
5. 重跑 agent 回報的 formatter/lint/type/tests。
6. 額外執行最少量、針對高風險邊界的 adversarial reproduction。
7. 驗收失敗：說明 root cause、最小修復範圍、阻擋哪個 gate，提供修復 Prompt。
8. 驗收通過：說明獨立證據、仍未完成事項，提供下一階段 Prompt。

### 何時只做定點驗收

- 先前已完整掃描／驗收，只修復明確問題。
- 變更檔案少、邊界清楚、使用者要求不要大量掃描。
- 只重跑受影響測試、相關 lint/type，以及原 PoC。

### 何時需要擴大

- 新增 broker/order/reconciliation、auth/secrets、DB transaction、scheduler/lease、網路 adapter。
- 修改共用安全 helper 或 hard risk constraints。
- Agent 的變更超出回報範圍。
- 發現一個可跨模組擴散的新 root cause。

不要因為 tests pass 就自動宣稱安全；也不要因為是金融系統就每次無差別重掃整個 repository。

---

## 14. P1-C3 已授權範圍、驗收重點與下一個邊界

P1-A／P1-B／P1-C1／P1-C2／P1-C3 與遠端 CI 已通過獨立驗收，P1 Core Gate 已關閉。下列
P1-C3範圍與驗收重點保留作已執行的驗收紀錄；這不代表已開始或授權P2。

P1-C3 已授權交付：

- `.github/workflows/ci.yml`：只含Ubuntu `quality-unit`與`postgres-integration`兩個jobs。
- actions固定完整commit SHA、uv固定版本、PostgreSQL 16.x Alpine固定reviewed digest。
- `permissions: contents: read`、`persist-credentials: false`、無`secrets.*`、OIDC、deploy token或`pull_request_target`。
- `REQUIRE_POSTGRES_INTEGRATION=1`時，missing URL、SQLite、連線失敗、非PostgreSQL 16、缺psycopg或任何skip全部fail。
- integration modules移除`pytest.importorskip("psycopg")`；一般non-integration本機run仍可排除integration。
- `scripts/verify_p1.sh`：locked sync、lock、format、lint、mypy、non-integration；`--postgres`才進DB測試。
- `scripts/run_postgres_integration.sh`：唯一disposable container、random localhost port、bounded readiness、exact cleanup、無volume／prune／DSN輸出。
- 在沒有專案`.venv`／uv cache的隔離副本執行兩個一鍵命令，驗證`uv.lock`不變與零殘留。
- 不建立自動GitHub-hosted macOS job；Keychain native contract保留本機fake-only驗證，避免非LLM服務費用。

P1-C3定點獨立驗收已依下列項目完成：

1. Workflow YAML是否真的只有兩個Ubuntu jobs；permissions、triggers、concurrency與pinning是否精確。
2. action SHA與PostgreSQL image digest是否能對應agent提供的官方來源／reviewed release，而非猜測或mutable-only tag。
3. required integration模式是否能用最小PoC證明missing URL、SQLite與skip均非零退出；普通unit run不被誤傷。
4. integration tests是否真連PostgreSQL 16，`passed/skipped`明確，沒有SQLite/mock fallback。
5. shell scripts是否安全解析random port、bounded等待，且success、failure、interrupt均只清理自己建立並核對identity的container。
6. clean-machine evidence是否來自隔離副本，不是沿用現有`.venv`／cache；`uv.lock`前後hash一致。
7. workflow/scripts是否沒有Keychain、API key、broker request、`docker prune`、curl-pipe-shell、stage/commit/push或trading外修改。
8. 測試後是否沒有container、port、volume、DB或background process殘留。

狀態規則：

- implementation agent完成後只能標示`P1-C3 implementation completed, pending independent acceptance`。
- P1-C3本機驗收通過後，只能說P1-C本機交付完成；遠端兩個required jobs成功後才可關閉`P1 Core Gate`。
- 2026-08-15 的遠端 run `31868962828` 已滿足上述條件，P1 Core Gate 已關閉。
- GitHub 發布授權只涵蓋本工作包；不得據此推定 P2 broker/order implementation 已獲授權。

---

## 15. 已執行的 P1-B Prompt（歷史紀錄，不得重跑）

下列 Prompt 已在本輪完成並由真實 PostgreSQL 驗收，只保留作 scope／acceptance 歷史。
下一個 AI 不應再次執行；除非 P1 source／migration／workflow 後續被修改，也不應重跑已封關的
P1 驗收。新 session 應先讀第 10、14、17、18 節，再進行 P2 定義討論。

```text
請開始 P1-B：PostgreSQL 權威狀態、append-only audit/domain events、job lease 與 market clock abstraction。

工作目錄：
<repository-root>

這是延續專案。先完整閱讀 PROJECT_HANDOFF.md，再依其中文件地圖閱讀 P1-B 直接相關文件，至少包含：
- docs/ARCHITECTURE.md
- docs/OPERATIONS_AND_SAFETY.md
- docs/ROADMAP_AND_ACCEPTANCE.md
- DECISIONS.md
- PROGRESS.md
- ISSUES.md
- WORKLOG.md

先檢查 AGENTS.md、目前 Git 狀態與既有檔案。這段歷史 Prompt 執行時，本目錄仍是上層 repository 的未追蹤子目錄；只能修改 `<repository-root>` 內本任務需要的檔案，不得清理、還原、stage、commit 或 push。

P1-A 已通過獨立驗收。保留 Paper-only、Tavily authorized pool fail-closed、canonical UTC、JSON-safe secret redaction 與 safe logging fallback，不得削弱或繞過。

本次只完成 P1-B，不得開始 broker adapter、Tavily/OpenAI client、行情、策略、蒸餾、下單、automation 或 launchd。

交付範圍：

1. PostgreSQL persistence adapter
   - domain 不直接依賴 PostgreSQL、SQLAlchemy、psycopg 或 migration framework。
   - 先定義 repository/unit-of-work ports，再由 infrastructure adapter 實作。
   - 使用真正 PostgreSQL integration tests；不得以 SQLite 代替 PostgreSQL-specific 驗證。
   - 所有資料時間使用 UTC；lease 判斷以資料庫時鐘為權威。

2. Migration 系統
   - 建立可重現 initial migration。
   - 至少包含 schema metadata、domain_events、audit_events、job_instances/job_leases。
   - 支援乾淨 DB 建立、upgrade 驗證及明確 rollback/restore 策略。
   - 不建立 order、fill、position 或 broker 表。

3. Domain event envelope
   至少包含：event_id、event_type、schema_version、aggregate_type、aggregate_id、aggregate_sequence、run_id、correlation_id、causation_id、occurred_at、recorded_at、payload、producer_version。

   要求：
   - event id 與 aggregate sequence 有 DB uniqueness/idempotency constraints。
   - payload 必須是明確 JSON-safe schema，不得 `default=str`。
   - occurred_at 來自 domain；recorded_at 來自 PostgreSQL UTC 時鐘。
   - 同一 aggregate sequence 不可重複或倒退。

4. Append-only audit ledger
   - audit event 寫入後，應用程式不得 UPDATE 或 DELETE。
   - 必須由 PostgreSQL constraint/trigger/privilege 等可驗證機制強制，不能只靠 Python convention。
   - audit payload 不得保存 API key、Authorization header 或未遮蔽 secret。
   - audit write 與對應狀態變更可放在同一 transaction。
   - audit 寫入失敗必須 rollback 全部狀態變更。

5. Job instance 與 lease
   至少包含 deterministic job key、trading_date、job_type/window、status、lease_owner、leased_until、fencing_token、attempt_count、created_at、updated_at。

   行為：
   - 同一 job key 只有一個有效執行者。
   - acquire、renew、release、expired takeover atomic。
   - 舊 owner 不能用過期 fencing token 寫入。
   - process crash 後可安全接管。
   - 不使用本機 clock 判斷 lease。

6. Market clock abstraction
   - 建立純 domain/application port 與 deterministic fake。
   - 不連 Alpaca、不寫死排程。
   - 能表達 trading date、market open/close、regular session、half-day、holiday/closed day。

7. 測試至少覆蓋
   - migration 建立與 PostgreSQL schema constraints。
   - append-only UPDATE/DELETE 被 DB 拒絕。
   - transaction rollback 時狀態與 audit 都不落地。
   - duplicate event id／aggregate sequence 被拒。
   - malformed/non-JSON-safe payload 被拒。
   - concurrent lease acquisition 只有一個成功。
   - renew/release owner 驗證。
   - expired takeover 與 fencing token 增加。
   - stale owner 寫入被拒。
   - DB UTC clock、不依賴本機 clock。
   - half-day、holiday、closed-day fake clock。
   - normal、boundary、invalid、concurrency、restart cases。

8. 文件與紀錄
   - 新增 ADR：PostgreSQL driver、migration、transaction、append-only 與 lease/fencing 策略。
   - 更新 README 的本機 PostgreSQL setup/test commands。
   - 更新 PROGRESS.md、WORKLOG.md；新問題追加 ISSUES.md。
   - 不得把 P1 Core Gate 標為完成，除非所有剩餘 P1 項目真的完成。

多 Agent 分工：
- 你（gpt-5.6-sol）負責 schema、transaction/fencing safety、架構整合與最後 review。
- 若環境允許，可把 migration/repository adapter 交給 Terra，把 concurrency/rollback/boundary tests 交給 Luna。
- 必須明列 owned files，告知彼此不是單獨工作、不得覆蓋他人修改。

執行要求：
- 先提出精簡、可驗證的工作計畫，再直接實作。
- 使用 apply_patch 修改檔案。
- 安裝依賴或啟動本機 PostgreSQL需要權限時，走正常 approval；不得繞過。
- 不索取或使用 Alpaca、Tavily、OpenAI API key。
- 不送任何 broker/data/model request。
- 不 stage、commit 或 push。

完成後執行並回報：
- uv sync --python 3.13 --locked
- uv lock --check --offline
- uv run ruff format --check .
- uv run ruff check .
- uv run mypy
- uv run pytest -q

另需回報：
- 真正 PostgreSQL integration test 結果。
- migration upgrade/rollback 或 restore 驗證。
- append-only DB enforcement 證據。
- transaction rollback 證據。
- lease concurrency/fencing 證據。
- 實際修改檔案。
- 未完成事項與下一個最小步驟。
```

---

## 16. P1-B 重新驗收時應優先檢查的地方

不要先相信「所有 tests passed」。先驗證：

1. Integration tests 是否真的連到 PostgreSQL，而不是 SQLite、mock 或只檢查 SQL 字串。
2. Migration 是否真的套用到乾淨 DB，constraint/trigger 是否存在。
3. Append-only 是否由 DB 拒絕 `UPDATE/DELETE`，不是 repository 沒提供 method 而已。
4. State mutation 與 audit insert 是否同一 transaction；刻意讓 audit insert 失敗後狀態是否 rollback。
5. Aggregate sequence 是否只防 duplicate，還是也能防倒退／跳過的並發競爭。
6. Lease 是否使用 PostgreSQL clock，acquire/renew/release 是否 atomic。
7. Fencing token 是否真正被後續寫入條件驗證；只生成 token 但沒有 consumer guard 不算完成。
8. 兩個 concurrent DB connections 是否只有一個取得 lease。
9. Expired owner 恢復後是否能錯誤更新 job。
10. JSONB payload 是否可能經 `str/repr/default=str` 輸入 secret 或非 JSON 值。
11. Migration downgrade 是否會破壞 audit；若基於安全理由禁止 downgrade，要有 ADR 和 restore 策略。
12. 測試是否留下 background PostgreSQL、container、port、暫存 DB 或 secrets。

本輪以上項目已全部通過。若 P1-B source/migration 後續變更，必須重新使用真實 PostgreSQL
驗證。本輪authority hardening因修改migration、job service與event schema，已重新通過真實PostgreSQL
16的33個integration tests與遠端zero-skip job；不要直接跳到 Alpaca 下單。

---

## 17. 給下一個 AI 的一句話狀態

> P0、完整P1與authority hardening均已通過本機及獨立GitHub Actions驗收，P1 Core Gate保持關閉；hardening code commit為`e8543b69bfc6a6d2dd9a87837d9d46bb11afc406`，run `31891905869`的`quality-unit`與`postgres-integration`均成功。公開repository為`ihsieh31/seven-lens-paper-trading`，required checks維持strict。現有Keychain read-only secret boundary、explicit telemetry context、PostgreSQL owner/runtime分權、hardened SECURITY DEFINER、typed event/audit registry、bounded persisted JSON、audit/transaction/lease/fencing、zero-skip gate與clean-machine scripts；仍沒有OpenTelemetry/exporter、API client、broker/order/fill表、排程或交易能力，也未查詢真實Keychain。下一步只先討論並定義P2安全工作包，不得直接開始broker/order implementation。

---

## 18. 新 session 起始 Prompt

把以下 Prompt 貼給新的 session；這一輪只做 P2 scope／architecture／acceptance 定義，不寫程式：

```text
請接手 Seven-Lens Paper Trading 專案。工作目錄是目前 repository root。

先完整閱讀 PROJECT_HANDOFF.md，並檢查目前 Git status、branch、HEAD 與 remote；再閱讀 P2 直接相關文件：
- docs/MASTER_PLAN.md
- docs/ARCHITECTURE.md
- docs/OPERATIONS_AND_SAFETY.md
- docs/ROADMAP_AND_ACCEPTANCE.md
- SECURITY.md
- DECISIONS.md
- PROGRESS.md
- ISSUES.md
- RISK_REGISTER.md

目前權威狀態：P0、完整 P1 與 authority hardening 已完成，P1 Core Gate 保持關閉；public repository 是
ihsieh31/seven-lens-paper-trading。main 必須包含hardening code commit
e8543b69bfc6a6d2dd9a87837d9d46bb11afc406；GitHub Actions run 31891905869 的quality-unit與
postgres-integration均成功，required checks仍為strict。其後若只有handoff/evidence descendant commit，
以origin/main現況與該commit自己的CI為準，不要求HEAD永遠等於上述code commit。

本次不要寫程式、不要修改文件、不要 stage／commit／push、不要建立 PR，也不要讀取 Keychain、
索取或使用任何 credential、呼叫 Alpaca 或其他外部交易／資料 API。除非目前狀態與上述證據不符，
不要重跑已封關的完整 P1 驗收。若 Git status 唯一修改是 PROJECT_HANDOFF.md，這是使用者要求的
handoff 更新，請保留且不要還原。

本次只完成 P2 — Alpaca Paper 執行安全的需求釐清與工作包定義。請先用繁體中文向我說明金融與
執行概念，再提出可討論的方案，不要把未確認選項當成決策。至少涵蓋：

1. 將 P2 拆成最小、可獨立驗收且有依賴順序的工作包；指出第一個建議工作包，但不要實作。
2. 定義 Paper-only broker capability boundary；程式中不得存在 live endpoint、live adapter 或模式切換。
3. 定義 OrderIntent、broker order、fill、position、cash/NAV ledger、outbox與reconciliation各自的
   權威來源、責任及允許的狀態轉移。
4. 定義 submit timeout before/after broker accept、duplicate/out-of-order updates、partial fill、cancel、
   reject、expire、process crash/restart、stale fencing token與broker/local mismatch的fail-closed行為。
5. 定義client_order_id、idempotency、transaction/outbox邊界、REST/WebSocket reconciliation與
   pause-entries／cancel-entries／人工緊急控制語意。
6. 列出需要使用者決定的金融／營運參數，以及哪些可先用安全provisional值；逐項解釋影響。
7. 為每個工作包提出可重現的acceptance tests與明確禁止範圍，包含fake broker與fault injection；
   不得以「API call成功」當成安全驗收。
8. 說明P2哪些變更會觸發真實PostgreSQL integration、migration restore、concurrency/crash tests及
   擴大安全review。

回覆格式：先給目前狀態判斷，再解釋關鍵概念、列出待決策問題、提出P2分包方案與建議的第一包，
最後等待我確認。只有在我完全理解並明確核准後，才規劃或實作程式架構。
```

---

## 19. P1 authority hardening報告查核、修復與遠端證據

### 確認為本階段問題並已修復

1. PostgreSQL migration/schema owner與application runtime authority未分離。
2. `SECURITY DEFINER` search path與relation qualification未達可抵抗`pg_temp` shadowing的完整契約。
3. Audit/domain event接受任意JSON；event name、payload schema與requested transition未封閉綁定。
4. Persisted `JsonObject`沒有node／width／UTF-8／serialized-size budgets。
5. 缺少統一`SECURITY.md`與Risk Register lifecycle/evidence taxonomy。

修復由migration 0002、`postgres_roles.py`、typed payload registry、DB check constraints、JSON budgets、
catalog/runtime adversarial tests與ADR-016落實。application runtime現在必須是外建non-owner login；
PUBLIC沒有schema CREATE、database TEMP或protected function EXECUTE。functions固定
`pg_catalog, public, pg_temp`並schema-qualify authoritative objects。

### 是P2-entry契約、但不是現存可利用path

- Config binding：目前沒有service composition root；已固定raw mapping只存在exact-schema parser edge、
  adapter只收typed config，P2不得用generic configuration bag。
- DB DSN/credential：目前沒有長駐runtime credential composition；已固定owner DSN不得進runtime，
  runtime需exact secret ref／bounded reveal，任何DSN不得進snapshot、argv、log、telemetry、audit或exception。

兩項都在P2加入長駐process前是blocker，但本輪不虛構尚不存在的adapter/composition implementation。

### 未證實或依法延後

- 未證實Keychain必須改為persistent-reference兩段查詢：Apple contract與現行exact service/account +
  match-all 0/1/many fail-closed behavior沒有提供此必要性，因此未改native boundary。
- Native Keychain smoke是真實evidence gap，但會建立／刪除disposable item，未獲該mutation授權；沒有
  查詢現有item，fake tests也未被冒充為native evidence。
- Coverage threshold與security-static／dependency/SBOM/license/secret-scan lane屬獨立quality／
  supply-chain工作包，不是本次可重現P1 exploit；未靜默改變ADR-015兩個required jobs與成本／權限。

### 驗收與發布

- Local locked gate：Ruff format/lint、Mypy 59 source files、`440 passed, 33 deselected`。
- 真實digest-pinned PostgreSQL 16.15：`33 passed, 0 skipped`，包含migration up/down/up、catalog ACL、
  runtime正常repository path、direct DML／ALTER trigger／function replacement／TEMP denial、owner temp
  shadowing與stale fencing。
- Code commit：`e8543b69bfc6a6d2dd9a87837d9d46bb11afc406`。
- GitHub Actions run [`31891905869`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/31891905869)：
  `quality-unit`與`postgres-integration`均為`success`；遠端數字分別為`440 passed, 33 deselected`與
  `33 passed`。
- 未讀取／修改Keychain，未使用broker/model/data API、repository secret或真實credential；沒有P2
  broker/order/fill、scheduler、launchd或live path。
