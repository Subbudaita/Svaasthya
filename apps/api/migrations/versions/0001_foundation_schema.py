"""Foundation schema: memory model tables, row-level security, runtime role.

Security model (see README "Tenant isolation"):

* ``swasthya_app`` is a NOLOGIN, NOBYPASSRLS group role. The API's login role
  is a member of it; policies are granted to it and to nothing else.
* ``app.current_user_id()`` reads the transaction-local ``app.user_id``
  setting. Unset → NULL → every policy evaluates false → no rows (fail closed).
* RLS is ENABLEd *and* FORCEd on every table, so the table owner is bound too.
  Only superusers / BYPASSRLS roles escape it, and the API refuses to start
  as one.

Revision ID: 0001
Revises:
Create Date: 2026-10-18 09:34:47.998581
"""

from collections.abc import Sequence

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "swasthya_app"
MAX_ACTIVE_IF_THEN_PLANS = 2  # docs/PLAN.md §3: max 2 active implementation intentions

# Every table owned by exactly one user through ``user_id``.
TENANT_TABLES = (
    "health_profiles",
    "program_contracts",
    "daily_logs",
    "meals",
    "vitals",
    "lab_reports",
    "lab_results",
    "conversations",
    "messages",
    "memory_facts",
    "hypotheses",
    "consistency_tracks",
    "if_then_plans",
    "escalations",
    "audit_log",
    "token_usage",
)
APPEND_ONLY_TABLES = ("audit_log",)


def _prelude() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute(
        """
        CREATE FUNCTION app.current_user_id() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
            AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$
        """
    )


