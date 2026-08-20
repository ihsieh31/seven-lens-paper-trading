# Seven-Lens Paper Trading System

本專案是一套只供本人使用、只連接 Alpaca Paper Trading、以七種公開投資研究方法論作為辯論委員的美股中期持有系統。

核心原則：LLM 只能產生有來源的研究判斷；確定性的投資組合、風控與執行核心才可產生委託，而且第一版完全不存在實盤交易路徑。

## 已確定範圍

- 每個美股交易日前開盤分析選股，開盤後執行主要再平衡。
- 收盤前再次評估既有持倉與最優候選，允許第二次再平衡。
- 不做當沖；正常持有期 10–60 個交易日，除風控退出外至少持有 5 個交易日。
- 七位委員：Howard Marks、Muddy Waters Research、Aswath Damodaran、Serenity / `@aleabitoreddit`、Terry Smith / Fundsmith、Michael Mauboussin、Lyn Alden。
- 僅使用免費公開內容與免費資料 API；LLM 費用不設上限。
- 系統可無人值守，但任何資料、模型、帳務或券商狀態不明時一律停止新增風險。

## 文件入口

- [新對話／驗收工作交接](PROJECT_HANDOFF.md)
- [主企劃書](docs/MASTER_PLAN.md)
- [系統架構](docs/ARCHITECTURE.md)
- [七人蒸餾規格](docs/DISTILLATION_SPEC.md)
- [TradingAgents 評估](docs/TRADINGAGENTS_ASSESSMENT.md)
- [營運與安全](docs/OPERATIONS_AND_SAFETY.md)
- [安全政策與信任邊界](SECURITY.md)
- [開發路線圖與驗收](docs/ROADMAP_AND_ACCEPTANCE.md)
- [來源與授權策略](docs/SOURCES.md)
- [第一個開發 Prompt](docs/FIRST_IMPLEMENTATION_PROMPT.md)
- [決策紀錄](DECISIONS.md)
- [目前進度](PROGRESS.md)
- [問題日誌](ISSUES.md)
- [工作日誌](WORKLOG.md)
- [風險登錄表](RISK_REGISTER.md)

## 目前狀態

`P1-C1 — macOS Keychain secret boundary`、`P1-C2 — dependency-neutral metrics/traces`、
`P1-C3 — CI／zero-skip／clean-machine gate` 已通過獨立驗收，P1 Core Gate 已關閉並有遠端 CI 證據。`P2 — Alpaca Paper 執行安全` 於 2026-08-20 完成 final remediation，**P2 Gate Closed**；ACC-001~009 全部關閉，exact code-bearing SHA `488f170` 的 GitHub Actions [`32360443947`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/32360443947) 中 `quality-unit`／`postgres-integration` 均成功。已交付基礎包含 Paper-only adapter、exclusive new-entry linearization、durable UNKNOWN/conflicting-fill pause、cash checkpoint + full-ledger NAV、runtime baseline read-only、checksum-compatible 0008→0009，以及 typed `MarkPriceUnavailableError`（任意 `ValueError`/程式缺陷不再降級）。legacy compatibility revision 的 `created_at` 是 legacy `effective_at`／authority-effective timestamp，source-row original `created_at` 保留；不是 migration execution time。`P2-E` 真實 Alpaca Paper GET-only 驗證已執行（見 `PROGRESS.md`）；關閉 P2 不授權真實下單，真實下單仍留 `P7` supervised gate，WebSocket transport 與 control shell CLI 依 ADR-019 留 `P6/P7`。

安全邊界：structured logging 只接受經 bounded redaction 轉成的 JSON-safe 值；cycle、過深、非字串 mapping key 或序列化異常會產生不含原始 fields 的固定 fallback audit event。Tavily `AUTHORIZED_ACCOUNT_POOL` 目前固定 fail closed，直到未來存在可獨立驗證外部授權的 verifier；使用者輸入的 reference、ticket 或 evidence record 不會自行升級權限。

## 開發環境與驗證

