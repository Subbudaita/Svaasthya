# infra/

- `db/create_roles.sql` — creates the RLS-bound runtime roles. Run once per cluster.
- `db/init/` — Docker entrypoint hooks used by the root `compose.yaml`.

Deploy targets (Railway/Fly, Vercel, Supabase) are configured when Phase 0 is
deployed; they need accounts and are not committed here.
