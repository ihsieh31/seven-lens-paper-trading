# TradingAgents 分析核心 Paper Trading 系統：第一性原理主企劃書

版本：0.5
日期：2026-08-27
狀態：P0～P3 Closed；P4 In progress（P4-A／P4-B已獨立驗收並Accepted／Closed；P4-C～F未開始）；P5～P8 Not started

## 1. 專案定義

本專案要建立的不是「會自動按買賣鍵的聊天機器人」，而是一套可重現、可追溯、遇到未知狀況會停止新增風險的自動投資作業系統。它每天先完成跨股票篩選，於開盤後 60 分鐘分析全部持倉與最多 12 個新候選；收盤前 90 分鐘只重評全部持倉與最多 5 個新候選。突發事件另走經二次資料確認的緊急分析流程。

系統只做 Alpaca Paper Trading，只供本人使用。第一版不保留任何實盤 adapter、實盤 URL、實盤 credential 欄位或切換選項。

### 1.1 成功的必要條件

成功不是報酬率單一數字，而是同時滿足：

1. **研究可稽核**：每個關鍵判斷能追到發布時間、來源 URL、抓取時間和引用片段。
2. **沒有前視偏差**：任何歷史決策只能看到當時已公開且系統當時可取得的資料。
3. **決策可重現**：同一資料快照、版本、設定與模型輸出能還原同一目標投資組合。
4. **執行一致**：斷線、timeout、重啟或重複事件不會造成重複下單。
5. **風控獨立**：模型認為再有把握，也無法越過部位、集中度、流動性、回撤和資料新鮮度限制。
6. **失敗關閉**：資料缺失、schema 錯誤、模型 timeout/429、帳務不一致或超過窗口時，結果為 `INVALID/NO_TRADE`，不是猜測或補單。
7. **營運可觀測**：每個 run 有狀態、輸入、輸出、延遲、成本、錯誤、訂單和 reconciliation 紀錄。

### 1.2 明確不做

- 不把當沖當成主要策略，但不設定最短持有期；符合已驗證理由與風控時允許同日減倉、平倉或獲利短線退出。
- 不做 options、crypto、OTC、ETN、槓桿或反向 ETF，也不做盤前盤後交易；允許 Alpaca 標示可放空且符合借券條件的標的。
- 不把公開人物寫成可冒充本人的聊天角色；七人方法論蒸餾不再是主線必要條件。
- 不付費購買 X API、研究訂閱、市場資料或雲端服務。
- 不繞過登入、付費牆、robots 或存取限制。
- 不以 Codex 桌面排程作為市場時鐘或唯一 broker workflow engine。
- 不因 Paper 獲利就推論實盤可行。

## 2. 從第一性原理拆解

一筆安全的自動交易必須依序回答六個不同問題；任何一層都不能由下一層倒推補齊：

1. **世界狀態是否可知？** 資料是否完整、新鮮、合法取得、時間一致？
2. **公司是否值得研究？** 先用廉價、確定性的指標把大股票池縮小。
3. **投資論點是否成立？** 技術、基本面、新聞與情緒分析員先獨立研究，Bull/Bear 研究員辯論，再由 Research Manager 與 Trader 形成有界研究結論。
4. **應該提出什麼組合要求？** Aggressive/Conservative/Neutral Risk Debate 與 LLM Portfolio Manager 在看過完整持倉、現金、buying power、未成交單、當日成交和剩餘限制後，只能產生結構化 `PortfolioProposal`。
5. **現在能不能下單？** deterministic Risk Engine 審核 proposal；若駁回，只允許 Portfolio Manager 依 reason codes 與 remaining limits 重申一次，第二次仍不通過即 `NO_TRADE`。
6. **委託後真實狀態為何？** 以 broker 回報與 reconciliation 更新帳本，不以模型或本地預期認定成交。

因此資料、研究、LLM 組合提案、確定性風控、執行、帳務是不同 bounded context。LLM 可以提出目標比例，但沒有核准或下單權。

## 3. 投資任務與預設宇宙

### 3.1 投資目標

