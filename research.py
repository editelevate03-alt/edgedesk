#!/usr/bin/env python3
"""
EdgeDesk research: honest backtests of all strategies, after costs.

- Daily candles: last 1 year and last 5 years, every market.
- 1-hour candles (forex + gold): last ~2 years (Yahoo's limit for hourly data).
- 6 original strategies + 4 new candidates.

Writes data/research.json and data/research.md. Paper research only - not investment advice.
Run:  python research.py        (needs internet / Yahoo)
      python research.py --demo (fake data, just to test the script)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import bot
from bot import MARKETS, STRATEGIES, add_indicators, backtest, clean, demo_data, fetch, log, stats, to_daily_index


# ---------------- 4 new candidate strategies ----------------
def s_breakout55(d):
    hi, lo = d.High.rolling(55).max().shift(1), d.Low.rolling(55).min().shift(1)
    return ((d.Close > hi) & (d.Close > d.ema200)).astype(int) - ((d.Close < lo) & (d.Close < d.ema200)).astype(int)


def s_ema_cross(d):
    up = (d.ema20 > d.ema50) & (d.ema20.shift(1) <= d.ema50.shift(1)) & (d.Close > d.ema200)
    dn = (d.ema20 < d.ema50) & (d.ema20.shift(1) >= d.ema50.shift(1)) & (d.Close < d.ema200)
    return up.astype(int) - dn.astype(int)


def s_bb_reversion(d):
    flat = (d.ema50 - d.ema50.shift(20)).abs() < 1.0 * d.atr          # sideways market only
    up = flat & (d.Close < d.bb_dn) & (d.rsi14 < 30)
    dn = flat & (d.Close > d.bb_up) & (d.rsi14 > 70)
    return up.astype(int) - dn.astype(int)


def s_momentum(d):
    r60 = d.Close / d.Close.shift(60) - 1
    up = (r60 > 0) & (d.Close > d.ema50) & (d.ema50 > d.ema200) & (d.rsi14.shift(1) < 50) & (d.rsi14 >= 50)
    dn = (r60 < 0) & (d.Close < d.ema50) & (d.ema50 < d.ema200) & (d.rsi14.shift(1) > 50) & (d.rsi14 <= 50)
    return up.astype(int) - dn.astype(int)


NEW = {
    "breakout55": dict(name="55-day breakout (Turtle-style)", fn=s_breakout55, stop_atr=3.0, rr=3.0, hold=40,
                       desc="Close beyond the 55-day high/low in the EMA200 trend; wide stop, big target."),
    "ema_cross": dict(name="EMA20/50 trend cross", fn=s_ema_cross, stop_atr=3.0, rr=4.0, hold=60,
                      desc="EMA20 crosses EMA50 in the EMA200 trend; long hold to catch big trends."),
    "bb_reversion": dict(name="Range Bollinger reversion", fn=s_bb_reversion, stop_atr=1.5, rr=1.5, hold=10,
                         desc="Only in sideways markets: fade closes outside the bands with RSI extreme."),
    "momentum": dict(name="Momentum pullback", fn=s_momentum, stop_atr=2.0, rr=2.5, hold=30,
                     desc="Strong 3-month trend; enter when RSI(14) turns back through 50."),
}
ALL = {**STRATEGIES, **NEW}


def verdict(st, min_n):
    n, e, pf, oos = st["trades"], st["expectancy"], st["profit_factor"], st["oos_expectancy"]
    if not n:
        return "NO TRADES"
    if n >= min_n and e >= 0.10 and (pf or 0) >= 1.2 and (oos or -1) > 0:
        return "PASS"
    if e > 0:
        return "WEAK +"
    return "LOSES"


def run_set(frames: dict, since: pd.Timestamp | None, min_n: int) -> dict:
    out = {}
    for key, p in ALL.items():
        per, pooled = {}, []
        for mid, d in frames.items():
            sig = p["fn"](d).fillna(0).to_numpy()
            tr = backtest(d, sig, p, bot.MARKET_BY_ID[mid]["cost"])
            if since is not None:
                tr = [t for t in tr if pd.Timestamp(t["date"]) >= since]
            st = stats([t["r"] for t in tr])
            per[mid] = st
            pooled += tr
        pooled.sort(key=lambda t: t["date"])
        st = stats([t["r"] for t in pooled])
        out[key] = dict(name=p["name"], desc=p["desc"], new=key in NEW, stats=st, verdict=verdict(st, min_n), per_market=per)
    return out


def market_table(res: dict) -> dict:
    """Per market: total R over all strategies, and the best strategy there."""
    tab = {}
    for m in MARKETS:
        mid = m["id"]
        rows = [(k, v["per_market"].get(mid)) for k, v in res.items() if v["per_market"].get(mid)]
        rows = [(k, s) for k, s in rows if s["trades"]]
        if not rows:
            continue
        total_n = sum(s["trades"] for _, s in rows)
        total_r = sum(s["total_r"] for _, s in rows)
        best = max(rows, key=lambda x: (x[1]["expectancy"] or -9) if x[1]["trades"] >= 5 else -9)
        tab[mid] = dict(name=m["name"], trades=total_n, avg_r_all_strategies=round(total_r / total_n, 3),
                        best_strategy=ALL[best[0]]["name"], best_avg_r=best[1]["expectancy"], best_trades=best[1]["trades"])
    return tab


def fmt_table(res: dict) -> str:
    lines = ["| Strategy | Verdict | Trades | Win % | Avg R | PF | Recent 30% avg R | Max DD R |", "|---|---|---|---|---|---|---|---|"]
    for v in sorted(res.values(), key=lambda v: -(v["stats"]["expectancy"] or -9)):
        s = v["stats"]
        lines.append(f"| {v['name']}{' *(new)*' if v['new'] else ''} | {v['verdict']} | {s['trades']} | {s['win_rate']} | "
                     f"{s['expectancy']} | {s['profit_factor']} | {s['oos_expectancy']} | {s['max_dd_r']} |")
    return "\n".join(lines)


def fmt_markets(tab: dict, ids=None) -> str:
    lines = ["| Market | Trades (all strategies) | Avg R / trade | Best strategy there | Its avg R (trades) |", "|---|---|---|---|---|"]
    for mid, t in sorted(tab.items(), key=lambda x: -x[1]["avg_r_all_strategies"]):
        if ids and mid not in ids:
            continue
        lines.append(f"| {t['name']} | {t['trades']} | {t['avg_r_all_strategies']:+.3f} | {t['best_strategy']} | {t['best_avg_r']} ({t['best_trades']}) |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    bot.DATA.mkdir(exist_ok=True)
    daily, hourly, errors = {}, {}, []
    for i, m in enumerate(MARKETS):
        try:
            if args.demo:
                d, _ = demo_data(m, 100 + i)
            else:
                d, _ = fetch(m["ticker"], "6y", "1d")
            daily[m["id"]] = add_indicators(to_daily_index(d))
            if m["group"] in ("fx", "metal"):
                if args.demo:
                    h = d.copy()
                else:
                    h, _ = fetch(m["ticker"], "730d", "1h")
                if len(h) > 400:
                    hourly[m["id"]] = add_indicators(h)
            log(f"research data: {m['id']} daily {len(daily[m['id']])}" + (f", hourly {len(hourly.get(m['id'], []))}" if m['id'] in hourly else ""))
        except Exception as e:
            errors.append(f"{m['id']}: {e}")
            log(f"research data: {m['id']} FAILED {e}")

    now = pd.Timestamp(datetime.now(timezone.utc).date())
    d1y = run_set(daily, now - pd.Timedelta(days=365), min_n=20)
    d5y = run_set(daily, now - pd.Timedelta(days=5 * 365), min_n=30)
    h2y = run_set(hourly, None, min_n=40) if hourly else {}
    fxids = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    m1y, m5y = market_table(d1y), market_table(d5y)
    mh = market_table(h2y) if h2y else {}

    passing = [(k, v) for k, v in d5y.items() if v["verdict"] == "PASS" and d1y[k]["stats"]["expectancy"] is not None and d1y[k]["stats"]["expectancy"] > 0]
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = [f"# EdgeDesk research - {gen}", "",
          "All results are **after estimated costs**, in R (1R = the amount risked, e.g. 1% of the account). "
          "Backtests do not predict the future. Paper research only - **not investment advice**.", "",
          "PASS = enough trades, avg ≥ +0.10R, profit factor ≥ 1.2 and positive in the most recent 30% of trades. "
          "WEAK + = slightly positive but not proven. LOSES = negative after costs.", "",
          "## Daily candles - last 1 year (all 12 markets)", fmt_table(d1y), "",
          "## Daily candles - last 5 years (all 12 markets)", fmt_table(d5y), ""]
    if h2y:
        md += ["## 1-hour candles - last ~2 years (forex + gold)", fmt_table(h2y), ""]
    md += ["## Forex & gold - which is best? (daily, all strategies combined)", "",
           "**Last 1 year**", fmt_markets(m1y, fxids), "", "**Last 5 years**", fmt_markets(m5y, fxids), ""]
    if mh:
        md += ["**1-hour, last ~2 years**", fmt_markets(mh, fxids), ""]
    md += ["## All markets - last 5 years (daily)", fmt_markets(m5y), "",
           "## Strategies that passed 5 years AND are positive in the last year",
           ("\n".join(f"- {v['name']}" for _, v in passing) if passing else "- **None.** No strategy is proven after costs on these markets."), ""]
    if errors:
        md += ["## Data errors", *[f"- {e}" for e in errors]]

    out = dict(generated_at=gen, mode="demo" if args.demo else "live", daily_1y=d1y, daily_5y=d5y, hourly_2y=h2y,
               markets_1y=m1y, markets_5y=m5y, markets_hourly=mh, passing=[k for k, _ in passing], errors=errors)
    (bot.DATA / ("research_demo.json" if args.demo else "research.json")).write_text(json.dumps(clean(out), indent=1))
    (bot.DATA / ("research_demo.md" if args.demo else "research.md")).write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
