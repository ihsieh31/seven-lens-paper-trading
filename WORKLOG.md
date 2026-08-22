# Work Log

本檔只保留可影響目前決策的里程碑。逐輪缺陷、命令輸出與已被取代的敘述保留於 Git history；
目前狀態以 `PROJECT_HANDOFF.md` 與 `PROGRESS.md` 為準。

## 2026-08-14 — P0／P1-A／P1-B

- 建立 Paper-only 規格、來源／資料／風控邊界與文件治理。
- 建立 Python 3.13／uv／strict config／canonical values／redaction-first logging。
- 建立 PostgreSQL migrations、append-only domain/audit、job lease與market clock。
- 獨立驗收修復credentials、JSON resource bounds、cycle/depth、UTC/schema strictness與
  PostgreSQL authority缺陷。

## 2026-08-15 — P1-C 與 P1 Gate Closed

- P1-C1：macOS Keychain exact read-only boundary、sealed `SecretRef`、hard timeout、零fallback。
- P1-C2：dependency-neutral metrics/traces、explicit context與audit identity一致性。
- P1-C3：locked Ubuntu quality＋PostgreSQL jobs、zero-skip、clean-machine bootstrap。
- 建立public repository；exact-SHA CI run `31868962828`成功，P1 Core Gate Closed。
- 後續authority hardening加入runtime/owner role分離、typed JSON registry與
  `SECURITY DEFINER`防shadowing。

## 2026-08-16 — Future Analyst Plugin

- 七位方法論與本機語料改為停用的Future Analyst Plugin；corpus不進repository、不讀取、
  不阻塞主線。
- 相關規劃commit `1d4d9bd31d993a5fb6803a8d08ff5deec04122e1`與CI
  `31950919861`成功。

## 2026-08-17～20 — P2 Paper執行安全

- 實作Paper adapter、order/fill state、reconciliation、control plane、NAV/ledger、runtime
  composition與GET-only acceptance。
- 多輪審查修復pause TOCTOU、duplicate submit、pagination、UNKNOWN terminal、flatten、asset
  gate、late/conflicting fills、baseline/NAV、migration compatibility與runtime authority。
- final remediation ACC-001～009全部關閉；commit `488f170`的exact-SHA CI
  `32360443947`兩個required jobs成功，P2 Gate Closed。
- 關閉不授權真實送單；WebSocket transport與control CLI仍留P6/P7。

## 2026-08-21 — P3架構與P3-A

- 採TradingAgents完整研究／risk debate／Portfolio Manager提案鏈；deterministic Risk保留唯一
  核准權，P2 execution不變。
- P3-A固定upstream `a33fd4c0f134485a43553a2c23a63cb14adbd88f`，完成Apache-2.0
  inventory與strict immutable contracts。
- remediation-R1獨立重新驗證Accepted；commit `9037dacc589690101ea60901a3f34991480a70e1`
  與exact-SHA CI `32488368972`成功，P3-A Gate Closed。
- 決定P3-B與P3-C合併實作但維持獨立子Gate；真實provider、Risk/Portfolio Manager與memory
  分別留P3-E、P3-D、P3-F。

## 2026-08-21 — P3-B+C implementation

- P3-B完成point-in-time evidence、SHA-256 CAS、GET-only source boundary、event verifier與
  PostgreSQL metadata authority。
- P3-C完成四分析員、兩輪Bull/Bear、Research Manager、Trader、scripted provider與
  InMemory/PostgreSQL stage persistence。
- migration 0010完成up/down/up；初版down漏刪version row已在發布前修復。
- 工作包未stage／commit／push，等待獨立驗收。

## 2026-08-22 — P3-C Remediation R1

- 首輪獨立驗收：P3-B Accepted；P3-C因persisted identity與DB transition authority兩項High
  被拒。
- 修復fresh/resume identity對等驗證、相鄰transition whitelist、terminal sinks、bounded retry、
  provider hash／producer strictness與fragment availability。
- 當時證據：targeted `24 passed`、non-integration `783 passed, 96 deselected`、PG16
  `88 passed, 8 deselected, 0 skipped`。

## 2026-08-22 — P3-B+C Remediation R2

- 二次審查重現並修復event亂序、official source冒充、VERIFIED packet矛盾／缺證據／stale、
  URL port、ghost CAS publication、runtime role verifier漏P3、packet/snapshot未綁定、
  InMemory run identity collision與provider跨deadline。
- CAS publication改為實際hash verifier；runtime撤銷publish權；P3 tables/functions納入
  owner與least-privilege proof。
- DB create-run核對packet snapshot；pipeline在provider返回後與每次authority advance前重查
  deadline；加入不同hash真實DB concurrency regression。
- 最終證據：targeted `30 passed`；lock/Ruff/format/mypy全綠；non-integration
  `789 passed, 100 deselected`；PG16 `92 passed, 8 deselected, 0 skipped`；
  `git diff --check`通過。
- P3-B首輪Accepted因新發現重新開啟。狀態為`P3-B+C remediation R2 completed; pending
  independent acceptance`。
- 未使用真實API／credential、未讀`skill/`、未stage／commit／push。

