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


def _zh_action(action: str) -> str:
    return ACTION_ZH.get(action, action or "未分類")


def _zh_themes(themes: list[str]) -> str:
    if not themes:
        return "無明確題材"
    return "、".join(THEME_ZH.get(theme, theme) for theme in themes[:2])


def _fmt_price(value: float | None) -> str:
    return f"${value:.2f}" if value is not None else "無資料"


def _fmt_score_line(s: StockScore) -> str:
    stop = _fmt_price(s.stop_price)
    return (
        f"{s.symbol} [{s.grade}] {s.total_score} 分\n"
        f"  價格：{_fmt_price(s.price)}｜停損參考：{stop}\n"
        f"  操作：{_zh_action(s.action)}\n"
        f"  題材：{_zh_themes(s.themes)}\n"
        f"  分數：技術 {s.technical_score} / 基本面 {s.fundamental_score} / "
        f"籌碼 {s.flow_score} / 新聞 {s.news_catalyst_score} / "
        f"市場 {s.market_sentiment_score} / 風險扣 {s.risk_penalty}"
    )


def _build_morning_report(
    top_scores: list[StockScore],
    market_prices: dict[str, float],
    today: date,
    ai_summaries: dict[str, str] | None = None,
) -> list[str]:
    """Build segmented Telegram messages, each below Telegram's 4096-char limit."""
    ai_summaries = ai_summaries or {}
    vix = market_prices.get("^VIX", 0)
    spy = market_prices.get("SPY", 0)
    qqq = market_prices.get("QQQ", 0)

    header = (
        f"美股 AI 早報｜{today}\n"
        f"大盤：SPY {_fmt_price(spy)}｜QQQ {_fmt_price(qqq)}｜VIX {vix:.1f}\n"
        "------------------------------\n"
    )

    grade_s = [s for s in top_scores if s.grade == "S"]
    grade_a = [s for s in top_scores if s.grade == "A"]
    others = [s for s in top_scores if s.grade not in ("S", "A")]

    parts: list[str] = []
    body = header

    def _flush_if_needed(segment: str) -> None:
        nonlocal body
        if len(body) + len(segment) > 3800:
            parts.append(body.rstrip())
            body = ""
        body += segment

    if grade_s:
        _flush_if_needed("S 級｜強勢買入候選\n")
        for score in grade_s:
            _flush_if_needed(_fmt_score_line(score) + "\n")
            if score.symbol in ai_summaries:
                _flush_if_needed(f"  AI 複核：{ai_summaries[score.symbol]}\n")
            _flush_if_needed("\n")

    if grade_a:
        _flush_if_needed("A 級｜觀察，等拉回買入\n")
        for score in grade_a:
            _flush_if_needed(_fmt_score_line(score) + "\n\n")

    if others:
        _flush_if_needed("其他高分標的\n")
        for score in others[:5]:
            _flush_if_needed(
                f"{score.symbol} [{score.grade}] {score.total_score} 分｜"
                f"{_fmt_price(score.price)}｜{_zh_action(score.action)}\n"
            )

    body += "\n由 US Stock AI 自動產生。"
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
    ) -> bool:
        today = today or date.today()
        segments = _build_morning_report(top_scores, market_prices, today, ai_summaries)
        ok = True
        for segment in segments:
            if not self.send(segment):
                ok = False
        return ok
