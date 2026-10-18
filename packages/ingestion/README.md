# packages/ingestion

Perception: turns a plate photo or a typed description into structured items.

- `plate_agent.py` — a hand-written Claude tool-use loop (docs/PLAN.md §10c,
  stage 1: no framework). The model sees the input, calls `search_foods`
  against the INDB table, and returns items with an INDB code, a portion and a
  role. **It never produces a nutrient number**; `swasthya_engines.plate` does.
- `images.py` — downscale/re-encode before upload (latency and cost).

Labs (Phase 5) and voice (Phase 6) land here later.
