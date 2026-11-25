#!/usr/bin/env python3
"""
EdgeDesk STOCK ANALYSER - daily swing-trading scan of the NIFTY 500 (fallback: ~100 large NSE stocks).

After the close it ranks stocks for tomorrow:
  A. TESTED SIGNAL - breakout rule from our backtest: close > previous close + 1.5 x ATR(10), ADX(14) > 15, +DI > -DI.
  B. WATCHLIST - strongest stocks (trend + strength vs NIFTY + near a 20-day high + volume surge + healthy volatility),
     with affordable stocks for your budget first.
SAFETY: skips price < Rs 50, average traded value < Rs 10 crore/day, and any day move > 15% (pump/operator traps).
Every stock gets a one-line "why", so you learn what a good swing setup looks like.
Only part A is a tested strategy (yellow, not proven). The watchlist is education, not a buy signal. Not investment advice.
"""
from __future__ import annotations

import io
import json
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd
import requests

import bot
from bot import IST, UA, log
from ts_ideas import adx

BUDGET = float(os.getenv("SCAN_BUDGET", "2000"))
MIN_PRICE, MIN_VALUE_CR, MAX_DAY_MOVE = 50, 10, 0.15
FALLBACK = """RELIANCE HDFCBANK ICICIBANK INFY TCS SBIN BHARTIARTL ITC LT KOTAKBANK AXISBANK HINDUNILVR BAJFINANCE MARUTI
SUNPHARMA TITAN ASIANPAINT ULTRACEMCO NTPC POWERGRID HCLTECH WIPRO TECHM NESTLEIND ONGC COALINDIA TATASTEEL JSWSTEEL
HINDALCO ADANIENT ADANIPORTS BAJAJFINSV BAJAJ-AUTO EICHERMOT HEROMOTOCO M&M GRASIM CIPLA DRREDDY DIVISLAB APOLLOHOSP
BRITANNIA TATACONSUM INDUSINDBK SBILIFE HDFCLIFE SHRIRAMFIN BEL HAL TRENT ETERNAL DMART PIDILITIND GODREJCP DABUR MARICO
COLPAL HAVELLS SIEMENS ABB DLF LODHA VEDL BPCL IOC GAIL TATAPOWER ADANIGREEN ADANIPOWER JINDALSTEL BANKBARODA PNB CANBK
UNIONBANK IDFCFIRSTB FEDERALBNK CHOLAFIN ICICIGI ICICIPRULI LICI IRFC RECLTD PFC TVSMOTOR MOTHERSON BOSCHLTD AMBUJACEM
SHREECEM TORNTPHARM LUPIN ZYDUSLIFE NAUKRI INDIGO HINDZINC VBL POLYCAB PERSISTENT LTIM MPHASIS COFORGE""".split()


def universe() -> tuple[list[str], str]:
    try:
        r = requests.get("https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv", headers=UA, timeout=20)
        r.raise_for_status()
        syms = pd.read_csv(io.StringIO(r.text))["Symbol"].dropna().astype(str).str.strip().tolist()
        if len(syms) > 300:
            return syms, "NIFTY 500"
    except Exception as e:
        log(f"NIFTY 500 list unavailable ({type(e).__name__}) - using top-100 fallback")
    return FALLBACK, "top ~100 NSE stocks"


def download(symbols):
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), 80):
        chunk = [s + ".NS" for s in symbols[i:i + 80]]
        try:
            df = yf.download(chunk, period="1y", interval="1d", group_by="ticker", auto_adjust=False,
                             threads=True, progress=False)
        except Exception as e:
            log(f"download chunk failed: {e}"); continue
        for t in chunk:
            try:
                d = df[t].dropna(subset=["Close"]) if isinstance(df.columns, pd.MultiIndex) else df.dropna(subset=["Close"])
                if len(d) > 60:
                    out[t[:-3]] = bot.to_daily_index(d[["Open", "High", "Low", "Close", "Volume"]])
            except Exception:
                pass
    return out


