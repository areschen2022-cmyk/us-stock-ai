"""OpenRouter / DeepSeek API client with token budget tracking.

Provider resolution (2026-07-13): DeepSeek's native API (api.deepseek.com,
OpenAI-compatible) is preferred when DEEPSEEK_API_KEY is set — the OpenRouter
path had been silently dead because OPENROUTER_API_KEY was never configured
in CI, so the AI council produced zero real reviews. OpenRouter remains as
the fallback provider for multi-model experiments.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from src.config_loader import env

_PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "deepseek/deepseek-chat",
    },
}
_TOKEN_BUDGET_DEFAULT = 4000


class OpenRouterClient:
    def __init__(self) -> None:
        deepseek_key = env("DEEPSEEK_API_KEY", "")
        openrouter_key = env("OPENROUTER_API_KEY", "")
        if deepseek_key:
            self.provider = "deepseek"
            self.api_key = deepseek_key
        else:
            self.provider = "openrouter"
            self.api_key = openrouter_key
        cfg = _PROVIDERS[self.provider]
        self.base_url = cfg["base_url"]
        model = env("AI_MODEL", cfg["default_model"])
        # AI_MODEL may carry an OpenRouter-style "vendor/model" id; the native
        # DeepSeek endpoint wants the bare model name
        if self.provider == "deepseek" and "/" in model:
            model = model.split("/", 1)[1]
        self.model = model
        self.token_budget = int(env("AI_TOKEN_BUDGET", str(_TOKEN_BUDGET_DEFAULT)))
        self._tokens_used: int = 0
        if self.api_key:
            print(f"[AI] provider={self.provider} model={self.model}")

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
        }
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/us-stock-ai"
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
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
            parsed = json.loads(clean)
        except Exception:
            return {"action": "N/A", "confidence": 0.0, "reason": result[:120]}
        # Schema validation (Codex audit-2 #4): a JSON list, non-numeric
        # confidence, or missing keys used to crash the whole daily pipeline
        # downstream (review["score"]=..., f"{conf:.0%}"). Normalize hard.
        if not isinstance(parsed, dict):
            return {"action": "N/A", "confidence": 0.0, "reason": str(parsed)[:120]}
        action = str(parsed.get("action") or "N/A").strip().title()
        if action not in ("Buy", "Hold", "Avoid"):
            action = "N/A"
        try:
            confidence = max(0.0, min(1.0, float(parsed.get("confidence"))))
        except (TypeError, ValueError):
            confidence = 0.0
        return {"action": action, "confidence": confidence,
                "reason": str(parsed.get("reason") or "")[:300]}
