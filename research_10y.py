#!/usr/bin/env python3
"""
EdgeDesk 10-YEAR STUDY (~100 large NSE stocks, public market data only).

A. LONG-TERM: 10-year CAGR, worst fall (max drawdown), volatility, % positive years, worst rolling-3-year CAGR,
   vs NIFTY 50. Ranked by risk-adjusted growth.
B. SWING: our breakout rule (close > prev close + 1.5 x ATR(10), ADX > 15, +DI > -DI; 5% stop, trail 3% after +5%,
   max 30 days; delivery costs) on each stock. TRAIN = first ~7 years, TEST = last ~3 years (unseen).

HONEST CAVEATS: survivorship bias - today's top-100 list contains companies that already did well, so past
returns look better than a real investor would have got picking 10 years ago. Past results don't predict the future.
Not investment advice.
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

import bot
from bot import IST, log
from stock_scanner import FALLBACK
from ts_ideas import breakout_trades, tstats


def download(symbols, period="11y"):
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), 50):
        chunk = [s + ".NS" for s in symbols[i:i + 50]]
        try:
            df = yf.download(chunk, period=period, interval="1d", group_by="ticker", auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            log(f"chunk failed {e}"); continue
        for t in chunk:
            try:
                d = df[t].dropna(subset=["Close"])
                if len(d) > 500:
                    out[t[:-3]] = bot.to_daily_index(d[["Open", "High", "Low", "Close", "Volume"]])
            except Exception:
                pass
    return out


def longterm(c: pd.Series) -> dict:
    c = c.dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1
    dd = float((c / c.cummax() - 1).min())
    r = c.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252))
    yearly = c.resample("YE").last().pct_change().dropna()
    roll3 = (c / c.shift(756)) ** (1 / 3) - 1
    last3 = (c.iloc[-1] / c.iloc[-756]) ** (1 / 3) - 1 if len(c) > 756 else np.nan
    return dict(years=round(yrs, 1), cagr=round(cagr * 100, 1), max_dd=round(dd * 100, 1), vol=round(vol * 100, 1),
                pos_years=round(float((yearly > 0).mean() * 100)), worst_3y=round(float(roll3.min() * 100), 1),
                last_3y=round(float(last3 * 100), 1), ret_per_risk=round(cagr / vol, 2) if vol > 0 else 0)


def main():
    syms = FALLBACK
    data = download(syms)
    nif = bot.to_daily_index(bot.fetch("^NSEI", "11y", "1d")[0]).Close
    nl = longterm(nif.iloc[-252 * 10:])
    rows = []
    for s, d in data.items():
        d = d.iloc[-252 * 10:]
        if len(d) < 252 * 7:
            continue
        lt = longterm(d.Close)
        split = d.index[int(len(d) * 0.7)]
        tr = breakout_trades(d.Close, d.High, d.Low, 1.5, 0.05, 0.05, 0.03, o=d.Open)
        a = tstats([t["ret"] for t in tr if pd.Timestamp(t["date"]) < split])
        b = tstats([t["ret"] for t in tr if pd.Timestamp(t["date"]) >= split])
        al = tstats([t["ret"] for t in tr])
        rows.append(dict(sym=s, price=round(float(d.Close.iloc[-1]), 2), **lt, swing_train=a, swing_test=b, swing_all=al))
    beat = [r for r in rows if r["cagr"] > nl["cagr"]]
    # Long-term quality: beat NIFTY, steady (>=60% positive years), last 3 years positive; ranked by return per risk
    quality = sorted([r for r in beat if r["pos_years"] >= 60 and r["last_3y"] > 0 and r["max_dd"] > -60],
                     key=lambda r: -r["ret_per_risk"])
    # Swing: works in BOTH periods with enough trades
    swing_ok = sorted([r for r in rows if r["swing_train"].get("trades", 0) >= 8 and r["swing_test"].get("trades", 0) >= 4
                       and (r["swing_train"].get("avg_pct") or -9) > 0.3 and (r["swing_test"].get("avg_pct") or -9) > 0.3],
                      key=lambda r: -((r["swing_all"].get("avg_pct") or 0)))
    pooled_all = [r["swing_all"] for r in rows if r["swing_all"].get("trades")]
    tot_tr = sum(x["trades"] for x in pooled_all)
    avg_all = sum(x["avg_pct"] * x["trades"] for x in pooled_all) / tot_tr if tot_tr else 0
    gen = datetime.now(IST).strftime("%d %b %Y")
    md = [f"# EdgeDesk 10-year study - {gen}", "",
          f"{len(rows)} large NSE stocks with 7-10 years of data. NIFTY 50: {nl['cagr']}%/yr, worst fall {nl['max_dd']}%, "
          f"positive years {nl['pos_years']}%.", "",
          "**Caveat:** survivorship bias - these are today's large companies, so the past looks rosier than reality. "
          "Not investment advice.", "",
          f"## A. Long-term: {len(beat)} of {len(rows)} beat NIFTY. Steady winners (beat NIFTY, ≥60% positive years, last 3 yrs positive, worst fall > -60%)",
          "| Stock | Price | 10y CAGR % | Worst fall % | Volatility % | Positive years % | Worst 3-yr CAGR % | Last 3 yrs CAGR % | Return/risk |",
          "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['sym']} | {r['price']:,.0f} | {r['cagr']} | {r['max_dd']} | {r['vol']} | {r['pos_years']} | {r['worst_3y']} | {r['last_3y']} | {r['ret_per_risk']} |" for r in quality[:25]]
    md += ["", f"## B. Swing breakout rule on each stock (pooled: {tot_tr} trades, avg {avg_all:+.2f}%/trade after costs)",
           f"Stocks where it worked in BOTH the first 7 years AND the last 3 unseen years (≥ +0.3%/trade): {len(swing_ok)}",
           "| Stock | Price | Train trades / win % / avg % | Test trades / win % / avg % | All avg % | Luck t |", "|---|---|---|---|---|---|"]
    md += [f"| {r['sym']} | {r['price']:,.0f} | {r['swing_train']['trades']} / {r['swing_train']['win']} / {r['swing_train']['avg_pct']} | "
           f"{r['swing_test']['trades']} / {r['swing_test']['win']} / {r['swing_test']['avg_pct']} | {r['swing_all']['avg_pct']} | {r['swing_all']['t']} |"
           for r in swing_ok[:25]]
    both = [r["sym"] for r in quality[:25] if r["sym"] in {x["sym"] for x in swing_ok}]
    md += ["", f"## In both lists (steady long-term AND swing rule worked): {', '.join(both) or 'none'}",
           "", "Many stocks are tested, so a few look good by luck. Prefer names that appear in BOTH lists and are large and liquid."]
    (bot.DATA / "study_10y.md").write_text("\n".join(md))
    (bot.DATA / "study_10y.json").write_text(json.dumps(bot.clean(dict(generated=gen, nifty=nl, rows=rows, quality=[r["sym"] for r in quality],
                                                                     swing_ok=[r["sym"] for r in swing_ok], both=both)), indent=1))
    print("\n".join(md))
    T = [f"📚 <b>10-year study</b> ({len(rows)} large NSE stocks)",
         f"NIFTY 50: <b>{nl['cagr']}%/yr</b> (worst fall {nl['max_dd']}%). {len(beat)} stocks beat it.", "",
         "<b>🏦 Long-term: steady winners</b> (beat NIFTY, mostly positive years, still rising):"]
    T += [f"• {r['sym']}: {r['cagr']}%/yr · worst fall {r['max_dd']}% · last 3 yrs {r['last_3y']}%/yr" for r in quality[:10]]
    T += ["", f"<b>📈 Swing breakout rule</b>: overall {avg_all:+.2f}%/trade over {tot_tr} trades. Worked in past AND recent years on:"]
    T += [f"• {r['sym']}: test {r['swing_test']['trades']} trades, win {r['swing_test']['win']}%, avg {r['swing_test']['avg_pct']:+.2f}%" for r in swing_ok[:8]]
    T += ["", f"⭐ <b>In both lists:</b> {', '.join(both) or 'none'}", "",
          "<i>Caveat: today's big companies were survivors - real past returns were lower. Many stocks tested, some look good by luck. "
          "Not investment advice. Full table: data/study_10y.md</i>"]
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    print(bot.telegram("\n".join(T)))


if __name__ == "__main__":
    main()
