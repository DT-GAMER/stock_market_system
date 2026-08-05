from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median
from threading import Lock

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyIntelligenceSnapshot,
    CompanyPeerComparisonSnapshot,
    CompanyValuationSnapshot,
    CorporateDisclosure,
    Dividend,
    FinancialStatement,
    NgxPulseFundamental,
    Price,
    UploadedReport,
)
from ngx_research.schemas import (
    CompanyMemoryRead,
    IntelligenceOpportunityRead,
    IntelligenceRunRead,
    IntelligenceScoreBreakdownRead,
)
from ngx_research.services.stock_classifier import ClassificationContext, classify_stock

HUNDRED = Decimal(100)
_INTELLIGENCE_RUN_LOCK = Lock()


@dataclass(frozen=True)
class CompanyMemory:
    company: Company
    latest_price: Price | None
    prices: list[Price]
    dividends: list[Dividend]
    fundamentals: list[NgxPulseFundamental]
    statements: list[FinancialStatement]
    disclosures: list[CorporateDisclosure]
    annual_report_count: int


@dataclass(frozen=True)
class CompanyPatterns:
    pe_ratio: Decimal | None
    roe: Decimal | None
    profit_margin: Decimal | None
    dividend_yield: Decimal | None
    debt_to_equity: Decimal | None
    eps_growth: Decimal | None
    revenue_growth: Decimal | None
    profit_growth: Decimal | None
    roe_consistency: Decimal
    margin_trend: Decimal | None
    debt_trend: Decimal | None
    cash_flow_quality: Decimal | None
    dividend_years: int
    dividend_growth: Decimal | None
    payout_safety: Decimal | None
    price_drawdown_percent: Decimal | None
    volatility: Decimal | None
    liquidity_score: Decimal
    data_confidence: Decimal


@dataclass(frozen=True)
class SectorStats:
    pe_median: Decimal | None
    roe_median: Decimal | None
    dividend_yield_median: Decimal | None
    margin_median: Decimal | None
    growth_median: Decimal | None
    liquidity_median: Decimal | None


def run_intelligence_engine(
    session: Session,
    as_of_date: date | None = None,
    limit: int | None = None,
) -> IntelligenceRunRead:
    with _INTELLIGENCE_RUN_LOCK:
        return _run_intelligence_engine_locked(session=session, as_of_date=as_of_date, limit=limit)


def _run_intelligence_engine_locked(
    session: Session,
    as_of_date: date | None = None,
    limit: int | None = None,
) -> IntelligenceRunRead:
    snapshot_date = as_of_date or datetime.now(UTC).date()
    memories = _company_memories(session, snapshot_date)
    patterns_by_company = {
        memory.company.id: _company_patterns(memory, snapshot_date) for memory in memories
    }
    sector_stats = _sector_stats(memories, patterns_by_company)

    _clear_dependent_snapshots(session, snapshot_date)
    session.execute(
        delete(CompanyIntelligenceSnapshot).where(
            CompanyIntelligenceSnapshot.as_of_date == snapshot_date
        ).execution_options(synchronize_session=False)
    )
    session.flush()
    generated: list[CompanyIntelligenceSnapshot] = []
    for memory in memories:
        snapshot = _build_snapshot(
            memory=memory,
            patterns=patterns_by_company[memory.company.id],
            sector_stats=sector_stats.get(_sector_key(memory.company.sector), SectorStats(None, None, None, None, None, None)),
            as_of_date=snapshot_date,
        )
        session.add(snapshot)
        generated.append(snapshot)
    session.commit()

    opportunities = latest_intelligence_opportunities(session, limit=limit or 100)
    return IntelligenceRunRead(
        as_of_date=snapshot_date,
        generated=len(generated),
        opportunities=opportunities,
    )


def _clear_dependent_snapshots(session: Session, snapshot_date: date) -> None:
    session.execute(
        delete(CompanyPeerComparisonSnapshot)
        .where(CompanyPeerComparisonSnapshot.as_of_date == snapshot_date)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        delete(CompanyValuationSnapshot)
        .where(CompanyValuationSnapshot.as_of_date == snapshot_date)
        .execution_options(synchronize_session=False)
    )
    session.flush()


