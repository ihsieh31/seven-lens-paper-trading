# Seven-Lens Paper Trading System

個人使用、Paper-only 的美股研究與交易系統。`Seven-Lens` 名稱是歷史相容識別；目前主線採
TradingAgents-style 多角色研究，七人蒸餾已降為停用的 Future Analyst Plugin。

LLM 只能產生有來源、符合 schema 的研究／組合提案；deterministic Risk 才能核准，P2
execution 只能處理已核准的 Paper `OrderIntent`。本專案沒有 Alpaca live endpoint、live adapter
或自動升級實盤的 gate。

## 狀態總覽

| 階段 | 狀態 | 說明 |
|---|---|---|
| P0 規格／治理 | Closed | 核心範圍、Paper-only 與風控邊界 |
| P1 基礎／權威狀態 | Closed | typed config、Keychain、PostgreSQL、telemetry、CI |
| P2 Paper 執行安全 | Closed | order/fill/reconciliation/control；真實下單仍未授權 |
| **P3 研究／提案／記憶** | **Closed** | A～F 與 cleanup Batch A～G 均已驗收；詳見下節與 `WORKLOG.md` |
| P4 | In progress | P4-A／P4-B已獨立驗收並Accepted／Closed；P4-C～F未開始 |
| P5～P8 | Not started | 驗證 → Shadow → Supervised → Unattended |

## P3 Close

P3 於 2026-08-26 完成最後一個子閘（reflection/memory/evals）的獨立重新驗收後一次性關閉。
範圍涵蓋 upstream contracts、point-in-time evidence/event、研究管線、Risk Debate 與提案、
provider isolation、immutable reflection lineage、bounded curated memory 與 synthetic eval 治理。

關門證據摘要：

- 全套 regression：targeted `423 passed`；`verify_p1.sh` non-integration `1299 passed,
  232 deselected`；真實 PostgreSQL 16 整合 `217 passed, 15 deselected, 0 skipped`。
- Offline eval byte-match：V12 frozen report 重算一致（split/report hash 不變）。
- Authorized live evidence（V12）：260/260 strict 且全正確、violations=0、130/130 pre-network
  fail-closed；Provider Transport first-attempt/eventual 皆 100%（僅為該批 snapshot）。
- 發布：工作樹以 `b59e466` 登載、CI tmpfs 修復 `d51e9a9`；exact-SHA run `32962320231`
  的 `quality-unit` 與 `postgres-integration` 兩 required jobs 成功。

2026-08-27 的 P1–P3 full remediation independent acceptance 另外確認：完整 non-integration
`1386 passed, 245 deselected`、targeted P1–P3 `857 passed`、PostgreSQL 16 integration
`243 passed, 2 deselected, 0 skipped`。runtime role 對 `control_state` 的 direct UPDATE 以 SQLSTATE
`42501` 拒絕，未達 FULL+CLEAN 的 direct resume 以 `55000` 拒絕；P3-4／P3-28 仍為明確 deferred，
P3-21 維持 FALSE POSITIVE。

## 核心邊界

- 只允許 Alpaca Paper；未知、缺失、過期、矛盾或未授權狀態一律 fail closed。
- analysis workers 沒有 broker credential、order/ledger write、shell 或任意 network capability。
- 只有 P4 deterministic Risk 能核准 target、計算 quantity 並交給既有 P2 execution。
- Synthetic eval 可對 `TIMEOUT`／`TRANSIENT`／`RATE_LIMIT` 最多 retry 兩次；production model
  transport、proposal 與交易路徑沒有 automatic retry 或 fallback authority。
- Runtime PostgreSQL role 不能直接修改 P3 tables 或執行 CAS publication；secret 只經固定
  `SecretRef` 與 macOS Keychain boundary，不從 `.env` 讀取真實秘密。
- Tavily 多帳號固定 fail closed，直到存在可獨立驗證的外部授權證據。
- 已核准且正分Gate實作的多來源規劃採封閉角色：Alpaca行情authority；FRED/ALFRED＋官方宏觀；SEC/IR；
  Alpaca Corporate Actions＋SEC/issuer/exchange確認；Tavily/GDELT discovery；yfinance只作研究補充。
- P4-B已實作且獨立驗收confirmed forward/reverse split的point-in-time identity、source lineage與三層entry quarantine；
  既有long未來仍走獨立deterministic `CORPORATE_ACTION_EXIT`，並在fills＋FULL reconciliation後記錄拆／合股原因、
  收益與衍生記憶。B Gate已Closed，仍沒有授權送單。

## 存續義務與下一步

- Provider Transport GREEN 僅為 V12 批次 snapshot。P6 Shadow 開始前，需另行授權的 synthetic
  canary 在 rolling 7 日且 ≥200 logical calls 達 first-attempt≥95%／eventual≤3 attempts≥99%；
  跌破即重開（OPEN-027）。
- 下一個階段仍是 **P4 multi-source／candidate／deterministic Risk**。`P4_PROGRAM_PLAN.md`與ADR-038已完成
  使用者設定確認；ADR-039亦已固定Factor V1、SEC SIC Division、correlation cluster與gross turnover。P4-A與P4-B
  均已fresh independent acceptance並Accepted／Closed；P4-C～F未開始。P4-A focused `372 passed`、P4-B focused
  `132 passed`，fresh PG16 integration `256 passed, 2 deselected, 0 skipped`；完整P4仍未Closed。
  P4只可建立no-submit intent；任何真實Paper送單能力屬P7且需再次明確授權。

## 文件入口

先讀：

1. [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)：目前唯一交接、邊界與下一步。
2. [PROGRESS.md](PROGRESS.md)：phase/gate 狀態與最新證據。
3. [docs/ROADMAP_AND_ACCEPTANCE.md](docs/ROADMAP_AND_ACCEPTANCE.md)：剩餘階段與完成條件。
4. [P4_PROGRAM_PLAN.md](P4_PROGRAM_PLAN.md)：已核准P4設定、模組邊界、工作包與Final Gate。

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

已完成階段的 implementation／acceptance prompt、P3 requirement map 與關門當下的 handoff 快照，
統一存放於 [`docs/archive/`](docs/archive/)（`prompts/`、`handoffs/`、`p3/`）。歸檔文件只作
歷史紀錄，不是現行授權或完成證據。

## 開發與驗證

唯一 bootstrap prerequisite 是 [`uv`](https://docs.astral.sh/uv/)。專案使用 Python 3.13，測試不讀
真實 Keychain、不呼叫真實 broker/model/source API。

```bash
./scripts/verify_p1.sh
./scripts/run_postgres_integration.sh
git diff --check
```

第一個命令執行 locked sync、lock、format、lint、mypy 與 non-integration tests。第二個命令使用
digest-pinned PostgreSQL 16、fake credentials、random localhost port 與 disk-backed anonymous volume，執行
zero-skip integration tests並清理 disposable container。手動 `TEST_DATABASE_URL` 只能指向專用 disposable
database。本機 Docker VM 記憶體有限時，仍請勿與其他高記憶體容器並行跑整合套件。

需要格式化時：`uv run ruff format .`。
