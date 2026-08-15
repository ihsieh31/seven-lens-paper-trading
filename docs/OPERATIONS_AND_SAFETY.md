# 無人值守營運與安全規格

## 1. 安全不變量

以下 invariants 任何時候都必須成立，且以 property/integration tests 證明：

1. 程式碼中不存在 Alpaca live adapter 或 live endpoint。
2. LLM process 無法讀 broker secrets、寫 order tables 或呼叫 broker。
3. 未完成 broker reconciliation 時不可新增風險。
4. 一個 `OrderIntent` 最多對應一個有效 `client_order_id`。
5. timeout、重啟、重複訊息和亂序事件不會增加重複委託。
6. stale/invalid/missing data 不會被預設值轉成可交易信號。
7. 過期 research/target 不可在下一窗口重用。
8. hard limits 只能由版本化設定和明確控制流程變更，不能由 LLM 變更。
9. 同日 alpha 反轉不會造成 round trip。
10. 告警失敗不會讓 fail-closed 狀態自動恢復。
11. Telemetry 不是權威 audit；recorder、diagnostic 或 exporter 故障不得改變 business result、transaction、rollback、retry 或 fail-closed 狀態。

## 2. 啟動順序

每次 process start/restart：

1. 驗證 binary/config/schema migration/version。
2. 斷言 broker base URL 為 Paper allowlist。
3. 以 capability-scoped provider exact resolve 該 process 所需 Keychain refs；任一 missing、ambiguous、denied、locked、timeout、malformed 或 backend unavailable 都停止啟動，不回退到 env、argv、DB 或另一 provider。
4. 取得 singleton supervisor lease。
5. 從 Alpaca REST 拉 account、positions、open/closed orders、recent fills。
6. 與 ledger 對帳；有 mismatch 則 `PAUSED_RECONCILIATION`。
7. 檢查 control flags、回撤與資料 freshness。
8. 只在全部健康且未錯過 window 時啟動 job。

不得為了「補做今天的工作」在 deadline 後執行原交易。

## 3. 健康狀態

| 狀態 | 新增部位 | 減風險 | 取消單 | 條件 |
|---|---|---|---|---|
| HEALTHY | 允許 | 允許 | 允許 | 全部檢查通過 |
| DEGRADED_RESEARCH | 禁止或限既有 target | 允許 | 允許 | 部分來源／模型失敗 |
| PAUSED_ENTRIES | 禁止 | 允許 | 允許 | 手動或風控暫停 |
| PAUSED_RECONCILIATION | 禁止 | 僅經核對 | 允許 | broker/ledger 不一致 |
| EMERGENCY | 禁止 | Paper flatten 可用 | 允許 | Critical incident |
| OFFLINE | 禁止 | 禁止 | 不保證 | 無服務／無 broker 連線 |

## 4. 故障矩陣

| 故障 | 自動行為 | 恢復條件 |
|---|---|---|
| Tavily 單帳號額度用盡／429 | 已授權 pool 才切到另一健康帳號；否則停止 discovery | cooldown/reset 或 Tavily 授權確認 |
| Tavily pool 授權不明／被撤回 | 立即降為單帳號模式，其他 keys disabled | 書面或後台授權重新驗證 |
| LLM timeout/429/schema error | 該 assessment `INVALID`；不自由文字 fallback | 新 run 或窗口內受限重試成功 |
| Primary source 無法讀 | 降低 coverage；material claim 不成立 | 來源恢復或替代 primary source |
| Quote stale/spread 超限 | 該 symbol 不送單 | 新 quote 通過門檻且仍在窗口 |
| Alpaca submit timeout | intent `UNKNOWN`；依 client id 查單 | 查明存在、拒絕或明確不存在 |
| WebSocket 斷線 | 暫停提交、REST reconcile、重連 | snapshot 與 ledger 一致 |
| DB 斷線 | 不提交 outbox；程序保持安全停止 | DB 恢復、lease/reconcile 完成 |
| 磁碟接近滿載 | 停止新 research，保留交易／audit 空間 | 空間回復並驗證 audit 完整 |
| 程序 crash | launchd 重啟；先 reconcile | 啟動檢查全過 |
| 錯過交易窗口 | job `EXPIRED`，不追單 | 下一個正式窗口 |
| 告警發送失敗 | 本地 critical state 照常生效、重試告警 | 通道恢復與 delivery receipt |
| Keychain secret missing／ambiguous／malformed | 停止該 process 啟動，不回傳部分 secret bundle | 由人工修正 exact service/account item 後重新啟動並讓全部 startup checks 通過 |
| Keychain denied／locked／interaction required | 停止啟動；不得彈出 UI、重試猜測或使用 fallback | 使用者登入並解鎖 Keychain、修正權限後明確重新啟動 |
| Keychain lookup timeout／worker crash／backend unavailable | 終止 spawned lookup worker、關閉 IPC，停止啟動 | backend 恢復，確認沒有殘留 child process，再由全新啟動重新檢查 |
| Metrics／trace recorder failure | 保留原 business 成功或 typed failure；增加 process-local drop count並產生固定、無 detail diagnostic，不重試 business transaction | recorder 恢復後 best-effort輸出 bounded drop metric；PostgreSQL audit仍是權威 |

## 5. 委託前檢查順序

每筆 order submit 前以同一 snapshot 驗證：

- system health = HEALTHY 或允許的減風險狀態；
- run/target/risk decision 未過期且 hash 相符；
- account id、Paper endpoint、buying power、cash；
- broker position/open orders 與 ledger 對齊；
- symbol active/tradable、非 halt；
- 市場為 regular session 且在本窗口內；
- quote age、spread、price collar；
- lot/day-trade restriction；
- post-trade gross/net/name/sector/cluster/turnover/ADV/drawdown limits；
- deterministic client id 尚無有效 broker order。

