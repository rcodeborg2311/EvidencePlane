from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path

MAX_PAYLOAD_BYTES = 256 * 1024


class ConfigurationError(RuntimeError):
    """Raised when required environment configuration is missing."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    hmac_secret: str
    admin_token: str
    enable_otel: bool
    enable_docs: bool
    github_app_id: str | None
    github_webhook_secret: str | None
    github_app_private_key: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = read_secret("DATABASE_URL")
        hmac_secret = read_secret("EVIDENCEPLANE_HMAC_SECRET")
        admin_token = read_secret("ADMIN_TOKEN")
        missing = [
            name
            for name, value in (
                ("DATABASE_URL", database_url),
                ("EVIDENCEPLANE_HMAC_SECRET", hmac_secret),
                ("ADMIN_TOKEN", admin_token),
            )
            if not value
        ]
        if missing:
            joined = ", ".join(missing)
            raise ConfigurationError(f"Missing required environment variable(s): {joined}")

        return cls(
            database_url=normalize_database_url(str(database_url)),
            hmac_secret=str(hmac_secret),
            admin_token=str(admin_token),
            enable_otel=env_flag("EVIDENCEPLANE_ENABLE_OTEL"),
            enable_docs=env_flag("EVIDENCEPLANE_ENABLE_DOCS"),
            github_app_id=read_secret("GITHUB_APP_ID") or None,
            github_webhook_secret=read_secret("GITHUB_WEBHOOK_SECRET") or None,
            github_app_private_key=read_secret("GITHUB_APP_PRIVATE_KEY") or None,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def read_secret(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    file_path = os.getenv(f"{name}_FILE")
    if not file_path:
        return None

    try:
        return Path(file_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(
            f"Unable to read secret file for {name}: {exc.strerror}"
        ) from exc
