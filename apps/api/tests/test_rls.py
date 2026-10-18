"""Tenant isolation is enforced by Postgres, not by application code.

Every test here talks to the database *directly* as the RLS-bound role — no
API, no ORM, no Python-side filtering — so a pass means the database itself
refused. If someone deletes every ``WHERE user_id = ...`` in the codebase,
these still hold.
"""

import uuid

import psycopg
import pytest
from psycopg import errors
from swasthya_api.db.models import TENANT_TABLES

# One minimal row per tenant table: (table, columns-and-values beyond user_id).
SAMPLE_ROWS: dict[str, dict[str, object]] = {
    "health_profiles": {"medication_context": "sample"},
    "program_contracts": {
        "version": 1,
        "phase": "reset",
        "body": '{"clauses": []}',
        "generator_version": "test",
    },
    "daily_logs": {"log_date": "2026-10-01", "energy": 3},
    "meals": {"eaten_at": "2026-10-01T08:00:00+05:30", "log_method": "text"},
    "vitals": {
        "measured_at": "2026-10-01T07:00:00+05:30",
        "kind": "weight",
        "value": 70,
        "unit": "kg",
    },
    "lab_reports": {"lab_name": "sample lab"},
    "conversations": {"title": "sample"},
    "memory_facts": {"category": "preference", "fact": "sample fact", "source": "test"},
    "hypotheses": {"statement": "sample", "prediction": "sample prediction"},
    "consistency_tracks": {"pillar": "sleep"},
    "if_then_plans": {"cue_event": "after lunch", "response_action": "walk 10 min"},
    "escalations": {"trigger_source": "test", "category": "test"},
    "audit_log": {"actor": "test", "action": "test"},
    "token_usage": {"usage_date": "2026-10-01", "tokens_used": 10},
    "channel_identities": {"channel": "telegram", "external_id": "__unique__"},
    "channel_link_codes": {
        "channel": "telegram",
        "code_hash": "__unique__",
        "expires_at": "2099-01-01T00:00:00Z",
    },
}


def _insert(conn: psycopg.Connection, table: str, user_id: uuid.UUID) -> None:
    values = {"user_id": user_id, **SAMPLE_ROWS[table]}
    values = {k: (uuid.uuid4().hex if v == "__unique__" else v) for k, v in values.items()}
    cols = ", ".join(values)
    params = ", ".join(["%s"] * len(values))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({params})", list(values.values()))


def _count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_sample_rows_cover_every_simple_tenant_table():
    # Child tables (FK to another tenant row) are covered by dedicated tests below.
    children = {"lab_results", "messages"}
    assert set(SAMPLE_ROWS) | children == set(TENANT_TABLES)


