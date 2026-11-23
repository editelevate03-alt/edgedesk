#!/usr/bin/env python3
"""
EdgeDesk Live - Chief + 10 bots.

A free, paper-trading research tool. It downloads prices, tests 6 strategies
after costs, scores setups 0-100, applies risk rules and a mistake guard,
writes data/signals.json for the website and sends Telegram alerts.

NOT investment advice. Signals are NOT guaranteed. Paper trade first.

Usage:
    python bot.py            # live data (Yahoo Finance)
    python bot.py --demo     # fake offline data, to check everything works
    python bot.py --hello    # live run + send "EdgeDesk is connected" to Telegram
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import random
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
IST = ZoneInfo("Asia/Kolkata")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# --------------------------------------------------------------------------
# Settings (safety rules). Change with care.
# --------------------------------------------------------------------------
RISK_PCT = 1.0                 # risk 1% of the paper account per trade
DAILY_LOSS_LIMIT_R = 3.0       # stop new signals after -3R closed today (= -3%)
MAX_OPEN_TRADES = 5
MAX_SAME_GROUP_SAME_DIR = 2    # correlation guard
MIN_SCORE = 60                 # minimum score for a signal
CHASE_LIMIT_ATR = 0.5          # don't chase: price moved > 0.5 ATR past trigger
STALE_DAYS = 4                 # data older than this = stale
ACCOUNTS = {
    "INR": float(os.getenv("ACCOUNT_INR", "100000")),
    "USD": float(os.getenv("ACCOUNT_USD", "10000")),
}
DISCLAIMER = ("Paper-trading research tool. Not investment advice. Signals are not guaranteed "
              "and can lose money. Past/backtest results do not predict the future.")

# group: india_index | india_stock | fx | metal. cost = one-side cost as a fraction
# of price (brokerage + taxes + spread + slippage estimate).
MARKETS = [
    dict(id="NIFTY", name="NIFTY 50", ticker="^NSEI", group="india_index", ccy="INR", cost=0.0003, tv="NSE:NIFTY", dp=2, unit="index units"),
    dict(id="BANKNIFTY", name="BANK NIFTY", ticker="^NSEBANK", group="india_index", ccy="INR", cost=0.0003, tv="NSE:BANKNIFTY", dp=2, unit="index units"),
    dict(id="RELIANCE", name="Reliance", ticker="RELIANCE.NS", group="india_stock", ccy="INR", cost=0.0008, tv="NSE:RELIANCE", dp=2, unit="shares"),
    dict(id="HDFCBANK", name="HDFC Bank", ticker="HDFCBANK.NS", group="india_stock", ccy="INR", cost=0.0008, tv="NSE:HDFCBANK", dp=2, unit="shares"),
    dict(id="ICICIBANK", name="ICICI Bank", ticker="ICICIBANK.NS", group="india_stock", ccy="INR", cost=0.0008, tv="NSE:ICICIBANK", dp=2, unit="shares"),
    dict(id="SBIN", name="SBI", ticker="SBIN.NS", group="india_stock", ccy="INR", cost=0.0008, tv="NSE:SBIN", dp=2, unit="shares"),
    dict(id="INFY", name="Infosys", ticker="INFY.NS", group="india_stock", ccy="INR", cost=0.0008, tv="NSE:INFY", dp=2, unit="shares"),
    dict(id="TCS", name="TCS", ticker="TCS.NS", group="india_stock", ccy="INR", cost=0.0008, tv="NSE:TCS", dp=2, unit="shares"),
    dict(id="EURUSD", name="EUR/USD", ticker="EURUSD=X", group="fx", ccy="USD", cost=0.00008, tv="FX:EURUSD", dp=5, unit="units"),
    dict(id="GBPUSD", name="GBP/USD", ticker="GBPUSD=X", group="fx", ccy="USD", cost=0.0001, tv="FX:GBPUSD", dp=5, unit="units"),
    dict(id="USDJPY", name="USD/JPY", ticker="JPY=X", group="fx", ccy="USD", cost=0.0001, tv="FX:USDJPY", dp=3, unit="units"),
    dict(id="XAUUSD", name="Gold (XAU/USD)", ticker="GC=F", group="metal", ccy="USD", cost=0.0003, tv="OANDA:XAUUSD", dp=2, unit="oz"),
]
NEWS_KEYWORDS = {
    "NIFTY": ["nifty", "sensex", "indian stocks", "dalal street", "rbi", "india market"],
    "BANKNIFTY": ["bank nifty", "banknifty", "rbi", "bank stocks", "psu bank"],
    "RELIANCE": ["reliance", "ril ", "jio", "mukesh ambani"],
    "HDFCBANK": ["hdfc bank", "hdfc"],
    "ICICIBANK": ["icici"],
    "SBIN": ["sbi", "state bank"],
    "INFY": ["infosys", "infy"],
    "TCS": ["tcs", "tata consultancy"],
    "EURUSD": ["eur/usd", "eurusd", "euro", "ecb", "lagarde"],
    "GBPUSD": ["gbp/usd", "gbpusd", "pound", "sterling", "bank of england", "boe"],
    "USDJPY": ["usd/jpy", "usdjpy", "yen", "boj", "bank of japan"],
    "XAUUSD": ["gold", "xau", "bullion"],
}
USD_WORDS = ["fed ", "federal reserve", "powell", "dollar", "us cpi", "nonfarm", "payrolls", "treasury yields"]
NEWS_FEEDS = [
    ("Google News: India markets", "https://news.google.com/rss/search?q=nifty+OR+sensex+OR+%22bank+nifty%22+when:2d&hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News: Indian stocks", "https://news.google.com/rss/search?q=Reliance+OR+%22HDFC+Bank%22+OR+ICICI+OR+SBI+OR+Infosys+OR+TCS+shares+when:2d&hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News: Forex & gold", "https://news.google.com/rss/search?q=EUR/USD+OR+GBP/USD+OR+USD/JPY+OR+%22gold+price%22+when:2d&hl=en-US&gl=US&ceid=US:en"),
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Google News: Fed & dollar", "https://news.google.com/rss/search?q=%22Federal+Reserve%22+OR+%22US+dollar%22+OR+%22gold+prices%22+when:2d&hl=en-US&gl=US&ceid=US:en"),
]
POS = ["surge", "jump", "rally", "gain", "gains", "rise", "rises", "soar", "record high", "beat", "beats", "upgrade",
       "bullish", "strong", "rebound", "climb", "higher", "profit up", "buy rating", "outperform", "boost"]
NEG = ["fall", "falls", "drop", "drops", "slump", "plunge", "crash", "loss", "losses", "miss", "misses", "downgrade",
       "bearish", "weak", "decline", "slide", "lower", "sell-off", "selloff", "tumble", "cut", "fear", "probe", "fraud"]
HIGH_IMPACT = ["rbi", "fed ", "federal reserve", "rate decision", "interest rate", "inflation", "cpi", "nonfarm",
               "payrolls", "budget", "ecb", "boj", "bank of england", "results", "earnings", "q2", "q3", "q4", "q1", "war"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str) -> None:
    print(f"[{now_utc().strftime('%H:%M:%S')}] {msg}", flush=True)


def clean(o):
    """Make data JSON-safe (numpy types, NaN -> None)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def rnd(x, dp=2):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return round(float(x), dp)
    except Exception:
        return None


