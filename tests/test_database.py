from pathlib import Path

from ngx_research.database import (
    _ensure_sqlite_parent,
    _safe_database_url,
    _sqlalchemy_database_url,
)


def test_postgres_urls_use_psycopg_driver() -> None:
    assert (
        _sqlalchemy_database_url("postgres://user:pass@example.com:5432/db")
        == "postgresql+psycopg://user:pass@example.com:5432/db"
    )
    assert (
        _sqlalchemy_database_url("postgresql://user:pass@example.com:5432/db")
        == "postgresql+psycopg://user:pass@example.com:5432/db"
    )


def test_sqlite_parent_directory_is_created(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "equitykobo.db"

    _ensure_sqlite_parent(f"sqlite:///{db_path}")

    assert db_path.parent.is_dir()


def test_safe_database_url_hides_password() -> None:
    safe_url = _safe_database_url("postgres://user:secret@example.com:5432/db")

    assert "secret" not in safe_url
    assert "***" in safe_url
