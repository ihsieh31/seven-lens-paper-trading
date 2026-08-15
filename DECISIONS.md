# Architecture Decision Log

此檔只記錄已作成、會影響後續實作的決策。每次變更不得覆寫歷史，必須新增決策並標示取代關係。

## ADR-001：全新專案，不沿用舊架構

- 日期：2026-08-14
- 狀態：Accepted
- 決策：在本 repository 從零定義系統；舊專案只能作為經驗，不匯入程式或架構。
- 理由：避免把未經重新驗證的假設、耦合與風險帶入新系統。

## ADR-002：七人委員是唯一策略

- 日期：2026-08-14
- 狀態：Accepted
- 決策：第一版不維護其他獨立策略或策略帳戶；七種方法論共同形成一個目標投資組合。
- 理由：控制研究、評估與歸因複雜度。

## ADR-003：蒸餾方法論，不模仿人格

- 日期：2026-08-14
- 狀態：Accepted
- 決策：只萃取公開可引用的分析框架、證據偏好、失效條件和常見盲點；不得聲稱本人背書，不複製語氣，不推定未公開觀點。

## ADR-004：Paper-only 且程式上不存在 live path

- 日期：2026-08-14
- 狀態：Accepted
- 決策：只接受 Alpaca Paper 網域與 Paper credentials；第一版不提供 `live=false/true` 切換，而是完全沒有 live adapter。
- 理由：設定錯誤不能把測試系統變成實盤系統。

## ADR-005：LLM 不得擁有下單權

- 日期：2026-08-14
- 狀態：Accepted
- 決策：LLM 只輸出符合 schema 的研究評估。確定性 Portfolio、Risk、Execution 模組才可產生與提交委託；研究程序拿不到券商憑證。

## ADR-006：TradingAgents 只作設計參考與隔離式 AnalysisProvider

- 日期：2026-08-14
- 狀態：Accepted
- 決策：採用其多角色辯論概念，不把其 Portfolio Manager 或自由文字交易訊號直接接上券商，也不直接 fork 成交易核心。

## ADR-007：零付費資料是硬限制

- 日期：2026-08-14
- 狀態：Accepted
- 決策：除 LLM 外不啟用任何付費資料、X Developer API 或付費研究訂閱。Tavily 必須設定月／日額度與禁止 PAYGO；資料不足即棄權或不交易。

## ADR-008：Codex Automations 不負責盤中關鍵排程

- 日期：2026-08-14
- 狀態：Accepted
- 決策：Codex 排程用於程式維護、測試、文件與離線蒸餾；交易時鐘由常駐 Python 服務、Alpaca 市場日曆與資料庫 job lease 控制。
- 理由：桌面自動化依賴電腦和應用程式運行，不應作為券商工作流的唯一時鐘。

## ADR-009：長倉、無槓桿、正常時段、整股

- 日期：2026-08-14
- 狀態：Provisional
- 決策：第一版只做美國上市普通股與未槓桿 ETF；不做空、不做選擇權、加密貨幣、OTC、ETN、槓桿／反向 ETF、盤前盤後交易或碎股。
- 重審條件：完成至少 60 個交易日 Paper gate 後，另立 ADR。

## ADR-010：本機模組化單體

- 日期：2026-08-14
- 狀態：Provisional
- 決策：以 Python 模組化單體、PostgreSQL 權威帳本、DuckDB/Parquet 研究快照、macOS Keychain、launchd 起步。
- 理由：單人免費營運下，比微服務更容易保證交易一致性、測試與恢復。

## ADR-011：Tavily 七帳號容量採合規條件式啟用