def latest_intelligence_opportunities(
    session: Session,
    limit: int = 100,
) -> list[IntelligenceOpportunityRead]:
    latest_date = session.scalar(
        select(func.max(CompanyIntelligenceSnapshot.as_of_date))
    )
    if latest_date is None:
        return []
    rows = session.execute(
        select(CompanyIntelligenceSnapshot, Company)
        .join(Company, Company.id == CompanyIntelligenceSnapshot.company_id)
        .where(CompanyIntelligenceSnapshot.as_of_date == latest_date)
        .order_by(desc(CompanyIntelligenceSnapshot.overall_score), Company.symbol)
        .limit(limit)
    )
    return [_opportunity_read(snapshot, company) for snapshot, company in rows]


def company_memory(session: Session, symbol: str) -> CompanyMemoryRead:
    company = session.scalar(select(Company).where(Company.symbol == symbol.upper()))
    if not company:
        raise ValueError("company not found")
    memory = _company_memory(session, company, datetime.now(UTC).date())
    return _memory_read(memory)


def _company_memories(session: Session, as_of_date: date) -> list[CompanyMemory]:
    companies = list(
        session.scalars(select(Company).where(Company.is_active.is_(True)).order_by(Company.symbol))
    )
    return [_company_memory(session, company, as_of_date) for company in companies]


def _company_memory(session: Session, company: Company, as_of_date: date) -> CompanyMemory:
    since = as_of_date - timedelta(days=365 * 10)
    prices = list(
        session.scalars(
            select(Price)
            .where(
                Price.company_id == company.id,
                Price.trade_date <= as_of_date,
                Price.trade_date >= since,
            )
            .order_by(desc(Price.trade_date), desc(Price.id))
        )
    )
    dividends = list(
        session.scalars(
            select(Dividend)
            .where(Dividend.company_id == company.id)
            .order_by(desc(Dividend.payment_date), desc(Dividend.declared_date), desc(Dividend.id))
        )
    )
    fundamentals = list(
        session.scalars(
            select(NgxPulseFundamental)
            .where(NgxPulseFundamental.company_id == company.id)
            .order_by(desc(NgxPulseFundamental.as_of_date), desc(NgxPulseFundamental.id))
        )
    )
    statements = list(
        session.scalars(
            select(FinancialStatement)
            .where(FinancialStatement.company_id == company.id)
            .order_by(desc(FinancialStatement.period_end), desc(FinancialStatement.id))
        )
    )
    disclosures = list(
        session.scalars(
            select(CorporateDisclosure)
            .where(CorporateDisclosure.company_id == company.id)
            .order_by(desc(CorporateDisclosure.published_at), desc(CorporateDisclosure.id))
            .limit(50)
        )
    )
    annual_report_count = session.scalar(
        select(func.count(UploadedReport.id)).where(UploadedReport.company_id == company.id)
    ) or 0
    return CompanyMemory(
        company=company,
        latest_price=prices[0] if prices else None,
        prices=prices,
        dividends=dividends,
        fundamentals=fundamentals,
        statements=statements,
        disclosures=disclosures,
        annual_report_count=annual_report_count,
    )


