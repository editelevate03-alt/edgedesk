#!/usr/bin/env python3
"""
EdgeDesk GAP-FADE bot (intraday stocks, Zerodha MIS) - separate from the ORB bot.

Rule (15-min candles): a stock opens > 1% away from yesterday's close (gap), and the FIRST 15-min candle
moves back against the gap -> trade the gap closing:
   gap UP + first candle red   -> SHORT (sell first, buy back)   target = yesterday's close, stop = first candle high
   gap DOWN + first candle green -> BUY                           target = yesterday's close, stop = first candle low
Exit by 3:10 PM at the latest. Max 2 signals per day. Quantity for Rs 2,000, no leverage.

LIVE mode: alerts include exact Kite steps; the user places every order. Monthly stop at Rs 1,600 (-20%).
Runs ~09:18 -> 15:20 IST, checks every minute, keeps an estimated record (data/gapfade_state.json).
Not proven (60-day test: 44 trades, win 57%). Not investment advice.
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
MONTH_FLOOR = float(os.getenv("GAPFADE_MONTH_FLOOR", "1600"))   # stop for the month below this (−20%)
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
    month = today[:7]
    month_pnl = sum(t["pnl"] for t in s["trades"] if t["date"].startswith(month))
    balance = CAPITAL + month_pnl
    halted = balance <= MONTH_FLOOR
    if not day.get("hello"):
        send(f"🔁 <b>EdgeDesk GAP-FADE · LIVE mode</b> ({today}) watching {len(STOCKS)} NIFTY stocks.\n"
             f"Estimated account this month: <b>₹{balance:,.0f}</b> (start ₹{CAPITAL:,.0f}, stop level ₹{MONTH_FLOOR:,.0f}).\n"
             + (f"🛑 <b>STOP FOR THE MONTH</b> - the account is at or below ₹{MONTH_FLOOR:,.0f}. No trades until next month. "
                "Take a break and review the trades with me." if halted else
                f"First alerts after the 9:30 candle · max {MAX_SIGNALS} trades · 2% risk (₹{MAX_RISK:,.0f}) · exit by 3:10 PM.")
             + "\n<i>Not investment advice. Results not guaranteed. You place every order yourself in Kite.</i>")
        day["hello"] = True
        save(s)
    if halted:
        return
    while True:
        now = now_ist()
        mins = now.hour * 60 + now.minute
        if mins >= END_MIN:
            break
        # ---- entries: once, right after the first 15-min candle (09:30) ----
        if not day.get("scanned") and mins >= 9 * 60 + 31:
            cands, have_data = [], 0
            for sym in STOCKS:
                try:
                    pc, b = bars(sym, now)
                except Exception as e:
                    log(f"{sym}: {e}"); continue
                if pc is None or len(b) < 1:
                    continue
                have_data += 1
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
            log(f"scan at {now:%H:%M}: {have_data}/{len(STOCKS)} stocks had today's first candle, {len(cands)} setups")
            if have_data < len(STOCKS) // 2:
                if mins < 9 * 60 + 50:            # first candle not published yet - try again next minute
                    time.sleep(60)
                    continue
                send("📅 Gap-fade: <b>no market data for today</b> - probably an NSE holiday (or a data delay). No trade.")
                day["scanned"] = True
                save(s)
                break
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
                first, second = ("SELL", "BUY") if c["side"] == -1 else ("BUY", "SELL")
                why = "went UP more than 1% at the open, then started falling" if c["side"] == -1 else "went DOWN more than 1% at the open, then started rising"
                sl_why = "highest price of 9:15-9:30" if c["side"] == -1 else "lowest price of 9:15-9:30"
                send(f"🔔 <b>TRADE: {first} {c['sym']}</b>\n"
                     f"Qty: <b>{qty}</b>\n"
                     f"Stop-loss: <b>{c['stop']:.2f}</b> (= {sl_why}; max loss ₹{risk_rs:,.0f})\n"
                     f"Target: <b>{c['target']:.2f}</b> (yesterday's close; profit ~₹{gain_rs:,.0f})\n\n"
                     f"<b>Do in Kite (all Intraday / MIS):</b>\n"
                     f"1) <code>{first} {qty} {c['sym']} MARKET</code>\n"
                     f"2) <code>{second} {qty} {c['sym']} SL-M trigger {c['stop']:.2f}</code>\n"
                     f"3) <code>{second} {qty} {c['sym']} LIMIT {c['target']:.2f}</code>\n"
                     f"When 2) or 3) fills, cancel the other. Close everything by 3:10 PM.\n\n"
                     f"Why: {c['sym']} {why}.\n"
                     f"Skip it if the price is already far from {c['entry']:.2f}. Never add more if it goes against you.\n"
                     "<i>Not proven · not investment advice.</i>")
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
            last_lock = t.get("lock_sent", 0.0)
            if not reason and best >= 1.0 and (last_lock == 0.0 or best >= last_lock + 0.5):
                first_msg = last_lock == 0.0
                send(f"🛡️ <b>{t['sym']}: move your stop-loss to ₹{stop_now:,.2f}</b> "
                     f"({'profit protection starts' if first_msg else 'raise it again'} - trade is up {best:.1f}R).\n"
                     f"In Kite: Orders → your SL order → Modify → trigger ₹{stop_now:,.2f} → Modify. "
                     "Never let this profit turn into a loss.")
                t["lock_sent"] = best
            if not reason and mins >= EXIT_MIN and len(b):
                reason, px = "3:10 PM time exit", float(b.Close.iloc[-1])
            if not reason:
                still.append(t); continue
            pnl = (t["side"] * (px - t["entry"]) - COST_RT * t["entry"]) * t["qty"]
            s["trades"].append(dict(date=today, sym=t["sym"], side=t["side"], reason=reason, pnl=round(pnl, 1)))
            close_act = "BUY back" if t["side"] == -1 else "SELL"
            send(f"🚪 <b>EXIT {t['sym']}</b> ({'short' if t['side'] == -1 else 'long'}) - {reason} at ~₹{px:,.2f}\n"
                 + ("If your stop/target order already filled: done ✅ - cancel any leftover order.\n" if "time" not in reason else
                    f"In Kite: <b>{close_act} {t['qty']} {t['sym']}</b> at market (MIS) now, and cancel the SL order.\n")
                 + f"Estimated result: <b>₹{pnl:+,.1f}</b> after charges (your real P&L is in Kite → Positions).")
        day["open"] = still
        save(s)
        if day.get("scanned") and not day["open"]:
            break
        time.sleep(60)
    tr = s["trades"]
    if tr and not day.get("summary"):
        w = sum(1 for t in tr if t["pnl"] > 0)
        mp = sum(t["pnl"] for t in tr if t["date"].startswith(month))
        send(f"📒 <b>Gap-fade record</b> (bot estimate): {len(tr)} trades · {w} wins ({w / len(tr) * 100:.0f}%) · total ₹{sum(t['pnl'] for t in tr):+,.1f}\n"
             f"This month: ₹{mp:+,.1f} → estimated account <b>₹{CAPITAL + mp:,.0f}</b> (stop level ₹{MONTH_FLOOR:,.0f})"
             + ("\n<i>After 30 trades we review the record together.</i>" if len(tr) < 30 else ""))
        day["summary"] = True
    save(s)


if __name__ == "__main__":
    main()
