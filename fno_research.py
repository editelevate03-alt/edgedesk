#!/usr/bin/env python3
"""
EdgeDesk F&O research - NIFTY / BANK NIFTY option BUYING with a small account (default Rs 2,000).

How it works (honest simulation, free data):
- Index candles from Yahoo: 15-minute (last ~60 days), 1-hour (last ~2 years), daily (~5 years).
- Strategies give an index direction (UP -> buy CE, DOWN -> buy PE).
- Option prices are ESTIMATED with Black-Scholes using India VIX as volatility (no free historical
  NSE option data exists). Real prices differ (skew, spreads), usually a bit worse for buyers.
- Strike: the nearest strike whose 1-lot cost fits your capital ("affordable"), and ATM for comparison.
- Expiry: NIFTY weekly (Tuesday), BANK NIFTY monthly (last Tuesday). Rolled if < 1 hour left.
- Exits: premium stop-loss (-40%), index stop / target, time exit (intraday: 15:15 IST).
- Costs: Zerodha Rs 20 x 2 brokerage + STT/exchange/GST/stamp (~Rs 25) + 0.3 pt slippage per side.

Results in rupees per trade for 1 lot. Writes data/fno_report.md/json and sends a Telegram summary.
Paper research only. Not investment advice. Past results do not guarantee future results.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
from datetime import datetime, time as dtime, timedelta, timezone

import numpy as np
import pandas as pd

import bot
from bot import IST, add_indicators, clean, fetch, log, stats

CAPITAL = float(os.getenv("FNO_CAPITAL", "2000"))
UNDERLYINGS = {
    "NIFTY": dict(ticker="^NSEI", lot=int(os.getenv("NIFTY_LOT", "65")), step=50, expiry="weekly"),
    "BANKNIFTY": dict(ticker="^NSEBANK", lot=int(os.getenv("BANKNIFTY_LOT", "30")), step=100, expiry="monthly"),
}
PREMIUM_SL = 0.40          # exit if option loses 40%
COST_FIXED = 65.0          # Rs per round trip (brokerage 40 + taxes/charges ~25)
SLIP_PTS = 0.3             # option points slippage per side
RISK_FREE = 0.065
ALL_TRADES: list = []


# ---------------- option pricing ----------------
def _N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, sig, cp):
    if T <= 1e-6 or sig <= 0:
        return max(0.0, cp * (S - K))
    st = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sig * sig) * T) / st
    d2 = d1 - st
    if cp == 1:
        return S * _N(d1) - K * math.exp(-RISK_FREE * T) * _N(d2)
    return K * math.exp(-RISK_FREE * T) * _N(-d2) - S * _N(-d1)


def next_expiry(ts: pd.Timestamp, kind: str) -> pd.Timestamp:
    """Next Tuesday 15:30 IST (weekly) or last Tuesday of month (monthly)."""
    t = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize(IST)
    d = t.normalize()
    for add in range(0, 70):
        day = d + pd.Timedelta(days=add)
        if day.weekday() != 1:
            continue
        if kind == "monthly" and (day + pd.Timedelta(days=7)).month == day.month:
            continue
        exp = day + pd.Timedelta(hours=15, minutes=30)
        if exp - t > pd.Timedelta(hours=1):
            return exp
    return t + pd.Timedelta(days=7)


SESSION_MIN = 375          # 09:15-15:30 IST
TRADING_YEAR_MIN = 252 * SESSION_MIN


def _session_min_left_today(t: pd.Timestamp) -> float:
    m = t.hour * 60 + t.minute + t.second / 60
    return float(min(max(15 * 60 + 30 - m, 0), SESSION_MIN))


def years(a: pd.Timestamp, b: pd.Timestamp) -> float:
    """Time to expiry in TRADING years (variance only accrues while the market is open).
    Calendar-hour time would make intraday option buying look far too good."""
    a, b = a.tz_convert(IST), b.tz_convert(IST)
    if b <= a:
        return 0.0
    if a.date() == b.date():
        return max(0.0, _session_min_left_today(a) - _session_min_left_today(b)) / TRADING_YEAR_MIN
    full_days = int(np.busday_count(a.date() + timedelta(days=1), b.date()))
    mins = _session_min_left_today(a) + full_days * SESSION_MIN + (SESSION_MIN - _session_min_left_today(b))
    return max(0.0, mins) / TRADING_YEAR_MIN


def pick_strike(S, T, sig, cp, u, mode):
    atm = round(S / u["step"]) * u["step"]
    if mode == "atm":
        return atm, bs(S, atm, T, sig, cp)
    for k in range(0, 40):
        K = atm + cp * k * u["step"]
        p = bs(S, K, T, sig, cp)
        if p < 0.5:
            return None, None
        if (p + SLIP_PTS) * u["lot"] <= CAPITAL:
            return K, p
    return None, None


# ---------------- indicators & strategies ----------------
def supertrend(d, n=10, mult=3.0):
    hl2 = (d.High + d.Low) / 2
    atr = d["atr"] if n == 14 else (pd.concat([d.High - d.Low, (d.High - d.Close.shift()).abs(), (d.Low - d.Close.shift()).abs()], axis=1)
                                     .max(axis=1).ewm(alpha=1 / n, adjust=False).mean())
    up, dn = (hl2 - mult * atr).to_numpy(), (hl2 + mult * atr).to_numpy()
    c = d.Close.to_numpy()
    trend = np.ones(len(d))
    fu, fd = up.copy(), dn.copy()
    for i in range(1, len(d)):
        fu[i] = max(up[i], fu[i - 1]) if c[i - 1] > fu[i - 1] else up[i]
        fd[i] = min(dn[i], fd[i - 1]) if c[i - 1] < fd[i - 1] else dn[i]
        trend[i] = 1 if c[i] > fd[i - 1] else (-1 if c[i] < fu[i - 1] else trend[i - 1])
    return pd.Series(trend, index=d.index)


def prep_intraday(df):
    d = add_indicators(df)
    d["day"] = [t.date() for t in d.index]
    d["tmin"] = [t.hour * 60 + t.minute for t in d.index]
    d["twap"] = d.groupby("day")["Close"].transform(lambda s: s.expanding().mean())   # index has no volume
    d["ema9"] = d.Close.ewm(span=9, adjust=False).mean()
    d["ema21"] = d.Close.ewm(span=21, adjust=False).mean()
    d["st"] = supertrend(d)
    d["last_of_day"] = d["day"] != d["day"].shift(-1)
    return d


def sig_orb(d, bars_in_range):
    """Opening-range breakout: first 30-60 min range, first breakout of the day before 13:00."""
    out = np.zeros(len(d)); stop = np.full(len(d), np.nan)
    for day, g in d.groupby("day", sort=False):
        idx = [d.index.get_loc(t) for t in g.index]
        if len(idx) <= bars_in_range:
            continue
        rh = d.High.iloc[idx[:bars_in_range]].max(); rl = d.Low.iloc[idx[:bars_in_range]].min()
        for i in idx[bars_in_range:]:
            if d.tmin.iloc[i] > 13 * 60:
                break
            if d.Close.iloc[i] > rh:
                out[i], stop[i] = 1, rl; break
            if d.Close.iloc[i] < rl:
                out[i], stop[i] = -1, rh; break
    return out, stop


def sig_twap(d):
    c, t = d.Close, d.twap
    up = (c > t) & (c.shift() <= t.shift()) & (d.ema21 > d.ema50) & (d.tmin >= 10 * 60) & (d.tmin <= 14 * 60)
    dn = (c < t) & (c.shift() >= t.shift()) & (d.ema21 < d.ema50) & (d.tmin >= 10 * 60) & (d.tmin <= 14 * 60)
    return (up.astype(int) - dn.astype(int)).to_numpy(), None


def sig_st(d):
    s = d.st
    ok = (d.tmin >= 9 * 60 + 45) & (d.tmin <= 14 * 60)
    up = (s == 1) & (s.shift() == -1) & ok
    dn = (s == -1) & (s.shift() == 1) & ok
    return (up.astype(int) - dn.astype(int)).to_numpy(), None


def sig_ema921(d):
    ok = (d.tmin >= 9 * 60 + 45) & (d.tmin <= 14 * 60)
    up = (d.ema9 > d.ema21) & (d.ema9.shift() <= d.ema21.shift()) & (d.rsi14 > 50) & ok
    dn = (d.ema9 < d.ema21) & (d.ema9.shift() >= d.ema21.shift()) & (d.rsi14 < 50) & ok
    return (up.astype(int) - dn.astype(int)).to_numpy(), None


def sig_daily_ema(d):
    up = (d.ema20 > d.ema50) & (d.ema20.shift() <= d.ema50.shift())
    dn = (d.ema20 < d.ema50) & (d.ema20.shift() >= d.ema50.shift())
    return (up.astype(int) - dn.astype(int)).to_numpy(), None


def sig_daily_breakout(d):
    up = (d.Close > d.dc_hi) & (d.Close > d.ema50)
    dn = (d.Close < d.dc_lo) & (d.Close < d.ema50)
    return (up.astype(int) - dn.astype(int)).to_numpy(), None


def sig_daily_st(d):
    s = supertrend(d)
    return (((s == 1) & (s.shift() == -1)).astype(int) - ((s == -1) & (s.shift() == 1)).astype(int)).to_numpy(), None


INTRADAY = {
    "orb": ("Opening-range breakout", None, 2.0),
    "twap": ("VWAP/TWAP cross with trend", 1.0, 2.0),
    "supertrend": ("Supertrend (10,3) flip", 1.0, 2.0),
    "ema921": ("EMA 9/21 cross + RSI", 1.0, 2.0),
}
DAILY = {
    "d_ema": ("Daily EMA20/50 cross", sig_daily_ema, 2.0, 2.5, 5),
    "d_breakout": ("Daily 20-day breakout", sig_daily_breakout, 2.0, 2.0, 5),
    "d_supertrend": ("Daily Supertrend flip", sig_daily_st, 2.0, 2.0, 5),
}


# ---------------- trade engine ----------------
def simulate(d, sig, stops, u, vix, stop_atr, rr, intraday, max_hold, mode):
    """Returns list of trades with rupee P&L for 1 lot of the chosen option."""
    trades, i, n = [], 0, len(d)
    O, H, L, C, A = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "atr"))
    idx = d.index
    while i < n - 1:
        s = int(sig[i])
        if s == 0 or not np.isfinite(A[i]):
            i += 1; continue
        if intraday and d.last_of_day.iloc[i]:
            i += 1; continue
        j0 = i + 1
        t0 = idx[j0] if intraday else (pd.Timestamp(idx[j0]).tz_localize(IST) + pd.Timedelta(hours=9, minutes=20))
        S0 = O[j0]
        iv = vix.get(pd.Timestamp(t0.date()), np.nan)
        if not np.isfinite(iv):
            i += 1; continue
        sig_v = iv / 100
        exp = next_expiry(t0, u["expiry"])
        K, p0 = pick_strike(S0, years(t0, exp), sig_v, s, u, mode)
        if K is None:
            trades.append(dict(time=str(t0), dir=s, skipped=True)); i += 1; continue
        if stops is not None and np.isfinite(stops[i]):
            ist = stops[i]
            if abs(S0 - ist) < 0.5 * A[i]:
                ist = S0 - s * 0.5 * A[i]
        else:
            ist = S0 - s * stop_atr * A[i]
        risk = abs(S0 - ist)
        itg = S0 + s * rr * risk
        entry = p0 + SLIP_PTS
        sl_prem = entry * (1 - PREMIUM_SL)
        exit_p, reason, j = None, None, j0
        last = min(n - 1, j0 + max_hold)
        while j <= last:
            tj = idx[j] if intraday else (pd.Timestamp(idx[j]).tz_localize(IST) + pd.Timedelta(hours=15, minutes=15))
            T = years(tj, exp)
            adverse = L[j] if s == 1 else H[j]
            favour = H[j] if s == 1 else L[j]
            if bs(adverse, K, T, sig_v, s) <= sl_prem:
                exit_p, reason = sl_prem, "premium SL"; break
            if s * (adverse - ist) <= 0:
                exit_p, reason = bs(ist, K, T, sig_v, s), "index stop"; break
            if s * (favour - itg) >= 0:
                exit_p, reason = bs(itg, K, T, sig_v, s), "target"; break
            if (intraday and (d.tmin.iloc[j] >= 15 * 60 + 15 or d.last_of_day.iloc[j])) or j == last or tj >= exp - pd.Timedelta(minutes=30):
                exit_p, reason = bs(C[j], K, T, sig_v, s), "time exit"; break
            j += 1
        if exit_p is None:
            break
        exit_p = max(0.05, exit_p - SLIP_PTS)
        pnl = (exit_p - entry) * u["lot"] - COST_FIXED
        trades.append(dict(time=str(t0), dir=s, strike=K, entry=round(entry, 2), exit=round(exit_p, 2), reason=reason,
                           cost=round(entry * u["lot"], 0), pnl=round(pnl, 0), index_win=reason == "target",
                           skipped=False))
        i = j + 1
    return trades


def summarise(trades):
    done = [t for t in trades if not t.get("skipped")]
    skipped = len(trades) - len(done)
    p = np.array([t["pnl"] for t in done]) if done else np.array([])
    if not len(p):
        return dict(trades=0, skipped=skipped)
    wins, losses = p[p > 0].sum(), -p[p < 0].sum()
    eq = np.cumsum(p)
    k = max(1, int(len(p) * 0.3))
    return dict(trades=len(p), skipped=skipped, win_rate=round(float((p > 0).mean() * 100), 1),
                avg_rs=round(float(p.mean()), 0), total_rs=round(float(p.sum()), 0),
                profit_factor=round(float(wins / losses), 2) if losses > 0 else None,
                max_dd_rs=round(float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()), 0),
                recent_avg_rs=round(float(p[-k:].mean()), 0),
                t_stat=round(float(p.mean() / (p.std(ddof=1) / math.sqrt(len(p)))), 2) if len(p) > 2 and p.std() > 0 else 0.0,
                avg_cost_rs=round(float(np.mean([t["cost"] for t in done])), 0),
                index_accuracy=round(float(np.mean([t["index_win"] for t in done]) * 100), 1))


def verdict(s, min_n=40):
    """PASS needs: 40+ trades, profit after costs, PF >= 1.3, positive recently, and t-stat >= 2
    (profit unlikely to be luck). Random signals on fair data must NOT pass."""
    if not s.get("trades"):
        return "NO TRADES"
    if (s["trades"] >= min_n and s["avg_rs"] > 0 and (s["profit_factor"] or 0) >= 1.3
            and s["recent_avg_rs"] > 0 and s.get("t_stat", 0) >= 2.0):
        return "PASS"
    return "WEAK +" if s["avg_rs"] > 0 else "LOSES"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()
    vix_df, _ = fetch("^INDIAVIX", "6y", "1d")
    vix = bot.to_daily_index(vix_df)["Close"]
    vix = vix.reindex(pd.date_range(vix.index.min(), pd.Timestamp.today().normalize())).ffill()
    vix = {k: float(v) for k, v in vix.items()}
    results, errors = [], []
    global ALL_TRADES
    ALL_TRADES = []
    for name, u in UNDERLYINGS.items():
        frames = {}
        for tf, period, interval in (("15m", "60d", "15m"), ("1h", "730d", "1h"), ("1d", "6y", "1d")):
            try:
                df, _ = fetch(u["ticker"], period, interval)
                if tf == "1d":
                    frames[tf] = add_indicators(bot.to_daily_index(df))
                else:
                    if df.index.tz is None:
                        df.index = df.index.tz_localize("UTC")
                    df.index = df.index.tz_convert(IST)
                    df = df[(df.index.time >= dtime(9, 15)) & (df.index.time < dtime(15, 30))]
                    frames[tf] = prep_intraday(df)
                log(f"fno data {name} {tf}: {len(frames[tf])} bars")
            except Exception as e:
                errors.append(f"{name} {tf}: {e}")
        for tf in ("15m", "1h"):
            d = frames.get(tf)
            if d is None or len(d) < 100:
                continue
            for key, (label, stop_atr, rr) in INTRADAY.items():
                if key == "orb":
                    sig, stops = sig_orb(d, 2 if tf == "15m" else 1)
                else:
                    sig, stops = {"twap": sig_twap, "supertrend": sig_st, "ema921": sig_ema921}[key](d)
                hold = 26 if tf == "15m" else 7
                for mode in ("affordable", "atm"):
                    tr = simulate(d, sig, stops, u, vix, stop_atr or 1.0, rr, True, hold, mode)
                    s = summarise(tr)
                    results.append(dict(underlying=name, chart=tf, strategy=label, mode=mode, stats=s, verdict=verdict(s),
                                        last_trades=[t for t in tr if not t.get("skipped")][-5:]))
        d = frames.get("1d")
        if d is not None and len(d) > 250:
            d = d.iloc[-5 * 252:]
            for key, (label, fn, stop_atr, rr, hold) in DAILY.items():
                sig, stops = fn(d)
                for mode in ("affordable", "atm"):
                    tr = simulate(d, sig, stops, u, vix, stop_atr, rr, False, hold, mode)
                    ALL_TRADES.extend(dict(index=name, chart="1d", strategy=label, mode=mode, **t) for t in tr if not t.get("skipped"))
                    s = summarise(tr)
                    results.append(dict(underlying=name, chart="1d", strategy=label, mode=mode, stats=s, verdict=verdict(s),
                                        last_trades=[t for t in tr if not t.get("skipped")][-5:]))

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    aff = [r for r in results if r["mode"] == "affordable"]
    aff.sort(key=lambda r: -(r["stats"].get("avg_rs") or -1e9))
    atm = {(r["underlying"], r["chart"], r["strategy"]): r for r in results if r["mode"] == "atm"}
    md = [f"# EdgeDesk F&O research - option BUYING with Rs {CAPITAL:,.0f} - {gen}", "",
          "Option prices are **estimates** (Black-Scholes + India VIX); real fills are usually a bit worse for buyers. "
          "Rupees per trade for 1 lot, after Zerodha charges and slippage. **Win %** = trades that made money (your 'accuracy'). "
          "Index accuracy = % of trades where the index reached the target. Paper research - not investment advice.", "",
          f"Lots: NIFTY {UNDERLYINGS['NIFTY']['lot']}, BANK NIFTY {UNDERLYINGS['BANKNIFTY']['lot']} (check current lot sizes on Kite). "
          "Charts: 15m = last ~60 days only (small sample), 1h = ~2 years, 1d = ~5 years.", "",
          f"## With Rs {CAPITAL:,.0f} (cheapest strike whose 1 lot fits)",
          "| Index | Chart | Strategy | Verdict | Trades | Skipped (too expensive) | Win % | Avg Rs/trade | Total Rs | Max drawdown Rs | Recent avg Rs | Luck test (t ≥ 2 = real) | Avg lot cost Rs |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in aff:
        s = r["stats"]
        md.append(f"| {r['underlying']} | {r['chart']} | {r['strategy']} | {r['verdict']} | {s.get('trades', 0)} | {s.get('skipped', 0)} | "
                  f"{s.get('win_rate', '-')} | {s.get('avg_rs', '-')} | {s.get('total_rs', '-')} | {s.get('max_dd_rs', '-')} | {s.get('recent_avg_rs', '-')} | {s.get('t_stat', '-')} | {s.get('avg_cost_rs', '-')} |")
    md += ["", "## Same strategies with ATM options (needs more capital)",
           "| Index | Chart | Strategy | Verdict | Trades | Win % | Index accuracy % | Avg Rs/trade | Total Rs | Avg lot cost Rs |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(atm.values(), key=lambda r: -(r["stats"].get("avg_rs") or -1e9)):
        s = r["stats"]
        md.append(f"| {r['underlying']} | {r['chart']} | {r['strategy']} | {r['verdict']} | {s.get('trades', 0)} | {s.get('win_rate', '-')} | "
                  f"{s.get('index_accuracy', '-')} | {s.get('avg_rs', '-')} | {s.get('total_rs', '-')} | {s.get('avg_cost_rs', '-')} |")
    passing = [r for r in aff if r["verdict"] == "PASS"]
    md += ["", "## Passed with your capital", *([f"- {r['underlying']} {r['chart']} {r['strategy']}: win {r['stats']['win_rate']}%, avg Rs {r['stats']['avg_rs']}/trade over {r['stats']['trades']} trades" for r in passing] or ["- **None.**"])]
    if errors:
        md += ["", "Data errors: " + "; ".join(errors)]
    if ALL_TRADES:   # every 5-year daily trade, one row each (open in Google Sheets / Excel)
        tdf = pd.DataFrame(ALL_TRADES)
        tdf["dir"] = tdf["dir"].map({1: "CE (up)", -1: "PE (down)"})
        tdf["result"] = np.where(tdf["pnl"] > 0, "WIN", "LOSS")
        tdf = tdf[["index", "chart", "strategy", "mode", "time", "dir", "strike", "entry", "exit", "reason", "cost", "pnl", "result"]]
        tdf.columns = ["Index", "Chart", "Strategy", "Strike type", "Entry time", "Option", "Strike", "Entry premium",
                       "Exit premium", "Exit reason", "Lot cost Rs", "Profit/Loss Rs", "Result"]
        tdf.sort_values(["Index", "Strategy", "Strike type", "Entry time"]).to_csv(bot.DATA / "trades_5y.csv", index=False)
        yr = tdf.assign(Year=tdf["Entry time"].str[:4]).groupby(["Index", "Strategy", "Strike type", "Year"])
        ytab = yr["Profit/Loss Rs"].agg(["count", "sum"]).join(yr["Result"].apply(lambda r: round((r == "WIN").mean() * 100, 1)).rename("win%"))
        md += ["", "## Daily strategies year by year (5 years)", "| Index | Strategy | Strike | Year | Trades | Win % | Total Rs |", "|---|---|---|---|---|---|---|"]
        md += [f"| {i} | {s_} | {m} | {y} | {int(r['count'])} | {r['win%']} | {r['sum']:,.0f} |" for (i, s_, m, y), r in ytab.iterrows()]
        md += ["", f"Every trade: data/trades_5y.csv ({len(tdf)} trades)"]
    (bot.DATA / "fno_report.md").write_text("\n".join(md))
    (bot.DATA / "fno_report.json").write_text(json.dumps(clean(dict(generated_at=gen, capital=CAPITAL, underlyings=UNDERLYINGS,
                                                                  results=results, passing=[(r['underlying'], r['chart'], r['strategy']) for r in passing],
                                                                  errors=errors)), indent=1))
    print("\n".join(md))

    top = aff[:6]
    T = [f"<b>EdgeDesk F&O report - option buying with Rs {CAPITAL:,.0f}</b>",
         "NIFTY & BANK NIFTY · after Zerodha charges · win % = accuracy", "", "<b>Top 6 by average rupees per trade</b>"]
    for r in top:
        s = r["stats"]
        icon = {"PASS": "✅", "WEAK +": "🟡", "LOSES": "❌"}.get(r["verdict"], "•")
        T.append(f"{icon} {r['underlying']} {r['chart']} · {html.escape(r['strategy'])}: win {s.get('win_rate', '-')}% · "
                 f"avg Rs {s.get('avg_rs', '-')} · total Rs {s.get('total_rs', '-')} · {s.get('trades', 0)} trades")
    T += ["", "<b>Passed with your capital:</b> " + (", ".join(f"{r['underlying']} {r['chart']} {html.escape(r['strategy'])}" for r in passing) or "NONE"),
          "", "<i>Option prices are estimates. Not investment advice. Not guaranteed. Full report on GitHub: data/fno_report.md</i>"]
    if not args.no_telegram:
        state = json.loads((bot.DATA / "state.json").read_text()) if (bot.DATA / "state.json").exists() else {}
        os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(state)
        ok, msg = bot.telegram("\n".join(T))
        log(f"telegram: {msg}")


if __name__ == "__main__":
    main()
