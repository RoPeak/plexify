from plexify import cli


def test_year_aware_scoring_prefers_exact_year() -> None:
    exact = cli._confidence_score("Mission Impossible", "Mission Impossible", 1996, 1996)
    far = cli._confidence_score("Mission Impossible", "Mission Impossible", 1996, 2018)
    assert exact > far


def test_year_aware_scoring_penalises_large_gaps() -> None:
    near = cli._confidence_score("Dune", "Dune", 2021, 2021)
    gap = cli._confidence_score("Dune", "Dune", 2021, 1984)
    assert near > gap
