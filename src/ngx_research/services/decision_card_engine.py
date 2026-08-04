from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ngx_research.models import Company, CompanyIntelligenceSnapshot
from ngx_research.schemas import (
    CompanyValuationRead,
    DecisionCardMetricRead,
    DecisionCardRead,
    DecisionCardSectionRead,
    IntelligenceScoreBreakdownRead,
)
from ngx_research.services.peer_comparison_engine import (
    latest_company_peer_comparison_snapshot,
    peer_comparison_snapshot_read,
)
from ngx_research.services.valuation_engine import (
    latest_company_valuation_snapshot,
    valuation_snapshot_read,
)

HUNDRED = Decimal(100)


def decision_card(session: Session, symbol: str) -> DecisionCardRead:
    normalized = symbol.strip().upper()
    company = session.scalar(select(Company).where(Company.symbol == normalized))
    if not company:
        raise ValueError(f"{normalized} is not in the company universe.")

    snapshot = session.scalar(
        select(CompanyIntelligenceSnapshot)
        .where(CompanyIntelligenceSnapshot.company_id == company.id)
        .order_by(desc(CompanyIntelligenceSnapshot.as_of_date), desc(CompanyIntelligenceSnapshot.id))
        .limit(1)
    )
    if not snapshot:
        raise ValueError(
            f"No intelligence snapshot found for {normalized}. "
            "Sync NGX Pulse data, then run POST /intelligence/run."
        )

    previous = session.scalar(
        select(CompanyIntelligenceSnapshot)
        .where(
            CompanyIntelligenceSnapshot.company_id == company.id,
            CompanyIntelligenceSnapshot.id != snapshot.id,
        )
        .order_by(desc(CompanyIntelligenceSnapshot.as_of_date), desc(CompanyIntelligenceSnapshot.id))
        .limit(1)
    )
    metrics = snapshot.metrics or {}
    source_summary = snapshot.source_summary or {}
    score_breakdown = _score_breakdown(snapshot)
    confidence_score = _confidence_score(snapshot)
    latest_price = _decimal(metrics.get("latest_price"))
    valuation_row = latest_company_valuation_snapshot(session, company.id)
    valuation = valuation_snapshot_read(*valuation_row) if valuation_row else None
    peer_row = latest_company_peer_comparison_snapshot(session, company.id)
    peer_comparison = peer_comparison_snapshot_read(*peer_row) if peer_row else None

    return DecisionCardRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        as_of_date=snapshot.as_of_date,
        latest_price=latest_price,
        latest_price_date=_date(source_summary.get("latest_price_date")),
        stock_types=snapshot.stock_types,
        answer=_answer(snapshot, valuation),
        invest_score=snapshot.overall_score,
        confidence=_confidence_label(confidence_score),
        confidence_score=confidence_score,
        risk_level=_risk_level(snapshot, metrics),
        suggested_horizon=_suggested_horizon(snapshot),
        valuation_status=_valuation_status(snapshot, metrics, valuation),
        financial_health=_financial_health(snapshot),
        dividend_quality=_dividend_quality(snapshot, metrics),
        moat_rating=_moat_rating(snapshot),
        one_paragraph_summary=_one_paragraph_summary(company, snapshot, metrics, valuation),
        decision_summary=_decision_summary(company, snapshot, metrics, valuation),
        score_breakdown=score_breakdown,
        valuation_snapshot=valuation,
        peer_comparison=peer_comparison,
        health_checks=_health_checks(snapshot, metrics, source_summary),
        valuation=_valuation_section(snapshot, metrics, valuation),
        why_buy=_why_buy_section(snapshot),
        why_not_buy=_why_not_buy_section(company, snapshot),
        growth_drivers=_growth_drivers_section(company, snapshot, metrics),
        threats=_threats_section(company, snapshot, metrics),
        dividend=_dividend_section(snapshot, metrics),
        moat=_moat_section(company, snapshot),
        future_outlook=_future_outlook_section(snapshot, metrics),
        stress_test=_stress_test_section(snapshot, metrics),
        portfolio_fit=_portfolio_fit_section(company, snapshot),
        what_changed=_what_changed_section(snapshot, previous),
        what_would_change_decision=_decision_change_section(snapshot),
        missing_data=snapshot.missing_data,
        data_quality_notes=_data_quality_notes(snapshot, source_summary),
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


def _answer(
    snapshot: CompanyIntelligenceSnapshot,
    valuation: CompanyValuationRead | None = None,
) -> str:
    label = snapshot.final_label
    if (
        valuation
        and valuation.valuation_label in {"Overvalued", "Expensive"}
        and label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}
    ):
        return "WAIT - quality may be interesting, but valuation is not attractive"
    if label == "Top Research Candidate":
        if valuation and valuation.valuation_label in {"Deeply Undervalued", "Undervalued"}:
            return "YES - top research candidate with valuation support"
        return "YES - top research candidate"
    if label == "Research Now":
        return "YES - research now before buying"
    if label == "Dividend Candidate":
        return "YES - income-focused research candidate"
    if label == "Good Company, Expensive":
        return "WAIT - good company, but entry price needs discipline"
    if label == "Watch for Better Entry":
        return "WAIT - monitor for a better entry"
    if label == "Speculative":
        return "ONLY IF HIGH RISK - speculative position only"
    if label == "Avoid for Now":
        return "NO - avoid for now"
    return "NOT YET - not enough verified data"


def _confidence_score(snapshot: CompanyIntelligenceSnapshot) -> Decimal:
    source_summary = snapshot.source_summary or {}
    present_layers = sum(
        1
        for key in (
            "price_records",
            "fundamentals_records",
            "financial_statement_records",
            "dividend_records",
            "disclosure_records",
            "annual_report_records",
        )
        if int(source_summary.get(key) or 0) > 0
    )
    layer_score = Decimal(present_layers) / Decimal(6) * HUNDRED
    support_score = min(Decimal(len(snapshot.reasons) * 12), HUNDRED)
    missing_penalty = min(Decimal(len(snapshot.missing_data) * 8), Decimal(35))
    score = (
        snapshot.data_confidence_score * Decimal("0.55")
        + layer_score * Decimal("0.25")
        + support_score * Decimal("0.20")
        - missing_penalty
    )
    return _clamp(score).quantize(Decimal("0.01"))


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


