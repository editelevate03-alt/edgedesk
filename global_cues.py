#!/usr/bin/env python3
"""
Global cues for the Indian market - backtest (5 years) + live morning brief.

Cues known BEFORE 09:15 IST (no look-ahead): previous US session (S&P 500, Nasdaq), crude oil (Brent),
USD/INR, US dollar index, US 10-year yield - all from the last daily bar dated before the Indian day.
Live brief also shows US futures and Asia (Nikkei, Hang Seng) right now.

Global score (-100..+100): + S&P/Nasdaq up, - crude up, - rupee weaker (USD/INR up), - dollar up, - US yield up.
Each cue is scaled by its own typical move (z-score, 1-year window).

Backtest questions (TRAIN = first 70%, TEST = last 30%):
  1. Does the score predict NIFTY / BANK NIFTY GAP at the open?
  2. Does it predict the OPEN -> CLOSE move (what an intraday option trade actually earns)?
  3. Do 1-hour ORB option trades that AGREE with the score win more often?
Not investment advice. Past patterns can stop working.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, time as dtime, timezone

import numpy as np
import pandas as pd

import bot
from bot import IST, clean, fetch, log

CUES = {  # name: (ticker, sign for India, label)
    "sp500": ("^GSPC", +1, "S&P 500 (US, last close)"),
    "nasdaq": ("^IXIC", +1, "Nasdaq (US, last close)"),
    "brent": ("BZ=F", -1, "Crude oil (Brent)"),
    "usdinr": ("INR=X", -1, "USD/INR (rupee)"),
    "dxy": ("DX-Y.NYB", -1, "US dollar index"),
    "us10y": ("^TNX", -1, "US 10-year yield"),
}
LIVE_ONLY = {"spfut": ("ES=F", +1, "US futures now"), "nikkei": ("^N225", +1, "Japan Nikkei now"), "hsi": ("^HSI", +1, "Hong Kong now")}
INDEX = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}


def daily(ticker, period="6y"):
    df, _ = fetch(ticker, period, "1d")
    return bot.to_daily_index(df)


def cue_frame(period="6y") -> pd.DataFrame:
    """One column per cue: z-scored daily % change, signed for India."""
    cols = {}
    for k, (t, sign, _) in CUES.items():
        try:
            c = daily(t, period)["Close"]
            ch = c.diff() if k == "us10y" else c.pct_change()
            z = ch / ch.rolling(250, min_periods=60).std()
            cols[k] = (sign * z).clip(-3, 3)
        except Exception as e:
            log(f"cue {k} failed: {e}")
    return pd.DataFrame(cols)


def score_for_days(cf: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    """For each Indian trading day D, use the latest cue values dated strictly BEFORE D (no look-ahead)."""
    shifted = cf.copy()
    shifted.index = shifted.index + pd.Timedelta(days=1)          # a bar dated D-1 becomes usable on D
    al = shifted.reindex(shifted.index.union(days)).sort_index().ffill(limit=4).reindex(days)
    al["score"] = (al.mean(axis=1) * 50).clip(-100, 100)
    return al


def acc_table(df, col, split):
    out = {}
    for part, d in (("train", df[df.index < split]), ("test", df[df.index >= split]), ("all", df)):
        r = {}
        for lab, mask in (("strong up (score ≥ +40)", d.score >= 40), ("strong down (score ≤ −40)", d.score <= -40)):
            sub = d[mask][col].dropna()
            want_up = "up" in lab
            r[lab] = dict(days=int(len(sub)),
                          right_pct=round(float(((sub > 0) if want_up else (sub < 0)).mean() * 100), 1) if len(sub) else None,
                          avg_move_pct=round(float(sub.mean() * 100), 3) if len(sub) else None)
        base = d[col].dropna()
        r["base_up_pct"] = round(float((base > 0).mean() * 100), 1)
        r["corr"] = round(float(d[["score", col]].corr().iloc[0, 1]), 3)
        out[part] = r
    return out


def orb_agreement(name, scores):
    """1-hour ORB option trades (ATM, from fno_research) split by agreement with the global score."""
    import fno_research as F
    u = F.UNDERLYINGS[name]
    df, _ = fetch(u["ticker"], "730d", "1h")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(IST)
    df = df[(df.index.time >= dtime(9, 15)) & (df.index.time < dtime(15, 30))]
    d = F.prep_intraday(df)
    vix = daily("^INDIAVIX")["Close"]
    vix = vix.reindex(pd.date_range(vix.index.min(), pd.Timestamp.today().normalize())).ffill()
    vix = {k: float(v) for k, v in vix.items()}
    sig, stops = F.sig_orb(d, 1)
    trades = [t for t in F.simulate(d, sig, stops, u, vix, 1.0, 2.0, True, 7, "atm") if not t.get("skipped")]
    res = {}
    for t in trades:
        day = pd.Timestamp(pd.Timestamp(t["time"]).date())
        sc = scores["score"].get(day, np.nan)
        t["g"] = None if not np.isfinite(sc) or abs(sc) < 20 else bool((sc > 0) == (t["dir"] == 1))
    for lab, flag in (("agrees", True), ("disagrees", False), ("neutral", None)):
        g = [t["pnl"] for t in trades if t["g"] is flag]
        res[lab] = dict(trades=len(g), win_pct=round(float(np.mean([p > 0 for p in g]) * 100), 1) if g else None,
                        avg_rs=round(float(np.mean(g)), 0) if g else None, total_rs=round(float(np.sum(g)), 0) if g else 0)
    return res


def backtest(send_tg=True):
    cf = cue_frame()
    report, tg = {}, []
    md = [f"# Global cues vs Indian market - 5-year backtest ({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})", "",
          "Cues known before 09:15 IST (previous US close, crude, USD/INR, dollar index, US 10y yield). "
          "'Right %' = how often the market moved in the direction the score said. Base = % of all days that were up. "
          "TRAIN = first 70% of days, TEST = last 30% (unseen). Not investment advice.", ""]
    for name, t in INDEX.items():
        ix = daily(t)
        days = ix.index[-5 * 252:]
        sc = score_for_days(cf, days)
        df = sc.join(pd.DataFrame({"gap": ix.Open / ix.Close.shift() - 1, "o2c": ix.Close / ix.Open - 1}))
        split = days[int(len(days) * 0.7)]
        gap, o2c = acc_table(df, "gap", split), acc_table(df, "o2c", split)
        try:
            orb = orb_agreement(name, sc)
        except Exception as e:
            orb = {"error": str(e)}
        report[name] = dict(gap=gap, open_to_close=o2c, orb_1h=orb)
        md += [f"## {name}", "| What | Period | Score ≥ +40: days / right % | Score ≤ −40: days / right % | Base up % | Correlation |", "|---|---|---|---|---|---|"]
        for what, tab in (("Opening gap", gap), ("Open → close (intraday)", o2c)):
            for part in ("train", "test"):
                r = tab[part]
                u, dn = r["strong up (score ≥ +40)"], r["strong down (score ≤ −40)"]
                md.append(f"| {what} | {part} | {u['days']} / {u['right_pct']}% | {dn['days']} / {dn['right_pct']}% | {r['base_up_pct']}% | {r['corr']} |")
        if "error" not in orb:
            md += ["", "**1-hour ORB option trades (ATM, ~2 years) split by global score**", "| Global score | Trades | Win % | Avg Rs | Total Rs |", "|---|---|---|---|---|"]
            md += [f"| {k} | {v['trades']} | {v['win_pct']} | {v['avg_rs']} | {v['total_rs']} |" for k, v in orb.items()]
        md.append("")
        gt, ot = gap["test"], o2c["test"]
        tg.append(f"<b>{name}</b> (unseen recent period)\n"
                  f"• Opening gap: score ≥+40 → gap up {gt['strong up (score ≥ +40)']['right_pct']}% of {gt['strong up (score ≥ +40)']['days']} days · "
                  f"score ≤−40 → gap down {gt['strong down (score ≤ −40)']['right_pct']}% (base up {gt['base_up_pct']}%)\n"
                  f"• Open→close: score ≥+40 → up {ot['strong up (score ≥ +40)']['right_pct']}% · score ≤−40 → down {ot['strong down (score ≤ −40)']['right_pct']}%"
                  + (f"\n• ORB trades: agrees win {orb['agrees']['win_pct']}% avg Rs {orb['agrees']['avg_rs']} ({orb['agrees']['trades']}) · "
                     f"disagrees win {orb['disagrees']['win_pct']}% avg Rs {orb['disagrees']['avg_rs']} ({orb['disagrees']['trades']})" if "error" not in orb else ""))
    (bot.DATA / "global_report.md").write_text("\n".join(md))
    (bot.DATA / "global_report.json").write_text(json.dumps(clean(report), indent=1))
    print("\n".join(md))
    if send_tg:
        txt = ("🌍 <b>Global cues backtest (5 years)</b>\nDo US markets, crude, rupee, dollar & US yields before 9:15 predict India?\n\n"
               + "\n\n".join(tg) + "\n\n<i>Gap = predicted at the open, but option prices already include the gap. "
               "What matters for intraday trades is open→close. Full report: data/global_report.md. Not investment advice.</i>")
        tg_send(txt)


def tg_send(txt):
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    ok, msg = bot.telegram(txt)
    log(f"telegram: {msg}")


def live_brief() -> dict:
    """Latest cues for the morning message (09:27 IST)."""
    today = pd.Timestamp(datetime.now(IST).date())
    cf = cue_frame(period="2y")
    sc = score_for_days(cf, pd.DatetimeIndex([today]))
    score = float(sc["score"].iloc[-1]) if np.isfinite(sc["score"].iloc[-1]) else 0.0
    lines = []
    for k, (t, sign, lab) in {**CUES, **LIVE_ONLY}.items():
        try:
            dfx, _ = fetch(t, "10d", "1d", tries=2)
            c = dfx["Close"].dropna()
            if k in LIVE_ONLY:                        # change since previous close, right now
                ch = c.iloc[-1] - c.iloc[-2] if k == "us10y" else c.iloc[-1] / c.iloc[-2] - 1
            else:
                cc = bot.to_daily_index(dfx)["Close"].dropna()
                cc = cc[cc.index < today]
                ch = cc.iloc[-1] - cc.iloc[-2] if k == "us10y" else cc.iloc[-1] / cc.iloc[-2] - 1
            good = sign * ch > 0
            val = f"{ch * 100:+.2f} bp" if k == "us10y" else f"{ch * 100:+.2f}%"
            lines.append(f"{'🟢' if good else '🔴'} {lab}: {val}")
        except Exception:
            lines.append(f"• {lab}: n/a")
    lab = "Positive 🟢" if score >= 20 else "Negative 🔴" if score <= -20 else "Neutral ⚪"
    text = (f"🌍 <b>Global cues before 9:15</b> - Global mood: <b>{lab} ({score:+.0f})</b>\n" + "\n".join(lines) +
            "\n<i>🟢 = good for India, 🔴 = bad. Tested over 5 years - see the global backtest report for how much it helps.</i>")
    return dict(score=score, text=text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    if a.backtest:
        backtest(send_tg=not a.no_telegram)
    else:
        print(live_brief()["text"])
