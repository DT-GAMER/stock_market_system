from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ngx_research.config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    db_path = database_url.replace("sqlite:///", "", 1)
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def _sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def _safe_database_url(database_url: str) -> str:
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except ArgumentError:
        return "<invalid DATABASE_URL>"


database_url = _sqlalchemy_database_url(settings.database_url)
_ensure_sqlite_parent(database_url)

engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
    pool_recycle=1800 if not database_url.startswith("sqlite") else -1,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

SCHEMA_COLUMN_UPGRADES = {
    "financial_statements": {
        "statement_kind": "VARCHAR(80)",
        "gross_earnings": "NUMERIC(24, 4)",
        "interest_income": "NUMERIC(24, 4)",
        "net_interest_income": "NUMERIC(24, 4)",
        "customer_deposits": "NUMERIC(24, 4)",
        "loans_and_advances": "NUMERIC(24, 4)",
        "borrowings_total": "NUMERIC(24, 4)",
        "interest_expense": "NUMERIC(24, 4)",
        "npl_ratio": "NUMERIC(18, 4)",
        "capital_adequacy_ratio": "NUMERIC(18, 4)",
        "loan_to_deposit_ratio": "NUMERIC(18, 4)",
        "business_summary": "TEXT",
        "auditor_name": "VARCHAR(255)",
        "auditor_opinion": "TEXT",
        "major_risks": "JSON",
        "corporate_actions": "JSON",
    }
}


def init_db() -> None:
    from ngx_research import models  # noqa: F401

    try:
        Base.metadata.create_all(bind=engine)
        _apply_schema_column_upgrades()
    except SQLAlchemyError as exc:
        safe_url = _safe_database_url(database_url)
        raise RuntimeError(
            "Could not initialize the database. "
            f"Configured DATABASE_URL is {safe_url}. "
            "Make sure the PostgreSQL host and port are reachable before starting the app."
        ) from exc


def _apply_schema_column_upgrades() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, columns in SCHEMA_COLUMN_UPGRADES.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                )


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
