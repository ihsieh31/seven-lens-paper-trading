# 七人分析方法論蒸餾規格

## 1. 目標與倫理邊界

蒸餾產物是可測試的 **doctrine package**，不是模仿公開人物的替身。系統只能表達「根據版本 X 的公開來源，這套分析框架會如何檢查問題」，不能表達「某人現在會買什麼」、假冒語氣、暗示合作或背書。

每套 doctrine 必須同時保留：

- 關注的 causal mechanisms；
- 接受與拒絕的證據類型；
- 分析步驟與問題清單；
- 典型反例、盲點、domain boundary；
- 何時棄權；
- 來源 coverage、日期與版本；
- held-out evaluation 結果。

## 2. 產物結構

```text
doctrines/<doctrine_id>/
  doctrine.yaml             身分、版本、coverage、license metadata
  principles.md             可遷移原則
  evidence_policy.md        證據層級、時效、引用規則
  workflow.md               分析流程與輸出契約
  counterexamples.md        失敗案例、反例、適用邊界
  source_manifest.jsonl     URL 與時間等 metadata，不提交受限全文
  evals/
    golden_cases.jsonl
    held_out_cases.jsonl
    adversarial_cases.jsonl
    scores.json
```

`doctrine.yaml` 至少含：

```yaml
id: serenity_supply_chain
version: 0.1.0
display_name: Serenity supply-chain doctrine
not_affiliated: true
coverage_start: null
coverage_end: null
last_verified_at: null
domains: []
prohibited_claims: []
source_counts: {}
eval_status: draft
```

## 3. 來源政策

### 3.1 優先級

1. 本人公開長文、網站、blog、公開簡報／課程／訪談逐字稿。
2. 本人 X 公開貼文的 canonical URL。
3. 可回鏈原文的第三方索引或 GitHub corpus。
4. 他人評論只能用來尋找原始資料，不可證明本人的方法論。

### 3.2 SourceManifest

每一項保存：

- canonical URL、author/publisher、title；
- published/retrieved/available timestamp；
- access method、query、coverage window；
- content hash、最小必要摘錄位置；
- primary/secondary、license status、redistribution status；
- 是否可回鏈、是否驗證原始 post id；
- fact/opinion/heuristic/case-study 標籤；
- superseded/deleted/unavailable 狀態。

「公開可看」不自動等於「可提交全文」。不明授權的 corpus 僅保留本機個人研究所需資料，Git repository 只記 manifest、hash 和自有摘要。

### 3.3 X 收集

- 不使用 X Developer API，亦不使用登入 cookie 或非授權 credential scraping。
- 以 Tavily、搜尋引擎、公開 X URL、作者網站與合法索引逐來源 discovery。
- 每個帖子以 canonical status id 去重，保存搜尋 query 和覆蓋區間。
- 搜尋不到或頁面不可讀即標示缺口，絕不讓 LLM補寫。
- 蒸餾是離線週期性工作，不是盤中必要服務。

## 4. 蒸餾 pipeline

```mermaid
flowchart TD
    D["Discover public sources"] --> M["Manifest and rights review"]
    M --> C["Immutable minimal capture"]
    C --> N["Normalize and deduplicate"]
    N --> L["Label fact / opinion / heuristic / case"]
    L --> P["Extract doctrine propositions"]
    P --> R["Find counterexamples and reversals"]
    R --> S["Synthesize versioned doctrine"]
    S --> E["Held-out and adversarial evals"]
    E -->|pass| F["Freeze release"]
    E -->|fail| P
```

### 4.1 Discovery

為每位人物預先定義 query matrix：姓名、handle、網站、主題、常用詞、ticker、年份、訪談／podcast。搜尋結果只是候選；必須開啟並確認原始內容。

### 4.2 正規化與標註

- 保留原文語言、另存可重建翻譯；翻譯不可覆蓋原文。
- thread/reply 依 id 重建，但不得把不同日期貼文拼成一個無時間的觀點。
- 把內容拆成 `FACT_CLAIM`、`OPINION`、`HEURISTIC`、`FORECAST`、`TRADE_DISCLOSURE`、`REVERSAL`。
- 股票價格表現不是方法論真假的直接標籤；先檢查當時可用證據與因果假設。

### 4.3 Proposition extraction

每個候選原則必須寫成：

```text
When <conditions>, examine <mechanism> using <preferred evidence>.
The view strengthens if <confirmations>.
It weakens or is invalid if <counterevidence>.
Do not apply when <domain boundary>.
Sources: <ids>.
```

至少兩個相互獨立來源或一份高上下文 primary long-form 才能進核心 doctrine。單一貼文只能是 provisional note。

### 4.4 反例蒐集

刻意搜尋：錯誤預測、立場反轉、刪文引用、未實現催化劑、同一框架的失敗案例、倖存者偏差、事後敘事。沒有 counterexamples 的 doctrine 不得發布。

## 5. 七套蒸餾計畫

本機 `skill/` 候選語料截至 2026-08-16 尚未做內容、來源、授權或完整性審查；存在不等於合格。正式 P3 必須先產生 SourceManifest 與 quarantine report，再開始 proposition extraction。

### 5.1 Howard Marks

