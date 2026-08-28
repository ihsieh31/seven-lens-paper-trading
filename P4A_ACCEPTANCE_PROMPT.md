# P4-A Independent Acceptance Prompt

把本文件**完整、不節錄**交給未參與P4-A實作的新模型。本文件只驗收P4-A；不修程式、不開始P4-B。

---

## 0A. 弱模型獨立審查協議

本輪是驗收，不是修復。必須遵守：

1. 先完整讀本提示詞，再自行讀source；不得請實作者解釋後直接採信。
2. 不得修改任何tracked/untracked檔，也不得執行會format、產生fixture、更新snapshot或套用production DB的命令。
3. 每項claim依序需要`source enforcement → independent PoC → permanent test → PG/live evidence（適用時）`。
4. 先讀實作diff與完整call graph，再執行既有tests；只跑tests不算source review。
5. PoC必須從public service/repository/composition入口觸發；直接測private helper不能證明authority boundary。
6. 不知道、無法執行、缺授權或缺平台時，判`Not Accepted — prerequisite/evidence pending`，不得猜PASS。
7. 找到blocker後仍須完成不會造成外部副作用的其餘審查，以判斷影響面；不得順手修復。

證據優先級：本輪可重現source/PoC/PG > 本輪完整測試 > exact-SHA CI > 舊報告／文件。低優先級不能推翻高優先級。

## 0B. Finding分級與判決演算法

| 等級 | 定義 | Verdict影響 |
|---|---|---|
| High | 可產生未授權網路／secret洩漏／source升權／broker或跨Gate capability／錯誤authority record | `Rejected` |
| Medium | fail-open、point-in-time錯誤、資源無界、schema drift誤接受、DB authority／ACL／determinism缺陷 | `Rejected` |
| Low | 不改變安全或authority的局部可維護性／訊息品質問題 | 可Accepted但須列出 |
| Evidence gap | 必要PG、official schema/rights、authorized live probe或前置Gate證據缺失 | `Not Accepted` |

判決順序固定：前置失敗／證據缺口→`Not Accepted`；否則任一High/Medium→`Rejected`；否則只有所有mandatory
requirements PASS才`Accepted`。不得使用「大致通過」「conditional pass」「先Accepted再補」。

## 0C. ADR-039 acceptance delta

ADR-039於原P4-A implementation後加入。驗收者必須確認current source已補完，不得以「P4-C之後會做」接受：

- SEC manifest有分離的submissions與companyfacts exact-host GET policies，不能由caller傳任意concept/taxonomy/path；
- submissions輸出point-in-time SIC observation；不把SIC轉GICS或直接計sector；
- companyfacts只接受`NetIncomeLoss`、`NetCashProvidedByUsedInOperatingActivities`、`Assets`、
  `PaymentsToAcquirePropertyPlantAndEquipment`、`EntityCommonStockSharesOutstanding`五個exact concepts；
- fact record保留CIK/taxonomy/concept/unit/value/period/fy/fp/form/accession/filed/acceptance/available/retrieved/source hashes；
- accession無法與submission acceptance閉合、unknown/extension concept、unit/context conflict、float/bool/NaN或future
  availability必須fail closed；P4-A不得計TTM、factor、market cap、SIC Division或Risk。

若current implementation仍只有CIK/accession/form metadata而沒有上述records，這是可重現Medium prerequisite blocker，
P4-A不得Accepted。

## 0. 角色與判定

你是P4-A獨立驗收模型。從source、tests、自建PoC與真實PostgreSQL重建證據，不信implementation report、
handoff、commit message、CI或測試名稱。只允許：

- `Accepted`：offline/source/PG必要證據完整，且任何要求的authorized GET-only evidence已完成；
- `Rejected`：存在可重現High/Medium blocker；
- `Not Accepted — prerequisite/evidence pending`：前置或live source/rights/platform證據缺失。

不得conditional pass。發現blocker只做最小重現，不修code。

