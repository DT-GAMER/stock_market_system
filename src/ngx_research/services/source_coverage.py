from datetime import UTC, date, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import Company, Dividend, FinancialStatement, Price, UploadedReport
from ngx_research.schemas import CompanyCoverageRead, CoverageItem


def build_coverage(
    session: Session,
    as_of_date: date | None = None,
) -> list[CompanyCoverageRead]:
    coverage_date = as_of_date or datetime.now(UTC).date()
    companies = session.scalars(select(Company).where(Company.is_active.is_(True)).order_by(Company.symbol))
    return [_company_coverage(session, company, coverage_date) for company in companies]


def build_company_coverage(
    session: Session,
    symbol: str,
    as_of_date: date | None = None,
) -> CompanyCoverageRead | None:
    company = session.scalar(select(Company).where(Company.symbol == symbol.upper()))
    if not company:
        return None
    return _company_coverage(session, company, as_of_date or datetime.now(UTC).date())


def _company_coverage(session: Session, company: Company, coverage_date: date) -> CompanyCoverageRead:
    price = _price_coverage(session, company, coverage_date)
    fy_report = _fy_coverage(session, company, coverage_date)
    current_periods = _period_coverages(session, company, coverage_date)
    dividend = _dividend_coverage(session, company, coverage_date)
    uploaded_reports = _uploaded_report_coverage(session, company)

    items = [price, fy_report, dividend, uploaded_reports, *current_periods]
    coverage_score = round(sum(_item_score(item) for item in items) / len(items)) if items else 0
    next_actions = _next_actions(company, price, fy_report, current_periods, dividend, uploaded_reports)

    return CompanyCoverageRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        overall_status=_overall_status(coverage_score, next_actions),
        coverage_score=coverage_score,
        next_actions=next_actions,
        price=price,
        fy_report=fy_report,
        current_periods=current_periods,
        dividend=dividend,
        uploaded_reports=uploaded_reports,
    )


def _price_coverage(session: Session, company: Company, coverage_date: date) -> CoverageItem:
    price = session.scalar(
        select(Price)
        .where(Price.company_id == company.id, Price.trade_date <= coverage_date)
        .order_by(desc(Price.trade_date))
        .limit(1)
    )
    if not price:
        return CoverageItem(label="Latest price", status="missing", detail="No price imported")

    age_days = (coverage_date - price.trade_date).days
    if age_days <= 7 and not price.reviewed:
        status = "unreviewed"
    elif age_days <= 7:
        status = "done"
    elif age_days <= 31:
        status = "stale"
    else:
        status = "missing"

    return CoverageItem(
        label="Latest price",
        status=status,
        detail=f"{price.trade_date}: close {price.close_price}",
        reviewed=price.reviewed,
    )


def _fy_coverage(session: Session, company: Company, coverage_date: date) -> CoverageItem:
    prior_year = coverage_date.year - 1
    fy = session.scalar(
        select(FinancialStatement)
        .where(
            FinancialStatement.company_id == company.id,
            FinancialStatement.period_type == "FY",
            FinancialStatement.period_end >= date(prior_year, 1, 1),
            FinancialStatement.period_end <= coverage_date,
        )
        .order_by(desc(FinancialStatement.period_end))
        .limit(1)
    )
    if not fy:
        return CoverageItem(
            label=f"FY {prior_year}",
            status="missing",
            detail=f"No FY {prior_year} financial statement",
        )
    return CoverageItem(
        label=f"FY {fy.period_end.year}",
        status="done" if fy.reviewed else "unreviewed",
        detail=f"{fy.period_end}: revenue {fy.revenue}, PAT {fy.profit_after_tax}",
        reviewed=fy.reviewed,
    )


def _period_coverages(
    session: Session,
    company: Company,
    coverage_date: date,
) -> list[CoverageItem]:
    periods = _expected_periods(coverage_date)
    items: list[CoverageItem] = []
    for label, period_end in periods:
        statement = session.scalar(
            select(FinancialStatement)
            .where(
                FinancialStatement.company_id == company.id,
                FinancialStatement.period_type == label,
                FinancialStatement.period_end == period_end,
            )
            .limit(1)
        )
        if not statement:
            items.append(
                CoverageItem(
                    label=f"{label} {coverage_date.year}",
                    status="missing",
                    detail=f"No {label} statement for period ended {period_end}",
                )
            )
            continue
        items.append(
            CoverageItem(
                label=f"{label} {coverage_date.year}",
                status="done" if statement.reviewed else "unreviewed",
                detail=f"{statement.period_end}: revenue {statement.revenue}, PAT {statement.profit_after_tax}",
                reviewed=statement.reviewed,
            )
        )
    return items


