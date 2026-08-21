# P3-A Upstream／License／Contracts 實作交接包

最後更新：2026-08-21

專案：`/Users/zongen/Downloads/codex/trading`

目前 gate：P3-A implementation completed; pending independent acceptance

本輪唯一目標：完成固定上游來源／授權清單與 dependency-free、versioned、strict P3 contracts

權威決策：`DECISIONS.md` ADR-028

實作提示詞：`docs/P3A_IMPLEMENTATION_PROMPT.md`

獨立驗收提示詞：`docs/P3A_ACCEPTANCE_PROMPT.md`

## 1. 已驗證基線

- P0、P1、P2 已完成；P2 Gate Closed。
- 目前 `main`／`origin/main` 基線為 `22a121f64c5520d4e06d774ed04ce1dce37f3700`；P2 exact-main CI run `32361310657` 已通過。
- P2 關門不授權真實下單。真實 Alpaca Paper order mutation 仍只屬 P7。
- 現有工作樹含使用者已核准的 P3 規劃文件修改與本 handoff／prompt 變更；不得 reset、checkout、清理或覆寫。
- 未經使用者另行要求，不 stage、commit、push、建立 PR 或修改 remote／branch protection。

## 2. 不可變更的安全邊界

1. Paper-only；不得加入 live endpoint、live adapter 或 live switch。
2. P3 contract／analysis code 不得 import、呼叫或取得 Alpaca、execution、order、ledger write、broker credential。
3. 本工作包不得使用任何 Alpaca、Agnes、OpenCode、OpenAI、Tavily credential，也不得呼叫其 API。
4. schema／wire input 一律視為不可信；unknown field、錯誤 exact type、非 canonical 值、超限、duplicate、矛盾 cross-field 必須 fail closed。
5. LLM Portfolio Manager 只有提案權；本工作包不實作 Risk approval、target-to-quantity、OrderIntent 或 execution。
6. 不讀取或審查 repository 根目錄被忽略的 `skill/` 七人 corpus；Future Analyst Plugin 不屬 P3-A。
7. Tavily 七帳號輪替仍是 `OPEN-007`；不得在 P3-A 擴張或繞過現有 fail-closed compliance gate。

## 3. P3-A 的目的

P3-A 不是把 TradingAgents 跑起來，而是先固定未來所有 P3 模組必須遵守的語言。完成後應具備：

- 可證明的 TradingAgents 固定 commit、Apache-2.0 license 與 planned-source manifest；
- 不依賴 LangGraph、Pydantic 或任何 provider SDK 的 domain contracts；
- immutable `dataclass(frozen=True, slots=True)`／`StrEnum` 型別；
- exact、bounded、canonical wire encode/decode；
- 代表四分析員、兩種 debate、Research Manager、Trader、完整持倉、Portfolio Manager proposal 與第一次 Risk rejection feedback 的 contracts；
- golden fixtures、round-trip、mutation、resource-boundary 與 adversarial tests；
- source-level tests 證明 analysis contracts 沒有 broker／execution／network capability。

## 4. 固定 upstream 與授權

固定來源：

- repository：`https://github.com/TauricResearch/TradingAgents`
- commit：`a33fd4c0f134485a43553a2c23a63cb14adbd88f`
- license：Apache License 2.0
- 該 commit 的 `LICENSE` SHA-256：`c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`
- 已核對 repository root 沒有 `NOTICE`；manifest 必須明確記錄 `notice_present: false`，不得假造 NOTICE。

P3-A 建立：

```text
third_party/tradingagents/LICENSE
third_party/tradingagents/SOURCE_MANIFEST.json
third_party/tradingagents/README.md
THIRD_PARTY_NOTICES.md
```

`LICENSE` 必須是固定 commit 的原文且 hash 相符。`SOURCE_MANIFEST.json` 至少保存 repository、commit、license id／hash、retrieval time、`notice_present`、planned source paths 與 `runtime_code_vendored: false`。

本工作包只做來源／授權 inventory，不複製或 import upstream runtime Python。後續 P3-C／P3-D 才按 manifest 逐檔移植並標示修改，避免現在提前引入 LangGraph、Pydantic、provider 或 data side effects。

planned source paths 至少盤點：

