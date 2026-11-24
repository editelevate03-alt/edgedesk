#!/usr/bin/env python3
"""Diagnostic: what intraday NSE data does Yahoo return right now? Prints bar counts and last timestamps."""
from datetime import datetime
import bot
from bot import IST, fetch
now = datetime.now(IST)
print("now IST:", now)
for t in ["^NSEBANK", "^NSEI", "SBIN.NS", "RELIANCE.NS"]:
    for per, iv in (("5d", "15m"), ("1d", "5m"), ("1d", "1m")):
        try:
            df, src = fetch(t, per, iv, tries=1)
            idx = df.index.tz_convert(IST) if df.index.tz is not None else df.index
            today = [x for x in idx if x.date() == now.date()]
            print(f"{t:12s} {iv:4s} via {src:16s} bars={len(df):4d} today={len(today):3d} first_today={today[0] if today else '-'} last={idx[-1]}")
        except Exception as e:
            print(f"{t:12s} {iv:4s} ERROR {e}")
