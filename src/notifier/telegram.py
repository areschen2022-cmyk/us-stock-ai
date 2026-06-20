"""Telegram morning brief notifier for US Stock AI."""
from __future__ import annotations

import asyncio
from datetime import date

from src.config_loader import env
from src.scoring.score_engine import StockScore

ACTION_ZH = {
    "Strong Buy Candidate": "強勢買入候選",
    "Watch / Buy Pullback": "觀察，等拉回買入",
    "Monitor": "追蹤觀察",
    "Avoid": "暫時避開",
}

THEME_ZH = {
    "ai_infra": "AI 基礎建設",
    "semiconductor": "半導體",
    "cloud_saas": "雲端軟體",
    "cybersecurity": "資安",
    "defense": "國防軍工",
    "energy_power": "能源電力",
    "crypto_fintech": "加密金融",
    "glp1_biotech": "GLP-1 生技",
    "emerging_market": "新興市場",
}

DASHBOARD_URL = "https://areschen2022-cmyk.github.io/us-stock-ai/"


def _zh_action(action: str) -> str:
    return ACTION_ZH.get(action, action or "未分類")


def _zh_themes(themes: list[str]) -> str:
    if not themes:
        return ""
    return "、".join(THEME_ZH.get(t, t) for t in themes[:3])


def _fmt_price(value: float | None) -> str:
    return f"${value:.2f}" if value is not None else "無資料"


def _market_direction_icon(direction: str) -> str:
    return {"多頭": "📈", "空頭": "📉", "中性": "↔️"}.get(direction, "↔️")


