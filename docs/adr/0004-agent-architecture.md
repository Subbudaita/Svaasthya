# ADR 0004 — The agent: LangGraph for state, the raw SDK for every model call

- **Status:** Accepted
- **Date:** 2026-10-18
- **Phase:** 3

## Context

Phase 3 adds the supervisor and seven specialists (docs/PLAN.md §5). The plan
names LangGraph for durable, checkpointed, cross-channel state. Two risks come
with it. Framework model wrappers would bypass the per-user token cap and the
determinism boundary (ADR 0001). And the checkpoint tables would hold
conversation content outside RLS.

## Decision

- **LangGraph owns state and flow only.** The graph is precheck → route →
  specialist → guard → finalize. Every node that calls a model uses the
  Anthropic SDK directly, through the same metered call that reserves tokens
  against the daily cap. No LangChain model classes.
- **Model tiering:** `claude-haiku-4-5` routes (forced, strict tool).
  `claude-sonnet-5` answers (strict retrieval tools, structured output that
  cites clause ids and days). Citations are validated deterministically:
  unknown clauses and unseen days are dropped and logged.
- **One thread per person.** `thread_id` is the user id, so Telegram and the
  PWA share one conversation.
- **Checkpoints under RLS.** The checkpointer's schema is created by our
  migration from the library's SQL, frozen at 3.2. All three thread tables
  get forced RLS on `thread_id = app.current_user_id()::text`. Each turn runs
  the saver on a fresh connection bound to that person. A test pins the
  library's migration count.
- **Prompt caching:** tools → base prompt → specialist focus → the person's
  contract and profile (`cache_control`). Volatile context (time, today,
  last three days) goes in the user turn, after the cached prefix.
- **Context engineering:** each turn gets one deterministic read across the
  four stores. Episodic: today and the last 3 days. Semantic: recent facts,
  with full-text search as a tool. Procedural: the contract. Causal: standing
  hypotheses. Older data comes only through `get_days`.
- **Safety stopgaps until Phase 5:** a deterministic inbound red-flag screen
  runs before any model and works with no API key. A deterministic output
  guard rejects diagnoses, medicine and dose changes, and calories during
  Reset, and fails closed.
- **JITAI is not an agent.** Nudges are deterministic rules
  (`data/jitai.yaml`), delivered exactly once per rule per day, capped at 2
  per week, logged even when suppressed, and scored on a proximal outcome the
  next morning.

## Consequences

- The cost cap covers chat exactly as it covers plate photos.
- Upgrading LangGraph is low-risk: state and flow only. Upgrading the
  checkpointer needs a new migration, and the test forces that.
- No streaming yet. Chat replies arrive whole, so the "< 3 s first token"
  target is not met; streaming is a follow-up.
- Semantic memory uses full-text search until an embedding provider (Voyage,
  1024-d) is configured; `memory_facts.embedding` is ready for it.