def _risk_level(snapshot: CompanyIntelligenceSnapshot, metrics: dict) -> str:
    volatility = _decimal(metrics.get("volatility"))
    risk_score = snapshot.financial_risk_score
    stock_types = set(snapshot.stock_types)
    if "Weak/avoid candidate" in stock_types or risk_score < Decimal(35):
        return "Very High"
    if "Penny/speculative stock" in stock_types or risk_score < Decimal(55):
        return "High"
    if risk_score < Decimal(75) or (volatility is not None and volatility > Decimal(6)):
        return "Medium"
    return "Low"


def _suggested_horizon(snapshot: CompanyIntelligenceSnapshot) -> str:
    stock_types = set(snapshot.stock_types)
    if snapshot.final_label in {"Avoid for Now", "Needs Data"}:
        return "No holding period yet - build evidence first"
    if snapshot.final_label == "Speculative":
        return "Shorter, tightly reviewed position only"
    if "Quality compounder" in stock_types or "Blue chip candidate" in stock_types:
        return "8-15 years, reviewed after each annual result"
    if "Dividend stock" in stock_types or "Dividend history stock" in stock_types:
        return "5-10 years, reviewed around earnings and dividend declarations"
    return "3-5 years, reviewed quarterly"


def _valuation_status(
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
    valuation: CompanyValuationRead | None = None,
) -> str:
    if valuation:
        if valuation.margin_of_safety_percent is None:
            return f"{valuation.valuation_label} ({valuation.valuation_confidence} confidence)"
        return (
            f"{valuation.valuation_label}: "
            f"{_fmt_percent(valuation.margin_of_safety_percent)} margin of safety"
        )
    pe_ratio = _decimal(metrics.get("pe_ratio"))
    sector_pe = _decimal(metrics.get("sector_pe_median"))
    if pe_ratio is None or pe_ratio <= 0:
        return "Cannot value confidently yet"
    if snapshot.valuation_score >= Decimal(75):
        if sector_pe:
            return "Attractive vs sector earnings multiple"
        return "Attractive on absolute earnings multiple"
    if snapshot.valuation_score >= Decimal(50):
        return "Reasonable, but margin of safety is not large"
    if snapshot.valuation_score >= Decimal(35):
        return "Fair to expensive"
    return "Expensive or unsupported by earnings"


def _financial_health(snapshot: CompanyIntelligenceSnapshot) -> str:
    quality = snapshot.business_quality_score
    risk = snapshot.financial_risk_score
    confidence = snapshot.data_confidence_score
    if quality >= Decimal(80) and risk >= Decimal(75) and confidence >= Decimal(70):
        return "Excellent"
    if quality >= Decimal(65) and risk >= Decimal(65):
        return "Healthy"
    if quality >= Decimal(45) and risk >= Decimal(50):
        return "Mixed"
    if confidence < Decimal(45):
        return "Unknown"
    return "Weak"


def _dividend_quality(snapshot: CompanyIntelligenceSnapshot, metrics: dict) -> str:
    dividend_years = int(metrics.get("dividend_years") or 0)
    payout_safety = _decimal(metrics.get("payout_safety"))
    if snapshot.dividend_score >= Decimal(75) and dividend_years >= 3:
        return "Strong"
    if snapshot.dividend_score >= Decimal(55) and dividend_years >= 2:
        return "Promising"
    if payout_safety is not None and payout_safety >= Decimal(70):
        return "Potentially safe, but history is short"
    if dividend_years == 0:
        return "No dividend evidence yet"
    return "Weak or incomplete"


def _moat_rating(snapshot: CompanyIntelligenceSnapshot) -> str:
    stock_types = set(snapshot.stock_types)
    quality = snapshot.business_quality_score
    liquidity = snapshot.liquidity_score
    if "Blue chip candidate" in stock_types and "Quality compounder" in stock_types:
        return "Very Strong"
    if "Quality compounder" in stock_types or (quality >= Decimal(75) and liquidity >= Decimal(65)):
        return "Strong"
    if quality >= Decimal(55):
        return "Developing"
    if "Weak/avoid candidate" in stock_types:
        return "Weak"
    return "Unproven"


def _one_paragraph_summary(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
    valuation: CompanyValuationRead | None = None,
) -> str:
    valuation_status = _valuation_status(snapshot, metrics, valuation).lower()
    health = _financial_health(snapshot).lower()
    risk = _risk_level(snapshot, metrics).lower()
    confidence = _confidence_label(_confidence_score(snapshot)).lower()
    missing = (
        f" The main evidence gap is {', '.join(snapshot.missing_data[:3])}."
        if snapshot.missing_data
        else ""
    )
    return (
        f"{company.name} is currently classified as {snapshot.final_label.lower()} with an "
        f"invest score of {_fmt_score(snapshot.overall_score)}. The business health reads as "
        f"{health}, valuation reads as {valuation_status}, and risk is {risk}. Confidence is "
        f"{confidence} because the system is combining price, liquidity, fundamentals, "
        f"dividend records, and stored company memory instead of a single headline metric."
        f"{missing}"
    )


def _decision_summary(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
    valuation: CompanyValuationRead | None = None,
) -> str:
    answer = _answer(snapshot, valuation)
    pe_ratio = _decimal(metrics.get("pe_ratio"))
    sector_pe = _decimal(metrics.get("sector_pe_median"))
    pe_text = _valuation_text(valuation) if valuation else _pe_comparison(pe_ratio, sector_pe)
    if answer.startswith("YES"):
        return (
            f"{answer}. {company.symbol} deserves attention because the current evidence points "
            f"to a stronger-than-average opportunity profile. {pe_text} Before buying, confirm "
            "the thesis, decide your holding period, and size the position so one stock or sector "
            "cannot dominate the portfolio."
        )
    if answer.startswith("WAIT"):
        return (
            f"{answer}. The company may still be worth watching, but the current evidence does "
            f"not support rushing into a purchase. {pe_text} Track price, earnings updates, and "
            "dividend or disclosure changes until the margin of safety improves."
        )
    if answer.startswith("NO"):
        return (
            f"{answer}. The risk or weakness flags currently outweigh the positive evidence. "
            "Do not treat a low price as value until profitability, liquidity, and data confidence "
            "improve."
        )
    return (
        f"{answer}. The company is in the database, but the intelligence layer does not yet have "
        "enough verified financial evidence to form a dependable view."
    )