- 日期：2026-08-14
- 狀態：Accepted
- 決策：使用者宣告持有 7 個 Tavily 帳號，理論免費容量合計 7,000 credits／月。系統可以實作多帳號 `TavilyAccountPool`，但只有在 Tavily 書面確認或各帳號 Order Form／後台明確允許同一 Customer 彙總使用後，才能將月硬上限設為 7,000。
- 未確認狀態：只啟用一個帳號，月硬上限 1,000；其他 key 保持 disabled，不能自動輪替以規避方案限制。
- 已確認狀態：7 個帳號各自有 1,000 credits ledger；全域月硬上限 7,000、runtime 預算 5,600、research/incident reserve 1,400、一般交易日 soft cap 250。
- 安全要求：不自動建立帳號、不共享帳號、不隱藏 Customer identity、不以多 key 突破單帳號 RPM；每個 key 獨立追蹤 quota/reset/429，所有 secret 存 Keychain。
- 理由：提高可用資料容量，同時遵守 Tavily 對單一 Account、額外 Account、usage limitations 與禁止超限的現行條款。

## ADR-012：PostgreSQL driver、migration 與 transaction 策略

- 日期：2026-08-14
- 狀態：Accepted
- 決策：權威狀態 adapter 使用 psycopg 3 同步介面與參數化 SQL；domain/application 只依賴 repository 與 unit-of-work ports，不 import psycopg、SQLAlchemy、Alembic 或其他 migration framework。
- Migration：採 repository 內 checksummed、成對的 `0001_initial_up.sql`／`0001_initial_down.sql` 與小型 runner。乾淨資料庫在單一 transaction 建立；重複執行先核對 version/checksum；down 只允許在 disposable／restore-drill database 使用，營運恢復以已驗證備份還原後再向前 migration。
- Transaction：unit of work 預設 rollback，只有明確 `commit()` 才提交。狀態變更與 audit append 由 application service 放入同一 transaction；任一寫入失敗會整體 rollback。
- 時鐘與併發：`recorded_at`、lease acquire/renew/release/expiry 全部以 PostgreSQL UTC 時鐘判斷。本機時間不參與 lease 權威決策；每次接管遞增 fencing token，受保護 job state write 必須同時符合目前 owner、token 與未過期條件。
- Append-only：domain/audit event 使用資料庫 trigger 禁止 UPDATE/DELETE；audit payload 另有應用層與資料庫層 secret-bearing material 拒絕。
- 理由：P1-B 需要直接驗證 PostgreSQL 的 transaction、trigger、JSONB、locking 與 clock semantics；薄型 direct-SQL adapter 比 ORM abstraction 更容易審計這些高風險不變量。

## ADR-013：macOS Keychain secret boundary

- 日期：2026-08-15
- 狀態：Accepted；P1-C1 已通過獨立驗收
- 原生介面：使用 `pyobjc-framework-Security` 封裝 Security.framework `SecItemCopyMatching`，不使用 `/usr/bin/security`、shell 或 subprocess。query 僅限 generic-password exact service/account read、return data、match all，並以 `kSecUseAuthenticationUIFail` 禁止 authentication UI。
- 相依選擇：只加入 macOS conditional `pyobjc-framework-Security`。Apple 的較新建議是 `kSecUseAuthenticationContext` 搭配 `LAContext.interactionNotAllowed`，但在 macOS 26.5.1／PyObjC 12.2.2 的最小無查詢 reproduction 中 setter 後 getter 仍為 false，因此本階段不把未能證明生效的 LocalAuthentication 行為當安全控制。`kSecUseAuthenticationUIFail` 雖已 deprecated，但目前 module/constants 可匯入且語意直接；每次 OS／PyObjC 升級需重新驗證。
- Naming：Alpaca Paper key id、Alpaca Paper secret key、OpenAI API key 各自使用固定 service 與 `primary` account；Tavily 使用固定 service 與已驗證、非秘密 account id。caller 不能傳任意 service/account，也不能 list、prefix、wildcard、write、update、delete 或 export。
- Capability：application `SecretProvider` 只暴露 exact typed lookup；`ScopedSecretProvider` 在 backend 前強制 allowlist。execution 與 research/LLM scopes 分離，但不宣稱是 OS sandbox。
- Failure：not found、ambiguous、denied、locked、timeout、malformed、backend unavailable 與 capability denied 使用固定 bounded exception，全部 fail closed且不 fallback。required bundle 只有全部成功才回傳。
- Timeout：每次 native lookup 由 spawned short-lived worker 執行，預設 hard timeout 2 秒；timeout 後 terminate/join，必要時 kill/join，最後關閉 IPC/process resources。secret bytes 只經 IPC 回父程序建立 `SecretValue`，不進 argv、env、stdout/stderr、temp file 或 log；不宣稱 IPC/Python memory 加密。
- Value boundary：`SecretValue` 阻止意外 `str/repr/pickle/structured logging` 洩漏並提供明確 `reveal_text()` composition point，但不是 process-memory encryption 或強制隔離。
- Secret source：沒有 `.env`、argv、database、second provider 或 production fake fallback；tests 只用 fake/in-memory providers，且不查詢真實 Keychain 或驗證真實 credential。

