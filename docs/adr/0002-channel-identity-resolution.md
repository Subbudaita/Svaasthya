# ADR 0002 — Resolving a Telegram account to a user without weakening RLS

- **Status:** Accepted
- **Date:** 2026-10-18
- **Phase:** 1

## Context

Every tenant table is protected by RLS keyed on `app.user_id`, which is set
from a verified Supabase JWT. A Telegram update carries no JWT, only a
Telegram user id. To know whose meal a photo is, the bot must look that id up
**before** it knows the user, so the lookup can't go through the tenant
policy.

The options were:

- a `BYPASSRLS` service role for the bot;
- trusting an id passed in the message;
- a narrow, audited escape hatch.

## Decision

- `channel_identities` maps (channel, external id) → user. It is reachable
  only through two `SECURITY DEFINER` functions owned by the schema owner:
  - `app.resolve_channel_user(channel, external_id)` returns one user id.
  - `app.redeem_link_code(code, channel, external_id)` consumes a one-time,
    15-minute, sha256-hashed code that a *signed-in* user requested, and
    links the account.
- The runtime role can `EXECUTE` the functions but still sees only its own
  rows in both tables. Both tables keep forced RLS. The owner gets a policy on
  these two tables only, so the functions can act.
- After resolution the bot does everything else in a normal user-scoped
  transaction. RLS applies exactly as it does for HTTP requests.
- Only private 1:1 chats are handled. Group messages are dropped, so health
  data never reaches a group.

## Consequences

- No role in the system can read across tenants in general. The exception is
  a single question: "which user is Telegram id X?"
- Linking requires the user to act in two places (sign in, then send the code
  to the bot). That proves ownership of both accounts.
- Webhook mode authenticates Telegram with the secret-token header. Polling
  mode needs no inbound endpoint at all.
