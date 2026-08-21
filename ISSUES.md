# Issue Log

此檔記錄具體問題、阻塞和需要驗證的假設；關閉時保留原因與證據位置。

## DEFERRED-001：Future Analyst Plugin 七人公開語料完整性不均

- 嚴重度：High
- 狀態：Deferred（2026-08-21；七人蒸餾已移出 P3 主線）
- 問題：沒有 X Developer API 或付費訂閱，無法保證完整歷史貼文、刪文或即時性。
- 影響：蒸餾可能有選擇偏差；runtime 不能假定已看完某人的所有公開觀點。
- 處置：若插件重啟，保存 coverage window、query、URL、擷取時間和缺口；plugin 可 `ABSTAIN`；交易流程不依賴即時 X。
- 重啟條件：使用者核准可測試的蒸餾方法與獨立 Future Analyst Plugin 工作包；否則不讀取或審查 corpus。
- 關閉條件：每個啟用 plugin 達到保留規格的來源、rights、held-out、ablation 與 incremental-value 門檻。

## OPEN-002：免費來源的授權與再散布邊界

- 嚴重度：High
- 狀態：Open
- 問題：公開可讀不等於可大量複製或再散布；部分 GitHub 語料庫沒有明確 LICENSE。
- 處置：本機個人研究僅保存必要摘錄、URL 與 hash；不提交大批第三方全文；逐來源記錄授權與 robots/terms 狀態。
- 關閉條件：SourceManifest schema、保留政策與 repository exclusion tests 完成。

## OPEN-003：免費行情與基本面資料品質

- 嚴重度：High
- 狀態：Open
- 問題：Alpaca Paper 預設 IEX 覆蓋與免費 API 不能完全代表全市場成交和機構級基本面。
- 處置：資料品質旗標、價格交叉檢查、流動性緩衝、成交模型 haircut；缺資料不交易。
- 關閉條件：資料供應商矩陣及 normal/boundary/stale/outage 測試通過。

## OPEN-004：無人值守依賴單台 Mac

- 嚴重度：High
- 狀態：Open
- 問題：斷電、睡眠、網路或 launchd 失敗會錯過交易窗口。
- 處置：禁止睡眠、啟動自我檢查、missed-window = NO_TRADE、重啟 reconciliation、免費外部 heartbeat 告警。
- 關閉條件：故障注入演練與重啟恢復報告通過。

## OPEN-005：歷史回測可能有分析／來源前視偏差

- 嚴重度：Critical
- 狀態：Open
- 問題：用今天抓取的新聞、社群、基本面、prompt/model 行為或未來才形成的 plugin/doctrine 回放更早日期，會把未來資訊帶進回測。
- 處置：每個 source fragment 必須有 `published_at` 和 `available_at`；as-of retrieval；不足時只做 forward shadow。
- 關閉條件：time-travel tests 能證明未來內容不可被歷史 run 讀取。

## OPEN-006：告警通道尚未選定

- 嚴重度：Medium
- 狀態：Open
- 問題：Telegram、Discord webhook 或 email 尚未配置。
- 處置：先做可插拔 AlertPort；實作階段由使用者選一個免費通道。
- 關閉條件：真實端到端 critical alert 演練成功。

## OPEN-007：Tavily 七帳號彙總使用權尚未證實

- 嚴重度：High
- 狀態：Open
- 問題：使用者持有 7 個 Tavily 帳號，但現行 Platform Terms 表示每個 Order Form 原則上只提供單一 Account，額外 Account 可能需要個別 Order Form／費用，且不得超越 Customer limitations。
- 影響：未確認前不能把七個免費帳號自動輪替視為合法的 7,000 credits 池。
- 處置：向 Tavily support 取得書面確認，或保存後台／Order Form 明確允許同一 Customer 彙總七帳號的證據；程式預設 `SINGLE_ACCOUNT_UNVERIFIED`。P1-A 已新增 immutable evidence-record schema，但本地 record／reference 不構成授權；外部 verifier 尚不存在時 `AUTHORIZED_ACCOUNT_POOL` 無條件 fail closed。
- 關閉條件：建立可信任的外部驗證流程、授權證據保存、目前帳號集合綁定、ADR 更新與 `AUTHORIZED_ACCOUNT_POOL` 驗收全部通過。

## OPEN-024：TradingAgents 分析語意相容與隔離尚未實作驗證

