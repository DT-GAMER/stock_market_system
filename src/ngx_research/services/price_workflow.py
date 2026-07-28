import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import TextIOBase

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import Company, Price
from ngx_research.schemas import (
    LatestPriceRead,
    LiquidityRead,
    PriceImportValidationResult,
    PriceImportValidationRow,
    PriceRead,
)


@dataclass
class ParsedPriceRow:
    row_number: int
    symbol: str | None
    trade_date: date | None
    close_price: Decimal | None
    errors: list[str]
    warnings: list[str]


def validate_price_csv(file: TextIOBase, session: Session) -> PriceImportValidationResult:
    rows: list[PriceImportValidationRow] = []
    for parsed in _parse_rows(file):
        action = None
        status = "valid"
        if parsed.errors:
            status = "invalid"
        else:
            company = session.scalar(select(Company).where(Company.symbol == parsed.symbol))
            if not company:
                parsed.errors.append(f"unknown company symbol {parsed.symbol}; import company first")
                status = "invalid"
            elif _existing_price(session, company.id, parsed.trade_date):
                status = "duplicate"
                action = "skip_on_import"
            else:
                action = "insert_on_import"

        rows.append(
            PriceImportValidationRow(
                row_number=parsed.row_number,
                symbol=parsed.symbol,
                trade_date=parsed.trade_date,
                close_price=parsed.close_price,
                status=status,
                action=action,
                errors=parsed.errors,
                warnings=parsed.warnings,
            )
        )

    return PriceImportValidationResult(
        total_rows=len(rows),
        valid_rows=sum(1 for row in rows if row.status == "valid"),
        invalid_rows=sum(1 for row in rows if row.status == "invalid"),
        duplicate_rows=sum(1 for row in rows if row.status == "duplicate"),
        rows=rows,
    )


def latest_prices(session: Session, limit: int = 100) -> list[LatestPriceRead]:
    companies = session.scalars(select(Company).where(Company.is_active.is_(True)).order_by(Company.symbol))
    latest: list[LatestPriceRead] = []
    for company in companies:
        price = _latest_price(session, company.id)
        if not price:
            continue
        previous = session.scalar(
            select(Price)
            .where(Price.company_id == company.id, Price.trade_date < price.trade_date)
            .order_by(desc(Price.trade_date))
            .limit(1)
        )
        price_change = price.close_price - previous.close_price if previous else None
        price_change_percent = None
        if previous and previous.close_price:
            price_change_percent = ((price_change / previous.close_price) * Decimal(100)).quantize(
                Decimal("0.0001")
            )
        latest.append(
            LatestPriceRead(
                symbol=company.symbol,
                name=company.name,
                trade_date=price.trade_date,
                close_price=price.close_price,
                previous_close=previous.close_price if previous else None,
                price_change=price_change,
                price_change_percent=price_change_percent,
                volume=price.volume,
                value_traded=price.value_traded,
                reviewed=price.reviewed,
            )
        )
    return latest[:limit]


def price_history(session: Session, symbol: str, limit: int = 100) -> list[PriceRead] | None:
    company = session.scalar(select(Company).where(Company.symbol == symbol.upper()))
    if not company:
        return None
    rows = session.scalars(
        select(Price)
        .where(Price.company_id == company.id)
        .order_by(desc(Price.trade_date))
        .limit(limit)
    )
    return [
        PriceRead(
            id=price.id,
            symbol=company.symbol,
            trade_date=price.trade_date,
            close_price=price.close_price,
            volume=price.volume,
            reviewed=price.reviewed,
        )
        for price in rows
    ]


def liquidity_metrics(
    session: Session,
    window_days: int = 90,
    limit: int = 100,
) -> list[LiquidityRead]:
    as_of_date = datetime.now(UTC).date()
    since = as_of_date - timedelta(days=window_days)
    companies = session.scalars(select(Company).where(Company.is_active.is_(True)).order_by(Company.symbol))
    metrics: list[LiquidityRead] = []
    for company in companies:
        result = session.execute(
            select(
                func.count(Price.id),
                func.avg(Price.volume),
                func.avg(Price.value_traded),
                func.sum(Price.value_traded),
                func.max(Price.trade_date),
            ).where(
                Price.company_id == company.id,
                Price.trade_date >= since,
                Price.trade_date <= as_of_date,
            )
        ).one()
        trading_days, avg_volume, avg_value, total_value, latest_trade_date = result
        metrics.append(
            LiquidityRead(
                symbol=company.symbol,
                name=company.name,
                window_days=window_days,
                trading_days=trading_days or 0,
                average_volume=_decimal_or_none(avg_volume),
                average_value_traded=_decimal_or_none(avg_value),
                total_value_traded=_decimal_or_none(total_value),
                latest_trade_date=latest_trade_date,
                liquidity_status=_liquidity_status(trading_days or 0, total_value),
            )
        )
    return sorted(
        metrics,
        key=lambda item: item.total_value_traded or Decimal(0),
        reverse=True,
    )[:limit]