def _health_checks(
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
    source_summary: dict,
) -> list[DecisionCardMetricRead]:
    return [
        _growth_check("Revenue", _decimal(metrics.get("revenue_growth"))),
        _profit_check(snapshot, metrics),
        _cash_flow_check(_decimal(metrics.get("cash_flow_quality"))),
        _debt_check(_decimal(metrics.get("debt_to_equity")), _decimal(metrics.get("debt_trend"))),
        _dividend_check(snapshot, metrics),
        _liquidity_check(snapshot, source_summary),
        _data_confidence_check(snapshot, source_summary),
    ]


def _growth_check(label: str, growth: Decimal | None) -> DecisionCardMetricRead:
    if growth is None:
        return DecisionCardMetricRead(
            label=label,
            status="Missing",
            detail=(
                f"{label} growth cannot be judged yet because comparable historical statement "
                "data is not available in the company memory."
            ),
            evidence=["Add at least two comparable annual or quarterly financial statements."],
        )
    if growth >= Decimal(15):
        status = "Strong"
        detail = f"{label} is growing at {_fmt_percent(growth)}, which is a strong expansion signal."
    elif growth >= Decimal(5):
        status = "Good"
        detail = f"{label} is growing at {_fmt_percent(growth)}, which is positive but not explosive."
    elif growth >= Decimal(0):
        status = "Flat"
        detail = f"{label} is barely growing at {_fmt_percent(growth)}."
    else:
        status = "Weak"
        detail = f"{label} is declining at {_fmt_percent(growth)}."
    return DecisionCardMetricRead(label=label, status=status, score=None, detail=detail)


def _profit_check(snapshot: CompanyIntelligenceSnapshot, metrics: dict) -> DecisionCardMetricRead:
    profit_growth = _decimal(metrics.get("profit_growth"))
    eps_growth = _decimal(metrics.get("eps_growth"))
    roe = _decimal(metrics.get("roe"))
    margin = _decimal(metrics.get("profit_margin"))
    evidence = [
        _metric_sentence("ROE", roe, suffix="%"),
        _metric_sentence("profit margin", margin, suffix="%"),
        _metric_sentence("EPS growth", eps_growth, suffix="%"),
    ]
    evidence = [item for item in evidence if item]
    if snapshot.business_quality_score >= Decimal(75):
        status = "Strong"
        detail = "Profitability is strong relative to the available sector and company data."
    elif snapshot.business_quality_score >= Decimal(55):
        status = "Good"
        detail = "Profitability is acceptable, but the company is not yet a standout."
    elif profit_growth is not None and profit_growth < 0:
        status = "Weak"
        detail = f"Profit is declining at {_fmt_percent(profit_growth)}, so earnings quality needs review."
    else:
        status = "Incomplete"
        detail = "Profitability needs more evidence before it can carry an investment decision."
    return DecisionCardMetricRead(
        label="Profit and earnings",
        status=status,
        score=snapshot.business_quality_score,
        detail=detail,
        evidence=evidence or ["ROE, margin, or EPS history is incomplete."],
    )


def _cash_flow_check(cash_flow_quality: Decimal | None) -> DecisionCardMetricRead:
    if cash_flow_quality is None:
        return DecisionCardMetricRead(
            label="Cash flow",
            status="Missing",
            detail=(
                "Operating cash-flow quality is missing. This matters because reported profit is "
                "less reliable when it is not supported by cash generation."
            ),
            evidence=["Upload or sync statements containing operating cash flow."],
        )
    if cash_flow_quality >= Decimal(75):
        status = "Strong"
        detail = "Operating cash flow strongly supports reported profit."
    elif cash_flow_quality >= Decimal(50):
        status = "Acceptable"
        detail = "Operating cash flow provides some support, but it should still be reviewed."
    else:
        status = "Weak"
        detail = "Cash generation is weak relative to reported profit."
    return DecisionCardMetricRead(
        label="Cash flow",
        status=status,
        score=cash_flow_quality,
        detail=detail,
        evidence=[f"Cash-flow quality score: {_fmt_score(cash_flow_quality)}."],
    )


def _debt_check(
    debt_to_equity: Decimal | None,
    debt_trend: Decimal | None,
) -> DecisionCardMetricRead:
    if debt_to_equity is None:
        return DecisionCardMetricRead(
            label="Debt",
            status="Missing",
            detail="Debt-to-equity is missing, so balance-sheet risk cannot be fully judged yet.",
            evidence=["Add total liabilities and total equity from recent financial statements."],
        )
    if debt_to_equity <= Decimal("1.0"):
        status = "Low"
        detail = f"Debt-to-equity is {_fmt_number(debt_to_equity)}, which is conservative."
    elif debt_to_equity <= Decimal("2.5"):
        status = "Moderate"
        detail = f"Debt-to-equity is {_fmt_number(debt_to_equity)}, so leverage should be watched."
    else:
        status = "High"
        detail = f"Debt-to-equity is {_fmt_number(debt_to_equity)}, which increases financial risk."
    evidence = [f"Debt-to-equity: {_fmt_number(debt_to_equity)}."]
    if debt_trend is not None:
        direction = "rising" if debt_trend > 0 else "falling"
        evidence.append(f"Debt trend is {direction} by {_fmt_percent(abs(debt_trend))}.")
    return DecisionCardMetricRead(label="Debt", status=status, detail=detail, evidence=evidence)


