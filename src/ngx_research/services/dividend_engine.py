import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import TextIOBase

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import Company, Dividend, FinancialStatement, Price
from ngx_research.schemas import (
    DividendCandidateRead,
    DividendHistoryRead,
    DividendImportValidationResult,
    DividendImportValidationRow,
)


@dataclass
class ParsedDividendRow:
    row_number: int
    symbol: str | None
    declared_date: date | None
    payment_date: date | None
    amount_per_share: Decimal | None
    errors: list[str]
    warnings: list[str]


def validate_dividend_csv(file: TextIOBase, session: Session) -> DividendImportValidationResult:
    rows: list[DividendImportValidationRow] = []
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
            elif _existing_dividend(
                session,
                company.id,
                parsed.declared_date,
                parsed.amount_per_share,
            ):
                status = "duplicate"
                action = "skip_on_import"
            else:
                action = "insert_on_import"

        rows.append(
            DividendImportValidationRow(
                row_number=parsed.row_number,
                symbol=parsed.symbol,
                declared_date=parsed.declared_date,
                payment_date=parsed.payment_date,
                amount_per_share=parsed.amount_per_share,
                status=status,
                action=action,
                errors=parsed.errors,
                warnings=parsed.warnings,
            )
        )

    return DividendImportValidationResult(
        total_rows=len(rows),
        valid_rows=sum(1 for row in rows if row.status == "valid"),
        invalid_rows=sum(1 for row in rows if row.status == "invalid"),
        duplicate_rows=sum(1 for row in rows if row.status == "duplicate"),
        rows=rows,
    )


def dividend_history(
    session: Session,
    symbol: str,
    limit: int = 100,
) -> list[DividendHistoryRead] | None:
    company = session.scalar(select(Company).where(Company.symbol == symbol.upper()))
    if not company:
        return None
    dividends = session.scalars(
        select(Dividend)
        .where(Dividend.company_id == company.id)
        .order_by(desc(Dividend.payment_date), desc(Dividend.declared_date))
        .limit(limit)
    )
    return [
        DividendHistoryRead(
            id=dividend.id,
            symbol=company.symbol,
            declared_date=dividend.declared_date,
            ex_dividend_date=dividend.ex_dividend_date,
            payment_date=dividend.payment_date,
            amount_per_share=dividend.amount_per_share,
            currency=dividend.currency,
            reviewed=dividend.reviewed,
        )
        for dividend in dividends
    ]


def dividend_candidates(
    session: Session,
    lookback_years: int = 5,
    limit: int = 100,
) -> list[DividendCandidateRead]:
    as_of_date = datetime.now(UTC).date()
    companies = session.scalars(select(Company).where(Company.is_active.is_(True)).order_by(Company.symbol))
    candidates = [_company_dividend_candidate(session, company, as_of_date, lookback_years) for company in companies]
    return sorted(candidates, key=lambda item: item.safety_score, reverse=True)[:limit]