## ADR-014：Dependency-neutral telemetry 與 explicit context propagation

- 日期：2026-08-15
- 狀態：Accepted；P1-C2 已通過獨立驗收
- Ports：application recorder contracts只接受 typed `MetricPoint`／`SpanStart`／`SpanEnd`；正式 facade 只暴露 secret lookup與 job transition 操作，不接受 caller 自訂 instrument name 或任意 attributes。application/domain 不依賴 OpenTelemetry、Prometheus、Sentry、exporter/backend SDK、network client、logging implementation或 PostgreSQL。
- Registry：P1-C2 固定五個 metrics與兩個 spans；name、unit、attribute keys與 enum values皆封閉。每筆最多4 attributes、正式 instrument最多2；value最多64字元，每 instrument最多64 active series。run/correlation/trace/span ID、secret/account/job/symbol、URL/DSN/Authorization、payload與 exception material禁止成為 metric attributes；span error只保留 bounded `error.code`。
- Context：`TelemetryContext` immutable 且由 application service顯式接收；child只更換 span ID並保存 parent，不使用 ambient `contextvars`。只有經驗證 context可注入 structured log/span；startup/config log可沒有 context。
- Failure semantics：recorder普通 `Exception`不離開 fail-safe facade，只增加 process-local drop count並產生固定、無 exception detail diagnostic；diagnostic path不再次呼叫 telemetry backend。`KeyboardInterrupt`、`SystemExit`等 `BaseException`不吞。時間只用 injectable monotonic clock。
- Transaction關係：telemetry不是PostgreSQL audit。Secret lookup保留原成功值或 typed failure；job transition在span、UoW及repository之前驗證audit具有run ID，且audit與telemetry context的run/correlation identity完全一致，否則以固定typed error且零副作用fail closed。job成功 telemetry只在 commit及UoW正常退出後記錄，stale lease／audit／DB failure在rollback後記錄 bounded outcome並重拋原錯誤。telemetry failure不觸發 commit、rollback或retry，audit failure仍必須 rollback state mutation。
- 相依決策：P1-C2不引入 OpenTelemetry或任何 exporter/backend；未來 adapter只能在 infrastructure/composition boundary實作既有 ports，且需另行驗收。

## ADR-015：兩段式 Ubuntu CI、zero-skip PostgreSQL 與 clean-machine bootstrap

- 日期：2026-08-15
- 狀態：Accepted；P1-C3 與遠端 CI 已通過獨立驗收，P1 Core Gate 已關閉
- Jobs：GitHub Actions 只建立 `quality-unit` 與 `postgres-integration` 兩個 Ubuntu 24.04 jobs；
  workflow permission 僅 `contents: read`，checkout 禁用 credential persistence。PR 同 ref 的新 run
  取消舊 run；default branch 已開始的 gate 不因新 run 取消。
- Pinning：`actions/checkout`、`actions/setup-python`、`astral-sh/setup-uv` 固定 reviewed release 的
  完整 commit SHA；uv 固定版本。PostgreSQL 固定 exact `16.15-alpine` tag 加 OCI index digest。
  dependency cache 只使用 uv download/build cache，不保存 `.venv`、database 或 secret material。