以中期論點為主，在明確曝險上限下尋找風險調整後超額報酬；不設最短持有期，以便在論點失效、重大事件或硬風控觸發時及時退出。主要來源包括：

- 市場尚未充分定價的供應鏈瓶頸與第二階影響；
- 可被公開財報與產業資料驗證的主題性需求變化；
- 故事、數字和估值間的不一致；
- 被忽視的治理、會計、融資或商業模式風險；
- 宏觀流動性、財政、利率、能源和跨資產環境對個股的傳導。

### 3.2 股票宇宙

每月從 Alpaca `active + tradable` 美國資產建立版本化 universe snapshot，再套用：

- ordinary common stock；第一版排除全部ETF；
- 價格至少 USD 5；
- 20 日平均美元成交額至少 USD 20M；
- 至少 252 個有效交易日價格資料；
- 排除 OTC、preferred、warrant、unit、closed-end fund、ETN、槓桿／反向 ETF；
- 排除交易暫停或資料品質狀態不明者；已確認 `forward_split`／`reverse_split` 的標的進入
  `ENTRY_BLOCKED`，不送交分析委員，直到事件生效後且 authority/security-master 對帳乾淨；
- 第一版只用整股，候選價格不可使最小部位無法合理配置。

上述是初始校準值，不是假定永遠正確；任何調整須走 ADR、walk-forward 和 paper evidence。

### 3.3 候選漏斗

為避免對數千股票逐一呼叫 LLM：

| 階段 | 最大數量 | 方法 | 目的 |
|---|---:|---|---|
| Universe | 約 2,000–4,000 | 資產、價格、流動性硬篩 | 移除不可交易標的 |
| Quant screen | 100 | 趨勢、品質、估值 proxy、事件、風險 | 找值得取證者 |
| Evidence screen | 30 | SEC/IR/官方宏觀與交易所來源＋Tavily/GDELT discovery | 確認有足夠 point-in-time 資料且無拆／合股禁止狀態 |
| Full analysis | 12 | 四分析員 + Bull/Bear + Research Manager + Trader | 深度研究與結構化決策 |
| Portfolio | 最多 15 檔long | LLM proposal + deterministic risk | P4 long-only profile；short proposal一律typed拒絕 |

Quant screen固定使用ADR-039 `p4-factor-v1`：Trend 35%（126→21、252→21各半）、Quality 25%
（ROA、CFO/assets、accrual各三分之一）、Value 15%（earnings yield、FCF yield各半）、Low Risk 25%
（63-session volatility與252-session max drawdown反向各半）。Event只作hard evidence/quarantine gate，不進分數；
九項資料缺一即不排名。5/95 nearest-rank winsorization、midrank percentile與stable security ID tie-break均不可由模型改寫。

既有持倉永遠進入 evidence refresh；若資料不足，允許減倉或退出，不允許因缺資料自動加碼。

## 4. TradingAgents-style 分析核心