def _company_dividend_candidate(
    session: Session,
    company: Company,
    as_of_date: date,
    lookback_years: int,
) -> DividendCandidateRead:
    latest_price = _latest_price(session, company.id)
    latest_statement = _latest_statement(session, company.id, as_of_date)
    since = as_of_date - timedelta(days=365 * lookback_years)
    trailing_since = as_of_date - timedelta(days=365)

    dividends = session.scalars(
        select(Dividend)
        .where(
            Dividend.company_id == company.id,
            Dividend.payment_date.is_not(None),
            Dividend.payment_date >= since,
            Dividend.payment_date <= as_of_date,
        )
        .order_by(desc(Dividend.payment_date))
    ).all()
    trailing_dividend = session.scalar(
        select(func.sum(Dividend.amount_per_share)).where(
            Dividend.company_id == company.id,
            Dividend.payment_date.is_not(None),
            Dividend.payment_date >= trailing_since,
            Dividend.payment_date <= as_of_date,
        )
    )
    latest_payment_date = dividends[0].payment_date if dividends else None
    years_with_dividends = len({dividend.payment_date.year for dividend in dividends if dividend.payment_date})
    dividend_yield = _safe_percent(trailing_dividend, latest_price.close_price if latest_price else None)
    payout_ratio = _safe_percent(trailing_dividend, latest_statement.eps if latest_statement else None)
    dividend_cover = _safe_div(latest_statement.eps if latest_statement else None, trailing_dividend)
    warnings = _warnings(
        dividends=dividends,
        latest_price=latest_price,
        latest_statement=latest_statement,
        trailing_dividend=trailing_dividend,
        payout_ratio=payout_ratio,
        dividend_cover=dividend_cover,
        lookback_years=lookback_years,
    )
    safety_score = _safety_score(
        years_with_dividends=years_with_dividends,
        dividend_yield=dividend_yield,
        payout_ratio=payout_ratio,
        dividend_cover=dividend_cover,
        latest_price=latest_price,
        latest_statement=latest_statement,
        dividends=dividends,
        lookback_years=lookback_years,
    )
    return DividendCandidateRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        latest_price=latest_price.close_price if latest_price else None,
        trailing_dividend=trailing_dividend,
        dividend_yield=dividend_yield,
        dividend_events=len(dividends),
        years_with_dividends=years_with_dividends,
        latest_payment_date=latest_payment_date,
        latest_eps=latest_statement.eps if latest_statement else None,
        payout_ratio=payout_ratio,
        dividend_cover=dividend_cover,
        safety_score=safety_score,
        status=_status(safety_score, warnings),
        warnings=warnings,
    )


def _parse_rows(file: TextIOBase):
    reader = csv.DictReader(file)
    required_headers = {"symbol", "amount_per_share"}
    headers = {header.strip() for header in reader.fieldnames or []}
    missing_headers = required_headers - headers
    if missing_headers:
        yield ParsedDividendRow(
            row_number=1,
            symbol=None,
            declared_date=None,
            payment_date=None,
            amount_per_share=None,
            errors=[f"missing required headers: {', '.join(sorted(missing_headers))}"],
            warnings=[],
        )
        return

    seen: set[tuple[str, date | None, Decimal | None]] = set()
    for row_number, row in enumerate(reader, start=2):
        cleaned = {key.strip(): (value.strip() if value else "") for key, value in row.items()}
        parsed = _parse_row(row_number, cleaned)
        identity = (parsed.symbol or "", parsed.declared_date, parsed.amount_per_share)
        if identity in seen:
            parsed.errors.append("duplicate row inside uploaded CSV")
        seen.add(identity)
        yield parsed


def _parse_row(row_number: int, row: dict[str, str]) -> ParsedDividendRow:
    errors: list[str] = []
    warnings: list[str] = []
    symbol = row.get("symbol", "").upper() or None
    declared_date = _optional_date(row.get("declared_date", ""), "declared_date", errors)
    ex_dividend_date = _optional_date(row.get("ex_dividend_date", ""), "ex_dividend_date", errors)
    payment_date = _optional_date(row.get("payment_date", ""), "payment_date", errors)
    amount_per_share = _decimal(row.get("amount_per_share", ""), "amount_per_share", errors)

    if not symbol:
        errors.append("missing required field symbol")
    if amount_per_share is not None and amount_per_share <= Decimal(0):
        errors.append("amount_per_share must be greater than zero")
    if declared_date and ex_dividend_date and ex_dividend_date < declared_date:
        warnings.append("ex_dividend_date is before declared_date")
    if ex_dividend_date and payment_date and payment_date < ex_dividend_date:
        warnings.append("payment_date is before ex_dividend_date")
    if not declared_date:
        warnings.append("declared_date is missing; duplicate detection will be weaker")
    if not payment_date:
        warnings.append("payment_date is missing; trailing dividend calculations will ignore this row")

    return ParsedDividendRow(
        row_number=row_number,
        symbol=symbol,
        declared_date=declared_date,
        payment_date=payment_date,
        amount_per_share=amount_per_share,
        errors=errors,
        warnings=warnings,
    )


def _optional_date(value: str, field_name: str, errors: list[str]) -> date | None:
    if not value:
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
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        errors.append(f"{field_name} must be numeric")
        return None


def _existing_dividend(
    session: Session,
    company_id: int,
    declared_date: date | None,
    amount_per_share: Decimal | None,
) -> Dividend | None:
    if amount_per_share is None:
        return None
    return session.scalar(
        select(Dividend)
        .where(
            Dividend.company_id == company_id,
            Dividend.declared_date == declared_date,
            Dividend.amount_per_share == amount_per_share,
        )
        .limit(1)
    )


