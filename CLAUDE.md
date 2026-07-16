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
- 市場時機主軸 = 200MA regime + FTD 狀態（30 年驗證）；分配日僅參考
- 所有 universe 級回測宣稱必須用點時成分（`backtest_score_v2_pit.py` 的 `load_pit_membership`）
- 本 repo 有背景 auto-commit watcher；push 前先 `git pull --rebase`
- 每次優化後收尾：compileall + node --check(index.html script) + 亂碼掃描 +
  workflow log 掃 Traceback + 存 Trading Knowledge Hub + 提出下一步優化