- 嚴重度：High
- 狀態：Open（P3 blocker）
- 問題：P3-A 已建立 versioned strict contracts、固定 upstream/license inventory 與 contract adversarial/source-invariant tests，並於 2026-08-21 通過獨立重新驗證（remediation-R1 Accepted，Gate Closed）；semantic-parity、provider isolation 與 point-in-time record/replay 仍屬 P3-B~F 未完成範圍。
- 影響：若直接沿用上游 free-text fallback、live data、單檔 portfolio view 或可改寫 memory，可能造成不可重播決策、future leakage，或讓語言模型越過 deterministic Risk authority。
- 處置：P3-A 已通過獨立重新驗收並關閉（Gate Closed，證據見 PROGRESS／WORKLOG 2026-08-21）；後續 P3-B~F 再完成 evidence/schema verifier、semantic parity、隔離 provider、memory lineage、record/replay 與模型 adversarial evals。
- 關閉條件：P3 Definition of Done 全部有 source/test evidence，且 `INVALID/ABSTAIN` 無法進 P4、Portfolio Manager無 broker/order/ledger capability，第二次 Risk rejection 必為 `NO_TRADE`。

## OPEN-025：Agnes／OpenCode 模型能力、隱私與輸出穩定性尚未實測

- 嚴重度：High
- 狀態：Open（P3 blocker）
- 問題：公開文件不能保證每個模型都接受同名 `thinking/effort=max` 參數；Agnes Chat Completions 與 Muse Responses 類型不同，Muse Spark Contributor 不是 ZDR 且資料可用於訓練。
- 處置：capability-aware adapters 記錄 requested/effective reasoning；Analysts 用 Agnes 2.5／2.0 failover，深度角色用 Muse／Agnes 2.5 failover；每角色只備援一次。portfolio input 去識別化，使用者已接受 Muse data policy。
- 關閉條件：真實但不含 broker credential 的 contract smoke、schema/timeout/failover/privacy audit 與 held-out eval 通過；任何不支援參數不被假裝成功。

## OPEN-026：緊急事件驗證與 bounded memory 尚未實作

- 嚴重度：High
- 狀態：Open（P3 blocker）
- 問題：未驗證的壞價、延遲新聞或可改寫 memory 可能觸發錯誤同日平倉或 future leakage。
- 處置：價格雙來源／三 fresh samples、官方 primary news 規則、`DATA_CONFLICT`、3 分鐘緊急 graph、每日 immutable reflection、週六 4,000-line memory-curation skill。
- 關閉條件：故障注入證明單點壞價／舊 timestamp／來源衝突不進 LLM；緊急 graph 不開新倉；weekly compaction 可追溯且不修改 raw records／不洩漏未來結果。

## CLOSED-008：P1-A structured logging 可能洩漏 secret 或遺失 audit event

- 嚴重度：High
- 狀態：Closed（2026-08-14）
- 問題：Basic／quoted credentials、bytes、set、自訂物件與 secret-bearing mapping key 可越過舊 redactor；cycle 可造成 `RecursionError`，沒有 audit record。
- 處置：redactor 現在只產生 JSON-safe primitive/container，拒絕非字串 mapping key、避免 redacted-key collision，並限制 cycle/depth；formatter 移除 `default=str`，任何安全轉換／序列化失敗都輸出不含原始 fields 的固定 fallback event。
- 驗證：`tests/test_redaction_and_structured_logging.py` 對抗案例與完整測試通過；修復前 PoC 的 fake Basic/token 不再出現在輸出，cycle 不再拋出 `RecursionError`。

## CLOSED-009：UtcTimestamp 與 SchemaVersion 接受非 canonical／過大輸入

- 嚴重度：Medium
- 狀態：Closed（2026-08-14）
- 問題：寬鬆 ISO parser 接受替代分隔、week/compact date 與 `-00:00`；SchemaVersion 數字元件沒有上限。
- 處置：wire parser 固定為 `YYYY-MM-DDTHH:MM:SS.ffffffZ`；schema component 限制為 `0..9999`，constructor 立即拒絕。
- 驗證：新增 canonical normal/boundary/invalid regression cases，完整測試通過。

## CLOSED-010：P1-B 初版 migration 的 PostgreSQL-only PL/pgSQL 缺陷

