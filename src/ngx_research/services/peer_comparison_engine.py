from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from threading import Lock

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyIntelligenceSnapshot,
    CompanyPeerComparisonSnapshot,
    CompanyValuationSnapshot,
)
from ngx_research.schemas import (
    CompanyPeerComparisonRead,
    PeerCategoryWinnerRead,
    PeerComparisonRowRead,
    PeerComparisonRunRead,
    PeerMetricComparisonRead,
)

HUNDRED = Decimal(100)
_PEER_RUN_LOCK = Lock()


@dataclass(frozen=True)
class PeerInput:
    company: Company
    intelligence: CompanyIntelligenceSnapshot
    valuation: CompanyValuationSnapshot | None


@dataclass(frozen=True)
class PeerRow:
    symbol: str
    name: str
    sector: str | None
    final_label: str
    stock_types: list[str]
    peer_score: Decimal
    sector_rank: int | None
    overall_score: Decimal
    business_quality_score: Decimal
    growth_score: Decimal
    valuation_score: Decimal
    dividend_score: Decimal
    financial_risk_score: Decimal
    liquidity_score: Decimal
    data_confidence_score: Decimal
    latest_price: Decimal | None
    pe_ratio: Decimal | None
    roe: Decimal | None
    profit_margin: Decimal | None
    dividend_yield: Decimal | None
    margin_of_safety_percent: Decimal | None
    valuation_label: str | None


def run_peer_comparison_engine(
    session: Session,
    as_of_date: date | None = None,
    limit: int | None = None,
) -> PeerComparisonRunRead:
    with _PEER_RUN_LOCK:
        return _run_peer_comparison_engine_locked(session, as_of_date=as_of_date, limit=limit)


def _run_peer_comparison_engine_locked(
    session: Session,
    as_of_date: date | None = None,
    limit: int | None = None,
) -> PeerComparisonRunRead:
    comparison_date = as_of_date or _latest_intelligence_date(session) or datetime.now(UTC).date()
    inputs = _peer_inputs(session, comparison_date)
    grouped = _group_by_sector(inputs)

    session.execute(
        delete(CompanyPeerComparisonSnapshot)
        .where(CompanyPeerComparisonSnapshot.as_of_date == comparison_date)
        .execution_options(synchronize_session=False)
    )
    session.flush()

    generated: list[CompanyPeerComparisonSnapshot] = []
    for sector_inputs in grouped.values():
        ranked_rows = _ranked_peer_rows(sector_inputs)
        for item in sector_inputs:
            snapshot = _build_snapshot(item, ranked_rows)
            session.add(snapshot)
            generated.append(snapshot)
    session.commit()
    return PeerComparisonRunRead(
        as_of_date=comparison_date,
        generated=len(generated),
        comparisons=latest_peer_comparisons(session, limit=limit or 100),
    )


def latest_peer_comparisons(
    session: Session,
    limit: int = 100,
) -> list[CompanyPeerComparisonRead]:
    latest_date = session.scalar(select(func.max(CompanyPeerComparisonSnapshot.as_of_date)))
    if latest_date is None:
        return []
    rows = session.execute(
        select(CompanyPeerComparisonSnapshot, Company)
        .join(Company, Company.id == CompanyPeerComparisonSnapshot.company_id)
        .where(CompanyPeerComparisonSnapshot.as_of_date == latest_date)
        .order_by(CompanyPeerComparisonSnapshot.sector_rank, Company.symbol)
        .limit(limit)
    )
    return [_comparison_read(snapshot, company) for snapshot, company in rows]


def company_peer_comparison(session: Session, symbol: str) -> CompanyPeerComparisonRead:
    normalized = symbol.strip().upper()
    row = session.execute(
        select(CompanyPeerComparisonSnapshot, Company)
        .join(Company, Company.id == CompanyPeerComparisonSnapshot.company_id)
        .where(Company.symbol == normalized)
        .order_by(
            desc(CompanyPeerComparisonSnapshot.as_of_date),
            desc(CompanyPeerComparisonSnapshot.id),
        )
        .limit(1)
    ).first()
    if not row:
        raise ValueError(
            f"No peer comparison snapshot found for {normalized}. "
            "Run POST /comparison/run after syncing intelligence and valuation."
        )
    snapshot, company = row
    return _comparison_read(snapshot, company)


