# P4-C Independent Acceptance Prompt

完整交給未參與實作的新模型。只驗收P4-C；read-only、不修code、不開始P4-D。

## 0A. 審查協議與判決演算法

本輪不得修改檔案、生成snapshot、連外或呼叫model/broker。先source review，再自建public-entry PoC，再跑permanent
tests與PG16。每項claim需`source:file:line + PoC + test + PG（適用時）`。

- 缺P4-A/B fresh Accepted、ADR-039 exact manifests或manifest canonical/hash evidence：`Not Accepted`。
- future leakage、錯誤eligible、非deterministic ranking、supplement升權或跨Gate capability：High/Medium，`Rejected`。
- 只有不影響authority的維護性問題可列Low且不阻擋；不得conditional pass。

特別注意：核准taxonomy只有`sec-sic-division-v1`。若code、docs或tests仍使用GICS名稱／欄位，或以SIC以外mapping支撐
25% sector limit，至少Medium。不得把classification差異當alias處理。

## 0. 判定與前置

只允許Accepted／Rejected／Not Accepted — prerequisite/evidence pending。P4-A/B必須fresh Accepted。保存exact HEAD、
origin、dirty/untracked、diff/check與migration checksum；不讀credential、不連外、不呼叫model/broker。

## 1. Scope審查

確認C只擁有market snapshots、universe、deterministic screening與persistence/jobs。不得出現RiskDecision、
TargetPortfolio、quantity、IntentPlan、broker/order、provider/model call；不得變更A/B Accepted contracts或P2/P3 authority。

## 2. Market snapshot對抗

自建合法snapshot後mutation feed/entitlement、prices、spread、ADV、timestamps、coverage、source refs/hash。驗證：

- bid/ask/spread由source重算，float/NaN/negative/bid>ask拒絕；
- quote age 5秒與spread30bps的前/等於/後邊界一致；
- IEX永遠mandatory limited warning，不能宣稱NBBO/SIP；
- delayed SIP entitlement/response錯誤不退IEX或yfinance後冒充同authority；
- future/out-of-order/duplicate/closed-market freshness錯誤零snapshot authority；
- ADV 20日、split/as-of/holiday邏輯無future leakage。

## 3. Universe adversarial replay

建立每個included/excluded asset class、價格4.99/5.00、ADV邊界、251/252日、halt、unknown tradability、identity
ambiguous、symbol reuse、ENTRY_BLOCKED/REVIEW_REQUIRED案例。確認每個ineligible reason可稽核且不能因missing data
變eligible。

將相同inputs以不同permutation、DB row order、parallel completion輸入，universe bytes/hash/order必須相同。將今日
security metadata或corporate-action correction注入historical cutoff，必須不可見。

## 4. Funnel adversarial replay

讀全部factor公式、normalization、missing/outlier policy與tie-break。自建0/1/max/max+1、完全同分、NaN、missing、
duplicate security、same symbol different identity、future/stale/conflict evidence。證明100→30→12/5上限、canonical
order與窗口deadline不受dict/thread/DB順序影響。

驗證evidence screen只看typed metadata，不呼叫模型、不相信snippet/free text score；全部持倉與新增候選不混為
同一authority。

## 4A. ADR-039獨立formula／taxonomy／cluster oracle

不得import production factor、percentile、SIC或cluster helper當oracle。至少自行建立下列小型vectors：

1. **Factor raw math**：手算126→21、252→21 returns；4 non-overlapping quarters的TTM NI/CFO/CapEx、start/end assets、
   shares×previous close；63-return population volatility與252-close max drawdown。測YTD可／不可無歧義相減、extension tag、
   unit/context conflict、negative earnings/FCF、zero denominator與future filing。
2. **Winsor/midrank**：N=1、N=20、repeated ties、5%/95% exact index、extreme outlier。獨立計算q05/q95、clamped values、
   first/last index midrank、category與0.35/0.25/0.15/0.25 composite；逐byte比較production feature vector。
3. **Ordering**：建立composite同分但各category不同，以及全部同分案例；確認composite→Trend→Quality→Value→LowRisk
   降序、stable security ID升序。以至少10種input permutations比較top100/30/12/5。
