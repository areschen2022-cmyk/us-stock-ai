# 美股量化交易系統研究報告

日期：2026-07-13 ｜ 範圍：①知識庫回測成果的美股移植性 ②開源量化系統/框架/資料源 ③可用 agent skills

---

## 第一部分：知識庫回測成果 → 美股移植性評估

知識庫現有 524 筆 KP（backtest_supported 60 筆）。掃描 6/20 後更新的研究，按移植性分三類：

### A. 可直接移植（概念與美股日線系統相容）

| 來源 KP | 內容 | 美股落地方式 |
|---|---|---|
| `kp_tw_entry_trigger_protection`（台股, conf 0.82） | 「進場條件未觸發」的訊號 5 日報酬反達 +10.3%（勝率 76.5%）→ 開盤進場條件有過濾追高功能 | us-stock-ai 目前只評分、無進場觸發條件。可加「延伸度檢查」：距 pivot/50MA 過遠不追，等拉回觸發 — 對應 v2 回測發現 S 級集中強動能年、追高年份（2025）虧損 |
| `kp_tw_failure_pattern_summary`（conf 0.88） | 三大失敗歸因（題材失靈 -8.6%/進場後轉弱 -9.7%/停損觸發）→ 各自對應規則調整 | **移植整個歸因迴路機制**：us-stock-ai 的 forward_tracker 只記報酬、無失敗歸因分類。加上後可自動累積「美股版失敗模式」並回饋規則 |
| `kp_844e1c97cc3729` 強勢等拉回 | 66 樣本正報酬，保留潛力雷達條件 | 驗證了 potential_radar 的 early_strength 分級概念，美股版已有同構（radar），可放心保留 |
| `ares_kp_20eb990b526239` D1/H4 偏向過濾（XAUUSD, conf 0.81） | 高時間框架方向只做過濾器、不做獨立進場訊號（+17% 利潤改善，3/3 期為正） | 美股對應：**週線趨勢過濾日線訊號**。v2 評分可加週線 regime 檢查（SPY 週線 + 個股週線 MA 斜率） |
| `kp_backtest_walk_forward` / `kp_backtest_minimum_sample` | WF 至少 4/5 期正收益；30 筆最低樣本 | 已在 v2 回測遵循，持續作為美股升級 KP 的門檻 |

### B. 方法論可移植（機制照搬、參數重驗）

| 來源 | 方法論 | 美股應用 |
|---|---|---|
| `ares_kp_30cf0ffa5586c2` 策略角色路由（conf 0.8） | 按 regime 分派策略角色（強趨勢→回調、壓縮後→突破、盤整→反轉），而非堆疊指標 | us-stock-ai 已有雛形：v2 評分（趨勢）＋ potential_radar 的 low_base（壓縮）。可正式化為路由：多頭 regime 用 S/A 級動能單、壓縮期監控 VCP 突破 |
| `ares_kp_f85cb1c84f5eb2` 出場 RR 平台（conf 0.82） | 加大 ATR 目標改善總報酬，但 RR>5 無法修復弱勢期 → 出場搜索要設停止點 | us-stock-ai 出場端幾乎未開發（只有 2×ATR 停損）。**「先修出場、再改進場」**（`ares_kp_167026341a06c7` 同一結論）是下一個回測題目 |
| `ares_kp_60422352ffa40e` regime 閘門研究（pending） | 「前日過熱不進場」閘門（PF 1.347，5/8 年為正）；proxy 閘門易過擬合需雙重驗證 | 美股對應：大漲後隔日不追（gap-up 過熱過濾）。注意它還在 pending_validation，只搬方法不搬結論 |
| `ares_kp_c13a9309f52628` 三袖組合（Rejected） | 等風險多策略組合 OOS PF<1 被拒 — **拒絕紀錄也是知識** | 美股若做多策略組合（動能+殖利率+反轉），先用同一套 OOS PF(R)>1 + 相關性檢查框架把關 |

### C. 不可移植

