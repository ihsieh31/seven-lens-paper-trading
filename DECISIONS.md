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

### ADR-030 — P3-D per-symbol bundle、獨立proposal state與最多一次重申

- 日期：2026-08-22
- 狀態：Accepted（架構）；P3-D Gate獨立驗收，本ADR不是Gate證據
- per-symbol research：P3-C `AnalysisPipeline.run(..., symbol)`在每個symbol完成後留下COMPLETE的
  child run authority；P3-D以deterministic serial coordinator把parent `AnalysisInput`依
  `focus_symbols`順序展開為child inputs（focus縮成恰好該symbol，universe/snapshot/packet/
  data refs/as-of/window/deadline完全不變），全部child COMPLETE後才依parent順序join成不可混用的
  `ResearchBundle`。child run/input ID由parent input ID＋canonical symbol以不同domain tag
  deterministic衍生。
- 獨立proposal state machine：`PLANNED -> RISK_DEBATE -> PROPOSAL -> COMPLETE`，
  非終態可轉`INVALID|EXPIRED`；不把新stage塞入migration `0010`。
- `ProposalContext`：attempt精確1|2、綁bundle id/hash與當時full sanitized snapshot hash；
  attempt 2必須同時有previous context、superseded proposal與typed `RiskRejectionFeedback`
  （沿用P3-A contract），只可刷新snapshot/remaining limits/feedback，不可改research bundle、
  universe、window或evidence。時序固定：
  initial context/proposal < Risk review <= refreshed snapshot/context <= deadline。
- Risk Debate固定兩輪、AGGRESSIVE/CONSERVATIVE/NEUTRAL各恰好一次，citation屬frozen bundle set；
  六個argument完整persist前不得呼叫Portfolio Manager。
- 重申：只能由typed rejection＋refreshed snapshot啟動一次PM_RETRY；attempt 2精確supersede
  attempt 1；相同attempt-2 same-hash僅bounded冪等，different hash、第二個attempt 2或第三次proposal
  永遠拒絕（DB以UNIQUE(superseded_proposal_id)與UNIQUE(context_id)獨立強制）。
- P4保留deterministic hard-risk approval權；P3-D不產生gross/net/turnover/borrow approval、
  quantity、`TargetPortfolio`或`OrderIntent`，也不關閉R-29的P4部分。
- evolved `PortfolioProposal`落於新的`analysis/proposal_contracts.py`並綁context/bundle
  identity與hash；P3-A `contracts.py`的提案契約與golden bundle證據原樣保留。

### ADR-031 — P3-E固定Agnes 2.5 Flash單一路由

- 日期：2026-08-24
- 狀態：Accepted（使用者route決策）；P3-E Gate Accepted
- 所有P3-C/D logical roles固定使用`agnes-2.5-flash`，API flavor固定Chat Completions，exact
  endpoint policy固定`https://apihub.agnes-ai.com/v1/chat/completions`；runtime不可覆寫scheme、host、
  path、model或policy，也不呼叫model discovery自動升權。
- fallback固定為none，automatic retry固定停用；未來新增provider/model必須有新的使用者決策與獨立gate，
  不因本ADR取得fallback authority。
- 內部`reasoning_requested=MAX`只表示policy意圖；官方文件及authorized live observation尚未證明
  Agnes對應參數，因此不傳未知reasoning參數並記`reasoning_effective=UNKNOWN`。
- Agnes privacy不得標示ZDR或不訓練。使用者已明確了解此邊界，並批准正常Paper分析傳送完整
  portfolio、order content及verified source material；API key、Authorization header、account ID、
  broker order identifier永遠禁止外送。本次E-live另縮限為六個synthetic／de-identified案例、最多六次
  POST、無automatic retry／fallback／model discovery、無費用上限；使用者其後確認rotation並允許必要的
  remediation案例。最終六案例6/6成功；完整無payload證據見`docs/P3E_LIVE_EVIDENCE_2026-08-24.json`。
- secret identity固定Keychain generic password service
  `seven-lens.paper-trading.agnes.api-key`、account`primary`。repository、env、argv、audit、telemetry與
  prompt均不得保存credential；聊天中出現的credential視為已暴露，必須rotation後以互動式Keychain輸入。

### ADR-032 — P3-F immutable reflection、bounded memory與eval治理

