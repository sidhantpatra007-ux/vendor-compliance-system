import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    if os.getenv("ALLOW_LEGACY_SCHEMA_CREATE", "false").lower() not in {"1", "true", "yes"}:
        return
    import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