- 嚴重度：High
- 狀態：Closed（2026-08-14）
- 問題：第一次真實 PostgreSQL integration 發現 audit JSON secret scanner 對 boolean/null/number 缺少 `CASE ELSE`，且 lease acquire 的 output column 與 table column 同名造成 ambiguous reference；status transition 回傳欄位也未完整符合 repository mapper。
- 影響：安全 audit payload 可能無法寫入、lease 無法取得，或 fenced status transition 無法轉回 domain object；純單元測試與 SQLite substitute 都無法證明這些 PostgreSQL semantics。
- 處置：補上 scalar-safe `ELSE`、完整限定 table column、讓 transition 回傳完整 job identity/state；並補強單一 open lease unique index、security-definer lease functions、固定 search path、function execute revoke 與 generic API-key pattern。
- 驗證：PostgreSQL 16 container 上 migration integration 9/9、persistence/lease integration 9/9，完整 `uv run pytest -q` 242 tests 通過；up/down/up restore、append-only、rollback、concurrency、takeover 與 fencing 均由真實 PostgreSQL 執行。

## CLOSED-011：PostgreSQL runtime authority與SECURITY DEFINER trust boundary不足

- 嚴重度：Critical
- 狀態：Closed（2026-08-15；控制需持續回歸）
- 問題：P1 migration owner同時可作application connection，且privileged functions雖有固定search path，
  仍未把`pg_catalog`放在trusted schema前、未明示`pg_temp`最後，也沒有runtime role catalog proof。
- 處置：migration 0002完整schema-qualify authoritative objects，固定`pg_catalog, public, pg_temp`，
  撤銷PUBLIC schema CREATE／database TEMP／protected EXECUTE；新增外部建立runtime login的bounded
  provision與verify API，禁止owner membership、ownership、direct DML、trigger/function replacement與temp。
- 驗證：真實PostgreSQL 16 runtime-role、catalog、temp-shadowing、stale fencing與repository integration tests。

## CLOSED-012：權威event/audit接受任意或無資源上限JSON

- 嚴重度：High
- 狀態：Closed（2026-08-15；新增event type時重審）
- 問題：原本application可建立任意`JsonObject` payload，資料庫只檢查JSON object／secret pattern，且
  canonical JSON沒有depth、node、width、key/string/final byte budgets。
- 處置：只允許typed `JobCreatedPayload`與`JobStatusTransitionAuditPayload`，event type由payload衍生；
  application在任何telemetry/UoW前核對transition target，migration 0002以constraints獨立執行同一registry；
  `JsonObject`加入各項固定資源上限與不回顯input的bounded failure。
- 驗證：typed-boundary、resource-boundary、transaction-ordering與真實PostgreSQL constraint tests。

## DEFERRED-013：macOS native Keychain smoke evidence

- 嚴重度：Medium
- 狀態：Deferred；P2 composition前重審（已於 P2-E 驗證真實 happy path）
- 判斷：真實 Keychain happy path 已於 P2-E 使用並暴露／修復 native query 問題（`kSecMatchLimitOne`、`NSData` 正規化，見 `PROGRESS.md`）；正式 disposable adversarial smoke（locked/denied/malformed/timeout 等）仍為證據缺口。現有 fake tests 不得冒充 native evidence。
- 條件：另行核准專用 service/account namespace、建立與精確 cleanup，且絕不查詢現有真實 credential；當前無授權建立／刪除 disposable item。

## DEFERRED-014：coverage與security-static／supply-chain CI gate

- 嚴重度：Medium
- 狀態：Deferred；獨立quality工作包
- 判斷：缺少coverage threshold、dependency audit、SBOM、license／secret scan是quality hardening gap，
  但不是本次可重現的P1 authority exploit。現有ADR-015固定兩個required jobs，不能未定義工具、成本、
  false-positive policy與branch-protection migration就靜默加入第三job。
- 關閉條件：另立ADR與接受基線，證明不讀secret、不擴權、不降低既有zero-skip gates，並取得遠端CI證據。

## DEFERRED-015：P2 config與runtime DB credential composition

- 嚴重度：High
- 狀態：Mitigated（2026-08-20；composition root 已交付，長駐 process 仍 deferred）
- 判斷：原判斷「不存在 composition root」已過時。`application/composition.py` 已交付 exact-schema typed config、runtime DB `SecretRef`（`POSTGRES_RUNTIME_PASSWORD`）、`RuntimeDsn` 最窄 reveal、Paper endpoint allowlist 與 `build_execution_stack`（含 control 注入），並有 `tests/test_composition.py` 與 PostgreSQL runtime-role 整合驗證。現有 `control`/`broker`/`db` 邊界已可重現驗證，非「可利用的現存 execution path」已不再成立。
- 已固定契約：raw config 只留在 exact-schema parser edge，adapter 只收 typed config；runtime 使用 exact secret ref 與最窄 reveal 點；owner/runtime DSN 不得進 snapshot、argv、log、telemetry、audit 或 exception。
- 剩餘 deferred：長駐 process（launchd）與 startup privilege proof 仍屬 P6/P7 bring-up，不在本輪 P2 驗收範圍；composition root 本身已滿足 closure condition（見 ADR-018/021/024 與 PROGRESS.md P2-D 節）。
- 關閉條件：原條件中「P2 composition 實作與 adversarial tests 通過」已滿足；長駐 process 啟動 proof 留後續 gate 重審。

