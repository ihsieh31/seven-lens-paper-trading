# P3-A Implementation Session Prompt

請接手並完成 Seven-Lens Paper Trading 專案的 **P3-A Upstream／License／Contracts** 工作包。

工作目錄：

`/Users/zongen/Downloads/codex/trading`

## 必做起始動作

1. 完整閱讀 `PROJECT_HANDOFF.md`，它是本工作包唯一權威交接。
2. 閱讀 `DECISIONS.md` ADR-028、`docs/ARCHITECTURE.md` 第 2–5 節、`docs/ROADMAP_AND_ACCEPTANCE.md` 的 P3-A、`SECURITY.md`、`pyproject.toml`。
3. 執行 `git status --short`、`git diff --check`、`git log -3 --oneline`，辨識並保留目前所有使用者已核准的 dirty planning changes；不得 reset、checkout、清理或覆寫。
4. 檢查現有 `src/seven_lens/domain/value_objects.py`、`json_values.py`、tests 慣例，再開始實作。

## 唯一任務

依 `PROJECT_HANDOFF.md` 第 4–9 節完整實作 P3-A：

- 固定 TradingAgents commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`；
- 建立 Apache-2.0 `LICENSE` copy、`SOURCE_MANIFEST.json`、第三方 notices 與 planned-source inventory；
- 建立 dependency-free、immutable、strict、bounded、versioned P3 contracts；
- 建立 exact canonical wire encode/decode、golden fixtures、round-trip、resource-boundary 與 adversarial tests；
- 建立 source-invariant tests，證明 P3 contracts 沒有 broker／execution／network／credential capability；
- 更新 `PROJECT_HANDOFF.md` 的實作結果、`PROGRESS.md`、`WORKLOG.md`、`ISSUES.md`，只記錄實際證據。

主要 ownership：

```text
src/seven_lens/analysis/**
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

只在必要且不改變既有語意時小幅擴充 `domain/value_objects.py` 及其 tests。

## 強制邊界

- 不加入 Pydantic、LangGraph、JSON Schema runtime、provider SDK 或任何新 dependency；不改 `uv.lock`。
- 不複製／import upstream runtime Python；P3-A 只建立授權與 planned-source inventory。
- 不接 Agnes、OpenCode、OpenAI、Tavily、Alpaca；不讀取或要求任何 credential，不送這些服務的 API request。只允許為固定 SHA 的 license／source inventory 做無 credential、read-only GitHub retrieval。
- 不新增 DB migration/table/repository，不改 P2 execution、reconciliation、control、broker、Keychain 或 CI。
- 不實作 graph、analysts、debates、Portfolio Manager、Risk Engine、memory skill、event verifier、scheduling、target-to-quantity 或 order。
- 不讀取 repository 根目錄被忽略的 `skill/`。
- 不 stage、commit、push、建立 PR 或修改 remote／branch protection。
- `PortfolioProposal` 只是 request；不得生成 `OrderIntent` 或任何 broker action。
- schema/wire parser 不得寬鬆 coercion、自由文字 fallback、未知欄位忽略、靜默去重或 error echo。

## 實作要求

- 使用 Python 3.13 標準函式庫、`dataclass(frozen=True, slots=True)`、`StrEnum` 與既有 value objects／`JsonObject`。
- 所有 monetary／weight／confidence wire value 禁止 binary float；格式必須 canonical 且 tests 鎖定。
- sequence 必須 snapshot 成 tuple，exact type 驗證必須拒絕 bool-as-int、subclass、NaN／Infinity、negative zero、unknown／missing fields、duplicates、NUL、oversize 與 cross-field contradiction。
- 正常窗口候選上限分別 12／5；緊急窗口候選必須為零且 deadline 最多 3 分鐘；正常 deadline 最多 15 分鐘。
- Bull/Bear 與 Risk Debate round count 最大 2。
- Portfolio Manager request 必須符合 action／side／signed target weight 關係；confidence `< 0.65` 只能 `HOLD`。
- 第一份 proposal 與第二份 resubmission 的 attempt／supersedes 關係必須可證明；`RiskRejectionFeedback.review_round` 固定 1，不能形成第三輪。
- `PortfolioProposal` 必須有可執行的 exact boundary 驗證對應 `AnalysisInput`，不能只在文件聲稱 request symbols 屬於 input universe。
- 去識別化 contracts 禁止 account id、broker order id、raw broker payload、credential／Authorization material。

## 驗證順序

先跑 targeted：

```bash
uv run --locked pytest \
  tests/test_analysis_contracts.py \
  tests/test_analysis_contract_adversarial.py \
  tests/test_analysis_contract_source_invariants.py \
  -ra --tb=short
```

再跑完整 gates：

```bash
./scripts/verify_p1.sh
./scripts/run_postgres_integration.sh
git diff --check
```

不得把 Docker／環境錯誤寫成產品通過。若 default uv cache 權限失敗，可使用 isolated temporary `UV_CACHE_DIR`；不得改 lock 或降級依賴。PostgreSQL gate 必須是真實 PostgreSQL 16、零 required skip。

## 完成回報格式

先更新 `PROJECT_HANDOFF.md` 第 10 節，再回覆：

1. 結論：`P3-A implementation completed; pending independent acceptance` 或明確 blocker。
2. 實際新增／修改檔案。
3. Contract 與 license manifest 的關鍵設計。
4. targeted、Ruff、mypy、non-integration、PostgreSQL 16 的逐項實際結果與數量。
5. 未完成、偏差或需驗收者特別檢查的風險。
6. 明確聲明未使用 credentials／API、未改 P2、未 stage/commit/push。

不要自行宣告 P3-A Gate Closed；只有另一個獨立 session 驗收後才能關閉。
