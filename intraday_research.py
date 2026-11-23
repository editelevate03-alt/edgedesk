#!/usr/bin/env python3
"""
EdgeDesk intraday STOCKS research (Zerodha MIS) - long AND short, closed by 15:15 IST.

- 14 liquid NIFTY stocks. 1-hour candles (~2 years, walk-forward: TRAIN 70% / TEST 30%) and
  15-minute candles (~60 days, small sample - shown for reference).
- Strategies: opening-range breakout, VWAP trend, gap-and-go, gap-fade.
- Zerodha intraday costs ~0.11% of turnover per round trip (brokerage 0.03%/order, STT 0.025% sell,
  exchange, stamp, GST) + 0.02% slippage per side.
- Results in R (1R = amount risked) and in rupees for a Rs 2,000 position (no leverage).
- GREEN = passes TRAIN (luck test) AND profitable on unseen TEST. Paper research - not investment advice.
"""
from __future__ import annotations

import html
import json
import math
import os
from datetime import datetime, time as dtime, timezone

import numpy as np
import pandas as pd

import bot
from bot import IST, clean, fetch, log

STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "AXISBANK", "KOTAKBANK",
          "LT", "ITC", "BHARTIARTL", "BAJFINANCE", "MARUTI", "SUNPHARMA"]
CAPITAL = float(os.getenv("INTRADAY_CAPITAL", "2000"))
COST_RT = 0.0011 + 2 * 0.0002          # round-trip cost as fraction of price


def prep(df):
    d = bot.add_indicators(df)
    d["day"] = [t.date() for t in d.index]
    d["tmin"] = [t.hour * 60 + t.minute for t in d.index]
    tp = (d.High + d.Low + d.Close) / 3
    v = d.Volume.replace(0, np.nan).fillna(1)
    d["vwap"] = (tp * v).groupby(d["day"]).cumsum() / v.groupby(d["day"]).cumsum()
    d["last_of_day"] = d["day"] != d["day"].shift(-1)
    first = d.groupby("day").head(1)
    prev_close = d.groupby("day")["Close"].last().shift(1)
    d["gap"] = d["day"].map((first.set_index("day")["Open"] / prev_close).to_dict()) - 1
    d["prev_close"] = d["day"].map(prev_close.to_dict())
    return d


def signals(d, key, nbars):
    """Returns arrays: dir (+1/-1/0), stop price, target price (nan = use R multiple)."""
    n = len(d)
    sig, stop, tgt = np.zeros(n), np.full(n, np.nan), np.full(n, np.nan)
    for day, g in d.groupby("day", sort=False):
        idx = [d.index.get_loc(t) for t in g.index]
        if len(idx) <= nbars:
            continue
        rh, rl = d.High.iloc[idx[:nbars]].max(), d.Low.iloc[idx[:nbars]].min()
        gap = d.gap.iloc[idx[0]]
        first = idx[nbars - 1]
        if key == "orb":
            for i in idx[nbars:]:
                if d.tmin.iloc[i] > 13 * 60:
                    break
                if d.Close.iloc[i] > rh:
                    sig[i], stop[i] = 1, rl; break
                if d.Close.iloc[i] < rl:
                    sig[i], stop[i] = -1, rh; break
        elif key == "vwap":
            for i in idx[nbars:]:
                if not (10 * 60 <= d.tmin.iloc[i] <= 14 * 60):
                    continue
                c, pc, vw, pvw = d.Close.iloc[i], d.Close.iloc[i - 1], d.vwap.iloc[i], d.vwap.iloc[i - 1]
                if c > vw and pc <= pvw and d.ema20.iloc[i] > d.ema50.iloc[i]:
                    sig[i], stop[i] = 1, c - d.atr.iloc[i]; break
                if c < vw and pc >= pvw and d.ema20.iloc[i] < d.ema50.iloc[i]:
                    sig[i], stop[i] = -1, c + d.atr.iloc[i]; break
        elif key in ("gap_go", "gap_fade") and np.isfinite(gap) and abs(gap) >= 0.01:
            c, o = d.Close.iloc[first], d.Open.iloc[idx[0]]
            cont = (gap > 0 and c > o) or (gap < 0 and c < o)
            if key == "gap_go" and cont:
                s = 1 if gap > 0 else -1
                sig[first], stop[first] = s, (rl if s == 1 else rh)
            if key == "gap_fade" and not cont:
                s = -1 if gap > 0 else 1
                sig[first], stop[first] = s, (rh if s == -1 else rl)
                tgt[first] = d.prev_close.iloc[first]
    return sig, stop, tgt