## 2026-08-22 — 文件收斂

- README、handoff、progress、worklog與相關治理文件改為current-state-first；重複歷史濃縮成
  可稽核里程碑。
- 移除已完成P1/P3-B+C implementation／acceptance／remediation prompts及其引用；後續獨立
  驗收直接以`PROJECT_HANDOFF.md`、source、tests與真實PostgreSQL為準。
- 未改變任何Gate判定：P3-B、P3-C仍待獨立重新驗收。

## 2026-08-22 — P3-B+C Remediation R3

- 新一輪獨立驗收判定5 High＋1 Medium：packet hash不完整、pipeline未重驗tampered packet、
  caller可偽造CAS verifier、ACL proof漏TRUNCATE/REFERENCES/TRIGGER、persisted debate漏evidence
  closure、deadline晚於首次create-run。
- packet hash改為承諾全部nested evidence欄位；pipeline入口重跑nested contracts與packet integrity。
- publication repository綁定exact `FileContentStore`並實際核對hash/byte size；runtime ACL逐項證明
  只有SELECT，persisted debate與initial deadline均改為零旁路／零權威副作用。
- 新增逐欄mutation、post-construction tamper、foreign debate evidence、expired initial run、
  forged verifier與完整ACL drift regressions。
- 驗證：targeted `35 passed`；lock/Ruff/format/mypy全綠；non-integration
  `794 passed, 100 deselected`；PG16 `92 passed, 8 deselected, 0 skipped`；
  `git diff --check`通過。
- 狀態為`P3-B+C remediation R3 completed; pending independent acceptance`；未stage／commit／push。

## 2026-08-22 — P3-B+C Remediation R4

- 獨立驗收判定P3-B、P3-C Rejected並回傳3 High：future retrieved/published source可進入
  VERIFIED packet、analyst counterevidence漏做packet closure、pipeline未重驗tampered
  `AnalysisInput`與nested snapshot。
- source eligibility新增retrieved/published as-of gate；fresh與persisted analyst共用完整evidence＋
  counterevidence closure；pipeline在任何run authority前重跑input/snapshot/nested invariants。
- 新增future timestamp、fresh/resume foreign counterevidence、top-level/nested input tamper永久regression。
- 驗證：targeted `41 passed`；lock/Ruff/format/mypy全綠；non-integration
  `800 passed, 100 deselected`；PG16 `92 passed, 8 deselected, 0 skipped`；
  `git diff --check`通過。
- 狀態為`P3-B+C remediation R4 completed; pending independent acceptance`；未stage／commit／push。

## 2026-08-22 — P3-C Remediation R5

- 最新獨立重新驗收判定P3-B Accepted、P3-C Rejected：`AnalysisInput`未綁定snapshot `as_of`，
  pipeline也未綁定input／packet `data_snapshot_refs`，兩者都可產生VALID `TraderPlan`。
- 合約新增input/snapshot exact-as-of；pipeline新增ordered exact data-snapshot identity binding，全部在
  `create_run()`前執行。
- 新增stale/future snapshot合約與pipeline測試，以及foreign/missing/reordered refs零authority測試。
- 驗證：targeted `46 passed`；lock/Ruff/format/mypy全綠；non-integration
  `807 passed, 100 deselected`；PG16 `92 passed, 8 deselected, 0 skipped`；
  `git diff --check`通過。
- 狀態：P3-B Accepted；P3-C remediation R5 completed, pending independent acceptance；Combined
  Gate Open。未stage／commit／push。

## 2026-08-22 — P3-C Remediation R6

- 最新獨立驗收維持P3-B Accepted、P3-C Rejected：InMemory只以run id查identity collision，允許
  同一input建立多個PLANNED authority run，與PostgreSQL `UNIQUE(input_id)`語意不一致。
- InMemory新增`input_id → run_id`唯一索引；完全相同run identity仍冪等，不同run一律拒絕且不
  留下authority。
- InMemory與真實PostgreSQL都新增same/different packet-snapshot duplicate-input對照regression。
- 驗證：targeted `48 passed`；lock/Ruff/format/mypy全綠；non-integration
  `809 passed, 102 deselected`；PG16 `94 passed, 8 deselected, 0 skipped`；
  `git diff --check`通過。
- 狀態：P3-B Accepted；P3-C remediation R6 completed, pending independent acceptance；Combined
  Gate Open。未stage／commit／push。

## 2026-08-22 — P3-C R6獨立驗收Accepted

- source review確認InMemory的`input_id → run_id`反向唯一索引在任何authority寫入前拒絕相同
  input的第二個run；PostgreSQL `UNIQUE(input_id)`維持相同語意。
- 獨立PoC證明相同／不同packet-snapshot的第二個run均被拒絕且不留下authority；相同run＋
  完整相同identity仍冪等。
- 驗收重跑：targeted `48 passed`；lock／format／Ruff／mypy全綠；non-integration
  `809 passed, 102 deselected`；真實PG16 `94 passed, 8 deselected, 0 skipped`；
  `git diff --check`通過。
- P3-C Accepted；P3-B+C Combined Gate Closed。未stage／commit／push；P3-D仍未開始且需另行授權。
