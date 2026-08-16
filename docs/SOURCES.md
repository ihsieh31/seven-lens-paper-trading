# 外部依據與來源策略

此檔分成「架構依據」與「蒸餾候選來源」。候選不等於已驗證、已授權或已納入；每次實作須固定版本與保存 SourceManifest。

## 1. TradingAgents

- Upstream：[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- 本企劃固定檢查 commit：`a33fd4c0f134485a43553a2c23a63cb14adbd88f`
- 重要程式：[setup.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/setup.py)、[agent_states.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/utils/agent_states.py)、[portfolio_manager.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/managers/portfolio_manager.py)、[signal_processing.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/signal_processing.py)
- 用途：理解 graph/state/debate，不把 upstream 的語言模型訊號直接接券商。

## 2. Alpaca Paper Trading

- [Paper Trading 說明與限制](https://docs.alpaca.markets/docs/paper-trading)
- [Working with orders](https://docs.alpaca.markets/docs/working-with-orders)
- [WebSocket streaming](https://docs.alpaca.markets/docs/websocket-streaming)

對架構的影響：

- Paper 是 real-time simulation，但不能完整模擬 market impact、queue position、latency slippage、price improvement 等實盤微結構。
- Paper-only account 可能只有 IEX market data entitlement，研究與成交模型需保守處理覆蓋差異。
- 使用 `client_order_id` 和 trade updates；REST reconciliation 仍是必要安全機制。
- 本專案需自己建立 order state machine、outbox、ledger 和 fail-closed controls。

## 3. Tavily

- [API credits](https://docs.tavily.com/documentation/api-credits)
- [Rate limits](https://docs.tavily.com/documentation/rate-limits)
- [Platform Terms of Service](https://www.tavily.com/terms)
- [官方文件首頁](https://docs.tavily.com/)

對架構的影響：

- 每個免費 Researcher 帳號標示 1,000 credits／月；使用者宣告持有 7 個帳號，但合計 7,000 只有在 Tavily 明確允許同一 Customer 彙總使用時才成立。
- 現行條款表示每個 Order Form 原則上是單一 Account，額外 Accounts 可能需要各自的 Order Form／費用，且禁止超越 Customer limitations；因此不把多帳號輪替當成規避免費方案限制。
- 免費／development rate limit 與 429 `Retry-After` 要內建。
- 授權後的 pool 設定 runtime 5,600、research/incident reserve 1,400、每日 soft cap 250、全域月 hard cap 7,000；未確認時 hard cap 1,000。
- account pool 只做合規的配額平衡和故障隔離，不跨 key 併發繞過 rate limit；禁止自動 PAYGO。
- Tavily 是 discovery/extract 工具，不是 primary-source truth database。

## 4. OpenAI / Codex

- [OpenAI 最新模型選擇指南](https://developers.openai.com/api/docs/guides/latest-model)
- [Codex Automations](https://learn.chatgpt.com/docs/automations)

對架構的影響：

- Sol 用於高難度架構與高風險 review；Terra 用於平衡品質／成本的主要開發；Luna 用於大量、邊界清楚的重複工作。
- Scheduled tasks 適合週期性檢查、報告與 code maintenance；本機任務依賴機器和應用程式運行，因此不作為盤中交易的唯一排程器。

## 5. 七人 primary-source map

下列只代表 discovery 起點。每個內容仍需逐頁判斷是否免費、公開、可引用、是否為本人發表。

本機 `skill/` 已有七位候選語料；截至 2026-08-16 只確認路徑存在，尚未審查內容、來源、授權、完整性、重複、時間或可蒸餾性。該目錄約 827 MB，固定排除於公開 Git repository。以下是正式 P3 審查時的起點，不是已驗收清單。

### Howard Marks

- 本機候選：`skill/Howard Marks.pdf`。
- 計畫中的 primary sources：Oaktree 官方 memos、Howard Marks 公開文章、書面訪談與演講逐項進 manifest。
- 週期與風險框架必須保留發布時間；不得把回顧性的市場判斷當成當時可得訊號。

### Muddy Waters Research

- 本機候選：`skill/Muddy_Waters/`。
- 計畫中的 primary sources：Muddy Waters 官方研究、相關公司 filing／回應、監管與法院文件。
- 做 long-only 排除與風險檢查；每項負面主張必須區分已證實事實、研究機構主張、公司反駁與本系統推論。

### Aswath Damodaran

- 本機候選：`skill/aswath_damodaran/`。
- [NYU Damodaran Online](https://pages.stern.nyu.edu/~adamodar/)
- [Musings on Markets](https://aswathdamodaran.blogspot.com/)
- 官方免費課程、slides、spreadsheets、blog 是主要蒸餾來源；舊估值 input 不當今日資料。

### Serenity / @aleabitoreddit

- 本機候選：`skill/serenity-aleabitoreddit-data/`。
- [X profile](https://x.com/aleabitoreddit)
- 正式審查時核對 canonical X URL、coverage、dedup、擷取方式、刪文狀態與授權；未審查前不得直接成為 doctrine evidence。

### Terry Smith / Fundsmith

- 本機候選：`skill/terry_smith_fundsmith/`。
- 計畫中的 primary sources：Fundsmith 官方 owners' manual、annual letters、shareholder meeting materials 與 Terry Smith 公開訪談。
- 將企業品質、再投資 runway 與買入估值分開；歷史持倉或績效不得直接當成未來訊號。

### Michael Mauboussin

- 本機候選：`skill/michael_mauboussin/`。
- 計畫中的 primary sources：作者／任職機構正式發布的研究、書籍配套材料與公開演講訪談。
- base rate、期望值與競爭優勢框架必須標記樣本定義、期間與適用邊界，避免把舊統計當成固定常數。

### Lyn Alden

- 本機候選：`skill/Lyn_Alden/`。
- [Lyn Alden 官方網站](https://lynalden.com/)
- [X profile](https://x.com/LynAldenContact)
- 官方免費文章/newsletter、公開訪談、X canonical posts；長期 framework 與短期 timing 分離。

## 6. GitHub 其他設計候選

- 舊版 discovery 候選紀錄不再代表目前七位名單；若正式 P3 仍想借鑑 retrieval/evidence packet 設計，必須重新做 repository、license、source 與 prompt-injection 稽核。

結論：尚無「有名、完整、具授權、可追溯、含反例與 held-out eval」的一套 skill 能涵蓋七人。開源資產只當設計與 discovery 輸入。

## 7. 免費資料候選優先序

實作 P3 時才逐一驗證 API 條款、rate limit 和 point-in-time 能力：

1. SEC EDGAR submissions/companyfacts/filings；
2. 公司 IR 和官方 press release；
3. FRED、US Treasury、BLS、BEA、EIA 等官方宏觀資料；
4. Alpaca 提供的 Paper/market data；
5. Nasdaq/NYSE/company corporate-action 公開頁面；
6. Tavily discovery/extract；
7. 其他免費 API 僅在 license、穩定度、coverage 和 timestamp gate 通過後加入。

不得因 API 免費就默認允許大量抓取、再散布或 production 使用。

## 8. 引用與保存規則

- 研究報告用 canonical URL，不引用搜尋結果頁。
- 每個 material claim 最近處放 citation id；SourceManifest 解析成可點 URL。
- 摘要用自己的文字；保存必要短摘錄供 entailment，不大量重製原文。
- source URL 失效時保存 tombstone、hash 和既有 metadata；若無法驗證，降低 confidence。
- 每次 doctrine、backtest 和 daily run 都固定 source snapshot id。
