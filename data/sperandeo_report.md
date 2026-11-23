# Sperandeo rules - backtest (2026-11-23 23:23 UTC)

Daily candles, ~5 years, after costs. R = result in units of risk (1R = 1% of capital with ATR/structure stops). 'Protect' = stop to breakeven at +1R, then keep at least 50% of the best open gain. One trade at a time (never average). Not investment advice.

## 1-2-3 trend change
| Variant | Profit protection | Verdict | Trades | Win % | Avg R | Profit factor | Recent 30% avg R | Max DD R |
|---|---|---|---|---|---|---|---|---|
| all markets, long+short | off | LOSES | 270 | 40.7 | -0.082 | 0.85 | -0.368 | 44.16 |
| all markets, long+short | ON | LOSES | 282 | 51.8 | -0.023 | 0.95 | -0.246 | 32.92 |
| India only, buy-only (Kite delivery) | off | LOSES | 98 | 36.7 | -0.148 | 0.77 | -0.17 | 26.27 |
| India only, buy-only (Kite delivery) | ON | LOSES | 100 | 47.0 | -0.116 | 0.79 | -0.236 | 23.99 |

### 1-2-3 best markets (all markets, protection ON, 5+ trades)
- GBP/USD: 26 trades, win 65.4%, avg 0.299R
- Gold (XAU/USD): 24 trades, win 62.5%, avg 0.245R
- Reliance: 17 trades, win 58.8%, avg 0.182R
- BANK NIFTY: 29 trades, win 55.2%, avg 0.077R
- USD/JPY: 22 trades, win 59.1%, avg 0.032R
- Infosys: 23 trades, win 52.2%, avg -0.001R
- ICICI Bank: 26 trades, win 50.0%, avg -0.054R
- EUR/USD: 26 trades, win 46.2%, avg -0.1R
- TCS: 21 trades, win 47.6%, avg -0.134R
- NIFTY 50: 18 trades, win 44.4%, avg -0.243R
- SBI: 29 trades, win 37.9%, avg -0.268R
- HDFC Bank: 21 trades, win 42.9%, avg -0.337R

## Rule 2 (never let a profit turn into a loss) on the existing strategies
| Strategy | Avg R without | Avg R WITH protection | Win % without → with | Max DD R without → with |
|---|---|---|---|---|
| 55-day breakout (Turtle-style) | -0.161 | -0.122 | 35.0 → 44.8 | 42.44 → 36.21 |
| MACD trend cross | -0.046 | -0.013 | 40.3 → 49.0 | 33.11 → 27.54 |
| 20-day breakout | -0.049 | -0.026 | 41.3 → 50.3 | 53.43 → 45.01 |
| Trend pullback | -0.068 | -0.051 | 39.2 → 47.5 | 53.31 → 40.89 |
| EMA20/50 trend cross | 0.033 | 0.032 | 37.4 → 55.7 | 14.76 → 10.66 |
| Bollinger squeeze | 0.053 | 0.047 | 42.8 → 52.0 | 23.48 → 16.08 |
| RSI(2) dip buy | -0.002 | -0.008 | 48.2 → 48.5 | 14.45 → 14.42 |
| Momentum pullback | 0.092 | 0.086 | 41.3 → 54.9 | 15.03 → 11.6 |
| Inside-bar breakout | 0.044 | 0.033 | 46.4 → 52.0 | 24.57 → 26.23 |
| Range Bollinger reversion | 0.013 | -0.033 | 45.7 → 50.4 | 12.5 → 14.55 |

Profit protection improved the average result in **4 of 10** strategies.