```text
tradingagents/agents/analysts/{market,fundamentals,news,sentiment}_analyst.py
tradingagents/agents/researchers/{bull,bear}_researcher.py
tradingagents/agents/managers/{research_manager,portfolio_manager}.py
tradingagents/agents/trader/trader.py
tradingagents/agents/risk_mgmt/{aggressive,conservative,neutral}_debator.py
tradingagents/agents/utils/{agent_states,memory,rating,structured}.py
tradingagents/agents/schemas.py
tradingagents/graph/{analyst_execution,conditional_logic,propagation,reflection,setup,trading_graph}.py
```

以上 `{...}` 只是 handoff 的閱讀縮寫；`SOURCE_MANIFEST.json` 必須展開成逐一、精確的實際路徑。清單是 later-work inventory，不代表全部檔案最終都會複製。

## 5. 程式 ownership 與允許變更

主要 owned paths：

```text
src/seven_lens/analysis/__init__.py
src/seven_lens/analysis/contracts.py
tests/test_analysis_contracts.py
tests/test_analysis_contract_adversarial.py
tests/test_analysis_contract_source_invariants.py
tests/fixtures/p3a_contracts/**
third_party/tradingagents/**
THIRD_PARTY_NOTICES.md
PROJECT_HANDOFF.md
PROGRESS.md
WORKLOG.md
ISSUES.md
```

只有在 contract 真正需要且不改變既有語意時，才可小幅擴充 `src/seven_lens/domain/value_objects.py` 及其既有 tests。不得改 migrations、P2 execution/application/infrastructure、broker config、Keychain、dependencies、lockfile 或 CI workflow。

## 6. Contract 實作規則

### 6.1 共通規則

- 使用現有 `RunId`、`SchemaVersion`、`UtcTimestamp`；wire schema 初版固定 `1.0.0`。
- 新 contract 使用標準函式庫；不得新增 Pydantic、LangGraph、JSON Schema runtime、provider SDK。
- exact type：`bool` 不得冒充 `int`，enum subclass／陌生字串不得被寬鬆接受。
- 所有 sequence 在 construction 時 snapshot 成 tuple；不得保留 caller 可變 alias。
- 所有 identifier、symbol、text、list count、UTF-8 bytes、整份 wire document 都有明確上限。
- duplicate symbol／id／evidence ref 一律拒絕；不得靜默去重。
- 所有 decimal wire value 使用 canonical decimal string；禁止 binary float、NaN、Infinity、指數表示法、前後空白與負零。
- `to_wire()` 只輸出 JSON-safe exact object；`from_wire()` 要求 exact field set，unknown／missing field 均拒絕。
- canonical JSON 需沿用現有 `JsonObject` budget／serializer；錯誤訊息不得 echo 原始 payload、prompt、account material 或超長 marker。
- 不保存 chain-of-thought；只保存 bounded summary、claims、arguments、evidence refs 與 reason codes。

### 6.2 必要 enum／value types

至少定義：

- `AnalysisWindow`: `PRIMARY | SECONDARY | EMERGENCY`
- `AnalysisStatus`: `VALID | INVALID | ABSTAIN`
- `AnalystRole`: `TECHNICAL | FUNDAMENTALS | NEWS | SENTIMENT`
- `ResearchRating`: `BUY | OVERWEIGHT | HOLD | UNDERWEIGHT | SELL`
- `ProposalAction`: `OPEN | INCREASE | REDUCE | CLOSE | HOLD`
- `PositionSide`: `LONG | SHORT | FLAT`
- `SameDayExitReason`: `DOWNSIDE_BAND_EXCEEDED | THESIS_INVALIDATED | MATERIAL_NEW_EVENT | BORROW_LIQUIDITY_ANOMALY | HARD_RISK_TRIGGER`
- closed `ProposalReasonCode` 與 `RiskRejectionCode`；至少涵蓋 technical／fundamental／news／sentiment／valuation／rebalance、cash／buying power／max symbols／single-name／gross／net／turnover／borrow／open-order conflict／stale snapshot／same-day exit／data conflict／schema invalid。

權重與 confidence 建議以 canonical decimal value object 表示：target weight 範圍 `-0.150000..0.150000`，confidence 範圍 `0.0000..1.0000`；wire 固定位數或另一個同樣 exact、可解釋且無 float ambiguity 的格式可以接受，但必須在 contract docstring、fixtures 與 tests 固定，不能混用。

### 6.3 必要 contracts

至少實作以下 immutable types：

1. `AnalysisInput`
   - common metadata、window、deadline、portfolio snapshot、holding symbols、candidate symbols、focus symbols、evidence/data snapshot refs。
   - `PRIMARY`：候選最多 12；`SECONDARY`：最多 5；`EMERGENCY`：候選必須為空，focus 必須是既有持倉。
   - holdings 必須精確等於 snapshot positions symbols；candidate 不得與 holdings 重疊。
   - normal deadline 最多 `as_of + 15m`；emergency 最多 `as_of + 3m`。
