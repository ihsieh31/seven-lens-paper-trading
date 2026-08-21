# P3-A Independent Acceptance Session Prompt

請對 Seven-Lens Paper Trading 專案的 **P3-A Upstream／License／Contracts** 實作做獨立驗收。

工作目錄：

`/Users/zongen/Downloads/codex/trading`

你的任務是驗證與報告，不是相信 implementation agent 的完成敘述。除非使用者另外明確要求修復，**不要修改 production code、tests 或規劃文件**。

## 必做起始動作

1. 完整閱讀 `PROJECT_HANDOFF.md`、`docs/P3A_IMPLEMENTATION_PROMPT.md`、`DECISIONS.md` ADR-028、`docs/ARCHITECTURE.md` 第 2–5 節與 `SECURITY.md`。
2. 執行 `git status --short`、`git diff --stat`、`git diff --check`、`git log -3 --oneline`；列出 P3-A 實際變更與原有 dirty planning changes，確認沒有被還原。
3. 逐檔檢查 source、fixtures、tests、第三方 license／manifest；不得只看測試名稱或 implementation report。
4. 以 source inspection、focused PoC、完整 gates 三種證據獨立判斷。

## 驗收範圍

### A. Scope 與安全邊界

- 變更是否只在 P3-A owned paths；是否偷改 dependency、lockfile、migration、P2 execution、broker、Keychain、CI。
- `seven_lens.analysis` 是否完全沒有 execution／infrastructure／broker／network／provider SDK import 或 side effect。
- 是否沒有 live endpoint、live switch、OrderIntent、target-to-quantity、broker action 或 credential access。
- 是否沒有讀取／納入被忽略的 `skill/` corpus；是否沒有擴張 Tavily account-pool 權限。
- 是否沒有新增 Pydantic、LangGraph 或其他 dependency。

### B. Upstream／license 可重現性

- `SOURCE_MANIFEST.json` repository／commit 必須精確等於：
  `https://github.com/TauricResearch/TradingAgents`／`a33fd4c0f134485a43553a2c23a63cb14adbd88f`。
- `third_party/tradingagents/LICENSE` SHA-256 必須精確等於：
  `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`。
- manifest 必須記錄 Apache-2.0、`notice_present: false`、planned source paths、retrieval time、`runtime_code_vendored: false`。
- repository 不得已提前 vendoring/import upstream runtime Python；`THIRD_PARTY_NOTICES.md` 不得虛構 endorsement 或 NOTICE。
- 若網路可用，從固定 commit 重新取得 LICENSE／source tree 做 hash/path 比對；不要對浮動 `main` 驗收。

### C. Contract 完整性

逐項確認 `PROJECT_HANDOFF.md` 第 6 節列出的 enums/value types 與 13 類 contracts 全部存在，特別檢查：

- `AnalysisInput` holdings/candidates/focus、12／5／0 上限與 15m／3m deadlines；
- 四種 `AnalystReport` 與禁止 order semantics；
- Bull/Bear、Risk Debate round count `0..2` 及 complete state 兩輪；
- `ResearchConclusion`、`TraderPlan` 沒有 shares/cash/order/broker output；
- `PortfolioSnapshot` 是完整且去識別化的 positions/open orders/same-day fills/borrow/remaining limits，content hash 由 canonical sanitized fields 重算／驗證；
- `PortfolioRequest` action／side／signed weight、confidence `<0.65 => HOLD`、same-day reason enum；
- `PortfolioProposal` attempt 1／2 與 supersedes invariants，並以實際 `validate_against(AnalysisInput)` 或等價 boundary 證明 request symbols 不得超出 input universe；
- `RiskRejectionFeedback.review_round == 1` 且不能建立第三次重申。

### D. Strict wire 與資源安全

檢查 `to_wire/from_wire` 是否 exact、canonical、bounded：

- unknown/missing fields 拒絕；沒有 permissive coercion／free-text fallback；
- bool-as-int、enum subclass、float、NaN／Infinity、exponent、negative zero、前後空白拒絕；
- UUID/timestamp/schema version 使用既有 canonical value objects；
- sequence construction 後修改原 list/dict 不會改 contract；
- duplicate symbol/id/evidence、NUL、overlong Unicode、deep/wide/oversized JSON、cycle fail closed；
- exception 不 echo marker、payload、credential-like text；
- wire output 不含 account id、broker order id、Authorization、raw broker payload；
- round-trip 與 golden fixtures 是實際完整物件，不是只測 happy-path 的空殼。

## 必做 adversarial PoC

除了既有 tests，驗收者至少親自建立／執行以下 cases；可用臨時 one-off script 或 pytest `-k`，不要修改 repository：

1. `True` 放入 attempt／round/count，必須拒絕。
2. target weight `-0.000000`、`1e-1`、Python float，必須拒絕。
3. PRIMARY 13 candidates、SECONDARY 6、EMERGENCY 1 candidate，必須拒絕。
4. normal deadline `15m + 1µs`、emergency `3m + 1µs`，必須拒絕。
5. SHORT positive weight、CLOSE nonzero、低 confidence OPEN，必須拒絕。
6. proposal attempt 2 無 superseded id、attempt 1 有 superseded id，必須拒絕。
7. Risk rejection round 2 或 empty reason codes，必須拒絕。
8. duplicate evidence／symbol、holding-candidate overlap，必須拒絕。
9. 注入 `account_id`、`broker_order_id`、`Authorization` unknown fields，必須拒絕且錯誤不得 echo 值。
10. 建構後 mutation 原始 lists，contract/wire hash 必須不變。

任何一項可繞過即為 blocker，不得用「一般測試全綠」抵消。

## 必跑命令

```bash
uv lock --check --offline
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest \
  tests/test_analysis_contracts.py \
  tests/test_analysis_contract_adversarial.py \
  tests/test_analysis_contract_source_invariants.py \
  -ra --tb=short
uv run --locked pytest -m "not integration" -ra --tb=short
./scripts/run_postgres_integration.sh
git diff --check
```

PostgreSQL 必須是真實 disposable PostgreSQL 16 且零 required skip。不得把缺 Docker、cache／sandbox 權限或 dependency 問題當成產品成功；記錄根因與未取得的 evidence。

## Gate 判定

只有以下全部成立才可判 `P3-A Accepted`：

- scope／security／license／contracts／wire／adversarial 全部通過；
- targeted、Ruff、mypy、完整 non-integration、PostgreSQL 16 全綠；
- 無 required skip、無未解 High/Critical finding；
- implementation 沒有自行宣稱獨立驗收、沒有 stage/commit/push 或 credential/API 使用；
- 文件實作結果與可重現證據一致。

否則判 `P3-A Rejected` 或 `Blocked`，不要模糊寫「大致通過」。

## 回報格式

1. **Gate：Accepted / Rejected / Blocked**。
2. Findings 依 Critical→High→Medium→Low，附絕對檔案路徑與精確行號、重現方法、影響。
3. Upstream/license hash 與 scope diff 結果。
4. 十個 adversarial PoC 的逐項結果。
5. 每條命令的 exit code、passed/deselected/skipped 數量。
6. 若拒絕，提供一段可直接貼給修復 session 的 bounded prompt；不要自行修。
7. 明確說明是否有任何 evidence 未取得，以及原因。
