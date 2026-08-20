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
- 補充（2026-08-19）：工作副本已移回 Codex；本 ADR 的排程隔離與權限邊界同樣約束 Codex Automations。

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

## ADR-017：P2-A 執行 domain 契約、封閉狀態機與 deterministic client order id

- 日期：2026-08-17
- 狀態：Accepted
- 決策：P2 第一個工作包只交付執行層資料契約與驗收 harness，不含任何網路 client：
  `execution/orders.py` 以 exact-type frozen value objects 固化 Symbol、OrderSide、
  OrderQuantity（整股）、Price/UsdAmount（Decimal 恰兩位小數、整數 cents 為唯一入口、
  禁止浮點）、PriceCollar（offset 1..500 bps，provisional）與 OrderIntent／BrokerOrder／Fill。
- 內部 order lifecycle（CREATED→…→FILLED/CANCELED，含 REJECTED/EXPIRED/UNKNOWN）與 broker
  mirror lifecycle 各自為封閉轉移映射，Python 端以 exact enum 檢查，PostgreSQL 端以
  IMMUTABLE `*_transition_is_valid` 函數與 guard trigger 獨立執行同一映射；UNKNOWN 只能經
  `client_order_id` 查詢解析，狀態機不存在任何盲目重送邊。
- `client_order_id` 固定為 `slv1-{strategy}-{trading_date}-{window}-t{target_version}-{symbol}
  -{side}`；組合一致性、唯一性與欄位格式由 DB CHECK/UNIQUE 及 Python `__post_init__` 雙邊強制。
  fills 表 append-only（trigger 禁止 UPDATE/DELETE）；order_intents/broker_orders 的 identity
  欄位不可變，僅狀態與鏡像欄位可更新。
- runtime role 擴充為：order_intents/broker_orders INSERT+UPDATE、fills 僅 INSERT；三表皆無
  DELETE 權限，`verify_runtime_role()` 的 least-privilege 集合同步擴充。
- fake broker（`execution/fake_broker.py`）是 P2 安全驗收面：per-client-order-id 一次性故障
  plan（TIMEOUT_BEFORE_ACCEPT 不留單、TIMEOUT_AFTER_ACCEPT 留單、REJECT 可重播、partial fill
  腳本）與 idempotency/conflict 檢查；真實 Alpaca Paper adapter（P2-E）僅做 read-only 驗證，
  真實下單留待 P7 supervised 階段（2026-08-17 使用者核准）。
- 理由：Idempotency、timeout 語意與帳本不可變性必須先以純本機、可 fault-inject 的契約固化，
  才能讓後續 execution engine（P2-B）與 reconciliation（P2-C）在同一不變量上開發；
  「API call 成功」不等於安全驗收。

## ADR-018：P2-B~E 執行引擎、對帳、控制平面與 Paper adapter 的安全結構

- 日期：2026-08-17
- 狀態：Accepted
- 決策（P2-B）：ExecutionEngine 只以 deterministic client_order_id 與 broker 互動；SUBMITTING
  在任何 broker 呼叫前持久化（測試以 guard broker 當場斷言）；timeout 一律進 UNKNOWN，解析
  只能查詢；唯一允許的重送是「broker 證明無此 id 且 cancel 窗口仍開」的同 id 重送；fills 以
  execution_id 去重；參數矛盾的 broker order 拒絕記錄並抛出待對帳仲裁（fail closed、零副作用）。
- 決策（P2-C）：對帳以 broker 自身 account/order/fill/position view 對本地權威表比較；
  mismatch 分類封閉（NON_PAPER_ACCOUNT…POSITION_*）；reconciliation_runs 表 append-only 且
  `mismatch_count = cardinality(mismatch_kinds)` 由 DB 保證；`Reconciler.run()` 在 MISMATCH 時
  持久化證據並自動 pause_entries（記錄 PAUSE_ENTRIES 命令）。帳本投影（cash delta + FIFO lots）
  只以本地 fills+mirrors 為輸入；oversell、未知 order、超界現金一律 fail closed。
