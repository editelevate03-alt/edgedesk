#!/usr/bin/env python3
"""
India-only backtest report for trading on Zerodha Kite (delivery / CNC).

- BUY-only (no shorting in cash delivery), 8 NSE markets (NIFTY/BANK NIFTY via ETFs like NIFTYBEES/BANKBEES).
- Realistic Zerodha delivery costs ~0.12% per side (STT 0.1% each side + exchange, SEBI, stamp, GST).
  DP charge (~Rs 15.93 per sell) is NOT included - it hurts small trades even more.
- Periods: last 1, 3 and 5 years. All 10 strategies.
- Sends a short report to Telegram and writes data/india_report.md.

Paper research only. Not investment advice. Win rate alone does not mean profit.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, timezone

import pandas as pd

import bot
import research
from bot import MARKETS, add_indicators, backtest, clean, demo_data, fetch, log, stats, to_daily_index

ZERODHA_COST_SIDE = 0.0012
INDIA = [m for m in MARKETS if m["group"].startswith("india")]


def verdict(st, min_n):
    n, e, pf, oos = st["trades"], st["expectancy"], st["profit_factor"], st["oos_expectancy"]
    if not n:
        return "NO TRADES"
    if n >= min_n and e >= 0.10 and (pf or 0) >= 1.2 and (oos or -1) > 0:
        return "PASS"
    return "WEAK +" if e > 0 else "LOSES"


def run(frames, since, min_n):
    out = {}
    for key, p in research.ALL.items():
        pooled, per = [], {}
        for mid, d in frames.items():
            sig = p["fn"](d).fillna(0).clip(lower=0).to_numpy()          # BUY-only
            tr = backtest(d, sig, p, ZERODHA_COST_SIDE)
            tr = [t for t in tr if pd.Timestamp(t["date"]) >= since]
            per[mid] = stats([t["r"] for t in tr])
            pooled += tr
        pooled.sort(key=lambda t: t["date"])
        st = stats([t["r"] for t in pooled])
        out[key] = dict(name=p["name"], stats=st, verdict=verdict(st, min_n), per_market=per)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()
    frames, errors = {}, []
    for i, m in enumerate(INDIA):
        try:
            d = demo_data(m, 100 + i)[0] if args.demo else fetch(m["ticker"], "6y", "1d")[0]
            frames[m["id"]] = add_indicators(to_daily_index(d))
            log(f"india: {m['id']} {len(frames[m['id']])} bars")
        except Exception as e:
            errors.append(f"{m['id']}: {e}")
    now = pd.Timestamp(datetime.now(timezone.utc).date())
    periods = {"1y": run(frames, now - pd.Timedelta(days=365), 20),
               "3y": run(frames, now - pd.Timedelta(days=3 * 365), 30),
               "5y": run(frames, now - pd.Timedelta(days=5 * 365), 30)}

    # Bot's own forward-test (paper) record
    state = json.loads((bot.DATA / "state.json").read_text()) if (bot.DATA / "state.json").exists() else {}
    closed = state.get("closed_trades", [])
    fwd = stats([t.get("r") or 0 for t in closed])

    # Markdown report
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = [f"# EdgeDesk - India report for Kite (BUY-only, Zerodha delivery costs) - {gen}", "",
          "Win rate = % of trades that made money. **Avg R** = average result per trade after costs "
          "(1R = amount risked). A strategy can win 60% and still lose money if losses are bigger than wins.", ""]
    for per, res in periods.items():
        md += [f"## Last {per[0]} year(s)", "| Strategy | Verdict | Trades | Win rate % | Avg R | Profit factor |", "|---|---|---|---|---|---|"]
        for v in sorted(res.values(), key=lambda v: -(v["stats"]["expectancy"] or -9)):
            s = v["stats"]
            md.append(f"| {v['name']} | {v['verdict']} | {s['trades']} | {s['win_rate']} | {s['expectancy']} | {s['profit_factor']} |")
        md.append("")
    md += ["## Best market per strategy (last 5 years, min 10 trades)"]
    for v in periods["5y"].values():
        best = [(mid, s) for mid, s in v["per_market"].items() if s["trades"] >= 10]
        if best:
            mid, s = max(best, key=lambda x: x[1]["expectancy"] or -9)
            md.append(f"- {v['name']}: {bot.MARKET_BY_ID[mid]['name']} - {s['trades']} trades, win {s['win_rate']}%, avg {s['expectancy']}R")
    md += ["", f"## EdgeDesk bot forward-test (paper) record: {fwd['trades']} closed trades"
           + (f", win rate {fwd['win_rate']}%, avg {fwd['expectancy']}R, total {fwd['total_r']}R" if fwd["trades"] else " - too early, no closed trades yet")]
    if errors:
        md += ["", "Data errors: " + "; ".join(errors)]
    (bot.DATA / ("india_report_demo.md" if args.demo else "india_report.md")).write_text("\n".join(md))
    (bot.DATA / ("india_report_demo.json" if args.demo else "india_report.json")).write_text(json.dumps(clean(dict(generated_at=gen, periods=periods, forward=fwd, errors=errors)), indent=1))
    print("\n".join(md))

    # Telegram summary
    def line(v):
        s = v["stats"]
        icon = {"PASS": "✅", "WEAK +": "🟡", "LOSES": "❌"}.get(v["verdict"], "•")
        return f"{icon} {html.escape(v['name'])}: win {s['win_rate'] if s['win_rate'] is not None else '-'}% · avg {s['expectancy'] if s['expectancy'] is not None else '-'}R · {s['trades']} trades"
    r1, r5 = periods["1y"], periods["5y"]
    passed = [v["name"] for k, v in r5.items() if v["verdict"] == "PASS" and (r1[k]["stats"]["expectancy"] or -1) > 0]
    T = [f"<b>EdgeDesk India report (Kite, BUY-only)</b>",
         "8 NSE markets · after Zerodha delivery costs · win rate = accuracy", "",
         "<b>Last 1 year</b>"] + [line(v) for v in sorted(r1.values(), key=lambda v: -(v['stats']['expectancy'] or -9))] + \
        ["", "<b>Last 5 years</b>"] + [line(v) for v in sorted(r5.values(), key=lambda v: -(v['stats']['expectancy'] or -9))] + \
        ["", "<b>Proven (passed 5y AND positive last 1y):</b> " + (", ".join(passed) if passed else "NONE"),
         "", f"<b>Bot paper record:</b> {fwd['trades']} closed trades" + (f", win {fwd['win_rate']}%, total {fwd['total_r']}R" if fwd["trades"] else " (too early)"),
         "", "<i>Backtests are not guarantees. Not investment advice. Win rate alone does not mean profit - look at avg R.</i>"]
    if not args.no_telegram and not args.demo:
        os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(state)
        ok, msg = bot.telegram("\n".join(T))
        log(f"telegram report: {msg}")
    else:
        print("\n".join(T))


if __name__ == "__main__":
    main()
