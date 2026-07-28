import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from io import TextIOBase

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ngx_research.models import Company, Dividend, FinancialStatement, Price, SourceDocument


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def import_companies(file: TextIOBase, session: Session) -> ImportResult:
    result = ImportResult()
    for row_number, row in _rows(file):
        try:
            symbol = _required(row, "symbol").upper()
            company = session.scalar(select(Company).where(Company.symbol == symbol))
            if company:
                company.name = _required(row, "name")
                company.sector = _optional(row, "sector")
                company.market_board = _optional(row, "market_board")
            else:
                session.add(
                    Company(
                        symbol=symbol,
                        name=_required(row, "name"),
                        sector=_optional(row, "sector"),
                        market_board=_optional(row, "market_board"),
                    )
                )
            _commit(session)
            result.imported += 1
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            result.errors.append(f"row {row_number}: {exc}")
            result.skipped += 1
    return result


def import_prices(file: TextIOBase, session: Session) -> ImportResult:
    result = ImportResult()
    for row_number, row in _rows(file):
        try:
            company = _company(session, row)
            source = _source(session, row, "price_upload")
            price = Price(
                company_id=company.id,
                trade_date=_date(row, "trade_date"),
                close_price=_decimal(row, "close_price"),
                open_price=_optional_decimal(row, "open_price"),
                high_price=_optional_decimal(row, "high_price"),
                low_price=_optional_decimal(row, "low_price"),
                volume=_optional_int(row, "volume"),
                value_traded=_optional_decimal(row, "value_traded"),
                source_document_id=source.id if source else None,
            )
            session.add(price)
            _commit(session)
            result.imported += 1
        except IntegrityError:
            session.rollback()
            result.skipped += 1
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            result.errors.append(f"row {row_number}: {exc}")
            result.skipped += 1
    return result


def import_financial_statements(file: TextIOBase, session: Session) -> ImportResult:
    result = ImportResult()
    for row_number, row in _rows(file):
        try:
            company = _company(session, row)
            source = _source(session, row, "financial_statement_upload")
            statement = FinancialStatement(
                company_id=company.id,
                period_end=_date(row, "period_end"),
                period_type=_required(row, "period_type").upper(),
                currency=_optional(row, "currency") or "NGN",
                revenue=_optional_decimal(row, "revenue"),
                profit_after_tax=_optional_decimal(row, "profit_after_tax"),
                total_assets=_optional_decimal(row, "total_assets"),
                total_liabilities=_optional_decimal(row, "total_liabilities"),
                total_equity=_optional_decimal(row, "total_equity"),
                cash_flow_operations=_optional_decimal(row, "cash_flow_operations"),
                eps=_optional_decimal(row, "eps"),
                source_document_id=source.id if source else None,
            )
            session.add(statement)
            _commit(session)
            result.imported += 1
        except IntegrityError:
            session.rollback()
            result.skipped += 1
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            result.errors.append(f"row {row_number}: {exc}")
            result.skipped += 1
    return result


def import_dividends(file: TextIOBase, session: Session) -> ImportResult:
    result = ImportResult()
    for row_number, row in _rows(file):
        try:
            company = _company(session, row)
            source = _source(session, row, "dividend_upload")
            dividend = Dividend(
                company_id=company.id,
                declared_date=_optional_date(row, "declared_date"),
                ex_dividend_date=_optional_date(row, "ex_dividend_date"),
                payment_date=_optional_date(row, "payment_date"),
                amount_per_share=_decimal(row, "amount_per_share"),
                currency=_optional(row, "currency") or "NGN",
                source_document_id=source.id if source else None,
            )
            session.add(dividend)
            _commit(session)
            result.imported += 1
        except IntegrityError:
            session.rollback()
            result.skipped += 1
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            result.errors.append(f"row {row_number}: {exc}")
            result.skipped += 1
    return result


def _rows(file: TextIOBase):
    reader = csv.DictReader(file)
    for row_number, row in enumerate(reader, start=2):
        yield row_number, {key.strip(): (value.strip() if value else "") for key, value in row.items()}


def _company(session: Session, row: dict[str, str]) -> Company:
    symbol = _required(row, "symbol").upper()
    company = session.scalar(select(Company).where(Company.symbol == symbol))
    if not company:
        raise ValueError(f"unknown company symbol {symbol}; import companies first")
    return company


def _source(session: Session, row: dict[str, str], document_type: str) -> SourceDocument | None:
    name = _optional(row, "source_name")
    url = _optional(row, "source_url")
    if not name and not url:
        return None
    source = SourceDocument(name=name or url or "Unknown source", url=url, document_type=document_type)
    session.add(source)
    session.flush()
    return source


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key, "").strip()
    if not value:
        raise ValueError(f"missing required field {key}")
    return value


def _optional(row: dict[str, str], key: str) -> str | None:
    value = row.get(key, "").strip()
    return value or None


def _date(row: dict[str, str], key: str) -> date:
    try:
        return date.fromisoformat(_required(row, key))
    except ValueError as exc:
        raise ValueError(f"{key} must be YYYY-MM-DD") from exc


def _optional_date(row: dict[str, str], key: str) -> date | None:
    value = _optional(row, key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be YYYY-MM-DD") from exc


def _decimal(row: dict[str, str], key: str) -> Decimal:
    try:
        return Decimal(_required(row, key).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{key} must be numeric") from exc


def _optional_decimal(row: dict[str, str], key: str) -> Decimal | None:
    value = _optional(row, key)
    if not value:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{key} must be numeric") from exc


def _optional_int(row: dict[str, str], key: str) -> int | None:
    value = _optional(row, key)
    if not value:
        return None
    return int(value.replace(",", ""))


def _commit(session: Session) -> None:
    session.commit()
