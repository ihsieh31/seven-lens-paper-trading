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

### OPEN-027 — Agnes transport可靠性不可由單次P3-F batch永久證明

- 嚴重度：High／P6 blocker；不是P3-F功能正確性blocker。
- 問題：V4～V8多次因`TIMEOUT`／`TRANSIENT`在首錯停止；反覆換split沒有修復provider可用性，單次全綠也不能
  推論未來可用性。
- 控制：ADR-033有界兩次retry、attempt cap 780、指數backoff＋jitter、三個連續exhausted cases circuit breaker，
  並把Live Model Quality與Provider Transport分開報告。V12批次（2026-08-26）first-attempt/eventual皆100%，
  為該批GREEN snapshot。
- 關閉：P6前另行授權的synthetic canary在rolling 7日、至少200 logical calls窗口達first-attempt≥95%、
  eventual≤3 attempts≥99%，且P6～P8持續監控；provider/model改版或rolling window跌破門檻即重開。

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

目前沒有剩餘的 P1/P2/P3 Gate blocker；OPEN-002～007、OPEN-027 與其他 residual/future-phase issue
仍需依各自的外部、營運或後續 phase 關閉條件處理，不能由本次 acceptance 擴張關閉。