def _company_patterns(memory: CompanyMemory, as_of_date: date) -> CompanyPatterns:
    latest_fundamental = memory.fundamentals[0] if memory.fundamentals else None
    latest_statement = memory.statements[0] if memory.statements else None
    prior_statement = _prior_comparable_statement(memory.statements)
    pe_ratio = latest_fundamental.pe_ratio if latest_fundamental else None
    roe = _safe_percent(
        latest_statement.profit_after_tax if latest_statement else None,
        latest_statement.total_equity if latest_statement else None,
    ) or (latest_fundamental.roe if latest_fundamental else None)
    profit_margin = _safe_percent(
        latest_statement.profit_after_tax if latest_statement else None,
        latest_statement.revenue if latest_statement else None,
    ) or (latest_fundamental.profit_margin if latest_fundamental else None)
    dividend_yield = _trailing_dividend_yield(memory, as_of_date) or (
        latest_fundamental.dividend_yield if latest_fundamental else None
    )
    debt_to_equity = _safe_div(
        latest_statement.total_liabilities if latest_statement else None,
        latest_statement.total_equity if latest_statement else None,
    ) or (latest_fundamental.debt_equity if latest_fundamental else None)
    eps_growth = _fundamental_growth([item.eps for item in memory.fundamentals])
    revenue_growth = _safe_growth(
        latest_statement.revenue if latest_statement else None,
        prior_statement.revenue if prior_statement else None,
    )
    profit_growth = _safe_growth(
        latest_statement.profit_after_tax if latest_statement else None,
        prior_statement.profit_after_tax if prior_statement else None,
    )
    latest_price = memory.latest_price.close_price if memory.latest_price else None
    price_high = max((price.close_price for price in memory.prices[:260]), default=None)
    price_drawdown = _safe_percent(price_high - latest_price if price_high and latest_price else None, price_high)
    return CompanyPatterns(
        pe_ratio=pe_ratio,
        roe=roe,
        profit_margin=profit_margin,
        dividend_yield=dividend_yield,
        debt_to_equity=debt_to_equity,
        eps_growth=eps_growth,
        revenue_growth=revenue_growth,
        profit_growth=profit_growth,
        roe_consistency=_consistency([item.roe for item in memory.fundamentals if item.roe is not None]),
        margin_trend=_fundamental_growth([item.profit_margin for item in memory.fundamentals]),
        debt_trend=_fundamental_growth([item.debt_equity for item in memory.fundamentals]),
        cash_flow_quality=_cash_flow_quality(latest_statement),
        dividend_years=_dividend_years(memory.dividends),
        dividend_growth=_dividend_growth(memory.dividends),
        payout_safety=_payout_safety(latest_fundamental, latest_statement),
        price_drawdown_percent=price_drawdown,
        volatility=_price_volatility(memory.prices[:90]),
        liquidity_score=_liquidity_score(memory.prices[:90]),
        data_confidence=_data_confidence(memory),
    )


def _build_snapshot(
    memory: CompanyMemory,
    patterns: CompanyPatterns,
    sector_stats: SectorStats,
    as_of_date: date,
) -> CompanyIntelligenceSnapshot:
    company = memory.company
    market_cap = _market_cap(memory)
    classification = classify_stock(
        ClassificationContext(
            sector=company.sector,
            latest_price=memory.latest_price.close_price if memory.latest_price else None,
            market_cap=market_cap,
            pe_ratio=patterns.pe_ratio,
            sector_pe_median=sector_stats.pe_median,
            roe=patterns.roe,
            sector_roe_median=sector_stats.roe_median,
            profit_margin=patterns.profit_margin,
            dividend_yield=patterns.dividend_yield,
            dividend_years=patterns.dividend_years,
            revenue_growth=patterns.revenue_growth,
            profit_growth=patterns.profit_growth,
            eps_growth=patterns.eps_growth,
            debt_to_equity=patterns.debt_to_equity,
            cash_flow_to_profit=patterns.cash_flow_quality,
            price_drawdown_percent=patterns.price_drawdown_percent,
            liquidity_score=patterns.liquidity_score,
            data_confidence=patterns.data_confidence,
        )
    )
    quality = _quality_score(patterns, sector_stats)
    growth = _growth_score(patterns, sector_stats)
    valuation = _valuation_score(patterns, sector_stats)
    dividend = _dividend_score(patterns, sector_stats)
    risk = _financial_risk_score(patterns)
    momentum = _momentum_score(patterns)
    liquidity = patterns.liquidity_score
    confidence = patterns.data_confidence
    overall = (
        quality * Decimal("0.22")
        + growth * Decimal("0.14")
        + valuation * Decimal("0.18")
        + dividend * Decimal("0.12")
        + risk * Decimal("0.14")
        + momentum * Decimal("0.08")
        + liquidity * Decimal("0.06")
        + confidence * Decimal("0.06")
    ).quantize(Decimal("0.01"))
    missing_data = _missing_data(memory, patterns)
    risks = _risks(patterns, classification.risks, missing_data)
    label = _final_label(overall, quality, valuation, dividend, risk, liquidity, confidence, classification.stock_types, risks)
    reasons = _reasons(patterns, sector_stats, classification.reasons, label)
    next_actions = _next_actions(label, missing_data, risks)
    change_triggers = _decision_change_triggers(label)
    return CompanyIntelligenceSnapshot(
        company_id=company.id,
        as_of_date=as_of_date,
        sector=company.sector,
        final_label=label,
        stock_types=classification.stock_types,
        business_quality_score=quality,
        growth_score=growth,
        valuation_score=valuation,
        dividend_score=dividend,
        financial_risk_score=risk,
        momentum_score=momentum,
        liquidity_score=liquidity,
        data_confidence_score=confidence,
        overall_score=overall,
        reasons=reasons,
        risks=risks,
        missing_data=missing_data,
        next_actions=next_actions,
        decision_change_triggers=change_triggers,
        metrics=_metrics(patterns, sector_stats, memory),
        source_summary=_source_summary(memory),
    )


