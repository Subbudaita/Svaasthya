-- Runtime roles for Swasthya. Run ONCE per cluster, as an admin, before the
-- API starts. Locally, compose.yaml runs this automatically on first boot.
-- On Supabase, run it in the SQL editor with your own password.
--
--   swasthya_app  NOLOGIN group role. RLS policies are granted to it.
--                 (The first migration also creates it if missing.)
--   swasthya_api  LOGIN role the API connects as. Member of swasthya_app.
--                 Never superuser, never BYPASSRLS — the API refuses to start otherwise.
--
-- psql variable: api_password   e.g.  psql -v api_password="'...'" -f create_roles.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'swasthya_app') THEN
        CREATE ROLE swasthya_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'swasthya_api') THEN
        CREATE ROLE swasthya_api LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

ALTER ROLE swasthya_api WITH PASSWORD :api_password;
GRANT swasthya_app TO swasthya_api;
