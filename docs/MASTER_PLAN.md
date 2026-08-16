# 七人委員全自動 Paper Trading 系統：第一性原理主企劃書

版本：0.1
日期：2026-08-14
狀態：Planning baseline

## 1. 專案定義

本專案要建立的不是「會自動按買賣鍵的聊天機器人」，而是一套可重現、可追溯、遇到未知狀況會停止新增風險的自動投資作業系統。它每天在美股開盤前完成跨股票篩選與七人委員研究，開盤後執行主要再平衡；收盤前只針對持倉和極少數高優先候選重評，再進行一次受限再平衡。

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

- 不做當沖、超短線、盤前盤後交易。
- 不做空、不用 margin、不做 options、crypto、OTC、ETN、槓桿或反向 ETF。
- 不把公開人物寫成可冒充本人的聊天角色。
- 不付費購買 X API、研究訂閱、市場資料或雲端服務。
- 不繞過登入、付費牆、robots 或存取限制。
- 不以 Codex 桌面排程作為市場時鐘或唯一 broker workflow engine。
- 不因 Paper 獲利就推論實盤可行。

## 2. 從第一性原理拆解

一筆安全的自動交易必須依序回答六個不同問題；任何一層都不能由下一層倒推補齊：

1. **世界狀態是否可知？** 資料是否完整、新鮮、合法取得、時間一致？
2. **公司是否值得研究？** 先用廉價、確定性的指標把大股票池縮小。
3. **投資論點是否成立？** 七種方法論分別提出主張、證據、反證、時效和失效條件。
4. **這個論點在組合中值多少？** 根據預期報酬、風險、相關性、流動性、信心和換手成本計算 target weight。
5. **現在能不能下單？** 硬風控、帳務、market clock、quote freshness、price collar 和交易窗口決定。
6. **委託後真實狀態為何？** 以 broker 回報與 reconciliation 更新帳本，不以模型或本地預期認定成交。

因此資料、研究、組合、風控、執行、帳務是六個不同 bounded context。LLM 只存在於第三層和有限的證據分類工作。

## 3. 投資任務與預設宇宙

### 3.1 投資目標

在不使用槓桿的前提下，尋找 10–60 個交易日可實現的風險調整後超額報酬，主要來自：

- 市場尚未充分定價的供應鏈瓶頸與第二階影響；
- 可被公開財報與產業資料驗證的主題性需求變化；
- 故事、數字和估值間的不一致；
- 被忽視的治理、會計、融資或商業模式風險；
- 宏觀流動性、財政、利率、能源和跨資產環境對個股的傳導。

### 3.2 股票宇宙

每月從 Alpaca `active + tradable` 美國資產建立版本化 universe snapshot，再套用：

- 普通股或未槓桿 ETF；
- 價格至少 USD 5；
- 20 日平均美元成交額至少 USD 20M；
- 至少 252 個有效交易日價格資料；
- 排除 OTC、preferred、warrant、unit、closed-end fund、ETN、槓桿／反向 ETF；
- 排除 corporate action、交易暫停或資料品質狀態不明者；
- 第一版只用整股，候選價格不可使最小部位無法合理配置。

上述是初始校準值，不是假定永遠正確；任何調整須走 ADR、walk-forward 和 paper evidence。

### 3.3 候選漏斗

為避免對數千股票逐一呼叫 LLM：

| 階段 | 最大數量 | 方法 | 目的 |
|---|---:|---|---|
| Universe | 約 2,000–4,000 | 資產、價格、流動性硬篩 | 移除不可交易標的 |
| Quant screen | 100 | 趨勢、品質、估值 proxy、事件、風險 | 找值得取證者 |
| Evidence screen | 30 | Tavily/SEC/IR/免費來源摘要與缺口評分 | 確認有足夠資料 |
| Full committee | 12 | 七人 doctrine assessments + rebuttal | 深度辯論 |
| Portfolio | 最多 10 持倉 | deterministic optimizer + risk | 形成目標組合 |

既有持倉永遠進入 evidence refresh；若資料不足，允許減倉或退出，不允許因缺資料自動加碼。

## 4. 七人委員的分工

七位不是七票等權，也不是七個預先被要求看多／看空的角色。每位都可以 `SUPPORT`、`OPPOSE` 或 `ABSTAIN`，並對自己最擅長的 domain 給出 assessment。

