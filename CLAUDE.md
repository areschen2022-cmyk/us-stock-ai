# us-stock-ai — Claude 工作約定

## AI Agent 整合手冊（2026-07-16 起）

已安裝 Anthropic 官方金融 agent（`claude-for-financial-services` marketplace），
與本系統的固定掛鉤點：

| 時機 | Agent | 用法 |
|---|---|---|
| 候補股入池前 | `market-researcher` | 對掃描候補做產業格局/競爭定位檢視，結論附進 `add_watchlist_symbol.py --reason` |
| 財報臨近的 B 級以上持倉 | `earnings-reviewer` | 財報前後審閱（風險扣分裡的「財報臨近」訊號觸發時） |
| 高分股估值疑慮 | `model-builder` | 對 v3 A/S 級但 PE 極端的個股建簡易估值模型交叉檢核 |
| 每日自動複核 | DeepSeek council（內建） | `DEEPSEEK_API_KEY`，v2 S/A＋週線up 驅動選件 |

## 關鍵系統事實

- 評分 v3（2026-07-14 起）：`總分 = v2×0.60 + (基本面+資金流+新聞)×0.80 − 風險扣分`；
  之前的歷史等級為舊公式，跨期比較要斷代
- 新聞分有 VADER 情緒閘門：負面新聞（情緒 ≤ -0.15）題材分壓到 ≤3
- 池治理：入池走 `scripts/add_watchlist_symbol.py`（強制 --reason 留痕）；
  退池候選由週一掃描產生（v2<40 連續 4 週），人工確認後移除
- **池規模政策（2026-07-22 起）**：目標區間 60-90 檔，經每週治理式吸納達成。
  入池資格＝廣域掃描 S 級＋週線向上＋AI 複核非 Avoid＋財報 ≥7 天；
  單一產業每週最多吸納 4 檔（生技 binary 風險）；被財報排除者次週自動重審。
  超過 ~120 檔前需重估 CI 執行時間與 yfinance 配額
- **出場規則（2026-07-28 起）**：主要出場 = 收盤跌破 MA20（月底對照裁決，
  叢集 t 不顯著，主要靠方向一致性與 10 年回測支撐）；2×ATR 降為災難停損併列顯示。`stop_price` 欄位語意不變，
  仍供 shadow 停損驗證迴路使用，勿重新定義
- **統計解讀鐵則（2026-07-31 起）**：本系統每天對同一批股票重發訊號，同一檔多筆
  訊號不是獨立樣本。**任何 t 值一律引用 `t_clustered`（一檔一票）**，`monthend_review.py`
  已同時輸出兩者。實例：出場決策 t 0.96 被灌水成 6.63、live_top 對 MTUM 0.85→4.06。
  報告數字務必附上 `n_symbols`，只寫 n 會誤導
- 市場時機主軸 = 200MA regime + FTD 狀態（30 年驗證）；分配日僅參考
- 所有 universe 級回測宣稱必須用點時成分（`backtest_score_v2_pit.py` 的 `load_pit_membership`）
- 本 repo 有背景 auto-commit watcher；push 前先 `git pull --rebase`
- 每次優化後收尾：compileall + node --check(index.html script) + 亂碼掃描 +
  workflow log 掃 Traceback + 存 Trading Knowledge Hub + 提出下一步優化
