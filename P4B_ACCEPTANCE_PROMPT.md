# P4-B Independent Acceptance Prompt

完整交給未參與實作的新模型。只驗收P4-B；read-only、不修code、不開始P4-C。

## 0A. 獨立審查協議與判決規則

先讀source/diff，後跑測試；不得只引用implementation report。每個requirement依序取得：source enforcement、public
entry PoC、permanent test、PG16 authority evidence。找不到即`PENDING`，不是PASS。

| Finding | 判定 |
|---|---|
| High：錯誤security被視為eligible、split block可繞過、跨Gate/broker capability | Rejected |
| Medium：time-travel leakage、conflict被投票解決、transition/ACL/concurrency fail-open、非deterministic | Rejected |
| Low：不影響authority的局部維護性／訊息問題 | 可Accepted並列出 |
| 缺P4-A fresh Accepted、PG16或必要source lineage證據 | Not Accepted — prerequisite/evidence pending |

不得conditional pass。驗收全程不得修改、format、更新snapshot、套修復migration、連外、讀secret或呼叫model/broker。

## 0. 判定

只允許`Accepted`、`Rejected`、`Not Accepted — prerequisite/evidence pending`。Accepted要求P4-A fresh Accepted，
且security master／quarantine的source、永久測試、自建對抗PoC、真實PG16/ACL/併發證據完整。不能conditional pass。

## 1. Revision與限制

保存pwd、git status、HEAD/origin、log、diff stat/name/check、untracked、migration checksums；revision寫exact HEAD +
dirty set。不修改任何檔、不讀credential、不連外、不呼叫model/broker。發現High/Medium只做最小重現。

完整閱讀P4治理、A/B prompts、P4-A source contracts、全部B source/tests/migrations與P2 order/control/reconciliation
邊界。確認B沒有提前實作C～F。

## 2. Identity contract對抗

從合法identity逐欄tamper：security ID、symbol、exchange、class、CIK/CUSIP/ISIN、valid/available interval、source refs、
status/version/hash。測bool/subclass、unknown fields、negative/future/overlap、post-construction mutation。

自建time-travel案例：

- 同security換symbol；同symbol日後被另一security重用；
- decision cutoff前後±1µs；
- correction晚到但effective date較早；
- current ticker/CIK不能污染historical lookup；
- ambiguous/multiple identity必須REVIEW_REQUIRED而非任選。

在實際repository/service入口assert零錯誤authority，不只測constructor。

## 3. Corporate-action confirmation PoC

至少建立：forward split、reverse split、Alpaca-only、official-only、Alpaca+official、ratio conflict、date conflict、
identity conflict、withdrawn official、late discovery、unsupported action、effective已過、duplicate/reordered sources。

證明：

- discovery立即durable ENTRY_BLOCKED；Alpaca-only永不CONFIRMED；
- 至少一個正式SEC/IR/exchange且所有已讀來源無衝突才CONFIRMED；
- 查無事件不能解除block；source數量不能投票掩蓋衝突；
- confirmed後撤回/修正進REVIEW_REQUIRED；P4-B永不標EXITED；
- ratio使用exact representation，無float drift或forward/reverse倒置。

## 4. Quarantine三層一致性

呼叫實際candidate/Risk/future-submit query seam（若後兩者僅port，使用其public contract），對相同version/as-of
必須byte-identical。測unknown security、stale master、symbol mismatch、future source、multiple active identity、
event pending/confirmed/review。輸出需reason/event/source/version lineage，不能只bool。

證明沒有任何caller可透過不同參數、cache或fallback繞過block。

## 5. 真實PostgreSQL／failure injection

以PG16兩連線驗證：interval/unique/check/FK、illegal transition、same-hash idempotency、different-hash correction、
confirm-vs-withdraw、two confirms、rollback/crash、audit failure。檢查安全state先durable；第二actor在第一actor的
非必要persist failure後仍看見block。

以runtime role實際嘗試UPDATE/DELETE、直接解除block、偽造current projection、執行owner-only function；確認拒絕。
跑migration up/down/up與legacy-invalid preflight。SQLite/fake不算authority證據。

## 5A. 強制審查流程

1. **Scope diff**：列出每個changed/untracked file；確認沒有A wire漂移或C～F placeholder/capability。
2. **Data contract**：逐欄追constructor→canonical bytes/hash→repository serializer→DB constraints→reader revalidation。
3. **Time-travel**：以同一dataset查`valid_at`與`known_at`四種組合，確認late correction不回填歷史知識。
4. **Transition review**：從實際transition table列出所有state×event；未列組合必須typed拒絕而非default。
5. **Quarantine bypass search**：搜尋所有eligibility callers、cache、SQL projection、default/exception paths；從三個public seam
   執行相同PoC並比較canonical bytes。
6. **PG authority**：使用owner做setup、runtime做操作；以兩連線重現race與safe-state visibility，檢查catalog grants。
7. **Regression**：最後跑focused、full PG/non-PG、lint/type/diff；逐一解釋skip/deselect與基線差異。

## 5B. Mandatory evidence matrix

```text
Requirement | Source file:line | PoC | Permanent test | PG evidence | Verdict
Stable identity; symbol is not identity
valid_at vs available_at/known_at separation
No interval overlap/current-data leakage
Append-only correction/supersession
Discovery immediately blocks
Formal-source confirmation and all-source consistency
Conflict/withdrawal/late event -> REVIEW_REQUIRED
Three-seam quarantine equivalence
Safe state survives failure and is cross-connection visible
Runtime cannot bypass transition/unblock
No universe/Risk/Intent/broker/model capability
```

每列都需expected/observed。僅測class constructor、讀SQL文字或引用test name不得標PASS。

## 6. Scope與完整回歸

Imports/composition/source invariants證明無universe、ranking、RiskDecision、TargetPortfolio、quantity、IntentPlan、
broker submit/cancel、short cover。P4-A wire/hash未被不相容修改。

執行focused、source/event/P2 safety nearby tests、完整non-integration、完整PG16 zero-skip、Ruff、format、mypy、
`git diff --check`。任何skip漂移、migration checksum或P1～P3 regression都阻擋Accepted。

## 7. 報告

最低read-only命令集：

```bash
uv run --locked pytest tests/test_p4b_*.py tests/test_paper_only_source_invariants.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

另需自建time-travel、transition與two-connection PoCs；任何命令缺失或unexpected skip列為evidence gap。

```text
P4-B VERDICT: Accepted | Rejected | Not Accepted — prerequisite/evidence pending
REVISION: <exact revision>
```

列findings（severity、file:line、PoC、expected/observed、authority impact、required fix）、identity/time travel、
confirmation、quarantine、PG/ACL/concurrency、scope與full regression。Accepted後單一步驟是另開P4-C實作；不得修改
狀態或開始下一Gate。

Finding格式固定：

```text
ID / Severity / Requirement:
File:line:
Minimal public-entry PoC:
Expected:
Observed:
Persisted rows/call counts:
Authority impact:
Required remediation boundary（不實作）:
```

報告另列`FILES REVIEWED/NOT REVIEWED`、完整matrix、PG server/version與兩連線步驟、原始命令/exit code/test counts、
external/model/broker call counts，以及所有`UNVERIFIED CLAIMS`。只有matrix全PASS且無High/Medium才能Accepted。