def _dividend_check(
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
) -> DecisionCardMetricRead:
    years = int(metrics.get("dividend_years") or 0)
    yield_value = _decimal(metrics.get("dividend_yield"))
    payout_safety = _decimal(metrics.get("payout_safety"))
    detail = _dividend_quality(snapshot, metrics)
    evidence = [
        f"Dividend years in database: {years}.",
        _metric_sentence("dividend yield", yield_value, suffix="%"),
        _metric_sentence("payout safety score", payout_safety),
    ]
    return DecisionCardMetricRead(
        label="Dividend safety",
        status=detail,
        score=snapshot.dividend_score,
        detail=(
            "Dividend evidence is judged from yield, consistency, and payout safety. "
            f"Current read: {detail.lower()}."
        ),
        evidence=[item for item in evidence if item],
    )


def _liquidity_check(
    snapshot: CompanyIntelligenceSnapshot,
    source_summary: dict,
) -> DecisionCardMetricRead:
    records = int(source_summary.get("price_records") or 0)
    if snapshot.liquidity_score >= Decimal(75):
        status = "Reliable"
        detail = "Recent trading activity looks deep enough for normal retail entry and exit."
    elif snapshot.liquidity_score >= Decimal(45):
        status = "Moderate"
        detail = "Liquidity exists, but execution should still be watched before larger orders."
    else:
        status = "Thin"
        detail = "Liquidity is weak. The price may move sharply or be difficult to enter/exit cleanly."
    return DecisionCardMetricRead(
        label="Liquidity",
        status=status,
        score=snapshot.liquidity_score,
        detail=detail,
        evidence=[f"Price records available: {records}."],
    )


def _data_confidence_check(
    snapshot: CompanyIntelligenceSnapshot,
    source_summary: dict,
) -> DecisionCardMetricRead:
    evidence = [
        f"Price records: {int(source_summary.get('price_records') or 0)}.",
        f"Fundamental records: {int(source_summary.get('fundamentals_records') or 0)}.",
        f"Financial statements: {int(source_summary.get('financial_statement_records') or 0)}.",
        f"Dividend records: {int(source_summary.get('dividend_records') or 0)}.",
    ]
    if snapshot.data_confidence_score >= Decimal(80):
        status = "High"
        detail = "The system has enough data layers to make this signal useful."
    elif snapshot.data_confidence_score >= Decimal(60):
        status = "Medium"
        detail = "The signal is useful, but a few data layers still need strengthening."
    else:
        status = "Low"
        detail = "The signal should be treated as early research until more verified data is added."
    return DecisionCardMetricRead(
        label="Data confidence",
        status=status,
        score=snapshot.data_confidence_score,
        detail=detail,
        evidence=evidence,
    )


def _valuation_section(
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
    valuation: CompanyValuationRead | None = None,
) -> DecisionCardSectionRead:
    if valuation:
        points = [
            _fair_value_range_sentence(valuation),
            _expected_return_sentence(valuation),
            (
                f"Valuation confidence is {valuation.valuation_confidence.lower()} "
                f"({_fmt_score(valuation.confidence_score)})."
            ),
        ]
        points.extend(valuation.reasons)
        for method in valuation.methods:
            points.append(
                f"{method.name}: {_method_range_sentence(method)} {method.reason}"
            )
        if valuation.warnings:
            points.append("Main valuation warning: " + valuation.warnings[0])
        return DecisionCardSectionRead(
            title="Valuation",
            summary=(
                f"{valuation.valuation_label}. The model compares latest price with an estimated "
                "fair value range instead of relying on P/E alone."
            ),
            points=_dedupe(points),
        )

    pe_ratio = _decimal(metrics.get("pe_ratio"))
    sector_pe = _decimal(metrics.get("sector_pe_median"))
    latest_price = _decimal(metrics.get("latest_price"))
    drawdown = _decimal(metrics.get("price_drawdown_percent"))
    summary = _valuation_status(snapshot, metrics)
    points = [
        f"Valuation score is {_fmt_score(snapshot.valuation_score)}.",
        _price_sentence(latest_price),
        _pe_comparison(pe_ratio, sector_pe),
    ]
    if drawdown is not None:
        points.append(
            f"The stock is {_fmt_percent(drawdown)} below its observed 52-week high in the stored "
            "price memory; this helps distinguish a cheaper entry from a momentum chase."
        )
    if pe_ratio is None:
        points.append(
            "The system cannot estimate a fair-value range yet because EPS or P/E support is missing."
        )
    elif sector_pe and pe_ratio <= sector_pe:
        points.append(
            "The earnings multiple is not above the sector median, so valuation is not the main "
            "reason to reject the stock today."
        )
    else:
        points.append(
            "A better entry would require either a lower price or stronger earnings that justify "
            "the current multiple."
        )
    return DecisionCardSectionRead(title="Valuation", summary=summary, points=points)


def _why_buy_section(snapshot: CompanyIntelligenceSnapshot) -> DecisionCardSectionRead:
    points = [reason for reason in snapshot.reasons if "No strong positive edge" not in reason]
    if snapshot.business_quality_score >= Decimal(70):
        points.append("Business quality score is strong, so profitability metrics are doing real work.")
    if snapshot.valuation_score >= Decimal(60):
        points.append("Valuation score is attractive enough to justify active research.")
    if snapshot.dividend_score >= Decimal(60):
        points.append("Dividend evidence adds an income component to the investment case.")
    if snapshot.liquidity_score >= Decimal(65):
        points.append("Liquidity is strong enough that the signal is more practical for retail execution.")
    if not points:
        points = [
            (
                "There is no strong buy-side edge yet. The best use of this company today is "
                "monitoring, not immediate capital allocation."
            )
        ]
    return DecisionCardSectionRead(
        title="Why It Deserves Attention",
        summary=_positive_summary(snapshot),
        points=_dedupe(points),
    )