- 日期：2026-08-24
- 狀態：Accepted（架構）；P3-F live quality evidence pending（現行split V12）
- daily reflection與correction只追加；correction以typed supersedes lineage表示，raw row／bytes／hash不可更新。
- `available_at`與requested cutoff雙重限制point-in-time可見性；memory是可丟棄derived context，永遠不是
  proposal、Risk、order或broker authority。
- weekly `MemoryArtifact`固定最多4,000行、512 entries、512 KiB；line count、importance、dedup、quota、
  hash與selection均由deterministic policy重算，不信model自報。
- artifact bytes使用exact CAS readback驗hash／size後，透過append-only promotion history維持單一current；
  candidate失敗不改current，fallback只接受cutoff安全且完整性仍有效的previous artifact，否則注入none。
- memory-curator使用獨立最小權限PostgreSQL role；無raw mutation、proposal/source publish、secret、broker、
  order、control、owner DDL或trigger authority。
- eval split／case／fixture／report hash immutable；held-out在final evaluation前封閉，threshold或case變更需新
  split version與全量重跑。P3-F real-provider eval不繼承P3-E授權。

### ADR-033 — P3-F功能品質與Provider transport分離驗收

- 日期：2026-08-26
- 狀態：Accepted（使用者Gate重構決策）；V10 live已於12 attempts因`RESPONSE_CONTRACT`停止並消耗；
  尚無通過的live quality evidence，現行split為V12（V12已建立並於2026-08-26完成260/260 live batch；V1～V11 immutable）
- P3-E production transport仍維持單次呼叫、無SDK hidden retry與零fallback；P3-F eval orchestrator才可在
  本批exact authorization內重送同一個synthetic、無外部副作用的logical case。每案最多三次attempt
  （初次＋兩次retry），只限`TIMEOUT`／`TRANSIENT`／`RATE_LIMIT`；`AUTH`、`CONFIG`、`PERMANENT`、
  `PROTOCOL`、`SCHEMA`、`OVERSIZE`、`DEADLINE`、`AUDIT`、`RESPONSE_CONTRACT`與未知錯誤不重試。
- retry採2秒、4秒指數backoff加0～999ms deterministic jitter；連續三個logical cases各自耗盡三次attempt
  即開circuit breaker。260個logical requests的總attempt cap固定780，fallback仍為0。每次attempt以當下
  wall clock建立獨立180秒deadline，不得沿用batch起點造成後續request過期。
- P3-F拆為三個判定面：Offline Correctness Gate維持全部deterministic／安全／PG16門檻100%；Live Model
  Quality Gate要求至少250/260 strict completions、completed cases正確率至少98%、response-contract違規0、
  130/130 invalid／ambiguous pre-network fail-closed；Provider Transport Gate要求first-attempt成功率至少95%、
  三次內eventual成功率至少99%，並完整列出attempt、retry、timeout、latency與circuit-breaker分母。
- Provider Transport是可變營運狀態，不是永久程式正確性。它不再單獨阻止P3-F功能Gate，但未達標時禁止開始
  P6 Shadow；P6前須以另行授權的synthetic canary，在rolling 7日且至少200個logical calls窗口重新通過，
  P6～P8持續監控。單次260/260成功不得宣稱provider未來不會失敗。
- threshold與authorization/evidence schema變更使用新的source-only split（V10已消耗，現行為
  `p3f-synthetic-v11`）；V1～V10保持immutable historical evidence，不覆寫、不重送。任何新split的live
  attempt仍需使用者對exact model、payload、260／780 caps、timeout、privacy、cost、retry與stop scope
  重新明確授權。2026-08-26授權remediation：P3-F live parser升為`p3f-strict-route-decision-v5`，僅新增
  P3-E live路徑已驗收的單一exact JSON code fence normalization（恰好兩個marker、```json前綴＋換行後綴），
  其餘fence形狀與所有語意檢查維持fail closed。
- 依據：[AWS Well-Architected retry guidance](https://docs.aws.amazon.com/wellarchitected/2023-04-10/framework/rel_mitigate_interaction_failure_limit_retries.html)
  建議對idempotent transient failure採有上限的exponential backoff＋jitter，且只重試可恢復錯誤；
  [Azure Retry](https://learn.microsoft.com/en-us/azure/architecture/patterns/retry)與
  [Circuit Breaker](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker) guidance要求有限
  重試並在持續失敗時停止呼叫。這些是設計依據，不是Agnes可用性證明。

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
