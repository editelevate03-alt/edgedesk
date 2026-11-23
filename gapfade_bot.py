#!/usr/bin/env python3
"""
EdgeDesk GAP-FADE bot (intraday stocks, Zerodha MIS) - separate from the ORB bot.

Rule (15-min candles): a stock opens > 1% away from yesterday's close (gap), and the FIRST 15-min candle
moves back against the gap -> trade the gap closing:
   gap UP + first candle red   -> SHORT (sell first, buy back)   target = yesterday's close, stop = first candle high
   gap DOWN + first candle green -> BUY                           target = yesterday's close, stop = first candle low
Exit by 3:10 PM at the latest. Max 2 signals per day. Quantity for Rs 2,000, no leverage.

Runs ~09:20 -> 15:20 IST, checks every minute, sends Telegram alerts, keeps its own paper record
(data/gapfade_state.json). Yellow strategy (60-day test: 44 trades, win 57%, not proven). Not investment advice.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta

import pandas as pd

import bot
from bot import IST, fetch, log
from intraday_research import CAPITAL, COST_RT, STOCKS

STATE = bot.DATA / "gapfade_state.json"
MAX_SIGNALS = 2
MAX_RISK = 0.02 * CAPITAL       # Sperandeo rule 3: a stop-out costs at most 2% of capital (Rs 40 on Rs 2,000)
EXIT_MIN = 15 * 60 + 10
END_MIN = 15 * 60 + 20


def now_ist():
    return datetime.now(IST)


def load():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"trades": [], "days": {}}


def save(s):
    STATE.write_text(json.dumps(bot.clean(s), indent=1))


def send(text):
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    ok, msg = bot.telegram(text)
    log(f"telegram: {msg} :: {text[:70]!r}")


def bars(sym, now):
    df, _ = fetch(sym + ".NS", "5d", "15m", tries=2)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(IST)
    prev = df[[t.date() < now.date() for t in df.index]]
    today = df[[t.date() == now.date() and t + timedelta(minutes=15) <= now - timedelta(seconds=30) for t in df.index]]
    return (float(prev.Close.iloc[-1]) if len(prev) else None), today


def main():
    s = load()
    now = now_ist()
    if now.weekday() >= 5:
        return
    today = now.date().isoformat()
    day = s["days"].setdefault(today, {"open": []})
    if not day.get("hello"):
        send(f"🔁 <b>EdgeDesk GAP-FADE bot</b> ({today}) watching {len(STOCKS)} NIFTY stocks.\n"
             f"First alerts after the 9:30 candle. Max {MAX_SIGNALS} trades · Rs {CAPITAL:,.0f} per trade, no leverage · exit by 3:10 PM.\n"
             "<i>Separate from the BANK NIFTY ORB bot. Yellow (not proven) - paper trade first. Not investment advice.</i>")
        day["hello"] = True
        save(s)
    while True:
        now = now_ist()
        mins = now.hour * 60 + now.minute
        if mins >= END_MIN:
            break
        # ---- entries: once, right after the first 15-min candle (09:30) ----
        if not day.get("scanned") and mins >= 9 * 60 + 31:
            cands = []
            for sym in STOCKS:
                try:
                    pc, b = bars(sym, now)
                except Exception as e:
                    log(f"{sym}: {e}"); continue
                if pc is None or len(b) < 1:
                    continue
                f = b.iloc[0]
                gap = f.Open / pc - 1
                if abs(gap) < 0.01:
                    continue
                fade = (gap > 0 and f.Close < f.Open) or (gap < 0 and f.Close > f.Open)
                if not fade:
                    continue
                side = -1 if gap > 0 else 1
                entry = float(f.Close)
                stop = float(f.High if side == -1 else f.Low)
                target = pc
                risk = side * (entry - stop)
                reward = side * (target - entry)
                if risk <= entry * 0.001 or reward <= 0:
                    continue
                cands.append(dict(sym=sym, side=side, gap=gap, entry=entry, stop=stop, target=target, rr=reward / risk))
                time.sleep(0.3)
            cands.sort(key=lambda c: -c["rr"])
            day["scanned"] = True
            if not cands:
                send("🔁 Gap-fade: no stock gapped >1% and turned back in the first candle - <b>no trade today</b>.")
            for c in cands[:MAX_SIGNALS]:
                qty = min(math.floor(CAPITAL / c["entry"]), math.floor(MAX_RISK / abs(c["entry"] - c["stop"])))
                if qty < 1:
                    send(f"🔁 Gap-fade {c['sym']}: setup found but even 1 share would risk more than ₹{MAX_RISK:,.0f} (2% rule) - <b>skip</b>.")
                    continue
                risk_rs = abs(c["entry"] - c["stop"]) * qty
                gain_rs = abs(c["target"] - c["entry"]) * qty
                act = "SHORT (sell first)" if c["side"] == -1 else "BUY"
                send(f"🔁 <b>GAP-FADE: {act} {c['sym']}</b> (intraday / MIS)\n"
                     f"Gap {c['gap'] * 100:+.1f}% and the first candle turned back.\n"
                     f"Entry ~ ₹{c['entry']:,.2f} · Qty <b>{qty}</b> (~₹{qty * c['entry']:,.0f})\n"
                     f"🛑 Stop-loss ₹{c['stop']:,.2f} (risk ~₹{risk_rs:,.0f}) · 🎯 Target ₹{c['target']:,.2f} (yesterday's close, ~₹{gain_rs:,.0f})\n"
                     f"Exit by 3:10 PM if neither is hit. Place the stop-loss order right after entry.\n"
                     f"Rules: max risk 2% (₹{MAX_RISK:,.0f}) · at +1R move stop to breakeven · never give back >50% of the gain · <b>never add to a loser</b>.\n"
                     "<i>Yellow (not proven) · alerts only · not investment advice.</i>")
                day["open"].append(dict(c, qty=qty, time=now.isoformat()))
            save(s)
        # ---- exits ----
        still = []
        for t in day["open"]:
            try:
                _, b = bars(t["sym"], now)
            except Exception:
                still.append(t); continue
            after = b[b.index > pd.Timestamp(t["time"]) - timedelta(minutes=1)]
            reason, px = None, None
            risk0 = abs(t["entry"] - t["stop"])
            stop_now, best = t["stop"], 0.0
            for _, r in after.iterrows():
                adverse = r.High if t["side"] == -1 else r.Low
                fav = r.Low if t["side"] == -1 else r.High
                if t["side"] * (adverse - stop_now) <= 0:
                    reason = "stop-loss" if stop_now == t["stop"] else "protected stop (kept profit)"
                    px = stop_now; break
                if t["side"] * (fav - t["target"]) >= 0:
                    reason, px = "target (gap filled)", t["target"]; break
                best = max(best, t["side"] * (fav - t["entry"]) / risk0)          # Sperandeo rule 2 (after the bar)
                if best >= 1.0:
                    new_stop = t["entry"] + t["side"] * 0.5 * best * risk0
                    if t["side"] * (new_stop - stop_now) > 0:
                        stop_now = new_stop
            if not reason and best >= 1.0 and not t.get("be_sent"):
                send(f"🛡️ <b>{t['sym']}: move your stop-loss to ₹{stop_now:,.2f}</b> (profit protection - trade is up {best:.1f}R). "
                     "Never let this profit turn into a loss.")
                t["be_sent"] = True
            if not reason and mins >= EXIT_MIN and len(b):
                reason, px = "3:10 PM time exit", float(b.Close.iloc[-1])
            if not reason:
                still.append(t); continue
            pnl = (t["side"] * (px - t["entry"]) - COST_RT * t["entry"]) * t["qty"]
            s["trades"].append(dict(date=today, sym=t["sym"], side=t["side"], reason=reason, pnl=round(pnl, 1)))
            send(f"🚪 <b>EXIT {t['sym']}</b> ({'short' if t['side'] == -1 else 'long'}) - {reason} at ~₹{px:,.2f}\n"
                 f"Estimated result: <b>₹{pnl:+,.1f}</b> after charges (check your real P&L in Kite).")
        day["open"] = still
        save(s)
        if day.get("scanned") and not day["open"]:
            break
        time.sleep(60)
    tr = s["trades"]
    if tr and not day.get("summary"):
        w = sum(1 for t in tr if t["pnl"] > 0)
        send(f"📒 <b>Gap-fade paper record</b>: {len(tr)} trades · {w} wins ({w / len(tr) * 100:.0f}%) · total ₹{sum(t['pnl'] for t in tr):+,.1f}"
             + ("\n<i>Need 30+ trades before judging.</i>" if len(tr) < 30 else ""))
        day["summary"] = True
    save(s)


if __name__ == "__main__":
    main()
