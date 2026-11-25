# EdgeDesk stock-selection rulebook (simple version)

## 🔴 AVOID - never trade these
| Rule | Why |
|---|---|
| Price under ₹50 ("penny stock") | Easy to manipulate; huge spreads; often operator-driven |
| Traded value under ₹10 crore/day | Hard to sell when you want; price can be pushed around |
| Moved more than 15% in one day | Pump or big news - the move is usually over (or reverses) |
| Moved more than 30% in a week | Classic pump-and-dump pattern |
| 3+ days with >8% moves in a month | Erratic / operator activity |
| Daily range above 6% (very volatile) | Stop-loss gets hit by noise |
| Downtrend (below its 200-day average, 50-day below 200-day) | Don't buy falling stocks |
| Tips from Telegram/WhatsApp/YouTube "multibagger" groups | Most common way operators find buyers |

## 🟢 SELECT - all of these must be true
1. **Breakout today** (closed much higher than its normal move: > 1.5 × ATR) - our tested swing rule
2. **Strong uptrend**: price > 20-day > 50-day > 200-day average
3. **Very liquid**: at least ₹50 crore traded per day
4. **Normal volatility**: daily range 1-4%
5. **Fits your budget** with the 2% rule (max loss ≈ 2% of capital if the 5% stop hits)

## 🟡 WATCH - good stocks, not ready yet
Uptrend + stronger than NIFTY, but no breakout today. Keep them on a list; buy only if they turn 🟢.

## Every trade
- Buy next day at 9:15 (Delivery/CNC), set a GTT stop 5% below your buy price
- Raise the stop when the bot says 🛡️ (never lower it)
- Never average down; max 1 trade at a time with ₹2,000 (3 with ₹10,000+)

*Not investment advice. Only the 🟢 SELECT rule is backtested, and it is not proven yet.*