def analyse(sym, d, nifty_ret63):
    c, h, l, v = d.Close, d.High, d.Low, d.Volume
    price = float(c.iloc[-1])
    value_cr = float((c * v).tail(20).mean() / 1e7)
    day_move = float(c.iloc[-1] / c.iloc[-2] - 1)
    flags = []
    if price < MIN_PRICE:
        flags.append("penny price")
    if value_cr < MIN_VALUE_CR:
        flags.append(f"low liquidity (₹{value_cr:.1f} Cr/day)")
    if abs(day_move) > MAX_DAY_MOVE:
        flags.append(f"wild move {day_move * 100:+.0f}% today (pump/news risk)")
    if len(c) > 6 and abs(c.iloc[-1] / c.iloc[-6] - 1) > 0.30:
        flags.append("moved >30% in a week (pump/operator pattern)")
    big_days = int((c.pct_change().tail(20).abs() > 0.08).sum())
    if big_days >= 3:
        flags.append(f"{big_days} days with >8% moves in a month (erratic, possible operator activity)")
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr10 = tr.ewm(alpha=1 / 10, adjust=False).mean()
    ad, pdi, ndi = adx(h, l, c)
    e20, e50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean()
    jump = float((c.iloc[-1] - c.iloc[-2]) / atr10.iloc[-2])
    breakout = jump > 1.5 and ad.iloc[-1] > 15 and pdi.iloc[-1] > ndi.iloc[-1]
    ret63 = float(c.iloc[-1] / c.iloc[-64] - 1) if len(c) > 64 else 0.0
    rs = ret63 - nifty_ret63
    hi20 = float(h.tail(20).max())
    near_hi = price / hi20 - 1
    vol_surge = float(v.iloc[-1] / v.tail(20).mean()) if v.tail(20).mean() > 0 else 0
    atr_pct = float(atr10.iloc[-1] / price * 100)
    trend = int(price > e20.iloc[-1]) + int(e20.iloc[-1] > e50.iloc[-1]) + int(e50.iloc[-1] > e200.iloc[-1])
    score = (trend * 15 + max(-20, min(25, rs * 100)) + (15 if near_hi > -0.02 else 5 if near_hi > -0.05 else 0)
             + (10 if vol_surge > 1.5 else 0) + (10 if 1.2 <= atr_pct <= 4 else 0))
    qty = math.floor(min(BUDGET / price, 0.02 * BUDGET / (0.05 * price)))
    downtrend = price < e200.iloc[-1] and e50.iloc[-1] < e200.iloc[-1]
    if atr_pct > 6:
        flags.append(f"too volatile ({atr_pct:.1f}% daily range)")
    if flags:
        grade, grade_why = "AVOID", flags[0]
    elif downtrend:
        grade, grade_why = "AVOID", "downtrend (below its 200-day average) - don't buy falling stocks"
    elif breakout and trend == 3 and value_cr >= 50 and 1 <= atr_pct <= 4:
        grade, grade_why = "SELECT", "tested breakout + strong uptrend + very liquid + normal volatility"
    elif breakout:
        grade, grade_why = "WATCH", "breakout, but not all quality checks passed (trend/liquidity/volatility)"
    elif trend >= 2 and rs > 0:
        grade, grade_why = "WATCH", "healthy uptrend and stronger than NIFTY - wait for a breakout"
    else:
        grade, grade_why = "NEUTRAL", "no clear edge right now"
    why = []
    if trend == 3:
        why.append("strong uptrend (price > EMA20 > EMA50 > EMA200)")
    elif trend == 2:
        why.append("uptrend forming")
    if rs > 0.05:
        why.append(f"beating NIFTY by {rs * 100:.0f}% over 3 months")
    if near_hi > -0.02:
        why.append("near its 20-day high (possible breakout soon)")
    if vol_surge > 1.5:
        why.append(f"volume {vol_surge:.1f}× normal (big players active)")
    if breakout:
        why.insert(0, f"BREAKOUT: jumped {jump:.1f}× its normal move in an uptrend (ADX {ad.iloc[-1]:.0f})")
    return dict(sym=sym, price=round(price, 2), score=round(score, 1), breakout=bool(breakout), jump=round(jump, 2),
                adx=round(float(ad.iloc[-1]), 1), rs_pct=round(rs * 100, 1), near_hi_pct=round(near_hi * 100, 1),
                vol_surge=round(vol_surge, 2), atr_pct=round(atr_pct, 2), value_cr=round(value_cr, 1), qty=qty,
                stop=round(price * 0.95, 2), flags=flags, why=why or ["no strong features"],
                grade=grade, grade_why=grade_why, fits=qty >= 1)


