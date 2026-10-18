"""Schema for the four memory stores (docs/PLAN.md §5) plus supporting tables.

Conventions
-----------
* Every tenant table carries ``user_id`` and is protected by an RLS policy
  (created in the migration, not here — SQLAlchemy does not model policies).
* Parent tables expose ``UNIQUE (id, user_id)`` and children reference
  ``(parent_id, user_id)``. A plain FK check bypasses RLS, so without this a
  user could attach a row to someone else's parent by guessing its id.
* Nothing user-specific has a default here. Personal values arrive via
  onboarding; global values (thresholds, tables, templates) live in ``data/``.

Memory model → tables
---------------------
* Episodic:   daily_logs, meals, vitals, lab_reports, lab_results,
              conversations, messages
* Semantic:   memory_facts (+ pgvector embedding)
* Procedural: program_contracts (versioned)
* Causal:     hypotheses
"""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

LOG_METHODS = ("photo", "voice", "quick_pick", "text", "repeat")
CHANNELS = ("pwa", "telegram")
MESSAGE_ROLES = ("user", "assistant", "system", "tool")
HYPOTHESIS_STATUSES = ("proposed", "supported", "confirmed", "refuted")
PHASES = ("reset", "rebuild", "sustain")
IF_THEN_STATUSES = ("active", "paused", "retired")
ESCALATION_STATUSES = ("open", "acknowledged", "resolved")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
    }


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=func.gen_random_uuid())


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _jsonb_list() -> Mapped[list[Any]]:
    return mapped_column(server_default=text("'[]'::jsonb"))


def _jsonb_obj() -> Mapped[dict[str, Any]]:
    return mapped_column(server_default=text("'{}'::jsonb"))


# --- Identity ---------------------------------------------------------------