4. **Event hard gate**：無事件但資料完整應通過；discovery-only material claim、authority missing/stale/conflict、prompt
   injection與B quarantine非ELIGIBLE必須從Evidence top30移除，不得改quant score或找top100外替補。
5. **SIC**：逐一測01/09、10/14、15/17、20/39、40/49、50/51、52/59、60/67、70/89、91/97端點，以及18/19、
   68/69、90、98、99、missing、3-digit zero-pad、future/conflicting records。確認輸出A-J或SECTOR_UNKNOWN且無GICS。
6. **Cluster**：用可手算returns建立rho=0.75前／等於／後、negative correlation、connected-chain A-B-C、合法singleton、
   99/100 common observations、zero variance、non-finite與current holding不在top100。確認UNKNOWN不可被當singleton。

任何formula以float造成boundary漂移、使用sample denominator=62、absolute correlation、complete-linkage或不同SIC ranges皆
屬Medium以上；tests寫成相同錯誤不能抵銷independent oracle。

## 5. PostgreSQL／publication

真實PG16驗證parent-child completeness、single current authority、same-hash replay、different-hash conflict、partial
crash、commit前/後restart、兩連線publication race、runtime UPDATE/DELETE拒絕、migration up/down/up與legacy bad
data preflight。從corrupt persisted bytes/hash/status resume時必須fail closed。

## 5A. 強制審查流程與matrix

1. 列diff及imports，排除D～F、P2 order、provider/model/broker capability。
2. 獨立重算bid/ask/spread/ADV/calendar邊界，從assembler public入口注入變異records。
3. 建立完整universe fixture，每個hard filter至少一個include、boundary equal、exclude case。
4. 從原始point-in-time inputs獨立重算每個approved factor；比較feature、score、rank、top100/30/12/5。
5. 對同一dataset做至少10種permutation、duplicate、tie與thread/DB order；比較canonical bytes/hash。
6. 以future filing/bar/classification、late correction、symbol reuse測as-of leakage。
7. 真實PG16測partial child crash、CAS、two publishers、corrupt readback、runtime direct mutation與migration cycle。

```text
Requirement | Approved manifest/version | Source file:line | PoC expected/observed | Test | PG | Verdict
Market exactness/freshness/coverage
20-session split-aware point-in-time ADV
Asset/price/liquidity/history hard filters
Security/quarantine closure
SEC SIC Division V1 exact ranges/as-of/unknown policy
Correlation V1 126/min100/Pearson0.75/connected-components
Factor V1 9 raw formulas/concepts/TTM/weights/winsor/midrank
Top100/top30/focus12/5 deterministic truncation
Holdings vs new-candidate separation
Atomic append-only publication and ACL
No model/Risk/quantity/broker capability
```

不得以「ranking看起來合理」或golden fixture alone標PASS；需獨立重算。

## 6. 完整驗證與報告

最低read-only命令集：

```bash
uv run --locked pytest tests/test_p4c_*.py tests/test_market_clock.py -ra --tb=short
./scripts/verify_p1.sh
./scripts/verify_p1.sh --postgres
git diff --check
```

另需依4A獨立重算factor/ranking/SIC/cluster與permutation PoCs；golden tests不可替代。Manifest drift不能因tests綠而Accepted。

跑focused、A/B/P3 evidence/clock regressions、完整non-integration、完整PG16 zero-skip、Ruff、format、mypy、diff check。
任何future leakage、non-determinism、silent fallback、P4-D能力或High/Medium finding都Rejected。

```text
P4-C VERDICT: Accepted | Rejected | Not Accepted — prerequisite/evidence pending
REVISION: <exact revision>
```

列findings、market evidence、universe boundary matrix、funnel permutation/tie evidence、PG/concurrency、scope與full
regression。Accepted後只建議另開P4-D；不修改檔案或狀態。

Finding需列severity、requirement、file:line、public-entry PoC、expected/observed排名或rows、authority impact與限定修復範圍。
報告另列所有reviewed files、未審claim、factor/sector/cluster來源與版本、原始命令/exit code/counts、PG server/version、
external/model/broker call counts。Matrix任一PENDING/FAIL或任一High/Medium皆不得Accepted。
