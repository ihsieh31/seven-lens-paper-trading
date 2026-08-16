# Risk Register

## 狀態與證據規則

- `Open`：風險存在且控制尚未達驗收；必須保留 owner、下一個 gate與關閉條件。
- `Mitigated`：已實作控制並有可重現驗收證據，但風險仍需在相關變更時回歸測試。
- `Accepted`：在明確理由、owner與重審日期／觸發條件下接受 residual risk；不得只因修復困難使用。
- `Deferred`：確認有改善空間，但不屬目前 gate或缺少另行授權；必須寫明 target gate與不提前實作理由。
- `Closed`：風險來源已移除或已不再適用；需保存證據與日期。`Closed`不等於刪除歷史。

每次狀態變更必須引用source/test/ADR或外部證據，並在控制或依賴、OS、PostgreSQL major、composition
boundary、repository visibility／CI policy改變時重審。沒有真實平台證據時，不得把fake test寫成native或
remote acceptance。

| ID | 風險 | 可能性 | 衝擊 | 主要控制 | Owner | 狀態 |
|---|---|---:|---:|---|---|---|
| R-01 | LLM 幻覺或捏造來源 | 高 | 高 | 引用 schema、evidence verifier、無來源即棄權 | Research | Open |
| R-02 | LLM prompt injection 透過網頁進入 | 高 | 高 | 原始內容視為不可信資料、隔離工具、指令／內容分層 | Security | Open |
| R-03 | 未來資料滲入歷史回測 | 高 | 極高 | `available_at`、as-of query、time-travel tests | Data | Open |
| R-04 | 重複下單 | 中 | 極高 | deterministic `client_order_id`、outbox、查單後重試 | Execution | Open |
| R-05 | Broker/WebSocket 斷線後帳務漂移 | 中 | 極高 | REST reconciliation、啟動／重連／交易前後核對 | Execution | Open |
| R-06 | Paper fill 過度樂觀 | 高 | 高 | 經濟成交模擬、price collar、ADV/turnover 限制 | Validation | Open |
| R-07 | X／公開資料缺漏或遭移除 | 高 | 中 | coverage metadata、cache、棄權、非即時依賴 | Data | Open |
| R-08 | Tavily 額度用盡或七帳號彙總不被允許 | 中 | 高 | 合規 Gate；未確認只用 1,000；授權後 7,000 全域／每帳號 ledger；硬拒絕 PAYGO | Data | Open |
| R-09 | 單台 Mac 斷電／睡眠 | 中 | 高 | launchd、heartbeat、missed-window NO_TRADE | Ops | Open |
| R-10 | 七套 doctrine 仍可能形成共同盲點或風格集中 | 高 | 高 | 相關性、sector cap、base-rate／macro／valuation／forensic 交叉反駁 | Portfolio | Open |
| R-11 | 公開人物被錯誤模仿或背書 | 中 | 高 | doctrine-only、日期／來源標記、禁止人格聲稱 | Governance | Open |
| R-12 | 缺乏付費基本面導致錯誤估值 | 中 | 高 | SEC/IR primary sources、多源核對、confidence haircut | Research | Open |
| R-13 | Live credential 誤用 | 低 | 極高 | 無 live adapter、Paper endpoint allowlist、啟動斷言 | Security | Open |
| R-14 | 過度擬合七人權重 | 高 | 高 | blinded eval、ablation、walk-forward、權重正則化 | Validation | Open |
| R-15 | 無人值守卻沒有人工緊急控制 | 中 | 極高 | `pause_entries`、`cancel_open_orders`、`flatten_paper`、kill switch | Ops | Open |
| R-16 | Runtime誤用migration/schema-owner權限 | 中 | 極高 | 外建runtime login、bounded provisioning、startup privilege proof、owner DSN不進長駐process | Security/DB | Mitigated（ADR-016；PostgreSQL runtime-role adversarial tests） |
| R-17 | `SECURITY DEFINER`被search path／temp relation shadowing利用 | 中 | 極高 | fixed `pg_catalog, public, pg_temp`、schema qualification、撤銷PUBLIC CREATE/TEMP/EXECUTE | Security/DB | Mitigated（migration 0002；catalog＋shadowing tests） |
| R-18 | 任意或過大JSON污染權威event/audit ledger | 中 | 高 | typed payload registry、DB constraints、canonical JSON resource budgets | Domain/DB | Mitigated（ADR-016；unit＋PostgreSQL constraints） |
| R-19 | macOS Keychain只有fake contract、沒有native disposable smoke evidence | 低 | 高 | production exact read fail closed；native smoke需專用namespace與另行授權 | Security/Ops | Deferred（P2 composition前重審；不得查詢現有真實item） |
| R-20 | 缺少coverage threshold與security-static／supply-chain lane | 中 | 中 | 現有locked quality與PostgreSQL gates不變；另定義工具、基線、成本、false-positive policy | Quality/Security | Deferred（獨立quality工作包，不阻塞本次P1 authority修復） |
