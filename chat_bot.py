#!/usr/bin/env python3
"""
EdgeDesk CHAT BOT - talk to "My Trading Alerts" on Telegram. No AI account needed: a rule-based brain that
answers from EdgeDesk data and checks any NSE stock live (Yahoo Finance).

Runs as a long GitHub Actions job (started ~9:05 and ~15:40 IST, up to ~6 hours each) and replies within seconds.
Only answers the owner's chat. Never places trades. Not investment advice.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

import bot
from bot import IST, fetch, log, to_daily_index

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
CHAT_STATE = bot.DATA / "chat_state.json"
BUDGET = float(os.getenv("SCAN_BUDGET", "2000"))

GLOSSARY = {
    "stoploss": "A stop-loss is the price where you exit to stop a bigger loss. EdgeDesk swing trades use 5% below your buy price; gap-fade uses the first 15-minute candle's high/low. Always place it right after buying.",
    "gtt": "GTT (Good Till Triggered) is a Kite order that waits up to a year. For Delivery trades use GTT → Single → SELL with the trigger at your stop-loss. It sells automatically if the price falls there.",
    "breakout": "A breakout is when a stock closes much higher than its normal daily move (more than 1.5× ATR) while in an uptrend. EdgeDesk's swing rule buys at the next day's open.",
    "atr": "ATR (Average True Range) is a stock's normal daily movement in rupees. A move bigger than 1.5× ATR is unusual - that is our breakout signal.",
    "adx": "ADX measures trend strength: below 15 = no trend, 25+ = strong trend, 40+ = very strong.",
    "cnc": "CNC = Delivery. You own the shares and can hold them for days or weeks (swing trades).",
    "mis": "MIS = Intraday. The trade must be closed the same day (Kite auto-closes around 3:20 PM). Allows selling first (short).",
    "amo": "AMO = After Market Order. Place it in the evening; it is sent to the exchange at 9:15 next morning.",
    "gap": "A gap is when a stock opens far above or below yesterday's close. Gap-fade trades a gap of 1%+ that starts turning back in the first 15 minutes.",
    "swing": "Swing trading = holding a stock for a few days to weeks to catch one move. Lower costs than intraday, needs a stop-loss (GTT).",
    "intraday": "Intraday = buy and sell on the same day. Small moves, more trades, costs matter more.",
    "averaging": "Averaging down = buying more of a losing stock. EdgeDesk rule: NEVER do it - it turns small losses into big ones.",
    "penny": "Penny stocks (under ₹50) are easy to manipulate and hard to sell. EdgeDesk always avoids them.",
    "rr": "Risk/reward: how much you can lose vs. make. With a 5% stop, risk 2% of capital max per trade.",
}
HELP = ("🤖 <b>EdgeDesk bot - what you can send</b>\n"
        "• <code>signals</code> - today's 🟢 SELECT stocks, swing & gap-fade\n"
        "• <code>ITC</code> or <code>stock ICICIBANK</code> - live analysis of any NSE stock\n"
        "• <code>holdings</code> - your positions with P&L and stop-loss\n"
        "• <code>hold TCS 2500 1</code> - track a new holding (symbol, buy price, qty)\n"
        "• <code>sold TCS</code> - stop tracking a holding\n"
        "• <code>record</code> - bot trade records\n"
        "• <code>rules</code> - what to select / avoid\n"
        "• <code>learn GTT</code> (also: stoploss, breakout, atr, adx, cnc, mis, amo, gap, swing, averaging, penny, rr)\n"
        "<i>I never place trades. Not investment advice.</i>")


def tg(method, **params):
    r = requests.post(f"{API}/{method}", json=params, timeout=70)
    return r.json()


def reply(chat, text):
    for i in range(0, len(text), 3900):
        tg("sendMessage", chat_id=chat, text=text[i:i + 3900], parse_mode="HTML", disable_web_page_preview=True)


def load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def refresh_repo():
    try:
        subprocess.run(["git", "pull", "-q", "--rebase", "--autostash", "-X", "theirs"], timeout=30, check=False)
    except Exception:
        pass


def owner_chat():
    st = load(bot.DATA / "state.json", {})
    return str(os.getenv("TELEGRAM_CHAT_ID", "").strip() or st.get("telegram_chat_id", ""))


# ---------------- answers ----------------
def analyse_stock(sym: str) -> str:
    from stock_scanner import analyse, select_card
    sym = sym.upper().replace(".NS", "")
    try:
        d = to_daily_index(fetch(sym + ".NS", "1y", "1d", tries=2)[0])
        nif = to_daily_index(fetch("^NSEI", "1y", "1d", tries=2)[0]).Close
    except Exception:
        return f"❓ Couldn't find <b>{sym}</b> on NSE. Use the NSE symbol, e.g. RELIANCE, HDFCBANK, ITC."
    if len(d) < 70:
        return f"❓ Not enough history for {sym}."
    r = analyse(sym, d, float(nif.iloc[-1] / nif.iloc[-64] - 1))
    icon = {"SELECT": "🟢", "WATCH": "🟡", "NEUTRAL": "⚪", "AVOID": "🔴"}[r["grade"]]
    if r["grade"] == "SELECT":
        return select_card(r)
    fit = f"qty {r['qty']} fits ₹{BUDGET:,.0f}" if r["qty"] >= 1 else f"above your ₹{BUDGET:,.0f} with the 2% rule"
    return (f"{icon} <b>{sym}</b> - <b>{r['grade']}</b> · ₹{r['price']:,.2f} (last close/price)\n"
            f"Why: {r['grade_why']}\n"
            f"• Trend & strength: {', '.join(r['why'][:3])}\n"
            f"• Breakout today: {'YES' if r['breakout'] else 'no'} ({r['jump']}× normal move, needs >1.5) · ADX {r['adx']:.0f}\n"
            f"• vs NIFTY 3 months: {r['rs_pct']:+.0f}% · Daily movement {r['atr_pct']}% · Traded/day ₹{r['value_cr']:,.0f} Cr\n"
            f"• Budget: {fit}\n"
            + ("⛔ Don't buy - " + r["grade_why"] if r["grade"] == "AVOID" else
               "👀 Not a buy signal - wait until it turns 🟢 SELECT (breakout + all checks)." if r["grade"] == "WATCH" else
               "No clear setup right now.")
            + "\n<i>Not investment advice.</i>")


def signals() -> str:
    refresh_repo()
    sc = load(bot.DATA / "scanner.json", {})
    sw = load(bot.DATA / "breakout_state.json", {})
    gf = load(bot.DATA / "gapfade_state.json", {})
    today = datetime.now(IST).date().isoformat()
    L = [f"📊 <b>Signals</b> ({datetime.now(IST):%d %b %H:%M IST})"]
    if sc:
        sel = [r for r in sc.get("breakouts", []) if r.get("grade") == "SELECT"]
        L.append(f"\n🔎 Analyser ({sc.get('generated')}): " +
                 (", ".join(f"🟢 {r['sym']} ₹{r['price']:,.0f} ({'qty ' + str(r['qty']) if r['qty'] >= 1 else 'skip: budget'})" for r in sel) or "no 🟢 SELECT"))
        fits = sc.get("fits_budget", [])[:3]
        if fits:
            L.append("🟡 Watch (fits budget): " + ", ".join(f"{r['sym']} ₹{r['price']:,.0f}" for r in fits))
    else:
        L.append("\n🔎 Analyser: no scan yet (runs ~3:40 PM).")
    op = sw.get("open", [])
    L.append("📈 Swing: " + (", ".join(f"{p['sym']} qty {p['qty']} stop ₹{p['stop']}" for p in op) if op else "no open swing trade"))
    d = gf.get("days", {}).get(today)
    if not d:
        L.append("🔁 Gap-fade: not run yet today (starts 9:05, signals after 9:30)")
    else:
        L.append("🔁 Gap-fade: " + (", ".join(f"{t['sym']} {'SHORT' if t['side'] == -1 else 'BUY'} stop ₹{t['stop']}" for t in d.get("open", []))
                                   or ("scanned - no open trade" if d.get("scanned") else "watching")))
    L.append("\n<i>Trade only 🟢 / 🔔 signals that fit your budget. Not investment advice.</i>")
    return "\n".join(L)


def holdings(cs) -> str:
    hs = cs.get("holdings", {})
    if not hs:
        return "No holdings tracked. Add one: <code>hold ITC 308.60 1</code>"
    L = ["💼 <b>Your holdings</b>"]
    for sym, h in hs.items():
        try:
            d = to_daily_index(fetch(sym + ".NS", "6mo", "1d", tries=2)[0])
        except Exception:
            L.append(f"• {sym}: price unavailable"); continue
        since = d[d.index >= pd.Timestamp(h["date"])]
        price = float(d.Close.iloc[-1])
        high = float(since.High.max()) if len(since) else price
        stop = h["price"] * 0.95
        note = "5% below your buy price"
        if high >= h["price"] * 1.05:
            stop = max(stop, high * 0.97)
            note = f"3% below the highest price since buying (₹{high:,.2f}) - raise your GTT"
        pnl = (price - h["price"]) * h["qty"]
        L.append(f"• <b>{sym}</b> {h['qty']} @ ₹{h['price']:,.2f} → now ₹{price:,.2f} · P&L <b>₹{pnl:+,.1f}</b> ({(price / h['price'] - 1) * 100:+.1f}%)\n"
                 f"  GTT stop: <b>₹{stop:,.2f}</b> ({note})"
                 + ("\n  ⚠️ Price is at/below your stop - your GTT should have sold it. Check Kite." if price <= stop else ""))
    L.append("<i>Never move a stop down. Never average down.</i>")
    return "\n".join(L)


def record() -> str:
    refresh_repo()
    out = ["📒 <b>Records</b> (bot estimates)"]
    gf = load(bot.DATA / "gapfade_state.json", {}).get("trades", [])
    sw = load(bot.DATA / "breakout_state.json", {}).get("closed", [])
    orb = load(bot.DATA / "orb_state.json", {}).get("trades", [])
    for name, tr, key in (("🔁 Gap-fade", gf, "pnl"), ("📈 Swing", sw, "pnl"), ("🌅 ORB (paper)", orb, "est_pnl")):
        if tr:
            w = sum(1 for t in tr if (t.get(key) or 0) > 0)
            out.append(f"{name}: {len(tr)} trades · {w} wins ({w / len(tr) * 100:.0f}%) · ₹{sum(t.get(key) or 0 for t in tr):+,.1f}")
        else:
            out.append(f"{name}: no closed trades yet")
    out.append("<i>Your real P&L is in Kite. Judge a strategy after 30+ trades.</i>")
    return "\n".join(out)


RULES = ("📏 <b>Stock rules</b>\n🔴 <b>AVOID</b>: price < ₹50 · traded < ₹10 Cr/day · >15% jump in a day · >30% in a week · "
         "erratic >8% days · very volatile · downtrend · Telegram/WhatsApp tips\n"
         "🟢 <b>SELECT</b> (all): breakout >1.5× ATR · strong uptrend · ₹50 Cr+/day traded · 1-4% daily movement · fits budget (2% risk)\n"
         "🟡 <b>WATCH</b>: uptrend + stronger than NIFTY, no breakout yet\n"
         "Every trade: buy 9:15 in the buy zone · GTT stop −5% · raise stop when told · never average down.")


def answer(text: str, cs) -> str:
    t = text.strip()
    low = t.lower()
    words = re.findall(r"[a-z0-9&\-\.]+", low)
    if not words or low in ("/start", "/help", "help", "menu", "hi", "hello", "hlo", "hey"):
        return HELP
    w0 = words[0].lstrip("/")
    if w0 in ("signal", "signals", "today", "aaj"):
        return signals()
    if w0 in ("holding", "holdings", "portfolio", "positions"):
        return holdings(cs)
    if w0 == "hold" and len(words) >= 3:
        try:
            sym, price = words[1].upper(), float(words[2])
            qty = int(float(words[3])) if len(words) > 3 else 1
        except ValueError:
            return "Format: <code>hold SYMBOL BUYPRICE QTY</code>, e.g. <code>hold ITC 308.60 1</code>"
        cs.setdefault("holdings", {})[sym] = dict(price=price, qty=qty, date=datetime.now(IST).date().isoformat())
        return f"✅ Tracking {sym}: {qty} @ ₹{price:,.2f}. Set your GTT stop at <b>₹{price * 0.95:,.2f}</b>. Send <code>holdings</code> anytime."
    if w0 in ("sold", "remove", "unhold") and len(words) >= 2:
        cs.get("holdings", {}).pop(words[1].upper(), None)
        return f"🗑️ Stopped tracking {words[1].upper()}."
    if w0 in ("record", "records", "results", "pnl"):
        return record()
    if w0 in ("rule", "rules", "select", "avoid"):
        return RULES
    if w0 in ("learn", "what", "explain", "meaning") and len(words) >= 2:
        key = words[-1].replace("-", "").replace("stop", "stoploss") if words[-1] == "stop" else words[-1].replace("-", "")
        for k, v in GLOSSARY.items():
            if key.startswith(k[:4]) or k.startswith(key[:4]):
                return f"📚 <b>{k.upper()}</b>: {v}"
        return "📚 I can explain: " + ", ".join(GLOSSARY)
    if w0 in ("stock", "check", "analyse", "analyze", "price") and len(words) >= 2:
        return analyse_stock(words[1])
    if len(words) == 1 and re.fullmatch(r"[a-z&\-]{2,15}", words[0]):
        return analyse_stock(words[0])
    return "🤔 I didn't understand.\n\n" + HELP


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=350)
    a = ap.parse_args()
    owner = owner_chat()
    if not TOKEN or not owner:
        log("no token/owner chat - exiting"); return
    tg("deleteWebhook")
    cs = load(CHAT_STATE, {})
    cs.setdefault("holdings", {})        # private: kept in the Actions cache, never committed to the public repo
    offset = cs.get("offset")
    end = datetime.now(IST) + timedelta(minutes=a.minutes)
    log(f"chat bot listening until {end:%H:%M IST}")
    while datetime.now(IST) < end:
        try:
            r = tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message"])
        except Exception as e:
            log(f"poll error {type(e).__name__}"); time.sleep(5); continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            chat = str((m.get("chat") or {}).get("id", ""))
            text = m.get("text") or ""
            if chat != owner or not text:
                continue                                      # only the owner may use the bot
            try:
                tg("sendChatAction", chat_id=chat, action="typing")
                reply(chat, answer(text, cs))
            except Exception as e:
                reply(chat, f"⚠️ Sorry, something went wrong ({type(e).__name__}). Try again.")
            cs["offset"] = offset
            CHAT_STATE.write_text(json.dumps(bot.clean(cs), indent=1))
    cs["offset"] = offset
    CHAT_STATE.write_text(json.dumps(bot.clean(cs), indent=1))


if __name__ == "__main__":
    main()