- Zero-secret／zero-skip：CI 只使用 job-local fake DB credentials，不使用 repository secrets、OIDC、
  deploy token、Keychain 或 `.env`。required integration mode在 test collection 前驗證 PostgreSQL URL、
  psycopg、連線與 major 16；任何 integration skip 令 session 失敗。
- Clean machine：`uv` 是唯一 bootstrap prerequisite，負責選擇／取得 Python 3.13 與 locked sync。
  PostgreSQL script 使用 random localhost port、tmpfs 與 exact container identity cleanup，不使用持久
  volume 或廣泛 prune。
- 成本：不自動建立 GitHub-hosted macOS job；Keychain native boundary 目前由本機 fake-only contract
  tests 覆蓋，避免非 LLM hosted runner 成本。未來若要加入 macOS CI 必須另行核准成本與測試範圍。
- Gate 證據：公開且獨立的 `ihsieh31/seven-lens-paper-trading` repository 已建立；GitHub Actions
  run `31868962828` 在 commit `4e795ff1dc6d5b6bc51d4bd0e55149fda3e4cc61` 上讓 `quality-unit` 與
  `postgres-integration` 均成功，後者使用 PostgreSQL 16.15 且為 `19 passed, 0 skipped`。P1 Core
  Gate 因此關閉；此決策不授權 P2 或任何交易能力。

## ADR-016：P1 權威邊界補強與 P2 composition 前置契約

- 日期：2026-08-15
- 狀態：Accepted
- PostgreSQL authority：migration/schema owner 與 runtime login 必須分離。owner 只執行 checksummed
  migration、disposable restore drill 與 runtime role provisioning；長駐 process 不得取得 owner DSN。
  runtime role 必須由 operator 外部建立，再以 `provision_runtime_role()` 配置 bounded grants，並以
  `verify_runtime_role()` 證明非 owner、無 elevated flags／owner membership／schema CREATE／database
  TEMP／直接 authoritative state mutation或物件 ownership。任一 drift 都 fail closed。
- Privileged SQL：所有 lease/status `SECURITY DEFINER` functions 固定 `search_path = pg_catalog,
  public, pg_temp`，authoritative relations／row types完整 schema qualification；`PUBLIC`不得擁有
  protected function EXECUTE、public schema CREATE或database TEMPORARY。這些是 migration catalog
  acceptance，不只依 source string。
- Ledger payload：domain/audit event 改採封閉 typed payload registry，event type由payload決定；application
  service在 telemetry/UoW前綁定 requested transition，PostgreSQL check constraint獨立執行同一 allowlist。
  arbitrary raw JSON、exception、LLM/web content與 evidence不得直接寫入權威 ledger。
- JSON budgets：所有 persisted `JsonObject` 固定限制depth、total nodes、object/list width、key/string
  UTF-8 bytes與final canonical serialized bytes；錯誤訊息固定且不回顯payload。未來raw evidence需使用
  另行設計的content-addressed boundary，不得藉由放寬ledger budget處理。
- P2 composition：目前尚無service composition root或runtime DB credential source。P2在加入長駐process
  前，必須只把raw config留在exact-schema parser edge，adapter只收typed config；另定義runtime DB
  exact secret ref與最窄plaintext reveal點。owner/runtime DSN禁止進config snapshot、argv、log、telemetry、
  audit或exception。
- 驗收邊界：macOS Keychain現行exact service/account + `kSecMatchLimitAll`用於偵測ambiguous result；沒有
  Apple文件或native reproduction證明必須改成兩段persistent-ref查詢，因此不把該建議當成已證實漏洞。
  fake contract tests也不冒充native smoke；任何建立／刪除disposable Keychain item的smoke需另行授權。
  coverage threshold與新增security-static CI job屬後續quality/supply-chain gate，不是本次已證實P1 exploit，
  不得在未定義成本、工具、false-positive policy與required-check migration前靜默加入。
- 理由：P1建立的資料庫、audit與secret邊界會直接承載P2；在加入broker能力前補齊可由真實PostgreSQL
  證明的authority invariants，同時避免把未證實或屬後續composition/quality階段的要求誤報為現存漏洞。
