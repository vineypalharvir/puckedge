"""Runtime paths and external API settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "puckedge.db"


def database_path() -> Path:
    override = os.getenv("PUCKEDGE_DB", "").strip()
    if override:
        return Path(override)
    return DB_PATH

NHL_WEB_BASE = "https://api-web.nhle.com/v1"
NHL_STATS_BASE = "https://api.nhle.com/stats/rest/en"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
# us has DraftKings and FanDuel. uk and eu have Bet365 and Pinnacle.
ODDS_API_REGIONS = os.getenv("ODDS_API_REGIONS", "us,uk,eu").strip() or "us,uk,eu"
ODDS_API_REGION = ODDS_API_REGIONS
ODDS_BOOK_PRIORITY = ("bet365", "draftkings", "fanduel", "pinnacle")
BOOK_LABELS = {
    "bet365": "Bet365",
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "pinnacle": "Pinnacle",
    "synthetic-bet365": "Synthetic board",
}

REQUEST_TIMEOUT = 15.0
REQUEST_RETRIES = 3
USER_AGENT = "PuckEdgeParlayEngine/1.0"

DEFAULT_BANKROLL = 1000.0
DEFAULT_STAKE = 25.0
SETTLEMENT_INTERVAL_MINUTES = 15
