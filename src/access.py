"""Paid-access list. The owner edits this file and pushes it to GitHub.

Friends download the list when they sign in. A missing or expired entry
cannot open the app.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path

from config import ROOT

ACCESS_PATH = ROOT / "access.json"
REMOTE_ACCESS = "https://raw.githubusercontent.com/vineypalharvir/puckedge/main/access.json"
OWNER_USERNAME = "hvineypal"
FREE_UNTIL = datetime(2026, 11, 7, 23, 59, 59)
UNPAID_MESSAGE = (
    "You haven't paid yet. You can't access it. "
    "If this is an error, please contact the owner so they can fix it for you."
)


def _parse_when(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def load_local_access() -> dict:
    if not ACCESS_PATH.is_file():
        return {}
    try:
        payload = json.loads(ACCESS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload.get("users") or {})


def load_remote_access() -> dict:
    try:
        request = urllib.request.Request(REMOTE_ACCESS, headers={"User-Agent": "DarkNode"})
        with urllib.request.urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}
    return dict(payload.get("users") or {})


def load_access() -> dict:
    """Remote list wins when GitHub answers. Local file covers the owner while offline."""
    remote = load_remote_access()
    local = load_local_access()
    if remote:
        return remote
    return local


def save_access(users: dict) -> None:
    ACCESS_PATH.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")


def access_allowed(username: str, now: datetime | None = None, rules: dict | None = None) -> tuple[bool, str]:
    current = now or datetime.now()
    name = username.strip().lower()
    if name == OWNER_USERNAME:
        return True, ""
    if current <= FREE_UNTIL:
        return True, ""
    entry = (rules if rules is not None else load_access()).get(name)
    if not entry or not entry.get("approved"):
        return False, UNPAID_MESSAGE
    start = _parse_when(entry.get("start"))
    end = _parse_when(entry.get("end"))
    if start and current < start:
        return False, UNPAID_MESSAGE
    if end and current > end:
        return False, UNPAID_MESSAGE
    return True, ""