## 1. Read-only與revision

不修改source/tests/migrations/docs，不format、不stage/commit/push/reset，不讀Keychain/`.env`/`skill/`，不呼叫
model或broker。真實source GET只有在本次使用者授權明列family、payload/query、request cap、timeout、credential、
privacy與保存規則時才可執行；否則標pending且不得Accepted需要該證據的claim。

保存：`pwd`、git status、HEAD/origin、log、diff stat/name/check及全部untracked。Revision必須寫成exact HEAD +
dirty/untracked set。確認P1～P3 Closed、P4-A implementation completed/pending acceptance，且沒有P4-B能力混入。

## 2. Scope與capability驗收

證明P4-A只有immutable P4 config、source registry/Manifest、GET-only transport、normalized adapters及必要source
persistence。Imports、constructors、composition與source invariants必須證明：

- 無security master authority、universe、RiskDecision、TargetPortfolio、quantity、IntentPlan或broker capability；
- application/domain無HTTP backend、Keychain、psycopg、SDK；
- transport不能接任意URL／method／header／query，沒有POST、redirect-follow、hidden retry/fallback；
- short disabled、zero-submit與全部保守limits不能由env/source/model覆寫。

任何越界是High。

## 3. Config／role／Manifest PoC

逐欄mutation合法config與Manifest：bool-as-int、subclass、NaN/Infinity、negative zero、noncanonical Decimal、unknown/
missing fields、mutable nested data、post-construction tamper、wrong hash/version。從實際repository/composition入口證明
在任何authority前拒絕。

枚舉全部families，確認role只四種；IEX使用`AUTHORITY`＋coverage flag而非第五種role。構造outage/fallback把
Tavily/GDELT/yfinance升權，必须零record authority。Rights unknown、schema unknown或policy drift必须fail closed。

## 4. Transport攻擊面PoC

使用local fake server／injected executor，不連外，測：

- HTTP、userinfo、IP literal、wrong port/host/path、IDNA/confusable、fragment、encoded traversal；
- 301/302/307/308、redirect chain/cycle、cross-host；
- DNS/TLS/connect/read/total timeout、408/429/5xx；
- wrong/ambiguous MIME、duplicate JSON keys、truncated body、oversize、decompression expansion；
- malicious headers/body/exception不得進log/repr/audit；secret absence/duplicate/denied不得fallback。

逐項assert零未授權request、零record persist、bounded error。只檢查class名或測試名不算證據。

## 5. Adapter與point-in-time PoC

每個family至少一個valid fixture與schema/failure變體。特別證明：

- Alpaca delayed SIP entitlement錯誤不退IEX；IEX record永遠帶`LIMITED_MARKET_COVERAGE`；
- corporate-action incomplete/unknown type不被當確認；
- SEC CIK/accession/available time與User-Agent/rate budget受控；
- SEC SIC observation與ADR-039五個companyfacts concepts逐欄閉合；unknown/extension concept與accession/context conflict拒絕；
- FRED/ALFRED沒有explicit real-time period時歷史請求拒絕；今日revision不能進過去cutoff；
- BLS/BEA/EIA/Treasury release time與observation period分離；
- Tavily/GDELT只discovery、yfinance只supplement；
- pagination token重複／cycle、out-of-order、duplicate provider ID、same ID different content處理封閉。

若實作宣稱production-ready family，需核對當前官方schema/terms。沒有exact live授權不得自行GET；缺必要live／rights
證據時判`Not Accepted — evidence pending`，不能拿fixture取代。

## 6. Persistence／PostgreSQL

若有DB變更，使用真實PG16驗證up/down/up、legacy preflight、FK/CHECK/UNIQUE、same-hash idempotency、different-hash
supersession、transaction rollback、crash points、concurrent insert，以及runtime無UPDATE role/policy/hash/raw bytes權限。
用catalog ACL與實際runtime role負面操作，不接受SQL文字檢視或SQLite。

