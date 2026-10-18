from swasthya_shared.data import pillar_keys, pillars
from swasthya_shared.models import ModelTier


def test_model_ids_are_exact():
    # docs/PLAN.md §6: exact IDs, no date suffixes, no older substitutes.
    assert ModelTier.OPUS == "claude-opus-5"
    assert ModelTier.SONNET == "claude-sonnet-5"
    assert ModelTier.HAIKU == "claude-haiku-4-5"


def test_six_independent_pillars():
    keys = pillar_keys()
    assert len(pillars()) == 6
    assert len(keys) == 6