class User(Base):
    """One row per Supabase Auth user. ``id`` is the JWT ``sub`` claim."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    # Overrides the global DAILY_TOKEN_CAP for this user when set.
    daily_token_cap: Mapped[int | None] = mapped_column(Integer)
    # Ritual times chosen by the person; NULL means the default in data/rituals/.
    morning_brief_at: Mapped[time | None] = mapped_column(Time)
    evening_checkin_at: Mapped[time | None] = mapped_column(Time)
    onboarded_at: Mapped[datetime | None]
    # Day 1 of the program (local date the first contract was accepted).
    program_started_on: Mapped[date | None]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (
        CheckConstraint(
            "daily_token_cap IS NULL OR daily_token_cap > 0", name="token_cap_positive"
        ),
    )


class HealthProfile(Base):
    """History, conditions and medications gathered at onboarding (F1)."""

    __tablename__ = "health_profiles"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    sex: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[date | None]
    birth_year: Mapped[int | None] = mapped_column(SmallInteger)
    diet_pattern: Mapped[str | None] = mapped_column(Text)
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    conditions: Mapped[list[Any]] = _jsonb_list()
    medications: Mapped[list[Any]] = _jsonb_list()
    # Free-text medication context from day one (e.g. "started a GLP-1 in March").
    medication_context: Mapped[str | None] = mapped_column(Text)
    contraindications: Mapped[list[Any]] = _jsonb_list()
    # Religious / customary fasting days. Respected, never scored as violations.
    fasting_days: Mapped[list[Any]] = _jsonb_list()
    food_identity: Mapped[dict[str, Any]] = _jsonb_obj()
    # Answers about the person's day the contract derives from (wake, sleep,
    # first/last bite, family dinner, work pattern, ...). Never targets.
    lifestyle: Mapped[dict[str, Any]] = _jsonb_obj()
    goal: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (
        CheckConstraint("height_cm IS NULL OR height_cm > 0", name="height_positive"),
    )


# --- Procedural memory ------------------------------------------------------


class ProgramContract(Base):
    """Versioned Program Contract (F6). New version per change; never edited in place."""

    __tablename__ = "program_contracts"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    version: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(Text)
    body: Mapped[dict[str, Any]]
    # Version of the deterministic rule engine that produced ``body``.
    generator_version: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[datetime] = _created()
    superseded_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("user_id", "version", name="program_contracts_user_version_key"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(_in("phase", PHASES), name="phase_valid"),
    )


# --- Episodic memory --------------------------------------------------------


class DailyLog(Base):
    __tablename__ = "daily_logs"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    log_date: Mapped[date]
    first_bite_at: Mapped[datetime | None]
    last_bite_at: Mapped[datetime | None]
    sleep_minutes: Mapped[int | None] = mapped_column(Integer)
    steps: Mapped[int | None] = mapped_column(Integer)
    water_ml: Mapped[int | None] = mapped_column(Integer)
    energy: Mapped[int | None] = mapped_column(SmallInteger)
    gut_symptoms: Mapped[list[Any]] = _jsonb_list()
    # The person's own check-in answers, pillar → kept it today?
    pillar_reports: Mapped[dict[str, Any]] = _jsonb_obj()
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("user_id", "log_date", name="daily_logs_user_date_key"),
        CheckConstraint("energy IS NULL OR energy BETWEEN 1 AND 5", name="energy_scale"),
        CheckConstraint("sleep_minutes IS NULL OR sleep_minutes >= 0", name="sleep_nonneg"),
        CheckConstraint("steps IS NULL OR steps >= 0", name="steps_nonneg"),
        CheckConstraint("water_ml IS NULL OR water_ml >= 0", name="water_nonneg"),
    )


class Meal(Base):
    """A logged meal. ``items`` come from perception; ``nutrients`` from tables only."""

    __tablename__ = "meals"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    eaten_at: Mapped[datetime]
    log_method: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    photo_path: Mapped[str | None] = mapped_column(Text)
    # Current items after deterministic analysis (grams, source, per-item nutrients).
    items: Mapped[list[Any]] = _jsonb_list()
    # What perception originally returned, kept unchanged when the user corrects it.
    perceived_items: Mapped[list[Any]] = _jsonb_list()
    # Deterministically computed from nutrition tables — never LLM-generated.
    nutrients: Mapped[dict[str, Any] | None]
    # Balanced-plate rule id and its sentence.
    plate_read: Mapped[dict[str, Any] | None]
    # Model, latency, token usage of the perception call(s).
    analysis_meta: Mapped[dict[str, Any]] = _jsonb_obj()
    # Channel message this meal's card was sent as, e.g. "telegram:<chat>:<msg>".
    source_ref: Mapped[str | None] = mapped_column(Text)
    repeat_of_meal_id: Mapped[uuid.UUID | None]
    corrected_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="meals_id_user_key"),
        ForeignKeyConstraint(
            ["repeat_of_meal_id", "user_id"],
            ["meals.id", "meals.user_id"],
            name="meals_repeat_of_fkey",
        ),
        CheckConstraint(_in("log_method", LOG_METHODS), name="log_method_valid"),
        Index("meals_user_eaten_at_idx", "user_id", "eaten_at"),
        Index("meals_user_source_ref_idx", "user_id", "source_ref"),
    )


class Vital(Base):
    """Point measurements: weight, waist, body-fat %, BP, ..."""

    __tablename__ = "vitals"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    measured_at: Mapped[datetime]
    kind: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    unit: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class LabReport(Base):
    __tablename__ = "lab_reports"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    collected_on: Mapped[date | None]
    lab_name: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("id", "user_id", name="lab_reports_id_user_key"),)


class LabResult(Base):
    """One analyte from one report. Reference range is snapshotted at extraction."""

    __tablename__ = "lab_results"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    report_id: Mapped[uuid.UUID]
    analyte: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    unit: Mapped[str] = mapped_column(Text)
    ref_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ref_source: Mapped[str | None] = mapped_column(Text)
    retest_due_on: Mapped[date | None]
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        ForeignKeyConstraint(
            ["report_id", "user_id"],
            ["lab_reports.id", "lab_reports.user_id"],
            ondelete="CASCADE",
            name="lab_results_report_fkey",
        ),
    )


class Conversation(Base):
    """One thread across channels (F14). Channel is per message, not per thread."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("id", "user_id", name="conversations_id_user_key"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    conversation_id: Mapped[uuid.UUID]
    channel: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list[Any]] = _jsonb_list()
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="messages_id_user_key"),
        ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="CASCADE",
            name="messages_conversation_fkey",
        ),
        CheckConstraint(_in("channel", CHANNELS), name="channel_valid"),
        CheckConstraint(_in("role", MESSAGE_ROLES), name="role_valid"),
    )


# --- Semantic memory --------------------------------------------------------