def latest_company_peer_comparison_snapshot(
    session: Session,
    company_id: int,
) -> tuple[CompanyPeerComparisonSnapshot, Company] | None:
    return session.execute(
        select(CompanyPeerComparisonSnapshot, Company)
        .join(Company, Company.id == CompanyPeerComparisonSnapshot.company_id)
        .where(CompanyPeerComparisonSnapshot.company_id == company_id)
        .order_by(
            desc(CompanyPeerComparisonSnapshot.as_of_date),
            desc(CompanyPeerComparisonSnapshot.id),
        )
        .limit(1)
    ).first()


def peer_comparison_snapshot_read(
    snapshot: CompanyPeerComparisonSnapshot,
    company: Company,
) -> CompanyPeerComparisonRead:
    return _comparison_read(snapshot, company)


def _peer_inputs(session: Session, comparison_date: date) -> list[PeerInput]:
    rows = session.execute(
        select(CompanyIntelligenceSnapshot, Company)
        .join(Company, Company.id == CompanyIntelligenceSnapshot.company_id)
        .where(
            CompanyIntelligenceSnapshot.as_of_date == comparison_date,
            Company.is_active.is_(True),
        )
        .order_by(Company.symbol, desc(CompanyIntelligenceSnapshot.id))
    )
    valuations = {
        valuation.company_id: valuation
        for valuation in session.scalars(
            select(CompanyValuationSnapshot).where(
                CompanyValuationSnapshot.as_of_date == comparison_date
            )
        )
    }
    by_company: dict[int, PeerInput] = {}
    for intelligence, company in rows:
        by_company.setdefault(
            company.id,
            PeerInput(company=company, intelligence=intelligence, valuation=valuations.get(company.id)),
        )
    return list(by_company.values())


def _group_by_sector(inputs: list[PeerInput]) -> dict[str, list[PeerInput]]:
    grouped: dict[str, list[PeerInput]] = {}
    for item in inputs:
        grouped.setdefault(_sector_key(item.company.sector), []).append(item)
    return grouped


def _ranked_peer_rows(inputs: list[PeerInput]) -> list[PeerRow]:
    rows = [_peer_row(item, sector_rank=None) for item in inputs]
    ranked = sorted(rows, key=lambda row: (row.peer_score, row.symbol), reverse=True)
    return [
        PeerRow(
            **{
                **row.__dict__,
                "sector_rank": index,
            }
        )
        for index, row in enumerate(ranked, start=1)
    ]


def _build_snapshot(
    item: PeerInput,
    ranked_rows: list[PeerRow],
) -> CompanyPeerComparisonSnapshot:
    company = item.company
    intelligence = item.intelligence
    valuation = item.valuation
    company_row = next(row for row in ranked_rows if row.symbol == company.symbol)
    peer_count = len(ranked_rows)
    sector_percentile = _sector_percentile(company_row.sector_rank, peer_count)
    best = ranked_rows[0] if ranked_rows else None
    metric_comparisons = _metric_comparisons(company_row, ranked_rows)
    category_winners = _category_winners(ranked_rows)
    strengths = _strengths(company_row, metric_comparisons, peer_count)
    weaknesses = _weaknesses(company_row, metric_comparisons, peer_count)
    warnings = _warnings(item, peer_count, company_row)
    return CompanyPeerComparisonSnapshot(
        company_id=company.id,
        intelligence_snapshot_id=intelligence.id,
        valuation_snapshot_id=valuation.id if valuation else None,
        as_of_date=intelligence.as_of_date,
        sector=company.sector,
        peer_count=peer_count,
        sector_rank=company_row.sector_rank,
        sector_percentile=sector_percentile,
        comparison_label=_comparison_label(company_row.sector_rank, peer_count, sector_percentile),
        best_overall_peer_symbol=best.symbol if best else None,
        best_overall_peer_name=best.name if best else None,
        category_winners=[_category_json(item) for item in category_winners],
        metric_comparisons=[_metric_json(metric) for metric in metric_comparisons],
        peer_rows=[_row_json(row) for row in _display_rows(ranked_rows, company.symbol)],
        strengths=strengths,
        weaknesses=weaknesses,
        reasons=_reasons(company_row, ranked_rows, strengths),
        warnings=warnings,
        next_actions=_next_actions(company_row, weaknesses, warnings),
        metrics=_snapshot_metrics(company_row),
        source_summary={
            "intelligence_snapshot_id": intelligence.id,
            "valuation_snapshot_id": valuation.id if valuation else None,
            "sector": company.sector,
            "peer_count": peer_count,
        },
    )