def _row_level_security() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA app, public TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.current_user_id() TO {APP_ROLE}")

    tables = ("users", *TENANT_TABLES)
    for table in tables:
        owner_col = "id" if table == "users" else "user_id"
        privileges = (
            "SELECT, INSERT" if table in APPEND_ONLY_TABLES else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                AS PERMISSIVE FOR ALL TO {APP_ROLE}
                USING ({owner_col} = app.current_user_id())
                WITH CHECK ({owner_col} = app.current_user_id())
            """
        )
        op.execute(f"GRANT {privileges} ON {table} TO {APP_ROLE}")

    # On Supabase, PostgREST roles get default grants on public tables. They have
    # no policies (so RLS already denies them), but revoke anyway: this API is
    # the only door to the data.
    op.execute(
        f"""
        DO $$
        DECLARE r text;
        BEGIN
            FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
                IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
                    EXECUTE format('REVOKE ALL ON %s FROM %I', '{", ".join(tables)}', r);
                END IF;
            END LOOP;
        END
        $$
        """
    )


def _if_then_cap() -> None:
    op.execute(
        f"""
        CREATE FUNCTION app.enforce_if_then_cap() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status = 'active' THEN
                -- Serialise per user so two concurrent inserts cannot both pass.
                PERFORM pg_advisory_xact_lock(hashtextextended(NEW.user_id::text, 0));
                IF (SELECT count(*) FROM if_then_plans
                     WHERE user_id = NEW.user_id AND status = 'active' AND id <> NEW.id)
                   >= {MAX_ACTIVE_IF_THEN_PLANS} THEN
                    RAISE EXCEPTION 'at most {MAX_ACTIVE_IF_THEN_PLANS} active if-then plans'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER if_then_plans_active_cap
            BEFORE INSERT OR UPDATE OF status, user_id ON if_then_plans
            FOR EACH ROW EXECUTE FUNCTION app.enforce_if_then_cap()
        """
    )


def upgrade() -> None:
    _prelude()
    _tables()
    _row_level_security()
    _if_then_cap()


def downgrade() -> None:
    _drop_tables()
    op.execute("DROP SCHEMA IF EXISTS app CASCADE")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    # The role is cluster-wide and may serve other databases; it is left in place.


def _tables() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("daily_token_cap", sa.Integer(), nullable=True),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "daily_token_cap IS NULL OR daily_token_cap > 0", name="token_cap_positive"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=True),
        sa.Column("contract_clause", sa.Text(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"], unique=False)
    op.create_table(
        "consistency_tracks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("pillar", sa.Text(), nullable=False),
        sa.Column("current_run_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("longest_run_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("identity_statement", sa.Text(), nullable=True),
        sa.Column("last_counted_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "current_run_days >= 0 AND longest_run_days >= current_run_days",
            name="run_lengths_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "pillar", name="consistency_tracks_user_pillar_key"),
    )
    op.create_index(
        op.f("ix_consistency_tracks_user_id"), "consistency_tracks", ["user_id"], unique=False
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="conversations_id_user_key"),
    )
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_table(
        "daily_logs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("first_bite_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_bite_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sleep_minutes", sa.Integer(), nullable=True),
        sa.Column("steps", sa.Integer(), nullable=True),
        sa.Column("water_ml", sa.Integer(), nullable=True),
        sa.Column("energy", sa.SmallInteger(), nullable=True),
        sa.Column(
            "gut_symptoms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("energy IS NULL OR energy BETWEEN 1 AND 5", name="energy_scale"),
        sa.CheckConstraint("sleep_minutes IS NULL OR sleep_minutes >= 0", name="sleep_nonneg"),
        sa.CheckConstraint("steps IS NULL OR steps >= 0", name="steps_nonneg"),
        sa.CheckConstraint("water_ml IS NULL OR water_ml >= 0", name="water_nonneg"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "log_date", name="daily_logs_user_date_key"),
    )
    op.create_index(op.f("ix_daily_logs_user_id"), "daily_logs", ["user_id"], unique=False)
    op.create_table(
        "health_profiles",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("sex", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("height_cm", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column(
            "conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "medications",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("medication_context", sa.Text(), nullable=True),
        sa.Column(
            "contraindications",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fasting_days",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "food_identity",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("height_cm IS NULL OR height_cm > 0", name="height_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("mechanism", sa.Text(), nullable=True),
        sa.Column("prediction", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="proposed", nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "proposed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'supported', 'confirmed', 'refuted')", name="status_valid"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hypotheses_user_id"), "hypotheses", ["user_id"], unique=False)
    op.create_table(
        "if_then_plans",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("cue_event", sa.Text(), nullable=False),
        sa.Column("response_action", sa.Text(), nullable=False),
        sa.Column("anchor_routine", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("execution_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "success_rate",
            sa.Numeric(precision=5, scale=4),
            sa.Computed(
                "CASE WHEN execution_count > 0 "
                "THEN round(success_count::numeric / execution_count, 4) END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'paused', 'retired')", name="status_valid"),
        sa.CheckConstraint(
            "execution_count >= 0 AND success_count >= 0 AND success_count <= execution_count",
            name="counts_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_if_then_plans_user_id"), "if_then_plans", ["user_id"], unique=False)
    op.create_table(
        "lab_reports",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("collected_on", sa.Date(), nullable=True),
        sa.Column("lab_name", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="lab_reports_id_user_key"),
    )
    op.create_index(op.f("ix_lab_reports_user_id"), "lab_reports", ["user_id"], unique=False)
    op.create_table(
        "meals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("log_method", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("photo_path", sa.Text(), nullable=True),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("nutrients", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("repeat_of_meal_id", sa.UUID(), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "log_method IN ('photo', 'voice', 'quick_pick', 'text', 'repeat')",
            name="log_method_valid",
        ),
        sa.ForeignKeyConstraint(
            ["repeat_of_meal_id", "user_id"],
            ["meals.id", "meals.user_id"],
            name="meals_repeat_of_fkey",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="meals_id_user_key"),
    )
    op.create_index(op.f("ix_meals_user_id"), "meals", ["user_id"], unique=False)
    op.create_table(
        "memory_facts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.UUID(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memory_facts_user_id"), "memory_facts", ["user_id"], unique=False)
    op.create_table(
        "program_contracts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generator_version", sa.Text(), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("phase IN ('reset', 'rebuild', 'sustain')", name="phase_valid"),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "version", name="program_contracts_user_version_key"),
    )
    op.create_index(
        op.f("ix_program_contracts_user_id"), "program_contracts", ["user_id"], unique=False
    )
    op.create_table(
        "token_usage",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("tokens_used", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("tokens_used >= 0", name="tokens_nonneg"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "usage_date"),
    )
    op.create_table(
        "vitals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_vitals_user_id"), "vitals", ["user_id"], unique=False)
    op.create_table(
        "lab_results",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("analyte", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("ref_low", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("ref_high", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("ref_source", sa.Text(), nullable=True),
        sa.Column("retest_due_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "user_id"],
            ["lab_reports.id", "lab_reports.user_id"],
            name="lab_results_report_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lab_results_user_id"), "lab_results", ["user_id"], unique=False)
    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("channel IN ('pwa', 'telegram')", name="channel_valid"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="role_valid"),
        sa.ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            name="messages_conversation_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="messages_id_user_key"),
    )
    op.create_index(op.f("ix_messages_user_id"), "messages", ["user_id"], unique=False)
    op.create_table(
        "escalations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("trigger_source", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="status_valid"),
        sa.ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="escalations_message_fkey",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_escalations_user_id"), "escalations", ["user_id"], unique=False)
    # ### end Alembic commands ###


def _drop_tables() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_escalations_user_id"), table_name="escalations")
    op.drop_table("escalations")
    op.drop_index(op.f("ix_messages_user_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_lab_results_user_id"), table_name="lab_results")
    op.drop_table("lab_results")
    op.drop_index(op.f("ix_vitals_user_id"), table_name="vitals")
    op.drop_table("vitals")
    op.drop_table("token_usage")
    op.drop_index(op.f("ix_program_contracts_user_id"), table_name="program_contracts")
    op.drop_table("program_contracts")
    op.drop_index(op.f("ix_memory_facts_user_id"), table_name="memory_facts")
    op.drop_table("memory_facts")
    op.drop_index(op.f("ix_meals_user_id"), table_name="meals")
    op.drop_table("meals")
    op.drop_index(op.f("ix_lab_reports_user_id"), table_name="lab_reports")
    op.drop_table("lab_reports")
    op.drop_index(op.f("ix_if_then_plans_user_id"), table_name="if_then_plans")
    op.drop_table("if_then_plans")
    op.drop_index(op.f("ix_hypotheses_user_id"), table_name="hypotheses")
    op.drop_table("hypotheses")
    op.drop_table("health_profiles")
    op.drop_index(op.f("ix_daily_logs_user_id"), table_name="daily_logs")
    op.drop_table("daily_logs")
    op.drop_index(op.f("ix_conversations_user_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(op.f("ix_consistency_tracks_user_id"), table_name="consistency_tracks")
    op.drop_table("consistency_tracks")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("users")
    # ### end Alembic commands ###