| 方法論 | 主要任務 | 典型反問 |
|---|---|---|
| Howard Marks | 市場週期、風險、投資人心理與 second-level thinking | 共識已反映什麼，而週期位置讓哪些風險被低估？ |
| Muddy Waters Research | forensic accounting、揭露品質、治理與舞弊風險 | 哪些可核對證據會推翻管理層敘事或資產品質？ |
| Aswath Damodaran | 故事到數字、估值、風險與 terminal assumptions | 目前價格要求公司成為什麼樣的企業？ |
| Serenity / `@aleabitoreddit` | AI／半導體多跳 BOM、上游瓶頸、認證週期 | 真正不可替代的 chokepoint 在哪？ |
| Terry Smith / Fundsmith | 高品質複利、資本報酬、再投資與資本配置 | 企業能否以高報酬率長期再投資而不依賴槓桿？ |
| Michael Mauboussin | expectations investing、base rates、競爭優勢與機率決策 | 市價隱含哪些預期，base rate 與可逆證據是否支持？ |
| Lyn Alden | 財政／貨幣 regime、能源、美元、長週期 | 宏觀資產負債表環境支持或破壞哪個現金流？ |

### 4.1 辯論協議

1. **Evidence packet freeze**：本輪所有委員只讀同一個有時間戳快照。
2. **Blinded first pass**：各自獨立輸出，不可先看其他委員結論，降低群體從眾。
3. **Evidence verification**：每個 material claim 必須映射 source fragment；無法支持則降級或移除。
4. **Targeted rebuttal**：只把真正衝突的假設交給相關委員反駁，不進行無限對話。
5. **Neutral chair**：整合共識與歧見，但不得新增 evidence packet 中不存在的事實。
6. **Deterministic translation**：把結論轉為有限範圍的 alpha/confidence/risk inputs，再交給 optimizer。

### 4.2 權重原則

- 權重依「domain relevance × 過去校準 × 當前證據品質」決定，不是名氣或固定一人一票。
- Muddy Waters 類負面證據可觸發風險調查或否決，但一則疑點不等於自動做空。
- 委員一致不代表正確；高度相關的同源資訊要做 correlation haircut。
- 一位相關專家的 primary-source-backed 反例，可以勝過六個低相關、重複同一新聞的支持意見。
- 權重只能從 held-out / forward evaluation 更新，且有上下限和版本紀錄。

## 5. 每日作業流程

所有時間用 `America/New_York`，每次先讀 Alpaca market calendar/clock；半日市依收盤時間相對位移，不用寫死 16:00。

| 相對／預設時間 | 工作 | Deadline 行為 |
|---|---|---|
| 04:30 | 抓取價格、公司行動、SEC/IR、新聞與宏觀快照 | 失敗重試且不下單 |
| 06:00 | universe/quant screen，產生前 100 | 資料不新鮮則整輪無新增部位 |
| 06:30 | 對前 30 建 EvidencePacket | Tavily 超額則使用 cache/primary sources，不得 PAYGO |
| 07:00–09:00 | 前 12 七人 first pass、驗證、rebuttal、chair | 09:10 未完成者 `INVALID` |
| 09:10 | 凍結 `TargetPortfolio` 與 pre-trade risk decision | 之後模型輸出不可插單 |
| 09:35 | 主要再平衡 | 錯過窗口不追單 |
| 10:00 | post-trade reconciliation | 不一致則 pause entries |
| 15:15 | 持倉與少數高順位候選 refresh | 不重跑整個 universe |
| 15:35 | 凍結第二份 target；只允許受限 delta | 資料不足維持或降風險 |
| 15:40 | 收盤前再平衡 | 收盤前安全 cutoff 取消未成交單 |
| 收盤+5m | 最終 reconciliation | 不一致升級 critical |
| 收盤+30m | 日報、歸因、成本和 issue 更新 | 不影響已完成帳務 |

開盤後執行是因為避免把 Paper-only 的盤前流動性誤當正常成交；精確時間須由 paper experiments 校準。

## 6. 不做當沖的機械規則

- 每一 lot 保存 acquisition trading date。
- 同日買入的 share 不可因 alpha 改變在當日賣出。
- 同日賣出的 symbol 不可因 alpha 改變在當日買回。
- 正常研究退出需持有至少 5 個交易日。
- 只有 `RISK_EXIT`、`BROKER_CORRECTION`、`CORPORATE_ACTION` 可突破最短持有期；每次例外要單獨 audit event。
- 收盤前交易不是第二套當沖訊號，而是對目標投資組合的小幅修正、未成交處理或風險降低。

## 7. 初始投資組合與風控政策

以下是 Paper 校準起點，不是收益保證：

- 總曝險／淨長曝險不超過 NAV 100%；現金下限 20%。
- 最多 10 個持倉；單一標的 target 不超過 8%。
- 單一 GICS sector 不超過 25%；高度相關主題另設 30% cluster cap。
- 每日單邊成交額合計不超過 NAV 20%。
- 每筆預期部位不超過該股 20 日 ADV 的 0.1%。
- 不在 quote stale、spread 超限、trading halt、corporate action 不明時新增部位。
- 日內 Paper NAV 較前收盤跌 1.0%：停止新部位；跌 1.5%：取消所有 entry orders。
- 高水位回撤達 8%：portfolio freeze，只允許降風險，直到完成事件審查。
- 任何 source/evidence/model/broker/schema/reconciliation critical failure：停止新增風險。

