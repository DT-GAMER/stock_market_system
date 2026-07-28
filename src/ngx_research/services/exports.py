import csv
from io import StringIO
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ngx_research.models import Company, CompanyScore, Watchlist
from ngx_research.services.alerts import list_alert_events
from ngx_research.services.dividend_engine import dividend_candidates
from ngx_research.services.portfolio import portfolio_summary
from ngx_research.services.research_digest import build_research_digest
from ngx_research.services.watchlists import list_watchlists, watchlist_detail

EXPORT_DATASETS = {
    "portfolio_positions",
    "alert_events",
    "latest_scores",
    "dividend_candidates",
    "watchlists",
    "digest_actions",
}


def export_dataset_csv(session: Session, dataset: str, limit: int = 100) -> tuple[str, str]:
    normalized = dataset.lower()
    if normalized not in EXPORT_DATASETS:
        allowed = ", ".join(sorted(EXPORT_DATASETS))
        raise ValueError(f"dataset must be one of: {allowed}")

    rows = _dataset_rows(session, normalized, limit)
    filename = f"{normalized}.csv"
    return filename, _rows_to_csv(rows)


def _dataset_rows(session: Session, dataset: str, limit: int) -> list[dict[str, Any]]:
    if dataset == "portfolio_positions":
        return [position.model_dump() for position in portfolio_summary(session).positions[:limit]]
    if dataset == "alert_events":
        return [event.model_dump() for event in list_alert_events(session, limit=limit)]
    if dataset == "latest_scores":
        return _latest_score_rows(session, limit)
    if dataset == "dividend_candidates":
        return [candidate.model_dump() for candidate in dividend_candidates(session, limit=limit)]
    if dataset == "watchlists":
        return _watchlist_rows(session, limit)
    if dataset == "digest_actions":
        digest = build_research_digest(session, limit=limit)
        return [
            {"rank": index, "action": action}
            for index, action in enumerate(digest.next_actions, start=1)
        ]
    return []


def _latest_score_rows(session: Session, limit: int) -> list[dict[str, Any]]:
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
        .join(CompanyScore, CompanyScore.company_id == Company.id)
        .order_by(desc(CompanyScore.as_of_date), desc(CompanyScore.overall_score))
        .limit(limit)
    )
    return [dict(row._mapping) for row in rows]


def _watchlist_rows(session: Session, limit: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for watchlist in list_watchlists(session):
        db_watchlist = session.get(Watchlist, watchlist.id)
        if not db_watchlist:
            continue
        detail = watchlist_detail(session, db_watchlist)
        for member in detail.members:
            output.append(
                {
                    "watchlist_id": detail.id,
                    "watchlist_name": detail.name,
                    **member.model_dump(),
                }
            )
            if len(output) >= limit:
                return output
    return output


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = StringIO()
    if not rows:
        return ""
    fieldnames = list(rows[0])
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(value) for key, value in row.items()})
    return buffer.getvalue()


def _csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return " | ".join(str(item) for item in value)
    return value
