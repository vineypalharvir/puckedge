"""Turn raw offers into model probabilities and written rationales."""

from __future__ import annotations

import hashlib

from src.api.data_models import (
    GoalieProfile,
    MarketLeg,
    Offer,
    PlayerProfile,
    SlateContext,
    TeamProfile,
)
from src.engine.devig import (
    american_to_decimal,
    american_to_implied,
    expected_value,
    multiplicative_devig,
    poisson_sf_over,
)
from src.engine.probs import expected_team_goals, final_score_probs, over_under_from_hist


def _blended(recent: float, season: float) -> float:
    return 0.6 * recent + 0.4 * season


def _form_win_rate(team: TeamProfile) -> float:
    games = max(team.games_played, 1)
    season = (team.wins + 0.5 * team.ot_losses) / games
    l10_games = max(team.l10_gp, 1)
    recent = (team.l10_wins + 0.5 * team.l10_ot) / l10_games
    return _blended(recent, season)


def _mean(values: list[float], default: float) -> float:
    clean = [value for value in values if value > 0]
    if not clean:
        return default
    return sum(clean) / len(clean)


class ModelContext:
    def __init__(self, slate: SlateContext):
        self.slate = slate
        self.teams = slate.teams
        self.players = {player.player_id: player for player in slate.players}
        self.goalies = {goalie.player_id: goalie for goalie in slate.goalies}
        self.league_ga = _mean([team.ga_per_game for team in slate.teams.values()], 3.0)
        self.league_sa = _mean([team.shots_against_per_game for team in slate.teams.values()], 29.0)
        self.league_pk = _mean([team.penalty_kill_pct for team in slate.teams.values()], 0.80)
        self.league_sv = _mean(
            [goalie.save_pct for goalie in slate.goalies if goalie.projected_starter],
            0.900,
        )
        self._matrices: dict[str, dict] = {}
        self._lambdas: dict[str, tuple[float, float]] = {}

    def lambdas(self, game_id: str, home_abbrev: str, away_abbrev: str) -> tuple[float, float]:
        if game_id in self._lambdas:
            return self._lambdas[game_id]
        home = self.teams[home_abbrev]
        away = self.teams[away_abbrev]
        home_off = _blended(home.l10_gf_per_game, home.gf_per_game)
        away_off = _blended(away.l10_gf_per_game, away.gf_per_game)
        home_def = _blended(home.l10_ga_per_game, home.ga_per_game)
        away_def = _blended(away.l10_ga_per_game, away.ga_per_game)
        home_lam = expected_team_goals(home_off, away_def, self.league_ga)
        away_lam = expected_team_goals(away_off, home_def, self.league_ga)
        if home.back_to_back_road:
            home_lam *= 0.955
        elif home.back_to_back:
            home_lam *= 0.98
        if away.back_to_back_road:
            away_lam *= 0.955
        elif away.back_to_back:
            away_lam *= 0.98
        self._lambdas[game_id] = (home_lam, away_lam)
        return home_lam, away_lam

    def matrix(self, game_id: str, home_abbrev: str, away_abbrev: str) -> dict:
        if game_id not in self._matrices:
            home_lam, away_lam = self.lambdas(game_id, home_abbrev, away_abbrev)
            self._matrices[game_id] = final_score_probs(home_lam, away_lam)
        return self._matrices[game_id]


