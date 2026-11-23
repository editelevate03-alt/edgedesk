# Global cues vs Indian market - 5-year backtest (2026-11-23 23:23 UTC)

Cues known before 09:15 IST (previous US close, crude, USD/INR, dollar index, US 10y yield). 'Right %' = how often the market moved in the direction the score said. Base = % of all days that were up. TRAIN = first 70% of days, TEST = last 30% (unseen). Not investment advice.

## NIFTY
| What | Period | Score ≥ +40: days / right % | Score ≤ −40: days / right % | Base up % | Correlation |
|---|---|---|---|---|---|
| Opening gap | train | 46 / 93.5% | 63 / 76.2% | 64.7% | 0.477 |
| Opening gap | test | 26 / 69.2% | 25 / 84.0% | 52.9% | 0.361 |
| Open → close (intraday) | train | 46 / 47.8% | 63 / 50.8% | 47.7% | -0.077 |
| Open → close (intraday) | test | 26 / 53.8% | 25 / 52.0% | 48.7% | -0.0 |

**1-hour ORB option trades (ATM, ~2 years) split by global score**
| Global score | Trades | Win % | Avg Rs | Total Rs |
|---|---|---|---|---|
| agrees | 96 | 35.4 | -410.0 | -39379.0 |
| disagrees | 111 | 31.5 | -529.0 | -58743.0 |
| neutral | 271 | 33.9 | -291.0 | -78862.0 |

## BANKNIFTY
| What | Period | Score ≥ +40: days / right % | Score ≤ −40: days / right % | Base up % | Correlation |
|---|---|---|---|---|---|
| Opening gap | train | 46 / 89.1% | 63 / 79.4% | 60.4% | 0.459 |
| Opening gap | test | 26 / 61.5% | 25 / 76.0% | 50.3% | 0.352 |
| Open → close (intraday) | train | 46 / 41.3% | 63 / 50.8% | 48.3% | -0.116 |
| Open → close (intraday) | test | 26 / 42.3% | 25 / 52.0% | 50.5% | -0.012 |

**1-hour ORB option trades (ATM, ~2 years) split by global score**
| Global score | Trades | Win % | Avg Rs | Total Rs |
|---|---|---|---|---|
| agrees | 77 | 35.1 | -503.0 | -38744.0 |
| disagrees | 114 | 36.8 | -511.0 | -58300.0 |
| neutral | 272 | 46.0 | -99.0 | -27013.0 |