- 決策（P2-D）：control_commands 表 append-only、control_state 單例暫停旗標（暫停必帶 reason、
  由 guard trigger 與 CHECK 強制）；resume 必須最新 reconciliation 為 CLEAN 否則 fail closed；
  flatten 需明確 "FLATTEN_PAPER" 確認字串且先暫停，賣出意圖由本地帳本推導（limit 掛 collar 下界）。
  composition root 關閉兩個 P2-entry blocker：exact-schema typed 設定邊界（application 層禁
  urllib，DSN 組合移至 infrastructure）與 runtime DB password 的 exact SecretRef（新增
  POSTGRES_RUNTIME_PASSWORD kind）＋單一 bounded reveal（RuntimeDsn 不洩漏 str/repr）。
- 決策（P2-E）：AlpacaPaperAdapter 只接受 exact Paper endpoint，經可注入 transport（測試零網路）；
  回應嚴格解析，未知欄位/狀態 fail closed；408/429/5xx 視為 outcome-unknown。依使用者決策，
  真實 endpoint 僅授權 read-only 驗證，真實下單留 P7；list_open_orders 未做 >500 分頁（10 持倉
  上限下不成立，列為已知邊界）。
- 已知邊界（後續工作包）：order 狀態轉移未逐筆寫入 P1 typed audit event registry（order 表
  guard + append-only fills + control_commands 即為可驗證軌跡；如需 event 化需擴充封閉 registry）；
  engine/reconciler 尚未接 P1-C2 telemetry instrumentation；EXPIRED 與 in-flight submit 的極端
  競態由 reconciliation（UNKNOWN_BROKER_ORDER→pause）涵蓋。
- 理由：P2 的安全對象是「broker 與本地帳本可能分歧」本身；所有歧義一律落腳到可查證的
  UNKNOWN/EXPIRED/MISMATCH 狀態與自動暫停，而不是猜測或重試。

## ADR-019：Trade update consumer、NAV valuation 與 P2 收尾邊界

- 日期：2026-08-17
- 狀態：Accepted
- 決策：新增 transport 中立的 `TradeUpdateConsumer`（`execution/trade_updates.py`）：重複
  fill 以 execution id 冪等吸收；亂序狀態事件以 broker 時間戳判定 STALE、零副作用；未知
  intent／鏡像／broker id 不符一律 UNKNOWN_ORDER 不猜測；不可表示狀態抛 typed error 交對帳
  仲裁；無變化的重播事件分類為 DUPLICATE 而非 APPLIED。broker 端外部取消（本地仍在
  ACKNOWLEDGED/PARTIALLY_FILLED）經 CANCEL_PENDING 合法路由至 CANCELED。
- 決策：帳本新增 `account_valuation`（期初現金 + fills 現金效果 + 依供給價格的持倉市值）；
  缺價格或超界 fail closed，不以零估值替代。
- 範圍聲明：WebSocket 傳輸本體（連線、重連、心跳）屬 P6/P7 runtime bring-up；P2 交付消費
  語意與冪等/亂序驗收。control shell CLI 同理延後：`SECURITY.md` 禁止 DSN 進 argv/env，
  CLI 需完整 runtime credential 路徑（Keychain + RuntimeDsn），於 launchd 前置作業一併交付；
  控制命令本身已可由程式路徑完整執行並通過真實 PostgreSQL 驗證。
- 理由：P2 的可驗收對象是「事件消費的不變量」而非傳輸層；把傳輸延後到有真實憑證的階段，
  避免在無金鑰環境偽造連線證據。

## ADR-020：Window cutoff 語意——先仲裁歧義、先取消、絕不本地過期未知取消

- 日期：2026-08-17
- 狀態：Accepted
- 決策：`expire_overdue` 不再對所有逾期 live 意圖盲目本地 EXPIRED。新的截止順序：
  (1) SUBMITTING/UNKNOWN 先以 client_order_id 查詢解析——broker 仍持有的單不得與本地終態
  分歧；(2) 無 broker 單的 CREATED/RISK_APPROVED/OUTBOX_PENDING 直接本地 EXPIRED；
  (3) ACKNOWLEDGED/PARTIALLY_FILLED/CANCEL_PENDING 逾期時先向 broker 請求取消，僅在
  mirror 結構性錯誤時本地 EXPIRED 交對帳；(4) 取消請求遭遇 transport error 時**絕不本地
  過期**——已持久化的 CANCEL_PENDING 保留，訂單可能仍成交，交 recovery 與 reconciliation。
  同一意圖在同一輪截止中只回報最終狀態一次。