- 倫敦/亞洲時段規則、M5/M15 微結構、點差成本模型（美股日線系統無對應結構）
- 台股題材分類（AI伺服器/CoWoS 等標籤體系與美股 theme_detector 已有不同實作）

---

## 第二部分：開源美股量化系統研究（2026 現況）

### 2.1 回測框架

| 框架 | 定位 | 適配 us-stock-ai 的評估 |
|---|---|---|
| **vectorbt** | 向量化、極速參數掃描 | ✅ 已有 `vectorbt-expert` 技能。適合因子掃描/參數平台搜索；免費版夠用（Pro $499 分裂社群） |
| **zipline-reloaded** | Pipeline API 是最「因子原生」的橫斷面框架，point-in-time 處理最嚴謹 | ◎ 與我們的橫斷面評分回測同構 — 若要擴 universe 到全市場，值得遷移 |
| **backtrader** | 事件驅動經典，但 2021 起停止維護 | ✗ 不建議新投入 |
| **NautilusTrader** | Rust 核心事件驅動、微結構精確 | △ 過重 — 我們是日線選股不是高頻執行 |
| **PyBroker** | ML 導向 + walk-forward 紀律內建 | ○ 若評分進化為 ML 模型（feature→model→signal）時的首選 |
| **QuantConnect LEAN** | 開源引擎+雲平台、資料庫完整 | ○ 免費雲回測可當獨立驗證管道（交叉驗證我們自建回測） |
| **Microsoft Qlib + RD-Agent** | AI 量化全流程；PIT 資料庫防洩漏；RD-Agent 用 LLM 自動挖因子 | ◎ 中期最值得研究：其 Alpha158 因子庫與 PIT 結構可對照改進我們的評分因子；RD-Agent 與知識庫→假說→回測迴路理念一致 |

**建議**：短期維持自建 walk-forward（已驗證可用）＋ vectorbt 做參數掃描；中期用 zipline-reloaded Pipeline 擴 universe；Qlib 作因子研究參照。

### 2.2 資料源（回測歷史 + 每日更新）

| 來源 | 免費額度 | 用途 |
|---|---|---|
| yfinance | 免費（非官方，會斷） | 現行方案，短期續用但需備援 |
| **Tiingo** | 免費日線；Starter $10/mo | 量化研究定位，回測級歷史資料的低成本備援 |
| **FMP** | 250 req/day 免費 | tradermonty 技能生態的主要依賴；基本面/財報行事曆 |
| **Alpaca** | 免費即時 IEX + 交易 API | 若走向自動下單/紙上交易的一站式選擇 |
| EODHD | 付費為主 | 全球覆蓋、長歷史，升級選項 |
| Polygon | 無免費層（$99/mo 起） | 暫不需要 |
| Finnhub | 60 calls/min 免費 | 即時報價備援 |

### 2.3 執行券商 API（若走向自動化下單）

- **Alpaca**：開發者友善、免佣、無限紙上交易、REST/WebSocket，美股演算法交易首選入門
- **IBKR TWS API**：功能最完整（150+ 訂單型別、全球市場）但複雜度高
- 建議路徑：先 Alpaca 紙上交易接 us-stock-ai 訊號（影子下單），驗證後再考慮實盤

### 2.4 研究平台與選股器

- **OpenBB**（40k+ stars）：開放資料平台 + MCP server 給 AI agent — 與 Claude 工作流原生相容，可作資料/篩選補充層
- 開源 Minervini 選股器：`RyanJHamby/stock-screener`（**us_market.py 的 4 階段概念已借鑑此庫**）、`xang1234/stock-screener`（80+ 濾網+寬度指標）、`starboi-63/growth-stock-screener`（O'Neil RS rating 實作）— 後兩者的 RS 計算與 StockBee 寬度指標值得對照
- 分析庫：`alphalens-reloaded`（因子 IC 分析 — 可取代我們手寫的 IC 統計）、`quantstats`（績效報告）

---

## 第三部分：Agent Skills 盤點

### 已安裝（現成可用，屬 tradermonty 生態）