class BotLog:
    """Each bot reports ok / warn / error so the website can show health."""

    def __init__(self):
        self.items = {}

    def start(self, key, name, job):
        self.items[key] = dict(key=key, name=name, job=job, status="ok", note="", ms=0, _t=time.time())

    def done(self, key, note="", status="ok"):
        it = self.items[key]
        it["ms"] = int((time.time() - it.pop("_t", time.time())) * 1000)
        it["note"] = note
        if it["status"] != "error":
            it["status"] = status

    def fail(self, key, note):
        it = self.items[key]
        it["status"] = "error"
        it["note"] = note
        it["ms"] = int((time.time() - it.pop("_t", time.time())) * 1000)

    def as_list(self):
        return [{k: v for k, v in it.items() if not k.startswith("_")} for it in self.items.values()]


# --------------------------------------------------------------------------
# 1. Data bot
# --------------------------------------------------------------------------
def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Handle yfinance quirks: MultiIndex columns, lower-case names, timezones, NaN rows."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if c[0] in ("Open", "High", "Low", "Close", "Volume", "Adj Close") else c[-1]
                      for c in df.columns]
    df.columns = [str(c).strip().title().replace("Adj close", "Adj Close") for c in df.columns]
    need = ["Open", "High", "Low", "Close"]
    if not all(c in df.columns for c in need):
        return pd.DataFrame()
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    df = df[need + ["Volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=need)
    df = df[(df["Close"] > 0) & (df["High"] >= df["Low"])]
    df["Volume"] = df["Volume"].fillna(0)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def _yahoo_chart_api(ticker: str, rng: str, interval: str) -> pd.DataFrame:
    """Fallback: Yahoo's public chart endpoint (used if the yfinance library breaks)."""
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ticker)}"
        r = requests.get(url, params={"range": rng, "interval": interval, "includePrePost": "false"},
                         headers=UA, timeout=20)
        if r.status_code != 200:
            continue
        res = (r.json().get("chart") or {}).get("result") or []
        if not res:
            continue
        res = res[0]
        ts = res.get("timestamp") or []
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        tz = (res.get("meta") or {}).get("exchangeTimezoneName") or "UTC"
        idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz)
        df = pd.DataFrame({"Open": q.get("open"), "High": q.get("high"), "Low": q.get("low"),
                           "Close": q.get("close"), "Volume": q.get("volume")}, index=idx)
        return _normalise(df)
    return pd.DataFrame()


def fetch(ticker: str, period: str, interval: str, tries: int = 3) -> tuple[pd.DataFrame, str]:
    last_err = ""
    for attempt in range(tries):
        try:
            import yfinance as yf
            df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False, actions=False)
            df = _normalise(df)
            if len(df):
                return df, "yfinance"
            last_err = "empty data from yfinance"
        except Exception as e:  # rate limits, column changes, network
            last_err = f"yfinance: {type(e).__name__}: {e}"[:200]
        try:
            df = _yahoo_chart_api(ticker, period, interval)
            if len(df):
                return df, "yahoo-chart-api"
        except Exception as e:
            last_err += f" | chart api: {type(e).__name__}: {e}"[:200]
        time.sleep(2 + attempt * 3 + random.random())
    raise RuntimeError(last_err or "no data")


def to_daily_index(df: pd.DataFrame) -> pd.DataFrame:
    """Daily bars: keep the exchange's calendar date, drop the timezone."""
    df = df.copy()
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = pd.DatetimeIndex(idx).normalize()
    return df[~df.index.duplicated(keep="last")]