def _build_morning_report(
    top_scores: list[StockScore],
    market_prices: dict[str, float],
    today: date,
    ai_summaries: dict[str, str] | None = None,
    overview: dict | None = None,
) -> list[str]:
    """Build segmented Telegram messages, each below 3800 chars."""
    ai_summaries = ai_summaries or {}
    overview = overview or {}

    vix = market_prices.get("^VIX") or 0.0
    spy = market_prices.get("SPY") or 0.0
    qqq = market_prices.get("QQQ") or 0.0
    smh = market_prices.get("SMH")
    iwm = market_prices.get("IWM")

    # 風向
    if vix < 18:
        direction = "多頭"
    elif vix < 25:
        direction = "中性"
    else:
        direction = "空頭"
    dir_icon = _market_direction_icon(direction)

    total = overview.get("total_scored", len(top_scores))
    grade_s = overview.get("grade_S", sum(1 for s in top_scores if s.grade == "S"))
    grade_a = overview.get("grade_A", sum(1 for s in top_scores if s.grade == "A"))
    grade_b = overview.get("grade_B", sum(1 for s in top_scores if s.grade == "B"))

    # 主題彙整
    theme_counts: dict[str, int] = {}
    for s in top_scores:
        for t in (s.themes or []):
            theme_counts[t] = theme_counts.get(t, 0) + 1
    top_themes = sorted(theme_counts.items(), key=lambda x: -x[1])[:4]
    themes_str = "、".join(
        f"{THEME_ZH.get(t, t)}({n})" for t, n in top_themes
    ) if top_themes else "無明顯題材"

    # AI 統計
    ai_buy = overview.get("ai_buy", 0)
    ai_hold = overview.get("ai_hold", 0)
    ai_avoid = overview.get("ai_avoid", 0)
    ai_total = overview.get("ai_total", len(ai_summaries))

    # === 第一段：標頭 ===
    vix_str = f"{vix:.1f}"
    spy_str = _fmt_price(spy)
    qqq_str = _fmt_price(qqq)

    extra_indices = ""
    if smh:
        extra_indices += f"｜SMH {_fmt_price(smh)}"
    if iwm:
        extra_indices += f"｜IWM {_fmt_price(iwm)}"

    header = (
        f"美股 AI 早報｜{today}\n"
        f"大盤：SPY {spy_str}｜QQQ {qqq_str}｜VIX {vix_str}{extra_indices}\n"
        f"\n"
        f"{dir_icon} 風向：{direction}\n"
        f"題材：{themes_str}\n"
        f"掃描 {total} 檔｜S {grade_s}｜A {grade_a}｜B {grade_b}\n"
        f"\n"
    )

    parts: list[str] = []
    body = header

    def _flush(segment: str) -> None:
        nonlocal body
        if len(body) + len(segment) > 3800:
            parts.append(body.rstrip())
            body = ""
        body += segment

    # === 今日重點 ===
    highlights = [s for s in top_scores if s.grade in ("S", "A")][:5]
    if not highlights:
        highlights = top_scores[:3]

    if highlights:
        _flush("今日重點\n")
        for s in highlights:
            ai_tag = ""
            if s.symbol in ai_summaries:
                ai_tag = "｜AI 同意" if "buy" in ai_summaries[s.symbol].lower() or "強" in ai_summaries[s.symbol] else "｜AI 複核"
            stop_str = _fmt_price(s.stop_price)
            _flush(
                f"▸ {s.symbol}｜{s.total_score}/100｜{s.grade}｜"
                f"{_zh_action(s.action)}{ai_tag}\n"
                f"  現價 {_fmt_price(s.price)}  停損 {stop_str}\n"
            )

    if ai_total > 0:
        _flush(
            f"AI 複核：同意 {ai_buy}｜保留 {ai_hold}｜"
            f"不建議 {ai_avoid}｜已複核 {ai_total}\n"
        )

    _flush("\n")

    # === 完整 S 級 ===
    grade_s_list = [s for s in top_scores if s.grade == "S"]
    if grade_s_list:
        _flush("S 級 — 強勢買入候選\n")
        for s in grade_s_list:
            themes_tag = _zh_themes(s.themes)
            _flush(
                f"{s.symbol} [{s.grade}] {s.total_score} 分\n"
                f"  {_fmt_price(s.price)}  停損 {_fmt_price(s.stop_price)}\n"
                f"  操作：{_zh_action(s.action)}\n"
                + (f"  題材：{themes_tag}\n" if themes_tag else "")
                + f"  技{s.technical_score} 基{s.fundamental_score} "
                  f"流{s.flow_score} 聞{s.news_catalyst_score} "
                  f"市{s.market_sentiment_score} 扣{s.risk_penalty}\n"
            )
            if s.symbol in ai_summaries:
                _flush(f"  AI：{ai_summaries[s.symbol]}\n")
            _flush("\n")

    # === A 級 ===
    grade_a_list = [s for s in top_scores if s.grade == "A"]
    if grade_a_list:
        _flush("A 級 — 觀察，等拉回買入\n")
        for s in grade_a_list:
            _flush(
                f"{s.symbol} [{s.grade}] {s.total_score} 分｜"
                f"{_fmt_price(s.price)}｜{_zh_action(s.action)}\n"
            )
        _flush("\n")

    # === 其他高分 ===
    others = [s for s in top_scores if s.grade not in ("S", "A")]
    if others:
        _flush("其他追蹤標的\n")
        for s in others[:5]:
            _flush(
                f"{s.symbol} [{s.grade}] {s.total_score} 分｜"
                f"{_fmt_price(s.price)}｜{_zh_action(s.action)}\n"
            )
        _flush("\n")

    # === 頁尾 ===
    body += (
        f"監控頁：{DASHBOARD_URL}\n"
        "僅供研究追蹤，不是投資建議。"
    )
    parts.append(body.rstrip())
    return parts


class TelegramNotifier:
    def __init__(self) -> None:
        self.token = env("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = env("TELEGRAM_CHAT_ID", "").strip()

    def _is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def _send_async(self, text: str) -> bool:
        if not self._is_configured():
            print("[Telegram] Not configured; skipping send")
            return False
        try:
            from telegram import Bot
            bot = Bot(token=self.token)
            await bot.send_message(
                chat_id=self.chat_id,
                text=text,
                disable_web_page_preview=True,
            )
            return True
        except Exception as exc:
            print(f"[Telegram] Send failed: {exc}")
            return False

    def send(self, text: str) -> bool:
        return asyncio.run(self._send_async(text))

    def send_morning_report(
        self,
        top_scores: list[StockScore],
        market_prices: dict[str, float],
        today: date | None = None,
        ai_summaries: dict[str, str] | None = None,
        overview: dict | None = None,
    ) -> bool:
        today = today or date.today()
        segments = _build_morning_report(
            top_scores, market_prices, today, ai_summaries, overview
        )
        ok = True
        for segment in segments:
            if not self.send(segment):
                ok = False
        return ok