uptrend-analyzer、exposure-coach、macro-regime-detector、market-regimes、market-environment-analysis、market-news-analyst、scenario-analyzer、position-sizer、risk-management、entry-signals、technical-analyst、us-stock-analysis、backtest-expert、quantitative-research、vectorbt-expert、backtesting-py-oracle、trader-memory-core、signal-postmortem、edge-pipeline 全家桶（candidate/hint/concept/designer/reviewer/aggregator/orchestrator）、strategy-pivot-designer、strategy-compare、trading-wisdom、sharpe-ratio-non-iid-corrections、adaptive-wfo-epoch、evolutionary-metric-ranking

### 建議補裝（同生態的缺口，來源 tradermonty/claude-trading-skills）

| 技能 | 價值 | 依賴 |
|---|---|---|
| **VCP Screener** | Minervini VCP 掃描 S&P 500 — 直接補 potential_radar 的驗證視角 | FMP 免費層 |
| **CANSLIM Screener** | O'Neil 成長股法 — 與動能評分互補的基本面維度 | FMP |
| **IBD Distribution Day Monitor + FTD Detector** | 大盤頂部/底部確認訊號 — 補強 regime 閘門（目前只有 SPY 200MA+廣度） | FMP |
| **Market Breadth Analyzer / Market Top Detector** | 免 API 的寬度健康度量化 | 公開 CSV |
| **Theme Detector / Sector Analyst / Institutional Flow Tracker** | edge-signal-aggregator 宣告的上游來源，裝齊才能發揮聚合器 | FINVIZ 選配/FMP |
| **Drawdown Circuit Breaker / Pre-Trade Discipline Gate** | 帳戶級風控閘門，接 trader-memory-core | 本地 |

### 其他開源技能倉庫（次優先）

- `agiprolabs/claude-trading-skills`：67 技能但偏 DeFi/加密，美股適配度低
- `JoelLewis/finance_skills`：81 技能偏機構合規/投顧流程，非量化
- `shakeebshaan/claude-code-quant-skills`：量化研究工作流 hooks/commands，可挑選參考

---

## 第四部分：對 us-stock-ai 的導入路線圖

**P0（立即，零成本）**
1. 移植台股失敗歸因迴路到 forward_tracker（A 類移植的最高價值項）
2. 加「進場觸發/延伸度」條件（距 pivot 過遠不追）— 台股已驗證保護效果
3. 補裝免 API 技能：Market Breadth Analyzer、Market Top Detector、Drawdown Circuit Breaker

**P1（1-2 週）**
4. 週線方向過濾器回測（複用 backtest_score_v2.py 框架，對應 XAUUSD D1/H4 結論）
5. 出場優化回測：「先修出場再改進場」— ATR 目標/移動停損/時間停損掃描，設 RR 搜索停止點
6. 申請 FMP 免費 key，裝 VCP/CANSLIM/Distribution Day/FTD 四技能

**P2（1-2 月）**
7. alphalens-reloaded 取代手寫 IC 分析；quantstats 出績效報告
8. zipline-reloaded Pipeline 把 universe 從 40 檔擴到全市場（消除倖存者偏誤 — v2 回測已聲明的最大偏誤）
9. Alpaca 紙上交易接影子訊號（自動化下單前置驗證）
10. 研究 Qlib Alpha158 因子庫對照改進評分因子

---

## 主要資料來源

- 回測框架：python.financial、autotradelab.com、hasanjaved.me/blog、quantstart.com
- Qlib/RD-Agent：github.com/microsoft/qlib
- 資料源比較：nb-data.com、qveris.ai、ksred.com
- 券商 API：tradealgo.com、brokerchooser.com
- OpenBB：github.com/OpenBB-finance/OpenBB
- 選股器：github.com/RyanJHamby/stock-screener、github.com/xang1234/stock-screener、github.com/starboi-63/growth-stock-screener
- 技能：github.com/tradermonty/claude-trading-skills、github.com/agiprolabs/claude-trading-skills、github.com/JoelLewis/finance_skills
