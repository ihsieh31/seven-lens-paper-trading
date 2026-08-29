# Issue Log

只列目前Open／Deferred項目與已關閉索引。詳細歷史、逐案重現與舊狀態保留於Git history；
Issue關閉不會自動關閉較大的phase gate。

## Active

### OPEN-002 — 免費來源授權與再散布

- 嚴重度：High
- 問題：公開可讀不等於可重製、快取或再散布全文。
- 控制：保存URL/hash/時間/rights metadata；repo只放允許的metadata、摘要或fixture。
- 關閉：每個production source family有授權矩陣、保存政策與拒絕測試。

### OPEN-003 — 免費行情與基本面品質

- 嚴重度：High
- 問題：免費資料可能延遲、修訂、缺欄位或來源集中。
- 控制：point-in-time availability、多family核對、freshness/conflict gate、缺資料棄權。
- 關閉：P5 coverage、drift、walk-forward與economic-fill evidence通過。

### OPEN-004 — 單台Mac無人值守

- 嚴重度：High
- 問題：睡眠、斷電、網路與程序中止會錯過窗口或造成狀態不明。
- 控制：durable PostgreSQL state、lease、reconciliation、missed-window `NO_TRADE`。
- 關閉：P6/P8 restart、heartbeat、backup/restore與uptime gate通過。

### OPEN-005 — 歷史回測future leakage

- 嚴重度：Critical
- 問題：published time不等於當時實際available time，修訂資料也可能污染歷史決策。
- 控制：`available_at`、frozen packet/snapshot、as-of query與time-travel tests。
- 關閉：P5 point-in-time walk-forward與decision replay通過。

### OPEN-006 — 告警通道未選定

- 嚴重度：Medium
- 控制：保留capability-minimal `AlertPort`；未選定前不得宣稱critical alert可達。
- 關閉：免費通道由使用者選定並完成真實端到端演練。

### OPEN-007 — Tavily七帳號授權未證實

- 嚴重度：High
- 問題：使用者擁有多帳號不等於外部條款允許彙總或輪替額度。
- 控制：`SINGLE_ACCOUNT_UNVERIFIED`固定fail closed；本地ticket/reference不能自我升權。
- 關閉：可信外部授權、帳號集合綁定、quota ledger、ADR與獨立驗收全部完成。

### OPEN-027 — Provider transport可靠性不可由單次P3-F batch永久證明

- 嚴重度：High／P6 blocker；不是P3-F功能正確性blocker。
- 問題：V4～V8多次因`TIMEOUT`／`TRANSIENT`在首錯停止；反覆換split沒有修復provider可用性，單次全綠也不能
  推論未來可用性。
- 控制：ADR-033有界兩次retry、attempt cap 780、指數backoff＋jitter、三個連續exhausted cases circuit breaker，
  並把Live Model Quality與Provider Transport分開報告。V12批次（2026-08-26）first-attempt/eventual皆100%，
  為該批GREEN snapshot。
- 關閉：P6前另行授權的synthetic canary在rolling 7日、至少200 logical calls窗口達first-attempt≥95%、
  eventual≤3 attempts≥99%，且P6～P8持續監控；provider/model改版或rolling window跌破門檻即重開。

- 2026-08-28 更新：active route 已改為 NVIDIA `openai/gpt-oss-120b`（ADR-033）。V14 單批 live snapshot
  （130/130 pre-network、first-attempt 97.7%、eventual 99.2%、fallback 0）不構成永久可用性證明；
  rolling 7 日 ≥200 logical calls canary 義務改綁現行 route，仍為 P6 前置。
### OPEN-036 — 多來源與point-in-time security master residual scope

- 嚴重度：High／P4 blocker。
- 問題：P4-A與P4-B的source roles、offline adapters、point-in-time security master與corporate-action lineage
  已完成fresh independent acceptance並Accepted／Closed；但這不代表FRED/ALFRED、官方宏觀、SEC/IR、corporate
  actions、GDELT、交易所、yfinance等production use已驗收或已授權。普通fallback仍可能把discovery/supplement
  錯誤升權，或以今日revision/symbol污染歷史run。
  普通fallback可能把
  discovery/supplement錯誤升權，或以今日revision/symbol污染歷史run。
- 控制：ADR-036四種source role、exact-host GET-only、typed secret、rights/rate-limit/schema drift、
  observation/available/effective/vintage時間、event-sourced security master與silent-fallback fail closed。
- 關閉：P4-C～F完成後續production composition與source family整合驗收，P5完成vintage/symbol time-travel與
  walk-forward；任何真實API呼叫另需當次使用者授權。P4-A／P4-B子Gate已不再是本issue的未驗收缺口。

### OPEN-037 — 拆股／合股自動退出尚未實作／驗收

