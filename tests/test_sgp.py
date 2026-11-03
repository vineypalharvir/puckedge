from src.engine.sgp import ParlayFilter, describe_ticket, generate_valid_parlays, sample_matchup


def test_sgp_prunes_conflicts_and_prices_the_ticket():
    tickets = generate_valid_parlays(sample_matchup(), ParlayFilter(min_legs=2, max_legs=3, result_limit=40))
    assert tickets
    for ticket in tickets:
        ids = {leg.id for leg in ticket.legs}
        assert not {"ml-h", "ml-a"} <= ids
        assert not {"pts", "pts-alt"} <= ids
        assert not {"tot-o", "tot-u"} <= ids
        assert not {"ml-h", "sp-h"} <= ids
        assert not {"sp-h", "sp-h2"} <= ids
        assert ticket.raw_decimal > 1
        assert 0 < ticket.hit_prob <= 1
        assert ticket.adjusted_decimal > 1
        assert describe_ticket(ticket)
    anchored = generate_valid_parlays(
        sample_matchup(),
        ParlayFilter(min_legs=2, max_legs=3, include_leg_ids={"pts"}, result_limit=20),
    )
    assert anchored
    assert all(any(leg.id == "pts" for leg in ticket.legs) for ticket in anchored)