def _opportunity_read(
    snapshot: CompanyIntelligenceSnapshot,
    company: Company,
) -> IntelligenceOpportunityRead:
    return IntelligenceOpportunityRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        as_of_date=snapshot.as_of_date,
        final_label=snapshot.final_label,
        stock_types=snapshot.stock_types,
        scores=IntelligenceScoreBreakdownRead(
            business_quality=snapshot.business_quality_score,
            growth=snapshot.growth_score,
            valuation=snapshot.valuation_score,
            dividend=snapshot.dividend_score,
            financial_risk=snapshot.financial_risk_score,
            momentum=snapshot.momentum_score,
            liquidity=snapshot.liquidity_score,
            data_confidence=snapshot.data_confidence_score,
            overall=snapshot.overall_score,
        ),
        reasons=snapshot.reasons,
        risks=snapshot.risks,
        missing_data=snapshot.missing_data,
        next_actions=snapshot.next_actions,
        decision_change_triggers=snapshot.decision_change_triggers,
        metrics=snapshot.metrics,
        memory=_memory_read_from_snapshot(snapshot, company),
    )


def _memory_read_from_snapshot(
    snapshot: CompanyIntelligenceSnapshot,
    company: Company,
) -> CompanyMemoryRead:
    source_summary = snapshot.source_summary or {}
    metrics = snapshot.metrics or {}
    return CompanyMemoryRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        market_board=company.market_board,
        latest_price=_decimal_from_json(metrics.get("latest_price")),
        latest_price_date=_date_from_json(source_summary.get("latest_price_date")),
        price_records=int(source_summary.get("price_records") or 0),
        dividend_records=int(source_summary.get("dividend_records") or 0),
        fundamentals_records=int(source_summary.get("fundamentals_records") or 0),
        financial_statement_records=int(source_summary.get("financial_statement_records") or 0),
        disclosure_records=int(source_summary.get("disclosure_records") or 0),
        annual_report_records=int(source_summary.get("annual_report_records") or 0),
        latest_fundamental_date=_date_from_json(source_summary.get("latest_fundamental_date")),
        latest_statement_period_end=_date_from_json(source_summary.get("latest_statement_period_end")),
    )


def _memory_read(memory: CompanyMemory) -> CompanyMemoryRead:
    latest_fundamental = memory.fundamentals[0] if memory.fundamentals else None
    latest_statement = memory.statements[0] if memory.statements else None
    return CompanyMemoryRead(
        symbol=memory.company.symbol,
        name=memory.company.name,
        sector=memory.company.sector,
        market_board=memory.company.market_board,
        latest_price=memory.latest_price.close_price if memory.latest_price else None,
        latest_price_date=memory.latest_price.trade_date if memory.latest_price else None,
        price_records=len(memory.prices),
        dividend_records=len(memory.dividends),
        fundamentals_records=len(memory.fundamentals),
        financial_statement_records=len(memory.statements),
        disclosure_records=len(memory.disclosures),
        annual_report_records=memory.annual_report_count,
        latest_fundamental_date=latest_fundamental.as_of_date if latest_fundamental else None,
        latest_statement_period_end=latest_statement.period_end if latest_statement else None,
    )


