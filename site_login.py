#!/usr/bin/env python3
"""
Turns the SITE_LOGIN secret ("userid:password") into data/auth.json = salted PBKDF2 hash for the website login.
The real password is never written anywhere. If SITE_LOGIN is empty, the login screen is switched off.
Note: this keeps casual visitors out; it is not bank-level security (the GitHub repo itself is public).
"""
import hashlib, json, os, secrets
from pathlib import Path

P = Path(__file__).resolve().parent / "data" / "auth.json"
ITER = 150_000
login = os.getenv("SITE_LOGIN", "").strip()
if not login or ":" not in login:
    if P.exists():
        P.unlink()
    print("SITE_LOGIN not set (format userid:password) - login screen OFF")
else:
    user, pw = login.split(":", 1)
    old = json.loads(P.read_text()) if P.exists() else {}
    salt = old.get("salt") or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", f"{user.strip().lower()}:{pw}".encode(), bytes.fromhex(salt), ITER).hex()
    if old.get("hash") != h:
        P.write_text(json.dumps({"salt": salt, "iterations": ITER, "hash": h}))
        print("login screen ON (hash updated)")
    else:
        print("login screen ON (unchanged)")
