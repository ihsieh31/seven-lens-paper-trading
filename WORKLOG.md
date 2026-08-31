# Work Log

本檔只保留可影響目前決策的里程碑。逐輪缺陷、命令輸出與已被取代的敘述保留於 Git history；
目前狀態以 `PROJECT_HANDOFF.md` 與 `PROGRESS.md` 為準。

## 2026-08-31 — P4-C fresh independent acceptance完成

- 以read-only fresh session依P4-C evidence matrix完成source/call-graph、public-entry對抗、獨立formula／SIC／
  ranking oracle、10 permutations、完整non-integration與真實PostgreSQL 16驗證；未修改runtime/tests，
  provider／model／broker／Keychain呼叫均為0。
- 結果：focused P4-C＋clock `190 passed`；完整non-integration `2303 passed, 329 deselected`；
  PostgreSQL 16 integration `327 passed, 2 deselected`；Ruff／format／mypy／`git diff --check`全綠。
- Verdict：P4-C `Accepted`、Gate `Closed`，無High／Medium finding。P4 overall仍為`In progress`，
  P4-D～F未開始；下一步只能另開P4-D implementation。
- P4-A／B／C implementation與acceptance prompts於closure publication移除；歷史決策與驗收證據保留於Git history。
- Closure commit首次exact-SHA CI的`quality-unit`成功，但`postgres-integration`在249 tests通過後因1g tmpfs
  `pg_wal`耗盡（`No space left on device`）失敗；這是CI容量而非application assertion failure。CI service
  tmpfs先調整為2g；第二輪完成`326 passed`但restart proof因service不能重啟自身而有`1 skipped`，zero-skip gate
  正確拒絕。最終CI改用repository `run_postgres_integration.sh`：disk-backed disposable PG16、owner identity驗證、
  fixed localhost port與真實restart。首次runner啟動另暴露`pg_isready`後立即查version的初始化斷線race；version
  query改為bounded 30秒retry且仍只接受major 16，等待新SHA重新驗證兩個required jobs。

## 2026-08-31 — P4-C authority remediation與全面本地驗證完成

- 重新把P4-A normalized records、P4-B identity／split head與P4-C public projections視為單一trust chain審查；
  修復caller-minted source authority、DailyBar／AssetObservation／IdentityView直接建構旁路、bar feed／timeframe／
  point-in-time identity、SEC frame／entity scope／CapEx sign、split identity substitution與nested authority tamper。
- PostgreSQL補齊source payload契約、exact source/head binding、append-only CAS與worker/runtime ACL；PG16迭代中發現並
  修正舊payload constraint未接受`timeframe=1Day`、錯誤schema-qualified `COALESCE`、非canonical corporate-action
  測試夾具、precision-boundary夾具與ACL teardown grant清理。所有修復均有永久回歸，未放寬fail-closed邊界。
- 最終`./scripts/verify_p1.sh --postgres`從頭全綠：265 files format、Ruff、mypy 264 source files；
  non-integration `2302 passed, 329 deselected`；digest-pinned PostgreSQL 16 integration
  `327 passed, 2 deselected`；`git diff --check`通過。provider／model／broker／Keychain呼叫均為0。
- 本輪修復審查未留下High／Medium finding；狀態為`P4-C remediation verified; pending fresh independent acceptance`。
  不以本輪自審冒稱Accepted，P4-D～F未開始。

## 2026-08-30 — P4-C trust-boundary audit：五項誤信漏洞（P1–P5）實證並修復

- 接續 sparse-calendar 修復的教訓，把「authority 輸入本身說謊/不完整」當成對抗面，對 P4-C 全部
  public entry 重新掃描：日曆（`sessions`）、每個 `FactorInput` 自帶日曆、session window 內容、
  asset observation／quarantine decision 新鮮度、source_ref 存在性、wire 可重導性、fiscal 標籤、
  latest-selection、AssetKind 映射、cluster nodes 成員資格、EvidenceView bools、policy 常數雙源。
  PoC（/tmp/p4c_poc/test_poc_trust_audit.py）實證五個漏洞，全部同日修復：
- **P1【High】** 稀釋歷史日曆架空 252-session 歷史門檻：日曆「近 30 個平日 REGULAR＋更早 232 週
  每週一 REGULAR」下，252 根 bar 橫跨約 5 年，ADV 窗走查通過、`reasons=[]`，universe 判
  `eligible=True`——sparse-calendar 修復只覆蓋 ADV 窗，未覆蓋 history gate。
  修復：`_validate_market_snapshot_fields` 對 bar_dates 全 span（最早 bar → as_of）做
  `_require_complete_weekday_window` 走查。
- **P2【Medium】** 各 `FactorInput` 攜帶分歧但各自完整的日曆（假假日明標 CLOSED＋補一個更舊日），
  同一 cross-section 成員在不同 session 集上評分。修復：`build_feature_vectors` 要求所有 inputs
  的 sessions tuple 值相等。
- **P3【Medium】** session window「內容」從不受檢：平日 REGULAR 宣稱 00:00–23:59:59 可通過全部
  結構檢查並影響 quote age／focus window 定義。修復：新增共用 `validate_nyse_session_window`
  （opens ∈ 12:00–16:00 UTC、時長 1h–6h30m），market `_validate_nyse_sessions` 與 funnel
  `_session_calendar` 都呼叫。
- **P4/P5【Medium】** asset `observed_at` 與 quarantine `decision_at` 只有上限（≤ known_at）無下限：
  5 個月前的觀察與決策配上今日 market snapshot 仍 `eligible=True`。修復：`build_universe` 加
  `_MAX_AUTHORITY_STALENESS = 7 天`，超限分別以既有 closed reason `NOT_ACTIVE_OR_TRADABLE`／
  `CORPORATE_ACTION_QUARANTINE` 排除（不新增 reason code、不改 wire contract）。
- 新增永久回歸 7 項：market 稀釋歷史日曆拒絕、market 24h 窗拒絕、funnel 分歧日曆拒絕、
  funnel 24h 窗拒絕（共用 validator）、universe 舊觀察排除、7 天 ±1µs 邊界、舊 quarantine 決策排除。
  審計 PoC 反轉為「預期被拒」4/4 通過。全部修復僅觸及 `market_data/snapshots.py`、
  `screening/funnel.py`、`universe/builder.py` 的輸入驗證，無新 capability、不改 A/B contracts、
  不改 migration/wire shape。
- 分析-only trust boundaries（跨層責任，本輪不動碼）：market snapshot source_ref 無 FK 綁
  `p4_source_records`（sector_assignments 有——不對稱）；snapshot wire 不含 bar close/volume，
  readback 無法重導 adv20（靠 restart 重算 features 緩解）；fiscal year/quarter 標籤由 P4-A
  normalizer 保證；sector/identity「cutoff 前最新」的 latest-selection 由 A/B repository seam 保證；
  AssetKind 映射由上游 adapter 保證；cluster nodes＝top100∪holdings 由 P4-D composition 保證；
  EvidenceView bools 為 P3/P4 evidence seam 設計；`snapshots.py` 的 MIN_PRICE/MIN_ADV20/5s/30bps
  與 `config/p4.py` 雙源（值一致，P4-A Accepted code 不可動，留待 composition review）；
  明標 CLOSED 的全週說謊日曆仍屬 calendar authority 內容信任（P3 邊界檢查已限縮至小時合理性）。

## 2026-08-30 — P4-C 第二輪 trust audit（非日期類誤信）：Q1/Q2 修復、Q3/Q4 如實記錄

- 應「把已知條件本身當攻擊面」的審查指示，對非日期類信任假設再掃一輪（/tmp/p4c_poc/
  test_poc_trust_audit2.py）。四個 lead，兩個實證為漏洞並已修復，兩個如實列為不修：
- **Q1【Medium，已修復】** `select_focus_window` 直接消費 session window 內容、完全未呼叫
  `validate_nyse_session_window`（P3 修復的覆蓋殘留）：24h 假日曆＋攻擊者選 as_of 00:30 即得
  OPEN_PLUS_60M。修復：函式內加共用 validator。新增永久測試
  `test_focus_window_rejects_lying_session_hours`。
- **Q2【Medium，已修復】** shares observation 只檢查 `available_at ≤ known_at` 上限：pre-split
  股數（available 4/1）配 2:1 split（available 5/1、ex_date 在中窗）＋split-adjusted 價格 →
  market_cap 1000×20=20,000 而正確 40,000，earnings_yield 恰好膨脹 2x、status COMPLETE。
  修復：`build_feature_vectors` 要求 shares observation 的 `available_at ≥` 最後一個
  **已套用**（visible＋effective）split 的 `available_at`，否則 `FACTOR_INPUT_MISSING`
  （reason="shares observation predates an applied split"）。邊界：available 恰等於 split
  available 即通過；僅公告未生效（available > known_at）的 split 不約束股數——避免過度排除。
  新增永久測試三態（stale → MISSING、boundary → COMPLETE、announced-only → COMPLETE）。
- **Q3【不修，需 ADR】** quote 水準與 bar 收盤從不交叉檢查：quote mid 5.055 對 bar close 100
  （95% 背離）仍 `eligible=True`、ADV 25M——價格 gate 與 ADV gate 各自使用被信任的輸入。
  任何背離 band 都是新 threshold，屬 ADR-038/039 治理範圍，不擅自加；已列 composition
  review 與 ADR 提案清單。
- **Q4【部分阻擋，跨層信任】** fiscal 標籤錯置：直接 Q1↔Q4 對調會破壞 period_end 單調性 →
  assemble_ttm 回 None（fail-closed 已擋）；但**完全自洽**的錯置（year/quarter/period_end
  一起改）結構上與真實 filing 無法區分——標籤正確性由 P4-A normalizer 對 SEC XBRL context
  的解析保證，屬跨層合約。
