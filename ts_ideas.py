#!/usr/bin/env python3
"""
Ideas from the open-source Trading Strategy framework (tradingstrategy-ai/trade-executor, crypto),
adapted to Indian stocks (Zerodha delivery, BUY-only) and tested honestly. Not investment advice.

A. Momentum rotation (their "size-risk" / momentum alpha models): every N trading days rank the stocks by their
   return over the last L days and hold the top K equally (only if momentum > 1%), else cash.
   Compared with simply holding NIFTY 50. TRAIN = first 70% of time, TEST = last 30%.
B. ATR breakout + ADX regime filter (their "eth-breakout-atr"): buy when close > previous close + F x ATR(10)
   and ADX(14) > 15 with +DI > -DI; hard stop -S%; trailing stop T% activated after +A% gain.

Costs: ~0.12% per side (STT 0.1% + charges) + Rs 15.93 DP charge per stock sold (on a Rs 50,000 account).
"""
from __future__ import annotations

import html
import itertools
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import bot
from bot import clean, fetch, log, to_daily_index

STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "AXISBANK", "KOTAKBANK",
          "LT", "ITC", "BHARTIARTL", "BAJFINANCE", "MARUTI", "SUNPHARMA", "HINDUNILVR", "TITAN",
          "ASIANPAINT", "ULTRACEMCO", "NTPC", "POWERGRID"]
ACCOUNT = 50_000
COST_SIDE = 0.0012
DP_RS = 15.93


def load():
    px, hi, lo, op = {}, {}, {}, {}
    for s in STOCKS:
        try:
            d = to_daily_index(fetch(s + ".NS", "6y", "1d")[0])
            px[s], hi[s], lo[s], op[s] = d.Close, d.High, d.Low, d.Open
        except Exception as e:
            log(f"{s}: {e}")
    nifty = to_daily_index(fetch("^NSEI", "6y", "1d")[0]).Close
    P = pd.DataFrame(px).dropna(how="all").ffill()
    global OPEN
    OPEN = pd.DataFrame(op).reindex(P.index).ffill()
    return P, pd.DataFrame(hi).reindex(P.index).ffill(), pd.DataFrame(lo).reindex(P.index).ffill(), nifty.reindex(P.index).ffill()


def perf(eq: pd.Series) -> dict:
    eq = eq.dropna()
    if len(eq) < 30:
        return {}
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    dd = (eq / eq.cummax() - 1).min()
    return dict(cagr=round(float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, 1),
                max_dd=round(float(dd) * 100, 1),
                sharpe=round(float(r.mean() / r.std() * np.sqrt(252)), 2) if r.std() > 0 else 0.0,
                total=round(float(eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1))


def rotation(P, L, K, N):
    """Equity curve of momentum rotation (buy-only), after costs. Decision uses momentum known at the
    previous close; trades happen at today's close (no look-ahead)."""
    R = P.pct_change().fillna(0).to_numpy()
    M = (P / P.shift(L) - 1).to_numpy()
    n, k = R.shape
    w = np.zeros(k)
    val, eq = 1.0, [1.0]
    for i in range(1, n):
        pr = float((w * R[i]).sum())
        val *= 1 + pr
        w = w * (1 + R[i]) / (1 + pr) if (1 + pr) != 0 else w
        if i % N == 0 and np.isfinite(M[i - 1]).sum() >= K:
            m = np.where(np.isfinite(M[i - 1]), M[i - 1], -np.inf)
            order = np.argsort(-m)[:K]
            new = np.zeros(k)
            for o in order:
                if m[o] > 0.01:
                    new[o] = 1.0 / K
            turnover = float(np.abs(new - w).sum())
            sells = int(((w > 1e-9) & (new < w - 1e-9)).sum())
            val -= val * turnover * COST_SIDE + sells * DP_RS / ACCOUNT
            w = new
        eq.append(val)
    return pd.Series(eq, index=P.index)


def adx(h, l, c, n=14):
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=c.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    ndi = 100 * pd.Series(ndm, index=c.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), pdi, ndi


def breakout_trades(c, h, l, F, S, A, T, hold=30, o=None):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr10 = tr.ewm(alpha=1 / 10, adjust=False).mean()
    ad, pdi, ndi = adx(h, l, c)
    sig = (c > c.shift() + F * atr10.shift()) & (ad > 15) & (pdi > ndi)
    C, H, L = c.to_numpy(), h.to_numpy(), l.to_numpy()
    O = o.to_numpy() if o is not None else C
    out, i, n = [], 30, len(c)
    S_ = sig.to_numpy()
    while i < n - 1:
        if not S_[i]:
            i += 1; continue
        e = C[i]                     # buy at the signal day's close (as their engine does on candle close)
        stop = e * (1 - S)
        peak, trail_on, j, px = e, False, i + 1, None
        while j < n and j <= i + hold:
            if O[j] <= stop:                 # gapped below the stop: filled at the (worse) open
                px = O[j]; break
            if L[j] <= stop:
                px = stop; break
            peak = max(peak, H[j])
            if not trail_on and peak >= e * (1 + A):
                trail_on = True
            if trail_on:
                stop = max(stop, peak * (1 - T))
            j += 1
        if px is None:
            j = min(j, n - 1); px = C[j]
        out.append(dict(date=str(c.index[i].date()), ret=float(px / e - 1 - 2 * COST_SIDE - DP_RS / (ACCOUNT / 3))))
        i = j + 1
    return out


def tstats(rs):
    if not rs:
        return dict(trades=0)
    a = np.array(rs)
    return dict(trades=len(a), win=round(float((a > 0).mean() * 100), 1), avg_pct=round(float(a.mean() * 100), 2),
                total_pct=round(float(a.sum() * 100), 1),
                t=round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), 2) if len(a) > 2 and a.std() > 0 else 0.0)


