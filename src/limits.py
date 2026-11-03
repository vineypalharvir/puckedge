"""Per-account betting limits, asked once when a client first opens DarkNode."""

from __future__ import annotations

import json
import math
from pathlib import Path

from config import ROOT

LIMITS_PATH = ROOT / "data" / "limits.json"


def _key(username: str) -> str:
    return (username or "local").strip().lower() or "local"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def load_max_stake(username: str, path: Path = LIMITS_PATH) -> float | None:
    """The most this account will stake on one ticket, or None when never set."""
    row = _read(path).get(_key(username))
    return _valid(row.get("max_stake")) if isinstance(row, dict) else None


def save_max_stake(username: str, max_stake: float, path: Path = LIMITS_PATH) -> float:
    amount = _valid(max_stake)
    if amount is None:
        raise ValueError("Max stake must be above $0.")
    data = _read(path)
    data[_key(username)] = {"max_stake": round(amount, 2)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return round(amount, 2)


def capped_stake(stake: float, max_stake: float | None) -> float:
    """Stake limited to the account's max. A missing limit leaves the stake alone."""
    amount = _valid(stake) or 0.0
    cap = _valid(max_stake)
    return min(amount, cap) if cap is not None else amount
