# Architecture Decision Log

本檔保留仍約束目前系統的決策與supersession索引。逐輪修訂全文保留於Git history；狀態以本檔、
`PROJECT_HANDOFF.md`與`PROGRESS.md`當前版本為準。

## Current decisions

### ADR-004 — Paper-only，程式不存在live path

- 狀態：Accepted
- 決策：broker endpoint只接受Alpaca Paper exact host；不提供live adapter、live URL或mode switch。
- 邊界：P7只授權supervised Paper，不會自動升級實盤。

### ADR-007 — 零付費資料是硬限制

- 狀態：Accepted
- 決策：只使用允許的免費公開內容／API；付費資料或PAYGO需另行核准。
- 失敗：額度、rights、freshness或availability不明時棄權，不以未授權來源補洞。

### ADR-008 — Codex Automations不負責盤中關鍵排程

- 狀態：Accepted
- 決策：關鍵排程由本機runtime/launchd與durable lease負責；Automations只可做非關鍵提醒或報告。

### ADR-010 — 本機模組化單體

- 狀態：Accepted
- 決策：單一Python package、PostgreSQL authority與清楚ports/adapters；不為未證實規模拆微服務。

### ADR-011 — Tavily多帳號採外部授權Gate

- 狀態：Accepted
- 決策：沒有可信外部授權時固定`SINGLE_ACCOUNT_UNVERIFIED`；本地reference不能升級權限。
- 若未來獲准：每帳號usage/reset/cooldown與全域hard cap分開稽核，不繞過rate limit。

### ADR-012 — PostgreSQL driver／migration／transaction

- 狀態：Accepted
- 決策：psycopg 3、checksummed SQL migrations、application transaction boundary；SQLite/mock不能
  支持PostgreSQL authority claim。

### ADR-013 — macOS Keychain secret boundary

- 狀態：Accepted；P1-C1 Closed
- 決策：固定typed `SecretRef`、Security.framework exact read-only query、UI disabled、2秒hard
  timeout；missing/duplicate/denied/malformed/backend failure全部fail closed，無env/argv/DB fallback。

### ADR-014 — Dependency-neutral telemetry

- 狀態：Accepted；P1-C2 Closed
- 決策：application只依賴typed recorder ports；bounded attributes、explicit context，telemetry失敗
  不得改變business state、transaction或audit。

### ADR-015 — Locked CI與zero-skip PostgreSQL

- 狀態：Accepted；P1 Core Gate Closed
- 決策：Ubuntu quality-unit＋digest-pinned PostgreSQL integration；required mode任一skip／非PG／
  major錯誤都失敗。

### ADR-016 — PostgreSQL owner/runtime authority分離

- 狀態：Accepted
- 決策：migration owner不進長駐process；externally-created runtime role只有精確table/function
  capability，startup必須驗證role flags、membership、ownership與ACL。
- `SECURITY DEFINER`固定`pg_catalog, public, pg_temp`並schema-qualify authoritative objects。

### ADR-017～026 — P2 Paper execution authority

- 狀態：Accepted；P2 Gate Closed
- 決策集合：closed state machines、deterministic client order id、durable UNKNOWN、broker truth優先、
  pause/reconciliation/control authority、append-only fills、cash checkpoint/full-ledger NAV、exclusive
  new-entry lock、runtime baseline read-only與checksum-compatible migrations。
- final evidence：commit `488f170`，exact-SHA CI `32360443947`成功。
- 保留邊界：真實submit留P7；WebSocket transport與operator CLI留P6/P7。

### ADR-028 — 完整TradingAgents提案鏈，deterministic Risk保留核准權

- 日期：2026-08-21
- 狀態：Accepted（架構）；P3-B+C Combined Gate Closed
- upstream：固定`TauricResearch/TradingAgents` commit
  `a33fd4c0f134485a43553a2c23a63cb14adbd88f`，保留Apache-2.0 attribution；不移植CLI、
  simulated exchange或order path。
- graph：Technical／Fundamentals／News／Sentiment → 兩輪Bull/Bear → Research Manager → Trader
  → 兩輪Aggressive/Conservative/Neutral Risk Debate → LLM Portfolio Manager。
- output：Portfolio Manager只能產生strict `PortfolioProposal`；不能核准risk、計算quantity、建立
  `OrderIntent`或呼叫broker。
- deterministic Risk：第一次拒絕回傳reason codes與remaining limits，只允許一次重申；第二次
  拒絕固定`NO_TRADE`。
- portfolio limits：long gross≤100%、short gross≤20%、total gross≤120%、net 40%～100%、
  long+short最多15檔、單股absolute weight≤15%、normal turnover≤40%。
- schedule：開盤後60分鐘全部持倉＋最多12候選；收盤前90分鐘全部持倉＋最多5候選；正常deadline
  15分鐘、verified emergency 3分鐘。
- event：價格需兩family各三個fresh ordered samples；official-primary news只接受精確kind/family；
  conflict/stale/unverified不啟動緊急LLM。
- memory：immutable raw records；P3-F才實作每日reflection與每週≤4,000行LLM-visible curation。
- Future Analyst Plugin維持disabled，不進critical path。

### ADR-029 — P3-B與P3-C合併實作、獨立驗收

- 日期：2026-08-21
- 狀態：Accepted（工作包結構）；P3-B與P3-C均Accepted，Combined Gate Closed
- P3-B：source/evidence/time、CAS、GET source boundaries、input assembly、event verification、
  PostgreSQL evidence authority。
- P3-C：capability-minimal provider、scripted fake、四分析員至Trader、run/stage persistence。
- 兩個子Gate必須分別Accepted；implementation、commit、push或CI不能自行關閉Combined Gate。
- P3-C止於`TraderPlan`；P3-D/E/F與P4不得因合併提前取得authority。

## Superseded／historical index

| ADR | 狀態 | 取代關係／保留內容 |
|---|---|---|
| ADR-001 | Historical | 新專案基線已完成 |
| ADR-002／003 | Superseded by ADR-028 | 七人主線改為disabled Future Analyst Plugin；doctrine-only原則保留 |
| ADR-005 | Amended by ADR-028 | LLM仍無核准／下單權，但可產生strict portfolio proposal |
| ADR-006 | Superseded by ADR-028 | provider isolation與無broker authority保留 |
| ADR-009 | Superseded by ADR-028 | long-only／禁止同日交易被long-short與evidence-based same-day規則取代 |
| ADR-027 | Superseded by ADR-028 | 固定upstream、隔離與Future Plugin邊界保留；「只到Trader」被取代 |

## Change control

新的決策只有在會改變scope、authority、external dependency、risk limit、gate或不可逆資料語意時
新增ADR。一般修復寫入`WORKLOG.md`與tests即可；不得為了保留每次對話而複製整段歷史。