def slate_badges(slate: SlateContext) -> dict[str, list[str]]:
    teams = [slate.teams[abbrev] for abbrev in {game.home.abbrev for game in slate.games} | {game.away.abbrev for game in slate.games} if abbrev in slate.teams]
    if not teams:
        return {}
    pp_rank = {team.abbrev: index for index, team in enumerate(sorted(teams, key=lambda item: item.power_play_pct, reverse=True), start=1)}
    pk_rank = {team.abbrev: index for index, team in enumerate(sorted(teams, key=lambda item: item.penalty_kill_pct), start=1)}
    goalies: dict[str, GoalieProfile] = {}
    for goalie in slate.goalies:
        if goalie.projected_starter or goalie.team_abbrev not in goalies:
            goalies[goalie.team_abbrev] = goalie
    badges: dict[str, list[str]] = {}
    for game in slate.games:
        notes: list[str] = []
        home = slate.teams.get(game.home.abbrev)
        away = slate.teams.get(game.away.abbrev)
        if home and away:
            if pp_rank.get(home.abbrev, 99) <= 3 and pk_rank.get(away.abbrev, 99) <= 5:
                notes.append("Top PP vs bottom-5 PK")
            if pp_rank.get(away.abbrev, 99) <= 3 and pk_rank.get(home.abbrev, 99) <= 5:
                notes.append("Top PP vs bottom-5 PK")
            if home.back_to_back_road or away.back_to_back_road:
                notes.append("B2B fatigue alert")
            elif home.back_to_back or away.back_to_back:
                notes.append("B2B fatigue alert")
        home_goalie = goalies.get(game.home.abbrev)
        away_goalie = goalies.get(game.away.abbrev)
        if home_goalie and away_goalie and abs(home_goalie.save_pct - away_goalie.save_pct) >= 0.015:
            better = home_goalie if home_goalie.save_pct > away_goalie.save_pct else away_goalie
            notes.append(f"Goalie edge: {better.name} {better.save_pct:.3f} SV%")
        badges[game.game_id] = notes
    return badges


def price_offers(slate: SlateContext, offers: list[Offer]) -> list[MarketLeg]:
    model = ModelContext(slate)
    grouped: dict[str, list[Offer]] = {}
    for offer in offers:
        grouped.setdefault(offer.market_key, []).append(offer)
    fair_by_id: dict[int, float] = {}
    for group in grouped.values():
        implied = [american_to_implied(offer.american_odds) for offer in group]
        if len(group) >= 2 and sum(implied) > 0:
            fair = multiplicative_devig(implied)
        else:
            fair = [min(implied[0] / 1.045, 0.97)]
        for offer, probability in zip(group, fair):
            fair_by_id[id(offer)] = probability

    games = {game.game_id: game for game in slate.games}
    legs: list[MarketLeg] = []
    for offer in offers:
        game = games.get(offer.game_id)
        if game is None or game.home.abbrev not in slate.teams or game.away.abbrev not in slate.teams:
            continue
        win, push, rationale, tags = _model_leg(model, game.home.abbrev, game.away.abbrev, offer)
        decimal_odds = american_to_decimal(offer.american_odds)
        implied = american_to_implied(offer.american_odds)
        fair = fair_by_id.get(id(offer), implied)
        ev = expected_value(win, decimal_odds, push)
        digest = hashlib.sha1(
            f"{offer.market_key}|{offer.side}|{offer.american_odds}|{offer.bookmaker}".encode()
        ).hexdigest()[:16]
        legs.append(
            MarketLeg(
                leg_id=digest,
                game_id=offer.game_id,
                matchup=offer.matchup,
                game_date=offer.game_date,
                start_time_utc=offer.start_time_utc,
                market_type=offer.market_type,
                market_key=offer.market_key,
                target_entity=offer.target_entity,
                player_id=offer.player_id,
                team_abbrev=offer.team_abbrev,
                opponent_abbrev=offer.opponent_abbrev,
                selection=offer.selection,
                side=offer.side,
                line=offer.line,
                american_odds=offer.american_odds,
                decimal_odds=decimal_odds,
                implied_prob=implied,
                fair_prob=fair,
                model_prob=win,
                push_prob=push,
                ev=ev,
                bookmaker=offer.bookmaker,
                rationale=rationale,
                tags=tags,
                selectable=offer.selectable,
            )
        )
    return legs


