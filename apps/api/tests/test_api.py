import uuid

import pytest
from fastapi.testclient import TestClient
from swasthya_api.db.session import RlsNotEnforcedError
from swasthya_api.main import create_app
from swasthya_api.settings import Settings


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_checks_database(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_me_requires_token(client):
    assert client.get("/v1/me").status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"secret": "wrong-secret-wrong-secret-wrong-secret-00"},
        {"audience": "anon"},
        {"expires_in": -60},
    ],
)
def test_me_rejects_bad_tokens(client, make_token, overrides):
    token = make_token(uuid.uuid4(), **overrides)
    assert client.get("/v1/me", headers=auth(token)).status_code == 401


def test_me_provisions_once(client, make_token, settings):
    user = uuid.uuid4()
    first = client.get("/v1/me", headers=auth(make_token(user)))
    second = client.get("/v1/me", headers=auth(make_token(user)))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["timezone"] == settings.default_timezone
    assert first.json()["onboarded"] is False


def test_auth_not_configured_fails_closed(settings, make_token):
    unconfigured = settings.model_copy(update={"supabase_jwt_secret": None, "supabase_url": None})
    with TestClient(create_app(unconfigured)) as c:
        r = c.get("/v1/me", headers=auth(make_token(uuid.uuid4())))
    assert r.status_code == 503


def test_refuses_to_start_as_superuser(admin_url):
    superuser = Settings.model_validate(
        {"DATABASE_URL": admin_url, "SUPABASE_JWT_SECRET": "x" * 32}
    )
    with pytest.raises(RlsNotEnforcedError):
        with TestClient(create_app(superuser)):
            pass


def test_settings_reject_non_psycopg_url():
    with pytest.raises(ValueError):
        Settings.model_validate({"DATABASE_URL": "postgresql://u:p@localhost/db"})
