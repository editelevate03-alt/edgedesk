#!/usr/bin/env python3
"""
EdgeDesk strategy search with walk-forward validation ("the bot's own intelligence").

For NIFTY / BANK NIFTY ATM option buying (1 lot):
- Tries many settings of every strategy on 1-hour (~2 years) and daily (~5 years) charts.
- TRAIN = first 70% of the time, TEST = last 30% (data the settings never saw).
- GREEN only if: profitable after costs on TRAIN (PASS incl. luck test) AND profitable on TEST
  (avg > 0, profit factor >= 1.2, 15+ trades) AND whole-period luck test t >= 2.
- Many combinations are tested, so some look good by luck - the unseen TEST period is the guard.

Writes data/green_strategies.json (used by the live bot) and data/search_report.md; sends a Telegram summary.
Option prices are estimates (Black-Scholes + India VIX, trading-time clock). Not investment advice.
"""
from __future__ import annotations

import html
import itertools
import json
import os
from datetime import datetime, time as dtime, timezone

import numpy as np
import pandas as pd

import bot
import fno_research as F
from bot import IST, add_indicators, clean, fetch, log


def cutoff(sig, d, last_min):
    s = sig.copy()
    s[(d.tmin > last_min).to_numpy()] = 0
    return s


def split_stats(trades, split_ts):
    done = [t for t in trades if not t.get("skipped")]
    tr = [t for t in done if pd.Timestamp(t["time"]) < split_ts]
    te = [t for t in done if pd.Timestamp(t["time"]) >= split_ts]
    return F.summarise(tr), F.summarise(te), F.summarise(done)


def is_green(tr, te, full):
    return (tr.get("trades", 0) >= 30 and F.verdict(tr, min_n=30) == "PASS"
            and te.get("trades", 0) >= 15 and te["avg_rs"] > 0 and (te.get("profit_factor") or 0) >= 1.2
            and full.get("t_stat", 0) >= 2.0)