任一項 false 就拒絕，保存 machine-readable reason code。

## 6. Reconciliation

### 6.1 觸發點

- 每次啟動、重啟、WebSocket reconnect；
- 每個 execution window 前後；
- submit timeout；
- 每 5 分鐘輕量核對 open orders，每 30 分鐘完整核對（交易時段）；
- 收盤後最終核對。

### 6.2 權威順序

券商對 broker account/orders/fills/positions 是外部事實來源；本地 ledger 保存策略語意與可稽核歷史。兩者不一致時不得直接覆寫：產生 mismatch、分類原因、以可重放事件修復，再保存 before/after snapshot。

### 6.3 必查項目

- broker order 是否都有 local mapping；
- local acknowledged order 是否存在於 broker；
- fill execution id 是否重複或缺漏；
- position qty 是否等於 fills/corporate actions 後結果；
- cash、buying power、NAV 在 tolerance 內；
- canceled/rejected/expired order 不再被當作 working；
- partial fill remaining quantity 與 target delta 一致。

## 7. 緊急控制與人工介入

正常交易不需人工批准，但需提供：

- `pause_entries --reason`：立即停止新委託與加碼。
- `cancel_open_orders --paper-account <id>`：只取消 Paper orders。
- `flatten_paper --dry-run`，再明確確認執行；不得一鍵誤觸。
- `resume_entries --incident <id>`：只有 health/reconcile/report 通過才可恢復。
- 只讀 status dashboard 即使 research pipeline 故障仍應可用。

控制面本機綁定、強驗證、完整 audit；不得直接暴露公網。

## 8. 告警

免費通道採可插拔 AlertPort（Telegram bot、Discord webhook 或 email 擇一）。

### CRITICAL

- endpoint/account 不是 Paper；
- reconciliation mismatch 未能自動分類；
- duplicate order risk；
- 硬風控 breach；
- ledger write/audit failure；
- Paper account 出現本系統無法解釋的委託。

動作：`pause_entries`，持續重送告警，不等使用者回覆才生效。

### HIGH

- broker/data outage 超過窗口容忍；
- source injection、Tavily global/per-account budget exhaustion 或 account compliance 異常；
- daily drawdown stop；
- supervisor/heartbeat 中斷。

### WARN/INFO

- 個別 doctrine abstain、延遲、較高 spread、每日報告。

## 9. Runbook

### Broker order 不明

1. pause entries。
2. 用 client order id 查 REST，不重送。
3. 拉 open/closed orders 和 fills。
4. 對帳並決定 ACK/REJECT/UNKNOWN。
5. 超過窗口則取消或維持已成交結果，不追 target。
6. 建 incident，保存 API request id、時間和 snapshots。

### Position mismatch

1. pause entries，取消可能擴大風險的 working orders。
2. 重拉 broker snapshots。
3. 檢查 partial fills、corporate action、手動 Paper 操作、duplicate events。
4. 以 repair event 修 ledger；不可直接改 row 掩蓋歷史。
5. 全量 reconciliation 通過才可 resume。

### Mac 重啟／睡眠

1. launchd 啟動 supervisor。
2. 判斷錯過哪些 job/window，全部標 EXPIRED。
3. reconciliation。
4. 若仍在下一正式窗口且資料與 target 是該窗口新產物，才執行；不可使用過期 target。

## 10. 備份與恢復

- PostgreSQL 每日加密 logical backup，重要交易事件後 WAL/等價增量保護。
- manifests、configs、doctrine versions、reports 和 DB backup 都在本機第二儲存位置；不含 plaintext secrets。
- 每月 restore drill 到隔離 DB，驗證 row counts、hash chain 和 reconciliation replay。
- 研究 raw cache 可重抓；order/fill/audit ledger 不可遺失。

## 11. 安全測試

- secret scan、dependency audit、SBOM、license scan；
- prompt injection corpus 和 tool-boundary tests；
- endpoint allowlist mutation tests；
- fuzz order event sequence、idempotency property tests；
- kill -9、network partition、DB failure、disk full、clock shift；
- DST、half-day、holiday、early close；
- 任何測試絕不使用真實 live credential。

## 12. Codex 自動化隔離

Codex scheduled tasks 只能在獨立 worktree 或只讀模式執行測試、報告和蒸餾候選更新。它不能自動合併到當日交易 runtime，也不能接觸 broker credential。production artifact 只能由通過 release gate 的固定 commit/dependency lock 建置。

## 13. P1 CI 與 clean-machine gate

- GitHub Actions 只建立 Ubuntu `quality-unit` 與 `postgres-integration`；workflow permission 僅
  `contents: read`，checkout 不保留 credential，沒有 repository secret、OIDC、deploy token、
  `pull_request_target` 或 hosted macOS job。
- 第三方 actions 固定 reviewed release 的完整 commit SHA；uv 固定版本。只保存 uv
  download/build cache，不 cache `.venv`、database volume、Keychain 或 secret material。
- PostgreSQL job 與本機腳本只使用 digest-pinned official PostgreSQL 16 Alpine image、明顯 fake
  credentials 與 disposable data。required mode 會在 collection 前驗證 psycopg、URL、連線與 server
  major，並把任何 integration skip 轉成 session failure。
- 本機 container 使用 random localhost port 與 tmpfs；success、test failure、readiness failure 或
  interrupt 都只在 container ID、exact name 與 ownership label 相符後清理該 container。禁止
  `docker prune` 或停止其他 container。
- `uv` 是 clean-machine 唯一 bootstrap prerequisite；verification script 不讀 `.env`、Keychain 或
  credential。workflow 檔存在只代表待啟用實作，不能替代獨立 repository 的遠端 required-job 證據。
