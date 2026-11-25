#!/usr/bin/env python3
"""Analyse a list of NSE symbols (from the SYMBOLS env var) with EdgeDesk's engine. Prints JSON.
No quantities or buy prices - only public market data."""
import json, os
import bot
from bot import fetch, to_daily_index
from stock_scanner import analyse
syms = [s.strip().upper() for s in os.getenv("SYMBOLS", "").replace(",", " ").split() if s.strip()]
nif = to_daily_index(fetch("^NSEI", "2y", "1d")[0]).Close
n63 = float(nif.iloc[-1] / nif.iloc[-64] - 1)
out = []
for sym in syms[:30]:
    try:
        d = to_daily_index(fetch(sym + ".NS", "2y", "1d", tries=2)[0])
        r = analyse(sym, d.tail(260), n63)
        c = d.Close
        e200 = c.ewm(span=200, adjust=False).mean()
        r.update(hi52=round(float(d.High.tail(250).max()), 2), lo52=round(float(d.Low.tail(250).min()), 2),
                 ema200=round(float(e200.iloc[-1]), 2), above_200=bool(c.iloc[-1] > e200.iloc[-1]),
                 ret_1y=round(float(c.iloc[-1] / c.iloc[-250] - 1) * 100, 1) if len(c) > 250 else None,
                 ret_3m=round(float(c.iloc[-1] / c.iloc[-64] - 1) * 100, 1), why=r["why"][:3])
        out.append(r)
    except Exception as e:
        out.append(dict(sym=sym, error=str(e)[:80]))
print("ANALYSIS_JSON=" + json.dumps(bot.clean(out)))
