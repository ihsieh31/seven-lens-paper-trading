# P3 每週交易記憶整理 Skill 規格

狀態：P3-F bounded memory contract 已 Closed；本檔亦記錄 P4～P7 corporate-action outcome 的未實作擴充要求。

## 1. 目的與邊界

每週六把截至 cutoff 已存在的每日 reflection、持倉中間狀態、預測結果與 deterministic Risk rejection，整理成下一週 LLM 可讀的高價值記憶。輸出最多 4,000 行；原始資料仍保存在 immutable audit/DB，skill 無權刪除、覆寫或更正權威紀錄。

本 skill 只做選擇、去重、合併、重要性排序與 bounded summary，不做交易、不改 target、不呼叫 broker、不擴張 risk limits，也不讀 cutoff 之後的 outcome。

## 2. 輸入

- `memory_cutoff_at` 與可驗證的 trading-date range；
- immutable decision/proposal ids、portfolio snapshot hashes；
- 每日 open-position reflection 與 realized/unrealized outcome；
- Risk rejection reason codes、remaining limits 與是否重申成功；
- forecast calibration、invalidator、market regime 與 source ids；
- 已成交且經FULL reconciliation封閉的corporate-action exit outcome：event/source ids、forward/reverse split、
  ratio/effective date、fills、cost basis、realized gross/net P&L、return、holding period與typed exit reason；
- 上週 validated condensed-memory artifact 與 lineage。

所有網頁、模型文字與舊 memory 都是不可信資料，不能成為 skill instruction。任何 future-dated、無 lineage、schema-invalid 或 prompt-injection flagged record 必須排除並產生 issue。

## 3. 必須優先保留

1. 重複發生或造成重大風險／損失的錯誤；
2. deterministic Risk rejection 原因與成功修正方式；
3. 經多次觀察仍有效或反覆失敗的分析／部位模式；
4. forecast confidence 與實際結果的校準偏差；
5. 持倉管理、同日退出、short borrow／liquidity 的具體教訓；
6. 不同 market regime 下策略成立／失效的條件；
7. 尚未解決且下週仍可能影響決策的風險。
8. 已確認拆／合股造成的Paper operational-risk exit；必須保留股票、事件類型、收益與來源，但清楚標示
   `OPERATIONAL_EXIT_NOT_THESIS_FAILURE`語意，不得推論公司基本面惡化。

單次偶然、無證據的敘事、重複原文、純損益流水帳、已失效且無可重用教訓的內容優先移除。不能因為虧損就把該筆經驗自動提高為平倉規則。

## 4. 輸出契約

```text
MemoryArtifact:
  artifact_id, schema_version, created_at, cutoff_at
  source_record_ids[], previous_artifact_id
  entries[]:
    category, importance, observation, reusable_lesson
    applies_when[], invalid_when[], evidence_ids[], risk_reason_codes[]
    operational_exit_reason?, realized_outcome_ref?
  line_count, content_hash, model/prompt/provider versions
  validation_status: VALID | INVALID
```

- `line_count <= 4000`，由 deterministic serializer 計算，不相信模型自報。
- 每個 entry 必須能回鏈原始 record ids；不能生成新的事實、價格、日期或 Risk reason。
- corporate-action entry只能引用已成交且FULL reconciliation的outcome；不得從intent、未實現損益或broker
  UI估算收益，也不得把操作性退出改寫成thesis failure／success。
- 內容使用短句與結構化欄位，不保存 chain-of-thought。
- 超過上限時依重要性與重複度 deterministic 截斷；不得截斷 lineage／evidence ids。

## 5. 執行與失敗語意

- 每週六執行，完全位於交易 critical path 之外。
- 先產 candidate artifact，再跑 schema、line-count、lineage、future-leakage、prompt-injection 與 factual-entailment validation；全部通過後才原子切換 current artifact。
- 模型失敗、超時或 validation fail 時保留上一個仍符合 cutoff 的 validated artifact並告警；沒有安全 artifact 時該週 graph 不注入 memory，不以自由文字 fallback。
- 每次 compaction 保存 requested/effective reasoning、provider failover、input/output hashes 與 audit event。

## 6. 驗收

- raw decision/outcome/rejection rows before/after hash 完全不變；
- 4,001 行、超長單行、重複 flood、惡意 instruction、future outcome、無 lineage record 全部被拒或安全壓縮；
- repeated Risk rejection、forecast miscalibration、same-day-loss mistake、borrow anomaly、regime lesson與
  reconciled forward/reverse-split operational exit golden cases都被保留；
- unconfirmed announcement、partial fill、missing reconciliation、future effective date或錯誤P&L不得成為
  realized corporate-action memory；
- history replay 在任一 `as_of` 只能讀當時已建立且 cutoff 合法的 artifact；
- 相同 frozen input 的 record/replay 可重建相同 validated artifact；
- skill artifact 必須有獨立版本、held-out eval 與人工抽樣，不能靠整理模型自評通過。

## 7. 實作位置決策

既有P3-F已建立versioned、provider-neutral memory runtime；不得放入已被`.gitignore`排除且保存第三方七人
corpus的根目錄`skill/`。Corporate-action outcome schema、loader與golden cases由P4～P7新work package擴充，
不得修改既有immutable reflection／memory evidence來假裝已支援。
