from datetime import UTC, datetime

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyScore,
    Dividend,
    FinancialStatement,
    Price,
    ScanResult,
    ScanRun,
)
from ngx_research.schemas import (
    PendingReviewSummaryRead,
    ResearchDigestRead,
    ScanRunRead,
    ScoreRead,
)
from ngx_research.services.alerts import list_alert_events
from ngx_research.services.dividend_engine import dividend_candidates
from ngx_research.services.portfolio import portfolio_summary
from ngx_research.services.watchlists import list_watchlists


def build_research_digest(session: Session, limit: int = 10) -> ResearchDigestRead:
    portfolio = portfolio_summary(session)
    pending_review = _pending_review_summary(session)
    open_alerts = list_alert_events(session, status="open", limit=limit)
    latest_scan = _latest_scan(session, limit=limit)
    candidates = dividend_candidates(session, limit=limit)
    watchlists = list_watchlists(session)
    return ResearchDigestRead(
        generated_date=datetime.now(UTC).date(),
        portfolio=portfolio,
        pending_review=pending_review,
        open_alerts=open_alerts,
        latest_scan=latest_scan,
        dividend_candidates=candidates,
        watchlists=watchlists,
        next_actions=_next_actions(
            portfolio_warnings=portfolio.warnings,
            pending_review=pending_review,
            open_alert_count=len(open_alerts),
            scan=latest_scan,
            watchlist_count=len(watchlists),
        ),
    )


def _pending_review_summary(session: Session) -> PendingReviewSummaryRead:
    price_count = _unreviewed_count(session, Price)
    financial_count = _unreviewed_count(session, FinancialStatement)
    dividend_count = _unreviewed_count(session, Dividend)
    return PendingReviewSummaryRead(
        prices=price_count,
        financial_statements=financial_count,
        dividends=dividend_count,
        total=price_count + financial_count + dividend_count,
    )


def _unreviewed_count(session: Session, model) -> int:
    return session.scalar(select(func.count(model.id)).where(model.reviewed.is_(False))) or 0


def _latest_scan(session: Session, limit: int) -> ScanRunRead:
    scan_run = session.scalar(select(ScanRun).order_by(desc(ScanRun.created_at)).limit(1))
    if not scan_run:
        return ScanRunRead(scan_run_id=0, as_of_date=datetime.now(UTC).date(), results=[])

    rows = session.execute(
        select(
            Company.symbol,
            Company.name,
            Company.sector,
            CompanyScore.as_of_date,
            CompanyScore.quality_score,
            CompanyScore.growth_score,
            CompanyScore.valuation_score,
            CompanyScore.dividend_score,
            CompanyScore.risk_score,
            CompanyScore.overall_score,
            CompanyScore.status,
            CompanyScore.reasons,
            CompanyScore.risks,
        )
        .join(ScanResult, ScanResult.score_id == CompanyScore.id)
        .join(Company, Company.id == CompanyScore.company_id)
        .where(ScanResult.scan_run_id == scan_run.id)
        .order_by(ScanResult.rank)
        .limit(limit)
    )
    return ScanRunRead(
        scan_run_id=scan_run.id,
        as_of_date=scan_run.as_of_date,
        results=[ScoreRead.model_validate(row._mapping) for row in rows],
    )


def _next_actions(
    portfolio_warnings: list[str],
    pending_review: PendingReviewSummaryRead,
    open_alert_count: int,
    scan: ScanRunRead,
    watchlist_count: int,
) -> list[str]:
    actions: list[str] = []
    if pending_review.total:
        actions.append(f"Review {pending_review.total} unapproved data records before relying on scores.")
    if open_alert_count:
        actions.append(f"Resolve {open_alert_count} open alert event(s).")
    actions.extend(portfolio_warnings[:3])
    needs_review = [item.symbol for item in scan.results if item.status == "Needs source review"]
    if needs_review:
        actions.append(f"Source-review scan leaders: {', '.join(needs_review[:5])}.")
    if not scan.results:
        actions.append("Run a market scan to refresh opportunity rankings.")
    if watchlist_count == 0:
        actions.append("Create at least one watchlist for focused research.")
    if not actions:
        actions.append("No urgent research actions. Continue monitoring prices, reports, and alerts.")
    return actions
