from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import Company, CompanyScore, InvestmentNote
from ngx_research.schemas import (
    InvestmentBriefRead,
    InvestmentNoteRead,
    PortfolioPositionRead,
    ScoreRead,
)
from ngx_research.services.portfolio import portfolio_summary

ALLOWED_DECISIONS = {
    "BUY",
    "WATCH",
    "HOLD",
    "SELL",
    "AVOID",
    "RESEARCH",
}


def create_note(
    session: Session,
    company: Company,
    thesis: str,
    risks: str | None,
    decision: str | None,
    note_date: date | None,
) -> InvestmentNoteRead:
    normalized_decision = _normalize_decision(decision)
    note = InvestmentNote(
        company_id=company.id,
        note_date=note_date or datetime.now(UTC).date(),
        thesis=thesis.strip(),
        risks=risks.strip() if risks else None,
        decision=normalized_decision,
    )
    if not note.thesis:
        raise ValueError("thesis is required")

    session.add(note)
    session.commit()
    session.refresh(note)
    return _note_read(session, note, company)


def list_notes(
    session: Session,
    symbol: str | None = None,
    limit: int = 100,
) -> list[InvestmentNoteRead]:
    statement = (
        select(InvestmentNote, Company)
        .join(Company, Company.id == InvestmentNote.company_id)
        .order_by(desc(InvestmentNote.note_date), desc(InvestmentNote.id))
        .limit(limit)
    )
    if symbol:
        statement = statement.where(Company.symbol == symbol.upper())

    return [_note_read(session, note, company) for note, company in session.execute(statement)]


def company_brief(session: Session, company: Company) -> InvestmentBriefRead:
    latest_note = session.scalar(
        select(InvestmentNote)
        .where(InvestmentNote.company_id == company.id)
        .order_by(desc(InvestmentNote.note_date), desc(InvestmentNote.id))
        .limit(1)
    )
    note_count = session.scalar(
        select(func.count(InvestmentNote.id)).where(InvestmentNote.company_id == company.id)
    )
    latest_score = _latest_score_read(session, company.id)
    position = _position_for_symbol(session, company.symbol)
    latest_note_read = _note_read(session, latest_note, company) if latest_note else None
    return InvestmentBriefRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        latest_note=latest_note_read,
        note_count=note_count or 0,
        latest_score=latest_score,
        portfolio_position=position,
        checklist=_checklist(latest_note_read, latest_score, position),
    )


def _note_read(session: Session, note: InvestmentNote, company: Company) -> InvestmentNoteRead:
    score = _latest_score(session, company.id)
    position = _position_for_symbol(session, company.symbol)
    return InvestmentNoteRead(
        id=note.id,
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        note_date=note.note_date,
        thesis=note.thesis,
        risks=note.risks,
        decision=note.decision,
        latest_score=score.overall_score if score else None,
        latest_status=score.status if score else None,
        portfolio_quantity=position.quantity if position else Decimal(0),
        portfolio_weight=position.portfolio_weight if position else Decimal(0),
        portfolio_unrealized_gain_loss_percent=(
            position.unrealized_gain_loss_percent if position else None
        ),
    )


def _latest_score(session: Session, company_id: int) -> CompanyScore | None:
    return session.scalar(
        select(CompanyScore)
        .where(CompanyScore.company_id == company_id)
        .order_by(desc(CompanyScore.as_of_date), desc(CompanyScore.id))
        .limit(1)
    )


def _latest_score_read(session: Session, company_id: int) -> ScoreRead | None:
    row = session.execute(
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
        .join(CompanyScore, CompanyScore.company_id == Company.id)
        .where(Company.id == company_id)
        .order_by(desc(CompanyScore.as_of_date), desc(CompanyScore.id))
        .limit(1)
    ).first()
    return ScoreRead.model_validate(row._mapping) if row else None


def _position_for_symbol(session: Session, symbol: str) -> PortfolioPositionRead | None:
    summary = portfolio_summary(session)
    return next((position for position in summary.positions if position.symbol == symbol), None)


def _normalize_decision(decision: str | None) -> str | None:
    if not decision:
        return None
    normalized = decision.upper()
    if normalized not in ALLOWED_DECISIONS:
        allowed = ", ".join(sorted(ALLOWED_DECISIONS))
        raise ValueError(f"decision must be one of: {allowed}")
    return normalized


def _checklist(
    note: InvestmentNoteRead | None,
    score: ScoreRead | None,
    position: PortfolioPositionRead | None,
) -> list[str]:
    items: list[str] = []
    if not score:
        items.append("Run a market scan before relying on this decision.")
    elif score.status in {"Insufficient data", "Needs source review"}:
        items.append(f"Resolve scanner status: {score.status}.")
    if not note:
        items.append("Write a thesis before buying or increasing exposure.")
    elif not note.risks:
        items.append("Add explicit risks before acting on the thesis.")
    if position and position.portfolio_weight and position.portfolio_weight > Decimal(30):
        items.append("Position is above 30% of portfolio value; review concentration risk.")
    if not items:
        items.append("Decision record has thesis, risks, scanner context, and portfolio context.")
    return items
