from __future__ import annotations

from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError


TECHNICAL_ERROR_MARKERS = (
    "psycopg.",
    "sqlalchemy.",
    "[SQL:",
    "[parameters:",
    "background on this error",
    "duplicate key value violates",
    "unique constraint",
    "foreign key constraint",
    "traceback",
)


def public_error_code(exc: BaseException) -> str:
    if isinstance(exc, IntegrityError):
        return "database_conflict"
    if isinstance(exc, OperationalError):
        return "database_unavailable"
    if isinstance(exc, SQLAlchemyError):
        return "database_error"
    return "unexpected_error"


def public_error_message(exc: BaseException, *, action: str = "complete this action") -> str:
    if isinstance(exc, IntegrityError):
        return (
            "EquityKobo found duplicate or conflicting provider data while saving this refresh. "
            "Your source data is safe; please retry after the current sync finishes."
        )
    if isinstance(exc, OperationalError):
        return (
            "EquityKobo could not reach the database right now. "
            "Please try again shortly."
        )
    if isinstance(exc, SQLAlchemyError):
        return (
            "EquityKobo could not update the database right now. "
            "Please try again shortly."
        )

    message = str(exc).strip()
    if message and not looks_technical(message):
        return message
    return f"EquityKobo could not {action}. Please try again shortly."


def provider_row_error_message(exc: BaseException) -> str:
    message = str(exc).strip()
    if message and not looks_technical(message):
        return message
    return "One provider row could not be saved because it conflicted with existing data."


def looks_technical(message: str) -> bool:
    normalized = message.lower()
    return any(marker in normalized for marker in TECHNICAL_ERROR_MARKERS)