class MemoryFact(Base):
    """Durable facts about this user ("gas with spicy food")."""

    __tablename__ = "memory_facts"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    category: Mapped[str] = mapped_column(Text)
    fact: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[uuid.UUID | None]
    # Dimension is fixed when the embedding model is chosen (Phase 3).
    embedding: Mapped[Any | None] = mapped_column(Vector())
    superseded_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()


# --- Causal memory ----------------------------------------------------------


class Hypothesis(Base):
    """A falsifiable claim about this body (F8)."""

    __tablename__ = "hypotheses"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    statement: Mapped[str] = mapped_column(Text)
    mechanism: Mapped[str | None] = mapped_column(Text)
    # What observation would support or refute it. Required: no unfalsifiable claims.
    prediction: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="proposed")
    # Statistics from the deterministic layer (effect size, n, lag).
    evidence: Mapped[dict[str, Any]] = _jsonb_obj()
    proposed_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (CheckConstraint(_in("status", HYPOTHESIS_STATUSES), name="status_valid"),)


# --- Behaviour engine -------------------------------------------------------


class ConsistencyTrack(Base):
    """One of six independent pillar tracks — never a single global streak."""

    __tablename__ = "consistency_tracks"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    # Key from data/pillars.json.
    pillar: Mapped[str] = mapped_column(Text)
    current_run_days: Mapped[int] = mapped_column(Integer, server_default="0")
    longest_run_days: Mapped[int] = mapped_column(Integer, server_default="0")
    identity_statement: Mapped[str | None] = mapped_column(Text)
    last_counted_on: Mapped[date | None]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("user_id", "pillar", name="consistency_tracks_user_pillar_key"),
        UniqueConstraint("id", "user_id", name="consistency_tracks_id_user_key"),
        CheckConstraint(
            "current_run_days >= 0 AND longest_run_days >= current_run_days",
            name="run_lengths_valid",
        ),
    )


PILLAR_DAY_RESULTS = ("met", "missed", "grace", "exempt", "observation", "no_data")


class PillarDay(Base):
    """One pillar's result on one local day. The causal engine reads these."""

    __tablename__ = "pillar_days"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    pillar: Mapped[str] = mapped_column(Text)
    day: Mapped[date]
    result: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("user_id", "pillar", "day", name="pillar_days_user_pillar_day_key"),
        CheckConstraint(_in("result", PILLAR_DAY_RESULTS), name="result_valid"),
    )


class GraceDay(Base):
    """A miss silently covered. At most two per track per month (trigger)."""

    __tablename__ = "grace_days"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    track_id: Mapped[uuid.UUID]
    pillar: Mapped[str] = mapped_column(Text)
    applied_on: Mapped[date]
    # Set when the next morning brief tells the person — retroactive, never before.
    revealed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("track_id", "applied_on", name="grace_days_track_day_key"),
        ForeignKeyConstraint(
            ["track_id", "user_id"],
            ["consistency_tracks.id", "consistency_tracks.user_id"],
            ondelete="CASCADE",
            name="grace_days_track_fkey",
        ),
    )


REWARD_KINDS = ("process", "outcome")
REWARD_STATUSES = ("available", "paid")


class Reward(Base):
    """The reward ledger: the person's own non-food rewards, paid retroactively."""

    __tablename__ = "rewards"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="process")
    status: Mapped[str] = mapped_column(Text, server_default="available")
    paid_at: Mapped[datetime | None]
    # What earned it: pillar, run length, day.
    paid_for: Mapped[dict[str, Any]] = _jsonb_obj()
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        CheckConstraint(_in("kind", REWARD_KINDS), name="kind_valid"),
        CheckConstraint(_in("status", REWARD_STATUSES), name="status_valid"),
    )


class AccountabilityContact(Base):
    """One real person who agreed to read the weekly letter (F16)."""

    __tablename__ = "accountability_contacts"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    name_and_relation: Mapped[str] = mapped_column(Text)
    agreed: Mapped[bool]
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None]


