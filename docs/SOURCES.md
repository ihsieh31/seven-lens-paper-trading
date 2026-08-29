# 外部依據與來源策略

此檔分成「現行分析／資料架構依據」、「已核准且正分Gate實作的多來源規劃」與「停用的 Future Analyst Plugin 候選來源」。規劃、初版adapter或候選不等於已驗證、已授權或已納入；每次實作須固定版本、host、rights、rate limit 與 SourceManifest，並經對應 phase 的獨立驗收。

## 1. TradingAgents

- Upstream：[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- 本企劃固定檢查 commit：`a33fd4c0f134485a43553a2c23a63cb14adbd88f`（2026-08-21 `git ls-remote` 核對為 upstream `main`）
- License：[Apache License 2.0](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/LICENSE)；直接複製所需檔案時保留 license／copyright／NOTICE（若有），並標示本專案修改。
- 重要程式：[setup.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/setup.py)、[agent_states.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/utils/agent_states.py)、[schemas.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/schemas.py)、[research_manager.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/managers/research_manager.py)、[trader.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/trader/trader.py)、[portfolio_manager.py](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/managers/portfolio_manager.py)
- 用途：P3 採用四分析員→兩輪 Bull/Bear→Research Manager→Trader→兩輪 Risk Debate→Portfolio Manager 的完整研究／提案 graph；只移植所需 analysis/data/debate/risk/manager/memory 模組，order path 不納入。

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

## 4. LLM providers / Codex

- [OpenAI 最新模型選擇指南](https://developers.openai.com/api/docs/guides/latest-model)
- [Codex Automations](https://learn.chatgpt.com/docs/automations)
- [Agnes AI API reference](https://github.com/1038lab/Agnes-AI/blob/main/references/api.md)
- [Agnes AI model catalog](https://github.com/AgnesAI-Labs/AgnesAI-Models/blob/main/MODEL_CATALOG.md)
- [NVIDIA NIM / build.nvidia.com API](https://docs.api.nvidia.com/)：production base URL
  `https://integrate.api.nvidia.com/v1`，OpenAI-compatible Chat Completions
  （`POST /v1/chat/completions`），`Authorization: Bearer`；`openai/gpt-oss-120b` 為
  NIM 上的 OpenAI gpt-oss-120b 部署（reasoning 模型；回應含 `reasoning_content`，僅作 bounded
  非 authority 欄位處理）。2026-08-28 起為現行分析 route（ADR-033）
- [OpenCode Go models、pricing 與 privacy](https://opencode.ai/docs/go/)

對架構的影響：

- 分析 provider 已自「Agnes endpoint/model 寫死」改為 generic operator configuration：兩個
  `python -m seven_lens.cli.analysis_provider set-endpoint / set-model` 指令持久設定 base URL 與
  model；config 以 strict schema＋route_config_hash 存於
  `${XDG_CONFIG_HOME:-$HOME/.config}/seven-lens/analysis-provider.json`。2026-08-28 設定後的
  active route 於 2026-08-28 由使用者改為 `https://integrate.api.nvidia.com/v1` ＋
  `openai/gpt-oss-120b`
  （route_config_hash `0659d8fa9b38c9e7a800ce2bdc89b14eeb76a5c83f157f6b65afcbe568162524`，
  endpoint_policy_id `analysis-route-v1:` + 該 hash）。package-owned default 仍為 legacy
  Agnes base/model（generation=0），operator 檔案不存在時才使用。
- 無fallback、無automatic retry、無 per-request override；舊 Agnes V12 歷史證據與 route identity
  保留於 DB（provider=`AGNES`）與 archive 不變。新 route 的 P3-E/P3-F live 證據 pending fresh
  authorization。
- 公開文件未證實可用的MAX reasoning參數；只保存`reasoning_requested=MAX`，不傳未知參數並記
  `reasoning_effective=UNKNOWN`。
- Agnes不得標示ZDR／不訓練；每批live payload、request count與成本仍需新的明確授權。
- 未來任何provider/model需通過相同held-out/schema/safety/latency gate並取得新決策，不能自動啟用。
- Scheduled tasks 適合週期性檢查、報告與 code maintenance；本機任務依賴機器和應用程式運行，因此不作為盤中交易的唯一排程器。

## 5. Future Analyst Plugin 的七人 primary-source map（停用）

下列只代表 discovery 起點。每個內容仍需逐頁判斷是否免費、公開、可引用、是否為本人發表。

本機 `skill/` 已有七位候選語料；截至 2026-08-16 只確認路徑存在，尚未審查內容、來源、授權、完整性、重複、時間或可蒸餾性。該目錄約 827 MB，固定排除於公開 Git repository。以下只在使用者未來重新核准插件研究時作 discovery 起點，不是 P3 工作、不是已驗收清單。

### Howard Marks

- 本機候選：`skill/Howard Marks.pdf`。
- 計畫中的 primary sources：Oaktree 官方 memos、Howard Marks 公開文章、書面訪談與演講逐項進 manifest。
- 週期與風險框架必須保留發布時間；不得把回顧性的市場判斷當成當時可得訊號。

### Muddy Waters Research

- 本機候選：`skill/Muddy_Waters/`。
- 計畫中的 primary sources：Muddy Waters 官方研究、相關公司 filing／回應、監管與法院文件。
- 做 long/short 風險檢查；每項負面主張必須區分已證實事實、研究機構主張、公司反駁與本系統推論。

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

- 舊版 discovery 候選紀錄不代表 P3 主線；Future Analyst Plugin 若重新啟動，必須重新做 repository、license、source 與 prompt-injection 稽核。

結論：尚無「有名、完整、具授權、可追溯、含反例與 held-out eval」的一套 skill 能涵蓋七人。開源資產只當設計與 discovery 輸入。

## 7. 免費資料候選優先序

未來實作production source adapter時逐一驗證 API 條款、rate limit 和 point-in-time 能力：

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
- 每次 `PortfolioProposal`、backtest 和 daily run 都固定 portfolio/source/data snapshot ids；Future Analyst Plugin 若啟用，另固定 plugin/doctrine version。

## 9. 已核准的多來源資訊規劃（P4-A／P4-B Accepted／Closed；完整P4尚未完成）

來源不是可互換的普通 fallback。每個 adapter 與每筆資料都必須標記下列封閉角色之一：

- `AUTHORITY`：可支撐對應類別的 deterministic 判定；
- `CONFIRMATION`：只確認、否定或指出 authority 衝突；
- `DISCOVERY`：只發現候選事件／原始來源，不能直接支撐 material claim；
- `RESEARCH_SUPPLEMENT`：只增加研究背景，不能靜默填補 authority 缺口。

| 類別 | 主要來源與角色 | 備援／補充與角色 | 用途與 fail-closed 邊界 |
|---|---|---|---|
| 行情／K 線 | Alpaca delayed historical SIP `AUTHORITY`；IEX latest在P4 zero-submit範圍為`AUTHORITY` | yfinance `RESEARCH_SUPPLEMENT`／異常比對 | IEX snapshot另標`LIMITED_MARKET_COVERAGE`，不能推導P7完整市場authority；yfinance不得補權 |
| 宏觀 | FRED（現行標準化查詢）／ALFRED（歷史 vintage `AUTHORITY`） | Treasury、BLS、BEA、EIA 官方發布 `AUTHORITY/CONFIRMATION` | 利率、通膨、GDP、就業、能源；歷史 replay 必須使用當時可見 vintage，不得使用今日修訂值 |
| 基本面 | SEC submissions、filings、XBRL companyfacts `AUTHORITY` | 公司 IR／官方新聞稿 `AUTHORITY/CONFIRMATION` | 10-K、10-Q、8-K、財務數據與管理層資訊；身份以 CIK／accession 為主，不只靠 ticker |
| 公司事件 | Alpaca Corporate Actions 結構化 feed `DISCOVERY/CONFIRMATION` | SEC、公司 IR、Nasdaq／NYSE 正式公告 `AUTHORITY` | 拆股、合股、股息、併購、改名、停牌等；Alpaca 延遲或缺漏不能被解讀成「沒有事件」 |
| 新聞／事件搜尋 | Tavily `DISCOVERY` | GDELT `DISCOVERY` | 發現公司、產業、全球與宏觀事件；material claim 必須回到原始 publisher／官方來源 |
| 交易所資料 | Nasdaq／NYSE 官方頁面或經核准 feed `AUTHORITY` | 無自動普通 fallback | corporate actions、上市狀態與公告；付費 feed、license 或不穩定 HTML 不得被默認為可用 |
| Metadata | Alpaca current asset metadata `AUTHORITY` | yfinance 顯示性欄位 `RESEARCH_SUPPLEMENT` | 公司名、產業、ticker、exchange；歷史 replay 另用 event-sourced security master 防止 symbol/venue 前視 |

P4固定零付費profile。SEC與部分政府bulk/公共端點可不使用key；FRED/ALFRED、BEA、EIA等免費服務可能
要求註冊key，BLS未註冊／註冊方案有不同配額。免費key仍是credential，必須使用source-specific typed
`SecretRef`；申請、Keychain寫入或真實GET前需當次授權。不得因免費而把rate limit視為無上限。

P4的ADR-039 SEC authority固定為同一CIK的submissions top-level `sic`及Company Facts exact allowlist：
`us-gaap/NetIncomeLoss`、`us-gaap/NetCashProvidedByUsedInOperatingActivities`、`us-gaap/Assets`、
`us-gaap/PaymentsToAcquirePropertyPlantAndEquipment`、`dei/EntityCommonStockSharesOutstanding`。每筆保留taxonomy、
concept、unit、period、FY/FP、form、accession、filed／accepted可用時間與content hash；不得以同義concept、今日值、
ticker或模型推測補缺。Sector採point-in-time SEC SIC Division A～J；缺失、衝突、future或未映射值為
`SECTOR_UNKNOWN`並禁止新增曝險，不使用GICS。

規劃依據：

- [FRED／ALFRED API](https://fred.stlouisfed.org/docs/api/fred/fred/)與
  [real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)；
- [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)；
- [Treasury Fiscal Data API](https://fiscaldata.treasury.gov/api-documentation/)、
  [BLS Public Data API](https://www.bls.gov/developers/api_signature_v2.htm)、
  [BEA API](https://apps.bea.gov/api/signup/)、[EIA API v2](https://www.eia.gov/opendata/documentation.php)；
- [Alpaca Corporate Actions](https://docs.alpaca.markets/us/reference/corporateactions-1)；
- [Nasdaq Symbol Directory](https://nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs)與
  [NYSE Corporate Actions](https://www.nyse.com/market-data/corporate-actions)；
- [GDELT data](https://gdeltproject.org/data.html)；
- [yfinance disclaimer](https://ranaroussi.github.io/yfinance/index.html)：非 Yahoo 官方產品，僅作個人研究補充，實作前仍須核對當時條款。

## 10. Point-in-time 與衝突規則

每筆 source record 依資料類別保存：`source_role`、`provider_record_id`、穩定 security identity、
`observation_period`、`published_at`、`discovered_at`、`available_at`、`retrieved_at`、`effective_at`、
`vintage_date`、`content_hash`、rights/licence 狀態及 supersession lineage。未知欄位不得以抓取時間或今日值猜補。

- 同一類別不同 authority 矛盾時標記 `DATA_CONFLICT`，禁止新增曝險；
- discovery／supplement 永遠不能升權成 authority；
- 來源失效可以降級研究 coverage，但不能降級交易所需的價格、上市狀態或 corporate-action gate；
- 新來源必須通過 exact-host/redirect、pagination、rate-limit、schema drift、rights、fixture、as-of 與 failure-injection gate；
- 真實 API key、下載或 production 呼叫均需要另行授權；本規劃本身不授權網路使用。

## 11. 拆股／合股確認來源政策

第一版自動保護只涵蓋 `forward_split` 與 `reverse_split`，不把 stock dividend、unit split、spin-off、merger、
name change 或其他 reorganization 偷偷納入同一 authority。自動退出的 `CONFIRMED` 必須同時具備：

1. 精確 security identity closure（symbol lineage 加 CIK、CUSIP 或經核准的等價穩定 ID）；
2. 明確事件類型、ratio 與 effective／ex-date；
3. 至少一個 SEC、issuer IR 或 listing exchange 正式公告；
4. 已讀來源彼此不矛盾，且每個來源的 `available_at <= decision_at`；
5. 原始內容 hash、URL／record ID 與確認時間可稽核。

只有 Alpaca、Tavily、GDELT、yfinance 或搜尋摘要時：立即禁止新買入並告警，但尚不足以自動平倉。正式來源確認後才可進 `CORPORATE_ACTION_CONFIRMED`；若來源撤回、ratio／日期改變或身份不閉合，維持禁止新增曝險並升級人工事件審查。
