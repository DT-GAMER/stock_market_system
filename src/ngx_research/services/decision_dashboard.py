from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyIntelligenceSnapshot,
    CompanyPeerComparisonSnapshot,
    CompanyValuationSnapshot,
)
from ngx_research.schemas import (
    DecisionDashboardCategoryRead,
    DecisionDashboardOpportunityRead,
    DecisionDashboardRead,
    DecisionDashboardSpotlightRead,
    DecisionDashboardSummaryRead,
    IntelligenceScoreBreakdownRead,
)

HUNDRED = Decimal(100)


def decision_opportunity_dashboard(session: Session, limit: int | None = None) -> DecisionDashboardRead:
    latest_date = session.scalar(select(func.max(CompanyIntelligenceSnapshot.as_of_date)))
    if latest_date is None:
        today = datetime.now(UTC)
        return DecisionDashboardRead(
            as_of_date=today.date(),
            generated_at=today,
            market_summary=DecisionDashboardSummaryRead(
                companies_scanned=0,
                research_candidates=0,
                dividend_candidates=0,
                undervalued_quality=0,
                sector_leaders=0,
                watch_for_entry=0,
                avoid_or_needs_data=0,
            ),
            spotlight_cards=_spotlights(None, None, None),
            categories=[],
            ranked=[],
            data_notes=[
                "No intelligence snapshot exists yet. Sync NGX Pulse data, then run intelligence, valuation, and peer comparison.",
            ],
        )

    rows = _latest_intelligence_rows(session, latest_date)
    valuations = _valuation_map(session, latest_date)
    comparisons = _peer_map(session, latest_date)
    opportunities = [
        _opportunity_read(company, snapshot, valuations.get(company.id), comparisons.get(company.id))
        for snapshot, company in rows
    ]
    ranked = sorted(opportunities, key=_ranking_key, reverse=True)
    visible = ranked if limit is None else ranked[:limit]
    categories = _categories(ranked)
    return DecisionDashboardRead(
        as_of_date=latest_date,
        generated_at=datetime.now(UTC),
        market_summary=_summary(ranked),
        spotlight_cards=_spotlights(_best_overall(ranked), _best_dividend(ranked), _best_value(ranked)),
        categories=categories,
        ranked=visible,
        data_notes=_data_notes(ranked, valuations, comparisons),
    )


def _latest_intelligence_rows(
    session: Session,
    latest_date: date,
) -> list[tuple[CompanyIntelligenceSnapshot, Company]]:
    result = session.execute(
        select(CompanyIntelligenceSnapshot, Company)
        .join(Company, Company.id == CompanyIntelligenceSnapshot.company_id)
        .where(
            CompanyIntelligenceSnapshot.as_of_date == latest_date,
            Company.is_active.is_(True),
        )
        .order_by(Company.symbol, desc(CompanyIntelligenceSnapshot.id))
    )
    by_company: dict[int, tuple[CompanyIntelligenceSnapshot, Company]] = {}
    for snapshot, company in result:
        by_company.setdefault(company.id, (snapshot, company))
    return list(by_company.values())


def _valuation_map(
    session: Session,
    latest_date: date,
) -> dict[int, CompanyValuationSnapshot]:
    result = session.scalars(
        select(CompanyValuationSnapshot)
        .where(CompanyValuationSnapshot.as_of_date == latest_date)
        .order_by(CompanyValuationSnapshot.company_id, desc(CompanyValuationSnapshot.id))
    )
    valuations: dict[int, CompanyValuationSnapshot] = {}
    for valuation in result:
        valuations.setdefault(valuation.company_id, valuation)
    return valuations


def _peer_map(
    session: Session,
    latest_date: date,
) -> dict[int, CompanyPeerComparisonSnapshot]:
    result = session.scalars(
        select(CompanyPeerComparisonSnapshot)
        .where(CompanyPeerComparisonSnapshot.as_of_date == latest_date)
        .order_by(CompanyPeerComparisonSnapshot.company_id, desc(CompanyPeerComparisonSnapshot.id))
    )
    comparisons: dict[int, CompanyPeerComparisonSnapshot] = {}
    for comparison in result:
        comparisons.setdefault(comparison.company_id, comparison)
    return comparisons


