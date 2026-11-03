"""Team logo URLs from the ESPN CDN, with a text badge when a team has no known logo.

ESPN logo files are named by ESPN's own team slug, which differs from the
abbreviation the NHL feed or a sportsbook uses for some teams (LAK is "la",
NJD is "nj", Utah is "utah", and so on). Every abbreviation the app can produce
is listed here explicitly, so a logo is only requested when the slug has been
checked against the CDN. Anything else renders as a text badge, never as a
broken image.
"""

from __future__ import annotations

import html

CDN = "https://a.espncdn.com/i/teamlogos/{sport}/500/{slug}.png"

_SPORT_PATH = {"NHL": "nhl", "NBA": "nba", "NFL": "nfl"}

# abbreviation the app sees -> ESPN file slug (lowercase)
_NHL = {
    "ANA": "ana", "BOS": "bos", "BUF": "buf", "CGY": "cgy", "CAR": "car", "CHI": "chi",
    "COL": "col", "CBJ": "cbj", "DAL": "dal", "DET": "det", "EDM": "edm", "FLA": "fla",
    "LAK": "la", "MIN": "min", "MTL": "mtl", "NSH": "nsh", "NJD": "nj", "NYI": "nyi",
    "NYR": "nyr", "OTT": "ott", "PHI": "phi", "PIT": "pit", "SJS": "sj", "SEA": "sea",
    "STL": "stl", "TBL": "tb", "TOR": "tor", "UTA": "utah", "VAN": "van", "VGK": "vgk",
    "WPG": "wpg", "WSH": "wsh",
    # ESPN-style spellings, in case a feed uses them
    "LA": "la", "NJ": "nj", "SJ": "sj", "TB": "tb", "UTAH": "utah", "WAS": "wsh",
}

# ESPN's NBA feed uses GS, NY, NO, SA, UTAH and WSH. The usual NBA tricodes are kept as aliases.
_NBA = {
    "ATL": "atl", "BOS": "bos", "BKN": "bkn", "CHA": "cha", "CHI": "chi", "CLE": "cle",
    "DAL": "dal", "DEN": "den", "DET": "det", "GS": "gs", "HOU": "hou", "IND": "ind",
    "LAC": "lac", "LAL": "lal", "MEM": "mem", "MIA": "mia", "MIL": "mil", "MIN": "min",
    "NO": "no", "NY": "ny", "OKC": "okc", "ORL": "orl", "PHI": "phi", "PHX": "phx",
    "POR": "por", "SAC": "sac", "SA": "sa", "TOR": "tor", "UTAH": "utah", "WSH": "wsh",
    "GSW": "gs", "NOP": "no", "NYK": "ny", "SAS": "sa", "UTA": "utah", "WAS": "wsh", "PHO": "phx",
}

_NFL = {
    "ARI": "ari", "ATL": "atl", "BAL": "bal", "BUF": "buf", "CAR": "car", "CHI": "chi",
    "CIN": "cin", "CLE": "cle", "DAL": "dal", "DEN": "den", "DET": "det", "GB": "gb",
    "HOU": "hou", "IND": "ind", "JAX": "jax", "KC": "kc", "LV": "lv", "LAC": "lac",
    "LAR": "lar", "MIA": "mia", "MIN": "min", "NE": "ne", "NO": "no", "NYG": "nyg",
    "NYJ": "nyj", "PHI": "phi", "PIT": "pit", "SF": "sf", "SEA": "sea", "TB": "tb",
    "TEN": "ten", "WSH": "wsh",
    "WAS": "wsh", "JAC": "jax", "LA": "lar", "LVR": "lv", "GNB": "gb", "KAN": "kc",
    "NWE": "ne", "NOR": "no", "SFO": "sf", "TAM": "tb",
}

SLUGS: dict[str, dict[str, str]] = {"NHL": _NHL, "NBA": _NBA, "NFL": _NFL}


def logo_slug(league: str, abbrev: str) -> str | None:
    table = SLUGS.get(str(league or "").upper())
    if table is None:
        return None
    return table.get(str(abbrev or "").strip().upper())


def team_logo_url(league: str, abbrev: str) -> str | None:
    """CDN URL for a known team, or None when the abbreviation has no verified logo."""
    slug = logo_slug(league, abbrev)
    if slug is None:
        return None
    return CDN.format(sport=_SPORT_PATH[str(league).upper()], slug=slug)


def team_badge_html(abbrev: str, size: int = 28) -> str:
    text = html.escape((str(abbrev or "").strip().upper() or "?")[:4])
    return (
        f"<span class='team-badge' role='img' aria-label='{text}' "
        f"style='width:{size}px;height:{size}px;'>{text}</span>"
    )


def team_logo_html(league: str, abbrev: str, size: int = 28, css_class: str = "tick-logo") -> str:
    """An <img> for a known team, otherwise a readable text badge of the same size."""
    url = team_logo_url(league, abbrev)
    if url is None:
        return team_badge_html(abbrev, size)
    return (
        f"<img class='{css_class}' src='{url}' alt='' width='{size}' height='{size}' loading='lazy' "
        f"style='width:{size}px;height:{size}px;max-width:{size}px;max-height:{size}px;object-fit:contain;'>"
    )