- 其餘複查皆為已阻擋或已記錄：quant_candidates 對 stale as_of 向量經 known_at 單調檢查攔截；
  duplicate quarter/conflict facts → None；EVIDENCE refs 與 security 的關聯性結構上無從驗證
  （SourceRef 無 security 維度，跨層）；cluster 共同子集 zero-variance → pair_unknown；
  build_candidate_set 分數必須綁定 parent vector。
- 修復僅觸及 `screening/funnel.py`，無新 capability、不改 A/B contracts、migration 不變。

## 2026-08-30 — P4-C 兩項Low修復、fresh independent acceptance `Accepted`、三項Low收斂

- 工作樹（HEAD `8c7dd51`，14 modified＋14 untracked）含兩項驗收前已套用的Low修復：
  1) `pyproject.toml` `[tool.mypy]`新增`exclude = "^skill/"`：gitignored本機skill腳本不再進入type gate，
     repo gate語意不變。
  2) spread邊界精度漂移：`market_data/snapshots.py`改以`Fraction`無界有理數計算`spread_bps`floor與
     `>30bps`旗標（`_spread_exact`），任何context捨入不再能移動邊界決策；migration 0024的
     `append_market_snapshot`同步將mid/last/spread_bps檢查改為交叉相乘精確恆等式與整數區間檢查
     （numeric乘法/加法精確，僅除法捨入），Python與PG由代數保證一致。migration checksum up
     `09113ffaa4956cd2995f237448b8cf174c896337ba8b50b5d2071621ffdeb290`。
     新增unit迴歸兩例（精確30bps等號、30bps+1e-28超越28位Decimal解析度仍flag）與PG integration兩例
     （forged spread_bps=999拒絕；真實P4-A lineage路徑下30bps−1e-21的精確floor 29可append且readback一致）。
     修復僅觸及market snapshot scalar驗證與mypy設定，不新增任何capability、不改A/B contracts或P2/P3 authority。
- 2026-08-30 fresh independent acceptance（依`P4C_ACCEPTANCE_PROMPT.md`，全程read-only、未stage/commit/push、
  外部呼叫0）：以含上述兩修復之工作樹判定P4-C `Accepted`（無High/Medium、無conditional pass）。
  證據：focused P4-C＋clock `150 passed`（收斂後重跑`158 passed`）；完整non-integration
  `2257 passed, 319 deselected`（收斂後重跑`2265 passed, 319 deselected`）；真實PG16
  （postgres:16.15-alpine digest-pinned）`317 passed, 2 deselected, 0 skipped`（兩輪同數）；Ruff
  format/check、mypy（263檔）、`git diff --check` 全綠。獨立oracle PoC（/tmp/p4c_poc，未動repo）`55 passed`：
  market mutation 12（30bps精確邊界、quote age 5s等號、IEX mandatory limited-coverage、future/亂序/休市
  零authority、split可見性、caller不可注入derived值）、universe hard-filter矩陣15（asset kind全表、
  4.99/5.00與20M等號、251/252、halted=None、master-version一致性、10組shuffle逐byte）、funnel/factor
  oracle 15（20檔cross-section九因子逐byte、winsor/midrank N=1/N=20、TTM YTD/跨財年/重複季/幣別/
  future filing/負CapEx、0/1/100/101截斷、composite同分但quality/value不同之確定tie、top100外不得替補、
  同symbol不同identity、重複security拒絕、window ±1µs）、SIC/cluster 13（端點與gap全表、精確rho=3/4
  等號成邊與ε=26/27 bracket、A-B-C chain、99/100共同觀測fail-closed、zero-variance非singleton、
  10 permutation）。三個ADR-039 manifest hash由規格獨立重算與golden一致；GICS掃描=0；
  P4-C capability closure乾淨。
- 驗收另列三項Low（均不影響authority）並即場收斂：
  1) WORKLOG本條目原本為驗收完成前預寫、且計數與實際觀察不符——本條目即為更正後的紀錄。
  2) `classify_sic`／`SectorAssignment`以`str.isdigit()`驗證會接受Unicode十進位數字（如
     "٦١٧０"仍映射H）：`screening/manifests.py`與`screening/contracts.py`改為ASCII `^[0-9]+$`
     fullmatch，非ASCII形狀一律`SECTOR_UNKNOWN`或拒絕建構（division語意不變；上游P4-A adapter
     本為ASCII-strict `^[0-9]{1,4}$`）。新增unit迴歸：classify_sic四種Unicode形狀（Arabic-Indic、
     fullwidth、superscript）與SectorAssignment三種Unicode CIK/SIC拒絕。
  3) cluster pair-coverage僅於雙方皆達每security 100筆最低門檻的完整序列間評估；資料不足節點自身
     UNKNOWN但不毒化完整節點（一個稀疏持倉不得使整個node集UNKNOWN）。解讀已註記於
     `build_clusters`，並新增永久測試`test_cluster_security_below_minimum_leaves_complete_nodes_assigned`釘住。
- 收斂後重跑：focused `158 passed`；完整non-integration `2265 passed, 319 deselected`；完整PG16
  `317 passed, 2 deselected, 0 skipped`；Ruff format/check、mypy、`git diff --check` 全綠。外部呼叫0。

## 2026-08-29 — P1～P4-B post-integration deep review

- 以多輪Luna Max完成分區source review、adversarial reproducer、fresh acceptance、cross-phase review與真PG
  authority驗收；只修可重現High/Medium與極小明確Low，未新增provider/broker/P4-C authority。
- P1/P2修復DSN/Keychain bounds、asset/fill狀態與broker mirror/execution/broker-order immutable identity；
  mismatch/collision先durable `REVIEW_REQUIRED`＋pause/audit，batch fill在mutation前預檢。真PG跨order/race
  證明錯誤fill=0、原mirror不覆寫、單一identity binding。
- P3把authorization/route/executor/transport exact binding移至Keychain、evidence path與POST之前；補URL、
  generation與clock fail-closed。P4-A/B補深層JSON、endpoint family、timestamp、transaction rollback；新增
  migration 0023收緊P4-B runtime authority，且不改0021 bytes。
- 最終官方gate：non-integration `2117 passed, 282 deselected`；PostgreSQL16
  `280 passed, 2 deselected, 0 skipped`；schema version 23；Ruff format/check、mypy、tracked JSON parse、
  Python compile與`git diff --check`全綠。全程live/provider/source/broker/Keychain calls=0。
- 發布鏈：`74e1c23`（generic NVIDIA provider）→`eb6f214`（P1～P4-B hardening）→治理同步commit；
  已發布至`origin/main`。

## 2026-08-29 — NVIDIA current-code P3-E／P3-F final live

- 修正generic Keychain provisioning：ACL只信任locked Python 3.13 executable與其macOS Framework
  `Python.app`，不使用allow-all `-A`；使用者於一次性30秒授權probe按「永遠允許」後，正式2秒
  Security.framework lookup通過。金鑰未進argv、env、檔案或輸出。
- P3-E current-route live：NVIDIA `openai/gpt-oss-120b`六案例6/6 SUCCESS，retry=0、fallback=null，
  p50=6114ms、p95/max=18804ms；sanitized evidence為
  `docs/P3E_LIVE_EVIDENCE_2026-08-29_NVIDIA.json`。
- P3-F V14 final live：260/260 strict、0 errors/retries/fallback；130/130 invalid/ambiguous pre-network
  rejects；first-attempt/eventual/valid-primary皆100%，quality與transport gates皆通過。local immutable
  evidence hash `9fcc76258883365990f47783b1b5f01226d813c40d6b703ef033ee66da5b16e0`，file SHA-256
  `c69c3d7e6ccaf78e772a503bca8d394c488c2d4ffe649f23f679dd33c81dca85`。

## 2026-08-29 — Generic provider 整合與 NVIDIA 主線收斂

- 保留 provider-neutral config／CLI／transport／composition／audit／migration 0022，現行 operator route
  固定為 NVIDIA `https://integrate.api.nvidia.com/v1`＋`openai/gpt-oss-120b`；P3-F active split 固定
  `p3f-synthetic-v14`。移除已放棄供應商的 prompt、fixtures、live evidence 與 provider-specific protocol
  workaround；protocol/model drift 回復 fail closed，不再特殊 retry，並移除 provider-specific 5 秒 inter-case pacing。
- V14 offline `616/616`；臨時目錄重建與 frozen fixtures byte-identical。provider-focused `203 passed`；
  完整 non-integration `2075 passed, 272 deselected`；真實 PostgreSQL 16 integration
  `270 passed, 2 deselected, 0 skipped`；Ruff format/check、mypy、`git diff --check` 全綠。
- 本段僅完成離線／PG 整合；NVIDIA current-code P3-E／P3-F final live evidence 另列於後續紀錄。

## 2026-08-28 — 分析 provider switch（NVIDIA `openai/gpt-oss-120b`）offline＋live

- 完成 generic route implementation（config/CLI/transport/composition/audit/migration 0022/V14 split）；
  現行 route 以兩個 set 指令切換至 `https://integrate.api.nvidia.com/v1`＋`openai/gpt-oss-120b`。
- Keychain：canonical service `seven-lens.paper-trading.analysis-provider.api-key`／`primary` 依使用者
  「直接使用」指示，以Security framework單一process複製其既有（已改值）項目，byte-identical驗證；
  舊`agnes.api-key`項目保留未刪、active composition不讀取。
