from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ngx_research.models import Company, CompanyScore, Watchlist, WatchlistItem
from ngx_research.schemas import (
    WatchlistActionRead,
    WatchlistDetailRead,
    WatchlistMemberRead,
    WatchlistRead,
)
from ngx_research.services.portfolio import portfolio_summary


def create_watchlist(session: Session, name: str) -> WatchlistRead:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("watchlist name is required")

    watchlist = Watchlist(name=clean_name)
    session.add(watchlist)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError("watchlist already exists") from exc
    session.refresh(watchlist)
    return WatchlistRead(id=watchlist.id, name=watchlist.name, member_count=0)


def list_watchlists(session: Session) -> list[WatchlistRead]:
    rows = session.execute(
        select(
            Watchlist.id,
            Watchlist.name,
            func.count(WatchlistItem.id).label("member_count"),
        )
        .outerjoin(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
        .group_by(Watchlist.id)
        .order_by(Watchlist.name)
    )
    return [WatchlistRead.model_validate(row._mapping) for row in rows]


def watchlist_detail(session: Session, watchlist: Watchlist) -> WatchlistDetailRead:
    rows = session.execute(
        select(Company)
        .join(WatchlistItem, WatchlistItem.company_id == Company.id)
        .where(WatchlistItem.watchlist_id == watchlist.id)
        .order_by(Company.symbol)
    )
    score_by_company = _latest_scores(session)
    position_by_symbol = {position.symbol: position for position in portfolio_summary(session).positions}
    members = []
    for company in rows.scalars():
        score = score_by_company.get(company.id)
        position = position_by_symbol.get(company.symbol)
        members.append(
            WatchlistMemberRead(
                symbol=company.symbol,
                name=company.name,
                sector=company.sector,
                latest_score=score.overall_score if score else None,
                latest_status=score.status if score else None,
                portfolio_quantity=position.quantity if position else Decimal(0),
                portfolio_weight=position.portfolio_weight if position else Decimal(0),
            )
        )
    return WatchlistDetailRead(id=watchlist.id, name=watchlist.name, members=members)


def add_to_watchlist(
    session: Session,
    watchlist: Watchlist,
    company: Company,
) -> WatchlistActionRead:
    item = WatchlistItem(watchlist_id=watchlist.id, company_id=company.id)
    session.add(item)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"{company.symbol} is already in {watchlist.name}") from exc
    return WatchlistActionRead(watchlist_id=watchlist.id, symbol=company.symbol, action="added")


def remove_from_watchlist(
    session: Session,
    watchlist: Watchlist,
    company: Company,
) -> WatchlistActionRead:
    item = session.scalar(
        select(WatchlistItem)
        .where(WatchlistItem.watchlist_id == watchlist.id)
        .where(WatchlistItem.company_id == company.id)
    )
    if not item:
        raise ValueError(f"{company.symbol} is not in {watchlist.name}")
    session.delete(item)
    session.commit()
    return WatchlistActionRead(watchlist_id=watchlist.id, symbol=company.symbol, action="removed")


def _latest_scores(session: Session) -> dict[int, CompanyScore]:
    scores: dict[int, CompanyScore] = {}
    rows = session.scalars(
        select(CompanyScore).order_by(
            CompanyScore.company_id,
            desc(CompanyScore.as_of_date),
            desc(CompanyScore.id),
        )
    )
    for score in rows:
        scores.setdefault(score.company_id, score)
    return scores