## ASSESSED-016：Keychain必須改成persistent-reference兩段查詢

- 嚴重度：Not confirmed
- 狀態：Assessed（2026-08-15）
- 判斷：目前query是exact generic-password service/account、`kSecMatchLimitAll`、return data並對0／1／多筆
  fail closed；Apple文件允許match-all回傳多筆result，且generic-password service/account屬primary key。
  未取得平台文件或native reproduction證明return-data + match-all不安全，因此不以推測修改native boundary。
- 重審條件：Apple行為改變、OS/PyObjC升級、native ambiguous-result reproduction或官方文件明確要求。

## CLOSED-017：ExecutionEngine 提交路徑未檢查 pause 狀態（pause bypass）

- 嚴重度：Critical
- 狀態：Closed（2026-08-17 修復並驗證；ADR-021）
- 問題：`ExecutionEngine` 只注入 broker 與 clock（`src/seven_lens/application/execution_service.py`
  `__init__`），`submit_from_outbox` 由 OUTBOX_PENDING 直接轉 SUBMITTING 並呼叫 broker，全程未讀
  `ControlStateSnapshot.entries_paused`；`ControlPlane.assert_entries_allowed`（`control_service.py`）
  只在 operator shell 路徑存在。因此 `pause_entries` 後 outbox worker 仍會送出全新 entry，違反
  OPERATIONS_AND_SAFETY 的 PAUSED_ENTRIES 語意與「暫停後不得建立新 exposure 意圖」不變量。
- 修復前證據：`tests/test_execution_pause_remediation.py` 全部案例 red（引擎缺少 `control`
  依賴：`TypeError`/缺 `ExecutionPausedError`；paused 下 `submit_from_outbox` 回傳
  `ACKNOWLEDGED` 且 broker 產生新 order 即缺陷存在）；RISK_REGISTER R-09/R-04 控制面缺口。
- 處置：engine 注入 `control` state source（預設無 pause source＝不阻擋，既有呼叫點不變）；
  `submit_from_outbox` 在 SUBMITTING 轉移／commit／broker 呼叫前檢查 pause，paused 且非
  RISK_EXIT 抛 `ExecutionPausedError`（零副作用）；paused 下 cancel/expire/fills 維持可用；
  resume 不需重建 engine；`build_execution_stack(..., control=...)` 注入同一 control
  repository，reconciliation mismatch 自動暫停立即對引擎生效。
- 驗證：`test_execution_pause_remediation.py` 5/5 綠（paused 阻擋零副作用、resume 恢復、
  unpaused 正常、RISK_EXIT 放行、expire/cancel 不受影響）；靜態 gate 與
  `verify_p1.sh --postgres` 全綠。

## CLOSED-018：broker_orders 混用兩種時鐘（broker updated_at 被本地 DB 時間覆寫）

- 嚴重度：High
- 狀態：Closed（2026-08-17 修復並驗證；ADR-021；migration 0006）
- 問題：`migrations/0003` 的 `broker_orders.guard_broker_order_write` trigger 把
  `updated_at` 固定寫成 `statement_timestamp()`，`postgres.py` INSERT 亦未寫入 broker 的
  `updated_at`；domain `BrokerOrder.updated_at` 語意是 broker 端事件時間，而
  `TradeUpdateConsumer._apply_status` 以 `observed_at < mirror.updated_at` 判 STALE。真實
  PostgreSQL 下 mirror.updated_at 是本地寫入時間：broker 時鐘偏差／事件回放時序下，合法 broker
  事件被誤判 STALE 靜默丟棄；本地記錄時間與 broker 時間混在同一欄位無從稽核。
- 修復前證據：`tests/integration/test_broker_order_timestamps_postgres.py` red（roundtrip
  回讀 `updated_at` 為本地現在而非 broker 時間；clock-skew 事件被 STALE 丟棄）。
- 處置：migration 0006 新增 `broker_orders.broker_updated_at`（broker 時間，`guard_broker_updated_at`
  trigger 強制單調不倒退；backfill 用本地時間近似）與本地 `updated_at`（statement_timestamp）
  分工；`record_broker_order`/`update_broker_order_status(+broker_observed_at)` 持久化 broker
  時間，mapper 以 broker_updated_at 供給 domain；fake repo 同步語意；consumer 的 STALE 基準
  改 broker 時間（`_apply_fill` 以 fill occurrence、`_apply_status` 以 observed_at）。
