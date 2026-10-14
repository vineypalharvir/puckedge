"""Pydantic models shared by the pipeline, generator, and ledger."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TeamSide(BaseModel):
    team_id: int
    abbrev: str
    name: str
    place: str = ""
    common: str = ""


class Game(BaseModel):
    game_id: str
    game_date: str
    start_time_utc: str
    game_state: str
    venue: str = ""
    home: TeamSide
    away: TeamSide
    home_score: int | None = None
    away_score: int | None = None
    home_sog: int | None = None
    away_sog: int | None = None


class TeamProfile(BaseModel):
    abbrev: str
    name: str
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    ot_losses: int = 0
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0
    gf_per_game: float = 3.0
    ga_per_game: float = 3.0
    l10_wins: int = 0
    l10_losses: int = 0
    l10_ot: int = 0
    l10_gp: int = 0
    l10_gf: float = 0.0
    l10_ga: float = 0.0
    l10_gf_per_game: float = 3.0
    l10_ga_per_game: float = 3.0
    home_wins: int = 0
    home_losses: int = 0
    home_ot: int = 0
    road_wins: int = 0
    road_losses: int = 0
    road_ot: int = 0
    streak_code: str = ""
    streak_count: int = 0
    shots_for_per_game: float = 29.0
    shots_against_per_game: float = 29.0
    power_play_pct: float = 0.20
    penalty_kill_pct: float = 0.80
    rest_days: int | None = None
    back_to_back: bool = False
    back_to_back_road: bool = False
    last_game_date: str | None = None


class PlayerProfile(BaseModel):
    player_id: int
    name: str
    team_abbrev: str
    position: str
    games_played: int = 0
    goals: int = 0
    assists: int = 0
    points: int = 0
    shots: int = 0
    sog_per_game: float = 2.5
    goals_per_game: float = 0.3
    points_per_game: float = 0.7
    recent_shots: list[int] = Field(default_factory=list)
    recent_goals: list[int] = Field(default_factory=list)
    recent_points: list[int] = Field(default_factory=list)
    l5_sog: float = 2.5
    l10_sog: float = 2.5
    l5_goals: float = 0.3
    l10_goals: float = 0.3
    l5_points: float = 0.7
    l10_points: float = 0.7
    l5_games: int = 0
    l10_games: int = 0


class GoalieProfile(BaseModel):
    player_id: int
    name: str
    team_abbrev: str
    games_started: int = 0
    save_pct: float = 0.900
    goals_against_average: float = 2.8
    shots_against: int = 0
    saves: int = 0
    goals_against: int = 0
    projected_starter: bool = False


class SlateContext(BaseModel):
    slate_date: str
    source: str
    games: list[Game]
    teams: dict[str, TeamProfile]
    players: list[PlayerProfile]
    goalies: list[GoalieProfile]
    notes: list[str] = Field(default_factory=list)


class Offer(BaseModel):
    game_id: str
    matchup: str
    game_date: str
    start_time_utc: str
    market_type: str
    market_key: str
    target_entity: str
    player_id: int | None = None
    team_abbrev: str | None = None
    opponent_abbrev: str | None = None
    selection: str
    side: str
    line: float | None = None
    american_odds: int
    bookmaker: str
    selectable: bool = True


class MarketLeg(BaseModel):
    leg_id: str
    game_id: str
    matchup: str
    game_date: str
    start_time_utc: str
    market_type: str
    market_key: str
    target_entity: str
    player_id: int | None = None
    team_abbrev: str | None = None
    opponent_abbrev: str | None = None
    selection: str
    side: str
    line: float | None = None
    american_odds: int
    decimal_odds: float
    implied_prob: float
    fair_prob: float
    model_prob: float
    push_prob: float = 0.0
    ev: float
    bookmaker: str
    rationale: str
    tags: list[str] = Field(default_factory=list)
    selectable: bool = True


class BuiltParlay(BaseModel):
    risk_tier: str
    legs: list[MarketLeg]
    american_odds: int
    decimal_odds: float
    model_prob: float
    ev: float
    synergy_score: int
    synergy_notes: list[str] = Field(default_factory=list)
    rationale_summary: str
    within_odds_band: bool
    filters_relaxed: bool = False
    recommended_stake: float
    guideline_low: float
    guideline_high: float
    kelly_fraction: float
    exposure_pct: float
    exposure_warning: str | None = None
    stake: float
    potential_payout: float
    net_profit_if_win: float
    entry_edge: float


class SkaterLine(BaseModel):
    player_id: int
    name: str
    team_abbrev: str
    goals: int = 0
    assists: int = 0
    points: int = 0
    sog: int = 0


class GoalieLine(BaseModel):
    player_id: int
    name: str
    team_abbrev: str
    saves: int = 0
    goals_against: int = 0
    shots_against: int = 0
    starter: bool = False


class BoxScore(BaseModel):
    game_id: str
    game_state: str
    home_abbrev: str
    away_abbrev: str
    home_score: int
    away_score: int
    home_sog: int = 0
    away_sog: int = 0
    went_beyond_regulation: bool = False
    skaters: list[SkaterLine] = Field(default_factory=list)
    goalies: list[GoalieLine] = Field(default_factory=list)

    @property
    def is_final(self) -> bool:
        return self.game_state in {"OFF", "FINAL", "OFFICIAL"}


class PricedBoard(BaseModel):
    context: SlateContext
    odds_source: str
    legs: list[MarketLeg]
    badges: dict[str, list[str]] = Field(default_factory=dict)
    available_books: list[str] = Field(default_factory=list)