def _latest_price(session: Session, company_id: int) -> Price | None:
    return session.scalar(
        select(Price).where(Price.company_id == company_id).order_by(desc(Price.trade_date)).limit(1)
    )


def _latest_statement(
    session: Session,
    company_id: int,
    as_of_date: date,
) -> FinancialStatement | None:
    fy_statement = session.scalar(
        select(FinancialStatement)
        .where(
            FinancialStatement.company_id == company_id,
            FinancialStatement.period_end <= as_of_date,
            FinancialStatement.period_type == "FY",
            FinancialStatement.eps.is_not(None),
        )
        .order_by(desc(FinancialStatement.period_end))
        .limit(1)
    )
    if fy_statement:
        return fy_statement
    return session.scalar(
        select(FinancialStatement)
        .where(
            FinancialStatement.company_id == company_id,
            FinancialStatement.period_end <= as_of_date,
            FinancialStatement.eps.is_not(None),
        )
        .order_by(desc(FinancialStatement.period_end))
        .limit(1)
    )


def _safe_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal(0)):
        return None
    return (numerator / denominator).quantize(Decimal("0.0001"))


def _safe_percent(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    value = _safe_div(numerator, denominator)
    if value is None:
        return None
    return (value * Decimal(100)).quantize(Decimal("0.0001"))


def _warnings(
    dividends: list[Dividend],
    latest_price: Price | None,
    latest_statement: FinancialStatement | None,
    trailing_dividend: Decimal | None,
    payout_ratio: Decimal | None,
    dividend_cover: Decimal | None,
    lookback_years: int,
) -> list[str]:
    warnings: list[str] = []
    if not dividends:
        warnings.append("No paid dividend records in lookback window.")
    if latest_price is None:
        warnings.append("No latest price; dividend yield cannot be calculated.")
    if latest_statement is None:
        warnings.append("No EPS data; payout ratio and dividend cover cannot be calculated.")
    if trailing_dividend is None:
        warnings.append("No paid dividend in the trailing 12 months.")
    if payout_ratio is not None and payout_ratio > Decimal(80):
        warnings.append("Payout ratio is high; dividend may be harder to sustain.")
    if dividend_cover is not None and dividend_cover < Decimal("1.25"):
        warnings.append("Dividend cover is low.")
    if len({dividend.payment_date.year for dividend in dividends if dividend.payment_date}) < min(3, lookback_years):
        warnings.append("Dividend record is not yet consistent across multiple years.")
    if any(not dividend.reviewed for dividend in dividends):
        warnings.append("Some dividend records are unreviewed.")
    return warnings


def _safety_score(
    years_with_dividends: int,
    dividend_yield: Decimal | None,
    payout_ratio: Decimal | None,
    dividend_cover: Decimal | None,
    latest_price: Price | None,
    latest_statement: FinancialStatement | None,
    dividends: list[Dividend],
    lookback_years: int,
) -> int:
    score = 0
    score += min(years_with_dividends, lookback_years) * 8
    if dividend_yield is not None:
        if Decimal(3) <= dividend_yield <= Decimal(12):
            score += 25
        elif dividend_yield > Decimal(0):
            score += 10
    if payout_ratio is not None:
        if payout_ratio <= Decimal(60):
            score += 25
        elif payout_ratio <= Decimal(80):
            score += 15
        else:
            score -= 10
    if dividend_cover is not None:
        if dividend_cover >= Decimal(2):
            score += 20
        elif dividend_cover >= Decimal("1.25"):
            score += 10
        else:
            score -= 15
    if latest_price is not None:
        score += 5
    if latest_statement is not None:
        score += 5
    if dividends and all(dividend.reviewed for dividend in dividends):
        score += 5
    return max(0, min(100, score))


def _status(safety_score: int, warnings: list[str]) -> str:
    if safety_score >= 75 and not warnings:
        return "Strong dividend candidate"
    if safety_score >= 65:
        return "Dividend candidate - review warnings"
    if safety_score >= 40:
        return "Watch dividend data"
    return "Insufficient dividend evidence"