def _dividend_coverage(session: Session, company: Company, coverage_date: date) -> CoverageItem:
    since = coverage_date - timedelta(days=540)
    dividend = session.scalar(
        select(Dividend)
        .where(
            Dividend.company_id == company.id,
            Dividend.payment_date.is_not(None),
            Dividend.payment_date >= since,
            Dividend.payment_date <= coverage_date,
        )
        .order_by(desc(Dividend.payment_date))
        .limit(1)
    )
    if not dividend:
        return CoverageItem(
            label="Dividend",
            status="missing",
            detail="No paid dividend imported in the last 18 months",
        )
    return CoverageItem(
        label="Dividend",
        status="done" if dividend.reviewed else "unreviewed",
        detail=f"{dividend.payment_date}: {dividend.amount_per_share} per share",
        reviewed=dividend.reviewed,
    )


def _uploaded_report_coverage(session: Session, company: Company) -> CoverageItem:
    count = session.scalar(
        select(func.count(UploadedReport.id)).where(UploadedReport.company_id == company.id)
    )
    latest = session.scalar(
        select(UploadedReport)
        .where(UploadedReport.company_id == company.id)
        .order_by(desc(UploadedReport.created_at))
        .limit(1)
    )
    if not count:
        return CoverageItem(
            label="Uploaded reports",
            status="missing",
            detail="No report files uploaded",
        )
    return CoverageItem(
        label="Uploaded reports",
        status="done" if latest and latest.status == "text_extracted" else "partial",
        detail=f"{count} uploaded; latest status {latest.status if latest else 'unknown'}",
    )


def _expected_periods(coverage_date: date) -> list[tuple[str, date]]:
    year = coverage_date.year
    candidates = [
        ("Q1", date(year, 3, 31)),
        ("Q2", date(year, 6, 30)),
        ("Q3", date(year, 9, 30)),
        ("Q4", date(year, 12, 31)),
    ]
    return [(label, period_end) for label, period_end in candidates if period_end <= coverage_date]


def _item_score(item: CoverageItem) -> int:
    scores = {
        "done": 100,
        "partial": 65,
        "unreviewed": 50,
        "stale": 40,
        "missing": 0,
    }
    return scores.get(item.status, 0)


def _overall_status(coverage_score: int, next_actions: list[str]) -> str:
    if coverage_score >= 85 and not next_actions:
        return "Complete"
    if coverage_score >= 65:
        return "Mostly ready"
    if coverage_score >= 35:
        return "Partial"
    return "Incomplete"


def _next_actions(
    company: Company,
    price: CoverageItem,
    fy_report: CoverageItem,
    current_periods: list[CoverageItem],
    dividend: CoverageItem,
    uploaded_reports: CoverageItem,
) -> list[str]:
    actions: list[str] = []
    if price.status in {"missing", "stale"}:
        actions.append(f"Import latest price for {company.symbol}")
    elif price.status == "unreviewed":
        actions.append(f"Review latest price for {company.symbol}")
    if fy_report.status == "missing":
        actions.append(f"Collect latest FY annual report data for {company.symbol}")
    if fy_report.status == "unreviewed":
        actions.append(f"Review latest FY annual report data for {company.symbol}")
    for period in current_periods:
        if period.status == "missing":
            actions.append(f"Collect {period.label} data for {company.symbol}")
        elif period.status == "unreviewed":
            actions.append(f"Review {period.label} data for {company.symbol}")
    if dividend.status == "missing":
        actions.append(f"Check latest dividend/corporate action data for {company.symbol}")
    elif dividend.status == "unreviewed":
        actions.append(f"Review latest dividend record for {company.symbol}")
    if uploaded_reports.status == "missing":
        actions.append(f"Upload at least one source report for {company.symbol}")
    elif uploaded_reports.status == "partial":
        actions.append(f"Extract text from latest uploaded report for {company.symbol}")
    return actions
