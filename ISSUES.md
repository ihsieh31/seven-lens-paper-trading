# Issue Log

此檔記錄具體問題、阻塞和需要驗證的假設；關閉時保留原因與證據位置。

## OPEN-001：七人公開語料完整性不均

- 嚴重度：High
- 狀態：Open
- 問題：沒有 X Developer API 或付費訂閱，無法保證完整歷史貼文、刪文或即時性。
- 影響：蒸餾可能有選擇偏差；runtime 不能假定已看完某人的所有公開觀點。
- 處置：保存 coverage window、query、URL、擷取時間和缺口；委員可 `ABSTAIN`；交易流程不依賴即時 X。
- 關閉條件：每位委員達到蒸餾規格最低來源與 held-out evaluation 門檻。

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

## OPEN-005：歷史回測可能有蒸餾前視偏差

- 嚴重度：Critical
- 狀態：Open
- 問題：把 2026 年整理出的 doctrine 用於更早日期會把未來資訊帶進回測。
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
- 狀態：Deferred；P2 service composition前重審
- 判斷：缺少native smoke是真實證據缺口，不是已證實的read-only adapter漏洞。現有fake tests不得冒充
  native evidence；目前也沒有授權建立／刪除disposable Keychain item。
- 條件：另行核准專用service/account namespace、建立與精確cleanup，且絕不查詢現有真實credential。

## DEFERRED-014：coverage與security-static／supply-chain CI gate

- 嚴重度：Medium
- 狀態：Deferred；獨立quality工作包
- 判斷：缺少coverage threshold、dependency audit、SBOM、license／secret scan是quality hardening gap，
  但不是本次可重現的P1 authority exploit。現有ADR-015固定兩個required jobs，不能未定義工具、成本、
  false-positive policy與branch-protection migration就靜默加入第三job。
- 關閉條件：另立ADR與接受基線，證明不讀secret、不擴權、不降低既有zero-skip gates，並取得遠端CI證據。

## DEFERRED-015：P2 config與runtime DB credential composition

- 嚴重度：High
- 狀態：Deferred；P2加入任何長駐process前的blocker
- 判斷：目前不存在service composition root、runtime process或broker/database credential loader，因此不是
  可利用的現存execution path；但不能等adapter寫完才定義。
- 已固定契約：raw config只留在exact-schema parser edge，adapter只收typed config；runtime使用exact
  secret ref與最窄reveal點；owner/runtime DSN不得進snapshot、argv、log、telemetry、audit或exception。
- 關閉條件：P2 composition實作與adversarial tests通過，且runtime role proof在啟動前fail closed。

## ASSESSED-016：Keychain必須改成persistent-reference兩段查詢

- 嚴重度：Not confirmed
- 狀態：Assessed（2026-08-15）
- 判斷：目前query是exact generic-password service/account、`kSecMatchLimitAll`、return data並對0／1／多筆
  fail closed；Apple文件允許match-all回傳多筆result，且generic-password service/account屬primary key。
  未取得平台文件或native reproduction證明return-data + match-all不安全，因此不以推測修改native boundary。
- 重審條件：Apple行為改變、OS/PyObjC升級、native ambiguous-result reproduction或官方文件明確要求。
