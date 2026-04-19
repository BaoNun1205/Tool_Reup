from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from license_server.app.config import LicenseServerConfig


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def get_config() -> LicenseServerConfig:
    return LicenseServerConfig.from_env()


@lru_cache(maxsize=1)
def get_engine():
    config = get_config()
    connect_args = {"check_same_thread": False} if config.database_url.startswith("sqlite") else {}
    return create_engine(config.database_url, future=True, connect_args=connect_args)


@lru_cache(maxsize=1)
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)


def create_all() -> None:
    from license_server.app import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())


@contextmanager
def session_scope():
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    session: Session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
