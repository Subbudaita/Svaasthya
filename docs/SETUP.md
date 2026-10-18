# Setup — from zero to talking to your coach

Everything here runs on your own computer. You don't need Supabase or a
deployment until you want other people to use it.

## A. Accounts (≈10 minutes)

1. **Anthropic API key.** console.anthropic.com → Settings → API Keys →
   Create Key. Then Billing → add about $20. Keep the key private.
2. **Telegram bot.** In Telegram, message **@BotFather** → `/newbot` → choose a
   name and a username ending in `bot`. Copy the token (`123456:ABC...`).

## B. Install (once)

3. **Docker Desktop**, and start it.
4. **uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
5. **Node 20+** (only for the web shell).

## C. Get the code and configure it

```sh
git clone https://github.com/Subbudaita/Svaasthya && cd Svaasthya
git checkout claude/confident-einstein-b2n47b
cp .env.example .env
```

Edit `.env`. The two passwords are anything you invent; reuse them in the
two connection strings:

```sh
POSTGRES_PASSWORD=pick-one
SWASTHYA_API_DB_PASSWORD=pick-another
DATABASE_URL=postgresql+psycopg://swasthya_api:pick-another@localhost:5432/swasthya
MIGRATIONS_DATABASE_URL=postgresql+psycopg://postgres:pick-one@localhost:5432/swasthya
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:ABC...
```

## D. Start it

```sh
make install
make db
make migrate
make indb                              # builds the Indian food table
make dev-user EMAIL=you@example.com    # prints your user id and a link code
make telegram                          # leave running: bot + rituals + nudges
```

## E. Use it

1. In Telegram, send your bot `/start <code>`.
2. Send `/onboard`, answer the questions, reply `OK` to the proposed program.
3. Send plate photos. Also try `/log 2 roti dal`, `/same`, `/picks`, or reply
   to a plate message to correct it.
4. **Talk to it.** Any other message goes to the coach. Try "what should I
   eat now?" or "why am I so tired today?". It answers from your own last
   three days and says how sure it is.
5. From the next morning you'll get the 07:00 brief and the 21:30 check-in.
   At most two nudges a week arrive at moments your data says matter.

From the terminal: `make ask USER_ID=<your user id> Q="why am I so tired today?"`.

## F. Optional: measure plate accuracy

Put meal photos in `evals/vision/golden/` with one line each in
`labels.jsonl` (format in `evals/vision/README.md`), then run
`make eval-vision`.

## No `make`? Run the commands directly

`make` is only a shortcut. On Windows, or a Mac without developer tools
(`xcode-select --install` adds it), run these from the repository folder
instead:

| `make …` | Same thing without make |
|---|---|
| `make install` | `uv sync --group data` then `cd apps/web` → `npm ci` → `cd ../..` |
| `make db` | `docker compose up -d --wait db` |
| `make migrate` | `cd apps/api` → `uv run --env-file ../../.env alembic upgrade head` → `cd ../..` |
| `make indb` | `git clone --depth 1 https://github.com/lindsayjaacks/indian-nutrient-databank-indb- var/indb` then `uv run --group data python scripts/import_indb.py var/indb/INDB.xlsx` |
| `make dev-user EMAIL=you@example.com` | `uv run --env-file .env python -m swasthya_api.devtools create-user --email you@example.com` |
| `make telegram` | `uv run --env-file .env python -m swasthya_api.worker` |
| `make ask USER_ID=… Q="…"` | `uv run --env-file .env python -m swasthya_api.devtools ask --user-id … "…"` |
| `make test` | `uv run pytest` |

`npm ci` ends with "2 vulnerabilities (1 moderate, 1 high)". That's a
warning, not a failure: it's PostCSS bundled inside Next.js, a build tool,
and it doesn't affect running the app.

## If something fails

| Symptom | Fix |
|---|---|
| `make: command not found` | Use the table above, or install make |
| `uv: command not found` right after installing uv | Close and reopen the terminal so it picks up the new PATH |
| `npm: command not found` | Install Node 20+ (nodejs.org), reopen the terminal |
| `make db` / `make migrate` errors | Docker isn't running |
| Bot says "isn't set up on the server" | `ANTHROPIC_API_KEY` missing, or `make indb` not run |
| Bot doesn't answer at all | `make telegram` isn't running, or the token is wrong |
| "invalid or expired" code | Codes last 15 minutes: run `make dev-user` again |

Send the error text (never your keys) and it can be fixed.