def _why_not_buy_section(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
) -> DecisionCardSectionRead:
    points = list(snapshot.risks)
    if snapshot.missing_data:
        points.append(
            "Missing data weakens confidence: " + ", ".join(snapshot.missing_data[:5]) + "."
        )
    points.extend(_sector_threats(company.sector)[:3])
    if snapshot.final_label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}:
        summary = (
            "The company has positive signals, but the system still wants a written thesis, "
            "position-size limit, and exit rule before money is committed."
        )
    elif snapshot.final_label == "Good Company, Expensive":
        summary = "The main issue is entry discipline: a good company can still be a bad buy."
    elif snapshot.final_label == "Speculative":
        summary = "The main issue is downside control: speculative stocks need strict sizing."
    else:
        summary = "The current evidence is not strong enough to support a confident purchase."
    return DecisionCardSectionRead(
        title="Why You Should Be Careful",
        summary=summary,
        points=_dedupe(points) or ["No specific risk is stored yet; add disclosures and annual reports."],
    )


def _growth_drivers_section(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
) -> DecisionCardSectionRead:
    sector_points = _sector_growth_drivers(company.sector)
    score_points: list[str] = []
    revenue_growth = _decimal(metrics.get("revenue_growth"))
    profit_growth = _decimal(metrics.get("profit_growth"))
    eps_growth = _decimal(metrics.get("eps_growth"))
    if revenue_growth is not None:
        score_points.append(f"Revenue growth signal: {_fmt_percent(revenue_growth)}.")
    if profit_growth is not None:
        score_points.append(f"Profit growth signal: {_fmt_percent(profit_growth)}.")
    if eps_growth is not None:
        score_points.append(f"EPS growth signal: {_fmt_percent(eps_growth)}.")
    if snapshot.growth_score >= Decimal(70):
        summary = "Growth evidence is strong enough to be part of the investment thesis."
    elif snapshot.growth_score >= Decimal(45):
        summary = "Growth evidence is mixed; look for confirmation in the next report."
    else:
        summary = "Growth is not yet proven by the stored data."
    return DecisionCardSectionRead(
        title="Growth Drivers",
        summary=summary,
        points=_dedupe(score_points + sector_points),
    )


def _threats_section(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
) -> DecisionCardSectionRead:
    points = _sector_threats(company.sector)
    volatility = _decimal(metrics.get("volatility"))
    if volatility is not None and volatility > Decimal(6):
        points.append(
            f"Recent average absolute daily move is {_fmt_percent(volatility)}, so price swings "
            "may test conviction."
        )
    if snapshot.liquidity_score < Decimal(45):
        points.append("Thin liquidity can make entry and exit prices worse than the screen price.")
    if snapshot.data_confidence_score < Decimal(60):
        points.append("Low data confidence can make the current decision unstable after the next sync.")
    return DecisionCardSectionRead(
        title="Threats",
        summary="These are the issues that could weaken the investment thesis.",
        points=_dedupe(points),
    )


def _dividend_section(
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
) -> DecisionCardSectionRead:
    yield_value = _decimal(metrics.get("dividend_yield"))
    dividend_growth = _decimal(metrics.get("dividend_growth"))
    payout_safety = _decimal(metrics.get("payout_safety"))
    years = int(metrics.get("dividend_years") or 0)
    points = [
        f"Dividend score is {_fmt_score(snapshot.dividend_score)}.",
        f"Dividend years currently stored: {years}.",
    ]
    if yield_value is not None:
        points.append(f"Trailing dividend yield is {_fmt_percent(yield_value)} at the latest price.")
    else:
        points.append("Dividend yield cannot be calculated until price and dividend data align.")
    if dividend_growth is not None:
        points.append(f"Dividend growth from the latest comparable year is {_fmt_percent(dividend_growth)}.")
    if payout_safety is not None:
        points.append(f"Payout safety score is {_fmt_score(payout_safety)}.")
    summary = _dividend_quality(snapshot, metrics)
    return DecisionCardSectionRead(title="Dividend", summary=summary, points=points)


def _moat_section(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
) -> DecisionCardSectionRead:
    moat = _moat_rating(snapshot)
    points = [
        f"Moat rating is {moat}. This is a proxy rating, not a human governance review.",
        f"Stock types detected: {', '.join(snapshot.stock_types) or 'None yet'}.",
    ]
    if "Blue chip candidate" in snapshot.stock_types:
        points.append("Blue-chip evidence points to liquidity, scale, and quality metrics.")
    if "Quality compounder" in snapshot.stock_types:
        points.append("Quality-compounder evidence points to ROE, margin, leverage, and dividends.")
    points.extend(_sector_moat_factors(company.sector))
    if moat in {"Unproven", "Weak"}:
        points.append("Add qualitative notes on management, governance, brand, and competitive position.")
    return DecisionCardSectionRead(
        title="Moat",
        summary=f"{company.symbol}'s moat is currently rated {moat.lower()}.",
        points=_dedupe(points),
    )


def _future_outlook_section(
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
) -> DecisionCardSectionRead:
    quality = snapshot.business_quality_score
    growth = snapshot.growth_score
    valuation = snapshot.valuation_score
    if quality >= Decimal(70) and growth >= Decimal(60):
        summary = "Long-term outlook is constructive if earnings quality persists."
    elif quality >= Decimal(60) and valuation >= Decimal(60):
        summary = "Outlook is reasonable, but returns may depend more on valuation discipline."
    elif snapshot.final_label == "Good Company, Expensive":
        summary = "Business outlook may be fine, but expected return depends on a better entry."
    elif snapshot.final_label == "Needs Data":
        summary = "Outlook cannot be judged confidently until fundamentals history improves."
    else:
        summary = "Outlook is mixed and needs stronger evidence."
    points = [
        f"Business quality score: {_fmt_score(quality)}.",
        f"Growth score: {_fmt_score(growth)}.",
        f"Valuation score: {_fmt_score(valuation)}.",
        (
            "The system does not forecast a guaranteed price. It estimates whether the business "
            "profile, valuation, and risk are attractive enough for long-term research."
        ),
    ]
    if _decimal(metrics.get("eps_growth")) is not None:
        points.append(f"EPS growth evidence: {_fmt_percent(_decimal(metrics.get('eps_growth')))}.")
    return DecisionCardSectionRead(title="Future Outlook", summary=summary, points=points)