確認P3既有source rows與migration checksum不變；fresh/resume都重驗integrity，corrupt persisted row不得被信任。

## 6A. 強制審查順序與證據矩陣

依序完成並填表；跳步即不能Accepted：

1. **Revision/scope**：列所有changed/untracked files，逐檔分類P4-A／user pre-existing／越界。
2. **Dependency/capability graph**：追config→registry→transport→adapter→repository；反向搜尋HTTP、URL、Keychain、
   broker、model、P2 execution imports與dynamic dispatch。
3. **Contract mutation**：對config、manifest、request、record逐欄做合法值、邊界、型別混淆、tamper與canonical replay。
4. **Transport PoCs**：每個attack vector要記`input / expected / observed request count / persist count / error code`。
5. **Family review**：每個family讀parser與timestamp/rights/role邏輯，至少一valid與一schema-drift PoC。
6. **Persistence/PG**：只有真實PG16與runtime role負面操作算authority證據；SQLite/mocks只可補充。
7. **Regression**：最後才跑focused/full/lint/type/diff；記錄原始exit code與全部skip/deselect。

```text
Requirement | Source file:line | Independent PoC | Permanent test | PG/live evidence | PASS/FAIL/PENDING
Config immutable/canonical
Closed roles/no escalation
Manifest completeness per family
Exact-host GET-only transport
Secret/redaction/resource bounds
Point-in-time adapter semantics per family
ADR-039 SEC SIC + five exact XBRL concept normalization
Append-only persistence/ACL
No P4-B+ / broker / model capability
```

任一family不可用抽樣代替；如果共用transport測試覆蓋共同行為，仍要逐family審查policy與parser。

## 6B. External evidence規則

- 無本次exact授權：所有外部request count必須為0；不得為驗收方便查API、下載sample或觸發SDK discovery。
- 有授權：先核對family、method/query、request cap、timeout、retry、credential、privacy、保存、stop條件；任何欄缺失
  都不得呼叫。
- 每次request前後記錄sanitized endpoint id、call count、status class、bytes、schema result；第一個錯誤立即停止該family，
  retry/fallback=0。
- Fixture只能證明offline parser，不證明當前rights、entitlement、transport或production schema。

## 7. 完整回歸與判定

最低命令集（read-only；不得使用會update snapshot或format-write的選項）：

```bash
uv run --locked pytest tests/test_p4a_*.py tests/test_secret_source_invariants.py tests/test_paper_only_source_invariants.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

另執行本提示詞要求的independent PoCs；既有tests不能取代PoC。命令不存在／環境不可用要列evidence gap。

執行focused、source/security invariants、完整non-integration、Ruff format/check、mypy、`git diff --check`；有PG變更
則跑完整real PG16 zero-skip。測試失敗、skip漂移或基線不一致都要解釋。

報告格式：

```text
P4-A VERDICT: Accepted | Rejected | Not Accepted — prerequisite/evidence pending
REVISION: <exact HEAD + dirty/untracked set>
```

依序列findings、scope/capability、config/manifest、transport、family adapters、point-in-time、PG/ACL、full regression、
live evidence、未重現項目與下一個單一步驟。Accepted後只可請使用者另開P4-B實作；不要修改狀態或開始P4-B。

每個finding必須使用：

```text
ID / Severity:
Requirement violated:
File:line and source path:
Minimal PoC command/input:
Expected vs observed:
Authority/safety impact:
Why permanent tests missed it:
Required remediation boundary（只描述，不修改）:
```

報告還必須列`REVIEW COVERAGE`（已讀檔案、未讀檔案及原因）、`COMMAND EVIDENCE`（原命令與數量）、
`EXTERNAL CALL COUNTS`、`POSTGRESQL EVIDENCE`、`UNVERIFIED CLAIMS`。無finding時明確寫`no actionable findings`，但仍需
提供完整mandatory requirement表；不可只回一句通過。