def _model_leg(model: ModelContext, home_abbrev: str, away_abbrev: str, offer: Offer):
    matrix = model.matrix(offer.game_id, home_abbrev, away_abbrev)
    home = model.teams[home_abbrev]
    away = model.teams[away_abbrev]
    if offer.market_type == "MONEYLINE":
        win = matrix["p_home_ml"] if offer.side == "HOME" else matrix["p_away_ml"]
        team = home if offer.side == "HOME" else away
        opponent = away if offer.side == "HOME" else home
        return win, 0.0, _team_rationale(team, opponent, win, "win"), _team_tags(team)
    if offer.market_type == "MONEYLINE_REG":
        if offer.side == "HOME":
            win = matrix["p_home_reg"]
        elif offer.side == "AWAY":
            win = matrix["p_away_reg"]
        else:
            win = matrix["p_draw"]
        team = home if offer.side == "HOME" else away
        return win, 0.0, f"Regulation-only price. Model probability {win:.1%}.", []
    if offer.market_type == "PUCKLINE" and offer.line is not None:
        win = _puckline_prob(matrix, offer.side, offer.line)
        team = home if offer.team_abbrev == home.abbrev else away
        return win, 0.0, _team_rationale(team, away if team is home else home, win, f"puck line {offer.line:+.1f}"), _team_tags(team)
    if offer.market_type == "TOTAL" and offer.line is not None:
        over, push = over_under_from_hist(matrix["total"], offer.line)
        win = over if offer.side == "OVER" else max(0.02, 1.0 - over - push)
        home_lam, away_lam = model.lambdas(offer.game_id, home_abbrev, away_abbrev)
        rationale = (
            f"Projected total {home_lam + away_lam:.2f} goals "
            f"({home.abbrev} {home_lam:.2f}, {away.abbrev} {away_lam:.2f}). "
            f"Model {offer.side.title()} {offer.line:.1f} at {win:.1%}."
        )
        return win, push if offer.line == int(offer.line) else 0.0, rationale, []
    if offer.market_type == "TEAM_TOTAL" and offer.line is not None and offer.team_abbrev:
        hist = matrix["home_goals"] if offer.team_abbrev == home.abbrev else matrix["away_goals"]
        over, push = over_under_from_hist(hist, offer.line)
        win = over if offer.side == "OVER" else max(0.02, 1.0 - over - push)
        team = model.teams[offer.team_abbrev]
        rationale = (
            f"{team.name} scores { _blended(team.l10_gf_per_game, team.gf_per_game):.2f} per game "
            f"on the 60/40 recent blend. Model {offer.selection} at {win:.1%}."
        )
        return win, push if float(offer.line).is_integer() else 0.0, rationale, _team_tags(team)
    if offer.market_type == "SOG" and offer.player_id in model.players and offer.line is not None:
        return _counting_prop(model, offer, kind="sog")
    if offer.market_type == "POINTS" and offer.player_id in model.players and offer.line is not None:
        return _counting_prop(model, offer, kind="points")
    if offer.market_type == "GOALSCORER" and offer.player_id in model.players and offer.line is not None:
        return _counting_prop(model, offer, kind="goals")
    if offer.market_type == "SAVES" and offer.player_id in model.goalies and offer.line is not None:
        return _saves_prop(model, offer)
    implied = american_to_implied(offer.american_odds)
    return implied, 0.0, "Model fallback uses the no-vig price because the stat sample was missing.", []


def _puckline_prob(matrix: dict, side: str, line: float) -> float:
    if line < 0:
        return matrix["p_home_cover_15"] if side == "HOME" else matrix["p_away_cover_15"]
    return matrix["p_home_plus_15"] if side == "HOME" else matrix["p_away_plus_15"]


def _counting_prop(model: ModelContext, offer: Offer, kind: str):
    player = model.players[offer.player_id]
    opponent = model.teams.get(offer.opponent_abbrev or "")
    team = model.teams.get(player.team_abbrev)
    if kind == "sog":
        base = _blended(player.l10_sog, player.sog_per_game)
        if opponent is not None and model.league_sa:
            base *= (opponent.shots_against_per_game / model.league_sa) ** 0.55
        if opponent is not None and player.position != "D" and opponent.penalty_kill_pct < model.league_pk:
            base *= 1.0 + (model.league_pk - opponent.penalty_kill_pct) * 0.35
        recent = player.recent_shots
        season_rate = player.sog_per_game
        unit = "SOG"
    elif kind == "points":
        base = _blended(player.l10_points, player.points_per_game)
        if opponent is not None and model.league_ga:
            base *= (opponent.ga_per_game / model.league_ga) ** 0.40
        recent = player.recent_points
        season_rate = player.points_per_game
        unit = "points"
    else:
        base = _blended(player.l10_goals, player.goals_per_game)
        if opponent is not None and model.league_ga:
            base *= (opponent.ga_per_game / model.league_ga) ** 0.45
        recent = player.recent_goals
        season_rate = player.goals_per_game
        unit = "goals"
    if team is not None and team.back_to_back_road:
        base *= 0.96
    elif team is not None and team.back_to_back:
        base *= 0.98
    base = min(max(base, 0.05), 8.0)
    over, push = poisson_sf_over(base, offer.line or 0.5)
    if offer.side in {"OVER", "YES"}:
        win = over
    else:
        win = max(0.02, 1.0 - over - push)
    hit = _hit_rate(recent, offer.line or 0.5)
    pieces = [
        f"Season pace {season_rate:.2f} {unit}/game over {player.games_played} GP.",
        f"Last 10 blend {base:.2f} after opponent adjustment.",
    ]
    if hit:
        pieces.append(hit)
    if opponent is not None and kind == "sog":
        pieces.append(f"Opponent allows {opponent.shots_against_per_game:.1f} shots/game and kills penalties at {opponent.penalty_kill_pct:.1%}.")
    elif opponent is not None:
        pieces.append(f"Opponent allows {opponent.ga_per_game:.2f} goals/game.")
    if team is not None and team.back_to_back_road:
        pieces.append(f"{team.abbrev} is on a road back-to-back, so the rate is cut 4%.")
    pieces.append(f"Model {offer.side.title()} {offer.line:.1f} at {win:.1%}.")
    tags = ["B2B"] if team is not None and team.back_to_back else []
    return min(max(win, 0.02), 0.95), push if offer.line is not None and float(offer.line).is_integer() else 0.0, " ".join(pieces), tags