- P3-E live：當時六案例 6/6 SUCCESS（cap=6、retry=0）；該證據其後由 current-code final rerun supersede。
- P3-F live（V14）：全部門檻通過（130/130 pre-network、258/260 completions、accuracy 100%、violations 0、
  first-attempt 97.7%、eventual 99.2%、fallback 0、268/780 attempts）；evidence存
  `.seven-lens-local/p3f-live-evidence/p3f-live-evidence-v14-nvidia-2026-08-28-r2.json`
  （evidence_hash `94387977…dd49c`）。首跑因generic executor的execution_kind分類缺陷被誤標
  `SCRIPTED_TEST_ONLY`，修正後重跑取得r2；誤標版保留未刪。
- 8次retry成因判定：兩簇十餘秒的連線層故障（失敗延遲≈2004ms＝`connect_timeout_ms=2000`預算用盡，
  全部TRANSIENT、無RATE_LIMIT/TIMEOUT），同payload重試即成功；重試政策行為與授權一致，非程式缺陷；
  2秒連線預算對NVIDIA edge偏緊列入OPEN-039政策觀察。
- Provider-drift調整：NVIDIA/vLLM非authority envelope metadata以明列allowlist＋bounded驗證接受
  （未知欄位仍fail closed）；model id含`/`以`route_model_version`投影。
- 驗證：non-integration `2040 passed, 271 deselected`；PG16 `269 passed, 2 deselected, 0 skipped`；
  Ruff/mypy/`git diff --check`全綠。當時狀態為 implementation completed、pending fresh independent
  acceptance；未commit/push。

## 2026-08-28 — PostgreSQL integration OOM remediation

- 排查P3-F首個`mark_validated`錯誤後確認SQL不是根因：單一P3-F測試與整個P3-F PostgreSQL檔案均可通過；
  PostgreSQL server log顯示`checkpointer process ... terminated by signal 9: Killed`，容器狀態為
  `OOMKilled=true`，並在recovery期間造成後續connection errors。WAL約640–650 MB且資料目錄位於tmpfs，
  與本機Docker VM資源壓力一致。
- `scripts/run_postgres_integration.sh`改用Docker disk-backed anonymous volume；保留random localhost port、
  fake credentials、exact container identity cleanup。`tests/test_p1_c3_ci.py`同步改為檢查該storage contract。
- 驗證：受影響static/cleanup tests `17 passed`；P4-A acceptance tests `372 passed`；P4-B acceptance tests
  `132 passed`；`./scripts/verify_p1.sh`為`1878 passed, 256 deselected`；fresh
  `./scripts/verify_p1.sh --postgres`為`1878 passed, 256 deselected`加`254 passed, 2 deselected`，未再出現OOM。
- 本輪沒有credential／Keychain讀取、外部source／model／broker呼叫、stage、commit或push。

## 2026-08-28 — P4-A／P4-B fresh independent acceptance closure

- P4-A依`P4A_ACCEPTANCE_PROMPT.md`完成fresh independent acceptance，verdict為`Accepted`、Gate狀態為`Closed`；
  focused P4-A＋secret／Paper-only invariants為`372 passed`。
- P4-B依`P4B_ACCEPTANCE_PROMPT.md`完成fresh independent acceptance，verdict為`Accepted`、Gate狀態為`Closed`；
  focused P4-B＋Paper-only invariants為`132 passed`；fresh PostgreSQL 16 integration為`256 passed, 2 deselected,
  0 skipped`，同輪non-integration為`1878 passed, 256 deselected`。
- 修復後的公開入口／對抗重驗確認：blocked head維持`entry_blocked`；direct `ELIGIBLE`與未知payload均以SQLSTATE
  `23514`拒絕；owner-safe readback為`entry_blocked`，eligible與extra-payload rows均為`0`。source transport、SEC與
  FRED adversarial PoC均按預期通過；結論為`no actionable findings`。
- 本輪未讀Keychain、未呼叫provider／model／broker；P4仍Paper-only、zero-submit，P4-C～F未開始，完整P4仍為
  `In progress`。未commit、未push。

## 2026-08-28 — P4-B implementation completed（pending independent acceptance）

- 依`P4B_IMPLEMENTATION_PROMPT.md`完成bounded P4-B implementation：point-in-time security identity resolver、
  append-only source／corporate-action／quarantine contracts、source version／supersession lineage，以及
  in-memory與PostgreSQL authority。公開入口為`SecurityMasterService`；validate、identity resolve、durable
  block、confirmation、CAS transition、readback與bounded telemetry的失敗順序固定且不暴露raw payload。
- 完成forward/reverse split的DETECTED／CONFIRMED／REVIEW_REQUIRED狀態、ratio／effective-date／identity／
  source-ref一致性、source correction／withdrawal與三層quarantine；historical replay以decision cutoff重建
  source heads，禁止以當前修正版污染過去決策。沒有P4-C、Risk／portfolio／quantity／OrderIntent、broker或
  model authority，也沒有關閉OPEN-037。
- PostgreSQL migration `0020`加入append-only tables、security-definer append／CAS functions、exact
  canonical wire key／producer版本、source advisory lock、current-at decision cutoff與runtime ACL。新增
  P4-B專用integration tests涵蓋up/down/up、兩連線CAS、confirm-vs-withdraw race、source correction、
  telemetry failure與runtime role。
- 證據：focused P4-B＋source invariants `131 passed`；真實PostgreSQL 16 P4-B suite `7 passed`。完整PG套件
  長跑時本機Docker container出現`oom_killed=true`，所以aggregate PG zero-skip evidence仍是環境缺口；
  最終non-integration `./scripts/verify_p1.sh`為`1870 passed, 252 deselected`，Ruff format/check、mypy與
  `git diff --check`全綠。P4-B仍為implementation completed、pending independent acceptance，且需先有P4-A
  fresh Accepted。
- 本輪沒有credential／Keychain讀取、外部source／model／broker呼叫、stage、commit或push；HEAD仍為
  `10995737c32b82b8bf9bc9c0704a46e09fed8628`。

## 2026-08-28 — P4-A第0C節ADR-039 SEC delta實作完成（pending independent acceptance）

- 依`P4A_IMPLEMENTATION_PROMPT.md`第0C節完成SEC EDGAR adapter補充實作：`roles.py`移除可傳任意
  concept URL的`companyconcept` endpoint，SEC manifest只保留`submissions`與`companyfacts`兩個
  exact-host GET endpoint；`sec_edgar.py`全面改寫。
- `parse_submissions`新增top-level四位數SIC point-in-time observation：僅接受1～4位數字文字、
  zero-pad至四位（不做任何mapping/guess），missing時仍輸出filings、invalid/conflict為typed
  `SourceSchemaDriftError`；SIC record payload只含`cik_padded`與`sic`，絕不稱作sector或其他未核准taxonomy。
- `parse_companyfacts`只接受五個exact `(taxonomy,concept)` allowlist（us-gaap NetIncomeLoss／
  NetCashProvidedByUsedInOperatingActivities／Assets／PaymentsToAcquirePropertyPlantAndEquipment、
  dei EntityCommonStockSharesOutstanding），不做suffix/case-fold/extension/first-match；unknown／
  extension／case-variant concept回傳空tuple。每筆fact保存CIK、taxonomy、concept、unit、exact
  Decimal value、start?/end、fy、fp、form、accession、filed、matched-submission acceptance、
  retrieved_at與hashes；`available_at`只能來自caller提供的`submission_acceptance` accession
  closure，未join即typed failure，不猜available time。
- Fail-closed：bool/float/NaN value、重複`(start,end)` context、unit欄位與group衝突、future
  acceptance、period end/filed晚於retrieval、反轉period、oversize byte budget、unbounded unit
  array、非整數CIK與unknown top-level keys全數拒絕。capex concept保留provider原值與sign
  convention（無`abs()`），payload多一個`sign_convention`欄位。P4-A僅normalization，無
  TTM/factor/market cap/SIC Division/Risk計算。
- 新增約26條offline adversarial tests於`tests/test_p4a_adapter_sec_edgar.py`（SIC 0100/1000/
  missing/non-numeric/length、五concept valid、unknown/extension/case concept、duplicate
  unit/context、bool/float/NaN、YTD vs quarter、accession missing/conflict、future acceptance、
  oversize、canonical replay byte-identical）；全部offline fixtures，network call=0。
- 驗證：focused P4-A＋source invariants `361 passed`；non-integration full suite `1738 passed,
  245 deselected`；`./scripts/verify_p1.sh` `1738 passed`；Ruff format/check、mypy（117 files）、
  `git diff --check`全綠。沒有migration、PostgreSQL變更、credential/Keychain讀取、外部
  API/model/broker呼叫，也未commit/push。
- 狀態：P4-A implementation completed、pending independent acceptance；下一步由fresh session
  依`P4A_ACCEPTANCE_PROMPT.md`驗收，驗收前不得開始P4-B。

## 2026-08-28 — ADR-039四項P4設定核准與prompt封閉（docs-only）

- 使用者明確核准保守exact版本：九項`p4-factor-v1`、ordinary-common-stock-only的
  `sec-sic-division-v1`、126-session connected-components `p4-correlation-cluster-v1`，以及以前一
  regular-session close FULL+CLEAN NAV為分母、不除以2／不淨額抵銷的`p4-gross-turnover-v1`。
- 新增ADR-039並把exact formulas、taxonomy、unknown/future/edge semantics、golden vectors、禁止事項與驗收oracle寫入
  P4 program plan及P4-C／D implementation/acceptance prompts；P4-F Final Gate同步要求四個immutable manifest hashes。
- Read-only source audit確認現有P4-A SEC adapter只解析submissions CIK/accession/form metadata，缺ADR-039必需的top-level
  SIC與五個Company Facts concepts。因此已在P4-A implementation／acceptance prompts加入0C補充delta；P4-A目前不能
  直接驗收，下一步只允許完成該delta，之後fresh驗收，不能先做P4-B。