def demo_data(m: dict, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic prices for --demo (no internet)."""
    base = {"NIFTY": 24500, "BANKNIFTY": 52000, "RELIANCE": 1300, "HDFCBANK": 1700, "ICICIBANK": 1250, "SBIN": 800,
            "INFY": 1850, "TCS": 4100, "EURUSD": 1.08, "GBPUSD": 1.27, "USDJPY": 150.0, "XAUUSD": 2650}[m["id"]]
    vol = 0.006 if m["group"] == "fx" else (0.011 if m["group"] != "india_stock" else 0.016)
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=1300)
    drift = np.repeat(rng.normal(0, vol / 10, 1300 // 60 + 1), 60)[:1300]
    rets = rng.normal(0, vol, 1300) + drift
    close = base * np.exp(np.cumsum(rets) - np.cumsum(rets)[-1])
    openp = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, vol / 4, 1300))
    hi = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, vol / 2, 1300)))
    lo = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, vol / 2, 1300)))
    v = 0 if m["group"] == "fx" else rng.integers(1_000_000, 5_000_000, 1300)
    d = pd.DataFrame({"Open": openp, "High": hi, "Low": lo, "Close": close, "Volume": v}, index=days)
    t = pd.date_range(end=now_utc(), periods=40, freq="15min")
    p = close[-1] * (1 + np.cumsum(rng.normal(0, vol / 10, 40)))
    intra = pd.DataFrame({"Open": p, "High": p * 1.0005, "Low": p * 0.9995, "Close": p, "Volume": 0}, index=t)
    return d, intra


def data_bot(demo: bool, bots: BotLog, errors: list) -> dict:
    bots.start("data", "Data bot", "Downloads daily + 15-minute prices (Yahoo Finance, free)")
    out = {}
    sources = set()
    for i, m in enumerate(MARKETS):
        try:
            if demo:
                daily, intra = demo_data(m, seed=100 + i)
                sources.add("demo")
            else:
                daily, src = fetch(m["ticker"], "5y", "1d")
                sources.add(src)
                try:
                    intra, _ = fetch(m["ticker"], "5d", "15m", tries=2)
                except Exception as e:
                    intra = pd.DataFrame()
                    errors.append(f"{m['id']}: no 15-minute data ({e}); using daily close as price")
                time.sleep(0.6)  # be gentle with Yahoo (rate limits)
            daily = to_daily_index(daily)
            if len(daily) < 250:
                raise RuntimeError(f"only {len(daily)} daily bars (need 250+)")
            out[m["id"]] = dict(meta=m, daily=daily, intra=intra)
            log(f"data: {m['id']:<10} {len(daily)} daily bars, last {daily.index[-1].date()}")
        except Exception as e:
            errors.append(f"{m['id']} ({m['ticker']}): {e}")
            log(f"data: {m['id']} FAILED: {e}")
    ok = len(out)
    note = f"{ok}/{len(MARKETS)} markets loaded via {', '.join(sorted(sources)) or 'none'}"
    bots.done("data", note, "ok" if ok == len(MARKETS) else ("warn" if ok else "error"))
    return out


# --------------------------------------------------------------------------
# 2. Chart bot (indicators)
# --------------------------------------------------------------------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(dn != 0, 100.0).fillna(50.0)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["Close"]
    d["ema20"], d["ema50"], d["ema200"] = ema(c, 20), ema(c, 50), ema(c, 200)
    d["rsi14"], d["rsi2"] = rsi(c, 14), rsi(c, 2)
    pc = c.shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - pc).abs(), (d["Low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    d["bb_up"], d["bb_dn"] = mid + 2 * sd, mid - 2 * sd
    d["bbw"] = (d["bb_up"] - d["bb_dn"]) / mid
    d["bbw_rank"] = d["bbw"].rolling(120).rank(pct=True)
    d["dc_hi"] = d["High"].rolling(20).max().shift(1)
    d["dc_lo"] = d["Low"].rolling(20).min().shift(1)
    d["macd"] = ema(c, 12) - ema(c, 26)
    d["macd_sig"] = ema(d["macd"], 9)
    d["macd_hist"] = d["macd"] - d["macd_sig"]
    v = d["Volume"].replace(0, np.nan)
    d["vol_ratio"] = v / v.rolling(20).mean()
    d["inside"] = (d["High"] < d["High"].shift(1)) & (d["Low"] > d["Low"].shift(1))
    return d


def completed_bars(m: dict, daily: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Strategies only use finished daily candles. Returns (bars, today_is_forming)."""
    if m["group"].startswith("india"):
        now = datetime.now(IST)
        today = pd.Timestamp(now.date())
        closed = now.hour * 60 + now.minute >= 15 * 60 + 40  # NSE closes 15:30 IST
    else:
        now = now_utc()
        today = pd.Timestamp(now.date())
        closed = False
    forming = daily.index[-1] >= today and not closed
    return (daily.iloc[:-1] if forming else daily), forming


# --------------------------------------------------------------------------
# 3. Candle bot (candlestick patterns)
# --------------------------------------------------------------------------
def candle_bot_patterns(d: pd.DataFrame) -> list[dict]:
    if len(d) < 3:
        return []
    r, p = d.iloc[-1], d.iloc[-2]
    o, h, l, c = r["Open"], r["High"], r["Low"], r["Close"]
    body, rng_ = abs(c - o), max(h - l, 1e-12)
    upper, lower = h - max(o, c), min(o, c) - l
    pats = []
    if c > o and p["Close"] < p["Open"] and c >= p["Open"] and o <= p["Close"]:
        pats.append(dict(name="Bullish engulfing", bias=1))
    if c < o and p["Close"] > p["Open"] and c <= p["Open"] and o >= p["Close"]:
        pats.append(dict(name="Bearish engulfing", bias=-1))
    if lower >= 2 * body and upper <= body and body / rng_ < 0.4:
        pats.append(dict(name="Hammer", bias=1))
    if upper >= 2 * body and lower <= body and body / rng_ < 0.4:
        pats.append(dict(name="Shooting star", bias=-1))
    if body / rng_ < 0.1:
        pats.append(dict(name="Doji (indecision)", bias=0))
    if h < p["High"] and l > p["Low"]:
        pats.append(dict(name="Inside bar", bias=0))
    if body / rng_ > 0.7 and body > 1.2 * r["atr"] * 0.6:
        pats.append(dict(name="Strong bull candle" if c > o else "Strong bear candle", bias=1 if c > o else -1))
    return pats


# --------------------------------------------------------------------------
# 4. Flow bot (volume)  /  5. Network bot (breadth, relative strength)
# --------------------------------------------------------------------------
def flow_bot(d: pd.DataFrame) -> dict:
    vr = d["vol_ratio"].iloc[-1]
    if pd.isna(vr):
        return dict(available=False, vol_ratio=None, obv_slope=0, note="No volume data (normal for forex)")
    obv = (np.sign(d["Close"].diff()).fillna(0) * d["Volume"]).cumsum()
    slope = float(np.sign(obv.iloc[-1] - obv.iloc[-11])) if len(obv) > 11 else 0.0
    note = f"Volume {vr:.1f}x 20-day average, OBV {'rising' if slope > 0 else 'falling' if slope < 0 else 'flat'}"
    return dict(available=True, vol_ratio=rnd(vr, 2), obv_slope=slope, note=note)


def network_bot(frames: dict) -> dict:
    groups = {}
    for mid, d in frames.items():
        g = MARKET_BY_ID[mid]["group"]
        groups.setdefault(g, []).append(1.0 if d["Close"].iloc[-1] > d["ema50"].iloc[-1] else 0.0)
    breadth = {g: rnd(sum(v) / len(v), 2) for g, v in groups.items()}
    ret20 = {mid: float(d["Close"].iloc[-1] / d["Close"].iloc[-21] - 1) for mid, d in frames.items() if len(d) > 21}
    usd = [x for x in [-ret20.get("EURUSD", np.nan), -ret20.get("GBPUSD", np.nan), ret20.get("USDJPY", np.nan)]
           if not pd.isna(x)]
    usd_strength = float(np.mean(usd)) if usd else 0.0
    rel = {}
    for mid, r in ret20.items():
        if MARKET_BY_ID[mid]["group"] == "india_stock" and "NIFTY" in ret20:
            rel[mid] = r - ret20["NIFTY"]
    return dict(breadth=breadth, ret20=ret20, usd_strength=usd_strength, rel_strength=rel)


def network_score(mid: str, direction: int, net: dict) -> tuple[float, str]:
    m = MARKET_BY_ID[mid]
    b = net["breadth"].get(m["group"], 0.5)
    b = b if direction == 1 else 1 - b
    score = 10 * b
    note = f"{int(b * 100)}% of {m['group'].replace('_', ' ')} markets agree"
    if mid in net["rel_strength"]:
        rs = net["rel_strength"][mid] * direction
        score = 0.6 * score + (4 if rs > 0 else 0)
        note += f"; {'stronger' if net['rel_strength'][mid] > 0 else 'weaker'} than NIFTY over 20 days"
    elif m["group"] in ("fx", "metal"):
        usd = net["usd_strength"]
        usd_dir = 1 if mid == "USDJPY" else -1  # USD up -> EURUSD/GBPUSD/Gold down
        agrees = (usd * usd_dir * direction) > 0
        score = 0.6 * score + (4 if agrees else 0)
        note += f"; US dollar {'strong' if usd > 0 else 'weak'} over 20 days"
    return min(10.0, score), note


# --------------------------------------------------------------------------
# 6. News bot (free RSS feeds, simple keyword sentiment)
# --------------------------------------------------------------------------
def news_bot(demo: bool, bots: BotLog) -> list[dict]:
    bots.start("news", "News bot", "Reads free RSS headlines and tags them by market")
    items, fails = [], 0
    if demo:
        items = [dict(title="Demo: Nifty edges higher as banks gain", source="demo", link="", published=now_utc().isoformat()),
                 dict(title="Demo: Gold slips as dollar firms ahead of Fed minutes", source="demo", link="", published=now_utc().isoformat())]
    else:
        for name, url in NEWS_FEEDS:
            try:
                r = requests.get(url, headers=UA, timeout=12)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                for it in root.iter("item"):
                    title = (it.findtext("title") or "").strip()
                    if not title:
                        continue
                    pub = it.findtext("pubDate") or ""
                    try:
                        pdt = pd.to_datetime(pub, utc=True).isoformat()
                    except Exception:
                        pdt = None
                    items.append(dict(title=html.unescape(title), source=name, link=(it.findtext("link") or "").strip(), published=pdt))
            except Exception as e:
                fails += 1
                log(f"news: {name} failed: {type(e).__name__}: {e}")
    cutoff = now_utc() - timedelta(hours=48)
    seen, out = set(), []
    for it in items:
        key = re.sub(r"\W+", "", it["title"].lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        if it["published"]:
            try:
                if pd.Timestamp(it["published"]) < cutoff:
                    continue
            except Exception:
                pass
        t = " " + it["title"].lower() + " "
        s = sum(w in t for w in POS) - sum(w in t for w in NEG)
        it["sentiment"] = int(np.sign(s))
        it["high_impact"] = any(w in t for w in HIGH_IMPACT)
        it["markets"] = [mid for mid, kws in NEWS_KEYWORDS.items() if any(k in t for k in kws)]
        if any(w in t for w in USD_WORDS):
            it["markets"] = sorted(set(it["markets"]) | {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD"})
            it["usd_news"] = True
        out.append(it)
    out.sort(key=lambda x: x.get("published") or "", reverse=True)
    out = out[:60]
    status = "ok" if fails == 0 else ("warn" if fails < len(NEWS_FEEDS) or demo else "error")
    bots.done("news", f"{len(out)} headlines from last 48h ({fails} feed(s) failed)", status)
    return out


def news_score(mid: str, direction: int, news: list[dict]) -> tuple[float, str, bool]:
    rel = [n for n in news if mid in n.get("markets", [])]
    if not rel:
        return 6.0, "No recent headlines", False
    sents = []
    for n in rel:
        s = n["sentiment"]
        if n.get("usd_news") and mid not in ("USDJPY",) and not any(k in n["title"].lower() for k in NEWS_KEYWORDS[mid]):
            s = -s  # good USD news is bad for EUR/USD, GBP/USD, gold
        sents.append(s)
    avg = float(np.mean(sents)) * direction
    impact = any(n["high_impact"] for n in rel)
    score = 6 + 4 * avg
    return max(0.0, min(10.0, score)), f"{len(rel)} headline(s), tone {'supports' if avg > 0 else 'against' if avg < 0 else 'neutral to'} this direction", impact


# --------------------------------------------------------------------------
# 7. Strategy bot (6 strategies)
# --------------------------------------------------------------------------
def s_trend_pullback(d):
    up = (d.Close > d.ema50) & (d.ema50 > d.ema200) & (d.Low <= d.ema20) & (d.Close > d.ema20) & (d.rsi14 > 40)
    dn = (d.Close < d.ema50) & (d.ema50 < d.ema200) & (d.High >= d.ema20) & (d.Close < d.ema20) & (d.rsi14 < 60)
    return up.astype(int) - dn.astype(int)


def s_donchian(d):
    up = (d.Close > d.dc_hi) & (d.Close > d.ema200)
    dn = (d.Close < d.dc_lo) & (d.Close < d.ema200)
    return up.astype(int) - dn.astype(int)


def s_rsi_reversion(d):
    up = (d.rsi2 < 5) & (d.Close > d.ema200)
    dn = (d.rsi2 > 95) & (d.Close < d.ema200)
    return up.astype(int) - dn.astype(int)


def s_squeeze(d):
    sq = d.bbw_rank.shift(1) < 0.2
    up = sq & (d.Close > d.bb_up)
    dn = sq & (d.Close < d.bb_dn)
    return up.astype(int) - dn.astype(int)


def s_macd(d):
    cu = (d.macd > d.macd_sig) & (d.macd.shift(1) <= d.macd_sig.shift(1))
    cd = (d.macd < d.macd_sig) & (d.macd.shift(1) >= d.macd_sig.shift(1))
    return (cu & (d.Close > d.ema200)).astype(int) - (cd & (d.Close < d.ema200)).astype(int)


def s_inside_bar(d):
    ib = d.inside.shift(1, fill_value=False).astype(bool)
    up = ib & (d.Close > d.High.shift(1)) & (d.ema50 > d.ema200)
    dn = ib & (d.Close < d.Low.shift(1)) & (d.ema50 < d.ema200)
    return up.astype(int) - dn.astype(int)


def near_setups(key: str, d: pd.DataFrame) -> list[dict]:
    """'Upcoming' = conditions are close but the trigger hasn't fired yet."""
    r = d.iloc[-1]
    a = r["atr"]
    up_tr = r.Close > r.ema50 > r.ema200
    dn_tr = r.Close < r.ema50 < r.ema200
    out = []
    if key == "trend_pullback":
        if up_tr and 0 < r.Low - r.ema20 < 0.6 * a:
            out.append(dict(dir=1, trigger=r.ema20, when="price dips to EMA20 and closes back above it"))
        if dn_tr and 0 < r.ema20 - r.High < 0.6 * a:
            out.append(dict(dir=-1, trigger=r.ema20, when="price rallies to EMA20 and closes back below it"))
    elif key == "donchian":
        if r.Close > r.ema200 and 0 < r.dc_hi - r.Close < 0.5 * a:
            out.append(dict(dir=1, trigger=max(r.dc_hi, r.High), when="a daily close above the 20-day high"))
        if r.Close < r.ema200 and 0 < r.Close - r.dc_lo < 0.5 * a:
            out.append(dict(dir=-1, trigger=min(r.dc_lo, r.Low), when="a daily close below the 20-day low"))
    elif key == "rsi_reversion":
        if r.Close > r.ema200 and 5 <= r.rsi2 < 15:
            out.append(dict(dir=1, trigger=r.Close, when="RSI(2) drops below 5 (one more down day)"))
        if r.Close < r.ema200 and 85 < r.rsi2 <= 95:
            out.append(dict(dir=-1, trigger=r.Close, when="RSI(2) rises above 95 (one more up day)"))
    elif key == "squeeze":
        if r.bbw_rank < 0.2:
            if r.Close >= r.ema50:
                out.append(dict(dir=1, trigger=r.bb_up, when="a close above the upper Bollinger band"))
            else:
                out.append(dict(dir=-1, trigger=r.bb_dn, when="a close below the lower Bollinger band"))
    elif key == "macd":
        h, ph = r.macd_hist, d["macd_hist"].iloc[-2]
        if r.Close > r.ema200 and h < 0 and h > ph and abs(h) < 0.08 * a:
            out.append(dict(dir=1, trigger=r.Close, when="MACD crosses above its signal line"))
        if r.Close < r.ema200 and h > 0 and h < ph and abs(h) < 0.08 * a:
            out.append(dict(dir=-1, trigger=r.Close, when="MACD crosses below its signal line"))
    elif key == "inside_bar":
        if r.inside and r.ema50 > r.ema200:
            out.append(dict(dir=1, trigger=r.High, when="a daily close above this inside bar's high"))
        if r.inside and r.ema50 < r.ema200:
            out.append(dict(dir=-1, trigger=r.Low, when="a daily close below this inside bar's low"))
    return out


STRATEGIES = {
    "trend_pullback": dict(name="Trend pullback", fn=s_trend_pullback, stop_atr=1.5, rr=2.0, hold=15,
                           desc="In an uptrend (price > EMA50 > EMA200), buy when price dips to EMA20 and closes back above. Mirror for shorts."),
    "donchian": dict(name="20-day breakout", fn=s_donchian, stop_atr=2.0, rr=2.0, hold=20,
                     desc="Buy a close above the 20-day high when above EMA200; sell a close below the 20-day low when below EMA200."),
    "rsi_reversion": dict(name="RSI(2) dip buy", fn=s_rsi_reversion, stop_atr=2.0, rr=1.5, hold=7,
                          desc="In a long-term uptrend, buy a sharp short-term dip (RSI(2) < 5). Mirror for shorts."),
    "squeeze": dict(name="Bollinger squeeze", fn=s_squeeze, stop_atr=1.5, rr=2.0, hold=15,
                    desc="After a very quiet period (narrow bands), trade the breakout close outside the bands."),
    "macd": dict(name="MACD trend cross", fn=s_macd, stop_atr=2.0, rr=2.0, hold=20,
                 desc="MACD crosses its signal line in the direction of the EMA200 trend."),
    "inside_bar": dict(name="Inside-bar breakout", fn=s_inside_bar, stop_atr=1.5, rr=2.0, hold=10,
                       desc="After an inside day, trade the close beyond its high/low in the trend direction."),
}
MARKET_BY_ID = {m["id"]: m for m in MARKETS}


# --------------------------------------------------------------------------
# 8. Backtest bot (after costs, honest verdicts)
# --------------------------------------------------------------------------
WARMUP = 200


def backtest(d: pd.DataFrame, sig: np.ndarray, p: dict, cost_side: float) -> list[dict]:
    o, h, l, c, a = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "atr"))
    n, trades, i = len(d), [], WARMUP
    while i < n - 1:
        s = int(sig[i])
        if s == 0 or not np.isfinite(a[i]) or a[i] <= 0:
            i += 1
            continue
        e = o[i + 1]
        risk = p["stop_atr"] * a[i]
        stop, tgt = e - s * risk, e + s * p["rr"] * risk
        r, j = None, i + 1
        last = min(i + p["hold"], n - 1)
        while j <= last:
            if j > i + 1 and (s * (o[j] - stop) <= 0):         # gapped through stop
                r = s * (o[j] - e) / risk
                break
            hit_stop = (l[j] <= stop) if s == 1 else (h[j] >= stop)
            hit_tgt = (h[j] >= tgt) if s == 1 else (l[j] <= tgt)
            if hit_stop:                                       # stop first if both (conservative)
                r = -1.0
                break
            if hit_tgt:
                r = p["rr"]
                break
            j += 1
        if r is None:
            if i + p["hold"] > n - 1:                           # still open at the end of data
                break
            j = last
            r = s * (c[j] - e) / risk                           # time exit
        r -= 2 * cost_side * e / risk                           # costs both sides, in R
        trades.append(dict(date=str(d.index[i + 1].date()), dir=s, r=float(r)))
        i = j + 1
    return trades


def stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return dict(trades=0, win_rate=None, expectancy=None, profit_factor=None, total_r=0, max_dd_r=0, oos_expectancy=None)
    arr = np.array(rs)
    wins, losses = arr[arr > 0].sum(), -arr[arr < 0].sum()
    eq = np.cumsum(arr)
    dd = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max())
    k = max(1, int(n * 0.3))
    return dict(trades=n, win_rate=rnd((arr > 0).mean() * 100, 1), expectancy=rnd(arr.mean(), 3),
                profit_factor=rnd(wins / losses, 2) if losses > 0 else None, total_r=rnd(arr.sum(), 2),
                max_dd_r=rnd(dd, 2), oos_expectancy=rnd(arr[-k:].mean(), 3))


def verdict(st: dict, pooled: bool) -> str:
    n, e, pf, oos = st["trades"], st["expectancy"], st["profit_factor"], st["oos_expectancy"]
    if n == 0 or e is None:
        return "NO DATA"
    min_n = 30 if pooled else 8
    if n >= min_n and e >= 0.10 and (pf or 0) >= 1.2 and (oos or -1) > 0:
        return "TRADEABLE"
    if e > 0 and n >= (15 if pooled else 4):
        return "WATCH"
    return "REJECTED"


def backtest_bot(frames: dict, bots: BotLog) -> tuple[dict, dict]:
    bots.start("backtest", "Backtest bot", "Tests all 6 strategies on ~5 years of daily data, after costs")
    results, sigs = {}, {}
    for key, p in STRATEGIES.items():
        per, all_trades = {}, []
        for mid, d in frames.items():
            s = p["fn"](d).fillna(0).to_numpy()
            sigs[(key, mid)] = s
            tr = backtest(d, s, p, MARKET_BY_ID[mid]["cost"])
            st = stats([t["r"] for t in tr])
            per[mid] = dict(stats=st, verdict=verdict(st, pooled=False))
            all_trades += tr
        all_trades.sort(key=lambda t: t["date"])
        st = stats([t["r"] for t in all_trades])
        v = verdict(st, pooled=True)
        for mid in per:   # a market only counts as tradeable if the strategy works overall AND there
            if v != "TRADEABLE" and per[mid]["verdict"] == "TRADEABLE":
                per[mid]["verdict"] = "WATCH"
        results[key] = dict(key=key, name=p["name"], desc=p["desc"], stop_atr=p["stop_atr"], rr=p["rr"],
                            hold_days=p["hold"], verdict=v, stats=st, per_market=per)
    n_ok = sum(r["verdict"] == "TRADEABLE" for r in results.values())
    bots.done("backtest", f"{n_ok}/6 strategies TRADEABLE after costs")
    return results, sigs


# --------------------------------------------------------------------------
# 9. Risk bot (1% sizing, daily loss limit) + Mistake guard
# --------------------------------------------------------------------------
def position_size(m: dict, entry: float, stop: float) -> dict:
    acct = ACCOUNTS[m["ccy"]]
    risk_amt = acct * RISK_PCT / 100
    per_unit = abs(entry - stop)
    if m["id"] == "USDJPY":
        per_unit = per_unit / entry   # P&L is in JPY; convert to USD
    qty = risk_amt / per_unit if per_unit > 0 else 0
    out = dict(account=acct, currency=m["ccy"], risk_pct=RISK_PCT, risk_amount=rnd(risk_amt, 2), unit=m["unit"])
    if m["group"] == "fx":
        out["qty"] = int(qty)
        out["lots"] = rnd(qty / 100000, 2)
        out["text"] = f"{int(qty):,} units (~{qty / 100000:.2f} standard lots)"
    elif m["group"] == "metal":
        out["qty"] = rnd(qty, 2)
        out["text"] = f"{qty:.2f} oz"
    else:
        out["qty"] = int(qty)
        out["text"] = f"{int(qty):,} {m['unit']}"
    out["too_small"] = qty < (1 if m["group"] != "metal" else 0.01)
    return out


def mistake_guard(c: dict, ctx: dict) -> tuple[list[str], list[str]]:
    """Returns (blocks, warnings). Any block = no signal."""
    blocks, warns = [], []
    a = c["atr"]
    if not c.get("stop"):
        blocks.append("No stop-loss")
    else:
        dist = abs(c["entry"] - c["stop"])
        if dist < 0.5 * a:
            blocks.append("Stop too tight (< 0.5 ATR) - normal noise would hit it")
        if dist > 4 * a:
            blocks.append("Stop too wide (> 4 ATR)")
        if abs(c["target"] - c["entry"]) / dist < 1.49:
            blocks.append("Reward:risk below 1.5")
    if c["market_verdict"] != "TRADEABLE":
        blocks.append(f"Strategy is {c['market_verdict']} for this market in the backtest (not proven after costs)")
    if c["chase_atr"] > CHASE_LIMIT_ATR:
        blocks.append(f"Price already moved {c['chase_atr']:.1f} ATR past the trigger - don't chase")
    if c["stale_days"] > STALE_DAYS:
        blocks.append(f"Data is stale ({c['stale_days']} days old)")
    if ctx["halted"]:
        blocks.append(f"Daily loss limit hit ({DAILY_LOSS_LIMIT_R:.0f}R) - no new trades today")
    if c["market"] in ctx["open_markets"]:
        blocks.append("Already have an open paper trade on this market")
    if ctx["open_count"] >= MAX_OPEN_TRADES:
        blocks.append(f"Max {MAX_OPEN_TRADES} open trades reached")
    if ctx["group_dir"].get((c["group"], c["dir"]), 0) >= MAX_SAME_GROUP_SAME_DIR:
        blocks.append("Too many same-direction trades in correlated markets")
    if c["conflict"]:
        blocks.append("Another strategy gives the opposite direction on this market")
    if c["score"] < MIN_SCORE:
        blocks.append(f"Score {c['score']} is below {MIN_SCORE}")
    if c["size"]["too_small"]:
        blocks.append("1% risk is smaller than the minimum trade size for this account")
    if c["news_impact"]:
        warns.append("High-impact news around this market - expect bigger moves, consider waiting")
    if c["m_group"] == "india_index":
        warns.append("Index: trade via futures/ETF. Check lot size - if 1 lot risks more than 1%, skip")
    return blocks, warns


# --------------------------------------------------------------------------
# 10. Forward-test bot (paper trades, track record)
# --------------------------------------------------------------------------
def forward_test_bot(state: dict, data: dict, bots: BotLog) -> list[dict]:
    bots.start("forward", "Forward-test bot", "Tracks every signal as a paper trade until stop, target or time exit")
    closed_now = []
    still = []
    for t in state.get("open_trades", []):
        md = data.get(t["market"])
        if not md:
            still.append(t)
            continue
        opened = pd.Timestamp(t["opened_at"])
        s = 1 if t["dir"] == "LONG" else -1
        bars = []
        dly = md["daily"]
        open_day = pd.Timestamp(opened.tz_convert(IST if MARKET_BY_ID[t["market"]]["group"].startswith("india") else "UTC").date())
        for ts, r in dly[dly.index > open_day].iterrows():
            bars.append((ts, r["Open"], r["High"], r["Low"], r["Close"]))
        intra = md["intra"]
        if len(intra):
            ii = intra.copy()
            if ii.index.tz is None:
                ii.index = ii.index.tz_localize("UTC")
            ii = ii[ii.index > opened]
            last_daily = bars[-1][0] if bars else None
            for ts, r in ii.iterrows():
                if last_daily is not None and pd.Timestamp(ts.date()) <= last_daily:
                    continue
                bars.append((ts, r["Open"], r["High"], r["Low"], r["Close"]))
        exit_px, reason = None, None
        for ts, o, h, l, c in bars:
            if s * (o - t["stop"]) <= 0:
                exit_px, reason = o, "Stop (gap)"
                break
            if (l <= t["stop"]) if s == 1 else (h >= t["stop"]):
                exit_px, reason = t["stop"], "Stop-loss"
                break
            if (h >= t["target"]) if s == 1 else (l <= t["target"]):
                exit_px, reason = t["target"], "Target"
                break
        age = (now_utc() - opened.to_pydatetime()).days
        if exit_px is None and age > t.get("hold_days", 15) * 1.45:
            exit_px, reason = md["price"], "Time exit"
        if exit_px is None:
            t["last_price"] = md["price"]
            t["open_r"] = rnd(s * (md["price"] - t["entry"]) / t["risk"], 2)
            still.append(t)
            continue
        cost = 2 * MARKET_BY_ID[t["market"]]["cost"] * t["entry"] / t["risk"]
        t.update(exit=float(exit_px), exit_reason=reason, closed_at=now_utc().isoformat(),
                 r=rnd(s * (exit_px - t["entry"]) / t["risk"] - cost, 2))
        closed_now.append(t)
    state["open_trades"] = still
    state.setdefault("closed_trades", []).extend(closed_now)
    state["closed_trades"] = state["closed_trades"][-500:]
    bots.done("forward", f"{len(still)} open, {len(closed_now)} closed this run, {len(state['closed_trades'])} total closed")
    return closed_now


def track_record(state: dict) -> dict:
    closed = state.get("closed_trades", [])
    by = {}
    for t in closed:
        by.setdefault(t["strategy"], []).append(t["r"] or 0)
    return dict(stats=stats([t["r"] or 0 for t in closed]),
                by_strategy={k: stats(v) for k, v in by.items()},
                closed=list(reversed(closed[-50:])),
                started=state.get("started"))


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def find_chat_id(state: dict) -> str:
    """Chat id from the secret; otherwise the chat id saved earlier; otherwise ask Telegram who
    last messaged the bot (the user just sends /start to their bot - no @userinfobot needed)."""
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if chat:
        return chat
    if state.get("telegram_chat_id"):
        return str(state["telegram_chat_id"])
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        return ""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
        if not r.get("ok"):
            log(f"telegram: getUpdates refused: {r.get('error_code')} {r.get('description')}")
            if r.get("error_code") == 409:
                log("telegram: this bot has a webhook (another app, e.g. Alisa, receives its messages). "
                    "Use a separate bot for EdgeDesk, or set the TELEGRAM_CHAT_ID secret.")
            return ""
        log(f"telegram: getUpdates returned {len(r.get('result', []))} update(s)")
        for upd in reversed(r.get("result", [])):
            msg = upd.get("message") or upd.get("my_chat_member") or {}
            c = msg.get("chat") or {}
            if c.get("type") == "private" and c.get("id"):
                state["telegram_chat_id"] = c["id"]
                log("telegram: found your chat automatically (you messaged the bot)")
                return str(c["id"])
        log("telegram: no chat found - open your bot in Telegram and send /start, then run again")
    except Exception as e:
        log(f"telegram: getUpdates failed: {type(e).__name__}")
    return ""


def telegram(text: str) -> tuple[bool, str]:
    token, chat = os.getenv("TELEGRAM_TOKEN", "").strip(), os.environ.get("EDGEDESK_CHAT", "").strip()
    if not token or not chat:
        return False, ("TELEGRAM_TOKEN secret not set" if not token else "no chat yet - open your bot in Telegram and send /start")
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json=dict(chat_id=chat, text=text[:4000], parse_mode="HTML", disable_web_page_preview=True),
                          timeout=15)
        if r.status_code == 200 and r.json().get("ok"):
            return True, "sent"
        desc = r.json().get("description", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        return False, f"Telegram error {r.status_code}: {desc}"
    except Exception as e:
        return False, f"Telegram request failed: {type(e).__name__}"


def fmt(x, dp):
    return f"{x:,.{dp}f}"


def signal_message(s: dict) -> str:
    m = MARKET_BY_ID[s["market"]]
    dp = m["dp"]
    arrow = "🟢 LONG" if s["direction"] == "LONG" else "🔴 SHORT"
    w = "".join(f"\n⚠️ {html.escape(x)}" for x in s["warnings"])
    return (f"<b>EdgeDesk signal - {html.escape(m['name'])}</b>\n{arrow} · {html.escape(s['strategy_name'])} · score {s['score']}/100\n"
            f"Entry ~ {fmt(s['entry'], dp)}\nStop  {fmt(s['stop'], dp)}\nTarget {fmt(s['target'], dp)}  (R:R {s['rr']})\n"
            f"Size (1% risk on {m['ccy']} {ACCOUNTS[m['ccy']]:,.0f}): {html.escape(s['size']['text'])}{w}\n\n"
            f"<i>Paper-trade idea, not investment advice. Not guaranteed. Always use the stop.</i>")


# --------------------------------------------------------------------------
# Chief
# --------------------------------------------------------------------------
def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def chief(args) -> int:
    t0 = time.time()
    DATA.mkdir(exist_ok=True)
    state_path = DATA / ("state_demo.json" if args.demo else "state.json")
    state = load_json(state_path, {})
    state.setdefault("started", now_utc().isoformat())
    state.setdefault("open_trades", [])
    state.setdefault("closed_trades", [])
    state.setdefault("sent_ids", [])
    errors: list[str] = []
    bots = BotLog()
    bots.start("chief", "Chief", "Runs all bots, combines scores, applies the rules, writes the website data")

    raw = data_bot(args.demo, bots, errors)
    if not raw:
        log("No market data loaded at all - keeping the previous signals.json. Errors:\n  " + "\n  ".join(errors))
        return 2

    # Chart bot
    bots.start("chart", "Chart bot", "Calculates EMA, RSI, ATR, Bollinger, MACD, 20-day high/low")
    frames, markets, data = {}, [], {}
    for mid, md in raw.items():
        m = md["meta"]
        full = add_indicators(md["daily"])
        done, forming = completed_bars(m, full)
        frames[mid] = done
        intra = md["intra"]
        price = float(intra["Close"].iloc[-1]) if len(intra) else float(full["Close"].iloc[-1])
        price_time = (intra.index[-1] if len(intra) else full.index[-1])
        prev_close = float(done["Close"].iloc[-1]) if forming else float(full["Close"].iloc[-2])
        today = pd.Timestamp(datetime.now(IST if m["group"].startswith("india") else timezone.utc).date())
        stale = int((today - full.index[-1]).days)
        data[mid] = dict(daily=full, intra=intra, price=price)
        r = done.iloc[-1]
        trend = "Up" if r.Close > r.ema50 > r.ema200 else "Down" if r.Close < r.ema50 < r.ema200 else "Mixed"
        candles = [dict(time=str(ix.date()), open=rnd(x.Open, m["dp"]), high=rnd(x.High, m["dp"]),
                        low=rnd(x.Low, m["dp"]), close=rnd(x.Close, m["dp"])) for ix, x in full.tail(130).iterrows()]
        if len(intra) and forming is False and str(pd.Timestamp(price_time).date()) > candles[-1]["time"]:
            pass  # new session started but no daily bar yet; chart shows last daily bar
        markets.append(dict(
            id=mid, name=m["name"], ticker=m["ticker"], tv=m["tv"], group=m["group"], ccy=m["ccy"], dp=m["dp"],
            price=rnd(price, m["dp"]), price_time=clean(pd.Timestamp(price_time)), prev_close=rnd(prev_close, m["dp"]),
            change_pct=rnd((price / prev_close - 1) * 100, 2), last_bar=str(full.index[-1].date()),
            last_completed_bar=str(done.index[-1].date()), today_forming=forming, stale_days=stale,
            trend=trend, rsi14=rnd(r.rsi14, 1), atr=rnd(r.atr, m["dp"]), ema20=rnd(r.ema20, m["dp"]),
            ema50=rnd(r.ema50, m["dp"]), ema200=rnd(r.ema200, m["dp"]), candles=candles))
    bots.done("chart", f"Indicators for {len(frames)} markets (finished daily candles only)")

    bots.start("candle", "Candle bot", "Finds candlestick patterns on the last finished candle")
    patterns = {mid: candle_bot_patterns(d) for mid, d in frames.items()}
    for mk in markets:
        mk["patterns"] = patterns[mk["id"]]
    bots.done("candle", f"{sum(len(v) for v in patterns.values())} patterns found")

    bots.start("flow", "Flow bot", "Checks volume and on-balance volume (money flow)")
    flows = {mid: flow_bot(d) for mid, d in frames.items()}
    for mk in markets:
        mk["flow"] = flows[mk["id"]]
    bots.done("flow", f"Volume available for {sum(f['available'] for f in flows.values())} markets")

    bots.start("network", "Network bot", "Market breadth, relative strength vs NIFTY, US-dollar strength")
    net = network_bot(frames)
    bots.done("network", "Breadth: " + ", ".join(f"{g.replace('_', ' ')} {int((v or 0) * 100)}%" for g, v in net["breadth"].items()))

    news = news_bot(args.demo, bots)
    bt, sigs = backtest_bot(frames, bots)

    # Forward test first (closes trades, updates daily P&L)
    closed_now = forward_test_bot(state, data, bots)
    today_ist = datetime.now(IST).date().isoformat()
    day_r = sum((t["r"] or 0) for t in state["closed_trades"] if pd.Timestamp(t["closed_at"]).tz_convert(IST).date().isoformat() == today_ist)
    halted = day_r <= -DAILY_LOSS_LIMIT_R

    # Strategy bot + scoring
    bots.start("strategy", "Strategy bot", "Checks the 6 strategies on the latest finished candle")
    cands = []
    for mid, d in frames.items():
        m = MARKET_BY_ID[mid]
        r = d.iloc[-1]
        dirs = {key: int(sigs[(key, mid)][-1]) for key in STRATEGIES}
        for key, s in dirs.items():
            if s == 0:
                continue
            p = STRATEGIES[key]
            price = data[mid]["price"]
            a = float(r.atr)
            risk = p["stop_atr"] * a
            entry, stop = price, price - s * risk
            target = price + s * p["rr"] * risk
            mv = bt[key]["per_market"][mid]["verdict"]
            # score parts
            st = bt[key]["per_market"][mid]["stats"]
            if mv == "TRADEABLE":
                sc_bt = 20 + min(10, 25 * max(0, st["expectancy"] or 0))
            elif mv == "WATCH":
                sc_bt = 10
            else:
                sc_bt = 0
            sc_trend = (10 if s * (r.ema50 - r.ema200) > 0 else 0) + (10 if s * (r.Close - r.ema200) > 0 else 0)
            pb = [pt["bias"] for pt in patterns[mid]]
            sc_candle = 15 if s in pb else (0 if -s in pb else 7)
            fl = flows[mid]
            if not fl["available"]:
                sc_flow = 5
            else:
                sc_flow = 2 + (4 if (fl["vol_ratio"] or 0) >= 1.2 else 2) + (4 if fl["obv_slope"] * s > 0 else 0)
            sc_net, net_note = network_score(mid, s, net)
            sc_news, news_note, impact = news_score(mid, s, news)
            sc_rr = 5 if p["rr"] >= 2 else 3
            parts = dict(backtest=round(sc_bt), trend=sc_trend, candle=sc_candle, flow=sc_flow,
                         network=round(sc_net), news=round(sc_news), reward_risk=sc_rr)
            score = int(round(min(100, sum(parts.values()))))
            cands.append(dict(
                id=f"{mid}-{key}-{d.index[-1].date()}-{'L' if s == 1 else 'S'}",
                market=mid, name=m["name"], group=m["group"], m_group=m["group"], strategy=key, strategy_name=p["name"],
                dir=s, direction="LONG" if s == 1 else "SHORT", entry=rnd(entry, m["dp"]), stop=rnd(stop, m["dp"]),
                target=rnd(target, m["dp"]), rr=p["rr"], risk=float(risk), atr=a, hold_days=p["hold"],
                trigger_close=rnd(r.Close, m["dp"]), bar_date=str(d.index[-1].date()),
                chase_atr=round(max(0.0, s * (price - r.Close) / a), 2), stale_days=next(x["stale_days"] for x in markets if x["id"] == mid),
                market_verdict=mv, strategy_verdict=bt[key]["verdict"], score=score, score_parts=parts,
                notes=[fl["note"], net_note, news_note] + [f"Candle: {x['name']}" for x in patterns[mid]],
                news_impact=impact, conflict=(1 in dirs.values() and -1 in dirs.values()),
                size=position_size(m, entry, stop), time=now_utc().isoformat()))
    bots.done("strategy", f"{len(cands)} raw setups triggered on the last candle")

    # Risk bot + mistake guard
    bots.start("risk", "Risk & mistake guard", "1% sizing, stop on every trade, daily loss limit, blocks common mistakes")
    known = {t["id"] for t in state["open_trades"]} | {t["id"] for t in state["closed_trades"]}
    ctx = dict(halted=halted, open_markets={t["market"] for t in state["open_trades"]},
               open_count=len(state["open_trades"]), group_dir={})
    for t in state["open_trades"]:
        k = (MARKET_BY_ID[t["market"]]["group"], 1 if t["dir"] == "LONG" else -1)
        ctx["group_dir"][k] = ctx["group_dir"].get(k, 0) + 1
    accepted, blocked, new_signals = [], [], []
    for c in sorted(cands, key=lambda x: -x["score"]):
        if c["id"] in known:
            continue  # already handled in an earlier run (it's in open trades / record)
        blocks, warns = mistake_guard(c, ctx)
        c["blocks"], c["warnings"] = blocks, warns
        if blocks:
            blocked.append(c)
            continue
        accepted.append(c)
        new_signals.append(c)
        ctx["open_markets"].add(c["market"])
        ctx["open_count"] += 1
        k = (c["group"], c["dir"])
        ctx["group_dir"][k] = ctx["group_dir"].get(k, 0) + 1
        state["open_trades"].append(dict(
            id=c["id"], market=c["market"], name=c["name"], strategy=c["strategy"], strategy_name=c["strategy_name"],
            dir=c["direction"], entry=c["entry"], stop=c["stop"], target=c["target"], risk=c["risk"], rr=c["rr"],
            score=c["score"], hold_days=c["hold_days"], opened_at=c["time"], size=c["size"]["text"]))
    state["sent_ids"] = (state["sent_ids"] + [c["id"] for c in accepted])[-800:]
    bots.done("risk", f"{len(accepted)} new signal(s), {len(blocked)} blocked; today's closed P&L {day_r:+.2f}R"
              + (" - TRADING HALTED" if halted else ""), "warn" if halted else "ok")

    # Upcoming setups
    upcoming = []
    for mid, d in frames.items():
        m = MARKET_BY_ID[mid]
        for key in STRATEGIES:
            for u in near_setups(key, d):
                mv = bt[key]["per_market"][mid]["verdict"]
                upcoming.append(dict(market=mid, name=m["name"], strategy=key, strategy_name=STRATEGIES[key]["name"],
                                     direction="LONG" if u["dir"] == 1 else "SHORT", trigger=rnd(u["trigger"], m["dp"]),
                                     when=u["when"], verdict=mv, price=rnd(data[mid]["price"], m["dp"])))
    upcoming.sort(key=lambda u: (u["verdict"] != "TRADEABLE", u["verdict"] != "WATCH", u["market"]))

    # Signals shown on the site = currently open paper trades from signals (latest first)
    open_view = []
    for t in reversed(state["open_trades"]):
        mk = MARKET_BY_ID[t["market"]]
        s = 1 if t["dir"] == "LONG" else -1
        px = data.get(t["market"], {}).get("price", t["entry"])
        open_view.append(dict(t, direction=t["dir"], price=rnd(px, mk["dp"]), open_r=rnd(s * (px - t["entry"]) / t["risk"], 2), dp=mk["dp"],
                              is_new=t["id"] in {c["id"] for c in new_signals}))

    # Telegram
    bots.start("telegram", "Telegram bot", "Sends new signals, closed trades and warnings to your phone")
    tg_notes = []
    os.environ["EDGEDESK_CHAT"] = find_chat_id(state)
    tg_on = bool(os.getenv("TELEGRAM_TOKEN") and os.environ["EDGEDESK_CHAT"])
    hello_ok = True
    if args.hello:
        ok, msg = telegram(f"✅ <b>EdgeDesk is connected</b>\nMode: {'demo' if args.demo else 'live'} · "
                           f"{len(raw)}/{len(MARKETS)} markets loaded · {len(open_view)} open paper trade(s).\n"
                           f"You'll get alerts here for new signals and closed trades.\n\n<i>{DISCLAIMER}</i>")
        tg_notes.append(f"hello: {msg}")
        hello_ok = ok
        if ok:
            state["connected_at"] = now_utc().isoformat()
    if tg_on and not args.demo:
        for c in new_signals:
            ok, msg = telegram(signal_message(c))
            tg_notes.append(f"{c['id']}: {msg}")
        for t in closed_now:
            emoji = "✅" if (t["r"] or 0) > 0 else "❌"
            telegram(f"{emoji} <b>Paper trade closed - {html.escape(t['name'])}</b>\n{t['dir']} · {html.escape(t['strategy_name'])}\n"
                     f"{t['exit_reason']} at {t['exit']:.{MARKET_BY_ID[t['market']]['dp']}f} → <b>{t['r']:+.2f}R</b> (after costs)")
        if halted and state.get("halt_sent") != today_ist:
            telegram(f"🛑 <b>Daily loss limit hit</b> ({day_r:+.2f}R today). EdgeDesk will not send new signals until tomorrow. Step away from the screen.")
            state["halt_sent"] = today_ist
    if not tg_on:
        bots.done("telegram", "Not configured (add the TELEGRAM_TOKEN secret and send /start to your bot)", "warn")
    else:
        bad = [n for n in tg_notes if "sent" not in n]
        bots.done("telegram", f"{len(new_signals)} signal alert(s), {len(closed_now)} close alert(s)" + (f"; {bad[0]}" if bad else ""),
                  "error" if bad else "ok")

    bots.done("chief", f"Finished in {time.time() - t0:.1f}s" + (f" with {len(errors)} warning(s)" if errors else ""),
              "warn" if errors else "ok")

    tradeable = [k for k, v in bt.items() if v["verdict"] == "TRADEABLE"]
    gen = now_utc()
    out = dict(
        app="EdgeDesk Live", version=VERSION, mode="demo" if args.demo else "live",
        generated_at=gen.isoformat(), generated_at_ist=gen.astimezone(IST).strftime("%d %b %Y, %H:%M IST"),
        refresh_minutes=15, disclaimer=DISCLAIMER,
        settings=dict(risk_pct=RISK_PCT, daily_loss_limit_r=DAILY_LOSS_LIMIT_R, max_open=MAX_OPEN_TRADES, min_score=MIN_SCORE,
                      chase_limit_atr=CHASE_LIMIT_ATR, accounts=ACCOUNTS, timeframe="Daily candles (finished bars only)"),
        summary=dict(markets_loaded=len(raw), markets_total=len(MARKETS), new_signals=len(new_signals), open_trades=len(open_view),
                     upcoming=len(upcoming), blocked=len(blocked), tradeable_strategies=tradeable,
                     today_closed_r=rnd(day_r, 2), trading_halted=halted),
        markets=markets, signals=open_view, new_signals=[c["id"] for c in new_signals],
        blocked=[{k: c[k] for k in ("market", "name", "strategy_name", "direction", "score", "blocks", "bar_date", "entry", "stop", "target")} for c in blocked],
        upcoming=upcoming, track_record=track_record(state),
        strategies=list(bt.values()), bots=bots.as_list(), news=news[:40], errors=errors,
    )
    (DATA / ("signals_demo.json" if args.demo and args.keep_live else "signals.json")).write_text(json.dumps(clean(out), indent=1))
    state_path.write_text(json.dumps(clean(state), indent=1))
    log(f"Wrote data/signals.json: {len(raw)} markets, {len(new_signals)} new signals, {len(open_view)} open, "
        f"{len(upcoming)} upcoming, {len(blocked)} blocked, tradeable: {tradeable or 'none'}")
    if args.hello and not hello_ok:
        log("ERROR: could not send the Telegram 'connected' message: " + "; ".join(tg_notes))
        return 3
    return 0


def main():
    ap = argparse.ArgumentParser(description="EdgeDesk Live bots (paper trading, not investment advice)")
    ap.add_argument("--demo", action="store_true", help="use fake offline data")
    ap.add_argument("--hello", action="store_true", help="send 'EdgeDesk is connected' to Telegram")
    ap.add_argument("--keep-live", action="store_true", help="with --demo: write signals_demo.json instead of signals.json")
    args = ap.parse_args()
    try:
        sys.exit(chief(args))
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