2. `AnalystReport`
   - role、symbol、status、summary、observations、material claims、citation/evidence refs、counterevidence、missing evidence、risks、catalysts、invalidators、confidence、input/version refs。
   - 不得含 quantity、order type、broker action 或 unrestricted target。
3. `InvestmentDebateState`
   - bull/bear bounded arguments、verified/disputed claims、unresolved conflicts、round count；只能 `0..2`，complete state 必須兩輪。
4. `ResearchConclusion`
   - symbol、五級 rating、bounded summary、drivers、risks、invalidators、evidence、confidence、status/version refs。
5. `TraderPlan`
   - symbol、directional research action、bounded reason codes/evidence/entry or downside bands；不得含 shares、cash amount、order type 或 broker semantics。
6. `RiskDebateState`
   - aggressive/conservative/neutral bounded arguments、unresolved conflicts、round count；只能 `0..2`，complete state 必須兩輪。
7. `PortfolioPosition`
   - symbol、side、quantity、signed weight、average entry/current price、market value、unrealized P&L、realized P&L today、opened timestamp／same-day flag；不含 account id。
8. `OpenOrderSummary`、`SameDayFillSummary`、`BorrowStatus`
   - 只保留分析需要的 bounded fields；不得含 broker order id、account id、credential、raw broker payload。
9. `RemainingLimits`
   - remaining slots、long/short/total gross、net lower/upper room、single-name、turnover；只是 snapshot data，不在 P3-A 實作 Risk Engine。
10. `PortfolioSnapshot`
    - as-of、NAV、cash、buying power、全部 positions、open orders、same-day fills、borrow statuses、remaining limits、content hash；需驗證 unique symbols／references 與去識別化。content hash 必須由 canonical sanitized fields 重算／驗證，不能信任 caller 任意字串。
11. `PortfolioRequest`
    - symbol、action、side、target weight、confidence、evidence ids、closed reason codes、invalidators、optional same-day exit reason。
    - LONG 權重必須正、SHORT 必須負、FLAT 必須零；CLOSE 必須 FLAT/zero；OPEN/INCREASE 不得 FLAT/zero。
    - confidence `< 0.65` 時只允許 `HOLD`。
12. `PortfolioProposal`
    - proposal id、attempt `1|2`、optional superseded proposal id、analysis input id／universe hash、snapshot hash、window、requests、version refs、expiration、status。
    - attempt 1 不得有 superseded id；attempt 2 必須有。requests symbol 唯一；提供明確的 `validate_against(AnalysisInput)` 或等價 exact boundary，證明 requests 不得超出該 input 的 holdings + candidates，不能只靠 caller 約定。
    - 它是 request，不是 Risk approval 或 order。
13. `RiskRejectionFeedback`
    - rejected proposal id、review round 固定 `1`、non-empty closed rejection codes、rejected symbols、remaining limits、constraints snapshot hash、review timestamp。
    - contract 本身不能建立第三輪；第二份 proposal若再拒絕，由 P4 產生 `NO_TRADE`，不回 P3 graph。

若實作者拆成數個小型 contract module，可以接受；但不得形成循環依賴或引入 P2／infrastructure imports。

## 7. 必要 fixtures 與測試

Golden fixtures 至少包含：

- primary：多個既有 long/short positions + 12 candidates；
- secondary：全部 positions + 5 candidates；
- emergency：只含受影響／相關 holdings、零 candidate、3 分鐘 deadline；
- first proposal、Risk rejection feedback、second proposal；
- invalid／abstain analyst/report cases；
- long open、short open、reduce、close、hold requests。

Adversarial tests 至少覆蓋：

- bool-as-int、float/NaN/Infinity、noncanonical decimal、negative zero；
- nil/noncanonical UUID、naive/non-UTC/noncanonical timestamp；
- unknown/missing fields、enum casing、subclass、mutable alias；
- duplicate symbols/evidence ids、holding/candidate overlap、candidate count 13／6／emergency nonzero；
- deadline 超過 15m／3m；
- LONG negative、SHORT positive、FLAT nonzero、CLOSE nonzero、OPEN zero；
- confidence 0.6499 配非 HOLD；
- attempt/supersedes 矛盾、Risk feedback round 不為 1 或空 rejection codes；
- account id、broker order id、credential-like／Authorization fields、raw broker payload；
- overlong Unicode、NUL、deep/wide/oversized JSON、cycle 與 error-message non-echo；
- source import scan：`seven_lens.analysis` 不 import `execution`、`infrastructure`、broker/provider/network SDK，且不含 live endpoint。