- 驗證：`test_broker_order_timestamps_postgres.py` 2/2 綠（roundtrip 保留 broker 時間；
  clock skew 下事件 APPLIED 不再 STALE）；migration 0006 在 `test_migrations.py` 完整
  up/down/up cycle 綠；`run_postgres_integration.sh` 66 passed/8 deselected；
  `verify_p1.sh --postgres` 全綠。

## ASSESSED-019：CANCEL_PENDING→EXPIRED 與「不可表示終態」已由既有機制涵蓋

- 嚴重度：Not confirmed
- 狀態：Assessed（2026-08-17）
- 判斷：Python 與 SQL 兩份狀態機映射已一致允許 CANCEL_PENDING→EXPIRED（`orders.py` 與
  `0003` guard 逐對相等，`test_sql_transition_functions_match_the_python_maps` 強制）；
  窗口截止四步（解析→取消→僅無單過期→transport error 停滯）由 ADR-020 涵蓋；SUBMITTING 遇
  broker 終態以 `ExecutionStateError` fail closed 零副作用（ADR-018、round-2 對抗測試已鎖）。
  本次審查對 A/E/F/G/H/N 各以 red reproduction 重新證明缺陷並全部修復（CLOSED-017、
  CLOSED-018、F/H 於 `test_alpaca_paper_adapter.py`、G 於 `test_reconciliation_and_ledger.py`、
  N 於 `test_p1_c3_ci.py`）；B/C/D 不再重述為獨立缺陷。
- 重審條件：狀態機映射或截止語意被修改時重跑同等價整合測試。

### A–N 清單第二輪重審（2026-08-18，第二輪 remediation）

- 對 A–N 全部項目重跑一次對照：A（pause）維持閉合（本輪未改 submit 閘門語意，
  `test_execution_pause_remediation.py` 5/5 仍綠）；B/C/D 維持不再重述；E（雙時鐘）
  深化為 watermark 保守化（0007 清 NULL＋submitted_at lower bound，CLOSED-020/ADR-022）；
  F（重複 id）深化為過期後查無單→UNKNOWN（`_resolve_duplicate` 404→
  `DuplicateClientOrderIdUnknown`，`test_duplicate_id_with_missing_order_is_ambiguity_
  not_rejection`）；G（終態對帳）深化為 closed-history pass（list_recent_orders
  範圍掃描）；H（分頁）維持閉合；N（CI postgres job）維持閉合（integration 66/66）。
  新發現並修復：deadline 後本地 EXPIRED 矛盾、filled_quantity 可倒退、flatten 未對帳
  即下單、重複 flatten id 碰撞、對帳僅存 kind、未知資產仍下單（全部列入 CLOSED-020）。

## CLOSED-020：broker 真值未知時引擎仍可能本地宣告終態（第二輪 remediation）

- 嚴重度：High
- 狀態：Closed（2026-08-18，resolved by design change + adversarial tests）
- 問題：`expire_overdue` 對 SUBMITTING 超時一律本地 EXPIRED，等同在 broker 可能仍持有
  訂單時自行宣告「已結束」；`broker_updated_at` 的 0006 backfill 用本地時間造成過高
  watermark，可能把合法 broker 事件全程誤判 STALE 丟棄；`broker_orders` guard 未防
  filled_quantity 倒退與 FILLED 數量不一致；flatten 在未確認 paused、未 resolve
  歧義、未取消、未與 broker position view 對帳前就下賣單，且固定 target_version=1
  使重複 flatten 的 client order id 碰撞。
- 處置（對應 ADR-022）：
  1. 引擎 spike：`resolve()` 重寫（deadline 後 GET 無單 → SUBMITTING 轉 UNKNOWN，已
     UNKNOWN 保持）；`expire_overdue()` 只對從未到過 broker 的狀態本地 EXPIRED，
     取消路徑含 transport error 一律保留 CANCEL_PENDING；`recover()` 修掉同一 sweep
     重複 resolve。
  2. watermark：0007 將 broker_updated_at 清成 NULL（unknown），domain 以 submitted_at
     lower bound 讀取；trade updates 回放同值 → DUPLICATE，同 timestamp 不同值 →
     明確衝突錯誤。
  3. SQL guard：0007 新版 `guard_broker_order_write`／`guard_broker_order_insert`
     （filled 不倒退、FILLED exact、身份 immutable、僅兩端非 NULL 才禁倒退），
     status CHECK 完整 15 態，`REVIEW_REQUIRED` 收斂六個 review 狀態。
  4. flatten 六步 + `FlattenPriceProvider` seam + durable `flatten_generation`
     （`control_state` 新欄位，同交易原子遞增）＋ position 對帳不符即 abort。
  5. 對帳明細：新增 `reconciliation_mismatches`（append-only、kind+detail、ordinal
     穩定）；closed-history pass 以 `list_recent_orders(since=前一輪 observed_at)`
     補終態漏報；`INTENT_STATUS_MISMATCH` 納入 SQL kinds CHECK。
  6. 資產閘：`submit_from_outbox` 以 `get_asset` 驗證 symbol 已知且 tradable
     （含 RISK_EXIT），flatten 下單前預檢全部部位。