def main():
    vix_df, _ = fetch("^INDIAVIX", "6y", "1d")
    vix = bot.to_daily_index(vix_df)["Close"]
    vix = vix.reindex(pd.date_range(vix.index.min(), pd.Timestamp.today().normalize())).ffill()
    vix = {k: float(v) for k, v in vix.items()}
    rows, tested = [], 0
    for name, u in F.UNDERLYINGS.items():
        # ---- 1-hour intraday ----
        df, _ = fetch(u["ticker"], "730d", "1h")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(IST)
        df = df[(df.index.time >= dtime(9, 15)) & (df.index.time < dtime(15, 30))]
        d = F.prep_intraday(df)
        split = d.index[int(len(d) * 0.7)]
        base = {"orb": F.sig_orb(d, 1), "twap": F.sig_twap(d), "supertrend": F.sig_st(d), "ema921": F.sig_ema921(d)}
        for (key, (sig, stops)), stop_atr, rr, psl, last in itertools.product(
                base.items(), (0.75, 1.0, 1.5), (1.5, 2.0, 3.0), (0.3, 0.4, 0.5), (12 * 60 + 15, 14 * 60 + 15)):
            F.PREMIUM_SL = psl
            s2 = cutoff(sig, d, last)
            trades = F.simulate(d, s2, stops if key == "orb" else None, u, vix, stop_atr, rr, True, 7, "atm")
            tested += 1
            tr, te, full = split_stats(trades, split)
            rows.append(dict(underlying=name, chart="1h", strategy=key, label=F.INTRADAY[key][0],
                             params=dict(stop_atr=stop_atr, rr=rr, premium_sl=psl, last_entry=f"{last // 60}:{last % 60:02d}"),
                             train=tr, test=te, full=full, green=is_green(tr, te, full)))
        log(f"search {name} 1h done ({tested} tested)")
        # ---- daily ----
        dd, _ = fetch(u["ticker"], "6y", "1d")
        dd = add_indicators(bot.to_daily_index(dd)).iloc[-5 * 252:]
        split_d = pd.Timestamp(dd.index[int(len(dd) * 0.7)]).tz_localize(IST)
        for (key, (label, fn, _, _, _)), stop_atr, rr, psl, hold in itertools.product(
                F.DAILY.items(), (1.5, 2.0, 3.0), (1.5, 2.0, 3.0), (0.3, 0.4, 0.5), (2, 5)):
            F.PREMIUM_SL = psl
            sig, stops = fn(dd)
            trades = F.simulate(dd, sig, stops, u, vix, stop_atr, rr, False, hold, "atm")
            tested += 1
            tr, te, full = split_stats(trades, split_d)
            rows.append(dict(underlying=name, chart="1d", strategy=key, label=label,
                             params=dict(stop_atr=stop_atr, rr=rr, premium_sl=psl, hold_days=hold),
                             train=tr, test=te, full=full, green=is_green(tr, te, full)))
        log(f"search {name} daily done ({tested} tested)")
    F.PREMIUM_SL = 0.40

    greens = [r for r in rows if r["green"]]
    greens.sort(key=lambda r: -(r["test"].get("avg_rs") or 0))
    best = sorted(rows, key=lambda r: -((r["test"].get("avg_rs") or -1e9) if r["test"].get("trades", 0) >= 15 else -1e9))[:10]
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def fmt(r):
        t, e, f = r["train"], r["test"], r["full"]
        p = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        return (f"| {'🟢' if r['green'] else '🟡' if (e.get('avg_rs') or 0) > 0 else '🔴'} | {r['underlying']} {r['chart']} | {r['label']} | {p} | "
                f"{t.get('trades', 0)} / {t.get('win_rate', '-')}% / Rs {t.get('avg_rs', '-')} | "
                f"{e.get('trades', 0)} / {e.get('win_rate', '-')}% / Rs {e.get('avg_rs', '-')} | {f.get('t_stat', '-')} | {f.get('avg_cost_rs', '-')} |")
    head = ["| | Index & chart | Strategy | Settings | TRAIN trades / win / avg | TEST (unseen) trades / win / avg | Luck test t | Lot cost Rs |",
            "|---|---|---|---|---|---|---|---|"]
    md = [f"# EdgeDesk strategy search (walk-forward) - {gen}", "",
          f"{tested} combinations tested on NIFTY & BANK NIFTY ATM options (1 lot, after Zerodha charges). "
          "TRAIN = first 70% of data, TEST = last 30% (unseen). 🟢 = passed both + luck test. "
          "Option prices are estimates. Not investment advice.", "",
          f"## 🟢 GREEN strategies ({len(greens)})", *(head + [fmt(r) for r in greens] if greens else ["**None passed.**"]), "",
          "## Best 10 on the unseen TEST period (for reference, mostly not green)", *head, *[fmt(r) for r in best]]
    (bot.DATA / "search_report.md").write_text("\n".join(md))
    (bot.DATA / "green_strategies.json").write_text(json.dumps(clean(dict(
        generated_at=gen, tested=tested,
        green=[dict(underlying=r["underlying"], chart=r["chart"], strategy=r["strategy"], label=r["label"], params=r["params"],
                    test=r["test"], full=r["full"]) for r in greens])), indent=1))
    print("\n".join(md))

    T = [f"<b>EdgeDesk strategy search</b> - {tested} combinations, walk-forward tested", ""]
    if greens:
        T.append(f"🟢 <b>{len(greens)} GREEN</b> (passed past AND unseen recent data):")
        for r in greens[:5]:
            e = r["test"]
            T.append(f"• {r['underlying']} {r['chart']} {html.escape(r['label'])} ({', '.join(f'{k} {v}' for k, v in r['params'].items())}) - "
                     f"unseen: {e['trades']} trades, win {e['win_rate']}%, avg Rs {e['avg_rs']}")
        T.append("\nThe bot will send signals ONLY from these, paper-tracked first.")
    else:
        T.append("🔴 <b>No GREEN strategy.</b> Nothing passed both the past and the unseen recent data. "
                 "The bot will send NO signals rather than yellow ones - protecting your money.")
    T.append("\n<i>Option prices estimated. Not investment advice. Not guaranteed.</i>")
    state = json.loads((bot.DATA / "state.json").read_text()) if (bot.DATA / "state.json").exists() else {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(state)
    ok, msg = bot.telegram("\n".join(T))
    log(f"telegram: {msg}")


if __name__ == "__main__":
    main()
