"""Every bet a picker can offer, whether or not a sportsbook posted a price."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    key: str
    label: str
    sport: str
    group: str
    stat_keys: tuple[str, ...]
    nhl_key: str | None
    lines: tuple[float, ...]
    milestones: tuple[float, ...]
    anytime: bool = False
    board_market: str = ""


def _m(**kwargs) -> Market:
    return Market(**kwargs)


MARKETS: tuple[Market, ...] = (
    _m(key="nhl_goals", label="Goals", sport="NHL", group="skater", stat_keys=("goals",), nhl_key="goals", lines=(0.5, 1.5), milestones=(2,), anytime=True, board_market="GOALSCORER"),
    _m(key="nhl_assists", label="Assists", sport="NHL", group="skater", stat_keys=("assists",), nhl_key="assists", lines=(0.5, 1.5), milestones=(1, 2), board_market="ASSISTS"),
    _m(key="nhl_points", label="Points", sport="NHL", group="skater", stat_keys=("points",), nhl_key="points", lines=(0.5, 1.5, 2.5), milestones=(1, 2, 3), board_market="POINTS"),
    _m(key="nhl_sog", label="Shots on goal", sport="NHL", group="skater", stat_keys=("shotsTotal", "shots"), nhl_key="shots", lines=(1.5, 2.5, 3.5, 4.5), milestones=(2, 3, 4, 5), board_market="SOG"),
    _m(key="nhl_hits", label="Hits", sport="NHL", group="skater", stat_keys=("hits",), nhl_key="hits", lines=(0.5, 1.5, 2.5, 3.5), milestones=(1, 2, 3), board_market="HITS"),
    _m(key="nhl_blocks", label="Blocked shots", sport="NHL", group="skater", stat_keys=("blockedShots", "blocks"), nhl_key="blockedShots", lines=(0.5, 1.5, 2.5), milestones=(1, 2), board_market="BLOCKS"),
    _m(key="nhl_pim", label="Penalty minutes", sport="NHL", group="skater", stat_keys=("penaltyMinutes", "pim"), nhl_key="pim", lines=(0.5, 1.5, 2.5), milestones=(2,), board_market="PIM"),
    _m(key="nhl_saves", label="Saves", sport="NHL", group="goalie", stat_keys=("saves",), nhl_key="saves", lines=(24.5, 28.5, 31.5), milestones=(), board_market="SAVES"),
    _m(key="nhl_ga", label="Goals against", sport="NHL", group="goalie", stat_keys=("goalsAgainst",), nhl_key="goalsAgainst", lines=(1.5, 2.5, 3.5), milestones=(), board_market="GOALS_AGAINST"),
    _m(key="nba_points", label="Points", sport="NBA", group="player", stat_keys=("points",), nhl_key=None, lines=(14.5, 19.5, 24.5, 29.5), milestones=(10, 15, 20, 25, 30, 35), board_market="POINTS"),
    _m(key="nba_rebounds", label="Rebounds", sport="NBA", group="player", stat_keys=("rebounds", "totalRebounds"), nhl_key=None, lines=(4.5, 6.5, 8.5, 10.5), milestones=(6, 8, 10, 12), board_market="REBOUNDS"),
    _m(key="nba_assists", label="Assists", sport="NBA", group="player", stat_keys=("assists",), nhl_key=None, lines=(3.5, 5.5, 7.5, 9.5), milestones=(4, 6, 8, 10), board_market="ASSISTS"),
    _m(key="nba_threes", label="Threes", sport="NBA", group="player", stat_keys=("threePointFieldGoalsMade",), nhl_key=None, lines=(0.5, 1.5, 2.5, 3.5, 4.5), milestones=(2, 3, 4), board_market="THREES"),
    _m(key="nba_steals", label="Steals", sport="NBA", group="player", stat_keys=("steals",), nhl_key=None, lines=(0.5, 1.5, 2.5), milestones=(1, 2), board_market="STEALS"),
    _m(key="nba_blocks", label="Blocks", sport="NBA", group="player", stat_keys=("blocks",), nhl_key=None, lines=(0.5, 1.5, 2.5), milestones=(1, 2), board_market="BLOCKS"),
    _m(key="nba_turnovers", label="Turnovers", sport="NBA", group="player", stat_keys=("turnovers",), nhl_key=None, lines=(1.5, 2.5, 3.5), milestones=(2, 3), board_market="TURNOVERS"),
    _m(key="nba_pra", label="Points + rebounds + assists", sport="NBA", group="player", stat_keys=("points", "rebounds", "totalRebounds", "assists"), nhl_key=None, lines=(24.5, 34.5, 44.5), milestones=(25, 30, 40), board_market="PRA"),
    _m(key="nfl_td", label="Anytime touchdown", sport="NFL", group="player", stat_keys=("rushingTouchdowns", "receivingTouchdowns", "passingTouchdowns"), nhl_key=None, lines=(), milestones=(2,), anytime=True, board_market="ANYTIME_TD"),
    _m(key="nfl_pass_yds", label="Passing yards", sport="NFL", group="player", stat_keys=("passingYards",), nhl_key=None, lines=(199.5, 224.5, 249.5, 274.5, 299.5), milestones=(200, 250, 300), board_market="PASS_YDS"),
    _m(key="nfl_pass_td", label="Passing touchdowns", sport="NFL", group="player", stat_keys=("passingTouchdowns",), nhl_key=None, lines=(0.5, 1.5, 2.5), milestones=(1, 2, 3), board_market="PASS_TD"),
    _m(key="nfl_comp", label="Completions", sport="NFL", group="player", stat_keys=("completions",), nhl_key=None, lines=(17.5, 20.5, 23.5), milestones=(20, 25), board_market="COMPLETIONS"),
    _m(key="nfl_int", label="Interceptions", sport="NFL", group="player", stat_keys=("interceptions",), nhl_key=None, lines=(0.5, 1.5), milestones=(1,), board_market="INTERCEPTIONS"),
    _m(key="nfl_rush_yds", label="Rushing yards", sport="NFL", group="player", stat_keys=("rushingYards",), nhl_key=None, lines=(24.5, 49.5, 74.5, 99.5), milestones=(50, 75, 100), board_market="RUSH_YDS"),
    _m(key="nfl_rush_att", label="Rush attempts", sport="NFL", group="player", stat_keys=("rushingAttempts",), nhl_key=None, lines=(9.5, 12.5, 15.5), milestones=(10, 15), board_market="RUSH_ATT"),
    _m(key="nfl_rec_yds", label="Receiving yards", sport="NFL", group="player", stat_keys=("receivingYards",), nhl_key=None, lines=(29.5, 49.5, 69.5, 99.5), milestones=(40, 60, 80, 100), board_market="REC_YDS"),
    _m(key="nfl_rec", label="Receptions", sport="NFL", group="player", stat_keys=("receptions",), nhl_key=None, lines=(2.5, 3.5, 4.5, 5.5), milestones=(3, 5, 6), board_market="RECEPTIONS"),
    _m(key="team_ml", label="Moneyline", sport="ALL", group="team", stat_keys=(), nhl_key=None, lines=(), milestones=(), board_market="MONEYLINE"),
    _m(key="team_spread", label="Spread", sport="ALL", group="team", stat_keys=(), nhl_key=None, lines=(), milestones=(), board_market="SPREAD"),
    _m(key="team_total", label="Game total", sport="ALL", group="team", stat_keys=(), nhl_key=None, lines=(), milestones=(), board_market="TOTAL"),
    _m(key="team_team_total", label="Team total", sport="ALL", group="team", stat_keys=(), nhl_key=None, lines=(), milestones=(), board_market="TEAM_TOTAL"),
)


def markets_for(sport: str, group: str) -> list[Market]:
    sport = sport.upper()
    rows = []
    for market in MARKETS:
        if market.group != group:
            continue
        if market.sport not in {sport, "ALL"}:
            continue
        if sport == "NHL" and market.key == "team_spread":
            rows.append(Market(**{**market.__dict__, "label": "Puck line"}))
            continue
        rows.append(market)
    return rows