- 證據：`tests/test_execution_engine.py`（TestPendingCancelCutoff 4 案、
  TestBrokerTerminalRecovery、TestDuplicateDelayedVisibility、TestAssetGate 3 案）、
  `tests/test_control_plane.py`（flatten 5 新增案）、`tests/test_reconciliation_
  and_ledger.py`（closed-history 4 案）、`tests/test_session.py`；`scripts/
  run_postgres_integration.sh` 66 passed、`verify_p1.sh --postgres` 66 passed、
  non-integration 621 passed、ruff/mypy 92 檔全綠；migration 0007 up/down 於
  `test_migrations.py::test_migration_up_down_restore_cycle_is_explicit` 驗證。
- 重審條件：獨立驗收重跑上列 gate 並審 0007 SQL 與 ADR-022 逐項對照。

## CLOSED-021：cash/NAV 真實帳戶讀取（P2-E %08 cash & NAV）已由真實驗證覆蓋

- 嚴重度：Low
- 狀態：Closed（2026-08-18）
- 問題：第二輪 planning 疑慮——P2-E 真實 read-only 證據中的 cash/equity 讀取是否
  有格式/NAV 缺陷。
- 判斷：P2-E 首次真實驗證（2026-08-17，operator 授權）已實測 `USDT`/`cash` 解析：
  `cash 100000.00`、`equity 100000.00`（`_two_decimal_decimal` 正規化至恰 2dp，
  exponent 異常仍 fail closed，詳 PROGRESS.md P2-E 節）；本 repo P2 範圍無 NAV 計算
  元件（ledger 只輸出 cash_delta 與 lots，NAV valuation 屬後續工作包）；無程式碼缺陷。
- 重審條件：P7 引入 NAV/portfolio valuation 時建立逐 tick 對帳。

## SUPERSEDED-021：CLOSED-021 的 cash/NAV 關閉理由不成立（P2-CUR-006）

- 嚴重度：High
- 狀態：Superseded（2026-08-20；P2-CUR-006）
- 原判斷：CLOSED-021 認為 P2 無 NAV 元件、無程式碼缺陷，僅驗證 broker cash/equity wire parsing。
- 再審結果：ROADMAP P2 明列 `cash/NAV ledger`；`ledger.py` 已有 `account_valuation`，但 `Reconciler.collect` 未使用，未建立 expected account id / authoritative opening cash baseline / mark price seam / buying power parse+tolerance；屬 CURRENT VERIFIED SPEC GAP。
- 處置：新增 `PaperAccount.buying_power` 嚴格解析、`account_baselines` 權威基線表（migration 0008）、`AccountReconciliationPolicy` / `ReconciliationMarkPriceProvider` seam、4 類新 mismatch kinds（ACCOUNT_ID/CASH/NAV/BUYING_POWER + UNAVAILABLE）及 `LOCAL_LEDGER_INVARIANT`，並以 `--` 內 PostgreSQL roundtrip 與 fake 對抗測試覆蓋。CLOSED-021 僅代表 wire parsing，已被本 P2-CUR-006 全面對帳取代。
- 重審條件：P2 帳務 gate 以新對帳契約與真實 PostgreSQL 整合重驗後關閉。

## CLOSED-022：P2 獨立驗收發現的 recovery、pagination、flatten 與 asset/review 缺陷

- 嚴重度：Critical/High
- 狀態：Closed（2026-08-19；ADR-023）
- 問題與修復：pause race 可讓 recovery 重送新部位，已在 reservation 前後與 broker submit
  前重查並停在 UNKNOWN；Alpaca orders/fills 改用官方 `after_order_id`/`page_token`；flatten
  有任何未收斂 order 即 abort；asset gate 要求 US_EQUITY；REVIEW_REQUIRED 永遠產生
  reconciliation mismatch。