- 理由：本地 EXPIRED 是「我們不再行動」的聲明，不是「broker 單不存在」的證據；與 broker
  狀態矛盾時必須以查詢與取消收斂，分歧只能由 reconciliation 顯式揭露（自動暫停）。

## ADR-021：執行安全門檻補強——pause 內嵌引擎、雙時鐘、重複訂單解析、終態對帳

- 日期：2026-08-17
- 狀態：Accepted
- 決策：獨立對抗審查（ISSUES.md OPEN-017/OPEN-018 與 ASSESSED-019）後的四項執行層決策：
  1. `pause_entries` 不再只擋 operator shell：`ExecutionEngine` 注入 `control` state source，
     `submit_from_outbox` 在 SUBMITTING 轉移與任何 broker 呼叫**之前**檢查
     `entries_paused`，違反時抛 `ExecutionPausedError`（零副作用）；RISK_EXIT（緊急離場、
     flatten）與 risk-reduction（cancel/expire/fills/resolve）一律不受 pause 影響；resume
     不需重建 engine。composition 以 `build_execution_stack(..., control=...)` 注入同一個
     control repository（reconciliation mismatch 自動暫停立即生效）。
  2. `broker_orders` 雙時鐘：新增 `broker_updated_at`（broker 自身時間，trigger 保證只能
     前進）與既有 DB 本地 `updated_at`（`statement_timestamp()`，稽核用）分離；domain
     `BrokerOrder.updated_at` 一律為 broker 時間。`TradeUpdateConsumer` 的 STALE 基準改為
     broker 時間，clock skew／事件回放不再把合法 broker 事件誤判 STALE 丟棄
     （migration 0006）。
  3. Alpaca 422/400（`client order id already exists`）不再一律分類為 REJECTED：
     submit 對重複 id 改以 `GET /v2/orders:by_client_order_id` 查詢解析，參數一致回
     `SubmitAccepted`（冪等 recovery），參數矛盾或查不到則維持
     `ORDER_PARAMETERS_REJECTED`；follow-up GET 非 2xx 亦fail-soft回 rejection。
  4. Reconciler 補齊兩個盲點：terminal intent（FILLED/CANCELED/EXPIRED/REJECTED）卻有
     broker 仍開單 → 新增 `MismatchKind.INTENT_STATUS_MISMATCH`；terminal mirror 對照
     broker 開單 → `STATUS_MISMATCH`（原實作只比較 open mirrors）。
- 理由：pause 語意在 operator 層維持不變，但 outbox worker／任何引擎呼叫方都是同一入口，
  必須在引擎層 fail closed 才不會出現「暫停後仍建立新 exposure」；時鐘混用會靜默丟失合法
  broker 事件（STALE），是稽核與帳務正確性問題；重複 client id 是崩潰恢復的既有冪等契約，
  422 分類為 REJECTED 會破壞 recovery；reconciliation 只比較 open mirrors 會漏報終態分歧，
  而分歧正是 pause/reconciliation 存在的理由。
- 證據：`tests/test_execution_pause_remediation.py`、`tests/integration/test_broker_order
  _timestamps_postgres.py`（均先 red 後 green）、`tests/test_alpaca_paper_adapter.py`
  `TestDuplicateClientOrderId`、`tests/test_reconciliation_and_ledger.py`
  `TestTerminalIntentWithOpenBrokerOrder`、`tests/test_p1_c3_ci.py`
  `test_postgres_integration_job_excludes_live_marker`；`verify_p1.sh --postgres`、
  `run_postgres_integration.sh` 全綠；ci.yml 與本機 script 的 postgres job 一律
  `-m "integration and not live"`（live 只能經 P2-E CLI 手動執行）。

## ADR-022：P2 second remediation——broker 真值未知即不宣告終態、flatten 六步、資產閘與詳情對帳