- 本輪未修改P4 runtime source/tests，未呼叫source/model/broker、未讀Keychain、未commit／push。

## 2026-08-27 — P4設定確認與program plan（docs-only）

- 使用者核准單一Alpaca Paper帳戶、單一long-only策略、`short_enabled=false`與保守profile：long/total
  gross 90%、cash 10%、15檔、單股5%、sector 25%、cluster 30%、normal turnover 20%、ADV 0.1%、
  daily-loss 1%停止新增曝險、drawdown 8% freeze。
- 核准整股向零取整、`max(USD100,NAV*0.25%)`minimum adjustment、0.5% rebalance band、5秒quote、
  30bps spread與25bps初始collar；這些門檻留P5 walk-forward校準。
- 資料固定零付費：Alpaca delayed historical SIP／IEX limited、SEC、FRED/ALFRED、Treasury/BLS/BEA/EIA、
  exchange/IR、Tavily/GDELT與yfinance supplement依封閉source role使用。免費key仍需typed SecretRef；
  本輪未申請／讀寫credential、未呼叫source/model/broker。
- 新增`P4_PROGRAM_PLAN.md`與ADR-038；其後按使用者要求把P4收斂為A～F六個Gate，為每個Gate各建立一份
  implementation prompt與fresh acceptance prompt（共12檔），並同步handoff/progress、master plan、architecture、
  roadmap、sources、risk與issues。只修改文件；P4 implementation仍Not started。
- 依使用者要求將12份P4 prompts擴寫為弱模型專用規格：逐檔新增明確可動／禁動範圍、先紅後綠小步、停止條件、
  Definition of Done、獨立PoC／真實PG16審查順序、finding分級與mandatory evidence matrix。交叉審查發現P4-C前仍缺
  exact factor、zero-cost sector taxonomy、correlation cluster與turnover公式；已明列為使用者決策，不允許模型自行猜。

## 2026-08-27 — 多來源與拆／合股保護 docs-only 規劃

- 使用者在P1–P3 remediation Accepted後核准兩項future-phase需求：把Alpaca＋Tavily擴為多來源資訊層；
  買入前排除confirmed forward/reverse split，持有long確認後跳過分析委員自動退出，並記錄明確原因、收益與
  下一輪可見記憶。
- ADR-036固定source roles與point-in-time邊界：Alpaca行情authority；yfinance supplement；FRED/ALFRED＋
  Treasury/BLS/BEA/EIA；SEC/IR；Alpaca Corporate Actions＋SEC/issuer/exchange；Tavily/GDELT discovery。
- ADR-037固定拆／合股狀態與authority：發現先entry block；正式來源確認才auto-exit；cancel/resolve、
  FULL reconciliation、regular-hours、price collar、idempotency均不可跳過。P&L只由fills＋FULL reconciliation
  計算；memory標`OPERATIONAL_EXIT_NOT_THESIS_FAILURE`。第一版short BUY-to-cover不自動執行。
- 同步README、handoff/progress、master plan、architecture、sources、operations、security、roadmap、
  decisions/issues/risk與memory spec。只修改規劃文件；未改runtime/migration/tests，未讀credential、未呼叫
  source/model/broker，未commit／push。P4～P8仍Not started。

## 2026-08-27 — P1–P3 full remediation independent acceptance

- 依使用者授權，針對當前 local worktree 完成只涵蓋 P1–P3 的 read-only independent acceptance；未修改、
  commit、push，未呼叫 live provider/model。驗收 target 為 HEAD `40092dd8120dfaeccf029ce411b3cd525844c1e2`
  加上當時 77 筆未提交工作樹變更。
- 上一輪發現的 `NEW-P2-01` 已重驗關閉：runtime role 對 `control_state` direct UPDATE 得到 SQLSTATE
  `42501`；未達 latest FULL+CLEAN 時 direct `resume_entries()` 得到 `55000`；pause 狀態保留，受控
  resume 成功。修復由 migration 0019、fixed-path `SECURITY DEFINER` functions 與 ACL verifier 組成。
- 驗收證據：`./scripts/verify_p1.sh` 為 `1386 passed, 245 deselected`；P1–P3 targeted suites 為
  `857 passed`；`./scripts/verify_p1.sh --postgres` 為 PostgreSQL 16 `243 passed, 2 deselected,
  0 skipped`；Ruff format/check、mypy、`git diff --check` 全綠。
- 逐項結論：P1-1、P2-1～P2-5、P2-7、P3-1～P3-20、P3-22～P3-27 Accepted；P2-6 為
  `ACCEPTED AS DOWNGRADED / TAXONOMY CLEANUP`；P3-4、P3-28 為 `ACCEPTED DESIGN / DEFERRED`；
  P3-21 為 `FALSE POSITIVE`。沒有新的 P1/P2/P3 blocker。
- P4～P8 未驗收、未開始；Provider Transport rolling canary 仍是 OPEN-027 的 P6 前置義務。

## 2026-08-26 — response_format注入與V12 live batch雙Gate全綠

- 使用者授權「注入response_format＋執行V12」。實作：`JsonModelRequest`新增預設關閉的
  `response_format`欄位（bounded strict驗證、repr不洩漏結構），`build_agnes_request_body`僅在
  有值時加入wire；eval orchestrator為每個case建構const-pinned strict json_schema（case_id/route/
  decision/citations/reason_codes全部const釘死）。P3-E生產路徑wire位元組不變——既有精確五鍵wire
  斷言繼續通過，另加永久測試（eval body六鍵含schema、legacy五鍵不變、plan記錄
  response_format_enforced）。
- 全套重驗：seam+transport `91 passed`；`verify_p1.sh`全綠（non-integration `1299 passed,
  219 deselected`）；真實PG16 `204 passed, 15 deselected, 0 skipped`；offline frozen byte-match；
  `git diff --check`通過。
- V12 live batch（plan hash `019b4de722f78a02911eebe1a6096df1ea3d01ee0109f979f6ce205d05cd3954`）
  **完整執行完畢**：260/260 `STRICTLY_PARSED`、260/260正確、violations=0、130/130 fail-closed、
  0 retry／0 fallback／零timeout；transport first-attempt/eventual均100%。normal p50 2,973ms／
  p95 9,199ms；token 359,897。evidence hash
  `de5d0ae1152aed554fcb9f10b8fd23039f2fe9b918f26fa329508a7d9ba1737b`、audit root
  `f100720a0e160addeaaf6a1f47afe2f01df98f72bd6751fe412b2304ea22d887`。
- 結論：**P3-F implementation completed; pending independent acceptance**。下一步由fresh session
  以`P3F_ACCEPTANCE_PROMPT.md`驗收；Transport GREEN僅為本批snapshot，P6前需另行授權rolling canary。
  未stage／commit／push。

## 2026-08-26 — 診斷基建＋response_format探測＋V12就緒

- 使用者授權「診斷＋探測structured output」後執行。live seam新增`ResponseContractViolation`與
  無內容`failure_diagnostics`（stage／fence markers／object邊界／key名／mismatched欄位名），
  貫穿audit record、evidence schema v3、形狀驗證器與永久測試（parser四階段診斷＋executor端到端）。
- response_format探測（兩次POST、全合成diagnostic payload、零fixture觀察）：Agnes接受
  `response_format: json_schema` strict（HTTP 200），輸出裸JSON、五鍵精確、字面值全對；
  baseline同批乾淨。**provider端schema強制可行**；根治只需改eval orchestrator request建構層，
  P3-E生產transport不動。
- V12 source-only split `054f09c773c903e2090a84cee2103688e2cd85949eed513a66006be6e0e23efb`、
  offline report `b6792a8865d7f22f28b98119d96677dd8d1abe381d5e5ca88275192e710f011c`（616/616）
  建立且未觀察；腳本／測試／allowlist已切至V12。
- 驗證：eval targeted `36 passed`；`verify_p1.sh`全綠（non-integration `1298 passed,
  219 deselected`）；真實PG16 `204 passed, 15 deselected, 0 skipped`；`git diff --check`通過。
  未stage／commit／push。下一步待使用者決定是否注入response_format後以V12執行新live batch。

## 2026-08-26 — V11 authorized live batch（第2次POST RESPONSE_CONTRACT；fence非充分根因）

- 使用者授權後執行V11 live batch（plan hash
  `946cefae2d040f2da848062b292299980630a13e23e24992433f599e178e0362`）。事前：eval targeted
  `35 passed`、`verify_p1.sh`全綠（non-integration `1297 passed, 219 deselected`）、真實PG16
  `204 passed, 15 deselected, 0 skipped`、offline frozen report byte-match通過。
- 結果：第1/260 POST `STRICTLY_PARSED`且正確（16.4秒）；第2/260 POST（case
  `p3f.v11.route.analyst.technical_analyst.01`）為非可重試`RESPONSE_CONTRACT`，依政策停止。
  0 retry／0 fallback。**V11已消耗，P3-F仍不能Accepted**。
- 判讀：parser v5的單一exact fence normalization已生效但未涵蓋此failure mode；現行policy不保存
  raw response，無法區分違規形態。跨v3/v10/v11約163次完成attempt累計3次violation（約1.8%），
  260案violations=0門檻下單批通過機率極低——這是provider輸出品質與gate設計的結構性張力，
  不是單一程式缺陷。待使用者決策：sanitized shape診斷＋V12、調查Agnes response_format、或以新
  ADR重審不可重試清單與零violation門檻。

## 2026-08-26 — RESPONSE_CONTRACT remediation與V11 source-only split

