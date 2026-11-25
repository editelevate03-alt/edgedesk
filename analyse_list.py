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


def note(r):
    """Plain-language holding check (no personal amounts)."""
    if "error" in r:
        return f"❓ <b>{r['sym']}</b>: no data on Yahoo (new listing / SME / corporate event) - check in Kite."
    p, lo, hi, e200 = r["price"], r["lo52"], r["hi52"], r["ema200"]
    pos = (p - lo) / (hi - lo) * 100 if hi > lo else 50
    trend = "🟢 uptrend (above 200-day avg)" if r["above_200"] else "🔴 downtrend (below 200-day avg)"
    tips = []
    if "penny price" in r.get("flags", []):
        tips.append("penny stock (under ₹50) - easy to manipulate, don't add more")
    if not r["above_200"]:
        tips.append(f"don't average down; a rebound needs a close back above ₹{e200:,.2f} (200-day avg)")
        if pos < 10:
            tips.append(f"near its 1-year low ₹{lo:,.2f} - if it breaks below, the fall may continue; decide your exit level now")
    else:
        tips.append(f"protect it: consider a GTT stop near ₹{min(e200, p * 0.95):,.2f}")
    if r.get("breakout"):
        tips.append("broke out today")
    return (f"<b>{r['sym']}</b> ₹{p:,.2f} · {r['grade']} · {trend}\n"
            f"3 months {r['ret_3m']:+.0f}% · 1 year {r['ret_1y'] if r['ret_1y'] is not None else '-'}% · "
            f"1-yr range ₹{lo:,.2f}-₹{hi:,.2f} (at {pos:.0f}% of range)\n→ " + "; ".join(tips))


if os.getenv("SEND_TG") == "1":
    msg = ["📋 <b>Your holdings check</b> (live market data · no amounts stored anywhere)", ""]
    msg += [note(r) + "\n" for r in out]
    msg.append("<b>Rules:</b> never average down · protect winners with a GTT stop · penny & downtrend stocks: no new money · "
               "your exact P&L is in Kite.\n<i>Rule-based analysis, not investment advice.</i>")
    st = {}
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        pass
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    text = "\n".join(msg)
    for i in range(0, len(text), 3800):
        ok, m = bot.telegram(text[i:i + 3800])
    print("telegram:", m)               # the report itself is NOT printed (public logs)
else:
    print("ANALYSIS_JSON=" + json.dumps(bot.clean(out)))