def _sector_stats(
    memories: list[CompanyMemory],
    patterns_by_company: dict[int, CompanyPatterns],
) -> dict[str, SectorStats]:
    grouped: dict[str, list[CompanyPatterns]] = {}
    for memory in memories:
        grouped.setdefault(_sector_key(memory.company.sector), []).append(patterns_by_company[memory.company.id])
    return {
        sector: SectorStats(
            pe_median=_median([item.pe_ratio for item in patterns]),
            roe_median=_median([item.roe for item in patterns]),
            dividend_yield_median=_median([item.dividend_yield for item in patterns]),
            margin_median=_median([item.profit_margin for item in patterns]),
            growth_median=_median([item.eps_growth or item.profit_growth or item.revenue_growth for item in patterns]),
            liquidity_median=_median([item.liquidity_score for item in patterns]),
        )
        for sector, patterns in grouped.items()
    }


def _quality_score(patterns: CompanyPatterns, sector: SectorStats) -> Decimal:
    return _average(
        _score_higher(patterns.roe, Decimal(5), max(sector.roe_median or Decimal(20), Decimal(20))),
        _score_higher(patterns.profit_margin, Decimal(5), max(sector.margin_median or Decimal(20), Decimal(20))),
        patterns.roe_consistency,
    )


def _growth_score(patterns: CompanyPatterns, sector: SectorStats) -> Decimal:
    target = max(sector.growth_median or Decimal(12), Decimal(12))
    return _average(
        _score_higher(patterns.eps_growth, Decimal(0), target),
        _score_higher(patterns.revenue_growth, Decimal(0), target),
        _score_higher(patterns.profit_growth, Decimal(0), target),
    )


def _valuation_score(patterns: CompanyPatterns, sector: SectorStats) -> Decimal:
    if patterns.pe_ratio is None or patterns.pe_ratio <= 0:
        return Decimal(0)
    sector_pe = sector.pe_median or Decimal(10)
    if patterns.pe_ratio <= sector_pe * Decimal("0.65"):
        return Decimal(90)
    if patterns.pe_ratio <= sector_pe:
        return Decimal(75)
    if patterns.pe_ratio <= sector_pe * Decimal("1.35"):
        return Decimal(50)
    return Decimal(20)


def _dividend_score(patterns: CompanyPatterns, sector: SectorStats) -> Decimal:
    yield_score = _score_higher(patterns.dividend_yield, Decimal(0), max(sector.dividend_yield_median or Decimal(5), Decimal(5)))
    consistency = min(Decimal(patterns.dividend_years) * Decimal(18), HUNDRED)
    safety = patterns.payout_safety or Decimal(45)
    return _average(yield_score, consistency, safety)


def _financial_risk_score(patterns: CompanyPatterns) -> Decimal:
    score = HUNDRED
    if patterns.debt_to_equity is not None and patterns.debt_to_equity > Decimal(3):
        score -= Decimal(25)
    if patterns.cash_flow_quality is not None and patterns.cash_flow_quality < Decimal(50):
        score -= Decimal(20)
    if patterns.volatility is not None and patterns.volatility > Decimal(6):
        score -= Decimal(15)
    return max(Decimal(0), score)


def _momentum_score(patterns: CompanyPatterns) -> Decimal:
    if patterns.price_drawdown_percent is None:
        return Decimal(45)
    if patterns.price_drawdown_percent <= Decimal(10):
        return Decimal(70)
    if patterns.price_drawdown_percent <= Decimal(30):
        return Decimal(55)
    return Decimal(35)


def _final_label(
    overall: Decimal,
    quality: Decimal,
    valuation: Decimal,
    dividend: Decimal,
    risk: Decimal,
    liquidity: Decimal,
    confidence: Decimal,
    stock_types: list[str],
    risks: list[str],
) -> str:
    if confidence < Decimal(45):
        return "Needs Data"
    if "Weak/avoid candidate" in stock_types or risk < Decimal(35):
        return "Avoid for Now"
    if "Penny/speculative stock" in stock_types and liquidity < Decimal(45):
        return "Speculative"
    if overall >= Decimal(78) and quality >= Decimal(65) and valuation >= Decimal(55):
        return "Top Research Candidate"
    if overall >= Decimal(65) and valuation >= Decimal(50):
        return "Research Now"
    if "Dividend stock" in stock_types and dividend >= Decimal(55):
        return "Dividend Candidate"
    if quality >= Decimal(70) and valuation < Decimal(35):
        return "Good Company, Expensive"
    if risks:
        return "Watch for Better Entry"
    return "Watch for Better Entry"