def select_card(r, budget=BUDGET):
    need = r["price"] * 0.05 / 0.02
    risk1 = r["price"] * 0.05
    if r["qty"] >= 1:
        money = f"✅ Fits your ₹{budget:,.0f}: qty <b>{r['qty']}</b> (max loss ~₹{r['qty'] * risk1:,.0f})"
    elif r["price"] <= budget:
        money = (f"⚠️ 1 share costs ₹{r['price']:,.0f} but risks ~₹{risk1:,.0f} = {risk1 / budget * 100:.1f}% of ₹{budget:,.0f} "
                 f"(rule is 2%) → <b>skip</b>. Need ~₹{need:,.0f} for 1 share within the rule.")
    else:
        money = f"❌ 1 share (₹{r['price']:,.0f}) is above your ₹{budget:,.0f} → <b>skip</b>. Need ~₹{need:,.0f}."
    return (f"🟢 <b>SELECT: {r['sym']}</b> - closing price <b>₹{r['price']:,.2f}</b>\n"
            f"• Breakout strength: <b>{r['jump']}×</b> normal move (needs >1.5)\n"
            f"• Trend strength ADX: <b>{r['adx']:.0f}</b> (25+ = strong)\n"
            f"• vs NIFTY (3 months): <b>{r['rs_pct']:+.0f}%</b>\n"
            f"• From 20-day high: {r['near_hi_pct']:+.1f}% · Volume: {r['vol_surge']}× normal\n"
            f"• Daily movement: {r['atr_pct']}% · Traded/day: ₹{r['value_cr']:,.0f} Cr\n"
            f"• <b>Buy zone tomorrow 9:15:</b> ₹{r['price'] * 0.99:,.2f} – ₹{r['price'] * 1.015:,.2f} "
            f"(LIMIT max ₹{r['price'] * 1.015:,.2f}; opens higher = don't chase, lower = skip)\n"
            f"• Stop-loss (−5%): <b>≈ ₹{r['stop']:,.2f}</b> (5% below YOUR buy price)\n"
            f"• Score: {r['score']:.0f}/100\n\n{money}\n\n"
            f"<b>If you trade it:</b> tomorrow 9:15 <code>BUY {max(r['qty'], 1)} {r['sym']} CNC LIMIT {r['price'] * 1.015:.2f}</code> "
            f"(cancel if not filled by 9:30), "
            f"then GTT → SELL trigger = buy price × 0.95. Raise the stop when told. Never average down.\n"
            "<i>Tested rule but not proven · not investment advice.</i>")


def send_details():
    d = json.loads((bot.DATA / "scanner.json").read_text())
    sel = [r for r in d["breakouts"] if r.get("grade") == "SELECT"]
    msgs = [select_card(r, d.get("budget", BUDGET)) for r in sel] or ["🟢 No SELECT stocks in the latest scan."]
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    for m in msgs:
        print(bot.telegram(f"🔎 Details from the {d['generated']} scan\n\n" + m))


