"""Migrations run clean, match the models, and enforce schema-level invariants."""

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg import errors
from sqlalchemy import create_engine
from swasthya_api.db.models import Base


def test_downgrade_then_upgrade_is_clean(alembic_cfg, app_url, admin_url):
    command.downgrade(alembic_cfg, "base")
    with psycopg.connect(admin_url.replace("+psycopg", ""), autocommit=True) as conn:
        left = conn.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
        ).fetchone()[0]
    assert left == 0
    command.upgrade(alembic_cfg, "head")


def test_models_match_migrations(admin_url, app_url):
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], f"models and migrations disagree: {diff}"


def test_health_profile_has_medication_context(admin_conn, app_url):
    col = admin_conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'health_profiles' AND column_name = 'medication_context'"
    ).fetchone()
    assert col == ("text",)


def test_memory_facts_embedding_is_pgvector(admin_conn, app_url):
    col = admin_conn.execute(
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_name = 'memory_facts' AND column_name = 'embedding'"
    ).fetchone()
    assert col == ("vector",)


def _plan(conn, user_id, status="active"):
    return conn.execute(
        "INSERT INTO if_then_plans (user_id, cue_event, response_action, status) "
        "VALUES (%s, 'cue', 'action', %s) RETURNING id",
        (user_id, status),
    ).fetchone()[0]


def test_at_most_two_active_if_then_plans(make_user, as_user):
    user = make_user()
    with as_user(user) as conn:
        _plan(conn, user)
        _plan(conn, user)
        retired = _plan(conn, user, status="retired")  # inactive plans don't count
    with pytest.raises(errors.CheckViolation):
        with as_user(user) as conn:
            _plan(conn, user)
    with pytest.raises(errors.CheckViolation):
        with as_user(user) as conn:
            conn.execute("UPDATE if_then_plans SET status = 'active' WHERE id = %s", (retired,))


def test_if_then_success_rate_is_derived(make_user, as_user):
    user = make_user()
    with as_user(user) as conn:
        plan = _plan(conn, user)
        conn.execute(
            "UPDATE if_then_plans SET execution_count = 4, success_count = 3 WHERE id = %s",
            (plan,),
        )
        rate = conn.execute(
            "SELECT success_rate FROM if_then_plans WHERE id = %s", (plan,)
        ).fetchone()[0]
    assert float(rate) == 0.75


def test_program_contract_versions_unique_per_user(make_user, as_user):
    user = make_user()
    sql = (
        "INSERT INTO program_contracts (user_id, version, phase, body, generator_version) "
        "VALUES (%s, 1, 'reset', '{}', 'test')"
    )
    with as_user(user) as conn:
        conn.execute(sql, (user,))
    with pytest.raises(errors.UniqueViolation):
        with as_user(user) as conn:
            conn.execute(sql, (user,))


def test_hypothesis_status_lifecycle_values(make_user, as_user):
    user = make_user()
    with pytest.raises(errors.CheckViolation):
        with as_user(user) as conn:
            conn.execute(
                "INSERT INTO hypotheses (user_id, statement, prediction, status) "
                "VALUES (%s, 's', 'p', 'probably')",
                (user,),
            )