def _opportunity_read(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
) -> DecisionDashboardOpportunityRead:
    metrics = snapshot.metrics or {}
    source_summary = snapshot.source_summary or {}
    confidence_score = _confidence_score(snapshot, valuation, comparison)
    answer = _answer(snapshot, valuation, comparison, confidence_score)
    reasons = _dashboard_reasons(snapshot, valuation, comparison)
    risks = _dashboard_risks(snapshot, valuation, comparison)
    next_actions = _dashboard_next_actions(snapshot, valuation, comparison, answer)
    stock_types = snapshot.stock_types or ["Unclassified"]
    return DecisionDashboardOpportunityRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        as_of_date=snapshot.as_of_date,
        answer=answer,
        tone=_tone(answer),
        final_label=snapshot.final_label,
        invest_score=snapshot.overall_score,
        confidence=_confidence_label(confidence_score),
        confidence_score=confidence_score,
        risk_level=_risk_level(snapshot, valuation, comparison),
        suggested_horizon=_suggested_horizon(snapshot),
        latest_price=_decimal(metrics.get("latest_price")),
        latest_price_date=_date(source_summary.get("latest_price_date")),
        fair_value_mid=valuation.fair_value_mid if valuation else None,
        margin_of_safety_percent=valuation.margin_of_safety_percent if valuation else None,
        valuation_label=valuation.valuation_label if valuation else None,
        valuation_confidence=valuation.valuation_confidence if valuation else None,
        peer_rank=comparison.sector_rank if comparison else None,
        peer_count=comparison.peer_count if comparison else None,
        peer_label=comparison.comparison_label if comparison else None,
        best_peer_symbol=comparison.best_overall_peer_symbol if comparison else None,
        stock_types=stock_types,
        category_tags=_category_tags(stock_types, snapshot, valuation, comparison),
        why_attention=reasons[0],
        main_risk=risks[0],
        next_action=next_actions[0],
        reasons=reasons,
        risks=risks,
        next_actions=next_actions,
        missing_data=snapshot.missing_data,
        scores=_score_breakdown(snapshot),
        metrics={
            **metrics,
            "peer_score": comparison.metrics.get("peer_score") if comparison and comparison.metrics else None,
            "peer_rank": comparison.sector_rank if comparison else None,
            "peer_count": comparison.peer_count if comparison else None,
            "fair_value_mid": str(valuation.fair_value_mid) if valuation and valuation.fair_value_mid else None,
            "margin_of_safety_percent": str(valuation.margin_of_safety_percent)
            if valuation and valuation.margin_of_safety_percent is not None
            else None,
        },
    )


def _score_breakdown(snapshot: CompanyIntelligenceSnapshot) -> IntelligenceScoreBreakdownRead:
    return IntelligenceScoreBreakdownRead(
        business_quality=snapshot.business_quality_score,
        growth=snapshot.growth_score,
        valuation=snapshot.valuation_score,
        dividend=snapshot.dividend_score,
        financial_risk=snapshot.financial_risk_score,
        momentum=snapshot.momentum_score,
        liquidity=snapshot.liquidity_score,
        data_confidence=snapshot.data_confidence_score,
        overall=snapshot.overall_score,
    )


def _confidence_score(
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
) -> Decimal:
    score = snapshot.data_confidence_score * Decimal("0.55")
    if valuation:
        score += valuation.confidence_score * Decimal("0.25")
    else:
        score += Decimal(10)
    if comparison and comparison.peer_count >= 3:
        score += Decimal(12)
    elif comparison:
        score += Decimal(7)
    support = min(Decimal(len(snapshot.reasons) * 2), Decimal(8))
    missing_penalty = min(Decimal(len(snapshot.missing_data) * 3), Decimal(18))
    return _clamp(score + support - missing_penalty).quantize(Decimal("0.01"))


