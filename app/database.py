from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import get_settings

_ENGINES: dict[str, Engine] = {}


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    engine = _ENGINES.get(url)
    if engine is not None:
        return engine

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(
        url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=True,
    )
    _ENGINES[url] = engine
    return engine


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session


def reset_database_cache() -> None:
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()
