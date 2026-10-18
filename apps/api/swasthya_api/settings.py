"""Typed settings, loaded from the environment (see .env.example)."""

from enum import StrEnum
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Environment = Field(default=Environment.LOCAL, alias="SWASTHYA_ENV")

    # Runtime connection. MUST be a role that is neither superuser nor BYPASSRLS;
    # the API refuses to serve requests otherwise (see db.session.assert_rls_enforced).
    database_url: PostgresDsn = Field(alias="DATABASE_URL")

    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    # Legacy HS256 project secret. If unset, tokens are verified against the
    # project's JWKS at {SUPABASE_URL}/auth/v1/.well-known/jwks.json.
    supabase_jwt_secret: str | None = Field(default=None, alias="SUPABASE_JWT_SECRET", repr=False)
    supabase_jwt_audience: str = Field(default="authenticated", alias="SUPABASE_JWT_AUDIENCE")

    # Hard per-user daily token cap (docs/PLAN.md §4). A user row may lower or
    # raise it via users.daily_token_cap.
    daily_token_cap: int = Field(default=300_000, gt=0, alias="DAILY_TOKEN_CAP")

    # Applied to new users until onboarding records their own timezone.
    default_timezone: str = Field(default="Asia/Kolkata", alias="DEFAULT_TIMEZONE")

    cors_allow_origins: list[str] = Field(default_factory=list, alias="CORS_ALLOW_ORIGINS")

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use the postgresql+psycopg:// scheme")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
