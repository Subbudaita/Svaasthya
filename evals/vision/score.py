"""Scoring for the vision golden set. Deterministic; unit-tested."""

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

NAME_MATCH = 0.8


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", name.lower()).strip()


def _similar(a: dict[str, Any], b: dict[str, Any]) -> float:
    if a.get("food_code") and b.get("food_code"):
        return 1.0 if a["food_code"] == b["food_code"] else 0.0
    return SequenceMatcher(None, _norm(a["name"]), _norm(b["name"])).ratio()


def match(predicted: Sequence[dict], labelled: Sequence[dict]) -> list[tuple[int, int]]:
    """Greedy one-to-one matching by similarity, best pairs first."""
    pairs = sorted(
        ((_similar(p, t), i, j) for i, p in enumerate(predicted) for j, t in enumerate(labelled)),
        reverse=True,
    )
    used_p, used_t, out = set(), set(), []
    for score, i, j in pairs:
        if score < NAME_MATCH or i in used_p or j in used_t:
            continue
        used_p.add(i)
        used_t.add(j)
        out.append((i, j))
    return out


@dataclass(frozen=True)
class PhotoScore:
    tp: int
    fp: int
    fn: int
    portion_errors: tuple[float, ...]


def score_photo(predicted: Sequence[dict], labelled: Sequence[dict]) -> PhotoScore:
    pairs = match(predicted, labelled)
    errors = []
    for i, j in pairs:
        true_g, pred_g = labelled[j].get("grams"), predicted[i].get("grams")
        if true_g and pred_g is not None:
            errors.append(abs(pred_g - true_g) / true_g)
    return PhotoScore(
        tp=len(pairs),
        fp=len(predicted) - len(pairs),
        fn=len(labelled) - len(pairs),
        portion_errors=tuple(errors),
    )


def summarise(scores: Sequence[PhotoScore], latencies_ms: Sequence[int]) -> dict[str, Any]:
    tp = sum(s.tp for s in scores)
    fp = sum(s.fp for s in scores)
    fn = sum(s.fn for s in scores)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    errors = [e for s in scores for e in s.portion_errors]
    lat = sorted(latencies_ms)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else None
    return {
        "photos": len(scores),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "median_portion_error": round(statistics.median(errors), 3) if errors else None,
        "portion_pairs": len(errors),
        "latency_p50_ms": int(statistics.median(lat)) if lat else None,
        "latency_p95_ms": p95,
    }


GATE = {"f1": 0.80, "median_portion_error": 0.30, "latency_p95_ms": 15_000}


def gate(summary: dict[str, Any]) -> dict[str, bool]:
    mpe = summary["median_portion_error"]
    return {
        "f1": summary["f1"] >= GATE["f1"],
        "median_portion_error": mpe is not None and mpe <= GATE["median_portion_error"],
        "latency_p95_ms": summary["latency_p95_ms"] is not None
        and summary["latency_p95_ms"] < GATE["latency_p95_ms"],
    }