- 日期：2026-08-18
- 狀態：Accepted（implementation completed, pending independent re-acceptance）
- 決策：第二輪 remediation 的核心不變量——「broker truth 未知時不得自行宣告終態」——
  落為以下六組決策（migration 0007 為其持久化載體）：
  1. **UNKNOWN 語意**：deadline 後 `GET` 查無單的 SUBMITTING 不再轉 EXPIRED，改為
     UNKNOWN（reconciliation/recovery 才收斂）；只有從未到過 broker 的
     CREATED/RISK_APPROVED/OUTBOX_PENDING 可本地 EXPIRED。`expire_overdue` 取消路徑出現
     transport/config 錯誤時保留 CANCEL_PENDING，絕不本地 EXPIRED。
  2. **broker watermark 保守化**：0006 以本地 `updated_at` 回填的 broker_updated_at 是
     過高 watermark 且不可復原 → 0007 全部清成 NULL（unknown），domain 讀 NULL 時以
     submitted_at 作 lower bound：永不把合法 broker 事件誤判 STALE。回放同值
     （status+filled_quantity 相等）→ DUPLICATE；同 timestamp 不同值 → 明確衝突錯誤。
  3. **broker_orders SQL guard 完整化**：0006 的 trigger 允許非法轉移與 filled_quantity
     倒退 → 0007 以「僅當兩端都非 NULL 才禁止倒退」取代單調 guard；新增
     filled_quantity 永不倒退、FILLED 必需恰等 quantity、INSERT 側身份欄位 immutable 與
     FILLED exact；status CHECK 擴為完整 Alpaca 15 態；`REVIEW_REQUIRED` 進入 intent
     狀態機，六個 review broker 狀態一律收斂至 REVIEW_REQUIRED（永不自行猜終態）。
  4. **flatten 六步**：確認 → 已暫停 → resolve SUBMITTING/UNKNOWN → 取消
     ACK/PARTIALLY/CANCEL_PENDING → refresh 收斂 → **position view vs 本地 ledger
     必須逐符號一致，否則 abort（零新單）** → 最後才以價格 seam（`FlattenPriceProvider`）
     估價、以同一交易內原子遞增的 durable `flatten_generation` 作 target_version 下單。
     重複 flatten 的 client order id 永不碰撞。
  5. **資產閘**：`submit_from_outbox` 在任何狀態轉移前以 broker 自身 asset view
     （`get_asset`）驗證 symbol 已知且可交易，未知/不可交易一律 fail-closed
     （含 RISK_EXIT；flatten 在下單前對全部部位預檢）。
  6. **詳情對帳**：`reconciliation_mismatches` 明細表逐條保存每筆 mismatch 的
     kind+detail（ordinal 穩定、append-only）；closed-history pass 以
     `list_recent_orders(since=前一輪 observed_at)` 重掃已關閉 broker 單，
     補 UNKNOWN_BROKER_ORDER/STATUS_MISMATCH/MISSING_LOCAL_FILL 三類漏報；
     `mismatch_kinds` CHECK 納入 `INTENT_STATUS_MISMATCH`；runtime role 僅增
     INSERT/SELECT，仍無 DDL。
- 理由：終態是「broker 已確認」的宣稱，本地以 clock 到期代替 broker 確認會製造
  不可收斂的分歧（尤其 cancel 未決時）；watermark 過高會靜默丟失合法 broker 事件；
  flatten 在未暫停/未取消/未對帳前下賣單會在緊急時擴大風險；每次 flatten 用固定
  target_version 使重複 flatten 撞 id；對帳只存 kind 不存 detail 使證據不可稽核。
- 證據：`tests/test_execution_engine.py`（TestPendingCancelCutoff、TestBrokerTerminal-
  Recovery、TestDuplicateDelayedVisibility、TestAssetGate）、`tests/test_control_plane.py`
  （flatten aborts/disagreement/price seam/generation 三連）、`tests/test_reconciliation_
  and_ledger.py`（closed-history 四案）、`tests/test_session.py`、`tests/fakes/orders.py`
  invariants；`scripts/run_postgres_integration.sh` 66 passed、`verify_p1.sh --postgres`
  66 passed、non-integration 621 passed、ruff/mypy 92 檔全綠。

## ADR-023：P2 獨立驗收修復與 Codex 回遷

