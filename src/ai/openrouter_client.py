"""OpenRouter / DeepSeek API client with token budget tracking."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from src.config_loader import env

_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "deepseek/deepseek-chat"
_TOKEN_BUDGET_DEFAULT = 4000


class OpenRouterClient:
    def __init__(self) -> None:
        self.api_key = env("OPENROUTER_API_KEY", "")
        self.model = env("AI_MODEL", _DEFAULT_MODEL)
        self.token_budget = int(env("AI_TOKEN_BUDGET", str(_TOKEN_BUDGET_DEFAULT)))
        self._tokens_used: int = 0

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    def _is_configured(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str | None:
        if not self._is_configured():
            return None
        if self._tokens_used >= self.token_budget:
            print(f"[AI] Token budget exhausted ({self._tokens_used}/{self.token_budget})")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/us-stock-ai",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = httpx.post(
                f"{_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage", {})
            self._tokens_used += usage.get("total_tokens", max_tokens)
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            print(f"[AI] OpenRouter error: {exc}")
            return None

    def single_review(self, symbol: str, score_summary: str) -> dict[str, Any]:
        """Single-model review for a candidate stock."""
        prompt = (
            f"You are a professional US equity analyst. Review this stock briefly.\n"
            f"Stock: {symbol}\n"
            f"Scoring summary: {score_summary}\n\n"
            "Reply in JSON with keys: action (Buy/Hold/Avoid), confidence (0-1), reason (1 sentence)."
        )
        result = self.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        if not result:
            return {"action": "N/A", "confidence": 0.0, "reason": "AI unavailable"}
        try:
            clean = result.strip().lstrip("```json").rstrip("```").strip()
            return json.loads(clean)
        except Exception:
            return {"action": "N/A", "confidence": 0.0, "reason": result[:120]}