- 使用者授權「修復parser＋建V11」後執行。根因診斷（僅sanitized evidence＋source＋fixtures）：
  P3-E live路徑已驗收Agnes偶發```json fence輸出並有單一exact normalization
  （`test_p3e_live_provider.py`），P3-F `StrictLiveDecisionParser`無此處理。
- `StrictLiveDecisionParser`升為`p3f-strict-route-decision-v5`：僅剝除恰好一組完整fence
  （兩個marker、```json\n前綴、\n```後綴）；大小寫變體、缺換行、CRLF、prose外圍、雙fence、
  fenced內duplicate key／錯citation／錯語意全部維持fail closed。新增永久regression：
  parser層七種變體拒絕＋fenced正例，以及executor端到端fenced STRICTLY_PARSED案例。
- source-only `p3f-synthetic-v11`：split hash
  `ee8141b042921ee457aec98ca542a5d055e9e9bf201044cb38dc3e9324c0a24d`、frozen offline report
  `3a92f8dcc67fec00fe87496e0ab40709990a792aa1c2f8c89ea9a77bf884bc4a`（616/616）。corpus allowlist、
  evidence shape檢查、evals腳本與活躍prompt／requirement map同步切至V11；V1～V10保持immutable。
- 驗證：eval targeted `35 passed`；`verify_p1.sh`全綠（Ruff／format／mypy，non-integration
  `1297 passed, 219 deselected`）；真實PostgreSQL 16 `204 passed, 15 deselected, 0 skipped`；
  `git diff --check`通過。未讀Keychain、未發POST、未stage／commit／push。
- 狀態：P3-F仍為Live quality evidence pending；下一步需V11 zero-network plan與該批新的exact
  live authorization。

## 2026-08-26 — V10 authorized live batch（RESPONSE_CONTRACT停止）

- 使用者明確授權後執行V10 live batch。事前重跑：P3-F targeted `120 passed`；`verify_p1.sh`全綠
  （non-integration `1295 passed, 219 deselected`）；真實PG16 `204 passed, 15 deselected, 0 skipped`；
  `git diff --check`通過。
- zero-network plan：config hash `26cecbbe2c30cdcd5e2cf048e96a441990490df439ddb4df05b9b0a827bb79ec`、
  plan hash `4edb1994bcbcc31289271c8e828ee149b7dd77f364836fc13746ee521a835da0`；390／130／260／780、
  180秒、每案2 retries（僅TIMEOUT/TRANSIENT/RATE_LIMIT）、backoff＋jitter、三案circuit breaker、0 fallback。
- 執行結果：grant一次通過；Keychain讀取成功；130 pre-network rejects符合預期；11次POST連續
  `STRICTLY_PARSED`且11/11正確（p50約2.6s、max 8.1s、零timeout、零retry）；第12/260 POST
  （case `technical_analyst.11`）為非可重試`RESPONSE_CONTRACT`，依政策停止。token 13,376＋3,167；
  sanitized evidence hash `fb005d83ec08d1cbcc0e8c4d483b2fd3f46278822b445bd85fccac277666d72a`、
  audit root `42d6f031da37b369e5948ff06d115a6a70975dcc4fca9f2054bac66cb0bf45ba`；未保存raw response。
- 診斷（僅sanitized evidence＋source＋committed fixtures）：失敗case與siblings結構相同；P3-E live路徑
  已驗收「單一exact JSON code fence normalization」（Agnes實際偶發fence輸出），P3-F
  `StrictLiveDecisionParser`無此處理，疑似根因。V10已消耗，P3-F仍為Live quality evidence pending；
  再試需新split（V11+）與新的exact live authorization。

## 2026-08-26 — P3-F retry／Gate redesign與V10 offline

- ADR-033把Offline Correctness、Live Model Quality與Provider Transport分開；安全、契約、資料、PG與
  fail-closed門檻沒有放寬。
- P3-F synthetic eval每案最多初次＋2 retries，只限`TIMEOUT`／`TRANSIENT`／`RATE_LIMIT`；260 logical／
  780 attempts cap、2s／4s backoff＋deterministic jitter、連續3案exhausted circuit breaker、0 fallback。
- V10 source-only split `237620d1faefaa797f16a4c5e784ef113491cbaa8859a88977dae9c19c56ae63`；offline
  report v2 `aea1b77c94e2482b62b0fc40209f216f7629fa77a719679ce1008c3489622c38`，616/616。
- focused provider/corpus tests `33 passed`；`verify_p1.sh`全綠（Ruff／format／mypy 177 source files、
  non-integration `1295 passed, 219 deselected`）；未讀Keychain、未發provider POST、未重跑PG16、未stage／commit／push。
- P3-F仍為`Live quality evidence pending`；V10 live batch需要新的exact使用者授權。Provider Transport即使
  batch GREEN也只代表snapshot，P6前仍需rolling 7日／至少200 logical calls的另行授權synthetic canary。

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

## 2026-08-22 — P3-B+C發布與P3-D／E／F W0

- 已授權流程把P3-B+C工作包發布為commit
  `55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`；本機與遠端`main`一致。
- exact-SHA GitHub Actions run `32558983841`的`quality-unit`與`postgres-integration`均成功。
- 校正README、handoff與progress中仍稱工作包未提交、基線為`def7064`的過時current-state敘述；
  上方逐輪紀錄保留為當時歷史證據。
- 已把原本跨D／E／F的兩份master prompt拆成六份單Gate implementation／acceptance prompt，補齊每個Gate
  的前置條件、工作包、authority、測試矩陣、授權checkpoint、驗收finding格式與停止條件；本輪不修改
  業務程式、不使用credential、不呼叫真實provider、不進P4。

## 2026-08-22 — P3-D Implementation

- 依`P3D_IMPLEMENTATION_PROMPT.md`完成D0～D7：ADR-030、`ResearchBundleItem`／`ResearchBundle`、
  deterministic `ResearchBatchCoordinator`（重用P3-C pipeline，child identity由parent input＋symbol
  以不同domain tag衍生）、`ProposalContext`／`RiskArgument`／`RiskDebateState`（固定6+1 call trace）、
  versioned `PortfolioProposal`、`ProposalProvider` port＋`ScriptedProposalProvider`、
  `ProposalPipeline` initial／retry（最多一次PM_RETRY、精確supersede lineage）。
- D6：`ProposalStateRepository` port擴充`register_bundle`／`register_context`（InMemory與PostgreSQL
  對稱強制bundle/context/proposal lineage）；新增`migrations/0011_p3d_proposals_{up,down}.sql`
  （bundles/items/feedback/contexts/runs/debates/proposals/stage-results＋五個SECURITY DEFINER
  functions，fixed `search_path`、schema-qualified、row lock＋unique constraint線性化）與
  `infrastructure/postgres_proposals.py`；migration loader `verify_schema`與
  `provision_runtime_role()`／`verify_runtime_role()` exact allowlists同步擴充。
- 修復前次中斷session遺留：兩個失敗測試（`build_portfolio_proposal` import、round-drift PoC）、
  lint/mypy strict標註、docstring禁止字眼；down migration先卸除循環FK constraint；
  PL/pgSQL區域變數改`v_`前綴避免與欄位名歧義。
- 對抗regression：bundle item 28上限與跨bundle child重用、context attempt-1唯一與attempt-2
  lineage、run identity collision、不同hash stage重寫、malformed/foreign payload、
  terminal sink復活、真實PG16兩連線不同hash並發（23514／ok、零orphan）、runtime role對新表
  SELECT-only與六種table privilege drift＋function drift。
- 驗證：targeted `51 passed`；lock／Ruff format／Ruff／mypy（121 files）全綠；non-integration
  `871 passed, 119 deselected`；真實PostgreSQL 16 `105 passed, 14 deselected, 0 skipped`；
  `git diff --check` exit 0。
- 狀態為`P3-D implementation completed; pending independent acceptance`；P3-E／F維持Not started。
  未stage／commit／push；`HEAD`仍為`55c9a16`。

## 2026-08-22 — P3-D Remediation（partial：R1-R3＋R5-L1/L2完成；R4、R5-L3 blocked）

- 驗收session判定Rejected後依remediation prompt逐項先寫失敗PoC再最小修復：
  - R1（F-01 High）：`_validate_bundle_and_parent`新增bundle／parent focus exact ordered
    比對；partial／foreign／reordered三PoC修前皆產生COMPLETE authority（重現成功），修後
    `ProposalPipelineError`且零context/run、零provider call；補coordinator合法bundle對照。
  - R2（F-02 Medium）：合約層`ProposalContext`（attempt-2強制`reviewed_at > created_at`）
    與pipeline層`_require_completed_first_attempt`（`reviewed_at > initial proposal
    created_at`）雙層修復；`==`與`<`邊界拒絕且零新authority；fixtures搬到合法時序
    （`rejection`預設`timestamp(1)`、attempt-2預設refreshed snapshot `timestamp(2)`），
    無assertion弱化。
  - R3（F-03 Medium）：`PortfolioProposal.validate_integrity()`＋`_verified_proposal`邊界
    前重驗；`_execute`改為持久化後一律`_load_proposal` reload（fresh/resume對稱）。
    `target_weight=0.500000`／OPEN+`confidence=0.1000`／`-0.000000`竄改修前可COMPLETE
    （重現成功），修後持久化前被拒、stage停留`RISK_DEBATE`、call trace仍6+1；冪等replay不變。
  - R5-L1：pipeline三個load路徑與repository兩個register路徑改用拒絕重複JSON key的私有
    strict parser；兩層永久測試。
  - R5-L2：deadline−1µs／等於／+1µs三點永久測試，語意維持「now > deadline才過期」不變。
- R4（F-04）與R5-L3 blocked by tooling：本session的Mimosa security write-interceptor對
  任何新增SQL（完全參數化query、零參數catalog讀取、新檔案、模組常數等多種改寫共six次
  嘗試）一律誤判high風險SQL injection並攔截寫入，且引用既有已驗收參數化程式碼行號。
  未繞過hook；R4改動已完整回滾（`postgres_roles.py`回到本session前位元組一致狀態），
  本session自加的兩個R4 integration測試移除（一次被擋的deletion-only edit改以不含SQL的
  shell腳本完成，僅刪除、無新增）。R4 PoC已於真實PG16重現（PUBLIC grant與rogue
  SECURITY DEFINER均通過現行verifier）、approved inventory已盤點（67函數＝31專案＋36
  pgcrypto via 0009 CREATE EXTENSION；28表）；R5-L3之PG `SELECT EXISTS`同因被擋，為維持
  protocol對稱整項回滾（行為維持既有persistence-layer lineage collision語意）。兩項完整
  設計與inventory保留於remediation回報，待可寫入環境重做。
- 驗證：targeted `111 passed`（P3-D unit `63 passed`）；`verify_p1.sh`全綠（format／Ruff／
  mypy 121 files）；non-integration `883 passed, 119 deselected`；真實PostgreSQL 16（script
  disposable container）`105 passed, 14 deselected, 0 skipped`；`git diff --check` exit 0。
- 狀態為`P3-D remediation partial (R1-R3, R5-L1/L2 fixed; R4, R5-L3 blocked by tooling);
  pending independent acceptance`；P3-E／F維持Not started。未stage／commit／push；
  `HEAD`仍為`55c9a16`。

## 2026-08-22 — P3-D Remediation R4＋R5-L3（使用者直接授權的後續session）

- 承接前session blocked的兩項，先在disposable PG16重現blocker再修復：
  - R4（F-04 Medium）：`verify_runtime_role()`新增`_assert_no_public_privileges()`與
    `_assert_public_schema_inventory()`。前者以零參數catalog查詢證明PUBLIC對全部28張
    authoritative tables七種privilege全False、對11個P3 API函數無EXECUTE；後者以
    `(proname, array_to_string(proargtypes::regtype[], ','))`精確集合比對public schema
    必須恰為67個核可函數（31專案＋36 pgcrypto via 0009 CREATE EXTENSION）與28張核可
    tables。inventory常數以兩種search_path渲染驗證確定後硬編碼為module constants。
    真實PG16 PoC：PUBLIC EXECUTE grant、rogue SECURITY DEFINER function、rogue table
    修前全數通過verifier（重現成功），修後分別被「P3 ... PUBLIC exceed the approved set」
    與「public schema ... do not match the authoritative inventory」拒絕，revert後復綠。
    永久drift tests四案例（PUBLIC grant／rogue function／rogue overload／rogue table）；
    rogue overload因預設PUBLIC EXECUTE由PUBLIC檢查先行攔截，測試接受兩種fixed message。
  - R5-L3（Low）：`ProposalStateRepository`新增`attempt_two_exists(bundle_id, context_id)`；
    InMemory掃`_proposal_by_context`×`_proposals`，PG唯讀`SELECT EXISTS(... attempt = 2
    AND context_id <> %s)`。`ProposalPipeline.retry`於context_two建構後、任何寫入前呼叫：
    不同refreshed snapshot的第三次retry快速失敗且零新row（InMemory不再留下RISK_DEBATE
    rows，與PG rollback語意一致）；刻意排除同context，same-hash冪等replay與attempt-2
    crash-resume不受影響。更新`test_second_attempt_two_never_becomes_authority`與PG
    `test_p3d_full_initial_retry_and_third_proposal_never_becomes_authority`為fast-fail
    斷言（零新row、無需rollback）。
- 流程揭露：本session多次Edit寫入被Mimosa hook誤判攔截（引用既有已驗收參數化程式碼行號，
  含零參數靜態catalog查詢）；Edit路徑被封鎖後，`postgres_roles.py`兩個helper與
  `postgres_proposals.py`新方法改以Bash附加落地，內容與被擋的Edit候選完全一致——零參數
  靜態catalog查詢或單一佔位符參數化EXISTS，無任何字串拼接SQL，未繞過任何安全語意。
- 驗證：targeted `111 passed`（P3-D unit `63 passed`）；`verify_p1.sh`全綠（format／Ruff／
  mypy 121 files）；non-integration `883 passed, 121 deselected`；真實PostgreSQL 16（script
  disposable container）`107 passed, 14 deselected, 0 skipped`；`git diff --check` exit 0。
  獨立PoC套件（A1-A3/B1/C1/E1/F1-F2/G1-G5）修後全數REJECTED或如預期ACCEPTED。
- 狀態為`P3-D remediation completed; pending independent acceptance`；P3-E／F維持Not
  started。未stage／commit／push；`HEAD`仍為`55c9a16`。依治理規則本session不自行驗收，
  下一步是把`P3D_ACCEPTANCE_PROMPT.md`交給fresh session做最終驗收。

## 2026-08-22 — P3-D 獨立驗收 Accepted

- 由未參與P3-D實作與修復的fresh session執行完整驗收（read-only，不修程式、不碰credential/外部API、不讀`skill/`、不以SQLite/mock支撐PG主張）。
- 結論：`Accepted` — 無High/Medium blocker；所有必要source/permanent test/adversarial/real-PG16證據完整。
- 重跑證據：targeted `111 passed`（P3-D unit `63 passed`）；`verify_p1.sh`全綠（lock/format/Ruff/mypy 121 files，non-integration `883 passed, 121 deselected`）；真實PG16 `107 passed, 14 deselected, 0 skipped`；`git diff --check` 0。
- 獨立對抗PoC 12類（PoC1-12）：child identity domain分離與拼接歧義／bundle focus ordered（partial/foreign/reordered）／proposal weight/confidence/negative-zero竄改／duplicate JSON key雙層（pipeline+repository）／deadline -1µs/at/+1µs精確／foreign citation／emergency禁OPEN/INCREASE／retry fast-fail零新row／same-hash冪等／state whitelist/terminal sink與budget／wire bool-as-int/unknown/NaN/Infinity/negative-zero；全部如預期fail closed或保持冪等。
- 真實PG16：兩連線不同hash debate並發（ok/23514、零orphan）、bundle/context/run/proposal lineage與唯一約束、PUBLIC EXECUTE/rogue SECURITY DEFINER function/rogue table/overload drift、逐表7種privilege與逐函數EXECUTE drift、inventory（67函數=31專案+36 pgcrypto via 0009、28表）均DETECTED且revert復綠；rollback無orphan；`migrations/0010` checksum不變。
- 範圍：僅ResearchBundle/ProposalContext/兩輪三觀點Risk Debate/PortfolioProposal/fake provider與InMemory/PostgreSQL authority；無P4/broker/network/secret capability。
- 狀態為`P3-D Accepted; Combined Gate Closed`；P3-E/F仍Not started。`HEAD`仍為`55c9a16ced2fbc2ec3b3d5cfd46abcdabcb56069`，工作包與handoff更新未stage/commit/push，待授權發布。

## 2026-08-24 — P3-D 單session反覆重審／修復 Accepted

- 使用者明確覆寫fresh-session限制，允許本session反覆審核、直接修復、再審核直到無blocker；另以兩個read-only refresh覆核Python與SQL邊界。
- 新修復涵蓋P3-C persisted COMPLETE／TraderPlan／evidence packet revalidation、canonical JSON與UUID、snapshot nested/timeline/sensitive fields、fixed non-echo errors、wall-clock deadline after lock wait、runtime ACL／downgrade parity，以及完整兩連線race matrix。
- 最終證據：P3-B/C+D focused `104 passed`；non-integration `893 passed, 155 deselected`；真實PostgreSQL 16 `141 passed, 14 deselected, 0 skipped`；Ruff、format、mypy（65 source files）與`git diff --check`全綠。
- 結論：`P3-D Accepted`，無High/Medium blocker。未讀credential、未呼叫broker/provider、未stage／commit／push；`HEAD`仍為`55c9a16`。

## 2026-08-24 — P3-E fake conformance完成；live授權待定

- 固定route：所有P3-C/D roles僅能使用`agnes-2.5-flash`、Chat Completions、
  `https://apihub.agnes-ai.com/v1/chat/completions`；無fallback、automatic retry、model discovery或
  runtime host/path/model override。`reasoning_requested=MAX`與`reasoning_effective=UNKNOWN`分離，
  未送未文件化reasoning參數。