- 證據：新增 6 個對抗案例；locked gate 627 passed / 74 deselected；真實 PostgreSQL 16
  66 passed / 8 deselected；Ruff/mypy/lock 全綠。

## CLOSED-023：P2 全面再驗收的 concurrency、partial audit 與 reconciliation/fill 缺陷

- 嚴重度：Critical/High
- 狀態：Closed（2026-08-19；ADR-024；Luna 三輪對抗重現）
- 問題與修復：pause check 與 broker submit 間的 TOCTOU 改由 PostgreSQL shared row lock
  線性化；cancel/flatten 中途失敗寫 `applied_at=NULL` partial command；broker query failure
  持久化 `BROKER_QUERY_FAILURE` 並 pause；open/history snapshot 以 timestamp+status 合併，
  equal timestamp 不同狀態不去重；fills 加全域 cursor-cycle/bounded-page 與 order-id identity。
- 證據：原四組 fault injection 與 equal-timestamp 重現均由 Luna 確認 closed；637 個
  non-integration、69 個 PostgreSQL 16 integration、1 個真實 GET-only live acceptance 全綠。

## P2-ACC-001：併發新單 shared lock 可同時越過 broker — P2 Blocker（High/Safety）

- 嚴重度：High / Safety Blocker
- 狀態：Closed（2026-08-20；本機與 exact-SHA remote CI 證據完成）
- 原始問題：`PostgresControlRepository.submission_guard()` 曾採 `FOR SHARE`，兩個新單可同時持有相容鎖並先後進入 broker.submit，超時轉 UNKNOWN 前第二單已建立新 exposure。
- 影響：違反「一旦有模糊提交朝 UNKNOWN 競賽，不得有第二個新單越過 broker 邊界」安全不變量。
- 修復：目前程式使用 `FOR UPDATE` 使非 `RISK_EXIT` 新單 broker 臨界區互斥；timeout 的 A 先 durable UNKNOWN/pause，B 才取得鎖並 fail closed；`RISK_EXIT` 豁免。
- 驗收：真實 PG 雙連線 `test_pg_timeout_unknown_blocks_racing_second_entry_at_broker_boundary` 證明 racing B broker call count = 0；restart gate、success release、RISK_EXIT 與 self-deadlock tests 全綠。

## P2-ACC-002：baseline cutoff 錯誤丟失 cutoff 前部位的 NAV — P2 Blocker（High/Accounting）

- 嚴重度：High / Accounting Blocker
- 狀態：Closed（2026-08-20；本機與 exact-SHA remote CI 證據完成）
- 原始問題：有 cutoff 的 revision 以 `post_projection` 同時計算 cash 與 NAV，導致 cutoff 前已建倉且仍持有的部位自 NAV 消失。
- 修復／驗收：cash 使用 checkpoint + post-cutoff fill delta；NAV positions/lots 永遠使用 full ledger。pre-cutoff position NAV、post-cutoff partial sell、same-timestamp execution-id tie-break tests 全綠。

## P2-ACC-003：runtime role 可任意 INSERT baseline revision — P2 Blocker（High/Authority）

- 嚴重度：High / Authority Blocker
- 狀態：Closed（2026-08-20；本機與 exact-SHA remote CI 證據完成）
- 原始問題：runtime 被授予 `account_baselines`/`account_baseline_revisions` 的 INSERT，追加最新 revision 即可竄改權威。
- 修復／驗收：runtime 對兩表僅 SELECT，INSERT/UPDATE/DELETE 均無權；owner/operator path 保留顯式 authority。兩個 runtime INSERT denial tests 與 privileged create/revision tests 於真實 PG 全綠。

## P2-ACC-004：合法 0008 mutated baseline 使 0009 遷移失敗 — P2 Blocker（High/Migration）

- 嚴重度：High / Migration Blocker
- 狀態：Closed（2026-08-20；本機與 exact-SHA remote CI 證據完成）
- 原始問題：0008 允許 UPDATE effective_at 而保留原 created_at，使 `effective_at > created_at` 合法；0009 以原 created_at 建 revision 並設 `CHECK (effective_at <= created_at)`，該合法 0008 狀態升級至 0009 即失敗。
- 修復：不改 checksummed 0009。runner 只對 8→9 compatibility transaction 暫時以 legacy `effective_at` 取代 source `created_at`，完成 copy 後還原 source original `created_at`。revision `created_at` 的語意精確為 legacy `effective_at`／authority-effective timestamp，不是 migration execution time。
- 驗收：mutated 0008 test 精確斷言 revision `created_at == legacy effective_at` 且 source `created_at` 原值保留；canonical 0008、clean latest、existing 0009/current、down/up 與 checksum gates 全綠。migration files/checksums 未變。