class IfThenPlan(Base):
    """Implementation intention. At most two active per user (enforced by trigger)."""

    __tablename__ = "if_then_plans"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    cue_event: Mapped[str] = mapped_column(Text)
    response_action: Mapped[str] = mapped_column(Text)
    anchor_routine: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    execution_count: Mapped[int] = mapped_column(Integer, server_default="0")
    success_count: Mapped[int] = mapped_column(Integer, server_default="0")
    success_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        Computed(
            "CASE WHEN execution_count > 0 "
            "THEN round(success_count::numeric / execution_count, 4) END",
            persisted=True,
        ),
    )
    retired_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (
        CheckConstraint(_in("status", IF_THEN_STATUSES), name="status_valid"),
        CheckConstraint(
            "execution_count >= 0 AND success_count >= 0 AND success_count <= execution_count",
            name="counts_valid",
        ),
    )


# --- Onboarding, consent, rituals ----------------------------------------------


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    script_version: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    answers: Mapped[dict[str, Any]] = _jsonb_obj()
    # The contract proposed at the end, for the person to accept or redo.
    proposed_contract: Mapped[dict[str, Any] | None]
    started_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None]

    __table_args__ = (
        Index(
            "onboarding_sessions_one_open_idx",
            "user_id",
            unique=True,
            postgresql_where=text("completed_at IS NULL"),
        ),
        CheckConstraint(_in("channel", CHANNELS), name="channel_valid"),
    )


class Consent(Base):
    """Consent and disclaimers, with the exact document version (§5)."""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    document: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = _created()


RITUALS = ("morning_brief", "evening_checkin")
RITUAL_STATUSES = ("sent", "missed", "failed")


class RitualRun(Base):
    """One ritual on one local day. The unique key makes delivery idempotent."""

    __tablename__ = "ritual_runs"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    ritual: Mapped[str] = mapped_column(Text)
    local_date: Mapped[date]
    status: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None]
    # Contract version and clause ids it cited; the causal question asked.
    payload: Mapped[dict[str, Any]] = _jsonb_obj()
    answer: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("user_id", "ritual", "local_date", name="ritual_runs_user_day_key"),
        CheckConstraint(_in("ritual", RITUALS), name="ritual_valid"),
        CheckConstraint(_in("status", RITUAL_STATUSES), name="status_valid"),
    )


# --- Safety -----------------------------------------------------------------


class Escalation(Base):
    """Coaching halted and the user referred out (F13)."""

    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    trigger_source: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    message_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(Text, server_default="open")
    details: Mapped[dict[str, Any]] = _jsonb_obj()
    created_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None]

    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="escalations_message_fkey",
        ),
        CheckConstraint(_in("status", ESCALATION_STATUSES), name="status_valid"),
    )


class AuditLog(Base):
    """Append-only. Every recommendation with its contract clause and evidence."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    occurred_at: Mapped[datetime] = _created()
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    contract_version: Mapped[int | None] = mapped_column(Integer)
    contract_clause: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = _jsonb_obj()
    payload: Mapped[dict[str, Any]] = _jsonb_obj()


# --- Channels ---------------------------------------------------------------

LINKED_CHANNELS = ("telegram",)


class ChannelIdentity(Base):
    """Maps an external account (a Telegram user id) to a Swasthya user.

    Inbound webhooks arrive with no user context, so lookups go through the
    narrow SECURITY DEFINER function ``app.resolve_channel_user``.
    """

    __tablename__ = "channel_identities"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    channel: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    linked_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="channel_identities_external_key"),
        UniqueConstraint("user_id", "channel", name="channel_identities_user_channel_key"),
        CheckConstraint(_in("channel", LINKED_CHANNELS), name="channel_valid"),
    )


class ChannelLinkCode(Base):
    """One-time code a signed-in user sends to the bot to link the channel."""

    __tablename__ = "channel_link_codes"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    channel: Mapped[str] = mapped_column(Text)
    # sha256 hex of the code; the code itself is never stored.
    code_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()

    __table_args__ = (CheckConstraint(_in("channel", LINKED_CHANNELS), name="channel_valid"),)


# --- Cost control -----------------------------------------------------------


class TokenUsage(Base):
    """Per-user, per-local-day token ledger behind the hard daily cap."""

    __tablename__ = "token_usage"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    tokens_used: Mapped[int] = mapped_column(BigInteger, server_default="0")
    call_count: Mapped[int] = mapped_column(Integer, server_default="0")
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (CheckConstraint("tokens_used >= 0", name="tokens_nonneg"),)


# Tables whose rows belong to exactly one user via ``user_id``.
TENANT_TABLES: tuple[str, ...] = tuple(
    t.name for t in Base.metadata.sorted_tables if t.name != "users"
)
