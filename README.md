# Seven-Lens Paper Trading System

個人使用、Paper-only 的美股研究與交易系統。`Seven-Lens` 名稱是歷史相容識別；目前主線採
TradingAgents-style 多角色研究，七人蒸餾已降為停用的 Future Analyst Plugin。

LLM 只能產生有來源、符合 schema 的研究／組合提案；deterministic Risk 才能核准，P2
execution 只能處理已核准的 Paper `OrderIntent`。本專案沒有 Alpaca live endpoint、live adapter
或自動升級實盤的 gate。

## 目前進度

| 階段 | 狀態 | 說明 |
|---|---|---|
| P0 規格／治理 | Closed | 核心範圍、Paper-only 與風控邊界已建立 |
| P1 基礎／權威狀態 | Closed | typed config、Keychain、PostgreSQL、telemetry、CI 已驗收 |
| P2 Paper 執行安全 | Closed | final remediation 與 exact-SHA CI 已完成；不授權真實下單 |
| P3-A upstream／contracts | Closed | 固定 upstream、license inventory、strict contracts 已驗收 |
| P3-B+C evidence／research pipeline | Closed | P3-B與P3-C均經獨立驗收Accepted；已發布至`main` |
| P3-D risk／proposal | Accepted | 2026-08-24授權單session反覆重審完成；零High/Medium blocker |
| P3-E | Accepted | authorized live六案例6/6與PG audit/full regression通過 |
| P3-F | Implementation completed; pending independent acceptance | V12 live完整跑完：260/260 strict且全正確、violations=0、transport 100%；待fresh session獨立驗收 |
| P4～P8 | Not started | 不得提前宣告能力或繞過後續 gate |

P3-B+C已發布於commit `55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`；本機與遠端
`main`一致。exact-SHA GitHub Actions run `32558983841`的`quality-unit`與
`postgres-integration`均成功。發布前獨立驗收證據：Ruff／format／mypy／lock全綠，
non-integration `809 passed, 102 deselected`，真實PostgreSQL 16
`94 passed, 8 deselected, 0 skipped`。

P3-D工作包維持**Accepted**。P3-E final authorized live batch六案例6/6成功、6 PG audit rows，
full `1174 passed, 165 deselected`、PG16 `150 passed, 15 deselected, 0 skipped`，狀態為**Accepted**。
P3-F V10 source-only split與offline report已完成：split
`237620d1faefaa797f16a4c5e784ef113491cbaa8859a88977dae9c19c56ae63`、report
`aea1b77c94e2482b62b0fc40209f216f7629fa77a719679ce1008c3489622c38`、616/616。P3-F尚未Accepted；
新live evidence未授權／未執行，P4仍未開始。

## 核心邊界

- 只允許 Alpaca Paper；未知、缺失、過期、矛盾或未授權狀態一律 fail closed。
- analysis workers 沒有 broker credential、order/ledger write、shell 或任意 network capability。
- P3-B+C 只到 `TraderPlan`；Risk Debate／Portfolio Manager 留 P3-D，真實 provider 留 P3-E，
  reflection／memory／evals 留 P3-F。
- P4 deterministic Risk 才能核准 target、計算 quantity 並交給既有 P2 execution。
- P3-F把Offline Correctness、Live Model Quality與Provider Transport分開判定。只有synthetic eval可對
  `TIMEOUT`／`TRANSIENT`／`RATE_LIMIT`最多retry兩次；production model transport、proposal與交易路徑沒有因此
  取得automatic retry或fallback authority。Transport狀態必須在P6前以rolling evidence重驗。
- runtime PostgreSQL role 不能直接修改 P3 tables，也不能執行 CAS publication；發布需由實際
  SHA-256 content verifier 與受信任 operator capability 完成。
- 不從 `.env` 讀取真實秘密；production secret 只經固定 `SecretRef` 與 macOS Keychain boundary。
- Tavily 多帳號仍固定 fail closed，直到存在可獨立驗證的外部授權證據。

## 文件入口

先讀：