唯一 bootstrap prerequisite 是 [`uv`](https://docs.astral.sh/uv/)；一鍵腳本會用 `uv` 取得／選擇
Python 3.13 並建立 locked environment。專案不會從 `.env` 讀取真實金鑰；`.env.example` 只列
非秘密設定名稱。腳本可從 repository 內任意 current directory 呼叫：

```bash
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
```

第一個命令執行 locked sync、lock、format、lint、mypy 與 non-integration tests；`--postgres`
再建立唯一 disposable PostgreSQL 16 container，只執行 integration tests，並在任何結束路徑核對
container identity 後清理。需要套用 formatter 時執行 `uv run ruff format .`，再重新執行驗證。
任何 broker endpoint、Tavily compliance mode、quota、UTC timestamp 或 schema 驗證失敗都必須阻止
建構或啟動，不得回退到寬鬆預設值。

`UtcTimestamp` wire format 固定為 `YYYY-MM-DDTHH:MM:SS.ffffffZ`；`SchemaVersion` 固定為三個 `0..9999` 的數字元件。

## macOS Keychain secret boundary

production secret lookup 只使用 Security.framework read-only exact generic-password query，不使用 `/usr/bin/security`，也沒有 env、argv、database 或 fake fallback。固定 naming：

- `seven-lens.paper-trading.alpaca-paper.key-id` / `primary`
- `seven-lens.paper-trading.alpaca-paper.secret-key` / `primary`
- `seven-lens.paper-trading.openai.api-key` / `primary`
- `seven-lens.paper-trading.tavily.api-key` / validated non-secret Tavily account id

查詢預設在 spawned worker 內使用 2 秒 hard timeout，且禁止 authentication UI。missing、duplicate、denied、locked、timeout、malformed 或 backend failure 全部停止，不會換來源。unit tests 只注入 fake provider/native bridge/process，絕不讀取使用者 Keychain。

`SecretValue` 只防止 plaintext 經 `str()`、`repr()`、pickle 或 structured logging 意外洩漏；它不是程序內加密容器或 OS-level isolation。未來 API client composition 必須在最窄邊界明確呼叫 `reveal_text()`，目前沒有任何 client 或 credential validation。

## P1-C2 metrics/traces boundary

application service 只透過 dependency-neutral recorder contracts 與 typed facade 記錄固定的五個
metrics、兩個 spans；沒有 OpenTelemetry、Prometheus、Sentry、exporter、backend SDK 或網路 client。
`TelemetryContext` 顯式傳遞 `RunId`、non-nil correlation UUID、canonical trace/span IDs，不使用
ambient context。metric attributes 只接受 registry 中的 bounded enum；run/trace IDs、account、job key、
URL/DSN、Authorization、payload 與 exception text 都不能成為 metric attributes。

Secret lookup 由 application decorator instrument，native Keychain bridge 不含 telemetry；job status 與
audit 仍在同一 PostgreSQL transaction，成功 metrics 只在 commit 與 UoW 正常退出後記錄。recorder
`Exception` 只增加 process-local drop count並產生固定 diagnostic，不會改變 lookup 結果、觸發 DB
commit/rollback/retry，或取代 PostgreSQL audit；`KeyboardInterrupt`、`SystemExit` 等 `BaseException`
不會被吞掉。job transition在啟動span或進入UoW前，強制audit具有run ID，且其run/correlation
identity必須與telemetry context相同；任何 mismatch以固定typed error fail closed且沒有telemetry或DB副作用。

## 本機 PostgreSQL integration

P1-B/P1-C integration 必須使用真正的 PostgreSQL 16；沒有 SQLite 或 mock fallback。使用：

```bash
./scripts/run_postgres_integration.sh
```

腳本使用 digest-pinned PostgreSQL `16.15-alpine`、明顯 fake credentials、random localhost port 與
tmpfs data directory，不建立持久 volume，也不輸出 DSN。`REQUIRE_POSTGRES_INTEGRATION=1` 時，
missing/non-PostgreSQL URL、缺 psycopg、連線失敗、server major 非 16 或任一 integration skip 均讓
session 失敗。migration integration tests 會執行完整 migration chain 的 up/down/up restore drill；任何
手動 `TEST_DATABASE_URL` 都只能指向專用 disposable database，絕不可指向共享或營運資料庫。

Migration/schema owner 與 application runtime 必須是不同 PostgreSQL roles。owner 只用於 migration、
disposable restore drill 與 `provision_runtime_role()`；長駐程式只能使用已通過
`verify_runtime_role()` 的非 owner login。runtime 沒有 schema CREATE、database TEMP、直接 lease/job
state mutation、trigger/function replacement或物件 ownership 權限；完整契約與未來 DSN composition
邊界見 [SECURITY.md](SECURITY.md)。