P3 採用 `TauricResearch/TradingAgents` 固定 commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f` 的完整研究／決策 graph，直接移植所需模組並針對本專案改造；不 fork、不移植 CLI、simulated exchange 或下單路徑。複製檔案須保留 Apache-2.0 LICENSE、attribution／NOTICE（若上游提供）並標示修改：

| 角色 | 輸入 | 有界輸出 |
|---|---|---|
| Technical / Market Analyst | point-in-time bars、技術指標、市場 regime | 趨勢、動能、波動、關鍵價位與資料缺口 |
| Fundamentals Analyst | filing、財務指標、公司輪廓、估值 inputs | 財務品質、成長、估值、red flags 與假設 |
| News Analyst | 公司、產業、全球與宏觀事件 | 事件、因果路徑、時效、催化劑與反證 |
| Sentiment Analyst | 經允許且有時間戳的新聞／社群訊號 | direction、intensity、confidence、來源分歧 |
| Bull / Bear Researchers | 四份 analyst reports | 有限輪次正反辯論與未解衝突 |
| Research Manager | analyst reports + debate state | 結構化研究結論與證據缺口 |
| Trader | 以上全部 | 結構化 trader plan；不是券商委託 |
| Aggressive / Conservative / Neutral | trader plan、完整研究與 portfolio snapshot | 兩輪有界 risk debate |
| Portfolio Manager | 全部研究、兩種 debate、完整 portfolio/account snapshot、記憶 | 結構化 `PortfolioProposal`；不是券商委託 |

Bull/Bear 與 Risk Debate 都固定兩輪。P3契約仍可表達long/short target weights，但使用者核准的P4第一版固定
`short_enabled=false`，任何short request都由deterministic Risk typed拒絕；P2 execution邊界不變。

### 4.1 分析協議

1. **Snapshot freeze**：同一 run 的分析員只讀相同 `as_of` 與 immutable source/data snapshot。
2. **Independent analyst pass**：四個 analyst report 分開保存；任一缺失不可由其他角色猜補。
3. **Bounded debate**：Bull/Bear 與三方 Risk Debate 都固定兩輪，直接標記引用、衝突與無法解決的假設。
4. **Evidence verification**：每個 material claim 必須映射 source/data reference；無法支持則移除或整體 `INVALID/ABSTAIN`。
5. **Structured boundary**：所有角色輸出都必須通過 versioned schema；Portfolio Manager 禁止輸出長篇建議文字，只能輸出機器可驗證的買賣要求。
6. **Full portfolio context**：每次 Portfolio Manager 呼叫都必須取得去識別化的 NAV、cash、buying power、全部持倉權重／成本／損益、open orders、same-day fills、borrowability 與 remaining limits。
7. **No order authority**：分析 graph 不讀 broker credentials、不呼叫 order API，也不輸出 share quantity、order type 或 `OrderIntent`。

### 4.2 `PortfolioProposal` 與審核原則

- 每一列只能是 `OPEN/INCREASE/REDUCE/CLOSE/HOLD`，包含 symbol、`LONG/SHORT/FLAT`、signed target weight、confidence、evidence ids、短 reason codes、論點失效條件與所有版本；禁止自由文字交易建議。
- target weight 的絕對值不得超過 15%；quantity／金額由 deterministic translation 根據最新權威帳戶快照計算。
- Risk Engine 回傳 `APPROVED` 或結構化 `REJECTED(reason_codes, remaining_limits)`。第一次拒絕不重跑 Analysts／debates；Portfolio Manager以同一份已驗證研究、刷新後的完整 portfolio snapshot 與 rejection feedback 重申一次，且不能加入本 run 候選集合外的標的。第二次拒絕即 `NO_TRADE`。
- schema、evidence、provider、timeout 或完整 portfolio snapshot 失敗皆為 `INVALID/NO_TRADE`，不得解析自由文字補救。
- confidence 低於 0.65 強制 `HOLD`；提高 confidence 不能放寬 deterministic limits。

### 4.3 反思與記憶

- 每日保存決策、結果、Risk rejection、持倉中間狀態與 forecast calibration；持倉未平也每日寫入。
- 經確認的拆／合股強制退出保存 typed event、來源／ratio／日期、fills、最終 reconciliation、已實現
  gross/net P&L、收益率與持有期間；記憶必須標示這是 Paper operational-risk exit，不是 thesis failure。
- 原始 decision/outcome/rejection 保存在 immutable audit/DB，不刪除、不由 LLM 改寫。
- LLM 可見的濃縮記憶每週六整理，最多 4,000 行；保留重複錯誤、風控拒絕原因、有效／失敗模式、預測校準、部位教訓與 market regime。
- P3 依 `docs/MEMORY_CURATION_SKILL_SPEC.md` 交付新的 memory-curation skill，限制整理器只做去重、合併與重要性保留；不得改寫權威原始紀錄或把未來 outcome 洩漏給歷史 run。

## 5. 每日作業流程

所有時間用 `America/New_York`，每次先讀 Alpaca market calendar/clock；半日市依收盤時間相對位移，不用寫死 16:00。

| 相對／預設時間 | 工作 | Deadline 行為 |
|---|---|---|
| 04:30 | 抓取多來源價格、公司行動、SEC/IR、新聞與 point-in-time 宏觀快照 | material authority 缺失或衝突即禁止新增曝險；不以 supplement 靜默補值 |
| 06:00 | universe/quant screen，產生前 100 | 資料不新鮮則整輪無新增部位 |
| 06:30 | 對前 30 建 EvidencePacket；先執行拆／合股 quarantine | confirmed 標的不建立 analysis run；Tavily 超額則使用 cache/primary sources，不得 PAYGO |
| 開盤+60m | 全部持倉 + 最多 12 個 long 候選，完整 graph | 全流程 15 分鐘內；超時 `NO_TRADE` |
| 分析完成後 | deterministic Risk 審核，必要時一次重申，凍結核准 target | 時間不足即不追單；後續 Paper evidence 可調整窗口 |
| 第一窗口後 | post-trade reconciliation | 不一致則 pause entries |
| 收盤-90m | 全部持倉 + 最多 5 個 long 候選，完整 graph | 全流程 15 分鐘內；超時 `NO_TRADE` |
| 第二分析完成後 | deterministic Risk 審核與受限再平衡 | 收盤前安全 cutoff 取消未成交單 |
| 收盤+5m | 最終 reconciliation | 不一致升級 critical |
| 收盤+30m | 日報、歸因、每日反思／記憶和 issue 更新 | 不影響已完成帳務 |
| 週六 | 壓縮 LLM-visible memory 至最多 4,000 行 | 不在交易 critical path |

開盤後執行是因為避免把 Paper-only 的盤前流動性誤當正常成交；精確時間須由 paper experiments 校準。

## 6. 同日交易與緊急事件規則

- 不設定最短持有期；同日long→sell與平倉後再進場都可發生，但仍受兩個正常窗口與每日20% normal
  turnover上限。P4第一版不允許short→cover或任何新空倉。
- 短線獲利退出不需特殊例外；同日虧損退出不能只因為帳面虧損，必須提供 `same_day_exit_reason_code`、evidence ids，且理由屬於 downside band 明顯超標、thesis invalidated、重大新事件、borrow/liquidity anomaly 或 hard-risk trigger。
- 不符合上述條件時 Risk Engine 拒絕；Portfolio Manager仍只有一次重申機會。
- 事件監測器追蹤價格／成交量異常、停牌、借券狀態與重大新聞。價格事件須兩個獨立來源一致且連續三個 fresh samples；官方 filing／交易所／公司公告可單一 primary source 驗證新聞事件。
- 來源延遲、timestamp 不符或資料衝突標記 `DATA_CONFLICT`，不啟動 LLM 緊急交易；只有已驗證的 deterministic hard-risk 規則仍可減倉並告警。
- 緊急分析只涵蓋受影響與高度相關持倉，只能`HOLD/REDUCE/CLOSE`，3分鐘超時即`NO_TRADE`；
  `RISK_EXIT`可在任何時間執行且不計20% normal turnover，但仍完整audit。
- 已確認 `forward_split`／`reverse_split` 是獨立的 deterministic operational-risk 事件：新候選在分析前、
  P4 核准前與 submit 前三次重驗並禁止新增曝險；既有 long position 不經 LLM，先取消／解析該 symbol
  未成交單、完成 broker/local reconciliation 與 tradability preflight，再於生效日前下一個安全 regular-hours
  窗口建立 `CORPORATE_ACTION_EXIT`。這不是現有人工 `flatten_paper` 的隱性擴權。
- 若事件已生效、停牌、symbol/CUSIP 已變更、position quantity 可能失真或無法在生效日前安全成交，禁止盲目
  使用原 quantity 送單；pause entries、凍結該 symbol、告警並先做人工事件對帳。short BUY-to-cover 不在第一版
  自動 authority，需另行 review。

## 7. 初始投資組合與風控政策

以下是 Paper 校準起點，不是收益保證：

- long gross與total gross都不超過NAV 90%；short gross固定0%；最低現金10%。不設最低曝險，沒有可行
  proposal時持有現金。
- 最多15個long positions；單一標的不超過NAV 5%；SEC SIC Division sector不超過25%，高度相關cluster不超過30%。
- `short_enabled=false`；`borrow_status`只保存供future review，不得建立新空倉、SELL-to-open或BUY-to-cover。
- normal daily gross turnover不超過NAV 20%；分子為當日normal fills＋working remainder worst-case notional＋proposal
  absolute trade notional，SELL proceeds不得抵銷BUY且不除以2；分母是前一regular session close的FULL+CLEAN
  reconciled NAV。經驗證且數值上降風險的typed `RISK_EXIT`不受normal cap，但仍記gross telemetry與完整audit。
- 每筆預期部位不超過該股 20 日 ADV 的 0.1%。
- 只用整股並向零取整；低於`max(USD 100, NAV*0.25%)`或target drift<NAV 0.5%的調整不建立intent。
- quote age上限5秒、spread上限30bps、初始price collar 25bps；任一缺失、矛盾或超限即`NO_TRADE`。
- 不在 quote stale、spread 超限、trading halt、corporate action 不明／待確認／已確認禁止期時新增部位。
- 日內 Paper NAV 較前收盤跌 1.0%：停止新部位；跌 1.5%：取消所有 entry orders。
- 高水位回撤達 8%：portfolio freeze，只允許降風險，直到完成事件審查。
- 任何 source/evidence/model/broker/schema/reconciliation critical failure：停止新增風險。

optimizer 的目標是最大化經 haircut 的預期報酬，減去 variance、concentration、turnover、slippage 和 uncertainty penalty；它不能自行放寬 constraint。若沒有可行解，持有現金。

## 8. 資料策略與零付費限制

### 8.1 來源角色與優先順序

來源使用 `AUTHORITY`、`CONFIRMATION`、`DISCOVERY`、`RESEARCH_SUPPLEMENT` 封閉角色；角色不可因主來源失效而
自動升權。完整矩陣與確認規則見 `docs/SOURCES.md`。

1. 歷史日線／ADV優先使用Alpaca可用的delayed SIP；P4最新quote使用免費IEX並標記
   `LIMITED_MARKET_COVERAGE`。yfinance只作研究補充與異常比對；P7前另驗證完整報價authority。
2. 宏觀以 FRED／ALFRED point-in-time series 為主要查詢，並以 Treasury／BLS／BEA／EIA 官方發布確認；
   回測使用當時 vintage，不使用今日修訂值。
3. 基本面以 SEC 為 authority，公司 IR／官方新聞稿補充；公司身份以 CIK／accession 為主。
4. 公司事件以 Alpaca 結構化 feed 發現／確認，SEC／issuer／Nasdaq／NYSE 正式公告決定自動 authority。
5. Tavily 與 GDELT 都只作 discovery；material claim 必須回到原始 publisher。
6. Metadata 以 Alpaca current asset 為 authority；yfinance 只補充顯示欄位，歷史 symbol/exchange 由
   event-sourced security master 管理。

### 8.2 Tavily 預算

使用者宣告持有 7 個 Tavily 帳號，理論免費容量為 7,000 credits／月。但 Tavily 現行 Platform Terms 表示每個 Order Form 原則上提供單一 Account，額外 Account 可能需要個別 Order Form／費用，且不得超越 Customer limitations。因此 7,000 額度是 **條件式容量**，不是未確認即可規避配額的授權。

程式必須支援兩種 compliance mode：

- `SINGLE_ACCOUNT_UNVERIFIED`：未取得 Tavily 書面確認／後台授權時，只啟用 1 組 key，月硬上限 1,000；其他帳號 disabled。
- `AUTHORIZED_ACCOUNT_POOL`：確認 7 個帳號均可由同一 Customer 合法彙總後，才啟用 7 組 key，月硬上限 7,000。

已授權 account pool 的 deterministic budget：

- 月 runtime budget 5,600 credits；research/incident reserve 1,400 credits。
- 一般交易日 runtime soft cap 250 credits；單日 hard cap 300；月全域 hard cap 7,000。
- 每個帳號各有獨立 1,000-credit ledger、reset timestamp、health、429 cooldown 和 enabled flag。
- router 以最低使用率的健康帳號循序分配，不靠跨帳號併發突破單帳號或服務 rate limit。
- 預設 basic search；同 URL、query 和 freshness window 去重。
- 只對前 30 候選做網頁 extract，完整多角色 analysis 只限前 12。
- 429 遵守 `Retry-After`，超過 deadline 即停止，不延後追單。
- 禁止 PAYGO、自動建立帳號或隱匿 identity；自動偵測方案／額度／授權異常並 pause discovery。

### 8.3 X 資料邊界

- 不建立 credential scraping、cookie harvesting 或規避登入的爬蟲。
- runtime 不以即時 X 為必要輸入；Sentiment Analyst 無法取得合規、可回溯且足夠新鮮的資料時必須降級或棄權。
- 七人候選語料只屬 Future Analyst Plugin，未重新核准前不蒸餾、不讀取、不成為 P3 依賴。
- 原文只保存個人研究所需最小片段；repository 不提交大量第三方全文。

## 9. 模型與多 Agent 分工

### 9.1 開發階段

- `gpt-5.6-sol`：總架構、金融安全、schema、release gate、重大 code review。
- `gpt-5.6-terra`：模組實作、整合測試、一般重構與 debug。
- `gpt-5.6-luna`：批次資料清理、fixture、重複性單元測試、文件更新。
- 所有 agent 必須有檔案 ownership；共享工作區時不得覆蓋他人修改。

### 9.2 每日研究模型路由

- Technical/Market、Fundamentals、News、Sentiment、Research Manager、Trader、三個Risk Debate角色及
  Portfolio Manager全部固定`agnes-2.5-flash`，使用exact Chat Completions endpoint。
- 此版本沒有fallback、automatic retry、可配置候選或Responses route；未來新增provider/model要先取得新決策並
  通過相同eval gate，不得由runtime任意切換。
- 例外只存在於P3-F synthetic eval harness：production transport本身仍無hidden retry；eval orchestrator可在
  exact使用者授權的260 logical／780 attempt cap內，僅對`TIMEOUT`／`TRANSIENT`／`RATE_LIMIT`重試兩次並以
  circuit breaker止損。這不延伸到production analysis或交易路徑。
- 所有角色記錄`reasoning_requested=MAX`，但在官方＋authorized live evidence證實前不傳未知參數，
  `reasoning_effective=UNKNOWN`，不得假裝已啟用MAX。
- 傳給模型的portfolio snapshot必須去除姓名、account id、broker order id等識別欄位，只保留分析必需數值；
  Agnes不得標示ZDR／不訓練，live payload仍受每批明確授權限制。
- 未來 `gpt-5.6` 只有通過相同 held-out、schema、safety、latency 與 cost eval gate 後才可加入，不自動升級或替換既有模型。

## 10. Codex 的正確角色

Codex 適合：

- 夜間單元／整合／property tests；
- 每週分析來源、provider/version drift 與資料缺口報告；
- 每日收盤後產生程式與資料品質報告；
- 文件、issue、risk register 和 dependency audit；
- 在獨立 worktree 完成非緊急修復並等待核准合併。

Codex 不負責：

- 開盤後 60 分鐘或收盤前 90 分鐘分析／交易窗口的唯一喚醒機制；
- 保管 broker credentials；
- 在 production 工作目錄自動修改程式後立即交易；
- 對未 reconciliation 的委託做自主補單。

## 11. 驗證哲學

### 11.1 研究有效性

- source citation coverage、citation entailment、source-date compliance；
- analyst role fidelity、graph semantic parity 與角色移除／ablation test；
- abstention precision/recall、hallucination rate、conflict handling；
- Brier score/校準曲線、不同 horizon 的 rank IC；
- 委員相關性、ablation、反例召回、權重穩定性；
- prompt injection 和惡意網頁防禦。

### 11.2 投資有效性

- 使用 point-in-time universe 和 as-of sources 的 walk-forward；
- 報酬扣除 conservative slippage、spread、unfilled 和 capacity haircut；
- 對 SPY 及風格／sector baseline 比較，不只看絕對報酬；
- 報告 turnover、drawdown、exposure、hit rate、tail loss 和 regime dependency；
- 不用同一段歷史選參數又宣稱 out-of-sample。

### 11.3 營運有效性

- 斷網、429、partial fill、duplicate update、out-of-order event、process crash；
- broker timeout before/after accept；
- DB unavailable、clock skew、stale quotes、half-day、holiday、DST；
- restart reconciliation、kill switch、pause/resume 和 missed-window；
- property：任何事件序列都不得超過硬風控或產生同一 intent 的第二筆有效 order。

## 12. 發布 Gate

1. **P0 Spec Gate**：本文件集核准，所有硬邊界無矛盾。
2. **P1 Core Gate**：schema、DB、config、secrets、observability、CI 完成。
3. **P2 Broker Safety Gate**：Paper adapter、outbox、reconciliation、故障注入全過。
4. **P3 Analysis/Proposal Gate**：完整 TradingAgents graph、`PortfolioProposal`、provider/memory、point-in-time/evidence/schema eval 過門檻。
5. **P4 Data/Candidate/Deterministic Risk Gate**：多來源 adapters、point-in-time security master、拆／合股
   quarantine 與 no-submit `CORPORATE_ACTION_EXIT` intent、候選漏斗、一次駁回重申、long-only hard risk 與
   `TargetPortfolio` 重建性過門檻。
6. **P5 Validation Gate**：point-in-time walk-forward、decision replay、attribution 與經濟成交模型完成。
7. **P6 Shadow Gate**：至少 20 個交易日只產生意圖、不送單，並演練拆／合股發現、quarantine、退出 intent、
   告警、記憶與 P&L lineage；零嚴重帳務錯誤。
8. **P7 Supervised Paper Gate**：至少 20 個交易日 Paper，逐日人工檢視但不手動干預策略；首次啟用
   `CORPORATE_ACTION_EXIT` 真實 submit 前需獨立 acceptance 與使用者明確授權。
9. **P8 Unattended Paper Gate**：再至少 40 個交易日無人值守，通過 uptime、reconciliation、風控和研究品質門檻。

無論 Paper 表現如何，本企劃沒有自動升級實盤的 gate。

P3-F內部驗收拆成：Offline Correctness（安全與deterministic結果100%）、Live Model Quality（至少250/260 strict
responses、completed正確率≥98%、response-contract violations=0、130/130 local fail-closed）與Provider
Transport（first-attempt≥95%、最多三attempt後eventual≥99%）。Transport是會隨時間變動的P6 readiness Gate，
需在P6前以rolling 7日且至少200個另行授權synthetic canary calls重驗；它不由單次P3-F batch永久關閉。

## 13. 最終交付物

- 可重建環境、版本鎖定與 secrets setup 文件；
- 完整 TradingAgents analysis/proposal graph、versioned `PortfolioProposal` schema、公開來源 manifest、portfolio/evidence/data snapshot 索引與 bounded-memory skill；
- 完整 Python modular monolith、DB migrations、CLI/control plane；
- Paper broker adapter、portfolio/risk/execution/reconciliation；
- backtest、walk-forward、shadow、paper reports；
- dashboard/日報/告警與 runbook；
- decision/progress/issue/work/risk 日誌；
- 測試證據與每個 gate 的簽核紀錄。

## 14. 目前結論

這個專案在技術上可行，但 TradingAgents 的 Portfolio Manager proposal 不等於可直接交易。正確邊界是：P3 保存可稽核、point-in-time、可重播的完整研究／提案與記憶；P4 deterministic Risk 才能核准 targets 並轉成 quantity；P2 只執行已核准的 Paper `OrderIntent`。資料不足、上游/provider 漂移、schema 失敗或第二次 Risk rejection 時必須 `NO_TRADE`。七人蒸餾保留為未來可選插件，不再阻塞主線。
