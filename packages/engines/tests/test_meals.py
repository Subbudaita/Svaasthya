from datetime import time

import pytest
from swasthya_engines.meals import fingerprint, slot_for


@pytest.mark.parametrize(
    ("t", "slot"),
    [
        (time(7, 30), "breakfast"),
        (time(11), "lunch"),
        (time(17), "snack"),
        (time(21, 30), "dinner"),
        (time(1), "dinner"),
        (time(3, 59), "dinner"),
    ],
)
def test_slots(t, slot):
    assert slot_for(t) == slot


def test_fingerprint_ignores_order():
    a = [
        {"food_code": "A", "quantity": 2, "unit": "chapati"},
        {"name": "Curd", "quantity": 1, "unit": "katori"},
    ]
    assert fingerprint(a) == fingerprint(list(reversed(a)))
    assert fingerprint(a) != fingerprint(a[:1])
