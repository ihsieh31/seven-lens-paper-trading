# Future Analyst Plugin／七人蒸餾保留規格

狀態：`DEFERRED`（2026-08-21）

本文件保留舊七人蒸餾設計，避免未來重新研究時遺失邊界；它不再是 P3、daily analysis、Shadow 或 Paper Trading 的必要依賴。未經使用者重新核准，不讀取或審查 `skill/`、不做 proposition extraction、不建立 doctrine、不把任何候選語料推到 repository。

目前主線請讀 `docs/TRADINGAGENTS_ASSESSMENT.md`、`docs/ARCHITECTURE.md` 與 `docs/ROADMAP_AND_ACCEPTANCE.md`：P3 採完整 TradingAgents 四分析員→兩輪 Bull/Bear→Research Manager→Trader→兩輪 Risk Debate→LLM Portfolio Manager，輸出 strict `PortfolioProposal`；P4 deterministic Risk 保留唯一核准權。

## 1. 未來插件定位

若日後找到合適且可驗證的蒸餾方法，七套方法論只能作為 **額外 Analyst Plugin**，不能取代四個核心分析員，也不能繞過 Research Manager、Trader、Risk Debate、Portfolio Manager、P4 hard risk 或 P2 execution boundary。

```text
Frozen AnalysisInput
        ├─ Technical / Market Analyst
        ├─ Fundamentals Analyst
        ├─ News Analyst
        ├─ Sentiment Analyst
        └─ Optional Analyst Plugins (disabled by default)
                    └─ Future seven doctrine lenses
                          ↓
              Bull / Bear → Research Manager → Trader
                          ↓
               Risk Debate → Portfolio Manager
                          ↓
                  PortfolioProposal (request only)
```

每個插件必須：

- 使用與核心 analyst 相同的 frozen `as_of`、source/data snapshot 與 citation policy；
- 輸出同一 versioned `AnalystReport` contract，不得輸出 target weight、quantity、order type 或 broker action；
- 可被獨立停用、ablation、timeout 與 fail closed；
- 不得因名稱、名氣或人數獲得固定投票權；
- 不得取得 broker credential、portfolio ledger write、shell 或任意 network 權限。

## 2. 候選七套插件（保留但停用）

1. Howard Marks：market cycles、risk、second-level thinking、investor psychology。
2. Muddy Waters Research：forensic accounting、disclosure quality、governance、fraud red flags。
3. Aswath Damodaran：story-to-numbers、valuation、risk pricing、terminal assumptions。
4. Serenity / `@aleabitoreddit`：AI／semiconductor supply chain、multi-hop BOM、qualification、capacity bottleneck。
5. Terry Smith / Fundsmith：quality compounders、ROIC、reinvestment、capital allocation。
6. Michael Mauboussin：expectations investing、base rates、competitive advantage、expected value。
7. Lyn Alden：fiscal／monetary regime、liquidity、energy、dollar system、long-cycle balance sheets。

這些名稱只描述未來可能研究的公開方法論，不代表本人參與、授權、背書或當前投資意見。若啟用，只蒸餾可引用的分析框架、證據偏好、反例、失效條件與 domain boundary；禁止人格／語氣模仿。

## 3. 本機 corpus 邊界

本機 `skill/` 候選語料截至 2026-08-16 只確認路徑與容量，未審查內容、來源、授權、完整性、重複、時間或可蒸餾性。存在不等於合格。

- `skill/Howard Marks.pdf`
- `skill/Muddy_Waters/`
- `skill/aswath_damodaran/`
- `skill/serenity-aleabitoreddit-data/`
- `skill/terry_smith_fundsmith/`
- `skill/michael_mauboussin/`
- `skill/Lyn_Alden/`

整個 `skill/` 必須維持 Git ignore。公開可看不等於可提交全文；未確認授權的 corpus 不能被 vendoring、commit、push 或當成已驗收 evidence。

## 4. 重新啟動條件

只有使用者明確決定恢復插件研究，且先同意一個可測試的蒸餾方法後，才能建立獨立 Future Plugin 工作包。重新啟動不得改寫 P3/P4 gate，並需先完成：

1. SourceManifest schema 與逐來源 provenance/rights/redistribution review；
2. quarantine、dedup、coverage、deleted/unavailable source report；
3. primary-source citation 與 `published_at/retrieved_at/available_at`；
4. 人工核准的 proposition extraction、counterexample 與 domain-boundary 方法；
5. golden/held-out/adversarial datasets；
6. plugin-level ablation、correlation、calibration 與增量價值 gate；
7. 明確的停止條件、owner、時程與不影響主線的資源上限。

在第 1–3 項通過前不得進 proposition extraction；在全部通過前不得進 daily analysis。

## 5. 若恢復時的來源與產物契約

SourceManifest 至少保存 canonical URL、author/publisher、title、published/retrieved/available timestamps、access method、coverage window、content hash、最小必要摘錄位置、primary/secondary、license/redistribution、superseded/deleted/unavailable 與 provenance。

候選原則必須能寫成：

```text
When <conditions>, examine <mechanism> using <preferred evidence>.
The view strengthens if <confirmations>.
It weakens or is invalid if <counterevidence>.
Do not apply when <domain boundary>.
Sources: <ids>.
```

每個 plugin release 至少需證明：

- primary-source citation coverage ≥ 90%；
- material claim entailment ≥ 95%；
- published/available date compliance = 100%；
- hallucinated source URL = 0；
- prompt injection success = 0；
- `ABSTAIN` case recall ≥ 90%；
- domain-boundary violation ≤ 2%；
- source rights/redistribution 狀態完整；
- 移除人名後仍按方法論執行；
- 相對四核心分析員 baseline 有 held-out/forward incremental value，否則保持停用。

## 6. 第三方 skill 採用 Gate

任何第三方 skill 必須同時具備明確 LICENSE、可回鏈原始 URL、無 credential/cookie scraping、無 auto-trade/shell/secret/任意 network 權限、時間戳／反例／eval 設計、人工抽樣、dependency/security/secret scan、固定 commit SHA 與本專案 schema/eval 證據。

目前沒有第三方資產達到此 Gate，也沒有已核准的蒸餾方法。因此此功能保持 `DEFERRED/DISABLED`。
