# ADR 0003 — Rituals: an idempotent minute tick, not a job per user

- **Status:** Accepted
- **Date:** 2026-10-18
- **Phase:** 2

## Context

The plan names APScheduler with a Postgres jobstore. Its requirements are
stricter than that choice: rituals *must* fire, each in the person's own time
zone, and a missed ritual is a logged incident. A per-user job store has three
problems:
- Every timezone or ritual-time change means rescheduling jobs.
- A worker that is down at 07:00 silently skips the job, or fires it late at
  an unknown time.
- The job store holds health-adjacent state outside RLS.

## Decision

- APScheduler runs **one** interval job, every minute, in the worker process.
  It needs no job store.
- Each tick asks Postgres for ritual candidates through
  `app.ritual_candidates()`, a `SECURITY DEFINER` function. It returns only
  ids, time zones, ritual times, program start dates and the Telegram id. The
  tick decides in Python, in local time, what is due.
- Delivery is exactly-once by construction. The tick first *claims* the
  ritual by inserting a `ritual_runs` row (unique on user, ritual, local
  date), and only then sends. A second tick, or a second worker, finds the row
  and does nothing.
- A ritual not delivered within `catch_up_minutes` (data/rituals/schedule.yaml)
  is recorded as `missed` and logged at error level. That is the incident. It
  is never sent late at night.
- The morning brief closes out the previous day first: it scores six tracks,
  applies grace days, and pays rewards. That makes the brief the single
  moment when grace days and recognitions are revealed.

## Consequences

- Changing a ritual time is one column update. Time zones are handled
  per tick, so daylight-saving changes need no special handling.
- At most one minute of latency, which is fine for a 07:00 brief.
- The schedule state is queryable SQL (`ritual_runs`), which also serves as
  the availability metric: share of `sent` vs `missed`.
- Upgrading to Temporal later only means replacing the tick. Delivery logic
  and idempotency stay in Postgres.
