# US Stock AI

Daily US stock monitoring system that scores a curated universe, writes a GitHub Pages dashboard, sends a compact Telegram brief, and records signal outcomes for later review.

## Architecture

```text
main.py
src/
  data_provider/   yfinance, SEC EDGAR, Nasdaq universe, retry queue
  indicators/      technical, fundamental, market, risk, flow
  news/            RSS fetcher and theme detector
  scoring/         100-point score engine and grade mapping
  storage/         SQLite WAL persistence
  report/          dashboard JSON writer
  notifier/        Telegram morning brief
  ai/              token-gated OpenRouter model council
  backtest/        forward signal tracking
docs/              GitHub Pages static dashboard
scripts/           post-update checks and knowledge export
tests/             focused pytest coverage
```

## Scoring

The composite score is:

```text
technical(0-30)
+ fundamental(0-20)
+ flow(0-15)
+ news_catalyst(0-15)
+ market_sentiment(0-10)
- risk_penalty(0-10)
```

Grades: `S >= 85`, `A >= 75`, `B >= 65`, `C >= 50`, `D < 50`.

AI review is token-gated: only stocks with `score >= 75`, or the fallback top 5 when no stock reaches the threshold, are sent to the model council.

## Setup

```powershell
cd C:\Users\User\Documents\us-stock-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` for optional integrations:

```text
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
OPENROUTER_API_KEY=
DEEPSEEK_API_KEY=
FINNHUB_API_KEY=
FRED_API_KEY=
```

Core data collection uses `yfinance` and SEC EDGAR, so an API key is not required for the basic daily run.

## Manual Run

Run the full daily pipeline:

```powershell
python main.py
```

Send only the morning Telegram brief from persisted scores:

```powershell
python main.py --telegram-only
```

Run tests:

```powershell
pytest
```

Run the post-update health check:

```powershell
python scripts/post_update_check.py
```

## GitHub Secrets

Add these repository secrets before enabling the workflow:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
OPENROUTER_API_KEY
DEEPSEEK_API_KEY
FINNHUB_API_KEY
FRED_API_KEY
```

Only `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `OPENROUTER_API_KEY` are needed for Telegram and AI review. The others are optional.

## GitHub Pages

The dashboard is served from `docs/`. `docs/dashboard_data.json` contains a small preview payload so `docs/index.html` can render before the first scheduled run.

The workflow runs:

- `21:30 UTC` on weekdays for daily scoring and dashboard update.
- `10:00 UTC` on weekdays for the morning Telegram brief.
