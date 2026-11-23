#!/usr/bin/env python3
"""
EdgeDesk ORB bot - BANK NIFTY 15-minute opening-range breakout, sent to Telegram.

Runs once per trading day as a long job (~09:27 to 15:20 IST) and checks every minute:
  09:45  range = high/low of the first two 15-min candles  -> Telegram "range set"
  <13:00 first 15-min CLOSE above high -> BUY CE alert, below low -> BUY PE alert
         (suggested strikes whose 1 lot fits your capital, estimated premiums, stop-loss, target)
  then   EXIT alerts: index target (2 x range), index stop (other side of range), or 15:15 time exit
Rules: 1 trade/day, no entry after 13:00, pause after 2 losing days in a row, expiry-day warning.

It does NOT place orders. Option premiums are ESTIMATES (Black-Scholes + India VIX) - always check
the real price in Kite's option chain. Yellow strategy (not proven). Not investment advice.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

import bot
import fno_research as F
from bot import IST, fetch, log

U = F.UNDERLYINGS["BANKNIFTY"]
CAPITAL = F.CAPITAL
STATE = bot.DATA / "orb_state.json"
END_MIN = 15 * 60 + 20
LAST_ENTRY_MIN = 13 * 60
EXIT_MIN = 15 * 60 + 15


def now_ist():
    return datetime.now(IST)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"trades": []}


def save_state(s):
    STATE.write_text(json.dumps(bot.clean(s), indent=1))


def send(text, state):
    try:
        main_state = json.loads((bot.DATA / "state.json").read_text())   # chat id saved by the main bot
    except Exception:
        main_state = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(main_state)
    ok, msg = bot.telegram(text)
    log(f"telegram: {msg} :: {text[:80]!r}")
    return ok


def bars_today(now):
    df, _ = fetch(U["ticker"], "5d", "15m", tries=2)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(IST)
    df = df[[t.date() == now.date() for t in df.index]]
    df = df[[t + timedelta(minutes=15) <= now - timedelta(seconds=30) for t in df.index]]   # finished candles only
    return df


def vix_now():
    try:
        v, _ = fetch("^INDIAVIX", "5d", "1d", tries=2)
        return float(v["Close"].iloc[-1])
    except Exception:
        return 14.0


def expiry_for(now):
    t = pd.Timestamp(now)
    exp = F.next_expiry(t, "monthly")
    note = ""
    if exp.date() == t.date():                      # expiry day: use next month, warn
        exp = F.next_expiry(t + pd.Timedelta(days=1), "monthly")
        note = "⚠️ Today is BANK NIFTY expiry - using NEXT month's contract. Expiry days are extra risky."
    return exp, note


def strike_ideas(S, cp, now, iv):
    exp, note = expiry_for(now)
    T = F.years(pd.Timestamp(now), exp)
    atm = round(S / U["step"]) * U["step"]
    rows, fit = [], None
    for k in range(0, 80):
        K = atm + cp * k * U["step"]
        p = F.bs(S, K, T, iv / 100, cp)
        lot_cost = p * U["lot"]
        if fit is None and lot_cost <= CAPITAL:
            fit = (K, p)
        if k == 0 or (fit and K == fit[0]):
            rows.append((K, p, lot_cost))
        if fit:
            break
    return exp, note, atm, rows, fit


def is_trading_loss(t):
    return (t.get("est_pnl") or 0) < 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single check (for testing)")
    args = ap.parse_args()
    state = load_state()
    now = now_ist()
    today = now.date().isoformat()
    if now.weekday() >= 5:
        log("weekend - nothing to do"); return
    day = state.setdefault("days", {}).setdefault(today, {})
    recent = [t for t in state["trades"] if t["date"] < today][-2:]
    paused = len(recent) == 2 and all(is_trading_loss(t) for t in recent)
    if not day.get("hello"):
        send(f"🌅 <b>EdgeDesk ORB bot is watching BANK NIFTY</b> ({today})\n"
             f"Range = first two 15-min candles (9:15-9:45). Entry only before 1:00 PM. Capital Rs {CAPITAL:,.0f}.\n"
             + ("⏸️ <b>Paused today: 2 losing days in a row.</b> Watch only - no trade." if paused else "")
             + "\n<i>Yellow strategy (not proven). Alerts only - you decide and place orders in Kite. Not investment advice.</i>", state)
        day["hello"] = True
        save_state(state)

    while True:
        now = now_ist()
        mins = now.hour * 60 + now.minute
        if mins >= END_MIN:
            break
        try:
            b = bars_today(now)
        except Exception as e:
            log(f"data error: {e}")
            b = pd.DataFrame()
        if len(b) == 0 and mins >= 10 * 60:
            if not day.get("closed_msg"):
                send("📅 No BANK NIFTY data today - market holiday or data problem. No trade.", state)
                day["closed_msg"] = True
                save_state(state)
            break
        if len(b) >= 2 and "range_hi" not in day:
            hi, lo = float(b.High.iloc[:2].max()), float(b.Low.iloc[:2].min())
            day.update(range_hi=hi, range_lo=lo)
            send(f"📏 <b>BANK NIFTY range set</b> (9:15-9:45)\nHigh <b>{hi:,.0f}</b> · Low <b>{lo:,.0f}</b> · size {hi - lo:,.0f} pts\n"
                 f"15-min close above {hi:,.0f} → BUY CE · below {lo:,.0f} → BUY PE (until 1:00 PM).", state)
            save_state(state)
        if "range_hi" in day and "entry" not in day and not day.get("no_trade") and not paused:
            hi, lo = day["range_hi"], day["range_lo"]
            for t, r in b.iloc[2:].iterrows():
                close_min = t.hour * 60 + t.minute + 15
                if close_min > LAST_ENTRY_MIN:
                    break
                cp = 1 if r.Close > hi else (-1 if r.Close < lo else 0)
                if cp == 0:
                    continue
                S = float(r.Close)
                rng = hi - lo
                stop_idx = lo if cp == 1 else hi
                tgt_idx = S + cp * 2 * rng
                iv = vix_now()
                exp, note, atm, rows, fit = strike_ideas(S, cp, now, iv)
                side = "CE" if cp == 1 else "PE"
                lines = [f"🔔 <b>BUY BANK NIFTY {side}</b> - breakout {'UP' if cp == 1 else 'DOWN'} at {S:,.0f} ({t.strftime('%H:%M')} candle)",
                         f"Expiry: <b>{exp.strftime('%d %b %Y')}</b> (monthly)"]
                if note:
                    lines.append(note)
                for K, p, c in rows:
                    tag = "ATM" if K == atm else "fits your Rs " + f"{CAPITAL:,.0f}"
                    lines.append(f"• {int(K)} {side}: est. premium ~Rs {p:,.0f} → 1 lot ({U['lot']}) ~Rs {c:,.0f} [{tag}]")
                if not fit:
                    lines.append(f"❌ No strike fits your Rs {CAPITAL:,.0f} today (monthly premiums too high this early in the month) - <b>SKIP, no trade</b>.")
                else:
                    K, p = fit
                    away = abs(K - S) / S * 100
                    if away > 2.5:
                        lines.append(f"⚠️ This strike is {away:.1f}% away from the price - a lottery ticket that usually expires worthless. "
                                     "Safer choice: <b>skip</b> and wait for the last 1-2 weeks before expiry.")
                    lines += [f"👉 Check the REAL premium of <b>{int(K)} {side}</b> in Kite. Only buy if 1 lot costs ≤ Rs {CAPITAL:,.0f}.",
                              f"🛑 Stop-loss: premium −40% (e.g. buy Rs {p:,.0f} → SL Rs {p * 0.6:,.0f}). Place it right after buying.",
                              f"🎯 Exit if BANK NIFTY reaches <b>{tgt_idx:,.0f}</b> · Exit if it goes back past <b>{stop_idx:,.0f}</b> · Exit by 3:15 PM."]
                lines.append("<i>Yellow (not proven) · ~1 in 3 trades won in tests · alerts only · not investment advice.</i>")
                send("\n".join(lines), state)
                if not fit:
                    day["no_trade"] = True
                    save_state(state)
                    break
                day["entry"] = dict(time=t.isoformat(), cp=cp, S=S, stop_idx=stop_idx, tgt_idx=tgt_idx,
                                    K=fit[0], prem=fit[1], iv=iv, exp=exp.isoformat())
                save_state(state)
                break
            if "entry" not in day and mins >= LAST_ENTRY_MIN + 15:
                day["no_trade"] = True
                send("😴 No breakout before 1:00 PM - <b>no trade today</b>. That's a good outcome too.", state)
                save_state(state)
        e = day.get("entry")
        if e and "exit" not in day:
            et = pd.Timestamp(e["time"])
            after = b[b.index > et]
            reason, px = None, None
            for t, r in after.iterrows():
                adverse = r.Low if e["cp"] == 1 else r.High
                favour = r.High if e["cp"] == 1 else r.Low
                if e["cp"] * (adverse - e["stop_idx"]) <= 0:
                    reason, px = "index went back through the range (stop)", e["stop_idx"]; break
                if e["cp"] * (favour - e["tgt_idx"]) >= 0:
                    reason, px = "target reached", e["tgt_idx"]; break
            if not reason and mins >= EXIT_MIN and len(b):
                reason, px = "3:15 PM time exit", float(b.Close.iloc[-1])
            if reason:
                est = None
                if e.get("K"):
                    T = F.years(pd.Timestamp(now), pd.Timestamp(e["exp"]))
                    exit_p = F.bs(px, e["K"], T, e["iv"] / 100, e["cp"])
                    exit_p = max(exit_p, e["prem"] * 0.6)          # your -40% premium SL caps the loss
                    est = round((exit_p - e["prem"]) * U["lot"] - F.COST_FIXED, 0)
                day["exit"] = dict(reason=reason, index=px, est_pnl=est, time=now.isoformat())
                state["trades"].append(dict(date=today, cp=e["cp"], reason=reason, est_pnl=est))
                send(f"🚪 <b>EXIT NOW - BANK NIFTY {'CE' if e['cp'] == 1 else 'PE'}</b>\nReason: {reason} (index ~{px:,.0f}).\n"
                     + (f"Estimated result for 1 lot: <b>Rs {est:+,.0f}</b> (check your real P&L in Kite)." if est is not None else ""), state)
                save_state(state)
        if args.once or ("exit" in day) or day.get("no_trade") or (paused and mins >= LAST_ENTRY_MIN):
            break
        time.sleep(60)

    tr = state["trades"]
    if tr and not day.get("summary"):
        wins = sum(1 for t in tr if (t.get("est_pnl") or 0) > 0)
        total = sum(t.get("est_pnl") or 0 for t in tr)
        send(f"📒 <b>ORB paper record</b>: {len(tr)} trades · {wins} wins ({wins / len(tr) * 100:.0f}%) · total est. Rs {total:+,.0f}", state)
        day["summary"] = True
    save_state(state)


if __name__ == "__main__":
    main()
