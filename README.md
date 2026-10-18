# Swasthya

An agentic personal health coach that explains *why* progress is or isn't
happening. Condition-aware, India-first, referral-first. The full requirements
and architecture are in [`docs/PLAN.md`](docs/PLAN.md). Read it first.

**Status: Phase 3 (Agents + JITAI).** On top of Phase 2 (contract, rituals,
behaviour engine) and Phase 1 (multi-method logging). **New here? Start with
[docs/SETUP.md](docs/SETUP.md).** Photograph a plate on
Telegram and get back what's on it plus one sentence on its balance. Every
number comes from the Indian Nutrient Databank, never from the model. Built
on Phase 0: schema with row-level security, an authenticated API, and a
per-user daily token cap.

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

## Try the agent on Telegram (Phase 1)

You need an Anthropic API key and a Telegram bot token from @BotFather. You
don't need Supabase or a public URL.

```sh
# in .env: ANTHROPIC_API_KEY=...  TELEGRAM_BOT_TOKEN=...
make indb                              # convert INDB → data/nutrition/indb.json
make dev-user EMAIL=you@example.com    # prints a link code
make api                               # terminal 1 (optional for Telegram, needed for HTTP)
make telegram                          # terminal 2: the worker — bot + ritual scheduler
```

In Telegram, send your bot `/start <code>`. Then:

| You send | It does |
|---|---|
| a photo (caption optional) | identifies items and portions, replies with what's on the plate and one sentence |
| `/log 2 roti, dal, bhindi` | the same, from words |
| `/same` | repeats yesterday's meal in the current slot (no model call) |
| `/picks` | your usual meals as one-tap buttons (no model call) |
| a reply to one of its plate messages | corrects that meal, and the correction is stored as a fact |

How a plate is analysed:

1. A hand-written tool-use loop (`packages/ingestion/swasthya_ingestion/plate_agent.py`)
   runs `claude-sonnet-5`. The model can call one tool, `search_foods`, which
   does a deterministic lookup in INDB. It returns items with an INDB code, a
   portion and a role. It never returns a nutrient.
2. `packages/engines/swasthya_engines/plate.py` turns portions into grams.
   For countable units like chapati or idli it uses the table's serving
   weight; otherwise it uses the visual estimate, labelled as an estimate.
   It then computes nutrients and picks the balanced-plate sentence from
   `data/nutrition/plate_rules.yaml`.
3. Every model call reserves its worst case against the daily token cap
   before it runs.

During Reset (and before any program exists) the reply shows **no calorie
numbers**. They are stored, but appear only from Rebuild.

**Measure it** with your own photos: `evals/vision/README.md`, then
`make eval-vision`.

**Known gaps:**
- Plain staples (plain curd, milk, whole fruit) are in IFCT 2017, which NIN
  only provides on request. Until it's added they show as "not in my
  nutrition table yet" instead of being guessed.
- Voice-note logging waits for speech-to-text (Phase 6).
- Photos are stored on local disk until Supabase Storage is connected.

## Your program (Phase 2)

Send `/onboard` to the bot. It asks about 30 short questions about your
**day**: when you wake and sleep, yesterday's first and last bite, family
dinner, energy crashes, fasting days. It never asks what target you want.
The rule engine then derives everything else:

- the eating window, anchored three hours before sleep, starting from your
  observed baseline, around family dinner, and never narrower than 12 h in
  Reset;
- the contraindication gates (pregnancy, T1D, insulin/sulfonylureas,
  eating-disorder history, kidney disease, underweight, …), which remove
  eating-window advice entirely where it isn't safe;
- a **Program Contract**: every clause with *why it applies to you*.

Reply `OK` to accept, or `/onboard` to answer again. From the next day:

| When | What |
|---|---|
| 07:00 | **Brief**: kitchen hours, breakfast already decided (your own protein breakfast if you have one), yesterday's context, any grace day revealed |
| 21:30 | **Check-in**: tap ✓/✗ for five pillars, then answer exactly one question about *why*; the answer is stored as a fact |

- **Days 1–3** only observe; nothing is scored.
- Six independent consistency tracks. Each gets two grace days a month,
  applied silently and revealed the next morning.
- Your own non-food rewards are paid retroactively at 14/30/60-day runs and
  never announced in advance.
- `/time morning 06:45` and `/time evening 21:00` move the rituals.

Design notes: [ADR 0003](docs/adr/0003-ritual-scheduling.md) (exactly-once
rituals; a missed one is a logged incident).

## The coach (Phase 3)

Any message that isn't a command goes to the coach, in Telegram or via
`POST /v1/chat`. Both are **one conversation**: start on Telegram at lunch,
continue in the app at night.

- A Haiku router picks one of seven specialists: Nutrition & Gut, Movement,
  Circadian, Mind, Clinical, Adherence, Insight.
- Sonnet answers from your own data. Today and the last three days are in
  context; it calls tools for older days, stored facts, and food values from
  INDB. Each answer cites the contract clauses and days it used, and says
  how confident it is. Citations are checked, and the answer is audited.
- **Safety before Phase 5:** emergencies such as chest pain, fainting or
  self-harm halt coaching and point to 112 / Tele-MANAS 14416, with no model
  involved. An output guard blocks diagnoses, medicine or dose changes, and
  calories during Reset.
- **Nudges (JITAI):** at most two a week, only when your data predicts a
  lapse:
  - before your usual energy dip after a short night;
  - mid-morning when breakfast hasn't happened and has been slipping;
  - before the kitchen closes when late dinners have been recurring.
  Each nudge is scored the next morning on what happened after it.

Design: [ADR 0004](docs/adr/0004-agent-architecture.md).

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
