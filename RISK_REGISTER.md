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
| R-10 | 四分析員與 Bull/Bear 共享模型／來源，可能形成相關盲點與虛假共識 | 高 | 高 | 獨立 report、source/data overlap haircut、角色 ablation、簡單 baseline、unresolved conflict 保留 | Research/Portfolio | Open |
| R-11 | Future Analyst Plugin 的公開人物被錯誤模仿或背書 | 中 | 高 | plugin disabled；若重啟採 doctrine-only、日期／來源標記、禁止人格聲稱與人工 gate | Governance | Deferred（2026-08-21；不在 P3 主線） |
| R-12 | 缺乏付費基本面導致錯誤估值 | 中 | 高 | SEC/IR primary sources、多源核對、confidence haircut | Research | Open |
| R-13 | Live credential 誤用 | 低 | 極高 | 無 live adapter、Paper endpoint allowlist、啟動斷言 | Security | Open |
| R-14 | 過度擬合 graph、prompt、角色權重或 analysis-to-portfolio translation | 高 | 高 | frozen versions、held-out、ablation、walk-forward、簡單 baseline、變更 ADR | Validation | Open |
| R-15 | Alpaca真實endpoint格式／分頁／時間戳可能與fake不同 | 中 | 高 | P2-E嚴格解析、408/429/5xx→UNKNOWN、status pagination與duplicate-id recovery；真實GET-only smoke已執行；submit留P7 | Execution | Mitigated（P2-E read-only evidence，2026-08-17） |
| R-16 | Order 狀態轉移未逐筆寫入 P1 typed audit event registry，事件軌跡依賴狀態表 guard+append-only fills+control_commands | 低 | 中 | ADR-018 已記錄；如需 event 化須擴充封閉 registry（含 migration 與測試）；任何 order-path 變更時重審 | Execution | Accepted |
| R-25 | 無人值守卻沒有完整人工緊急控制入口 | 中 | 極高 | `pause_entries`、`cancel_open_orders`、`flatten_paper` 已有 application path；control shell CLI 留 P6/P7 | Ops | Deferred（CLI 尚未交付） |
| R-26 | Runtime誤用migration/schema-owner權限 | 中 | 極高 | 外建runtime login、bounded provisioning、startup privilege proof、owner DSN不進長駐process | Security/DB | Mitigated（ADR-016；PostgreSQL runtime-role adversarial tests） |
| R-17 | `SECURITY DEFINER`被search path／temp relation shadowing利用 | 中 | 極高 | fixed `pg_catalog, public, pg_temp`、schema qualification、撤銷PUBLIC CREATE/TEMP/EXECUTE | Security/DB | Mitigated（migration 0002；catalog＋shadowing tests） |
| R-18 | 任意或過大JSON污染權威event/audit ledger | 中 | 高 | typed payload registry、DB constraints、canonical JSON resource budgets | Domain/DB | Mitigated（ADR-016；unit＋PostgreSQL constraints） |
| R-19 | macOS Keychain只有部分native happy-path、沒有formal disposable adversarial smoke | 低 | 高 | production exact read fail closed；native smoke需專用namespace與另行授權 | Security/Ops | Deferred（獨立native-smoke gate；不得查詢現有真實item） |
| R-20 | 缺少coverage threshold與security-static／supply-chain lane | 中 | 中 | 現有locked quality與PostgreSQL gates不變；另定義工具、基線、成本、false-positive policy | Quality/Security | Deferred（獨立quality工作包，不阻塞P3-B+C） |
| R-21 | Broker 真值未知時本地宣告終態／watermark 過高丟棄合法事件 | 中 | 極高 | ADR-022：UNKNOWN 語意（deadline 後查無單不自行終態）、0007 broker_updated_at 清 NULL＋submitted_at lower bound、回放同值 DUPLICATE／同戳相異報錯、expire 取消路徑 transport 錯誤保留 CANCEL_PENDING、六個 review 狀態收斂 REVIEW_REQUIRED | Execution | Mitigated（ADR-022；TestPendingCancelCutoff/TestBrokerTerminalRecovery/TestDuplicateDelayedVisibility/close-history 4 案；0007 up/down＋PG integration 66 passed，2026-08-18） |
| R-22 | Flatten 在緊急時自行下單但未先暫停/取消/對帳，或重複 flatten 撞 client order id | 中 | 極高 | 六步順序 fail-closed（確認→paused→resolve→cancel→refresh→position 對帳不符即 abort）；`FlattenPriceProvider` seam；`control_state.flatten_generation` 同交易原子遞增為 target_version | Execution | Mitigated（ADR-022；tests/test_control_plane.py flatten 5 案，2026-08-18） |
| R-23 | 對未知/不可交易 symbol 建立 in-flight 狀態與 broker 呼叫 | 中 | 高 | submit 前 `get_asset` 資產閘 fail-closed（含 RISK_EXIT）；flatten 對全部部位預檢後才進 generation | Execution | Mitigated（ADR-022；TestAssetGate 3 案＋flatten asset abort，2026-08-18） |
| R-24 | 對帳證據只有 kind 無 detail，終態漏報無法稽核 | 中 | 中 | append-only `reconciliation_mismatches`（kind+detail+穩定 ordinal）；`latest()` 以 parent/child 一致性驗證重建 detail（mismatch_count/kinds/空 CLEAN 檢查，任一不一致拋 `PersistenceInvariantError`）；closed-history pass 補 UNKNOWN 等 | Execution | Mitigated（ADR-022 + P2-CUR-001；`test_reconciliation_and_ledger.py` + PG latest detail roundtrip/ordinal/corruption + append-only，2026-08-20；migration 0008） |
| R-27 | P2併發、checkpoint accounting、baseline authority、遷移與late-fill回歸風險 | 中 | 極高 | ADR-026：exclusive new-entry lock、full-ledger NAV + post-cutoff cash delta、genesis-vs-first-fill real-PG race、runtime baseline read-only、0008→0009 checksum-compatible provenance、typed expected failures、conflicting fill durable pause；ACC-001~009 regression | Execution/DB | Mitigated（ACC-001~009 Closed；exact SHA `488f170` remote run `32360443947`兩jobs success，2026-08-20） |
| R-28 | TradingAgents upstream/provider/data drift 破壞 P3 語意、可重播性或隔離邊界 | 中 | 高 | 固定 SHA/dependency lock、semantic-parity/record-replay、graph/prompt/model/provider versions、季度 drift review、free-text fallback fail closed | Research/Security | Open（P3） |
| R-29 | LLM Portfolio Manager 忽略 cash／持倉／open orders 或在被拒後反覆改寫直到繞過限制 | 高 | 極高 | 每次強制完整去識別化 snapshot；strict PortfolioProposal；deterministic Risk reason codes；只允許一次重申；第二次拒絕 NO_TRADE | Portfolio/Risk | Open（P3/P4） |
| R-30 | long/short 與同日交易放大 gross、borrow、turnover 或虧損追殺風險 | 中 | 極高 | long 100%／short 20%／gross 120%／net 40–100%、15 檔／單股 15%、borrow gate、40% turnover、same-day loss reason/evidence gate | Portfolio/Risk | Open（P4/Paper calibration） |
| R-31 | 單點壞價、延遲新聞或來源衝突被誤判為突發事件 | 高 | 極高 | 兩family各三個ordered fresh samples、official kind/family精確綁定、timestamp gate、DATA_CONFLICT、緊急graph只減風險 | Data/Risk | Mitigated（P3-B Accepted；P5/Paper持續重驗） |
| R-32 | Agnes／Muse reasoning 參數或 API 類型不一致造成未察覺降級 | 高 | 高 | capability negotiation、requested/effective audit、Chat/Responses adapters、一次 failover、schema/held-out eval | Research | Open（P3） |
| R-33 | 反思記憶無限增長、被壓縮器改寫事實或把 future outcome 洩漏給歷史 run | 中 | 高 | immutable raw audit、每日 lineage、週六專用 skill、LLM-visible ≤4,000 行、as-of/time-travel tests | Research/Data | Open（P3/P5） |
| R-34 | 將provider timeout誤判為程式錯誤而反覆換held-out split，或把單次全綠誤當永久可用性 | 高 | 高 | ADR-033：功能／transport Gate分離、每案最多2 retry、780 attempt cap、backoff+jitter、3-case circuit breaker、rolling 7日≥200 canary | Research/Ops | Open（P3-F quality待live evidence；transport為P6 blocker） |