optimizer 的目標是最大化經 haircut 的預期報酬，減去 variance、concentration、turnover、slippage 和 uncertainty penalty；它不能自行放寬 constraint。若沒有可行解，持有現金。

## 8. 資料策略與零付費限制

### 8.1 來源優先順序

1. SEC filings、公司 IR、官方政府／監管／統計資料。
2. Alpaca Paper/market data 和交易日曆。
3. 作者本人免費公開網站、Substack、podcast/transcript、X 公開頁面。
4. Tavily 搜尋與擷取，作為 discovery/normalization，不把 search snippet 當最終證據。
5. 免費的高品質產業／新聞／開源資料。
6. 第三方整理語料僅作候選，必須回鏈原始 post/URL；不能回鏈者降級。

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
- 只對前 30 候選做網頁 extract，完整 committee 只限前 12。
- 429 遵守 `Retry-After`，超過 deadline 即停止，不延後追單。
- 禁止 PAYGO、自動建立帳號或隱匿 identity；自動偵測方案／額度／授權異常並 pause discovery。

### 8.3 X 資料邊界

- 不建立 credential scraping、cookie harvesting 或規避登入的爬蟲。
- 離線蒸餾逐一搜尋可公開存取的原始 X URL、作者網站或合法第三方索引。
- runtime 不以即時 X 為必要輸入；無法讀取時保留最後已驗證 doctrine，但「最新立場」標示 stale 並棄權。
- 原文只保存個人研究所需最小片段；repository 不提交大量第三方全文。

## 9. 模型與多 Agent 分工

### 9.1 開發階段

- `gpt-5.6-sol`：總架構、金融安全、schema、release gate、重大 code review。
- `gpt-5.6-terra`：模組實作、整合測試、一般重構與 debug。
- `gpt-5.6-luna`：批次資料清理、fixture、重複性單元測試、文件更新。
- 所有 agent 必須有檔案 ownership；共享工作區時不得覆蓋他人修改。

### 9.2 每日研究階段

- Luna 處理大量候選的抽取、分類和初步 assessment。
- Terra 處理前 12 的複雜辯論、衝突解決和 evidence repair。
- Sol 只在主席、重大矛盾、高影響持倉或 release/evaluation 上使用。
- DeepSeek V4 Flash、Agnes 2.5 Flash 僅能在建立 provider adapter、資料政策和同一套 eval gate 後成為可選模型，不可成為第一版必要依賴。
- 任一模型降級、超時或結構錯誤只能降低 coverage，不能繞過驗證。

## 10. Codex 的正確角色

Codex 適合：

- 夜間單元／整合／property tests；
- 每週 doctrine source refresh 和來源缺口報告；
- 每日收盤後產生程式與資料品質報告；
- 文件、issue、risk register 和 dependency audit；
- 在獨立 worktree 完成非緊急修復並等待核准合併。

Codex 不負責：

- 09:35 或 15:40 的唯一喚醒機制；
- 保管 broker credentials；
- 在 production 工作目錄自動修改程式後立即交易；
- 對未 reconciliation 的委託做自主補單。

## 11. 驗證哲學

### 11.1 研究有效性

- source citation coverage、citation entailment、source-date compliance；
- doctrine fidelity 與 persona-name removal test；
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
4. **P3 Research Gate**：七套 doctrine v1、source manifest、held-out eval 過門檻。
5. **P4 Strategy Gate**：point-in-time walk-forward 和經濟成交模型完成。
6. **P5 Shadow Gate**：至少 20 個交易日只產生意圖、不送單，零嚴重帳務錯誤。
7. **P6 Supervised Paper Gate**：至少 20 個交易日 Paper，逐日人工檢視但不手動干預策略。
8. **P7 Unattended Paper Gate**：再至少 40 個交易日無人值守，通過 uptime、reconciliation、風控和研究品質門檻。

無論 Paper 表現如何，本企劃沒有自動升級實盤的 gate。

## 13. 最終交付物

- 可重建環境、版本鎖定與 secrets setup 文件；
- 七人 doctrine registry、公開來源 manifest 和 evidence corpus 索引；
- 完整 Python modular monolith、DB migrations、CLI/control plane；
- Paper broker adapter、portfolio/risk/execution/reconciliation；
- backtest、walk-forward、shadow、paper reports；
- dashboard/日報/告警與 runbook；
- decision/progress/issue/work/risk 日誌；
- 測試證據與每個 gate 的簽核紀錄。

## 14. 目前結論

這個專案在技術上可行，但「免費公開資料 + 沒有 X API + 無人值守」必須接受一個不可妥協的結果：系統不能保證知道每位委員的最新公開發言，也不能用猜測填補。正確行為是保存可稽核的 doctrine、定期離線更新、在資料過期時棄權，並讓投資組合持有現金。這不是功能缺陷，而是零付費資料條件下的安全設計。