def _answer(
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
    confidence_score: Decimal,
) -> str:
    if snapshot.final_label in {"Avoid for Now", "Needs Data"}:
        return "NO - avoid for now" if snapshot.final_label == "Avoid for Now" else "NOT YET - needs more data"
    if snapshot.final_label == "Speculative":
        return "SPECULATIVE - small watchlist position only"
    if confidence_score < Decimal(45):
        return "NOT YET - confidence is too low"
    if (
        valuation
        and valuation.valuation_label in {"Overvalued", "Expensive"}
        and snapshot.final_label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}
    ):
        return "WAIT - good company, but entry price needs discipline"
    if snapshot.final_label == "Good Company, Expensive":
        return "WAIT - good company, expensive today"
    if snapshot.final_label == "Watch for Better Entry":
        return "WAIT - watch for a better entry"
    if snapshot.final_label in {"Top Research Candidate", "Research Now"}:
        if comparison and comparison.sector_rank == 1:
            return "YES - sector-leading research candidate"
        return "YES - research before buying"
    if snapshot.final_label == "Dividend Candidate":
        return "YES - dividend research candidate"
    return "NOT YET - monitor only"


def _dashboard_reasons(
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
) -> list[str]:
    reasons = list(snapshot.reasons)
    if valuation and valuation.margin_of_safety_percent is not None:
        reasons.append(
            f"Valuation engine estimates a {_fmt_percent(valuation.margin_of_safety_percent)} margin of safety."
        )
    if comparison and comparison.sector_rank:
        reasons.append(
            f"Ranks {comparison.sector_rank} of {comparison.peer_count} among sector peers."
        )
    if comparison and comparison.strengths:
        reasons.append(comparison.strengths[0])
    return _dedupe(reasons) or ["No clear positive reason is strong enough yet."]


def _dashboard_risks(
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
) -> list[str]:
    risks = list(snapshot.risks)
    if valuation and valuation.warnings:
        risks.append(valuation.warnings[0])
    if comparison and comparison.weaknesses:
        risks.append(comparison.weaknesses[0])
    if not valuation:
        risks.append("Fair value is not available yet, so entry price cannot be judged properly.")
    if not comparison:
        risks.append("Peer comparison is not available yet, so sector rank is missing.")
    return _dedupe(risks) or ["No major dashboard risk flag yet; still review the full decision card."]


def _dashboard_next_actions(
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
    answer: str,
) -> list[str]:
    actions = list(snapshot.next_actions)
    if answer.startswith("YES"):
        actions.insert(0, "Open the decision card, confirm the thesis, then decide whether to add to watchlist or plan a staged entry.")
    elif answer.startswith("WAIT"):
        actions.insert(0, "Add to watchlist and wait for valuation, price, or earnings to improve.")
    elif answer.startswith("SPECULATIVE"):
        actions.insert(0, "Avoid large allocation; only study with strict risk limits.")
    else:
        actions.insert(0, "Do not buy yet; improve data quality or choose a stronger candidate.")
    if valuation and valuation.missing_data:
        actions.append("Complete valuation data: " + ", ".join(valuation.missing_data[:3]) + ".")
    if comparison and comparison.next_actions:
        actions.append(comparison.next_actions[0])
    return _dedupe(actions)


def _category_tags(
    stock_types: list[str],
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
) -> list[str]:
    tags = list(stock_types[:3])
    if valuation and valuation.valuation_label in {"Deeply Undervalued", "Undervalued"}:
        tags.append("Undervalued")
    if comparison and comparison.sector_rank == 1:
        tags.append("Sector leader")
    if snapshot.final_label:
        tags.append(snapshot.final_label)
    return _dedupe(tags)


def _summary(opportunities: list[DecisionDashboardOpportunityRead]) -> DecisionDashboardSummaryRead:
    return DecisionDashboardSummaryRead(
        companies_scanned=len(opportunities),
        research_candidates=sum(1 for item in opportunities if item.answer.startswith("YES")),
        dividend_candidates=sum(1 for item in opportunities if _is_dividend_candidate(item)),
        undervalued_quality=sum(1 for item in opportunities if _is_undervalued_quality(item)),
        sector_leaders=sum(1 for item in opportunities if item.peer_rank == 1),
        watch_for_entry=sum(1 for item in opportunities if item.answer.startswith("WAIT")),
        avoid_or_needs_data=sum(
            1
            for item in opportunities
            if item.answer.startswith("NO") or item.answer.startswith("NOT YET")
        ),
    )