def _reasons(
    patterns: CompanyPatterns,
    sector: SectorStats,
    classification_reasons: list[str],
    label: str,
) -> list[str]:
    reasons = list(classification_reasons)
    if patterns.roe is not None and sector.roe_median is not None and patterns.roe >= sector.roe_median:
        reasons.append("ROE is at or above sector median.")
    if patterns.profit_margin is not None and sector.margin_median is not None and patterns.profit_margin >= sector.margin_median:
        reasons.append("Profit margin compares well against sector peers.")
    if patterns.pe_ratio is not None and sector.pe_median is not None and patterns.pe_ratio <= sector.pe_median:
        reasons.append("P/E is not expensive compared with sector median.")
    if patterns.liquidity_score >= Decimal(65):
        reasons.append("Trading liquidity appears reliable enough for retail execution.")
    if label == "Top Research Candidate":
        reasons.append("Composite intelligence score places this company among the strongest current opportunities.")
    return reasons or ["No strong positive edge detected yet."]


def _risks(
    patterns: CompanyPatterns,
    classification_risks: list[str],
    missing_data: list[str],
) -> list[str]:
    risks = list(classification_risks)
    if patterns.data_confidence < Decimal(70):
        risks.append("Data confidence is below preferred threshold.")
    if patterns.pe_ratio is None:
        risks.append("Valuation cannot be confirmed without P/E or EPS support.")
    if patterns.dividend_years == 0:
        if patterns.dividend_yield is not None:
            risks.append(
                "Dividend yield is available from fundamentals, but detailed payment history is not stored yet."
            )
        else:
            risks.append("No dividend yield or payment history found in current database.")
    if len(missing_data) >= 3:
        risks.append("Several important data layers are still missing.")
    return risks or ["No major rule-based risk flag."]


def _missing_data(memory: CompanyMemory, patterns: CompanyPatterns) -> list[str]:
    missing: list[str] = []
    if not memory.prices:
        missing.append("price history")
    if not memory.fundamentals:
        missing.append("NGX Pulse fundamentals")
    if not memory.statements:
        missing.append("annual financial statements")
    if not memory.dividends:
        if patterns.dividend_yield is not None:
            missing.append("detailed dividend payment history")
        else:
            missing.append("dividend history")
    if patterns.pe_ratio is None:
        missing.append("P/E ratio")
    if patterns.roe is None:
        missing.append("ROE")
    return missing


def _next_actions(label: str, missing_data: list[str], risks: list[str]) -> list[str]:
    actions: list[str] = []
    if label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}:
        actions.append("Open the company research page and review reasons, risks, and valuation.")
        actions.append("Add to watchlist before buying; observe price behavior and liquidity.")
    if missing_data:
        actions.append(f"Improve data quality: add or sync {', '.join(missing_data[:3])}.")
    if risks:
        actions.append("Review risk flags before committing capital.")
    return actions or ["Keep monitoring until a stronger opportunity signal appears."]


def _decision_change_triggers(label: str) -> list[str]:
    triggers = [
        "New fundamentals materially change EPS, ROE, margin, or debt metrics.",
        "Price moves enough to change valuation attractiveness.",
        "Disclosure/news creates a new risk or catalyst.",
    ]
    if label in {"Good Company, Expensive", "Watch for Better Entry"}:
        triggers.append("Price falls or earnings improve enough to restore margin of safety.")
    if label == "Dividend Candidate":
        triggers.append("Dividend cut, payout stress, or weak earnings would reduce income quality.")
    return triggers