- Domain：market cycles、risk、second-level thinking、investor psychology、contrarian discipline。
- 候選語料：`skill/Howard Marks.pdf`；正式審查時回到 Oaktree 官方 memos 與可核對公開發表。
- 注意：週期判斷必須有 `published_at`／`available_at`；不得把事後回顧寫回歷史 decision context。

### 5.2 Muddy Waters Research

- Domain：forensic accounting、disclosure quality、governance、related parties、asset/cash verification、fraud red flags。
- 候選語料：`skill/Muddy_Waters/`；正式審查時逐項核對官方研究、filing、監管／法院文件與公司回應。
- 注意：只作 long-only veto／risk lens；明確分離事實、指控、反駁與推論，不自動建立 short path。

### 5.3 Aswath Damodaran

- Domain：story-to-numbers、DCF、risk pricing、life cycle、optionality、terminal value。
- 候選語料：`skill/aswath_damodaran/`；primary sources 包含 NYU 教學頁、公開 lecture/slides/spreadsheets、blog、YouTube lectures。
- 注意：只萃取教學框架，不把某次估值的舊 input 當今日 fair value；估值結果必須展示假設敏感度而非單點精準值。

### 5.4 Serenity / @aleabitoreddit

- Domain：AI/semiconductor/optical/power supply chain、multi-hop BOM、qualification、capacity bottleneck。
- 候選語料：`skill/serenity-aleabitoreddit-data/`。
- 注意：正式審查時抽樣驗證 canonical X URL、coverage/dedup、時間、刪文狀態與擷取授權；只蒸餾有來源的方法論 proposition。

### 5.5 Terry Smith / Fundsmith

- Domain：quality compounders、ROIC、organic growth、reinvestment runway、balance-sheet discipline、capital allocation。
- 候選語料：`skill/terry_smith_fundsmith/`；正式審查時核對 Fundsmith 官方 owners' manual、annual letters 與 shareholder materials。
- 注意：把企業品質、可持續再投資與估值分開評估；過去績效或持倉不是未來推薦。

### 5.6 Michael Mauboussin

- Domain：expectations investing、base rates、competitive advantage、capital allocation、expected value、probabilistic decisions。
- 候選語料：`skill/michael_mauboussin/`；正式審查時核對作者或任職機構的正式發布版本。
- 注意：每個 base rate 必須記錄樣本、期間、分母與適用範圍；過時統計不得無條件沿用。

### 5.7 Lyn Alden

- Domain：fiscal dominance、monetary systems、liquidity、energy、dollar system、long-cycle balance sheets。
- 候選語料：`skill/Lyn_Alden/`；primary sources 包含官方免費文章/newsletter、公開 X、podcast/interview。
- 注意：長期 framework 與短期 market timing 分開評分；不得把宏觀正確直接映射為某個個股的短期買點。

## 6. 評估資料集

### 6.1 Golden cases

由 Sol + 人工規格化 20–30 個案例／doctrine，包含公開來源、合理結論、可接受分歧與必須棄權情境。

### 6.2 Held-out cases

- 建 doctrine 時完全不可見；
- 至少 30 個 case／doctrine，跨公司、年份、正反例；
- 版本發布後才解封計分；失敗案例進下一版 training/reference，不回改該次分數。

### 6.3 Adversarial cases

- 網頁內嵌 prompt injection；
- 假 canonical URL、錯誤作者、無日期 screenshot；
- 來源彼此矛盾；
- 只有付費文章標題；
- 舊觀點被新觀點推翻；
- ticker alias 誤判；
- 高人氣 secondary source 與低人氣 primary source 衝突。

## 7. 發布門檻

每套 doctrine v1 至少：

- primary-source citation coverage ≥ 90%；
- material claim entailment ≥ 95%；
- published/available date compliance = 100%；
- hallucinated source URL = 0；
- prompt injection success = 0；
- `ABSTAIN` case recall ≥ 90%；
- domain-boundary violation ≤ 2%；
- 至少 10 個反例／反轉或明確記錄找不到的搜尋證據；
- 所有來源具 rights/redistribution 狀態；
- 人名從 prompt 移除後仍能按 doctrine 執行，證明不是角色扮演。

未達標者可留在 `draft` 作研究，不可進 daily committee。

## 8. 更新與漂移

- 每週只做 source discovery 和 diff；不自動發布新 doctrine。
- 每月或重大方法論反轉才建立 candidate version。
- 新版需 regression + held-out extension；舊 run 永遠引用原 doctrine version。
- 若 90 天沒有可驗證的新來源，短期立場類欄位標示 stale；永久原則不因沒有發文而自動失效。
- source 被刪除時保留 tombstone/hash，不把無法再次驗證的 material claim 擴散到新版。

## 9. GitHub 開源技能採用 Gate

任何第三方 skill 只有在以下全過後才能 vendoring：

1. 明確 LICENSE 和來源再散布權；
2. corpus 可回鏈原始 URL，無 credential/cookie scraping；
3. 沒有 auto-trade、shell、secret、任意網路權限；
4. 有時間戳、反例、更新和 eval 設計；
5. 手動抽樣至少 100 筆或 5% corpus；
6. dependency/security/secret scan；
7. 固定 commit SHA，保存本地 patch 與 provenance；
8. 通過本專案 schema 和 held-out eval。

目前結論：沒有一套開源資產可直接滿足七人需求。可借鑑 retrieval/evidence packet 設計，但蒸餾與驗收由本專案自行掌握。