def _spotlights(
    best_overall: DecisionDashboardOpportunityRead | None,
    best_dividend: DecisionDashboardOpportunityRead | None,
    best_value: DecisionDashboardOpportunityRead | None,
) -> list[DecisionDashboardSpotlightRead]:
    return [
        DecisionDashboardSpotlightRead(
            key="best_overall",
            title="Best Overall Candidate",
            subtitle="Strongest blend of score, confidence, valuation, and peer position.",
            opportunity=best_overall,
        ),
        DecisionDashboardSpotlightRead(
            key="best_dividend",
            title="Best Dividend Candidate",
            subtitle="Income-focused candidate with dividend evidence and business support.",
            opportunity=best_dividend,
        ),
        DecisionDashboardSpotlightRead(
            key="best_value",
            title="Best Value Candidate",
            subtitle="Quality company with the most attractive margin-of-safety signal.",
            opportunity=best_value,
        ),
    ]


def _categories(opportunities: list[DecisionDashboardOpportunityRead]) -> list[DecisionDashboardCategoryRead]:
    return [
        DecisionDashboardCategoryRead(
            key="top_research",
            title="Top Companies Worth Researching",
            summary="Start here. These names have the best current blend of quality, valuation, confidence, and peer context.",
            items=opportunities[:8],
        ),
        DecisionDashboardCategoryRead(
            key="undervalued_quality",
            title="Undervalued Quality Companies",
            summary="Businesses with decent quality scores and a positive fair-value gap.",
            items=[item for item in opportunities if _is_undervalued_quality(item)][:8],
        ),
        DecisionDashboardCategoryRead(
            key="dividend_candidates",
            title="Dividend Candidates",
            summary="Companies with meaningful dividend evidence for income-focused research.",
            items=[item for item in opportunities if _is_dividend_candidate(item)][:8],
        ),
        DecisionDashboardCategoryRead(
            key="sector_leaders",
            title="Sector Leaders",
            summary="Companies currently ranking first inside their own sector peer group.",
            items=[item for item in opportunities if item.peer_rank == 1][:8],
        ),
        DecisionDashboardCategoryRead(
            key="watch_for_entry",
            title="Good To Watch, Not Chase",
            summary="Companies that may be interesting, but need a better entry, stronger evidence, or clearer valuation.",
            items=[item for item in opportunities if item.answer.startswith("WAIT")][:8],
        ),
        DecisionDashboardCategoryRead(
            key="avoid_or_speculative",
            title="Speculative Or Avoid For Now",
            summary="Names where risk, weak evidence, or missing data should slow the investor down.",
            items=[
                item
                for item in opportunities
                if item.answer.startswith("SPECULATIVE")
                or item.answer.startswith("NO")
                or item.answer.startswith("NOT YET")
            ][:8],
        ),
    ]


def _best_overall(
    opportunities: list[DecisionDashboardOpportunityRead],
) -> DecisionDashboardOpportunityRead | None:
    yes = [item for item in opportunities if item.answer.startswith("YES")]
    return (yes or opportunities)[0] if opportunities else None


def _best_dividend(
    opportunities: list[DecisionDashboardOpportunityRead],
) -> DecisionDashboardOpportunityRead | None:
    candidates = [item for item in opportunities if _is_dividend_candidate(item)]
    return max(candidates, key=lambda item: Decimal(str(item.scores.dividend))) if candidates else None


def _best_value(
    opportunities: list[DecisionDashboardOpportunityRead],
) -> DecisionDashboardOpportunityRead | None:
    candidates = [item for item in opportunities if item.margin_of_safety_percent is not None]
    return max(candidates, key=lambda item: item.margin_of_safety_percent or Decimal(-999)) if candidates else None


