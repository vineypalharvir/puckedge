# PuckEdge Parlay Engine

Local NHL analytics app. It prices a Bet365-compatible board, builds risk-tiered parlays, and grades every saved ticket after the games end.

Run it with:

```text
.venv\Scripts\python.exe -m streamlit run app.py
```

Set `ODDS_API_KEY` in `.env` for live Bet365 / consensus prices. With no key, the app still runs on official NHL stats plus a synthetic Bet365 board.

## Stack

- Python 3.11+
- Streamlit dashboard (`app.py`)
- httpx for the NHL API and The Odds API
- Pydantic v2 models
- SQLite + SQLAlchemy for parlays, legs, and bankroll transactions
- APScheduler settles pending tickets every 15 minutes, and the ledger has a manual settle button
- Offline mock slate when the NHL feed cannot be reached

## Data

NHL, no key required:

- `GET https://api-web.nhle.com/v1/schedule/{YYYY-MM-DD}`
- `GET https://api-web.nhle.com/v1/gamecenter/{gameId}/boxscore`
- `GET https://api-web.nhle.com/v1/standings/now`
- `GET https://api-web.nhle.com/v1/club-stats/{team}/{season}/2`
- `GET https://api-web.nhle.com/v1/player/{playerId}/game-log/{season}/2`

Odds, optional key:

- `GET https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds`
- Book priority: bet365, draftkings, fanduel, pinnacle
- Markets: moneyline, puck line, totals, player points, shots, anytime goal
- Missing markets are filled by the synthetic board

## Model

- American odds convert to implied probability with the standard positive and negative formulas.
- Two-way markets are devigged with the multiplicative method. Power devig is available in `src/engine/devig.py`.
- Team rates blend 60% of the last 10 games and 40% of the season.
- Opponent shots, goals against, and penalty kill move player and team totals.
- A team on the second night of a road back-to-back takes a 4.5% win-expectancy cut.
- Expected value is `(win probability × decimal odds) - loss probability`, so a push is not a loss.
- Legs should clear EV >= 0. If a tier cannot fill, the probability floor loosens and the card says so.

## Risk tiers

| Profile | Legs | Parlay odds | What it looks for | Min leg probability | Stake |
| --- | --- | --- | --- | --- | --- |
| Conservative | 2 | -120 to +160 | +1.5, team over 1.5, star over 2.5 shots | 68% | 2–3% of bankroll, quarter Kelly |
| Balanced | 3–4 | +250 to +500 | Favorite moneylines, totals, shots and points | 56% | 1–1.5%, eighth Kelly |
| Aggressive | 4–6 | +600 to +1500 | -1.5, dogs, anytime goals, team over 3.5 | 42% | 0.5% |
| Moonshot | 6–8 | +2000 to +10000 | Goal scorers and plus-money props | 25% | 0.1–0.25%, $10 cap |

A wager above 5% of bankroll on Aggressive or Moonshot raises a Kelly warning.

## Correlation

Boost: team -1.5 with that team's over 3.5; one teammate's points with another's shots; goalie saves with an opponent shot prop.

Reject: opposing moneylines, two sides of the same market, a -1.5 with the opponent's moneyline, a team's moneyline stacked with its own puck line, and a game under 5.5 with two or more anytime scorers in that game.

Same-game prices are multiplied as independent. The card says a book may shorten that ticket.

## Ledger

Saved tickets are paper or real. Real stakes leave the bankroll immediately and winning payouts return at settlement. Pending tickets re-check NHL box scores. One loss loses the ticket. All remaining legs winning, after pushes and voids are removed, wins at the reduced price. A total that lands on the number pushes.

Analytics show wagered, net, ROI, hit rate, entry edge, real versus paper, streaks, a bankroll curve, and hit rate by market. Closing-line value is stored only when a later price exists.

This is a local research and tracking tool, not a sportsbook and not betting advice.
