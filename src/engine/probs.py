"""Score distributions used for moneylines, puck lines, and totals."""

from __future__ import annotations

from src.engine.devig import poisson_pmf


def _clip(value: float, low: float = 0.02, high: float = 0.92) -> float:
    return min(max(value, low), high)


def final_score_probs(home_lambda: float, away_lambda: float) -> dict[str, float]:
    """Regulation Poisson scores, with a tied game resolved by a one-goal OT/SO.

    Returned keys:
    - p_home_ml, p_away_ml: two-way moneyline including OT/SO
    - p_home_reg, p_away_reg, p_draw: three-way regulation market
    - p_home_cover_15, p_away_cover_15: -1.5 on the final score
    - p_home_plus_15, p_away_plus_15: +1.5 on the final score
    - home_goals / away_goals / total: lists indexed by goal count (0..15, tail in last bin)
    """
    n = 14
    home_pmf = poisson_pmf(home_lambda, n)
    away_pmf = poisson_pmf(away_lambda, n)
    home_goals = [0.0] * (n + 2)
    away_goals = [0.0] * (n + 2)
    total = [0.0] * (n * 2 + 3)
    p_home_reg = p_away_reg = p_draw = 0.0
    p_home_by_2 = p_away_by_2 = 0.0
    weight_home = home_lambda / (home_lambda + away_lambda)

    for home in range(n):
        for away in range(n):
            prob = home_pmf[home] * away_pmf[away]
            if home > away:
                p_home_reg += prob
                home_goals[home] += prob
                away_goals[away] += prob
                total[home + away] += prob
                if home - away >= 2:
                    p_home_by_2 += prob
            elif away > home:
                p_away_reg += prob
                home_goals[home] += prob
                away_goals[away] += prob
                total[home + away] += prob
                if away - home >= 2:
                    p_away_by_2 += prob
            else:
                p_draw += prob
                total[home + away + 1] += prob
                home_goals[home + 1] += prob * weight_home
                away_goals[away] += prob * weight_home
                home_goals[home] += prob * (1.0 - weight_home)
                away_goals[away + 1] += prob * (1.0 - weight_home)

    p_home_ml = p_home_reg + p_draw * weight_home
    p_away_ml = p_away_reg + p_draw * (1.0 - weight_home)
    # +1.5 loses only when the opponent wins by 2+.
    p_home_plus = 1.0 - p_away_by_2
    p_away_plus = 1.0 - p_home_by_2
    return {
        "p_home_ml": _clip(p_home_ml),
        "p_away_ml": _clip(p_away_ml),
        "p_home_reg": _clip(p_home_reg, 0.02, 0.85),
        "p_away_reg": _clip(p_away_reg, 0.02, 0.85),
        "p_draw": _clip(p_draw, 0.05, 0.40),
        "p_home_cover_15": _clip(p_home_by_2, 0.05, 0.75),
        "p_away_cover_15": _clip(p_away_by_2, 0.05, 0.75),
        "p_home_plus_15": _clip(p_home_plus),
        "p_away_plus_15": _clip(p_away_plus),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "total": total,
    }


def over_under_from_hist(hist: list[float], line: float) -> tuple[float, float]:
    win = 0.0
    push = 0.0
    for goals, prob in enumerate(hist):
        if goals > line + 1e-9:
            win += prob
        elif abs(goals - line) < 1e-9:
            push += prob
    return min(max(win, 0.01), 0.95), min(max(push, 0.0), 0.5)


def expected_team_goals(offense_gf: float, defense_ga: float, league_ga: float) -> float:
    blended = 0.5 * offense_gf + 0.5 * defense_ga
    if league_ga > 0:
        blended *= (defense_ga / league_ga) ** 0.15
    return min(max(blended, 1.6), 4.8)