def main():
    P, H, L, nifty = load()
    split = P.index[int(len(P) * 0.7)]
    bench = {"train": perf(nifty[nifty.index < split]), "test": perf(nifty[nifty.index >= split]), "all": perf(nifty)}
    rot = []
    for Lb, K, N in itertools.product((20, 60, 120, 250), (2, 3, 5), (5, 21)):
        eq = rotation(P, Lb, K, N)
        rot.append(dict(lookback=Lb, hold=K, rebalance_days=N,
                        train=perf(eq[eq.index < split]), test=perf(eq[eq.index >= split]), all=perf(eq)))
    rot.sort(key=lambda r: -(r["train"].get("cagr") or -99))          # choose on TRAIN only
    best_train = rot[0]
    brk = []
    for F, S, A, T in itertools.product((1.0, 1.5, 2.0, 3.0), (0.02, 0.05), (0.05, 0.10), (0.01, 0.03)):
        trs = []
        for s in P.columns:
            ix = P[s].dropna().index
            trs += breakout_trades(P[s].dropna(), H[s].reindex(ix), L[s].reindex(ix), F, S, A, T, o=OPEN[s].reindex(ix))
        a = [t["ret"] for t in trs if pd.Timestamp(t["date"]) < split]
        b = [t["ret"] for t in trs if pd.Timestamp(t["date"]) >= split]
        brk.append(dict(F=F, stop=S, trail_after=A, trail=T, train=tstats(a), test=tstats(b), all=tstats([t["ret"] for t in trs])))
    brk.sort(key=lambda r: -(r["train"].get("avg_pct") or -99))
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = [f"# Trading Strategy (tradingstrategy-ai) ideas on Indian stocks - {gen}", "",
          f"{len(P.columns)} large NSE stocks, daily candles ~5 years, BUY-only (Kite delivery), after ~0.12%/side + DP charges. "
          "Settings are chosen on TRAIN (first 70%) and then checked on TEST (last 30%, unseen). Not investment advice.", "",
          f"**Benchmark - just hold NIFTY 50:** TRAIN CAGR {bench['train'].get('cagr')}% · TEST CAGR {bench['test'].get('cagr')}% · "
          f"all {bench['all'].get('cagr')}%/yr, worst drop {bench['all'].get('max_dd')}%", "",
          "## A. Momentum rotation (top 10 settings by TRAIN result)",
          "| Lookback days | Hold top | Rebalance every | TRAIN CAGR % | TEST CAGR % (unseen) | TEST worst drop % | All CAGR % | All Sharpe |", "|---|---|---|---|---|---|---|---|"]
    for r in rot[:10]:
        md.append(f"| {r['lookback']} | {r['hold']} | {r['rebalance_days']} days | {r['train'].get('cagr')} | {r['test'].get('cagr')} | "
                  f"{r['test'].get('max_dd')} | {r['all'].get('cagr')} | {r['all'].get('sharpe')} |")
    beats = sum(1 for r in rot if (r["test"].get("cagr") or -99) > (bench["test"].get("cagr") or 99))
    md += ["", f"{beats} of {len(rot)} rotation settings beat NIFTY on the unseen TEST period.", "",
           "## B. ATR breakout + ADX filter (top 8 settings by TRAIN result)",
           "| Breakout F×ATR | Hard stop | Trail after | Trail | TRAIN trades / win / avg % | TEST trades / win / avg % (unseen) | Luck t (all) |", "|---|---|---|---|---|---|---|"]
    for r in brk[:8]:
        a, b = r["train"], r["test"]
        md.append(f"| {r['F']} | {r['stop'] * 100:.0f}% | +{r['trail_after'] * 100:.0f}% | {r['trail'] * 100:.0f}% | "
                  f"{a.get('trades')} / {a.get('win')}% / {a.get('avg_pct')} | {b.get('trades')} / {b.get('win')}% / {b.get('avg_pct')} | {r['all'].get('t')} |")
    (bot.DATA / "ts_ideas_report.md").write_text("\n".join(md))
    (bot.DATA / "ts_ideas_report.json").write_text(json.dumps(clean(dict(generated_at=gen, benchmark=bench, rotation=rot, breakout=brk)), indent=1))
    print("\n".join(md))
    bt, bb = best_train, brk[0]
    T = ["🧪 <b>Trading Strategy ideas on Indian stocks</b> (5 years, after costs)", "",
         f"Hold NIFTY 50: {bench['all'].get('cagr')}%/yr · unseen period {bench['test'].get('cagr')}%/yr", "",
         f"<b>A. Momentum rotation</b> (best on past data: top {bt['hold']} by {bt['lookback']}-day return, rebalance every {bt['rebalance_days']} days)",
         f"Past {bt['train'].get('cagr')}%/yr → unseen {bt['test'].get('cagr')}%/yr (worst drop {bt['test'].get('max_dd')}%)",
         f"{beats} of {len(rot)} settings beat NIFTY on unseen data", "",
         f"<b>B. ATR breakout + ADX</b> (best on past data: F={bb['F']}, stop {bb['stop'] * 100:.0f}%)",
         f"Past avg {bb['train'].get('avg_pct')}%/trade ({bb['train'].get('trades')}) → unseen {bb['test'].get('avg_pct')}%/trade ({bb['test'].get('trades')}), win {bb['test'].get('win')}%",
         "", "<i>Full report: data/ts_ideas_report.md. Not investment advice.</i>"]
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    ok, msg = bot.telegram("\n".join(T))
    log(f"telegram: {msg}")


if __name__ == "__main__":
    main()
