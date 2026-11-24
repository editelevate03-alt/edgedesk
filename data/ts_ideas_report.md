# Trading Strategy (tradingstrategy-ai) ideas on Indian stocks - 2026-11-24 13:30 UTC

20 large NSE stocks, daily candles ~5 years, BUY-only (Kite delivery), after ~0.12%/side + DP charges. Settings are chosen on TRAIN (first 70%) and then checked on TEST (last 30%, unseen). Not investment advice.

**Benchmark - just hold NIFTY 50:** TRAIN CAGR 15.3% · TEST CAGR 4.6% · all 11.9%/yr, worst drop -17.2%

## A. Momentum rotation (top 10 settings by TRAIN result)
| Lookback days | Hold top | Rebalance every | TRAIN CAGR % | TEST CAGR % (unseen) | TEST worst drop % | All CAGR % | All Sharpe |
|---|---|---|---|---|---|---|---|
| 120 | 2 | 21 days | 14.7 | -0.8 | -22.2 | 10.4 | 0.6 |
| 120 | 3 | 21 days | 13.5 | -0.1 | -25.3 | 9.6 | 0.61 |
| 250 | 2 | 5 days | 12.4 | 4.3 | -17.4 | 10.0 | 0.61 |
| 60 | 2 | 21 days | 11.9 | -8.2 | -29.5 | 5.8 | 0.39 |
| 250 | 3 | 21 days | 11.2 | 1.2 | -18.6 | 8.1 | 0.56 |
| 250 | 2 | 21 days | 9.6 | 3.1 | -21.4 | 7.7 | 0.5 |
| 60 | 5 | 21 days | 9.5 | -9.0 | -25.1 | 3.7 | 0.32 |
| 120 | 5 | 21 days | 9.0 | -5.9 | -25.0 | 4.5 | 0.37 |
| 20 | 5 | 21 days | 8.8 | -0.5 | -19.5 | 6.1 | 0.47 |
| 60 | 3 | 21 days | 8.2 | -4.6 | -24.0 | 4.2 | 0.32 |

0 of 24 rotation settings beat NIFTY on the unseen TEST period.

## B. ATR breakout + ADX filter (top 8 settings by TRAIN result)
| Breakout F×ATR | Hard stop | Trail after | Trail | TRAIN trades / win / avg % | TEST trades / win / avg % (unseen) | Luck t (all) |
|---|---|---|---|---|---|---|
| 2.0 | 5% | +10% | 1% | 119 / 48.7% / 1.54 | 54 / 38.9% / 0.04 | 2.05 |
| 2.0 | 5% | +5% | 1% | 125 / 62.4% / 1.51 | 56 / 50.0% / 0.5 | 2.97 |
| 2.0 | 5% | +10% | 3% | 118 / 49.2% / 1.3 | 54 / 38.9% / -0.06 | 1.74 |
| 2.0 | 5% | +5% | 3% | 124 / 62.9% / 1.16 | 55 / 49.1% / 0.43 | 2.28 |
| 1.5 | 5% | +10% | 1% | 243 / 43.6% / 1.03 | 112 / 47.3% / 0.53 | 2.43 |
| 1.0 | 5% | +10% | 1% | 419 / 43.9% / 1.0 | 185 / 44.9% / 0.47 | 3.0 |
| 3.0 | 5% | +5% | 1% | 38 / 57.9% / 0.98 | 10 / 50.0% / 0.36 | 1.06 |
| 1.5 | 5% | +5% | 1% | 269 / 58.4% / 0.95 | 119 / 56.3% / 0.76 | 3.38 |