- 日期：2026-08-19
- 狀態：Accepted implementation；重新 Open 的 gate 已由 ADR-024 驗收後 Closed
- 決策：pause 必須同時約束初次提交與 SUBMITTING/UNKNOWN recovery；flatten 只有在所有
  既有 broker order 均收斂後才能建立 sell intents；asset gate 除 known/tradable 外必須是
  `US_EQUITY`；`REVIEW_REQUIRED` 永遠使 reconciliation 為 MISMATCH。Alpaca orders 依官方
  `after_order_id` 分頁並按 broker `updated_at` 本地過濾；fills 使用 `page_token`、
  `page_size`、`direction=asc` 與 `/activities/FILL`。
- 證據：新增 6 個回歸案例；locked gate 627 passed / 74 deselected；真實 PostgreSQL 16
  66 passed / 8 deselected；Ruff、mypy、lock 全綠。未讀 Keychain、未執行 live test、未
  commit/push。

## ADR-024：P2 全面再驗收——提交線性化、部分失敗稽核與 broker snapshot 合併

- 日期：2026-08-19
- 狀態：Accepted；P2 Gate Closed
- 決策：非 risk-exit submission 必須在 `control_state` 的 PostgreSQL shared row lock 下讀取
  pause 並跨越 broker submit，讓 concurrent pause UPDATE 只能在線性化提交完成後生效；控制
  多筆 cancel/flatten 的部分失敗必須逐筆保留狀態、寫 `applied_at=NULL` 的 bounded
  `PARTIAL_FAILURE` command、維持 pause 並拋 typed error。
- reconciliation 的任何 `BrokerTransportError` 必須持久化 `BROKER_QUERY_FAILURE` 並自動
  pause；open snapshot 與 recent history 以 broker `updated_at` 合併，較舊或同時間同狀態才
  去重，同時間不同狀態必須繼續比較並產生 mismatch。Alpaca 單一資產使用
  `/v2/assets/{symbol}`；open orders、fills 均 bounded pagination，fill 必須匹配 requested
  broker order id，未知 broker status 維持 fail-closed。
- 證據：Luna 三輪對抗重現最終無 validated blocker；Ruff/mypy 92 檔、637 non-integration、
  69 PostgreSQL 16 integration、1 live GET-only acceptance 全綠。P2 關門不授權真實下單、
  commit/push 或跳過 P3/P6/P7 gate。

## ADR-025：P2-CUR-001~006 Remediation——reconciliation detail、ledger invariant、late fill、FIFO、UNKNOWN 全域閘與帳務對帳

- 日期：2026-08-20
- 狀態：Accepted；P2 Gate 再次 Closed（P2-CUR 重驗）
- 決策：
  1. `reconciliation_mismatches` 的 evidence detail 由 `mismatch_kinds` 陣列恢復為 child table 真值：`latest()` 必讀 `reconciliation_mismatches` 並驗證 `mismatch_count == len(children)`、`kinds` 順序一致、`CLEAN↔0`/`MISMATCH↔≥1`，否則拋 `PersistenceInvariantError`（`src/seven_lens/infrastructure/postgres.py`；migration 0008）。
  2. `LedgerInvariantError`（duplicate execution、unknown order、oversell、cash 超界）於 `Reconciler.run` 轉 durable `LOCAL_LEDGER_INVARIANT` mismatch，自動 pause 並寫 `PAUSE_ENTRIES` 命令；不以 broad `except Exception` 捕捉（migration 0008 擴 kinds）。
  3. `TradeUpdateConsumer._apply_fill` 亂序完整修復：`filled_quantity = max(mirror, local_total)` 不倒退、`broker_updated_at = max(old, fill.occurred_at)` 不回退、已 terminal/review 不回退、`PENDING_CANCEL` 中可收 fills、衝突時保留 fill 並拋 `TradeUpdateError` 交 reconciliation（`src/seven_lens/execution/trade_updates.py`；`FakeOrderRepository` 同步 `filled_quantity`/`broker_updated_at` 單調檢查）。
  4. `project_ledger` 以 `(occurred_at, execution_id)` 為 canonical 回放序，與 DB arrival order 解耦；`ordered_lots` 改以 `opened_at.value` 排序（`src/seven_lens/execution/ledger.py`）。
  5. `UNKNOWN` 全域門檻：`ExecutionEngine._submit_while_guarded` 於 `BrokerTransportError`/`BrokerConflictError` → `UNKNOWN` 後持久化 `entries_paused` + `PAUSE_ENTRIES`（`src/seven_lens/application/execution_service.py` 內 `RLock` 防 `FakeControlRepository` deadlock）；`_entry_submission_guard` 在 `FOR SHARE` 內同時檢查 `entries_paused` 與 `UNKNOWN`/`REVIEW_REQUIRED` 未解；`Reconciler.collect` 對兩者產生 `INTENT_STATUS_MISMATCH` 使 CLEAN 不可達；`ControlPlane.resume_entries` 做 defense-in-depth 阻擋。
  6. 帳務對帳：`PaperAccount.buying_power` 嚴格解析（`src/seven_lens/infrastructure/alpaca_paper.py`）、`account_baselines` 權威基線表（migration 0008，`guard_account_baseline_write` immutable `account_id`）、`AccountReconciliationPolicy`（expected_account_id + cash/nav tolerance）與 `ReconciliationMarkPriceProvider` seam；`Reconciler.collect` 比較 `ACCOUNT_ID`/`BUYING_POWER`/`CASH`（`opening_cash + cash_delta`）/`NAV`（`account_valuation` + marks），缺 baseline/缺 price/缺 provider 皆為 `ACCOUNT_RECONCILIATION_UNAVAILABLE` 而非 CLEAN；新增 6 種 closed mismatch kinds。