def test_every_table_has_forced_rls_and_a_policy(admin_conn):
    rows = admin_conn.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
        """
    ).fetchall()
    tables = {r[0]: r[1:] for r in rows}
    assert set(tables) == {"users", *TENANT_TABLES}, "a table was added without RLS review"
    for name, (enabled, forced, policies) in tables.items():
        assert enabled, f"{name}: RLS not enabled"
        assert forced, f"{name}: RLS not forced (owner would bypass it)"
        assert policies >= 1, f"{name}: no policy"


def test_app_role_cannot_bypass_rls(as_user):
    with as_user(None) as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert row == (False, False)


@pytest.mark.parametrize("table", sorted(SAMPLE_ROWS))
def test_cross_user_read_returns_nothing(table, make_user, as_user):
    alice, bob = make_user(), make_user()
    with as_user(alice) as conn:
        _insert(conn, table, alice)
        assert _count(conn, table) == 1  # Alice sees her own row.

    with as_user(bob) as conn:
        assert _count(conn, table) == 0  # Bob does not — even with no WHERE clause.
        explicit = conn.execute(
            f"SELECT count(*) FROM {table} WHERE user_id = %s", (alice,)
        ).fetchone()[0]
        assert explicit == 0  # ...and not by asking for her id directly.


@pytest.mark.parametrize("table", sorted(SAMPLE_ROWS))
def test_cross_user_write_is_rejected(table, make_user, as_user):
    alice, bob = make_user(), make_user()
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(bob) as conn:
            _insert(conn, table, alice)  # Bob tries to write into Alice's data.


@pytest.mark.parametrize("table", sorted(set(SAMPLE_ROWS) - {"audit_log"}))
def test_cross_user_update_and_delete_touch_nothing(table, make_user, as_user, admin_conn):
    alice, bob = make_user(), make_user()
    with as_user(alice) as conn:
        _insert(conn, table, alice)

    with as_user(bob) as conn:
        assert conn.execute(f"UPDATE {table} SET user_id = user_id").rowcount == 0
        assert conn.execute(f"DELETE FROM {table}").rowcount == 0

    assert admin_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_users_table_is_isolated(make_user, as_user):
    alice, bob = make_user(), make_user()
    with as_user(bob) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM users").fetchall()]
    assert ids == [bob]
    assert alice not in ids


def test_no_identity_means_no_rows(make_user, as_user):
    alice = make_user()
    with as_user(alice) as conn:
        _insert(conn, "memory_facts", alice)
    with as_user(None) as conn:
        assert _count(conn, "memory_facts") == 0
        assert _count(conn, "users") == 0


def test_identity_does_not_leak_across_transactions(make_user, app_url):
    """set_config(..., true) is transaction-local: a pooled connection forgets it."""
    alice = make_user()
    with psycopg.connect(app_url.replace("+psycopg", ""), autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.user_id', %s, true)", (str(alice),))
            assert _count(conn, "users") == 1
        with conn.transaction():
            assert _count(conn, "users") == 0


def test_cannot_attach_child_to_another_users_parent(make_user, as_user):
    """A plain FK check bypasses RLS; composite (id, user_id) FKs close that hole."""
    alice, bob = make_user(), make_user()
    with as_user(alice) as conn:
        conv_id = conn.execute(
            "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (alice,)
        ).fetchone()[0]
        report_id = conn.execute(
            "INSERT INTO lab_reports (user_id) VALUES (%s) RETURNING id", (alice,)
        ).fetchone()[0]

    with pytest.raises(errors.ForeignKeyViolation):
        with as_user(bob) as conn:
            conn.execute(
                "INSERT INTO messages (user_id, conversation_id, channel, role, content) "
                "VALUES (%s, %s, 'pwa', 'user', 'hi')",
                (bob, conv_id),
            )
    with pytest.raises(errors.ForeignKeyViolation):
        with as_user(bob) as conn:
            conn.execute(
                "INSERT INTO lab_results (user_id, report_id, analyte, value, unit) "
                "VALUES (%s, %s, 'tsh', 2.1, 'mIU/L')",
                (bob, report_id),
            )


def test_child_rows_are_isolated(make_user, as_user):
    alice, bob = make_user(), make_user()
    with as_user(alice) as conn:
        conv_id = conn.execute(
            "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (alice,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO messages (user_id, conversation_id, channel, role, content) "
            "VALUES (%s, %s, 'telegram', 'user', 'private')",
            (alice, conv_id),
        )
    with as_user(bob) as conn:
        assert _count(conn, "messages") == 0


def test_audit_log_is_append_only(make_user, as_user):
    alice = make_user()
    with as_user(alice) as conn:
        _insert(conn, "audit_log", alice)
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(alice) as conn:
            conn.execute("UPDATE audit_log SET action = 'tampered'")
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(alice) as conn:
            conn.execute("DELETE FROM audit_log")


def test_cross_user_read_over_http(client, make_token):
    """End to end: Bob's token cannot surface Alice's data through the API."""
    alice, bob = uuid.uuid4(), uuid.uuid4()
    a = client.get("/v1/me", headers={"Authorization": f"Bearer {make_token(alice)}"})
    b = client.get("/v1/me", headers={"Authorization": f"Bearer {make_token(bob)}"})
    assert a.status_code == b.status_code == 200
    assert a.json()["id"] == str(alice)
    assert b.json()["id"] == str(bob)


def test_channel_lookup_only_through_definer_functions(make_user, as_user):
    """The bot resolves Telegram ids with no user context, via one narrow function."""
    from swasthya_api.channels import hash_code

    alice, bob = make_user(), make_user()
    with as_user(alice) as conn:
        conn.execute(
            "INSERT INTO channel_link_codes (user_id, channel, code_hash, expires_at) "
            "VALUES (%s, 'telegram', %s, now() + interval '10 minutes')",
            (alice, hash_code("abcd2345")),
        )
    with as_user(None) as conn:
        # No identity: the tables themselves are invisible...
        assert _count(conn, "channel_identities") == 0
        # ...but the redeem function links exactly the code's owner, once.
        assert (
            conn.execute("SELECT app.redeem_link_code('ABCD2345', 'telegram', '777')").fetchone()[0]
            == alice
        )
        assert (
            conn.execute("SELECT app.redeem_link_code('ABCD2345', 'telegram', '888')").fetchone()[0]
            is None
        )  # single use
        assert (
            conn.execute("SELECT app.resolve_channel_user('telegram', '777')").fetchone()[0]
            == alice
        )
        assert (
            conn.execute("SELECT app.resolve_channel_user('telegram', '999')").fetchone()[0] is None
        )
    with as_user(bob) as conn:
        assert _count(conn, "channel_identities") == 0  # Bob can't see Alice's link


def test_expired_link_code_is_refused(make_user, as_user):
    from swasthya_api.channels import hash_code

    alice = make_user()
    with as_user(alice) as conn:
        conn.execute(
            "INSERT INTO channel_link_codes (user_id, channel, code_hash, expires_at) "
            "VALUES (%s, 'telegram', %s, now() - interval '1 minute')",
            (alice, hash_code("OLDCODE9")),
        )
    with as_user(None) as conn:
        assert (
            conn.execute("SELECT app.redeem_link_code('OLDCODE9', 'telegram', '1')").fetchone()[0]
            is None
        )
