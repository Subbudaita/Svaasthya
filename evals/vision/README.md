# Vision golden set (Phase 1 gate)

Measures the plate agent against hand-labelled photos of real Indian meals:
**item identification F1**, **portion error**, and **latency**.

## Add photos

Put photos in `evals/vision/golden/` (git-ignored — they are your meals) and
one line per photo in `evals/vision/golden/labels.jsonl`:

```json
{"image": "001.jpg", "items": [{"name": "chapati", "food_code": "ASC096", "grams": 72}, {"name": "moong dal", "grams": 150}, {"name": "plain curd", "grams": 100}]}
```

- `name` is required; `food_code` (INDB) is optional but makes matching exact.
- `grams` is optional; weigh it if you can, otherwise leave it out — only
  labelled grams count towards portion error.
- See `labels.example.jsonl`.

## Run

```sh
make eval-vision                # needs ANTHROPIC_API_KEY; spends real tokens (~12k per photo max)
```

Writes a report to `evals/vision/reports/` and prints the summary.

## Gate (provisional — revisit after the first 60 photos)

| Metric | Gate |
|---|---|
| Item F1 | ≥ 0.80 |
| Median portion error (labelled grams) | ≤ 30% |
| p95 latency | < 15 s |

Matching: a predicted item matches a labelled one if both carry the same
`food_code`, otherwise if their names are ≥ 0.8 similar. One-to-one, greedy.