def _peer_row(item: PeerInput, sector_rank: int | None) -> PeerRow:
    metrics = item.intelligence.metrics or {}
    margin = item.valuation.margin_of_safety_percent if item.valuation else None
    peer_score = _peer_score(item.intelligence, margin)
    return PeerRow(
        symbol=item.company.symbol,
        name=item.company.name,
        sector=item.company.sector,
        final_label=item.intelligence.final_label,
        stock_types=item.intelligence.stock_types,
        peer_score=peer_score,
        sector_rank=sector_rank,
        overall_score=item.intelligence.overall_score,
        business_quality_score=item.intelligence.business_quality_score,
        growth_score=item.intelligence.growth_score,
        valuation_score=item.intelligence.valuation_score,
        dividend_score=item.intelligence.dividend_score,
        financial_risk_score=item.intelligence.financial_risk_score,
        liquidity_score=item.intelligence.liquidity_score,
        data_confidence_score=item.intelligence.data_confidence_score,
        latest_price=_decimal(metrics.get("latest_price")),
        pe_ratio=_decimal(metrics.get("pe_ratio")),
        roe=_decimal(metrics.get("roe")),
        profit_margin=_decimal(metrics.get("profit_margin")),
        dividend_yield=_decimal(metrics.get("dividend_yield")),
        margin_of_safety_percent=margin,
        valuation_label=item.valuation.valuation_label if item.valuation else None,
    )


def _peer_score(
    intelligence: CompanyIntelligenceSnapshot,
    margin_of_safety: Decimal | None,
) -> Decimal:
    margin_score = _score_margin_of_safety(margin_of_safety)
    score = (
        intelligence.overall_score * Decimal("0.50")
        + intelligence.business_quality_score * Decimal("0.15")
        + intelligence.valuation_score * Decimal("0.10")
        + margin_score * Decimal("0.10")
        + intelligence.liquidity_score * Decimal("0.08")
        + intelligence.data_confidence_score * Decimal("0.07")
    )
    return score.quantize(Decimal("0.01"))


def _score_margin_of_safety(value: Decimal | None) -> Decimal:
    if value is None:
        return Decimal(35)
    if value >= Decimal(30):
        return HUNDRED
    if value <= Decimal(-30):
        return Decimal(0)
    return ((value + Decimal(30)) / Decimal(60) * HUNDRED).quantize(Decimal("0.01"))


def _metric_comparisons(
    company_row: PeerRow,
    rows: list[PeerRow],
) -> list[PeerMetricComparisonRead]:
    specs = [
        ("Peer Score", "peer_score"),
        ("Business Quality", "business_quality_score"),
        ("Growth Strength", "growth_score"),
        ("Valuation Score", "valuation_score"),
        ("Margin of Safety", "margin_of_safety_percent"),
        ("Dividend Strength", "dividend_score"),
        ("Financial Risk Safety", "financial_risk_score"),
        ("Liquidity Safety", "liquidity_score"),
        ("Data Confidence", "data_confidence_score"),
        ("ROE", "roe"),
        ("Profit Margin", "profit_margin"),
        ("Dividend Yield", "dividend_yield"),
    ]
    comparisons: list[PeerMetricComparisonRead] = []
    for label, attr in specs:
        values = [(row, getattr(row, attr)) for row in rows if getattr(row, attr) is not None]
        company_value = getattr(company_row, attr)
        if not values or company_value is None:
            comparisons.append(
                PeerMetricComparisonRead(
                    metric=label,
                    company_value=company_value,
                    sector_median=None,
                    best_symbol=None,
                    best_value=None,
                    rank=None,
                    peer_count=len(rows),
                    interpretation=f"{label} cannot be compared because company or peer data is missing.",
                )
            )
            continue
        ranked = sorted(values, key=lambda item: item[1], reverse=True)
        rank = next(index for index, (row, _) in enumerate(ranked, start=1) if row.symbol == company_row.symbol)
        sector_median = _median([value for _, value in values])
        best_row, best_value = ranked[0]
        comparisons.append(
            PeerMetricComparisonRead(
                metric=label,
                company_value=company_value,
                sector_median=sector_median,
                best_symbol=best_row.symbol,
                best_value=best_value,
                rank=rank,
                peer_count=len(rows),
                interpretation=_metric_interpretation(label, company_value, sector_median, rank, len(values)),
            )
        )
    return comparisons


