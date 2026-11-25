#!/usr/bin/env python3
"""Analyse a list of NSE symbols (from the SYMBOLS env var) with EdgeDesk's engine. Prints JSON.
No quantities or buy prices - only public market data."""
import json, os
from datetime import datetime
import bot
from bot import IST, fetch, to_daily_index
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


def simple_note(r):
    """Very plain language, for family members who don't trade."""
    if "error" in r:
        return f"❓ <b>{r['sym']}</b> - no market data available. Please check this one in the Kite app."
    p, e200, lo = r["price"], r["ema200"], r["lo52"]
    penny = p < 50
    if r["above_200"] and r["ret_3m"] > 0:
        head = "👍 Doing well"
        tip = f"Keep it. Put a safety sell order (stop-loss) at about ₹{min(e200, p * 0.95):,.0f} so profit is protected."
    elif r["above_200"]:
        head = "🙂 Okay"
        tip = f"Keep watching. Safety sell order (stop-loss) at about ₹{min(e200, p * 0.95):,.0f}."
    else:
        head = "👎 Weak - price has been falling"
        tip = "Don't buy more of it now. Decide a price at which you will sell if it keeps falling" + \
              (f" (it is near its 1-year low ₹{lo:,.0f})." if (p - lo) / max(r['hi52'] - lo, 1e-9) < 0.1 else ".")
    extra = " ⚠️ Very cheap 'penny' share - risky, prices can be pushed up and down." if penny else ""
    return f"<b>{r['sym']}</b> (₹{p:,.2f}) - {head}.\n   Last 3 months: {r['ret_3m']:+.0f}%. {tip}{extra}"


def simple_report(rows):
    good = [r["sym"] for r in rows if "error" not in r and r["above_200"]]
    weak = [r["sym"] for r in rows if "error" not in r and not r["above_200"]]
    L = ["👨‍👩‍👦 <b>Portfolio report - simple version</b>",
         f"Date: {datetime.now(IST):%d %b %Y}", "",
         "<b>In short:</b>",
         f"👍 Going up: {', '.join(good) or 'none'}",
         f"👎 Falling: {', '.join(weak) or 'none'}", "",
         "<b>Each share:</b>"]
    L += [simple_note(r) for r in rows]
    L += ["", "<b>3 simple rules we follow:</b>",
          "1️⃣ Never buy more of a share that keeps falling (no 'averaging down').",
          "2️⃣ Protect profits with a safety sell order (stop-loss / GTT in Kite).",
          "3️⃣ Put regular monthly savings into a NIFTY 50 index fund - the safest long-term growth.",
          "", "<i>Made by our EdgeDesk tool from live market data. This is not professional investment advice.</i>"]
    return "\n".join(L)


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
    if os.getenv("STYLE") == "simple":
        msg = [simple_report(out)]
    else:
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
