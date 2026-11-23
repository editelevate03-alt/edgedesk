#!/usr/bin/env python3
"""
India market news bot - free RSS headlines -> news mood for NIFTY / BANK NIFTY.

- Sources: Economic Times Markets, Moneycontrol, Livemint, Business Standard, Google News (India).
- Mood = simple finance phrase/word scoring of headlines from the last ~18 hours, -100 (bearish) .. +100 (bullish).
- Flags high-impact events (RBI policy, budget, CPI/GDP data, US Fed, results, elections, war).
- Every day's mood is saved in data/news_log.json so we can later MEASURE whether it helps signals.

Headline mood is a rough hint, not a prediction. It has NOT been backtested (no free news archive).
Not investment advice.
"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import bot
from bot import IST, UA, log

FEEDS = [
    ("ET Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Livemint", "https://www.livemint.com/rss/markets"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Google News: Nifty", "https://news.google.com/rss/search?q=Nifty+OR+Sensex+OR+%22Bank+Nifty%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News: RBI/FII", "https://news.google.com/rss/search?q=RBI+OR+FII+OR+DII+OR+rupee+OR+%22crude+oil%22+India+when:1d&hl=en-IN&gl=IN&ceid=IN:en"),
]

# phrases first (context matters: "crude falls" is GOOD for India, "rupee falls" is BAD)
PHRASES = [
    (r"\b(crude|oil)\b.{0,25}\b(falls?|drops?|slips?|eases?|declines?|tumbles?|cools?)\b", +1),
    (r"\b(crude|oil)\b.{0,25}\b(rises?|jumps?|surges?|soars?|spikes?|climbs?)\b", -1),
    (r"\brupee\b.{0,25}\b(falls?|drops?|slips?|weakens?|record low|hits low)\b", -1),
    (r"\brupee\b.{0,25}\b(rises?|gains?|strengthens?|jumps?)\b", +1),
    (r"\b(fii|fpi|foreign investors?)s?\b.{0,30}\b(sell|selling|sold|outflows?|pull out|dump)", -1),
    (r"\b(fii|fpi|foreign investors?)s?\b.{0,30}\b(buy|buying|bought|inflows?|pour)", +1),
    (r"\binflation\b.{0,25}\b(eases?|cools?|falls?|slows?|lower)\b", +1),
    (r"\binflation\b.{0,25}\b(rises?|jumps?|accelerates?|higher|spikes?)\b", -1),
    (r"\b(rate|repo)\b.{0,15}\bcut\b", +1),
    (r"\b(rate|repo)\b.{0,15}\bhike\b", -1),
    (r"\bvix\b.{0,20}\b(jumps?|spikes?|surges?|rises?)\b", -1),
]
BULL = ["surge", "surges", "rally", "rallies", "jumps", "gains", "record high", "all-time high", "soars", "climbs", "rebound",
        "rebounds", "recovers", "beats", "upgrade", "bullish", "strong", "higher", "upbeat", "optimism", "buy rating", "outperform",
        "boost", "positive", "green", "jump", "jumped", "rise", "rises", "rose", "gain", "gained", "rallied", "soar", "up"]
BEAR = ["crash", "crashes", "plunge", "plunges", "slump", "slumps", "falls", "drops", "declines", "slides", "tumbles", "sell-off",
        "selloff", "sheds", "misses", "downgrade", "bearish", "weak", "lower", "worries", "fears", "concerns", "tension", "tensions",
        "war", "slowdown", "red", "loss", "losses", "probe", "fraud", "default", "fall", "fell", "drop", "dropped",
        "slip", "slips", "slipped", "slide", "plummets", "sink", "sinks", "down"]
EVENTS = {
    "RBI policy": r"\b(rbi|mpc)\b.{0,40}\b(policy|repo|rate|meeting|decision)\b",
    "Budget": r"\bbudget\b",
    "Inflation data": r"\b(cpi|wpi|inflation data|retail inflation)\b",
    "GDP data": r"\bgdp\b",
    "US Fed": r"\b(fed|fomc|powell)\b",
    "Company results": r"\b(q[1-4] results|quarterly results|earnings)\b",
    "Elections": r"\belection",
    "War / geopolitics": r"\b(war|attack|missile|border clash|sanctions)\b",
}
BANK_WORDS = r"\b(bank|banks|banking|rbi|hdfc|icici|sbi|kotak|axis|indusind|npa|credit growth|loan|psu bank|bank nifty)\b"


def score_title(t: str) -> int:
    s = " " + t.lower() + " "
    total = 0
    for pat, v in PHRASES:
        if re.search(pat, s):
            total += 2 * v
            s = re.sub(pat, " ", s)            # don't double-count the words inside the phrase
    total += sum(1 for w in BULL if re.search(rf"\b{re.escape(w)}\b", s))
    total -= sum(1 for w in BEAR if re.search(rf"\b{re.escape(w)}\b", s))
    return max(-1, min(1, total))


def fetch_headlines(hours: int = 18) -> tuple[list[dict], int]:
    items, fails = [], 0
    for name, url in FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=12)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = html.unescape((it.findtext("title") or "").strip())
                if not title:
                    continue
                try:
                    pub = pd.to_datetime(it.findtext("pubDate") or "", utc=True)
                except Exception:
                    pub = None
                items.append(dict(title=title, source=name, link=(it.findtext("link") or "").strip(),
                                  published=pub.isoformat() if pub is not None and not pd.isna(pub) else None))
        except Exception as e:
            fails += 1
            log(f"news: {name} failed: {type(e).__name__}")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen, out = set(), []
    for it in items:
        key = re.sub(r"\W+", "", it["title"].lower())[:70]
        if key in seen:
            continue
        seen.add(key)
        if it["published"] and pd.Timestamp(it["published"]) < cutoff:
            continue
        it["score"] = score_title(it["title"])
        it["bank"] = bool(re.search(BANK_WORDS, it["title"].lower()))
        it["events"] = [k for k, p in EVENTS.items() if re.search(p, it["title"].lower())]
        out.append(it)
    out.sort(key=lambda x: x.get("published") or "", reverse=True)
    return out, fails


def mood(items: list[dict], bank: bool = False) -> int:
    rel = [i for i in items if i["bank"]] if bank else items
    rel = [i for i in rel if i["score"] != 0] or rel
    if not rel:
        return 0
    return int(round(100 * sum(i["score"] for i in rel) / len(rel)))


def label(m: int) -> str:
    return "Bullish 🟢" if m >= 20 else "Bearish 🔴" if m <= -20 else "Neutral ⚪"


def brief(save: bool = True) -> dict:
    items, fails = fetch_headlines()
    m_nifty, m_bank = mood(items), mood(items, bank=True)
    events = sorted({e for i in items for e in i["events"]})
    today = datetime.now(IST).date().isoformat()
    res = dict(date=today, nifty=m_nifty, banknifty=m_bank, events=events, headlines=len(items), feeds_failed=fails)
    if save:
        p = bot.DATA / "news_log.json"
        try:
            logd = json.loads(p.read_text())
        except Exception:
            logd = {}
        logd[today] = res
        p.write_text(json.dumps(logd, indent=1))
    top = [i for i in items if i["score"] != 0][:6] or items[:6]
    lines = [f"📰 <b>India market news - {today}</b> ({len(items)} headlines)",
             f"NIFTY mood: <b>{label(m_nifty)} ({m_nifty:+d})</b> · BANK NIFTY mood: <b>{label(m_bank)} ({m_bank:+d})</b>"]
    if events:
        lines.append("⚡ <b>Big-event news today:</b> " + ", ".join(events) + " - expect bigger, faster moves. Smaller size or wait.")
    lines.append("")
    for i in top:
        icon = "🟢" if i["score"] > 0 else "🔴" if i["score"] < 0 else "⚪"
        lines.append(f"{icon} {html.escape(i['title'][:140])} <i>({html.escape(i['source'])})</i>")
    lines.append("\n<i>News mood is a rough hint from headlines, not a prediction. Not yet proven - the bot is measuring it.</i>")
    res["text"] = "\n".join(lines)
    return res


def agreement(m: int, cp: int) -> str:
    if abs(m) < 20:
        return "⚪ News mood neutral - no help either way"
    return "🟢 News mood AGREES with this trade" if (m > 0) == (cp == 1) else "⚠️ News mood DISAGREES with this trade - extra caution"


if __name__ == "__main__":
    b = brief(save=False)
    print(b["text"])