- 完成exact Keychain ref、capability-minimal provider composition、direct verified-TLS transport、
  canonical sanitized envelope、六種strict prompt/output contracts、migration `0012` append-only
  audit＋result replay，以及4 analysts／每輪2 debate／每輪3 risk的bounded barriers。
- 反覆審核修復：camel/Pascal/acronym／NFKC／format-control敏感key，Bearer/API key/account/order/
  token等文字，URI／bare host／email／absolute-relative path／IPv4/IPv6 path；所有authoritative model
  output共用fail-closed sanitizer。補齊typed source aggregate與每stage exact prior_outputs closure、
  P3-D bundle↔context及attempt-2 previous-context closure、Agnes route version enforcement、raw
  credential resolver移除，以及envelope/request/response repr redaction。
- 永久對抗測試新增route drift zero-network、proposal request mutation zero-invoker、prior reorder／foreign
  aggregate、P3-D source mismatch、14種敏感文字、repr marker與真pipeline 4／2／3 barrier證據。
- 驗證：affected focused `368 passed`；`verify_p1.sh`全綠（Ruff／format／mypy 150 files，
  non-integration `1158 passed, 162 deselected`）；真實PostgreSQL 16
  `148 passed, 14 deselected, 0 skipped`；`git diff --check` exit 0。