def simulate(d, sig, stop, tgt, rr=2.0):
    O, H, L, C = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    out, n = [], len(d)
    for i in np.nonzero(sig)[0]:
        if i + 1 >= n or d.last_of_day.iloc[i]:
            continue
        s, e = int(sig[i]), O[i + 1]
        st = stop[i]
        risk = s * (e - st)
        if not np.isfinite(risk) or risk <= e * 0.001:          # stop must be below entry for longs (above for shorts)
            continue
        tg = tgt[i] if np.isfinite(tgt[i]) and s * (tgt[i] - e) > 0 else e + s * rr * risk
        r = None
        for j in range(i + 1, n):
            if (L[j] <= st) if s == 1 else (H[j] >= st):
                r = -1.0; break
            if (H[j] >= tg) if s == 1 else (L[j] <= tg):
                r = s * (tg - e) / risk; break
            if d.tmin.iloc[j] >= 15 * 60 + 15 or d.last_of_day.iloc[j]:
                r = s * (C[j] - e) / risk; break
        if r is None:
            continue
        r -= COST_RT * e / risk
        qty = max(1, math.floor(CAPITAL / e))
        out.append(dict(time=str(d.index[i + 1]), dir=s, r=float(r), rs=float(r * risk * qty), qty=qty, price=float(e)))
    return out


def summ(tr):
    if not tr:
        return dict(trades=0)
    r = np.array([t["r"] for t in tr]); rs = np.array([t["rs"] for t in tr])
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    return dict(trades=len(r), win_rate=round(float((r > 0).mean() * 100), 1), avg_r=round(float(r.mean()), 3),
                pf=round(float(w / l), 2) if l > 0 else None, avg_rs=round(float(rs.mean()), 1), total_rs=round(float(rs.sum()), 0),
                t=round(float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))), 2) if len(r) > 2 and r.std() > 0 else 0.0)


def green(tr, te, full):
    return (tr.get("trades", 0) >= 60 and tr["avg_r"] > 0.05 and (tr["pf"] or 0) >= 1.2 and tr["t"] >= 2
            and te.get("trades", 0) >= 30 and te["avg_r"] > 0 and (te["pf"] or 0) >= 1.1 and full.get("t", 0) >= 2)


LABELS = {"orb": "Opening-range breakout", "vwap": "VWAP trend cross", "gap_go": "Gap-and-go (>1% gap)", "gap_fade": "Gap-fade (>1% gap)"}


