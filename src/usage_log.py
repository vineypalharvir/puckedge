"""Accounts and an owner-only activity log.

Passwords are stored as a hash. Events never include API keys, the admin
password, or .env contents. Each sign-in records the address the app can see.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import urllib.request
from datetime import datetime, timezone

from config import ROOT

LOG_PATH = ROOT / "data" / "activity.jsonl"
ACCOUNTS_PATH = ROOT / "data" / "accounts.json"
REMEMBER_PATH = ROOT / "data" / "remember.json"


def admin_password() -> str:
    return os.getenv("PUCKEDGE_ADMIN_PASSWORD", "").strip()


def password_matches(typed: str) -> bool:
    expected = admin_password()
    if not expected or not typed:
        return False
    return typed.strip() == expected


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000)
    return digest.hex()


def _load_accounts() -> list[dict]:
    if not ACCOUNTS_PATH.is_file():
        return []
    try:
        payload = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(payload.get("users") or [])


def _save_accounts(users: list[dict]) -> None:
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_PATH.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")


def create_account(name: str, username: str, password: str) -> str | None:
    """Return an error string, or None when the account is created."""
    clean_name = " ".join(name.split())
    clean_user = username.strip().lower()
    if len(clean_name) < 2:
        return "Enter your name."
    if len(clean_user) < 3 or any(not (ch.isalnum() or ch in "._-") for ch in clean_user):
        return "Username needs at least 3 letters or numbers."
    if len(password) < 6:
        return "Password needs at least 6 characters."
    users = _load_accounts()
    if any(user["username"] == clean_user for user in users):
        return "That username is already taken."
    salt = secrets.token_hex(16)
    users.append(
        {
            "name": clean_name,
            "username": clean_user,
            "salt": salt,
            "password_hash": _hash_password(password, salt),
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "ips": [],
        }
    )
    _save_accounts(users)
    return None


def load_remembered() -> dict | None:
    if not REMEMBER_PATH.is_file():
        return None
    try:
        payload = json.loads(REMEMBER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        return None
    return {"username": username, "password": password}


def save_remembered(username: str, password: str) -> None:
    REMEMBER_PATH.parent.mkdir(parents=True, exist_ok=True)
    REMEMBER_PATH.write_text(
        json.dumps({"username": username.strip(), "password": password}),
        encoding="utf-8",
    )


def clear_remembered() -> None:
    if REMEMBER_PATH.is_file():
        REMEMBER_PATH.unlink()


def authenticate(username: str, password: str) -> dict | None:
    clean_user = username.strip().lower()
    for user in _load_accounts():
        if user["username"] != clean_user:
            continue
        if _hash_password(password, user["salt"]) == user["password_hash"]:
            return {"name": user["name"], "username": user["username"], "ips": list(user.get("ips") or [])}
    return None


def remember_ips(username: str, ips: list[str]) -> list[str]:
    clean = []
    for ip in ips:
        text = str(ip or "").strip()
        if text and text not in clean:
            clean.append(text)
    if not clean:
        return []
    users = _load_accounts()
    saved: list[str] = []
    for user in users:
        if user["username"] != username:
            continue
        current = list(user.get("ips") or [])
        for ip in clean:
            if ip not in current:
                current.append(ip)
        user["ips"] = current
        saved = current
    _save_accounts(users)
    return saved


def list_accounts() -> list[dict]:
    public = []
    for user in _load_accounts():
        public.append(
            {
                "name": user.get("name", ""),
                "username": user.get("username", ""),
                "created": user.get("created", ""),
                "ips": ", ".join(user.get("ips") or []),
            }
        )
    return public


def lookup_public_ip() -> str:
    try:
        request = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "DarkNode"})
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.read().decode("utf-8").strip()
    except Exception:
        return ""


def record_event(action: str, sport: str, summary: str, *, username: str = "", name: str = "", ips: str = "") -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "name": name,
        "username": username,
        "ips": ips,
        "action": action,
        "sport": sport,
        "summary": summary[:400],
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def read_events() -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    events: list[dict] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(events))