def _stress_test_section(
    snapshot: CompanyIntelligenceSnapshot,
    metrics: dict,
) -> DecisionCardSectionRead:
    drawdown = _decimal(metrics.get("price_drawdown_percent"))
    volatility = _decimal(metrics.get("volatility"))
    points = [
        (
            "This is currently a proxy stress test. It uses volatility, liquidity, dividend "
            "records, debt, and cash-flow evidence until crisis-tagged historical analysis is added."
        ),
        f"Financial risk score: {_fmt_score(snapshot.financial_risk_score)}.",
        f"Liquidity score: {_fmt_score(snapshot.liquidity_score)}.",
    ]
    if drawdown is not None:
        points.append(f"Stored 52-week drawdown from high: {_fmt_percent(drawdown)}.")
    if volatility is not None:
        points.append(f"Recent volatility proxy: {_fmt_percent(volatility)} average absolute move.")
    if snapshot.dividend_score >= Decimal(60):
        points.append("Dividend history adds resilience evidence, but dividend cuts remain possible.")
    if snapshot.financial_risk_score >= Decimal(75):
        summary = "Resilience looks acceptable under the current proxy stress test."
    elif snapshot.financial_risk_score >= Decimal(50):
        summary = "Resilience is mixed; review debt, cash flow, and sector exposure carefully."
    else:
        summary = "Resilience is weak under the current proxy stress test."
    return DecisionCardSectionRead(title="Stress Test", summary=summary, points=points)


def _portfolio_fit_section(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot,
) -> DecisionCardSectionRead:
    sector = company.sector or "Unknown sector"
    if snapshot.final_label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}:
        summary = "Potential fit for a disciplined long-term investor."
    elif snapshot.final_label in {"Good Company, Expensive", "Watch for Better Entry"}:
        summary = "Better fit for a watchlist than an immediate purchase."
    elif snapshot.final_label == "Speculative":
        summary = "Only fits a high-risk sleeve with strict sizing."
    else:
        summary = "Not a portfolio fit until the evidence improves."
    points = [
        f"Sector exposure: {sector}. Do not let one sector dominate the portfolio.",
        "Position-size guardrail: avoid allowing one stock to exceed 30% of portfolio value.",
        "Sector guardrail: avoid allowing one sector to exceed 50% of portfolio value.",
        (
            "Beginner rule: add to watchlist first, then buy only after the thesis, risks, and exit "
            "rule are written down."
        ),
    ]
    if "Dividend stock" in snapshot.stock_types or "Dividend history stock" in snapshot.stock_types:
        points.append("This may fit an income objective if dividend safety remains strong.")
    if "Penny/speculative stock" in snapshot.stock_types:
        points.append("Speculative stocks should be sized smaller than blue-chip or quality candidates.")
    return DecisionCardSectionRead(title="Portfolio Fit", summary=summary, points=points)


def _what_changed_section(
    snapshot: CompanyIntelligenceSnapshot,
    previous: CompanyIntelligenceSnapshot | None,
) -> DecisionCardSectionRead:
    if previous is None:
        return DecisionCardSectionRead(
            title="What Changed",
            summary="This is the first stored intelligence snapshot for this company.",
            points=[
                (
                    "Use this as the baseline. Future scans will compare score, label, stock types, "
                    "valuation, and risk against this snapshot."
                ),
                (
                    "After new fundamentals, dividends, disclosures, or price data sync, this "
                    "section will show exactly what moved the decision."
                ),
            ],
        )
    points: list[str] = []
    score_change = snapshot.overall_score - previous.overall_score
    points.append(
        f"Invest score moved from {_fmt_score(previous.overall_score)} to "
        f"{_fmt_score(snapshot.overall_score)} ({_signed_score(score_change)})."
    )
    if snapshot.final_label != previous.final_label:
        points.append(
            f"Decision label changed from {previous.final_label} to {snapshot.final_label}."
        )
    type_changes = _type_changes(previous.stock_types, snapshot.stock_types)
    points.extend(type_changes)
    points.extend(
        _score_change_points(
            previous=previous,
            current=snapshot,
            score_names=(
                ("business quality", "business_quality_score"),
                ("growth", "growth_score"),
                ("valuation", "valuation_score"),
                ("dividend", "dividend_score"),
                ("risk safety", "financial_risk_score"),
                ("liquidity", "liquidity_score"),
                ("data confidence", "data_confidence_score"),
            ),
        )
    )
    return DecisionCardSectionRead(
        title="What Changed",
        summary="Latest snapshot compared with the previous stored snapshot.",
        points=points or ["No material score, label, or stock-type change detected."],
    )


def _decision_change_section(snapshot: CompanyIntelligenceSnapshot) -> DecisionCardSectionRead:
    points = list(snapshot.decision_change_triggers)
    label = snapshot.final_label
    if label == "Needs Data":
        points.append("A synced fundamentals record with EPS/P/E and ROE can move this out of Needs Data.")
        points.append("At least 30 recent price records improve liquidity and volatility confidence.")
    elif label == "Good Company, Expensive":
        points.append("A lower price or higher EPS would improve the margin of safety.")
        points.append("If earnings weaken while the price remains high, the decision can move to avoid.")
    elif label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}:
        points.append("A large price rally without matching earnings growth can downgrade valuation.")
        points.append("A negative disclosure, dividend cut, or cash-flow deterioration can reduce conviction.")
    elif label == "Speculative":
        points.append("Sustained profit, better liquidity, and stronger data confidence can upgrade it.")
    elif label == "Avoid for Now":
        points.append("Avoid status changes only when profitability, risk, and data confidence improve together.")
    return DecisionCardSectionRead(
        title="What Would Change The Decision",
        summary="These are the triggers the system will watch in future scans.",
        points=_dedupe(points),
    )