def main():
    res, errors = [], []
    for tf, period, interval, nb in (("1h", "730d", "1h", 1), ("15m", "60d", "15m", 2)):
        per_strat = {k: [] for k in LABELS}
        per_stock = {k: {} for k in LABELS}
        split = None
        for sym in STOCKS:
            try:
                df, _ = fetch(sym + ".NS", period, interval)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df.index = df.index.tz_convert(IST)
                df = df[(df.index.time >= dtime(9, 15)) & (df.index.time < dtime(15, 30))]
                d = prep(df)
                if split is None or d.index[int(len(d) * 0.7)] > split:
                    split = d.index[int(len(d) * 0.7)]
                for k in LABELS:
                    tr = simulate(d, *signals(d, k, nb))
                    per_strat[k] += tr
                    per_stock[k][sym] = summ(tr)
                log(f"intraday {tf} {sym}: {len(d)} bars")
            except Exception as e:
                errors.append(f"{tf} {sym}: {e}")
        for k in LABELS:
            tr_all = sorted(per_strat[k], key=lambda t: t["time"])
            a = [t for t in tr_all if pd.Timestamp(t["time"]) < split]
            b = [t for t in tr_all if pd.Timestamp(t["time"]) >= split]
            S, T, Fu = summ(a), summ(b), summ(tr_all)
            best = sorted(((s, v) for s, v in per_stock[k].items() if v.get("trades", 0) >= 15), key=lambda x: -x[1]["avg_r"])[:3]
            res.append(dict(chart=tf, strategy=k, label=LABELS[k], train=S, test=T, full=Fu,
                            green=tf == "1h" and green(S, T, Fu), best_stocks=[(s, v["avg_r"], v["trades"]) for s, v in best]))
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = [f"# EdgeDesk intraday stocks research (Zerodha MIS, long + short) - {gen}", "",
          f"14 liquid NIFTY stocks · Rs {CAPITAL:,.0f} position, no leverage · after ~0.15% round-trip costs · exit by 3:15 PM. "
          "R = result in units of the amount risked. 15m = last ~60 days only. Not investment advice.", "",
          "| | Chart | Strategy | TRAIN trades / win / avg R | TEST (unseen) trades / win / avg R | All: avg Rs/trade (Rs 2,000) | Luck t | Best stocks (avg R, trades) |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(res, key=lambda r: (r["chart"] != "1h", -(r["full"].get("avg_r") or -9))):
        S, T, Fu = r["train"], r["test"], r["full"]
        icon = "🟢" if r["green"] else ("🟡" if (Fu.get("avg_r") or -1) > 0 else "🔴")
        md.append(f"| {icon} | {r['chart']} | {r['label']} | {S.get('trades', 0)} / {S.get('win_rate', '-')}% / {S.get('avg_r', '-')} | "
                  f"{T.get('trades', 0)} / {T.get('win_rate', '-')}% / {T.get('avg_r', '-')} | {Fu.get('avg_rs', '-')} | {Fu.get('t', '-')} | "
                  + ", ".join(f"{s} ({a:+.2f}, {n})" for s, a, n in r["best_stocks"]) + " |")
    greens = [r for r in res if r["green"]]
    md += ["", "## GREEN", *([f"- {r['label']} (1h)" for r in greens] or ["- **None.**"])]
    if errors:
        md += ["", "Data errors: " + "; ".join(errors[:10])]
    (bot.DATA / "intraday_report.md").write_text("\n".join(md))
    (bot.DATA / "intraday_report.json").write_text(json.dumps(clean(dict(generated_at=gen, results=res, errors=errors)), indent=1))
    print("\n".join(md))
    T = ["📊 <b>Intraday stocks backtest</b> (14 NIFTY stocks, long+short, Zerodha costs)", ""]
    for r in sorted(res, key=lambda r: (r["chart"] != "1h", -(r["full"].get("avg_r") or -9))):
        Fu, Te = r["full"], r["test"]
        icon = "🟢" if r["green"] else ("🟡" if (Fu.get("avg_r") or -1) > 0 else "🔴")
        T.append(f"{icon} {r['chart']} {html.escape(r['label'])}: win {Fu.get('win_rate', '-')}% · avg {Fu.get('avg_r', '-')}R · "
                 f"Rs {Fu.get('avg_rs', '-')}/trade · unseen {Te.get('avg_r', '-')}R ({Fu.get('trades', 0)} trades)")
    T += ["", "<b>GREEN:</b> " + (", ".join(html.escape(r["label"]) for r in greens) or "NONE"),
          "<i>Full report: data/intraday_report.md. Not investment advice.</i>"]
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    if os.getenv("SEND_TG", "1") != "1":
        return
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    ok, msg = bot.telegram("\n".join(T))
    log(f"telegram: {msg}")


if __name__ == "__main__":
    main()
