import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score import gate, match, score_photo, summarise  # noqa: E402


def test_code_match_beats_name():
    pred = [{"name": "roti", "food_code": "ASC096"}]
    true = [{"name": "chapati", "food_code": "ASC096"}]
    assert match(pred, true) == [(0, 0)]


def test_one_to_one_and_portion_error():
    pred = [{"name": "chapati", "grams": 80}, {"name": "chapati", "grams": 40}, {"name": "pickle"}]
    true = [{"name": "chapati", "grams": 100}, {"name": "dal", "grams": 150}]
    s = score_photo(pred, true)
    assert (s.tp, s.fp, s.fn) == (1, 2, 1)
    assert s.portion_errors in ((0.2,), (0.6,))


def test_summary_and_gate():
    perfect = score_photo([{"name": "idli", "grams": 50}], [{"name": "idli", "grams": 50}])
    summary = summarise([perfect, perfect], [4000, 9000])
    assert summary["f1"] == 1.0 and summary["median_portion_error"] == 0.0
    assert all(gate(summary).values())
    slow = summarise([perfect], [16000])
    assert gate(slow)["latency_p95_ms"] is False
