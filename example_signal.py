#!/usr/bin/env python3
"""Sends a clearly-labelled EXAMPLE gap-fade trade (entry, stop move, exit) to Telegram. Not a real signal."""
import json, os, time
import bot

def send(t):
    try:
        st = json.loads((bot.DATA / "state.json").read_text())
    except Exception:
        st = {}
    os.environ["EDGEDESK_CHAT"] = bot.find_chat_id(st)
    print(bot.telegram("⚠️ <b>EXAMPLE ONLY - DO NOT TRADE</b>\n\n" + t))

send("🔔 <b>TRADE: SELL SBIN</b>\nQty: <b>2</b>\nStop-loss: <b>817.00</b> (= highest price of 9:15-9:30; max loss ₹10)\n"
     "Target: <b>800.00</b> (yesterday's close; profit ~₹24)\n\n<b>Do in Kite (all Intraday / MIS):</b>\n"
     "1) <code>SELL 2 SBIN MARKET</code>\n2) <code>BUY 2 SBIN SL-M trigger 817.00</code>\n3) <code>BUY 2 SBIN LIMIT 800.00</code>\n"
     "When 2) or 3) fills, cancel the other. Close everything by 3:10 PM.\n\n"
     "Why: SBIN went UP more than 1% at the open, then started falling.\n"
     "Skip it if the price is already far from 812.00. Never add more if it goes against you.\n<i>Not proven · not investment advice.</i>")
time.sleep(2)
send("🛡️ <b>SBIN: move your stop-loss to 809.50</b> (profit protection starts - trade is up 1.2R).\n"
     "In Kite: Orders → your SL order → Modify → trigger 809.50 → Modify. Never let this profit turn into a loss.")
time.sleep(2)
send("🚪 <b>EXIT SBIN</b> (short) - target (gap filled) at ~800.00\n"
     "If your stop/target order already filled: done ✅ - cancel any leftover order.\n"
     "Estimated result: <b>₹+21.6</b> after charges (your real P&L is in Kite → Positions).")