def _parse_rows(file: TextIOBase):
    reader = csv.DictReader(file)
    required_headers = {"symbol", "trade_date", "close_price"}
    headers = {header.strip() for header in reader.fieldnames or []}
    missing_headers = required_headers - headers
    if missing_headers:
        yield ParsedPriceRow(
            row_number=1,
            symbol=None,
            trade_date=None,
            close_price=None,
            errors=[f"missing required headers: {', '.join(sorted(missing_headers))}"],
            warnings=[],
        )
        return

    seen: set[tuple[str, date]] = set()
    for row_number, row in enumerate(reader, start=2):
        cleaned = {key.strip(): (value.strip() if value else "") for key, value in row.items()}
        parsed = _parse_row(row_number, cleaned)
        if parsed.symbol and parsed.trade_date:
            identity = (parsed.symbol, parsed.trade_date)
            if identity in seen:
                parsed.errors.append("duplicate row inside uploaded CSV")
            seen.add(identity)
        yield parsed


def _parse_row(row_number: int, row: dict[str, str]) -> ParsedPriceRow:
    errors: list[str] = []
    warnings: list[str] = []
    symbol = row.get("symbol", "").upper() or None
    trade_date = _date(row.get("trade_date", ""), "trade_date", errors)
    close_price = _decimal(row.get("close_price", ""), "close_price", errors)
    open_price = _optional_decimal(row.get("open_price", ""), "open_price", errors)
    high_price = _optional_decimal(row.get("high_price", ""), "high_price", errors)
    low_price = _optional_decimal(row.get("low_price", ""), "low_price", errors)
    volume = _optional_int(row.get("volume", ""), "volume", errors)
    value_traded = _optional_decimal(row.get("value_traded", ""), "value_traded", errors)

    if not symbol:
        errors.append("missing required field symbol")
    if close_price is not None and close_price <= Decimal(0):
        errors.append("close_price must be greater than zero")
    if volume is not None and volume < 0:
        errors.append("volume cannot be negative")
    if value_traded is not None and value_traded < Decimal(0):
        errors.append("value_traded cannot be negative")
    if high_price is not None and low_price is not None and high_price < low_price:
        errors.append("high_price cannot be lower than low_price")
    if close_price is not None and high_price is not None and close_price > high_price:
        warnings.append("close_price is above high_price")
    if close_price is not None and low_price is not None and close_price < low_price:
        warnings.append("close_price is below low_price")
    if open_price is not None and high_price is not None and open_price > high_price:
        warnings.append("open_price is above high_price")
    if open_price is not None and low_price is not None and open_price < low_price:
        warnings.append("open_price is below low_price")

    return ParsedPriceRow(
        row_number=row_number,
        symbol=symbol,
        trade_date=trade_date,
        close_price=close_price,
        errors=errors,
        warnings=warnings,
    )


def _date(value: str, field_name: str, errors: list[str]) -> date | None:
    if not value:
        errors.append(f"missing required field {field_name}")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(f"{field_name} must be YYYY-MM-DD")
        return None


def _decimal(value: str, field_name: str, errors: list[str]) -> Decimal | None:
    if not value:
        errors.append(f"missing required field {field_name}")
        return None
    return _optional_decimal(value, field_name, errors)


def _optional_decimal(value: str, field_name: str, errors: list[str]) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        errors.append(f"{field_name} must be numeric")
        return None


def _optional_int(value: str, field_name: str, errors: list[str]) -> int | None:
    if not value:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        errors.append(f"{field_name} must be an integer")
        return None


def _existing_price(session: Session, company_id: int, trade_date: date | None) -> Price | None:
    if not trade_date:
        return None
    return session.scalar(
        select(Price).where(Price.company_id == company_id, Price.trade_date == trade_date).limit(1)
    )


def _latest_price(session: Session, company_id: int) -> Price | None:
    return session.scalar(
        select(Price).where(Price.company_id == company_id).order_by(desc(Price.trade_date)).limit(1)
    )


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _liquidity_status(trading_days: int, total_value) -> str:
    total = _decimal_or_none(total_value) or Decimal(0)
    if trading_days == 0:
        return "No recent trading data"
    if trading_days < 5:
        return "Very thin"
    if total < Decimal(10000000):
        return "Thin"
    if total < Decimal(100000000):
        return "Moderate"
    return "Liquid"
