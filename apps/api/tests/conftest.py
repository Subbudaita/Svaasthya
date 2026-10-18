"""Test fixtures: an ephemeral Postgres, migrated to head, and an RLS-bound role.

Database source, in order:
1. ``TEST_DATABASE_URL`` — an empty admin database you provide.
2. Otherwise a throwaway ``pgvector/pgvector:pg16`` container via testcontainers.

No external accounts are needed. Auth tokens are minted locally with an HS256
test secret, exactly as Supabase's legacy signing works.
"""

import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import jwt
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine
from swasthya_api.db.session import create_engine
from swasthya_api.main import create_app
from swasthya_api.settings import Settings

API_DIR = Path(__file__).resolve().parents[1]
JWT_SECRET = "test-only-secret-not-used-anywhere-else-0123456789"
APP_LOGIN = "swasthya_api_test"
APP_PASSWORD = "test-only-password"
PG_IMAGE = "pgvector/pgvector:pg16"


def _plain(url: str) -> str:
    """SQLAlchemy URL → libpq URL for raw psycopg connections."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def alembic_config(admin_url: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["url"] = admin_url
    return cfg


@pytest.fixture(scope="session")
def admin_url() -> Iterator[str]:
    provided = os.environ.get("TEST_DATABASE_URL")
    if provided:
        yield provided
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(PG_IMAGE, dbname="swasthya_test", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def app_url(admin_url: str) -> str:
    """Migrate to head, then create the non-superuser login the API connects as."""
    command.upgrade(alembic_config(admin_url), "head")
    with psycopg.connect(_plain(admin_url), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_LOGIN,)).fetchone()
        if not exists:
            conn.execute(
                f"CREATE ROLE {APP_LOGIN} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{APP_PASSWORD}'"
            )
        conn.execute(f"GRANT swasthya_app TO {APP_LOGIN}")
    head, _, rest = admin_url.partition("://")
    host_part = rest.split("@", 1)[1]
    return f"{head}://{APP_LOGIN}:{APP_PASSWORD}@{host_part}"


@pytest.fixture(autouse=True)
def _clean_tables(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    if "app_url" not in request.fixturenames:
        return
    admin = request.getfixturevalue("admin_url")
    with psycopg.connect(_plain(admin), autocommit=True) as conn:
        conn.execute("TRUNCATE users CASCADE")


@pytest.fixture
def admin_conn(admin_url: str, app_url: str) -> Iterator[psycopg.Connection]:
    """Superuser connection — bypasses RLS. Use only to arrange fixtures."""
    with psycopg.connect(_plain(admin_url), autocommit=True) as conn:
        yield conn


@pytest.fixture
def as_user(app_url: str) -> Callable[..., Any]:
    """Open a transaction as the RLS-bound app role, acting as ``user_id``.

    ``as_user(None)`` opens one with no identity set at all.
    """

    @contextmanager
    def _open(user_id: uuid.UUID | None) -> Iterator[psycopg.Connection]:
        with psycopg.connect(_plain(app_url), autocommit=True) as conn:
            with conn.transaction():
                if user_id is not None:
                    conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
                yield conn

    return _open


@pytest.fixture
def make_user(admin_conn: psycopg.Connection) -> Callable[..., uuid.UUID]:
    def _make(timezone: str = "Asia/Kolkata", daily_token_cap: int | None = None) -> uuid.UUID:
        user_id = uuid.uuid4()
        admin_conn.execute(
            "INSERT INTO users (id, email, timezone, daily_token_cap) VALUES (%s, %s, %s, %s)",
            (user_id, f"{user_id}@example.test", timezone, daily_token_cap),
        )
        return user_id

    return _make


@pytest.fixture
def settings(app_url: str) -> Settings:
    return Settings.model_validate(
        {
            "SWASTHYA_ENV": "test",
            "DATABASE_URL": app_url,
            "SUPABASE_JWT_SECRET": JWT_SECRET,
            "DAILY_TOKEN_CAP": 10_000,
        }
    )


@pytest.fixture
async def engine(settings: Settings) -> AsyncEngine:
    eng = create_engine(str(settings.database_url))
    yield eng
    await eng.dispose()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def make_token() -> Callable[..., str]:
    def _make(
        user_id: uuid.UUID,
        *,
        secret: str = JWT_SECRET,
        audience: str = "authenticated",
        expires_in: int = 3600,
        email: str | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": str(user_id),
            "aud": audience,
            "role": "authenticated",
            "iat": now,
            "exp": now + expires_in,
            "email": email or f"{user_id}@example.test",
        }
        return jwt.encode(claims, secret, algorithm="HS256")

    return _make


@pytest.fixture
def alembic_cfg(admin_url: str) -> Config:
    return alembic_config(admin_url)


# --- Phase 1: perception doubles -------------------------------------------

FOOD_FIXTURE = Path(__file__).resolve().parents[3] / "packages/engines/tests/fixtures/foods.json"


def _message(content, stop_reason, usage=(1200, 300)):
    from anthropic.types import Message

    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
        }
    )


class ScriptedAnthropic:
    """Stands in for AsyncAnthropic: replays scripted perceptions, records calls."""

    def __init__(self) -> None:
        self.perceptions: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def will_see(self, *items: dict[str, Any], is_food: bool = True, note: str | None = None):
        import json

        self.perceptions.append(
            _message(
                [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"is_food": is_food, "items": list(items), "note": note}
                        ),
                    }
                ],
                "end_turn",
            )
        )

    async def create(self, **kwargs: Any):
        self.calls.append(kwargs)
        return self.perceptions.pop(0)

    async def close(self) -> None:
        pass


def plate_item(name, code, qty, unit, grams, role, confidence="high"):
    return {
        "name": name,
        "food_code": code,
        "quantity": qty,
        "unit": unit,
        "estimated_grams": grams,
        "role": role,
        "confidence": confidence,
    }


@pytest.fixture
def model() -> ScriptedAnthropic:
    return ScriptedAnthropic()


@pytest.fixture
def phase1_app(settings, model, tmp_path):
    """The real app, with the fixture food table and a scripted model client."""
    import json

    from swasthya_api.photos import LocalPhotoStore
    from swasthya_engines.nutrition import FoodTable

    # Each perception call reserves its worst case (~12k tokens) up front.
    app = create_app(settings.model_copy(update={"daily_token_cap": 200_000}))
    with TestClient(app) as c:
        services = app.state.services
        services.anthropic = model
        services.food_table = FoodTable.from_dict(json.loads(FOOD_FIXTURE.read_text()))
        services.photos = LocalPhotoStore(tmp_path)
        yield c, services


@pytest.fixture
def jpeg_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (180, 120, 60)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(name="item")
def _item_fixture():
    return plate_item
