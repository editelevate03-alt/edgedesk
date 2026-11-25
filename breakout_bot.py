#!/usr/bin/env python3
"""
EdgeDesk BREAKOUT SWING bot (separate bot) - idea from tradingstrategy-ai's "ATR breakout", tested on NSE stocks.

Runs once a day after the close (~15:47 IST):
  1. Manages open swing trades with today's candle: 5% stop, trailing stop 3% below the highest price
     once the trade is up 5%, max 30 trading days. Sends 🛡️ "raise your GTT stop" and 🚪 exit messages.
  2. Scans 20 large NSE stocks for a new breakout:
     close > yesterday's close + 1.5 x ATR(10) AND ADX(14) > 15 AND +DI > -DI  -> BUY at tomorrow's open (delivery/CNC).
Sizing: risk at most 2% of capital per trade (stop = 5% below entry). Max open trades: 1 (capital < Rs 10,000) else 3.
Backtest (5y): 24 of 32 settings positive on unseen data; this setting +0.67%/trade, win 56% (unseen). NOT proven (yellow).
Not investment advice. You place every order yourself in Kite.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd

import bot
from bot import IST, fetch, log, to_daily_index
from ts_ideas import STOCKS, adx

CAPITAL = float(os.getenv("SWING_CAPITAL", "2000"))
MAX_OPEN = 1 if CAPITAL < 10_000 else 3
F, STOP, TRAIL_AFTER, TRAIL, MAX_DAYS = 1.5, 0.05, 0.05, 0.03, 30
COST = 0.0012 * 2
DP = 15.93
STATE = bot.DATA / "breakout_state.json"


def send(text):
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    ok, msg = bot.telegram(text)
    log(f"telegram: {msg} :: {text[:70]!r}")


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"open": [], "closed": [], "runs": {}}


def daily(sym):
    return to_daily_index(fetch(sym + ".NS", "1y", "1d", tries=2)[0])


def preview():
    """Intraday preview: which stocks would qualify if today's price held until the close. No state, no Telegram."""
    today = pd.Timestamp(datetime.now(IST).date())
    rows = []
    for sym in STOCKS:
        try:
            d = daily(sym)
        except Exception as e:
            print(sym, "error", e); continue
        if len(d) < 60:
            continue
        c, h, l = d.Close, d.High, d.Low
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr10 = tr.ewm(alpha=1 / 10, adjust=False).mean()
        ad, pdi, ndi = adx(h, l, c)
        jump = (c.iloc[-1] - c.iloc[-2]) / atr10.iloc[-2]
        ok = jump > F and ad.iloc[-1] > 15 and pdi.iloc[-1] > ndi.iloc[-1]
        rows.append((sym, str(d.index[-1].date()), float(c.iloc[-1]), float(jump), float(ad.iloc[-1]), bool(pdi.iloc[-1] > ndi.iloc[-1]), ok))
    rows.sort(key=lambda r: -r[3])
    print(f"PREVIEW {datetime.now(IST):%H:%M IST} (needs jump > {F} x ATR, ADX > 15, +DI > -DI)")
    for r in rows:
        print(f"{'SIGNAL' if r[6] else '      '} {r[0]:11s} bar={r[1]} price={r[2]:9.2f} jump={r[3]:+5.2f}xATR ADX={r[4]:5.1f} uptrend={r[5]}")