- 嚴重度：Critical／P4～P7 blocker。
- 問題：P4-B拆／合股的point-in-time identity、source lineage與三層entry quarantine已fresh independent acceptance
  並Accepted／Closed；但Alpaca Paper可能不正確反映拆／合股後quantity／orders。現有`flatten_paper`是人工全帳戶SELL-only authority，
  不能支撐自動單一symbol退出；P3 memory與P4-E/P7的`CORPORATE_ACTION_EXIT` runtime wiring仍未完成。
- 控制：ADR-037；發現即三層entry block，正式來源確認才auto-exit；cancel/resolve→FULL reconcile→
  tradability/regular-hours/price collar→idempotent `CORPORATE_ACTION_EXIT`；late/changed/conflict進
  `REVIEW_REQUIRED`；fills＋FULL reconciliation後才計P&L與衍生memory。
- 關閉：P5 point-in-time replay、P6至少涵蓋forward/reverse split及
  partial/late/withdrawn/identity-drift shadow演練、P7 fresh independent acceptance與exact使用者submit授權。
  第一版short BUY-to-cover不在auto authority，若要支援需另開review。

### OPEN-038 — 零付費IEX最新行情不是完整市場報價authority

- 嚴重度：High／P7 blocker；不阻止P4 zero-submit planning。
- 問題：免費Alpaca即時feed主要是單一交易所IEX，不等同完整SIP/NBBO。它可支撐P4的有限覆蓋quote、spread
  與quantity模擬，但不能自行證明P7送單價格保護涵蓋全市場。
- 控制：每筆snapshot固定feed/entitlement並標`LIMITED_MARKET_COVERAGE`；歷史日線／ADV優先使用可得的
  delayed SIP；yfinance永不升權；quote>5秒、spread>30bps、來源缺失／衝突即`NO_TRADE`。
- 關閉：P5比較IEX／delayed SIP coverage與economic-fill偏差；P7前找到零付費且rights/latency/coverage可驗收
  的完整報價authority，否則Paper submit Gate維持Blocked。任何付費方案需使用者另行決策。

### OPEN-040 — PG integration runtime-role trigger測試出現既存server-connection flake

- 嚴重度：Medium（不阻塞P4-A scope；屬測試環境穩定性）。
- 問題：`test_runtime_role_verification_rejects_a_missing_guard_trigger`在disposable PG16 docker環境
  偶發`server closed the connection unexpectedly`。2026-08-27於P4-A變更工作樹與乾淨HEAD
  （P4-A檔案全數移出）各重跑均重現（1～2 errors），證明與P4-A變更無關。
- 控制：重跑確認、保留exact重現命令（`./scripts/verify_p1.sh --postgres`／
  `scripts/run_postgres_integration.sh`）；其餘242個integration tests全綠。
- 關閉：定位server crash根因（trigger drop路徑或docker資源）後修復或隔離，並取得穩定連續全綠run。

## Deferred

### DEFERRED-001 — Future Analyst Plugin語料完整性

七人方法論與本機corpus維持disabled；不讀取、不發布、不阻塞P3。只有使用者重新核准蒸餾方法、
來源／rights與獨立plugin gate後才恢復。

### DEFERRED-013 — Formal macOS Keychain adversarial smoke

production exact-read boundary與P2-E happy path已有證據；locked/denied/malformed/timeout的formal
native smoke需要專用namespace與另行授權，不得查詢現有真實item。

### DEFERRED-014 — Coverage／security-static／supply-chain lane

目前locked quality與PostgreSQL gates不變。新增coverage、dependency audit、SBOM或secret scan前，
需先定義工具、基線、false-positive與required-check政策；不屬P3-B+C acceptance。

### DEFERRED-025 — Control shell CLI

`pause_entries`、`cancel_open_orders`、`flatten_paper`已有application path；operator CLI留P6/P7，
不得因現有service存在就宣稱已交付人工控制入口。

### OPEN-039 — 分析 provider switch pending fresh acceptance 與 connect timeout 政策觀察

- 嚴重度：High（provider switch acceptance blocker）；政策觀察部分為 Low。
- 問題：2026-08-28 分析 provider switch（offline implementation＋P3-E live 6/6＋P3-F V14 全門檻通過）
  曾因 fresh independent acceptance findings 進入 remediation；實作者不得自行關閉驗收缺口。
  另 P3-F live 的 8 次 retry 全部為 ≈2004ms 的 TRANSIENT（`connect_timeout_ms=2000` 固定預算用盡），
  顯示 2 秒連線預算對 NVIDIA edge 偏緊；屬政策敏感度而非缺陷。
- 控制：fresh independent acceptance；`connect_timeout_ms` 屬 package-owned policy material，任何調整
  需新的使用者決策並重算 route hash（會改變 route identity）；rolling canary（OPEN-027）持續監控。
- 關閉：fresh acceptance verdict 出爐（Accepted／Rejected）；connect timeout 若需調整另開 ADR 並重跑
  P3-E/P3-F 授權 live 驗證。