def _metrics(patterns: CompanyPatterns, sector: SectorStats, memory: CompanyMemory) -> dict:
    latest_price = memory.latest_price.close_price if memory.latest_price else None
    return {
        "latest_price": _json_decimal(latest_price),
        "pe_ratio": _json_decimal(patterns.pe_ratio),
        "sector_pe_median": _json_decimal(sector.pe_median),
        "roe": _json_decimal(patterns.roe),
        "sector_roe_median": _json_decimal(sector.roe_median),
        "profit_margin": _json_decimal(patterns.profit_margin),
        "dividend_yield": _json_decimal(patterns.dividend_yield),
        "dividend_years": patterns.dividend_years,
        "eps_growth": _json_decimal(patterns.eps_growth),
        "revenue_growth": _json_decimal(patterns.revenue_growth),
        "profit_growth": _json_decimal(patterns.profit_growth),
        "roe_consistency": _json_decimal(patterns.roe_consistency),
        "margin_trend": _json_decimal(patterns.margin_trend),
        "debt_to_equity": _json_decimal(patterns.debt_to_equity),
        "debt_trend": _json_decimal(patterns.debt_trend),
        "cash_flow_quality": _json_decimal(patterns.cash_flow_quality),
        "price_drawdown_percent": _json_decimal(patterns.price_drawdown_percent),
        "volatility": _json_decimal(patterns.volatility),
        "liquidity_score": _json_decimal(patterns.liquidity_score),
        "dividend_growth": _json_decimal(patterns.dividend_growth),
        "payout_safety": _json_decimal(patterns.payout_safety),
        "data_confidence": _json_decimal(patterns.data_confidence),
    }


def _source_summary(memory: CompanyMemory) -> dict:
    latest_fundamental = memory.fundamentals[0] if memory.fundamentals else None
    latest_statement = memory.statements[0] if memory.statements else None
    return {
        "price_records": len(memory.prices),
        "fundamentals_records": len(memory.fundamentals),
        "financial_statement_records": len(memory.statements),
        "dividend_records": len(memory.dividends),
        "disclosure_records": len(memory.disclosures),
        "annual_report_records": memory.annual_report_count,
        "latest_price_date": memory.latest_price.trade_date.isoformat() if memory.latest_price else None,
        "latest_fundamental_date": latest_fundamental.as_of_date.isoformat() if latest_fundamental else None,
        "latest_statement_period_end": latest_statement.period_end.isoformat() if latest_statement else None,
    }


def _prior_comparable_statement(statements: list[FinancialStatement]) -> FinancialStatement | None:
    if len(statements) < 2:
        return None
    latest = statements[0]
    return next(
        (
            statement
            for statement in statements[1:]
            if statement.period_type == latest.period_type and statement.period_end < latest.period_end
        ),
        None,
    )


def _trailing_dividend_yield(memory: CompanyMemory, as_of_date: date) -> Decimal | None:
    if not memory.latest_price or memory.latest_price.close_price <= 0:
        return None
    since = as_of_date - timedelta(days=365)
    total = sum(
        dividend.amount_per_share
        for dividend in memory.dividends
        if dividend.payment_date and since <= dividend.payment_date <= as_of_date
    )
    return _safe_percent(total, memory.latest_price.close_price)


def _dividend_years(dividends: list[Dividend]) -> int:
    return len({(dividend.payment_date or dividend.declared_date).year for dividend in dividends if dividend.payment_date or dividend.declared_date})


def _dividend_growth(dividends: list[Dividend]) -> Decimal | None:
    yearly: dict[int, Decimal] = {}
    for dividend in dividends:
        event_date = dividend.payment_date or dividend.declared_date
        if event_date:
            yearly[event_date.year] = yearly.get(event_date.year, Decimal(0)) + dividend.amount_per_share
    years = sorted(yearly, reverse=True)
    if len(years) < 2:
        return None
    return _safe_growth(yearly[years[0]], yearly[years[1]])


def _payout_safety(
    fundamental: NgxPulseFundamental | None,
    statement: FinancialStatement | None,
) -> Decimal | None:
    payout_ratio = None
    if fundamental and fundamental.extra:
        raw = fundamental.extra.get("payout_ratio")
        payout_ratio = Decimal(str(raw)) if raw not in (None, "") else None
    if payout_ratio is not None:
        if payout_ratio <= Decimal(40):
            return Decimal(90)
        if payout_ratio <= Decimal(70):
            return Decimal(70)
        return Decimal(35)
    if statement and statement.eps and statement.eps > 0:
        return Decimal(60)
    return None