## 8. 明確不做

- 不執行 TradingAgents graph，不接 Agnes／OpenCode／GPT。
- 不新增 API client、Keychain refs、model routing、thinking/effort mapping。
- 不抓 market/news/SEC/Tavily data，不做 SourceManifest ingestion。
- 不實作 memory skill、reflection persistence、event verifier。
- 不實作 deterministic Risk rules、one-rejection application service、portfolio sizing、quantity 或 order。
- 不新增 DB table／migration／repository。
- 不修改 P2 execution、reconciliation、control、broker adapter。
- 不讀 `skill/`，不做七人蒸餾。

## 9. 完成條件

P3-A 只有同時滿足以下項目才可回報完成：

1. 固定 commit/license hash/source manifest 與第三方 notice 可由 source 重現。
2. 所有必要 contracts、wire round-trip、golden fixtures 與 adversarial tests 完成。
3. 沒有新 runtime/dev dependency、lockfile、migration、broker/provider/API 或 credential change。
4. targeted tests、Ruff format/check、mypy strict、完整 non-integration tests 全綠。
5. 真實 PostgreSQL 16 integration 完整回歸全綠且零 required skip；雖 P3-A 不改 DB，仍需證明未破壞 P1/P2。
6. `git diff --check` 通過；變更僅在核准 scope，既有使用者文件修改未被還原。
7. 更新本 handoff 的「實作結果」區、`PROGRESS.md`、`WORKLOG.md`、`ISSUES.md`；只能寫實際命令與結果，不得先宣稱獨立驗收通過。
8. 未 stage、commit、push，除非使用者在新 session 另行明確授權。

## 10. 實作結果（由 implementation session 填寫）

目前：`P3-A implementation completed; pending independent acceptance`。本 session 不宣告
P3-A Gate Closed。

變更檔案：新增 `src/seven_lens/analysis/{__init__,contracts}.py`、三個 P3-A test modules、
`tests/fixtures/p3a_contracts/golden_bundle.json`、`third_party/tradingagents/{LICENSE,
SOURCE_MANIFEST.json,README.md}` 與根目錄 `THIRD_PARTY_NOTICES.md`；同步本 handoff、
`PROGRESS.md`、`WORKLOG.md`、`ISSUES.md`。未修改 dependency、`uv.lock`、migration、P2
application/execution/infrastructure、CI 或 remote 設定。

設計結果：固定 TradingAgents commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`；
Apache-2.0 LICENSE SHA-256 實算為
`c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`；manifest 展開 23
個 later-work source paths，記錄 `notice_present: false` 與 `runtime_code_vendored: false`。
contracts 使用 Python 3.13 stdlib、frozen/slots dataclass、StrEnum、固定 1.0.0 schema、固定小數
字串、exact fields/types、tuple snapshot、JsonObject budgets、重算 snapshot/universe hashes與
`PortfolioProposal.validate_against(AnalysisInput)`。

targeted tests：
`uv run --locked pytest tests/test_analysis_contracts.py
tests/test_analysis_contract_adversarial.py tests/test_analysis_contract_source_invariants.py
-ra --tb=short`，`70 passed`。

Ruff／mypy／non-integration：`./scripts/verify_p1.sh` exit 0；uv locked checks通過；Ruff
format/check 通過；mypy strict `100 source files` 無 issue；non-integration `746 passed,
91 deselected`。

PostgreSQL 16 integration：`./scripts/run_postgres_integration.sh` exit 0；真實 disposable
PostgreSQL 16 `83 passed, 8 deselected, 0 skipped`。

已知問題／偏差：P3-B~F、provider smoke、semantic parity、point-in-time ingestion、Risk Engine
與 memory 均仍未實作，依範圍保留 Open；P3-A 尚待另一個獨立 session 使用
`docs/P3A_ACCEPTANCE_PROMPT.md` 驗收。實作期間只對固定 SHA 做無 credential、read-only GitHub
LICENSE/tree retrieval；未使用任何 credential，未呼叫 broker/data/model API，未讀 `skill/`，
未 stage/commit/push。

下一步：交給獨立 acceptance session，不由 implementation session 自行關閉 P3-A Gate。
