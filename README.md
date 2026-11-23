# EdgeDesk Live

A free trading-signal website plus Telegram alert bot for **NIFTY, BANK NIFTY, Reliance, HDFC Bank,
ICICI Bank, SBI, Infosys, TCS, EUR/USD, GBP/USD, USD/JPY and Gold (XAU/USD)**.

> **Paper trading only. Not investment advice.** Signals are not guaranteed and can lose money.
> Backtests don't predict the future. Every signal has a stop-loss and uses 1% risk.

## How it works

Every 15 minutes GitHub Actions (free for public repos) runs `bot.py`:

| Bot | Job |
|---|---|
| Chief | Runs all bots, combines scores, writes `data/signals.json` |
| 1. Data | Daily + 15-minute prices from Yahoo Finance (yfinance, with a direct-API fallback) |
| 2. Chart | EMA20/50/200, RSI, ATR, Bollinger, MACD, 20-day high/low |
| 3. Candle | Engulfing, hammer, shooting star, doji, inside bar… |
| 4. Flow | Volume vs average, on-balance volume |
| 5. Network | Market breadth, strength vs NIFTY, US-dollar strength |
| 6. News | Free RSS headlines (Google News, Economic Times, FXStreet) |
| 7. Strategy | 6 strategies on **finished daily candles** |
| 8. Backtest | ~5 years, all markets, **after costs** → TRADEABLE / WATCH / REJECTED |
| 9. Risk + mistake guard | 1% sizing, stop on every trade, −3R daily loss limit, blocks bad trades |
| 10. Forward test | Tracks every signal as a paper trade → honest track record |

A setup becomes a signal only if its strategy is **TRADEABLE** for that market, it scores **60+/100**,
and it passes the mistake guard. The guard blocks: no stop, tight or wide stop, R:R < 1.5, chasing, stale data,
duplicates, more than 5 open trades, correlated pile-ups, conflicting signals, and anything after the daily loss limit.

## Run it on a computer (optional)

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python bot.py --demo              # fake data, no internet needed
python bot.py                     # live data
python -m http.server 8000        # then open http://localhost:8000
```

Telegram (optional on a computer): set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` environment variables, then
`python bot.py --hello`.

## Settings

Top of `bot.py`: `RISK_PCT`, `DAILY_LOSS_LIMIT_R`, `MAX_OPEN_TRADES`, `MIN_SCORE`. Paper account sizes come from the
environment variables `ACCOUNT_INR` (default 1,00,000) and `ACCOUNT_USD` (default 10,000).

## Notes

- Yahoo data can be delayed. NSE index data in the free TradingView widget may be unavailable.
- GitHub may delay scheduled runs by a few minutes at busy times.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. If that happens, open
  Actions → EdgeDesk bots → Enable workflow.
- Never commit secrets. Telegram keys live only in **Settings → Secrets and variables → Actions**.