def _category_winners(rows: list[PeerRow]) -> list[PeerCategoryWinnerRead]:
    specs = [
        ("Overall Opportunity", "peer_score"),
        ("Business Quality", "business_quality_score"),
        ("Growth", "growth_score"),
        ("Valuation", "valuation_score"),
        ("Margin of Safety", "margin_of_safety_percent"),
        ("Dividend", "dividend_score"),
        ("Liquidity", "liquidity_score"),
    ]
    winners: list[PeerCategoryWinnerRead] = []
    for category, attr in specs:
        candidates = [row for row in rows if getattr(row, attr) is not None]
        if not candidates:
            winners.append(
                PeerCategoryWinnerRead(
                    category=category,
                    detail=f"No usable {category.lower()} data for this peer group.",
                )
            )
            continue
        winner = max(candidates, key=lambda row: getattr(row, attr))
        winners.append(
            PeerCategoryWinnerRead(
                category=category,
                symbol=winner.symbol,
                name=winner.name,
                value=getattr(winner, attr),
                detail=f"{winner.symbol} leads the sector group on {category.lower()}.",
            )
        )
    return winners


def _strengths(
    company_row: PeerRow,
    comparisons: list[PeerMetricComparisonRead],
    peer_count: int,
) -> list[str]:
    top_cutoff = max(1, (peer_count + 2) // 3)
    strengths: list[str] = []
    for comparison in comparisons:
        if comparison.rank is not None and comparison.rank <= top_cutoff:
            strengths.append(
                f"Top-third sector rank for {comparison.metric.lower()} "
                f"({comparison.rank} of {comparison.peer_count})."
            )
    if company_row.sector_rank == 1:
        strengths.append("Highest composite peer score in the sector group.")
    if company_row.margin_of_safety_percent is not None and company_row.margin_of_safety_percent >= Decimal(10):
        strengths.append("Fair-value estimate shows a positive margin of safety.")
    return _dedupe(strengths) or ["No clear peer-relative strength detected yet."]


def _weaknesses(
    company_row: PeerRow,
    comparisons: list[PeerMetricComparisonRead],
    peer_count: int,
) -> list[str]:
    bottom_cutoff = max(1, peer_count - ((peer_count + 2) // 3) + 1)
    weaknesses: list[str] = []
    for comparison in comparisons:
        if comparison.rank is not None and comparison.rank >= bottom_cutoff and peer_count >= 3:
            weaknesses.append(
                f"Bottom-third sector rank for {comparison.metric.lower()} "
                f"({comparison.rank} of {comparison.peer_count})."
            )
    if company_row.margin_of_safety_percent is not None and company_row.margin_of_safety_percent < Decimal(-10):
        weaknesses.append("Fair-value estimate suggests the stock is expensive relative to current price.")
    if company_row.data_confidence_score < Decimal(60):
        weaknesses.append("Data confidence is lower than preferred for peer ranking.")
    return _dedupe(weaknesses) or ["No major peer-relative weakness detected yet."]


def _warnings(
    item: PeerInput,
    peer_count: int,
    company_row: PeerRow,
) -> list[str]:
    warnings: list[str] = []
    if peer_count < 3:
        warnings.append("Peer set is small; rank may change materially as more sector data improves.")
    if _sector_key(item.company.sector) == "unknown":
        warnings.append("Sector is unknown, so peer comparison may be less meaningful.")
    if item.valuation is None:
        warnings.append("No valuation snapshot is available, so margin-of-safety comparison is missing.")
    if company_row.data_confidence_score < Decimal(60):
        warnings.append("Low data confidence reduces reliability of this peer comparison.")
    return warnings


def _reasons(
    company_row: PeerRow,
    rows: list[PeerRow],
    strengths: list[str],
) -> list[str]:
    reasons = [
        f"{company_row.symbol} ranks {company_row.sector_rank} of {len(rows)} in its sector peer group.",
        f"Composite peer score is {_fmt_score(company_row.peer_score)}.",
    ]
    if strengths and "No clear peer-relative strength detected yet." not in strengths:
        reasons.append("Main peer-relative strength: " + strengths[0])
    if rows and rows[0].symbol != company_row.symbol:
        reasons.append(f"The current sector leader is {rows[0].symbol}.")
    return reasons


def _next_actions(
    company_row: PeerRow,
    weaknesses: list[str],
    warnings: list[str],
) -> list[str]:
    actions = [
        "Compare the top sector peer before buying; do not study this company in isolation.",
        "Check whether the company is leading because of durable quality or only temporary price action.",
    ]
    if weaknesses and "No major peer-relative weakness detected yet." not in weaknesses:
        actions.append("Investigate peer-relative weakness: " + weaknesses[0])
    if warnings:
        actions.append("Improve comparison confidence by resolving warning: " + warnings[0])
    if company_row.sector_rank and company_row.sector_rank > 3:
        actions.append("Ask why a lower-ranked peer is a better use of capital than sector leaders.")
    return _dedupe(actions)


def _comparison_label(
    rank: int | None,
    peer_count: int,
    percentile: Decimal | None,
) -> str:
    if peer_count < 2 or rank is None or percentile is None:
        return "Insufficient Peer Set"
    if rank == 1:
        return "Sector Leader"
    if percentile >= Decimal(75):
        return "Top Sector Contender"
    if percentile >= Decimal(55):
        return "Above Sector Average"
    if percentile >= Decimal(30):
        return "Middle of Pack"
    return "Below Sector Peers"


def _sector_percentile(rank: int | None, peer_count: int) -> Decimal | None:
    if rank is None or peer_count <= 0:
        return None
    if peer_count == 1:
        return HUNDRED
    return ((Decimal(peer_count - rank) / Decimal(peer_count - 1)) * HUNDRED).quantize(
        Decimal("0.0001")
    )


def _display_rows(rows: list[PeerRow], symbol: str) -> list[PeerRow]:
    display = rows[:12]
    if any(row.symbol == symbol for row in display):
        return display
    target = next((row for row in rows if row.symbol == symbol), None)
    return [*display, target] if target else display


def _metric_interpretation(
    label: str,
    company_value: Decimal,
    sector_median: Decimal | None,
    rank: int,
    peer_count: int,
) -> str:
    rank_text = f"rank {rank} of {peer_count}"
    if sector_median is None:
        return f"{label} is {rank_text}; sector median is unavailable."
    if company_value >= sector_median:
        return f"{label} is above or equal to sector median and sits at {rank_text}."
    return f"{label} is below sector median and sits at {rank_text}."


def _snapshot_metrics(row: PeerRow) -> dict:
    return {
        "peer_score": _json_decimal(row.peer_score),
        "overall_score": _json_decimal(row.overall_score),
        "business_quality_score": _json_decimal(row.business_quality_score),
        "growth_score": _json_decimal(row.growth_score),
        "valuation_score": _json_decimal(row.valuation_score),
        "dividend_score": _json_decimal(row.dividend_score),
        "financial_risk_score": _json_decimal(row.financial_risk_score),
        "liquidity_score": _json_decimal(row.liquidity_score),
        "data_confidence_score": _json_decimal(row.data_confidence_score),
        "margin_of_safety_percent": _json_decimal(row.margin_of_safety_percent),
    }


def _comparison_read(
    snapshot: CompanyPeerComparisonSnapshot,
    company: Company,
) -> CompanyPeerComparisonRead:
    return CompanyPeerComparisonRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        as_of_date=snapshot.as_of_date,
        peer_count=snapshot.peer_count,
        sector_rank=snapshot.sector_rank,
        sector_percentile=snapshot.sector_percentile,
        comparison_label=snapshot.comparison_label,
        best_overall_peer_symbol=snapshot.best_overall_peer_symbol,
        best_overall_peer_name=snapshot.best_overall_peer_name,
        category_winners=[_category_read(item) for item in snapshot.category_winners],
        metric_comparisons=[_metric_read(item) for item in snapshot.metric_comparisons],
        peers=[_row_read(item) for item in snapshot.peer_rows],
        strengths=snapshot.strengths,
        weaknesses=snapshot.weaknesses,
        reasons=snapshot.reasons,
        warnings=snapshot.warnings,
        next_actions=snapshot.next_actions,
        metrics=snapshot.metrics,
        source_summary=snapshot.source_summary,
    )


def _category_json(item: PeerCategoryWinnerRead) -> dict:
    return {
        "category": item.category,
        "symbol": item.symbol,
        "name": item.name,
        "value": _json_decimal(item.value),
        "detail": item.detail,
    }


def _metric_json(item: PeerMetricComparisonRead) -> dict:
    return {
        "metric": item.metric,
        "company_value": _json_decimal(item.company_value),
        "sector_median": _json_decimal(item.sector_median),
        "best_symbol": item.best_symbol,
        "best_value": _json_decimal(item.best_value),
        "rank": item.rank,
        "peer_count": item.peer_count,
        "interpretation": item.interpretation,
    }


def _row_json(row: PeerRow) -> dict:
    return {
        "symbol": row.symbol,
        "name": row.name,
        "sector": row.sector,
        "final_label": row.final_label,
        "stock_types": row.stock_types,
        "sector_rank": row.sector_rank,
        "peer_score": _json_decimal(row.peer_score),
        "overall_score": _json_decimal(row.overall_score),
        "business_quality_score": _json_decimal(row.business_quality_score),
        "growth_score": _json_decimal(row.growth_score),
        "valuation_score": _json_decimal(row.valuation_score),
        "dividend_score": _json_decimal(row.dividend_score),
        "financial_risk_score": _json_decimal(row.financial_risk_score),
        "liquidity_score": _json_decimal(row.liquidity_score),
        "data_confidence_score": _json_decimal(row.data_confidence_score),
        "latest_price": _json_decimal(row.latest_price),
        "pe_ratio": _json_decimal(row.pe_ratio),
        "roe": _json_decimal(row.roe),
        "profit_margin": _json_decimal(row.profit_margin),
        "dividend_yield": _json_decimal(row.dividend_yield),
        "margin_of_safety_percent": _json_decimal(row.margin_of_safety_percent),
        "valuation_label": row.valuation_label,
    }


def _category_read(value: dict) -> PeerCategoryWinnerRead:
    return PeerCategoryWinnerRead(
        category=str(value.get("category") or "Unknown"),
        symbol=value.get("symbol"),
        name=value.get("name"),
        value=_decimal(value.get("value")),
        detail=str(value.get("detail") or ""),
    )


def _metric_read(value: dict) -> PeerMetricComparisonRead:
    return PeerMetricComparisonRead(
        metric=str(value.get("metric") or "Unknown"),
        company_value=_decimal(value.get("company_value")),
        sector_median=_decimal(value.get("sector_median")),
        best_symbol=value.get("best_symbol"),
        best_value=_decimal(value.get("best_value")),
        rank=value.get("rank"),
        peer_count=int(value.get("peer_count") or 0),
        interpretation=str(value.get("interpretation") or ""),
    )


def _row_read(value: dict) -> PeerComparisonRowRead:
    return PeerComparisonRowRead(
        symbol=str(value.get("symbol") or ""),
        name=str(value.get("name") or ""),
        sector=value.get("sector"),
        final_label=str(value.get("final_label") or ""),
        stock_types=list(value.get("stock_types") or []),
        sector_rank=value.get("sector_rank"),
        peer_score=_decimal(value.get("peer_score")) or Decimal(0),
        overall_score=_decimal(value.get("overall_score")) or Decimal(0),
        business_quality_score=_decimal(value.get("business_quality_score")) or Decimal(0),
        growth_score=_decimal(value.get("growth_score")) or Decimal(0),
        valuation_score=_decimal(value.get("valuation_score")) or Decimal(0),
        dividend_score=_decimal(value.get("dividend_score")) or Decimal(0),
        financial_risk_score=_decimal(value.get("financial_risk_score")) or Decimal(0),
        liquidity_score=_decimal(value.get("liquidity_score")) or Decimal(0),
        data_confidence_score=_decimal(value.get("data_confidence_score")) or Decimal(0),
        latest_price=_decimal(value.get("latest_price")),
        pe_ratio=_decimal(value.get("pe_ratio")),
        roe=_decimal(value.get("roe")),
        profit_margin=_decimal(value.get("profit_margin")),
        dividend_yield=_decimal(value.get("dividend_yield")),
        margin_of_safety_percent=_decimal(value.get("margin_of_safety_percent")),
        valuation_label=value.get("valuation_label"),
    )


def _latest_intelligence_date(session: Session) -> date | None:
    return session.scalar(select(func.max(CompanyIntelligenceSnapshot.as_of_date)))


def _sector_key(sector: str | None) -> str:
    return (sector or "Unknown").strip().lower()


def _median(values: list[Decimal | None]) -> Decimal | None:
    cleaned = [value for value in values if value is not None]
    return Decimal(str(median(cleaned))).quantize(Decimal("0.0001")) if cleaned else None


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _json_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _fmt_score(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value.quantize(Decimal('0.01'))}/100"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result
