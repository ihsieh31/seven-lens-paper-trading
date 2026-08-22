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
| P3-B+C evidence／research pipeline | Closed | P3-B與P3-C均經獨立驗收Accepted；工作包仍未提交 |
| P3-D～F、P4～P8 | Not started | 不得提前宣告能力或繞過後續 gate |

目前工作樹仍是未提交的 P3-B+C 工作包；`main`／`origin/main` 基線為
`def706440c7dda1a61610a9ea42b42005dfe115a`。最新本機證據：Ruff／format／mypy／lock 全綠，
non-integration `809 passed, 102 deselected`，真實 PostgreSQL 16
`94 passed, 8 deselected, 0 skipped`。

## 核心邊界

- 只允許 Alpaca Paper；未知、缺失、過期、矛盾或未授權狀態一律 fail closed。
- analysis workers 沒有 broker credential、order/ledger write、shell 或任意 network capability。
- P3-B+C 只到 `TraderPlan`；Risk Debate／Portfolio Manager 留 P3-D，真實 provider 留 P3-E，
  reflection／memory／evals 留 P3-F。
- P4 deterministic Risk 才能核准 target、計算 quantity 並交給既有 P2 execution。
- runtime PostgreSQL role 不能直接修改 P3 tables，也不能執行 CAS publication；發布需由實際
  SHA-256 content verifier 與受信任 operator capability 完成。
- 不從 `.env` 讀取真實秘密；production secret 只經固定 `SecretRef` 與 macOS Keychain boundary。
- Tavily 多帳號仍固定 fail closed，直到存在可獨立驗證的外部授權證據。

## 文件入口

先讀：

1. [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)：目前唯一交接、驗收邊界與下一步。
2. [PROGRESS.md](PROGRESS.md)：phase/gate 狀態與最新證據。
3. [docs/ROADMAP_AND_ACCEPTANCE.md](docs/ROADMAP_AND_ACCEPTANCE.md)：剩餘階段與完成條件。

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

已完成階段的 implementation／acceptance／remediation prompts 已移除；不得把歷史 prompt 當作
目前授權或 gate 狀態。

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

P3-B+C Combined Gate已Closed。下一步由使用者決定是否提交／推送目前工作包；P3-D仍為
Not started，必須另行授權後才能開始，也不得把本次關門解讀為Paper order readiness。