- 2026-08-29 fresh independent acceptance verdict＝**Rejected**，兩項 High 已於本輪修復：
  F-01 transport 未驗證 DNS 解析位址為 public（credential 可送 loopback/private）→ `ResolvedAddress`
  現逐地址拒絕 internal scope（含 IPv4-mapped），mixed public+private 整批 pre-connection 拒絕，並補
  permanent tests；F-02 migration 0022 up 的 route-hash backfill 在任何既有 claim/audit rows 的庫上必然
  55000 失敗（guard trigger 只允許 CLAIMED→CLOSED）→ backfill 改為 migration 交易內暫時 disable 該
  row-write guard 後回填再 enable（owner＋ACCESS EXCLUSIVE 交易內無繞過窗口），並補
  「0021 先寫 legacy rows 再升 0022」的真實 PG16 regression test。另修 F-06（generic live executor 的
  per-record response_hash_kind 誤標 SCRIPTED→現為 PROVIDER_RAW_RESPONSE_BODY_SHA256 且逐 post 正確）、
  F-04/F-05（docs latency 數字與 stale route 陳述）、F-08（production root 推導現拒絕任何 symlink
  path component，`validate_production_root`＋permanent test；loader 保留 spec 認可的 test-injection 路徑）。
- F-07（Low，延後）：generic route 的 claim 仍記錄 `reasoning_requested=MAX`。誠實修復需擴充
  `ReasoningRequested` 封閉 enum（目前僅 MAX）並放寬 0012 已獨立驗收的 claims/audits 表
  `reasoning_requested='MAX'` CHECK——對 audit 元數據級 Low 問題屬不成比例的 schema／domain 變更；
  MAX 為自 Agnes route 沿用的 package-owned policy 常數，不影響 authority 或輸出正確性。
- F-03（Medium，未完全關閉）：V14 live run 的 authorization JSON／grant 未隨 evidence 保存，其
  expiry／authorization_id／timeout_ms 已不可獨立重算（evidence 內數值門檻本輪已由 raw records 全部獨立
  重算通過）。`scripts/run_p3f_live_evals.sh` 現於 live-run 後自動歸檔 authorization 至 evidence 目錄；
  後續任何 live evidence 必須含此 artifact 才可支持 Accepted。
- 因 F-01/F-02/F-06 改動 transport／migration／evidence 代碼，既有 P3-E/P3-F live evidence 不再代表
  current code；修復後需以當次精確授權重跑新 route live evidence，再提交 fresh independent acceptance。

## Closed／Superseded索引

| IDs | 結論 |
|---|---|
| CLOSED-008～012 | P1 redaction、canonical values、migration、runtime authority與typed JSON修復 |
| CLOSED-017～018 | P2 pause bypass與broker timestamp authority修復 |
| ASSESSED-016／019 | Keychain persistent-reference與cancel/expire語意完成評估；未形成當前blocker |
| CLOSED-020～023 | P2 UNKNOWN、cash/NAV、recovery/pagination/flatten與concurrency/partial audit修復 |
| P2-ACC-001～009 | P2 final remediation全數Closed；證據見`PROGRESS.md` |
| CLOSED-024 | migration 0010 down漏刪version row已修復並通過up/down/up |
| CLOSED-025 | P3-E provider capability／privacy／output stability 已完成 capability audit、sanitized contract、timeout/failover、privacy 與 held-out evidence；P3-E Accepted，後續 rolling transport 另由 OPEN-027 管理 |
| CLOSED-P3C-024（原OPEN-024） | R6獨立驗收Accepted；P3-B+C Combined Gate Closed，證據見`PROJECT_HANDOFF.md` |
| CLOSED-026（原OPEN-026的P3部分） | P3-F於2026-08-26由獨立重新驗收Accepted（F-A1 remediation紅→綠重注入、PG16 217/0-skip、V12重算260/260＋violations=0＋130/130 fail-closed）；event對抗驗收先前已通過，immutable lineage、≤4,000行curation、future-leakage與Live Model Quality條件全數滿足。Transport rolling canary另列OPEN-027 |
| CLOSED-035 | P3 cleanup remediation Batch A–G 已於2026-08-27完成 current-worktree 的獨立 source、adversarial、完整 regression 與 real-PG acceptance；evidence 為 targeted 857、non-integration 1386/245 deselected、PG16 243/2 deselected/0 skipped |
| NEW-P2-01 — CLOSED | runtime role 原可直接 UPDATE `control_state` 的 authority blocker 已由 migration 0019、fixed-path `SECURITY DEFINER` control functions、ACL verifier 與 real-PG direct-update probe 關閉；direct update `42501`、unsafe resume `55000` |
| SUPERSEDED-021 | 舊cash/NAV關閉理由被P2-CUR證據取代 |

目前沒有剩餘的 P1/P2/P3 Gate blocker；OPEN-002～007、OPEN-027、OPEN-036～039 與其他 residual/future-phase
issue 仍需依各自的外部、營運或後續 phase 關閉條件處理，不能由本次 acceptance 擴張關閉。