1. [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)：目前唯一交接、驗收邊界與下一步。
2. [PROGRESS.md](PROGRESS.md)：phase/gate 狀態與最新證據。
3. [docs/ROADMAP_AND_ACCEPTANCE.md](docs/ROADMAP_AND_ACCEPTANCE.md)：剩餘階段與完成條件。

目前active執行文件已按Gate拆分；每份只授權一個明確任務：

- [P3D_IMPLEMENTATION_PROMPT.md](P3D_IMPLEMENTATION_PROMPT.md)／
  [P3D_ACCEPTANCE_PROMPT.md](P3D_ACCEPTANCE_PROMPT.md)
- [P3E_IMPLEMENTATION_PROMPT.md](P3E_IMPLEMENTATION_PROMPT.md)／
  [P3E_ACCEPTANCE_PROMPT.md](P3E_ACCEPTANCE_PROMPT.md)
- [P3F_IMPLEMENTATION_PROMPT.md](P3F_IMPLEMENTATION_PROMPT.md)／
  [P3F_ACCEPTANCE_PROMPT.md](P3F_ACCEPTANCE_PROMPT.md)

設計與治理：

- [docs/MASTER_PLAN.md](docs/MASTER_PLAN.md)：產品與投資流程基線。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：信任、模組、狀態與資料邊界。
- [SECURITY.md](SECURITY.md)：安全政策與 PostgreSQL／Keychain authority。
- [docs/OPERATIONS_AND_SAFETY.md](docs/OPERATIONS_AND_SAFETY.md)：營運、故障與人工控制。
- [docs/SOURCES.md](docs/SOURCES.md)：來源、授權與保存規則。
- [DECISIONS.md](DECISIONS.md)、[ISSUES.md](ISSUES.md)、[RISK_REGISTER.md](RISK_REGISTER.md)：
  決策、未結問題與風險。
- [WORKLOG.md](WORKLOG.md)：濃縮里程碑；逐輪細節保留於 Git history。

Future／deferred：

- [docs/TRADINGAGENTS_ASSESSMENT.md](docs/TRADINGAGENTS_ASSESSMENT.md)
- [docs/DISTILLATION_SPEC.md](docs/DISTILLATION_SPEC.md)
- [docs/MEMORY_CURATION_SKILL_SPEC.md](docs/MEMORY_CURATION_SKILL_SPEC.md)
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

已完成階段的implementation／acceptance／remediation prompts已移除；上列P3-D／E／F prompts是目前
active文件。prompt只定義scope與操作，不是完成或Gate Accepted證據。

## 開發與驗證

唯一 bootstrap prerequisite 是 [`uv`](https://docs.astral.sh/uv/)。專案使用 Python 3.13，測試不讀
真實 Keychain、不呼叫真實 broker/model/source API。

```bash
./scripts/verify_p1.sh
./scripts/run_postgres_integration.sh
git diff --check
```

第一個命令執行 locked sync、lock、format、lint、mypy 與 non-integration tests。第二個命令使用
digest-pinned PostgreSQL 16、fake credentials、random localhost port 與 tmpfs，執行 zero-skip
integration tests並清理 disposable container。手動 `TEST_DATABASE_URL` 只能指向專用 disposable
database。

需要格式化時：

```bash
uv run ruff format .
```

## 下一步

P3-B+C Combined Gate已Closed並發布；P3-D、P3-E維持Accepted。P3-F已完成offline與authorized
real-provider live證據：V12 batch在eval層注入const-pinned `response_format`後**完整執行**——
260/260 strict且全正確、violations=0、130/130 pre-network fail-closed、transport雙門檻100%，
Live Model Quality與Provider Transport同批雙綠。狀態為**implementation completed; pending
independent acceptance**；下一步由未參與實作的fresh session以`P3F_ACCEPTANCE_PROMPT.md`驗收。
Transport GREEN僅為本批snapshot，P6前需另行授權rolling canary重驗。不得把P3規劃或後續關門
解讀為Paper order readiness。
