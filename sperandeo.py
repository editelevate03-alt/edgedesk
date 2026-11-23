#!/usr/bin/env python3
"""
Victor Sperandeo's rules - implemented and backtested honestly (daily candles, ~5 years, after costs).

1. "1-2-3" trend change (confirmed swing points, no look-ahead):
   LONG  (end of a downtrend): (1) close breaks ABOVE the down-trendline through the last two lower swing highs,
         (2) the pullback makes a HIGHER LOW (fails to break the previous low),
         (3) close crosses ABOVE the minor peak between the low and the higher low  -> enter; stop below the higher low.
   SHORT is the mirror image.
2. Profit protection: at +1R move stop to breakeven; then never give back more than 50% of the best open gain.
3. Sizing: results in R (1R = 1% of capital risked, ATR-based stops) - so a stop-out costs ~1%.
4. Never average a loss: one position at a time per market, no adding.

Also tests rule 2 on the existing strategies (with vs without profit protection).
Writes data/sperandeo_report.md and sends a Telegram summary. Not investment advice.
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import bot
import research
from bot import MARKETS, MARKET_BY_ID, add_indicators, clean, fetch, log, stats, to_daily_index

K = 3            # swing point = highest/lowest of 3 bars on each side (confirmed 3 bars later)
MAX_WAIT = 40    # bars allowed between step 1 and step 3


def swings(d):
    H, L, n = d.High.to_numpy(), d.Low.to_numpy(), len(d)
    sh, sl = [], []
    for i in range(K, n - K):
        if H[i] == H[i - K:i + K + 1].max():
            sh.append((i, H[i]))
        if L[i] == L[i - K:i + K + 1].min():
            sl.append((i, L[i]))
    return sh, sl


def sig_123(d):
    """Returns (signal array +1/-1/0, stop array)."""
    n = len(d)
    C, H, L = d.Close.to_numpy(), d.High.to_numpy(), d.Low.to_numpy()
    sh, sl = swings(d)
    sig, stop = np.zeros(n), np.full(n, np.nan)
    for side in (1, -1):
        peaks = sh if side == 1 else sl          # trendline points (lower highs for longs)
        troughs = sl if side == 1 else sh
        state = None
        for t in range(n):
            known_p = [p for p in peaks if p[0] + K <= t]
            known_t = [p for p in troughs if p[0] + K <= t]
            if state is None:
                if len(known_p) < 2:
                    continue
                (i1, p1), (i2, p2) = known_p[-2], known_p[-1]
                if not ((p2 < p1) if side == 1 else (p2 > p1)) or t <= i2:
                    continue
                line = p2 + (p2 - p1) / (i2 - i1) * (t - i2)
                if side * (C[t] - line) > 0:                       # step 1: trendline broken
                    seg = slice(i2, t + 1)
                    extreme_i = i2 + (int(np.argmin(L[seg])) if side == 1 else int(np.argmax(H[seg])))
                    state = dict(t1=t, ext_i=extreme_i, ext=L[extreme_i] if side == 1 else H[extreme_i], retest=None)
                continue
            if t - state["t1"] > MAX_WAIT or side * ((L[t] if side == 1 else H[t]) - state["ext"]) < 0:
                state = None                                        # failed: made a new extreme / took too long
                continue
            if state["retest"] is None:                             # step 2: higher low (or lower high) confirmed
                cand = [q for q in known_t if q[0] > state["ext_i"] and side * (q[1] - state["ext"]) > 0]
                if cand:
                    ri, rp = cand[-1]
                    seg = slice(state["ext_i"], ri + 1)
                    minor = H[seg].max() if side == 1 else L[seg].min()
                    state.update(retest=(ri, rp), minor=minor)
                continue
            if side * (C[t] - state["minor"]) > 0:                  # step 3: crosses the minor peak/trough
                if sig[t] == 0:
                    sig[t], stop[t] = side, state["retest"][1]
                state = None
    return sig, stop


def backtest_managed(d, sig, stops, stop_atr, rr, hold, cost_side, protect):
    """One trade at a time (never average). Optional profit protection (breakeven at +1R, keep >= 50% of best gain)."""
    O, H, L, C, A = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "atr"))
    n, out, i = len(d), [], 200
    while i < n - 1:
        s = int(sig[i])
        if s == 0 or not np.isfinite(A[i]):
            i += 1; continue
        e = O[i + 1]
        st = stops[i] if stops is not None and np.isfinite(stops[i]) else e - s * stop_atr * A[i]
        risk = s * (e - st)
        if risk <= 0.2 * A[i] or risk > 4 * A[i]:                   # stop too tight / too wide -> skip (mistake guard)
            i += 1; continue
        tgt = e + s * rr * risk
        best, r, j = 0.0, None, i + 1
        last = min(n - 1, i + hold)
        while j <= last:
            if j > i + 1 and s * (O[j] - st) <= 0:
                r = s * (O[j] - e) / risk; break
            if (L[j] <= st) if s == 1 else (H[j] >= st):
                r = s * (st - e) / risk; break
            if (H[j] >= tgt) if s == 1 else (L[j] <= tgt):
                r = rr; break
            if protect:                                             # update stop AFTER this bar (no look-ahead)
                best = max(best, s * ((H[j] if s == 1 else L[j]) - e) / risk)
                if best >= 1.0:
                    lock = max(0.0, 0.5 * best)
                    st = e + s * lock * risk if s * ((e + s * lock * risk) - st) > 0 else st
            j += 1
        if r is None:
            if i + hold > n - 1:
                break
            j = last
            r = s * (C[j] - e) / risk
        r -= 2 * cost_side * e / risk
        out.append(dict(date=str(d.index[i + 1].date()), r=float(r)))
        i = j + 1
    return out


def main():
    frames = {}
    for m in MARKETS:
        try:
            frames[m["id"]] = add_indicators(to_daily_index(fetch(m["ticker"], "6y", "1d")[0])).iloc[-(5 * 252 + 200):]
        except Exception as e:
            log(f"{m['id']}: {e}")
    india_cost = 0.0012
    rows = []
    # 1-2-3 on all markets, both directions + India buy-only (Kite delivery)
    for variant in ("all markets, long+short", "India only, buy-only (Kite delivery)"):
        for protect in (False, True):
            pooled, per = [], {}
            for mid, d in frames.items():
                m = MARKET_BY_ID[mid]
                if variant.startswith("India") and not m["group"].startswith("india"):
                    continue
                sig, stops = sig_123(d)
                if variant.startswith("India"):
                    sig = np.clip(sig, 0, None)
                cost = india_cost if variant.startswith("India") else m["cost"]
                tr = backtest_managed(d, sig, stops, 2.0, 2.0, 30, cost, protect)
                per[mid] = stats([t["r"] for t in tr])
                pooled += tr
            pooled.sort(key=lambda t: t["date"])
            st = stats([t["r"] for t in pooled])
            rows.append(dict(strategy="1-2-3 trend change", variant=variant, protect=protect, stats=st,
                             verdict=research.verdict(st, 30), per_market=per))
    # Rule 2 applied to the existing strategies
    rule2 = []
    for key, p in research.ALL.items():
        res = {}
        for protect in (False, True):
            pooled = []
            for mid, d in frames.items():
                sig = p["fn"](d).fillna(0).to_numpy()
                pooled += backtest_managed(d, sig, None, p["stop_atr"], p["rr"], p["hold"], MARKET_BY_ID[mid]["cost"], protect)
            pooled.sort(key=lambda t: t["date"])
            res[protect] = stats([t["r"] for t in pooled])
        rule2.append(dict(name=p["name"], off=res[False], on=res[True]))

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = [f"# Sperandeo rules - backtest ({gen})", "",
          "Daily candles, ~5 years, after costs. R = result in units of risk (1R = 1% of capital with ATR/structure stops). "
          "'Protect' = stop to breakeven at +1R, then keep at least 50% of the best open gain. One trade at a time (never average). "
          "Not investment advice.", "",
          "## 1-2-3 trend change", "| Variant | Profit protection | Verdict | Trades | Win % | Avg R | Profit factor | Recent 30% avg R | Max DD R |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["stats"]
        md.append(f"| {r['variant']} | {'ON' if r['protect'] else 'off'} | {r['verdict']} | {s['trades']} | {s['win_rate']} | {s['expectancy']} | "
                  f"{s['profit_factor']} | {s['oos_expectancy']} | {s['max_dd_r']} |")
    md += ["", "### 1-2-3 best markets (all markets, protection ON, 5+ trades)"]
    on = [r for r in rows if r["variant"].startswith("all") and r["protect"]][0]
    for mid, s in sorted(on["per_market"].items(), key=lambda x: -(x[1]["expectancy"] or -9)):
        if s["trades"] >= 5:
            md.append(f"- {MARKET_BY_ID[mid]['name']}: {s['trades']} trades, win {s['win_rate']}%, avg {s['expectancy']}R")
    md += ["", "## Rule 2 (never let a profit turn into a loss) on the existing strategies",
           "| Strategy | Avg R without | Avg R WITH protection | Win % without → with | Max DD R without → with |", "|---|---|---|---|---|"]
    for r in sorted(rule2, key=lambda r: -((r["on"]["expectancy"] or -9) - (r["off"]["expectancy"] or -9))):
        a, b = r["off"], r["on"]
        md.append(f"| {r['name']} | {a['expectancy']} | {b['expectancy']} | {a['win_rate']} → {b['win_rate']} | {a['max_dd_r']} → {b['max_dd_r']} |")
    better = sum(1 for r in rule2 if (r["on"]["expectancy"] or -9) > (r["off"]["expectancy"] or -9))
    md += ["", f"Profit protection improved the average result in **{better} of {len(rule2)}** strategies."]
    (bot.DATA / "sperandeo_report.md").write_text("\n".join(md))
    (bot.DATA / "sperandeo_report.json").write_text(json.dumps(clean(dict(generated_at=gen, rows=rows, rule2=rule2)), indent=1))
    print("\n".join(md))

    T = ["📐 <b>Sperandeo rules - 5-year backtest</b>", "", "<b>1-2-3 trend change</b>"]
    for r in rows:
        s = r["stats"]
        icon = {"PASS": "✅", "WEAK +": "🟡", "LOSES": "❌"}.get(r["verdict"], "•")
        T.append(f"{icon} {html.escape(r['variant'])}, protection {'ON' if r['protect'] else 'off'}: "
                 f"win {s['win_rate']}% · avg {s['expectancy']}R · {s['trades']} trades")
    T += ["", f"<b>Rule 'never let a profit become a loss'</b>: improved {better} of {len(rule2)} existing strategies."]
    for r in sorted(rule2, key=lambda r: -((r["on"]["expectancy"] or -9) - (r["off"]["expectancy"] or -9)))[:3]:
        T.append(f"• {html.escape(r['name'])}: {r['off']['expectancy']}R → <b>{r['on']['expectancy']}R</b>")
    T.append("\n<i>Full report: data/sperandeo_report.md. Not investment advice.</i>")
    try:
        stt = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        stt = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(stt)
    ok, msg = bot.telegram("\n".join(T))
    log(f"telegram: {msg}")


if __name__ == "__main__":
    main()