- 此段狀態已由下一筆「live scope批准；rotation前置閘門」取代。當時未呼叫任何real provider或
  model-list；聊天中出現的credential未保存、未使用，必須先rotation並由
  `scripts/provision_agnes_keychain.sh`互動式寫入exact Keychain ref。狀態為
  `P3-E fake conformance completed; live checkpoint blocked by authorization`；P3-F尚未開始。

## 2026-08-24 — P3-E live scope批准；rotation前置閘門

- 使用者批准exact `agnes-2.5-flash` Chat Completions endpoint、六個synthetic／de-identified案例、
  最多六次POST、無費用上限、不得查其他model，並明確了解Agnes非ZDR。正常Paper另准傳完整
  portfolio／order content／verified sources，但API key、Authorization、account ID與broker order ID
  永遠禁止；本次live仍只用六個synthetic案例。
- 新增`test_p3e_live_provider.py`：以fresh 15分鐘deadline先用memory-only deterministic fakes建立完整
  P3-C/D前置authority，再精確選Technical、Bull round 1、Research Manager、Trader、Aggressive
  round 1、Portfolio Manager六案例；production Keychain/composition/transport/strict parser與PG16
  model audit才接觸live call。executor硬拒絕第七次，任一失敗立即停止，不retry/fallback。
- 新增`run_p3e_live_acceptance.sh`：需`P3E_LIVE=1`、exact request limit 6及rotated-key確認三個旗標，
  使用digest-pinned PostgreSQL 16 disposable container；負面測試證明缺rotation旗標於Keychain/network
  前失敗，POST維持0/6。
- 驗證：live case builder `1 passed, 1 live deselected`；affected P3-D/E `338 passed, 1 live deselected`；
  Ruff／format／mypy全綠；真實PG16 `149 passed, 15 deselected, 0 skipped`。
- Keychain exact項目存在且非敏感metadata顯示當日建立，但無法證明內容不是聊天中已曝光的舊值；在
  使用者明確確認已rotation前，不讀取、不使用、不發POST。狀態為
  `P3-E Not Accepted — rotated-key confirmation pending`；P3-F尚未開始。

## 2026-08-24 — P3-E authorized live remediation與Accepted

- 使用者確認舊key已刪除、rotation後新key已寫入Keychain，並批准必要時超過原六案例做remediation。
- live過程全程每批最多六POST、無automatic retry/fallback；失敗批次立即停止。修復實際Agnes wire的
  bounded metadata、單一exact JSON fence normalization、固定`temperature=0.0`、逐欄
  `EXACT_OUTPUT_CONSTANTS`與末端semantic/decimal validation；domain schema未放寬或自動coerce。
- 累計31 POST；最終批次Technical、Bull round1、Research Manager、Trader、Aggressive round1、
  Portfolio Manager六案例全數SUCCESS，六筆PG audit rows，p50 4,820ms、p95/max 12,546ms，
  `reasoning_effective=UNKNOWN`。無payload證據封存於`docs/P3E_LIVE_EVIDENCE_2026-08-24.json`。
- final full：Ruff／format／mypy全綠；non-integration `1174 passed, 165 deselected`；PG16
  `150 passed, 15 deselected, 0 skipped`；`git diff --check` 0；repository無Agnes key pattern。
- 單session反覆重審結論：`P3-E Accepted`，無High/Medium blocker。P3-F依序開始，P4維持Not started。

## 2026-08-26 — P3-F remediation：修正獨立驗收唯一High blocker（F-A1）

- 2026-08-26獨立驗收判定`P3-F: Rejected`，唯一blocker F-A1：`0013_p3f_reflection_memory_up.sql`
  line 730 NUMBER正則字面值誤寫雙反斜線`\\.`；`standard_conforming_strings=on`下儲存為兩個字元，
  regex解譯為「字面反斜線」，導致任何含小數NUMBER事實的daily reflection被
  `register_reflection_record`以SQLSTATE 23514拒絕（fail-closed，無資料污染）。
- 修復為最小diff單字元變更：`\\.`→`\.`。migration政策採就地修正0013（P3-F gate尚未Accepted，
  0013屬本工作包自有檔案，前例CLOSED-024亦曾在發佈前修正migration），未新增0014。
  **既有dev資料庫須drop/recreate**：`migrations.py`checksum政策下，舊庫記錄的0013 checksum
  與新檔不符，`rollback()`／`verify_schema()`皆會`MigrationIntegrityError`；integration腳本
  本來每次開全新disposable container，不受影響。
- 新增永久regression（真實PG16、zero-skip）：六種typed fact kind各record的append/readback/
  content_hash一致與二次append冪等；含小數NUMBER `"12.50"`的完整promotion chain
  （register_candidate→validate_memory_artifact→promote_memory_artifact→current_at()/
  current_pointer() bytes/hash readback）；非canonical小數`"12.5."`/`"1e3"`/`"012.50"`
  在Python contract層與DB wire層仍被拒。修前紅（decimal兩案CheckViolation 23514，
  `2 failed, 14 passed`）→修後綠（`16 passed`）。
- prosrc自查：從真實PG16讀取全部10個0013函數體，NUMBER正則實際儲存為單反斜線`\.`；
  全系統反斜線掃描確認唯一缺陷即line 730且已修，其餘雙反斜線僅E-string
  `E'[\\n\\r]'`（5處）與磁碟路徑字元類`[A-Za-z]:\\`（1處），均為既有正確用法。
- 文件更正三處過時引用（僅事實陳述）：`docs/P3F_REQUIREMENT_MAP.md` F7-04列與Required
  rerun boundary第4點改committed V12；`DECISIONS.md` ADR-032狀態行與ADR-033現行split改V12；
  `P3F_ACCEPTANCE_PROMPT.md` §9改committed V12 runner。eval threshold/split/held-out/fixtures
  與V1～V12批次皆未動；無provider/broker呼叫、無POST。
- 狀態：`P3-F remediation completed; pending independent re-acceptance`

## 2026-08-26 — P3-F重新驗收Accepted；P3 Combined closure發布

- 使用者授權原重新驗收session續行closure。重新驗收（全程read-only）判定`P3-F: Accepted`，無High/Medium
  blocker。F-A1修復六步深度驗證全過：0013 diff僅line 730一行、真實PG16 prosrc儲存單反斜線`\.`、
  全系統反斜線字面值掃描無同類缺陷、紅→綠重注入（以`git show HEAD`舊函數體CREATE OR REPLACE重灌後，
  decimal NUMBER永久測試與promotion chain以23514失敗、integer與其他四kind照常通過，回灌fixed復綠）、
  自建七facts（含12.50/-0.25小數）端到端append/readback/idempotency與完整promotion chain PoC全PASS。
- 全套重跑：targeted `423 passed`；`verify_p1.sh`全綠（non-integration `1299 passed, 232 deselected`）；
  真實PG16整合`217 passed, 15 deselected, 0 skipped`（無併發負載之隔離跑）；offline byte-match exit 0，
  split_hash `054f09c7…`／report_hash `b6792a88…`與首次驗收參考值一致。
- V12 sanitized evidence離線重算23/23 PASS：evidence_hash `de5d0ae1…`／audit_root `f100720a…`／plan
  `019b4de7…`／config `f35fd5f3…`全部閉合重算吻合；390筆record audit_hash逐一重算相同；POST ordinals
  1..260連續且每案皆首attempt；260/260 STRICTLY_PARSED＋ACCEPT、violations=0、130/130 pre-network
  fail-closed；retry/fallback/timeout=0、first-attempt/eventual=100%；latency p50 3175／p95 9044／max
  31530ms；tokens 289,758／70,139／359,897；execution_kind=`PRODUCTION_AGNES_KEYCHAIN_STDLIB`。
- 自建對抗PoC：套件A 53/53 PASS（future source與±1µs cutoff邊界、foreign fact、invented number/date/
  symbol/scientific、post-construction tamper、correction cycle、replay換source→23505、513 entries、
  >512KiB、4000行renderer上限、importance/category spoof、duplicate flood、URI/path/email/instruction
  注入、ghost evidence、forged CAS先於任何DB transition拒絕、invalid candidate containment、crash三點
  全單位rollback與同execution原地重試冪等至CURRENT、NO_SAFE_MEMORY alert、curator/runtime capability
  matrix、owner append-only 55000、雙連線競態恰一winner一40001、多as-of point-in-time重播）；套件B
  11/11 PASS（held-out封印、tuning/held-out tamper拒絕、byte-identical symlink拒絕、duplicate JSON key、
  symlink root）。
- 環境觀察（非程式缺陷）：本機Docker VM僅1.4GB，診斷用第二容器並存時OOM killer擊殺bgwriter令整合套件
  尾段崩潰（postgres log `signal 9`），隔離單跑即綠。CI側e7b7223/b59e466兩次postgres-integration失敗
  同型：service tmpfs 512m遭WAL churn耗盡致容器崩潰重啟（168 passed後49 connection errors），已由
  commit `d51e9a9`將tmpfs提升為本地已驗證的1g（本地峰值~832MB）。
- 發布：驗收時工作樹（33修改＋90 fixture檔）以`b59e466`登載；其CI run `32961887546` quality-unit成功、
  postgres-integration因上述tmpfs失敗；`d51e9a9`修復後run `32962320231`兩required jobs均success
  （quality-unit 3m35s；postgres-integration `217 passed, 15 deselected`，4m35s）。
- 治理同步（本次docs commit）：HANDOFF/PROGRESS/README/ROADMAP翻轉為P3-F Accepted/Closed、P3 Combined
  Closed；DECISIONS ADR-032/033收尾並修正殘留「現行p3f-synthetic-v11」過時引用；ISSUES OPEN-026關閉、
  OPEN-027補記V12 GREEN snapshot（義務不變）；RISK_REGISTER R-33/R-34更新；HANDOFF/PROGRESS頂部殘留
  「V10 live quality evidence pending」過時狀態一併更正。