- 範圍聲明：不實作 WS transport、`control CLI` shell、真實 Paper POST/DELETE、`P3` distillation/`P4` holding/`P5` backtest 等；`buying_power` 僅做嚴格快照與 presence 檢查，不偽造 expected buying power 公式（見 P2-CUR-006 買斷規則與本 ADR）。
- 證據：Ruff/mypy 92 檔全綠；non-integration 637 passed / 77 deselected（含新 `P2-CUR` 對抗）；PostgreSQL 16 integration 69 passed / 8 deselected（含 `latest` detail roundtrip/ordinal/corruption、`LOCAL_LEDGER_INVARIANT` pause、`account_baselines` 權限與 `0008 up/down/up`）；`verify_p1.sh --postgres` 全綠。R-24 重標 `Mitigated`，CLOSED-021 已 superseded。

## ADR-026：P2 最終修復——entry 互斥、cash checkpoint 與 immutable migration compatibility

- 日期：2026-08-20
- 狀態：Accepted implementation；P2 Gate Reopened，等待完整 acceptance matrix。
- 決策：非 `RISK_EXIT` 新單以 `control_state FOR UPDATE` 互斥跨越 broker-submit 臨界區；第一個提交在 timeout 後先持久化 `UNKNOWN`，才允許下一個新單取得鎖並看見 unresolved gate。`RISK_EXIT` 不持此新 exposure 鎖。
- 帳務：baseline revision 是 **cash checkpoint**，expected cash 為 checkpoint cash 加 post-cutoff cash delta；current NAV 的 lots/positions 永遠由 full fill ledger 取得。post-cutoff sell 可消耗 pre-cutoff lot，因此 cash delta 不以獨立 post-cutoff FIFO replay計算。genesis 只能在 fill ledger 空時建立，並以 PostgreSQL `fills` table lock 與第一筆 fill 序列化；有 fill 後 revision 必須帶真實 `(occurred_at, execution_id)` cutoff。
- Authority：runtime role 對 `account_baselines` / `account_baseline_revisions` 僅有 SELECT；建立 genesis/revision 是 migration owner/operator capability，不向一般 runtime 暴露 arbitrary INSERT。
- Migration：0009 已在 main 並由 checksummed runner 驗證，不能改寫。version-8 upgrade 若有效 baseline 有 `effective_at > created_at`，runner 在同一 migration transaction 暫時將 source created_at 對齊 effective_at 以供不變的 0009 建 revision，接著在 0009 trigger 暫停期間還原 source-row original created_at。新 revision 的 `created_at` 對該 legacy row 表示 migration-time authority timestamp；source-row original provenance 保留。既有 version-9 database 不會重新執行此 compat path。
- Late fill：fill fact 先 commit；derived mirror/intent projection 失敗先 rollback，再以同一 UoW 持久化 `entries_paused` 與 `PAUSE_ENTRIES` command，任何 safety persistence failure 以 typed error 可觀測地向外傳播。