def _cash_flow_quality(statement: FinancialStatement | None) -> Decimal | None:
    if not statement:
        return None
    ratio = _safe_div(statement.cash_flow_operations, statement.profit_after_tax)
    if ratio is None:
        return None
    return _score_higher(ratio, Decimal("0.4"), Decimal("1.2"))


def _fundamental_growth(values: list[Decimal | None]) -> Decimal | None:
    cleaned = [value for value in values if value is not None]
    if len(cleaned) < 2:
        return None
    return _safe_growth(cleaned[0], cleaned[1])


def _consistency(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(45)
    positive = sum(1 for value in values[:5] if value > 0)
    return min((Decimal(positive) / Decimal(min(len(values), 5))) * HUNDRED, HUNDRED).quantize(Decimal("0.01"))


def _price_volatility(prices: list[Price]) -> Decimal | None:
    if len(prices) < 10:
        return None
    changes: list[Decimal] = []
    ordered = list(reversed(prices))
    for previous, current in pairwise(ordered):
        change = _safe_percent(current.close_price - previous.close_price, previous.close_price)
        if change is not None:
            changes.append(abs(change))
    return _average(*changes) if changes else None


def _liquidity_score(prices: list[Price]) -> Decimal:
    recent = prices[:30]
    if not recent:
        return Decimal(0)
    traded_days = sum(1 for price in recent if price.volume and price.volume > 0)
    avg_volume = sum(Decimal(price.volume or 0) for price in recent) / Decimal(len(recent))
    consistency = (Decimal(traded_days) / Decimal(len(recent))) * Decimal(55)
    depth = min(avg_volume / Decimal(100_000), Decimal(1)) * Decimal(45)
    return (consistency + depth).quantize(Decimal("0.01"))


def _data_confidence(memory: CompanyMemory) -> Decimal:
    score = Decimal(0)
    if memory.latest_price:
        score += Decimal(25)
    if memory.fundamentals:
        score += Decimal(35)
    if memory.statements:
        score += Decimal(20)
    if memory.dividends:
        score += Decimal(10)
    if memory.prices and len(memory.prices) >= 30:
        score += Decimal(10)
    return min(score, HUNDRED)


def _market_cap(memory: CompanyMemory) -> Decimal | None:
    if not memory.fundamentals:
        return None
    raw = memory.fundamentals[0].raw_payload.get("market_cap") if memory.fundamentals[0].raw_payload else None
    return Decimal(str(raw)) if raw not in (None, "") else None


def _safe_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal(0)):
        return None
    return (numerator / denominator).quantize(Decimal("0.0001"))


def _safe_percent(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    value = _safe_div(numerator, denominator)
    return (value * HUNDRED).quantize(Decimal("0.0001")) if value is not None else None


def _safe_growth(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous in (None, Decimal(0)):
        return None
    return (((current - previous) / abs(previous)) * HUNDRED).quantize(Decimal("0.0001"))


def _score_higher(value: Decimal | None, low: Decimal, high: Decimal) -> Decimal:
    if value is None:
        return Decimal(0)
    if value <= low:
        return Decimal(0)
    if value >= high:
        return HUNDRED
    return (((value - low) / (high - low)) * HUNDRED).quantize(Decimal("0.01"))


def _average(*values: Decimal) -> Decimal:
    if not values:
        return Decimal(0)
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.01"))


def _median(values: list[Decimal | None]) -> Decimal | None:
    cleaned = [value for value in values if value is not None]
    return Decimal(str(median(cleaned))).quantize(Decimal("0.0001")) if cleaned else None


def _sector_key(sector: str | None) -> str:
    return (sector or "Unknown").strip().lower()


def _json_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _decimal_from_json(value) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, "") else None


def _date_from_json(value) -> date | None:
    return date.fromisoformat(str(value)) if value not in (None, "") else None