def main():
    import sys
    if "--preview" in sys.argv:
        return preview()
    s = load_state()
    now = datetime.now(IST)
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 15 * 60 + 35:
        log("outside the after-close window (weekdays 15:35-23:59 IST) - exiting quietly"); return
    today = pd.Timestamp(now.date())
    if s["runs"].get(str(today.date())):
        log("already ran today"); return
    data = {}
    for sym in STOCKS:
        try:
            data[sym] = daily(sym)
        except Exception as e:
            log(f"{sym}: {e}")
    fresh = [d for d in data.values() if len(d) and d.index[-1] == today]
    if len(fresh) < len(STOCKS) // 2:
        send("📈 Swing bot: <b>no market data for today</b> - probably an NSE holiday. Nothing to do.")
        s["runs"][str(today.date())] = "holiday"
        STATE.write_text(json.dumps(bot.clean(s), indent=1)); return
    msgs = []
    # ---- 1. manage open trades with today's candle ----
    still = []
    for p in s["open"]:
        d = data.get(p["sym"])
        if d is None or d.index[-1] != today:
            still.append(p); continue
        r = d.iloc[-1]
        if p.get("entry") is None:                     # bought at today's open
            p["entry"] = float(r.Open)
            p["stop"] = round(p["entry"] * (1 - STOP), 2)
            p["peak"] = p["entry"]
            msgs.append(f"✅ <b>{p['sym']}</b>: bought at ~₹{p['entry']:,.2f} (today's open). "
                        f"Set/confirm your GTT stop at <b>₹{p['stop']:,.2f}</b>.")
        p["days"] = p.get("days", 0) + 1
        exit_px, why = None, None
        if r.Open <= p["stop"]:
            exit_px, why = float(r.Open), "opened below the stop (gap down)"
        elif r.Low <= p["stop"]:
            exit_px, why = p["stop"], "stop-loss hit" if not p.get("trail_on") else "trailing stop hit (profit protected)"
        if exit_px is None:
            old = p["stop"]
            p["peak"] = max(p["peak"], float(r.High))
            if p["peak"] >= p["entry"] * (1 + TRAIL_AFTER):
                p["trail_on"] = True
                p["stop"] = round(max(p["stop"], p["peak"] * (1 - TRAIL)), 2)
            if p["stop"] > old:
                msgs.append(f"🛡️ <b>{p['sym']}: raise your GTT stop to ₹{p['stop']:,.2f}</b> (was ₹{old:,.2f}). "
                            "In Kite: GTT → your order → Modify trigger. Never let a profit turn into a loss.")
            if p["days"] >= MAX_DAYS:
                exit_px, why = float(r.Close), f"{MAX_DAYS}-day time limit - sell at tomorrow's open"
        if exit_px is not None:
            ret = exit_px / p["entry"] - 1 - COST
            pnl = ret * p["entry"] * p["qty"] - DP
            s["closed"].append(dict(sym=p["sym"], entry=p["entry"], exit=exit_px, days=p["days"], pnl=round(pnl, 1),
                                    reason=why, date=str(today.date())))
            msgs.append(f"🚪 <b>EXIT {p['sym']}</b> - {why} at ~₹{exit_px:,.2f}\n"
                        + ("Your GTT order should have sold it ✅ - check Kite → Holdings/Orders.\n" if "time limit" not in why
                           else f"In Kite: SELL {p['qty']} {p['sym']} (CNC) at tomorrow's open and delete the GTT.\n")
                        + f"Estimated result: <b>₹{pnl:+,.1f}</b> after charges.")
        else:
            still.append(p)
    s["open"] = still
    # ---- 2. scan for new breakouts ----
    held = {p["sym"] for p in s["open"]}
    cands = []
    for sym, d in data.items():
        if sym in held or len(d) < 60 or d.index[-1] != today:
            continue
        c, h, l = d.Close, d.High, d.Low
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr10 = tr.ewm(alpha=1 / 10, adjust=False).mean()
        ad, pdi, ndi = adx(h, l, c)
        jump = (c.iloc[-1] - c.iloc[-2]) / atr10.iloc[-2]
        if jump > F and ad.iloc[-1] > 15 and pdi.iloc[-1] > ndi.iloc[-1]:
            cands.append(dict(sym=sym, close=float(c.iloc[-1]), jump=float(jump), adx=float(ad.iloc[-1])))
    cands.sort(key=lambda x: -x["jump"])
    slots = MAX_OPEN - len(s["open"])
    for c in cands[:max(0, slots)]:
        price = c["close"]
        qty = math.floor(min(CAPITAL / MAX_OPEN / price, 0.02 * CAPITAL / (STOP * price)))
        if qty < 1:
            msgs.append(f"📈 Breakout in <b>{c['sym']}</b> (₹{price:,.0f}) but 1 share would risk more than 2% of ₹{CAPITAL:,.0f} - skip.")
            continue
        stop_est = price * (1 - STOP)
        s["open"].append(dict(sym=c["sym"], signal_date=str(today.date()), qty=qty, entry=None, stop=round(stop_est, 2)))
        msgs.append(f"📈 <b>SWING BUY: {c['sym']}</b> (Delivery / CNC) - buy tomorrow at 9:15\n"
                    f"Qty: <b>{qty}</b> (~₹{qty * price:,.0f}) · Today's close ₹{price:,.2f}\n"
                    f"Stop-loss: <b>5% below your buy price</b> (≈ ₹{stop_est:,.2f}, max loss ~₹{qty * price * STOP:,.0f})\n\n"
                    f"Buy zone: ₹{price * 0.99:,.2f} – ₹{price * 1.015:,.2f} (opens higher = don't chase · lower = skip)\n\n"
                    f"<b>Do in Kite:</b>\n1) <code>BUY {qty} {c['sym']} CNC LIMIT {price * 1.015:.2f}</code> (tomorrow 9:15; cancel if not filled by 9:30)\n"
                    f"2) Then <b>GTT</b> → Single → <code>SELL {qty} {c['sym']} trigger {stop_est:,.2f}</code>\n"
                    f"The bot will tell you when to raise the stop. Max hold {MAX_DAYS} trading days.\n"
                    f"Why: jumped {c['jump']:.1f}× its normal daily move in an uptrend (ADX {c['adx']:.0f}).\n"
                    "<i>Not proven · not investment advice.</i>")
    if not msgs:
        msgs.append(f"📈 Swing scan ({today.date()}): no breakout today · open trades: {len(s['open'])}.")
    tr = s["closed"]
    if tr:
        w = sum(1 for t in tr if t["pnl"] > 0)
        msgs.append(f"📒 Swing record: {len(tr)} closed · {w} wins · total ₹{sum(t['pnl'] for t in tr):+,.1f} · open {len(s['open'])}")
    send("\n\n".join(msgs))
    s["runs"][str(today.date())] = "ok"
    STATE.write_text(json.dumps(bot.clean(s), indent=1))


if __name__ == "__main__":
    main()