def _is_dividend_candidate(item: DecisionDashboardOpportunityRead) -> bool:
    dividend_yield = _decimal(item.metrics.get("dividend_yield"))
    return (
        item.final_label == "Dividend Candidate"
        or any("dividend" in stock_type.lower() for stock_type in item.stock_types)
        or (
            dividend_yield is not None
            and dividend_yield >= Decimal(4)
            and Decimal(str(item.scores.dividend)) >= Decimal(40)
        )
    )


def _is_undervalued_quality(item: DecisionDashboardOpportunityRead) -> bool:
    quality = Decimal(str(item.scores.business_quality))
    return (
        item.valuation_label in {"Deeply Undervalued", "Undervalued"}
        and quality >= Decimal(55)
    )


def _data_notes(
    opportunities: list[DecisionDashboardOpportunityRead],
    valuations: dict[int, CompanyValuationSnapshot],
    comparisons: dict[int, CompanyPeerComparisonSnapshot],
) -> list[str]:
    notes: list[str] = []
    if not opportunities:
        notes.append("No companies are available in the latest intelligence snapshot.")
    if opportunities and not valuations:
        notes.append("No valuation snapshots are available yet; run valuation before relying on entry-price signals.")
    elif len(valuations) < len(opportunities):
        notes.append("Some companies do not have valuation snapshots yet.")
    if opportunities and not comparisons:
        notes.append("No peer comparison snapshots are available yet; run peer comparison before relying on sector ranks.")
    elif len(comparisons) < len(opportunities):
        notes.append("Some companies do not have peer comparison snapshots yet.")
    low_confidence = sum(1 for item in opportunities if item.confidence_score < Decimal(45))
    if low_confidence:
        notes.append(f"{low_confidence} companies have low dashboard confidence and should not be used for buy decisions.")
    return notes


def _ranking_key(item: DecisionDashboardOpportunityRead) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    margin = item.margin_of_safety_percent if item.margin_of_safety_percent is not None else Decimal(-50)
    peer_bonus = Decimal(0)
    if item.peer_rank == 1:
        peer_bonus = Decimal(8)
    elif item.peer_rank and item.peer_count:
        peer_bonus = max(Decimal(0), Decimal(item.peer_count - item.peer_rank) / Decimal(item.peer_count) * Decimal(6))
    answer_bonus = Decimal(10) if item.answer.startswith("YES") else Decimal(0)
    return (
        item.invest_score + answer_bonus + peer_bonus,
        item.confidence_score,
        margin,
        Decimal(str(item.scores.business_quality)),
    )


def _risk_level(
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationSnapshot | None,
    comparison: CompanyPeerComparisonSnapshot | None,
) -> str:
    if snapshot.final_label in {"Avoid for Now", "Speculative"}:
        return "High"
    if snapshot.data_confidence_score < Decimal(55):
        return "High"
    if valuation and valuation.margin_of_safety_percent is not None and valuation.margin_of_safety_percent < Decimal(-15):
        return "High"
    if comparison and comparison.sector_rank and comparison.peer_count and comparison.sector_rank > max(3, comparison.peer_count // 2):
        return "Medium"
    if snapshot.financial_risk_score >= Decimal(70) and snapshot.liquidity_score >= Decimal(60):
        return "Low"
    return "Medium"


def _suggested_horizon(snapshot: CompanyIntelligenceSnapshot) -> str:
    if "Quality compounder" in snapshot.stock_types or "Blue chip candidate" in snapshot.stock_types:
        return "5-10+ years"
    if snapshot.final_label == "Dividend Candidate":
        return "3-7+ years"
    if snapshot.final_label in {"Speculative", "Watch for Better Entry"}:
        return "Watch first"
    return "3-5+ years"


def _confidence_label(score: Decimal) -> str:
    if score >= Decimal(85):
        return "Very High"
    if score >= Decimal(70):
        return "High"
    if score >= Decimal(55):
        return "Medium"
    if score >= Decimal(40):
        return "Low"
    return "Very Low"


def _tone(answer: str) -> str:
    normalized = answer.lower()
    if normalized.startswith("yes"):
        return "positive"
    if normalized.startswith("wait"):
        return "warning"
    if normalized.startswith("speculative"):
        return "speculative"
    return "danger"


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal(0), min(HUNDRED, value))


def _fmt_percent(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}%"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result