def _data_quality_notes(
    snapshot: CompanyIntelligenceSnapshot,
    source_summary: dict,
) -> list[str]:
    notes = [
        (
            "Company memory coverage: "
            f"{int(source_summary.get('price_records') or 0)} price records, "
            f"{int(source_summary.get('fundamentals_records') or 0)} fundamentals records, "
            f"{int(source_summary.get('financial_statement_records') or 0)} financial statements, "
            f"{int(source_summary.get('dividend_records') or 0)} dividend records, "
            f"{int(source_summary.get('disclosure_records') or 0)} disclosures, "
            f"{int(source_summary.get('annual_report_records') or 0)} annual reports."
        ),
        f"Data confidence score is {_fmt_score(snapshot.data_confidence_score)}.",
    ]
    latest_price_date = source_summary.get("latest_price_date")
    latest_fundamental_date = source_summary.get("latest_fundamental_date")
    latest_statement_period_end = source_summary.get("latest_statement_period_end")
    if latest_price_date:
        notes.append(f"Latest stored price date: {latest_price_date}.")
    if latest_fundamental_date:
        notes.append(f"Latest NGX Pulse fundamentals date: {latest_fundamental_date}.")
    if latest_statement_period_end:
        notes.append(f"Latest financial statement period end: {latest_statement_period_end}.")
    if snapshot.missing_data:
        notes.append("Missing data still limiting the decision: " + ", ".join(snapshot.missing_data) + ".")
    return notes


def _positive_summary(snapshot: CompanyIntelligenceSnapshot) -> str:
    if snapshot.final_label == "Top Research Candidate":
        return "This is one of the clearest opportunities in the latest intelligence run."
    if snapshot.final_label == "Research Now":
        return "The company has enough positive evidence to deserve deeper review."
    if snapshot.final_label == "Dividend Candidate":
        return "The dividend profile is the main reason this company deserves attention."
    if snapshot.final_label == "Good Company, Expensive":
        return "The business quality may be interesting, but current valuation weakens the entry."
    return "Positive evidence exists only where listed below; do not infer more than the data supports."


def _sector_growth_drivers(sector: str | None) -> list[str]:
    sector_key = (sector or "").lower()
    if "financial" in sector_key or "bank" in sector_key:
        return [
            (
                "For financial stocks, growth can come from loan-book expansion, deposit growth, "
                "non-interest income, and cost control."
            ),
            "Watch credit quality: fast growth is not attractive if bad loans rise with it.",
            "Digital banking scale can improve fee income and operating efficiency.",
        ]
    if "ict" in sector_key or "telecom" in sector_key:
        return [
            (
                "For telecom/ICT stocks, growth can come from data consumption, subscriber growth, "
                "digital services, and enterprise connectivity."
            ),
            "Pricing power matters because network investment and spectrum costs can be heavy.",
            "A durable customer base can support long-term cash generation.",
        ]
    if "oil" in sector_key or "gas" in sector_key:
        return [
            (
                "For oil and gas stocks, growth can come from production volumes, reserve "
                "replacement, gas commercialization, and stronger realized commodity prices."
            ),
            "Cash generation matters more than revenue alone because commodity cycles can be volatile.",
            "Debt discipline is important because oil-price downturns can stress balance sheets.",
        ]
    if "industrial" in sector_key or "cement" in sector_key:
        return [
            (
                "For industrial stocks, growth can come from infrastructure demand, capacity "
                "expansion, pricing power, and operating efficiency."
            ),
            "Energy cost control matters because production can be power-intensive.",
            "Market share and distribution scale can create durable advantages.",
        ]
    if "consumer" in sector_key:
        return [
            (
                "For consumer stocks, growth can come from volume recovery, price increases, brand "
                "strength, and distribution reach."
            ),
            "Input-cost inflation can pressure margins if the company lacks pricing power.",
            "Sustained consumer demand is more important than one strong quarter.",
        ]
    if "agric" in sector_key:
        return [
            (
                "For agriculture stocks, growth can come from commodity prices, planted area, "
                "processing capacity, and export or local demand."
            ),
            "Weather, disease, and global commodity cycles can affect results quickly.",
            "Cash-flow consistency matters because earnings can move with commodity prices.",
        ]
    return [
        "Growth should come from durable revenue expansion, improving margins, and stronger cash flow.",
        "The system needs company-specific disclosures and financial statements to refine these drivers.",
    ]


def _sector_threats(sector: str | None) -> list[str]:
    sector_key = (sector or "").lower()
    if "financial" in sector_key or "bank" in sector_key:
        return [
            "Banking-sector regulation can change capital requirements, liquidity rules, or dividends.",
            "Credit losses can rise if borrowers weaken during inflation or economic stress.",
            "FX and interest-rate movements can affect earnings quality and valuation.",
        ]
    if "ict" in sector_key or "telecom" in sector_key:
        return [
            "Telecom regulation, SIM registration rules, taxes, and spectrum costs can affect earnings.",
            "FX exposure can raise equipment and financing costs.",
            "Heavy capital expenditure can pressure free cash flow even when revenue grows.",
        ]
    if "oil" in sector_key or "gas" in sector_key:
        return [
            "Commodity price declines can reduce revenue and cash flow.",
            "Production disruptions, pipeline issues, and regulatory changes can hurt earnings.",
            "FX and debt costs can amplify volatility in reported results.",
        ]
    if "industrial" in sector_key or "cement" in sector_key:
        return [
            "Energy costs, logistics costs, and weak construction demand can pressure margins.",
            "Capacity expansion can hurt returns if demand is weaker than expected.",
            "Competition can reduce pricing power in weaker demand cycles.",
        ]
    if "consumer" in sector_key:
        return [
            "Inflation can weaken consumer demand and compress margins.",
            "FX-linked input costs can rise faster than prices can be passed to customers.",
            "Competition can reduce brand pricing power.",
        ]
    if "agric" in sector_key:
        return [
            "Commodity cycles can create sharp earnings swings.",
            "Weather, disease, and export policy can affect production and pricing.",
            "Agriculture profits can look strong in one cycle and weaken in the next.",
        ]
    return [
        "Regulation, inflation, FX movements, liquidity, and weak execution can hurt the thesis.",
        "Company-specific risks need disclosures and annual-report notes for deeper confidence.",
    ]