def _saves_prop(model: ModelContext, offer: Offer):
    goalie = model.goalies[offer.player_id]
    opponent = model.teams.get(offer.opponent_abbrev or "")
    expected_shots = opponent.shots_for_per_game if opponent is not None else 29.0
    if opponent is not None and opponent.back_to_back_road:
        expected_shots *= 0.96
    lam = min(max(expected_shots * goalie.save_pct, 12.0), 42.0)
    over, push = poisson_sf_over(lam, offer.line or 28.5)
    win = over if offer.side == "OVER" else max(0.02, 1.0 - over - push)
    shot_text = f"{opponent.abbrev} generates {opponent.shots_for_per_game:.1f} shots/game." if opponent else ""
    rationale = (
        f"{goalie.name} saves {goalie.save_pct:.3f} of shots faced "
        f"({goalie.saves} saves on {goalie.shots_against} shots). {shot_text} "
        f"Projected saves {lam:.1f}. Model {offer.side.title()} {offer.line:.1f} at {win:.1%}."
    ).strip()
    return win, 0.0, rationale, []


def _hit_rate(values: list[int], line: float) -> str | None:
    sample = values[:10]
    if len(sample) < 3:
        return None
    hits = sum(1 for value in sample if value > line)
    window = min(len(sample), 10)
    return f"Last {window} games: {hits}/{window} cleared {line:.1f} ({hits / window:.0%})."


def _team_tags(team: TeamProfile) -> list[str]:
    tags = []
    if team.back_to_back_road:
        tags.append("Road B2B")
    elif team.back_to_back:
        tags.append("B2B")
    if team.streak_code and team.streak_count:
        tags.append(f"{team.streak_code}{team.streak_count}")
    return tags


def _team_rationale(team: TeamProfile, opponent: TeamProfile, probability: float, label: str) -> str:
    form = _form_win_rate(team)
    rest = []
    if team.back_to_back_road:
        rest.append("Road back-to-back applies a -4.5% win-expectancy penalty.")
    elif team.back_to_back:
        rest.append("Playing on the second night of a back-to-back.")
    elif team.rest_days is not None:
        rest.append(f"{team.rest_days} days rest.")
    if opponent.rest_days is not None and not opponent.back_to_back:
        rest.append(f"Opponent has {opponent.rest_days} days rest.")
    record = (
        f"L10 {team.l10_wins}-{team.l10_losses}-{team.l10_ot}, "
        f"season {team.wins}-{team.losses}-{team.ot_losses}. "
        f"60/40 form rating {form:.1%}."
    )
    matchup = (
        f"Opponent allows {opponent.ga_per_game:.2f} goals/game and {opponent.shots_against_per_game:.1f} shots/game, "
        f"with a {opponent.penalty_kill_pct:.1%} penalty kill."
    )
    return f"{record} {matchup} Model {label} probability {probability:.1%}. " + " ".join(rest)
