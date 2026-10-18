# Swasthya

An agentic personal health coach that explains *why* progress is or isn't
happening. Condition-aware, India-first, referral-first. The full requirements
and architecture are in [`docs/PLAN.md`](docs/PLAN.md). Read it first.

**Status: Phase 0 (Foundation).** Schema with row-level security, an
authenticated API skeleton, a per-user daily token cap, and an empty Today
screen. Phase 0 has no agent, logging, or real UI yet.

## Repository layout

```
apps/
  api/          FastAPI + Pydantic v2 service, Alembic migrations, pytest suite
  web/          Next.js 15 + TypeScript + Tailwind shell (empty Today screen)
packages/
  shared/       Model IDs, data-file access          (Python package)
  engines/      Deterministic engines, no LLM calls  (Python package)
  agents/       LangGraph supervisor + specialists   (Phase 3, empty)
  ingestion/    Vision, labs, voice, manual          (Phase 1+, empty)
  memory/       Retrieval over the four stores       (Phase 3, empty)
data/           Global reference data: thresholds, tables, templates
evals/          Golden sets and adversarial suites   (Phase 1+, empty)
infra/          Database role setup, compose hooks
docs/           PLAN.md, ADRs
tests/          Repo-wide guards
```

## Local setup

You need **Docker**, **[uv](https://docs.astral.sh/uv/)**, and **Node.js 20.9+**.
uv installs Python 3.12 for you if it isn't present. Python 3.13+ is not
supported because some dependency wheels aren't available for it yet.

**1. Install dependencies**

```sh
uv sync                     # Python 3.12 venv with the api, shared and engines packages
(cd apps/web && npm ci)     # web dependencies
```

**2. Run the tests.** You need Docker and nothing else: no accounts, no `.env`.

```sh
uv run pytest
```

The suite starts a throwaway `pgvector/pgvector:pg16` container, migrates it to
head, and connects as a non-superuser role so that RLS is genuinely in force.
To use a database you already have instead, set `TEST_DATABASE_URL` to an
**empty** admin database.

**3. Create your `.env`**

```sh
cp .env.example .env
```

Choose values for `POSTGRES_PASSWORD` and `SWASTHYA_API_DB_PASSWORD`, then put
them into the two connection strings:

```sh
DATABASE_URL=postgresql+psycopg://swasthya_api:<SWASTHYA_API_DB_PASSWORD>@localhost:5432/swasthya
MIGRATIONS_DATABASE_URL=postgresql+psycopg://postgres:<POSTGRES_PASSWORD>@localhost:5432/swasthya
```

**4. Start Postgres and migrate**

```sh
docker compose up -d --wait db
(cd apps/api && uv run --env-file ../../.env alembic upgrade head)
```

On first boot the container creates the `swasthya_api` login role
(`infra/db/create_roles.sql`). Migrations run as the schema owner. The API runs
as `swasthya_api`.

**5. Run the API**

```sh
uv run --env-file .env uvicorn --factory swasthya_api.main:create_app --reload
curl localhost:8000/health/ready     # {"status":"ready",...}
```

Authenticated routes (`/v1/me`, `/v1/me/usage`) need a Supabase access token.
Until `SUPABASE_URL` or `SUPABASE_JWT_SECRET` is set, they return **503**: the
API fails closed and never trusts an unverified token.

**6. Run the web shell**

```sh
(cd apps/web && npm run dev)         # http://localhost:3000
```

`make install | db | migrate | api | web | test | lint` wraps the same commands.

## Connecting Supabase (needs an account)

Phase 0 is complete and testable without Supabase. To use a real project:

1. Create a project. In **Authentication → Sign In / Providers**, turn off
   public sign-ups. Then invite users from **Authentication → Users**. That
   makes signup invite-gated.
2. In the SQL editor, run `infra/db/create_roles.sql` with a strong password
   for `swasthya_api`.
3. Set `MIGRATIONS_DATABASE_URL` to the project's `postgres` connection string
   and `DATABASE_URL` to the same host with the `swasthya_api` user. Then run
   `alembic upgrade head`.
4. Set `SUPABASE_URL`. Tokens are then verified against the project's JWKS.
   Set `SUPABASE_JWT_SECRET` only for projects still on the legacy HS256
   secret.

## Tenant isolation

Isolation is enforced **by Postgres**, not by application code:

- Every table has RLS **enabled and forced**, with a single `tenant_isolation`
  policy for the `swasthya_app` role: `user_id = app.current_user_id()`. Forced
  RLS binds the table owner too.
- Each request runs in one transaction that starts with
  `set_config('app.user_id', <JWT sub>, true)`. The setting is
  transaction-local, so a pooled connection can't carry one user into the next
  request. If the setting is missing, the session sees zero rows.
- Child rows reference `(parent_id, user_id)`, not just `parent_id`. A plain
  foreign-key check bypasses RLS and would let a user attach a row to someone
  else's parent.
- `audit_log` is append-only: the app role has no `UPDATE`/`DELETE` privilege.
- The API refuses to start if its database role is a superuser or `BYPASSRLS`.

`apps/api/tests/test_rls.py` proves all of this by talking to the database
directly as the app role and attempting cross-user reads, writes, updates,
deletes, and foreign-key attachment. Every attempt fails. The suite also fails
if a new table is added without forced RLS and a policy.

## Daily token cap

`TokenBudget` (`apps/api/swasthya_api/budget.py`) enforces a hard per-user
daily cap that resets at the user's local midnight:

- Every model call **reserves** its worst case before calling, then **settles**
  to the actual usage.
- The reservation is a single conditional upsert, so the check and the
  increment happen atomically in Postgres. A test fires 50 parallel calls at
  the cap and exactly the number that fit succeed.
- A call that crashes keeps its full reservation, so a crash loop cannot escape
  the cap.
- An exhausted budget returns HTTP **429**. The cap comes from
  `DAILY_TOKEN_CAP`, and `users.daily_token_cap` can override it per user.

## Principles encoded in Phase 0

- **No user-specific values in code.** Personal facts are rows written by
  onboarding. A tripwire test (`tests/test_no_personal_values_in_code.py`)
  scans the code for the design persona's details.
- **South Asian thresholds, from `data/`.** The cutoffs are BMI ≥ 23 for
  overweight, ≥ 25 for obese, and waist ≥ 90 cm for men, in
  `data/anthropometry/south_asian.yaml`. Code reads the file and never restates
  the numbers.
- **The determinism boundary.** The LLM perceives and code computes. See
  [ADR 0001](docs/adr/0001-determinism-boundary.md).
- **Exact model IDs:** `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`
  (`packages/shared/swasthya_shared/models.py`, pinned by a test).