## P2-ACC-005：genesis baseline 僅文件限制，無強制不變量 — P2 Blocker（High/Accounting）

- 嚴重度：High / Accounting semantics Blocker
- 狀態：Closed（2026-08-20；本機與 exact-SHA remote CI 證據完成）
- 原始問題：`set_baseline()` 宣稱僅在無 fills 前可創 genesis，實際未強制且非事務安全，競態可產生 `fill 與 genesis 交錯`。
- 修復／驗收：`LOCK TABLE fills IN EXCLUSIVE MODE` 與 ledger check 在同一 transaction；新增兩個獨立 UoW/thread 的 `test_genesis_baseline_creation_race_with_first_fill_is_serialized`，以 Events、bounded joins、`lock_timeout` 證明 genesis 持鎖期間 first fill INSERT 阻塞，genesis commit 後才提交。empty-ledger allow、after-fill reject、revision cutoff tests 全綠。

## P2-ACC-006：Account 對帳仍將程式缺陷降級為 UNAVAILABLE — P2 Blocker（Medium-High）

- 嚴重度：Medium-High
- 狀態：Closed（2026-08-20；本機與 exact-SHA remote CI 證據完成）
- 原始問題：account reconciliation 仍以 broad `except ValueError` 處理 expected external/input absence，可能將程式或設定缺陷降級為 `ACCOUNT_RECONCILIATION_UNAVAILABLE`。
- 修復：新增 `MarkPriceUnavailableError`，只有 typed expected mark absence 轉 unavailable；unexpected `ValueError`、`AttributeError`、`TypeError`、`PersistenceInvariantError` 皆向外傳播。missing baseline 保持 fail-closed mismatch；ledger corruption 由外層轉 durable `LOCAL_LEDGER_INVARIANT`。
- 驗收：附件指定的 7 個 failure-taxonomy tests 全綠，未弱化原測試。

## P2-ACC-007：衝突遲到 fill 未持久化 reconciliation-required 證據 — P2 Blocker（Medium-High/Safety）

- 嚴重度：Medium-High / Safety
- 狀態：Closed（2026-08-20；本機與 exact-SHA remote CI 證據完成）
- 原始問題：`TradeUpdateConsumer._apply_fill` 於衝突時保留 fill 並 rollback 衍生狀態但未持久化 entries_paused/PAUSE 命令，控制面重啟後仍可建新單。
- 修復／驗收：fill fact 先 commit、partial derived state rollback，再 durable pause + `PAUSE_ENTRIES`，最後拋 typed failure；fresh connection test 可見 fill fact 與 pause。

## P2-ACC-008：治理文件仍稱 P2 Closed — P2 Blocker（Medium/Governance）

- 嚴重度：Medium / Governance
- 狀態：Closed（2026-08-20；治理與 exact-SHA remote CI 證據完成）
- 原始問題：README/PROGRESS top-level 曾稱 P2 Closed，後段又稱 Reopened；ACC-001 也仍將現行程式描述為 `FOR SHARE`。
- 處置：修復／本機驗證期間，上述文件一致標示 `P2 Gate Reopened — final acceptance in progress`；exact-SHA remote CI 全綠後再一致更新為 `P2 Gate Closed`。歷史 Closed／Reopened chronology 保留；current implementation 明載 `FOR UPDATE`。

## P2-ACC-009：缺完整回歸與關鍵場景證據 — P2 Blocker（Acceptance）

- 嚴重度：Acceptance Blocker
- 狀態：Closed（2026-08-20）
- 原始問題：缺少 fresh 完整鎖定 gate、真實 PG threading race／重啟／遷移／權限與 final-HEAD 遠端證據。
- 本機證據：lock、Ruff format/check、mypy 全綠；non-integration `676 passed, 91 deselected`；real PostgreSQL 16 `83 passed, 8 deselected, 0 skipped`；`verify_p1.sh` 與 `verify_p1.sh --postgres` exit 0。併發 B=0、重啟 UNKNOWN、RISK_EXIT、genesis-vs-fill race、衝突 fill restart pause、0008→latest、runtime INSERT denial、cutoff NAV 全部可重現。
- 遠端證據：code-bearing commit `488f170` 已推送；GitHub Actions [`32360443947`](https://github.com/ihsieh31/seven-lens-paper-trading/actions/runs/32360443947) 在該 exact SHA 上 `quality-unit`（19s）與 `postgres-integration`（1m8s）均成功。未沿用舊 run。