def _sector_moat_factors(sector: str | None) -> list[str]:
    sector_key = (sector or "").lower()
    if "financial" in sector_key or "bank" in sector_key:
        return [
            (
                "Possible moat sources: deposit franchise, branch or digital distribution, brand "
                "trust, risk management, and corporate relationships."
            )
        ]
    if "ict" in sector_key or "telecom" in sector_key:
        return [
            (
                "Possible moat sources: network coverage, licenses, subscriber base, switching "
                "costs, and scale economies."
            )
        ]
    if "oil" in sector_key or "gas" in sector_key:
        return [
            (
                "Possible moat sources: reserves, operating licenses, infrastructure access, "
                "technical execution, and cash-flow discipline."
            )
        ]
    if "industrial" in sector_key or "cement" in sector_key:
        return [
            (
                "Possible moat sources: plant scale, distribution reach, energy access, brand, "
                "and local market share."
            )
        ]
    return [
        (
            "Possible moat sources to confirm: brand, distribution, licensing, management "
            "quality, cost advantage, and pricing power."
        )
    ]


def _price_sentence(latest_price: Decimal | None) -> str:
    if latest_price is None:
        return "Latest price is missing, so valuation and entry timing are incomplete."
    return f"Latest stored price is {_fmt_money(latest_price)}."


def _valuation_text(valuation: CompanyValuationRead | None) -> str:
    if not valuation:
        return "Full fair-value valuation is not available yet."
    if valuation.fair_value_mid is None:
        return (
            f"Valuation label is {valuation.valuation_label.lower()}, but the system could not "
            "calculate a fair-value midpoint yet."
        )
    return (
        f"Estimated fair value is {_fmt_money(valuation.fair_value_low)} to "
        f"{_fmt_money(valuation.fair_value_high)}, with midpoint "
        f"{_fmt_money(valuation.fair_value_mid)}. Margin of safety to midpoint is "
        f"{_fmt_percent(valuation.margin_of_safety_percent)}."
    )


def _fair_value_range_sentence(valuation: CompanyValuationRead) -> str:
    if valuation.fair_value_low is None or valuation.fair_value_high is None:
        return "Fair value range cannot be estimated yet."
    return (
        f"Fair value range is {_fmt_money(valuation.fair_value_low)} to "
        f"{_fmt_money(valuation.fair_value_high)}, with midpoint "
        f"{_fmt_money(valuation.fair_value_mid)}."
    )


def _expected_return_sentence(valuation: CompanyValuationRead) -> str:
    if valuation.expected_return_low_percent is None or valuation.expected_return_high_percent is None:
        return "Expected return range cannot be estimated because latest price is missing."
    return (
        f"Expected return to fair-value range is "
        f"{_fmt_percent(valuation.expected_return_low_percent)} to "
        f"{_fmt_percent(valuation.expected_return_high_percent)} before dividends and future "
        "earnings changes."
    )


def _method_range_sentence(method) -> str:
    if method.fair_value_low is None or method.fair_value_high is None:
        return "No range available."
    return (
        f"method range {_fmt_money(method.fair_value_low)} to "
        f"{_fmt_money(method.fair_value_high)}."
    )


def _pe_comparison(pe_ratio: Decimal | None, sector_pe: Decimal | None) -> str:
    if pe_ratio is None or pe_ratio <= 0:
        return "P/E is missing or not meaningful, so earnings-based valuation is incomplete."
    if sector_pe is None or sector_pe <= 0:
        return f"P/E is {_fmt_number(pe_ratio)}; sector median P/E is not available."
    difference = ((pe_ratio - sector_pe) / sector_pe * HUNDRED).quantize(Decimal("0.01"))
    if difference <= Decimal(-10):
        return (
            f"P/E is {_fmt_number(pe_ratio)} versus sector median {_fmt_number(sector_pe)}, "
            f"about {_fmt_percent(abs(difference))} cheaper than peers."
        )
    if difference >= Decimal(10):
        return (
            f"P/E is {_fmt_number(pe_ratio)} versus sector median {_fmt_number(sector_pe)}, "
            f"about {_fmt_percent(difference)} more expensive than peers."
        )
    return (
        f"P/E is {_fmt_number(pe_ratio)} versus sector median {_fmt_number(sector_pe)}, "
        "roughly in line with peers."
    )


def _score_change_points(
    previous: CompanyIntelligenceSnapshot,
    current: CompanyIntelligenceSnapshot,
    score_names: tuple[tuple[str, str], ...],
) -> list[str]:
    points: list[str] = []
    for label, attr in score_names:
        old = getattr(previous, attr)
        new = getattr(current, attr)
        change = new - old
        if abs(change) >= Decimal(5):
            direction = "improved" if change > 0 else "weakened"
            points.append(
                f"{label.title()} {direction} from {_fmt_score(old)} to {_fmt_score(new)} "
                f"({_signed_score(change)})."
            )
    return points


def _type_changes(previous_types: list[str], current_types: list[str]) -> list[str]:
    previous = set(previous_types)
    current = set(current_types)
    points = []
    added = sorted(current - previous)
    removed = sorted(previous - current)
    if added:
        points.append("New stock type evidence added: " + ", ".join(added) + ".")
    if removed:
        points.append("Stock type evidence removed: " + ", ".join(removed) + ".")
    return points


def _metric_sentence(
    label: str,
    value: Decimal | None,
    suffix: str = "",
) -> str | None:
    if value is None:
        return None
    if suffix == "%":
        return f"{label}: {_fmt_percent(value)}."
    return f"{label}: {_fmt_number(value)}."


def _fmt_score(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}/100"


def _fmt_money(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return "₦" + f"{value.quantize(Decimal('0.01')):,.2f}"


def _fmt_number(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,}"


def _fmt_percent(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value.quantize(Decimal('0.01'))}%"


def _signed_score(value: Decimal) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value.quantize(Decimal('0.01'))}"


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal(0), min(HUNDRED, value))


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date(value):
    if value in (None, ""):
        return None
    try:
        from datetime import date

        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result