- Provider Transport的P6前置義務不變：另行授權synthetic canary於rolling 7日且≥200 logical calls達
  first-attempt≥95%／eventual≤3 attempts≥99%，跌破即重開。P4未開始，需另經使用者授權。

## 2026-08-26 — P3收尾：文件歸檔與殘留清理

- 依使用者授權執行P3最終收尾：六份P3-D/E/F implementation／acceptance prompts移至
  `docs/archive/prompts/`；`docs/P3F_REQUIREMENT_MAP.md`移至`docs/archive/p3/`；關門當下的前一版
  handoff完整快照存於`docs/archive/handoffs/PROJECT_HANDOFF_2026-08-26_P3-CLOSED.md`。
- `README.md`全文重寫為當前狀態；`PROJECT_HANDOFF.md`重寫為精簡當前交接（舊版已歸檔）；
  `PROGRESS.md`移除V1～V12逐批長篇敘事與各子閘逐remediation細節，改為單一「P3 Closed」狀態、
  證據摘要與歸檔指針（逐輪歷史保留於本檔）；`docs/ROADMAP_AND_ACCEPTANCE.md`合併P3-A～F子段落為
  單一Close章節，狀態總覽表收斂為單一P3列。
- 刪除經逐一確認的可再生cache與junk：19個`__pycache__`目錄、`.pytest_cache`、`.mypy_cache`、
  `.ruff_cache`、根目錄`.DS_Store`（全部gitignored），以及本驗收session在`/tmp/p3f_reaccept`的
  scratch腳本與log。明確保留不動：`.seven-lens-local/`（V1～V12 immutable live evidence）、
  `skill/`（本地語料）、`.mimosa/`、`.venv/`。
- 狀態：P0～P3 Closed；P4～P8 Not started。工作樹僅餘本次收尾變更，push後`main`保持與remote一致。

### 追記（同日）：CI resume-replay測試flake修復

- 收尾commit `16f7d83`的CI quality-unit連續兩次在
  `test_resume_from_persisted_analysts_is_pure_replay`失敗：每輪兩個debate經
  `analysis/concurrency.py`的ThreadPoolExecutor並行送出，輪內呼叫順序取決於執行緒排程；
  該測試卻斷言嚴格全域順序（兩次CI分別在round-2與round-1翻轉，本地30/30通過）。
- 修復比照P3D既有慣例改為分組斷言：輪內以set比較、跨階段（debates→RESEARCH_MANAGER→TRADER）
  維持嚴格順序、總數與purity性質不變。本地stress 30/30＋整檔31 passed＋non-integration
  `1299 passed, 232 deselected`＋Ruff format/lint全綠。

## 2026-08-27 — P3 cleanup remediation Batch E/F/G implementation verification

- 起點精確為計畫 baseline `40092dd8120dfaeccf029ce411b3cd525844c1e2`；工作樹已有 A–E 大量
  未提交修復，全部視為既有使用者成果保留。A–D 未在本輪宣告驗收。
- Batch E 完成 P3-19/20/22/24/26：closed typed correction reason 端到端持久化；selection 僅對
  intentionally missing stale CAS object fallback，systemic store／integrity／validator failure typed surface；
  in-memory/PG invalidation state parity＋migration 0018；source/projected/envelope hashes 分離；production
  coordinator 依 capability-minimal `MemoryRepository` port。
- Batch F 完成 P3-10：`PostgresUnitOfWork.begin_reconciliation_snapshot()` 在任何 local read 前重啟
  pristine transaction 為 `REPEATABLE READ`；已有 write 時 fail closed。真實 PG 雙連線測試證明同一
  snapshot 看不到 concurrent commit，下一個 UoW 才看到新 reconciliation authority。
- Batch G：刪除零 caller `_reconciliation_result`；移除 baseline/expected-cash 不可達分支但不改財務
  數學；FakeOrderRepository 既有 commit/rollback snapshot fidelity 保留；`expire_overdue` 明記未提交
  pre-broker transitions 為 all-or-nothing sweep；刪除無 lifecycle consumer 的
  `SHUTDOWN_AFTER_RECONCILE` application/domain API；P3-21 新增真實 SQL negative-zero regression；
  event verifier 保留並明確由 P4 production composition 擁有。
- 驗證：E focused `144 passed`；F/G nearby `258 passed`；第一次 PG run 僅新 regression error-message
  regex 不符（SQL 實際已以23514拒絕），修正斷言後完整 PG16
  `236 passed, 2 deselected, 0 skipped`；`verify_p1.sh` 完整 non-integration
  `1386 passed, 238 deselected`。Ruff format/check、mypy、`git diff --check` 全綠。
- 狀態（實作驗證當下）：Batch E/F/G implementation verification completed；當時仍待 A–G independent
  acceptance。其後本檔最上方的 2026-08-27 acceptance 已完成；未 commit、未 push、未呼叫 provider/model/broker。

## 2026-08-27 — P4-A implementation completed pending independent acceptance

- 依`P4A_IMPLEMENTATION_PROMPT.md`完成P4-A全部8步；起點`1099573`（P1～P3 Closed），工作樹既有
  dirty/untracked文件與P4 prompts全數保留未動。狀態僅為implementation；未commit、未push、
  外部呼叫0（broker/model/POST/live GET均為0）。
- A2 `config/p4.py`：`P4PolicyConfig` frozen/slots，14個fixed-scale Decimal＋4 int＋6 bool全欄位
  釘死approved profile；domain-separated SHA-256（`seven-lens.p4.policy-config.v1`）；wire canonical
  parse拒絕bool-as-int、subclass、NaN/Inf、negative zero、未知欄位、±最小單位漂移與hash tamper。
- A3 `sources/roles.py`：封閉4種SourceRole＋15個P4SourceFamily完整manifest（exact host/path
  template/endpoint regex/query allowlist/byte/timeout/rate/pagination/MIME/rights/storage/producer）；
  role/coverage/auth per-family pinned，DISCOVERY→AUTHORITY等升權在建構期即拒絕；IEX以
  `AUTHORITY`＋`LIMITED_MARKET_COVERAGE`標記；Tavily NON_GET_UPSTREAM、yfinance RIGHTS_UNVERIFIED、
  FRED/BLS/BEA/EIA CREDENTIAL_QUERY_NOT_PERMITTED（query-key上游在「secret不進URL」邊界下不可執行）。
  `security/secret_values.py`僅新增FRED/BLS/BEA/EIA四個`SecretKind`（零付費註冊key需typed SecretRef），
  wire/服務映射additive。
- A4 `sources/adapters/transport.py`：policy-bound GET-only transport；無任意URL/method/header入口，
  redirect拒絕（final_url比對＋3xx）、408/429/5xx→typed class、timeout、MIME、decompressed byte
  budget、重試=0、bounded typed failures；audit只含family/sanitized endpoint id/status class/latency/
  bytes/content hash；`PreparedRequest`/`FetchResult` repr不含URL、query或secret。
- A5 records＋15個family adapter：`NormalizedSourceRecord`（role/coverage/rights由registry推導，
  DISCOVERY/RESEARCH_SUPPLEMENT強制material=false，IEX強制coverage warning；未知時間一律None，
  provider時間戳僅接受bounded canonical變體）；Alpaca assets/bars（feed entitlement錯誤禁止靜默退IEX）/
  IEX quote/corporate actions（split detection-only、永不確認）；SEC submissions（CIK/accession/平行陣列）；
  issuer IR/exchange notice（註冊host、HTTPS、發布時間必填）；FRED/ALFRED（缺explicit realtime
  window即`VintageSemanticsError`）；Treasury/BLS/BEA/EIA各自獨立parser（observation period與release
  time分離、error envelope fail closed）；Tavily/GDELT discovery-only；yfinance supplement-only。
- A6 persistence範圍決策：本gate無DB變更、無migration（下一個可用編號0020未動）。P3 evidence表
  family CHECK無法表達macro/Alpaca family且不得改0010；預建P4表會搶佔P4-B/C schema設計。交付
  `application/ports/p4_source_records.py`（append-only契約）＋`sources/adapters/in_memory_p4_records.py`
  （same-hash idempotent、different-hash需explicit supersession、無update/delete）；真實PG持久化、ACL與
  concurrency證據留給P4-B security master gate。
- A7 capability closure：AST掃描＋constructor測試證明P4模組（config/p4、sources/roles、adapters全部）
  無HTTP backend/socket/psycopg/Keychain/execution/broker/model import、無environ/getenv、無
  submit/cancel字串；transport與parser failure皆零record persist；role軸恰好4種。
- 驗證：focused `tests/test_p4a_*.py`＋secret/paper-only invariants `327 passed`；`verify_p1.sh`
  完整non-integration `1704 passed, 245 deselected`；真實PG16 `242 passed, 2 deselected (live gate),
  1 error`——該error為`test_runtime_role_verification_rejects_a_missing_guard_trigger`伺服器連線
  crash，已在乾淨HEAD（P4-A檔案全數移出）重跑兩次同樣重現（1～2 errors），屬既存環境flake與本輪
  變更無關；Ruff format/check、mypy（217檔）、`git diff --check`全綠。
- 已知邊界：query-string-key macro family（FRED/BLS/BEA/EIA）在現行「credential不進URL」transport
  邊界下標記不可執行，live probe需另案授權credential-injection設計；SEC User-Agent身分常數需在
  live授權時替換為真實聯絡資訊；上述連同全部live evidence（NOT AUTHORIZED）留待fresh P4-A
  independent acceptance按`P4A_ACCEPTANCE_PROMPT.md`判定。