def main():
    import sys
    if "--details" in sys.argv:
        return send_details()
    syms, uni = universe()
    data = download(syms)
    nif = bot.to_daily_index(bot.fetch("^NSEI", "1y", "1d")[0]).Close
    nifty_ret63 = float(nif.iloc[-1] / nif.iloc[-64] - 1)
    today = pd.Timestamp(datetime.now(IST).date())
    fresh = {k: d for k, d in data.items() if d.index[-1] == today}
    if len(fresh) < len(data) // 2:
        log(f"only {len(fresh)}/{len(data)} have today's bar - market holiday or data delay; using latest available")
        fresh = data
    rows = [analyse(s, d, nifty_ret63) for s, d in fresh.items()]
    safe = [r for r in rows if r["grade"] != "AVOID"]
    risky = [r for r in rows if r["grade"] == "AVOID"]
    counts = {g: sum(1 for r in rows if r["grade"] == g) for g in ("SELECT", "WATCH", "NEUTRAL", "AVOID")}
    reasons = {}
    for r in risky:
        k = r["grade_why"].split(" (")[0].split(" - ")[0]
        k = "".join(ch for ch in k if not ch.isdigit()).replace("% ", "").strip()
        reasons[k] = reasons.get(k, 0) + 1
    brk = sorted([r for r in safe if r["breakout"]], key=lambda r: -r["jump"])
    watch = sorted([r for r in safe if not r["breakout"]], key=lambda r: -r["score"])
    cheap = [r for r in watch if r["qty"] >= 1][:5]
    gen = datetime.now(IST).strftime("%d %b %Y %H:%M IST")
    out = dict(generated=gen, universe=uni, scanned=len(rows), budget=BUDGET, nifty_3m_pct=round(nifty_ret63 * 100, 1),
               breakouts=brk, watchlist=watch[:30], fits_budget=cheap, excluded=len(risky),
               excluded_examples=[(r["sym"], r["grade_why"]) for r in risky[:30]], grades=counts)
    (bot.DATA / "scanner.json").write_text(json.dumps(bot.clean(out), indent=1))

    L = [f"🔎 <b>EdgeDesk stock analyser</b> - {gen}",
         f"Scanned {len(rows)} stocks ({uni}) · budget ₹{BUDGET:,.0f}",
         f"🟢 SELECT {counts['SELECT']} · 🟡 WATCH {counts['WATCH']} · ⚪ NEUTRAL {counts['NEUTRAL']} · 🔴 AVOID {counts['AVOID']}", ""]
    sel = [r for r in brk if r["grade"] == "SELECT"]
    L.append("<b>🟢 SELECT - tested breakout that passed every check (buy tomorrow 9:15, Delivery, stop −5%)</b>")
    if sel:
        for r in sel[:5]:
            fit = f"qty {r['qty']}" if r["fits"] else "too expensive for your budget - skip"
            L.append(f"📈 <b>{r['sym']}</b> ₹{r['price']:,.2f} · {fit} · stop ~₹{r['stop']:,.2f}\n   {r['why'][0]}")
    else:
        L.append("None today - no trade is fine.")
    other = [r for r in brk if r["grade"] != "SELECT"]
    if other:
        L.append("🟡 Breakouts that failed a quality check (don't trade): " + ", ".join(f"{r['sym']} ({r['grade_why'].split(' (')[0]})" for r in other[:4]))
    L += ["", "<b>B. Watchlist - strongest stocks overall (education, not buy signals)</b>"]
    for r in watch[:5]:
        tag = "✅ fits budget" if r["qty"] >= 1 else "💰 above budget"
        L.append(f"• <b>{r['sym']}</b> ₹{r['price']:,.0f} · score {r['score']:.0f} · {tag}\n   {', '.join(r['why'][:2])}")
    L += ["", f"<b>C. Strongest stocks that fit your ₹{BUDGET:,.0f}</b> (watch for a breakout)"]
    for r in cheap or []:
        L.append(f"• <b>{r['sym']}</b> ₹{r['price']:,.0f} · qty {r['qty']} · score {r['score']:.0f} - {', '.join(r['why'][:1])}")
    if not cheap:
        L.append("None today.")
    if reasons:
        L += ["", "<b>🔴 AVOID today</b> (" + str(len(risky)) + "): " + " · ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:5])]
    L += ["", "📚 <i>Rule of thumb: SELECT = breakout + strong uptrend + liquid + normal volatility. "
          "AVOID = penny, illiquid, wild jumps, erratic moves, downtrends.</i>",
          "<i>Only 🟢 SELECT uses the tested rule (still not proven). WATCH lists are for learning. Not investment advice.</i>"]
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    ok, msg = bot.telegram("\n".join(L))
    log(f"telegram: {msg}; scanned {len(rows)}, breakouts {len(brk)}, risky {len(risky)}")
    for r in sel[:5]:                                   # full detail card for every SELECT stock
        bot.telegram(select_card(r))


if __name__ == "__main__":
    main()
