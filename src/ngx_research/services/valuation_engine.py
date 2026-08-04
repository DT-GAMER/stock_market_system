from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from threading import Lock

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyIntelligenceSnapshot,
    CompanyValuationSnapshot,
    FinancialStatement,
    NgxPulseFundamental,
)
from ngx_research.schemas import (
    CompanyValuationRead,
    ValuationMethodRead,
    ValuationRunRead,
)

HUNDRED = Decimal(100)
_VALUATION_RUN_LOCK = Lock()


@dataclass(frozen=True)
class MethodValuation:
    name: str
    fair_value_low: Decimal | None
    fair_value_mid: Decimal | None
    fair_value_high: Decimal | None
    confidence_score: Decimal
    reason: str
    assumptions: list[str]
    warnings: list[str]


def run_valuation_engine(
    session: Session,
    as_of_date: date | None = None,
    limit: int | None = None,
) -> ValuationRunRead:
    with _VALUATION_RUN_LOCK:
        return _run_valuation_engine_locked(session=session, as_of_date=as_of_date, limit=limit)


def _run_valuation_engine_locked(
    session: Session,
    as_of_date: date | None = None,
    limit: int | None = None,
) -> ValuationRunRead:
    valuation_date = as_of_date or _latest_intelligence_date(session) or datetime.now(UTC).date()
    rows = _latest_intelligence_rows_for_date(session, valuation_date)
    session.execute(
        delete(CompanyValuationSnapshot)
        .where(CompanyValuationSnapshot.as_of_date == valuation_date)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    generated: list[CompanyValuationSnapshot] = []
    for intelligence, company in rows:
        fundamental = _latest_fundamental(session, company.id, valuation_date)
        statement = _latest_statement(session, company.id, valuation_date)
        valuation = _build_valuation_snapshot(company, intelligence, fundamental, statement)
        session.add(valuation)
        generated.append(valuation)
    session.commit()
    return ValuationRunRead(
        as_of_date=valuation_date,
        generated=len(generated),
        valuations=latest_valuations(session, limit=limit or 100),
    )


def _latest_intelligence_rows_for_date(
    session: Session,
    valuation_date: date,
) -> list[tuple[CompanyIntelligenceSnapshot, Company]]:
    rows = session.execute(
        select(CompanyIntelligenceSnapshot, Company)
        .join(Company, Company.id == CompanyIntelligenceSnapshot.company_id)
        .where(
            CompanyIntelligenceSnapshot.as_of_date == valuation_date,
            Company.is_active.is_(True),
        )
        .order_by(Company.symbol, desc(CompanyIntelligenceSnapshot.id))
    )
    by_company: dict[int, tuple[CompanyIntelligenceSnapshot, Company]] = {}
    for intelligence, company in rows:
        by_company.setdefault(company.id, (intelligence, company))
    return list(by_company.values())


def latest_valuations(session: Session, limit: int = 100) -> list[CompanyValuationRead]:
    latest_date = session.scalar(select(func.max(CompanyValuationSnapshot.as_of_date)))
    if latest_date is None:
        return []
    rows = session.execute(
        select(CompanyValuationSnapshot, Company)
        .join(Company, Company.id == CompanyValuationSnapshot.company_id)
        .where(CompanyValuationSnapshot.as_of_date == latest_date)
        .order_by(desc(CompanyValuationSnapshot.margin_of_safety_percent), Company.symbol)
        .limit(limit)
    )
    return [_valuation_read(valuation, company) for valuation, company in rows]


def company_valuation(session: Session, symbol: str) -> CompanyValuationRead:
    normalized = symbol.strip().upper()
    row = session.execute(
        select(CompanyValuationSnapshot, Company)
        .join(Company, Company.id == CompanyValuationSnapshot.company_id)
        .where(Company.symbol == normalized)
        .order_by(desc(CompanyValuationSnapshot.as_of_date), desc(CompanyValuationSnapshot.id))
        .limit(1)
    ).first()
    if not row:
        raise ValueError(
            f"No valuation snapshot found for {normalized}. "
            "Run POST /valuation/run after syncing data and intelligence."
        )
    valuation, company = row
    return _valuation_read(valuation, company)


def latest_company_valuation_snapshot(
    session: Session,
    company_id: int,
) -> tuple[CompanyValuationSnapshot, Company] | None:
    return session.execute(
        select(CompanyValuationSnapshot, Company)
        .join(Company, Company.id == CompanyValuationSnapshot.company_id)
        .where(CompanyValuationSnapshot.company_id == company_id)
        .order_by(desc(CompanyValuationSnapshot.as_of_date), desc(CompanyValuationSnapshot.id))
        .limit(1)
    ).first()


def valuation_snapshot_read(
    valuation: CompanyValuationSnapshot,
    company: Company,
) -> CompanyValuationRead:
    return _valuation_read(valuation, company)


def _build_valuation_snapshot(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
    fundamental: NgxPulseFundamental | None,
    statement: FinancialStatement | None,
) -> CompanyValuationSnapshot:
    metrics = intelligence.metrics or {}
    source_summary = intelligence.source_summary or {}
    latest_price = _decimal(metrics.get("latest_price"))
    latest_price_date = _date(source_summary.get("latest_price_date"))
    methods = _valuation_methods(company, intelligence, fundamental, statement)
    missing_data = _valuation_missing_data(intelligence, fundamental, latest_price, methods)
    fair_value_low, fair_value_mid, fair_value_high = _combine_methods(methods)
    expected_low = _percent_return(fair_value_low, latest_price)
    expected_high = _percent_return(fair_value_high, latest_price)
    margin_of_safety = _percent_return(fair_value_mid, latest_price)
    confidence_score = _confidence_score(intelligence, methods, missing_data)
    valuation_label = _valuation_label(latest_price, fair_value_mid, margin_of_safety)
    warnings = _warnings(company, intelligence, methods, missing_data, margin_of_safety)
    reasons = _reasons(latest_price, fair_value_low, fair_value_high, margin_of_safety, methods)
    assumptions = _assumptions(company, intelligence, methods)
    return CompanyValuationSnapshot(
        company_id=company.id,
        intelligence_snapshot_id=intelligence.id,
        as_of_date=intelligence.as_of_date,
        sector=company.sector,
        latest_price=latest_price,
        latest_price_date=latest_price_date,
        fair_value_low=fair_value_low,
        fair_value_mid=fair_value_mid,
        fair_value_high=fair_value_high,
        margin_of_safety_percent=margin_of_safety,
        expected_return_low_percent=expected_low,
        expected_return_high_percent=expected_high,
        valuation_label=valuation_label,
        valuation_confidence=_confidence_label(confidence_score),
        confidence_score=confidence_score,
        methods=[_method_json(method) for method in methods],
        assumptions=assumptions,
        reasons=reasons,
        warnings=warnings,
        missing_data=missing_data,
        metrics=_valuation_metrics(metrics, fundamental, statement),
        source_summary=source_summary,
    )


def _valuation_methods(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
    fundamental: NgxPulseFundamental | None,
    statement: FinancialStatement | None,
) -> list[MethodValuation]:
    methods: list[MethodValuation] = []
    for method in (
        _sector_pe_method(company, intelligence),
        _earnings_power_method(company, intelligence, fundamental, statement),
        _dividend_yield_method(company, intelligence, fundamental),
        _book_value_method(company, intelligence, fundamental),
    ):
        if method:
            methods.append(method)
    return methods


def _sector_pe_method(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
) -> MethodValuation | None:
    metrics = intelligence.metrics or {}
    latest_price = _decimal(metrics.get("latest_price"))
    pe_ratio = _decimal(metrics.get("pe_ratio"))
    sector_pe = _decimal(metrics.get("sector_pe_median"))
    if latest_price is None or pe_ratio is None or sector_pe is None or pe_ratio <= 0 or sector_pe <= 0:
        return None
    justified_pe = _quality_adjusted_multiple(sector_pe, intelligence)
    fair_mid = latest_price * justified_pe / pe_ratio
    fair_low = fair_mid * Decimal("0.90")
    fair_high = fair_mid * Decimal("1.10")
    confidence = _method_confidence(intelligence, Decimal(75))
    comparison = "below" if pe_ratio < sector_pe else "above"
    return MethodValuation(
        name="Sector P/E comparison",
        fair_value_low=_money(fair_low),
        fair_value_mid=_money(fair_mid),
        fair_value_high=_money(fair_high),
        confidence_score=confidence,
        reason=(
            f"{company.symbol} trades at P/E {_fmt_number(pe_ratio)} versus sector median "
            f"{_fmt_number(sector_pe)}. The model adjusts the sector multiple for business "
            f"quality and risk, then values the stock around P/E {_fmt_number(justified_pe)}."
        ),
        assumptions=[
            f"Current P/E is {comparison} the sector median.",
            "Sector peers are a reasonable first comparison set for this company.",
            "A 10% range is applied around the adjusted sector multiple.",
        ],
        warnings=[
            "Sector P/E can be misleading if the whole sector is overvalued or temporarily depressed."
        ],
    )


def _earnings_power_method(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
    fundamental: NgxPulseFundamental | None,
    statement: FinancialStatement | None,
) -> MethodValuation | None:
    metrics = intelligence.metrics or {}
    eps, eps_source = _eps(fundamental, statement, metrics)
    if eps is None or eps <= 0:
        return None
    sector_pe = _decimal(metrics.get("sector_pe_median"))
    justified_pe = _justified_pe(company.sector, sector_pe, intelligence)
    fair_mid = eps * justified_pe
    fair_low = fair_mid * Decimal("0.82")
    fair_high = fair_mid * Decimal("1.18")
    confidence = _method_confidence(
        intelligence,
        Decimal(80) if eps_source == "NGX Pulse EPS" else Decimal(62),
    )
    return MethodValuation(
        name="Earnings power valuation",
        fair_value_low=_money(fair_low),
        fair_value_mid=_money(fair_mid),
        fair_value_high=_money(fair_high),
        confidence_score=confidence,
        reason=(
            f"Earnings power values the company by applying a justified P/E of "
            f"{_fmt_number(justified_pe)} to {_fmt_number(eps)} EPS from {eps_source}."
        ),
        assumptions=[
            "EPS represents a sustainable earnings base rather than a one-off spike.",
            "The justified P/E is adjusted for quality, growth, risk, and sector context.",
            "An 18% range is used because earnings can change after new results.",
        ],
        warnings=[
            "If EPS falls, the fair value range falls quickly.",
            "This is not a price forecast; it is an estimate of earnings-supported value.",
        ],
    )


def _dividend_yield_method(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
    fundamental: NgxPulseFundamental | None,
) -> MethodValuation | None:
    metrics = intelligence.metrics or {}
    latest_price = _decimal(metrics.get("latest_price"))
    dividend_yield = _decimal(metrics.get("dividend_yield"))
    if latest_price is None or dividend_yield is None or dividend_yield <= 0:
        return None
    dividend_per_share = _decimal(fundamental.dividend_per_share if fundamental else None)
    if dividend_per_share is None:
        dividend_per_share = latest_price * dividend_yield / HUNDRED
    if dividend_per_share <= 0:
        return None
    target_yield = _target_dividend_yield(company.sector, intelligence)
    low_target = target_yield + Decimal("1.00")
    high_target = max(target_yield - Decimal("1.00"), Decimal("1.00"))
    fair_low = dividend_per_share / (low_target / HUNDRED)
    fair_mid = dividend_per_share / (target_yield / HUNDRED)
    fair_high = dividend_per_share / (high_target / HUNDRED)
    confidence = _method_confidence(intelligence, Decimal(64))
    return MethodValuation(
        name="Dividend yield support",
        fair_value_low=_money(fair_low),
        fair_value_mid=_money(fair_mid),
        fair_value_high=_money(fair_high),
        confidence_score=confidence,
        reason=(
            f"Dividend support estimates what price would make the current dividend of "
            f"{_fmt_money(dividend_per_share)} produce a fair yield around "
            f"{_fmt_percent(target_yield)}."
        ),
        assumptions=[
            "The latest dividend level is repeatable.",
            "Income investors would consider the target yield reasonable for this sector and risk.",
            "A one percentage-point yield range is applied around the target yield.",
        ],
        warnings=[
            "Dividend valuation is weak if earnings do not cover the dividend.",
            "A dividend cut would immediately reduce this fair value estimate.",
        ],
    )


def _book_value_method(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
    fundamental: NgxPulseFundamental | None,
) -> MethodValuation | None:
    if not _is_financial_sector(company.sector):
        return None
    metrics = intelligence.metrics or {}
    latest_price = _decimal(metrics.get("latest_price"))
    pb_ratio = _decimal(fundamental.pb_ratio if fundamental else None)
    roe = _decimal(metrics.get("roe"))
    if latest_price is None or pb_ratio is None or pb_ratio <= 0 or roe is None:
        return None
    book_value_per_share = latest_price / pb_ratio
    fair_pb = _justified_pb(roe, intelligence)
    fair_mid = book_value_per_share * fair_pb
    fair_low = fair_mid * Decimal("0.85")
    fair_high = fair_mid * Decimal("1.15")
    confidence = _method_confidence(intelligence, Decimal(70))
    return MethodValuation(
        name="Book value and ROE support",
        fair_value_low=_money(fair_low),
        fair_value_mid=_money(fair_mid),
        fair_value_high=_money(fair_high),
        confidence_score=confidence,
        reason=(
            f"For financial stocks, book value matters. The model estimates book value per share "
            f"at {_fmt_money(book_value_per_share)} and applies justified P/B "
            f"{_fmt_number(fair_pb)} based on ROE {_fmt_percent(roe)}."
        ),
        assumptions=[
            "Reported book value is reliable and not materially overstated.",
            "ROE is sustainable enough to justify the selected P/B multiple.",
            "This method is mainly used for banks and other financial companies.",
        ],
        warnings=[
            "P/B valuation can fail if asset quality, credit losses, or regulation weaken book value."
        ],
    )


def _combine_methods(
    methods: list[MethodValuation],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    valid = [
        method
        for method in methods
        if method.fair_value_low is not None
        and method.fair_value_mid is not None
        and method.fair_value_high is not None
    ]
    if not valid:
        return None, None, None
    weight_sum = sum(method.confidence_score for method in valid)
    if weight_sum <= 0:
        return None, None, None
    low = sum((method.fair_value_low or Decimal(0)) * method.confidence_score for method in valid)
    mid = sum((method.fair_value_mid or Decimal(0)) * method.confidence_score for method in valid)
    high = sum((method.fair_value_high or Decimal(0)) * method.confidence_score for method in valid)
    return _money(low / weight_sum), _money(mid / weight_sum), _money(high / weight_sum)


def _valuation_missing_data(
    intelligence: CompanyIntelligenceSnapshot,
    fundamental: NgxPulseFundamental | None,
    latest_price: Decimal | None,
    methods: list[MethodValuation],
) -> list[str]:
    metrics = intelligence.metrics or {}
    missing: list[str] = []
    if latest_price is None:
        missing.append("latest price")
    if _decimal(metrics.get("pe_ratio")) is None:
        missing.append("P/E ratio")
    if fundamental is None:
        missing.append("NGX Pulse fundamentals")
    if fundamental and fundamental.eps is None and _decimal(metrics.get("pe_ratio")) is None:
        missing.append("EPS")
    if _decimal(metrics.get("sector_pe_median")) is None:
        missing.append("sector P/E median")
    if not methods:
        missing.append("usable valuation method")
    return _dedupe(missing)


def _valuation_label(
    latest_price: Decimal | None,
    fair_value_mid: Decimal | None,
    margin_of_safety: Decimal | None,
) -> str:
    if latest_price is None:
        return "Price Missing"
    if fair_value_mid is None or margin_of_safety is None:
        return "Cannot Value Yet"
    if margin_of_safety >= Decimal(25):
        return "Deeply Undervalued"
    if margin_of_safety >= Decimal(10):
        return "Undervalued"
    if margin_of_safety >= Decimal(-10):
        return "Fairly Valued"
    if margin_of_safety >= Decimal(-25):
        return "Expensive"
    return "Overvalued"


def _confidence_score(
    intelligence: CompanyIntelligenceSnapshot,
    methods: list[MethodValuation],
    missing_data: list[str],
) -> Decimal:
    if not methods:
        return Decimal(0)
    method_confidence = sum(method.confidence_score for method in methods) / Decimal(len(methods))
    method_coverage = min(Decimal(len(methods)) / Decimal(3) * HUNDRED, HUNDRED)
    missing_penalty = min(Decimal(len(missing_data) * 8), Decimal(35))
    score = (
        intelligence.data_confidence_score * Decimal("0.35")
        + method_confidence * Decimal("0.45")
        + method_coverage * Decimal("0.20")
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


def _warnings(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
    methods: list[MethodValuation],
    missing_data: list[str],
    margin_of_safety: Decimal | None,
) -> list[str]:
    warnings: list[str] = []
    for method in methods:
        warnings.extend(method.warnings)
    if missing_data:
        warnings.append("Missing data reduces valuation reliability: " + ", ".join(missing_data) + ".")
    if "Penny/speculative stock" in intelligence.stock_types:
        warnings.append("Speculative stocks should not be valued with normal blue-chip confidence.")
    if margin_of_safety is not None and margin_of_safety < Decimal(-10):
        warnings.append("Current price is above estimated fair value; entry discipline is required.")
    if _is_cyclical_sector(company.sector):
        warnings.append("Cyclical sectors can look cheap near peak earnings and expensive near troughs.")
    return _dedupe(warnings)


def _reasons(
    latest_price: Decimal | None,
    fair_value_low: Decimal | None,
    fair_value_high: Decimal | None,
    margin_of_safety: Decimal | None,
    methods: list[MethodValuation],
) -> list[str]:
    if latest_price is None or fair_value_low is None or fair_value_high is None:
        return ["Fair value cannot be estimated until price and valuation inputs are available."]
    reasons = [
        (
            f"Estimated fair value range is {_fmt_money(fair_value_low)} to "
            f"{_fmt_money(fair_value_high)} versus latest price {_fmt_money(latest_price)}."
        ),
        f"Margin of safety to midpoint is {_fmt_percent(margin_of_safety)}.",
        f"Valuation uses {len(methods)} method(s): "
        + ", ".join(method.name for method in methods)
        + ".",
    ]
    if margin_of_safety is not None and margin_of_safety >= Decimal(10):
        reasons.append("The midpoint fair value is meaningfully above the current price.")
    elif margin_of_safety is not None and margin_of_safety < Decimal(-10):
        reasons.append("The current price is meaningfully above the midpoint fair value.")
    else:
        reasons.append("Current price sits close to estimated fair value; margin of safety is limited.")
    return reasons


def _assumptions(
    company: Company,
    intelligence: CompanyIntelligenceSnapshot,
    methods: list[MethodValuation],
) -> list[str]:
    assumptions = [
        "Fair value is a range, not a guaranteed future price.",
        "The model values the stock using currently stored EquityKobo data.",
        "New earnings, dividends, disclosures, or prices can change the valuation immediately.",
        f"{company.symbol} should still be compared with sector peers before buying.",
    ]
    if intelligence.final_label in {"Needs Data", "Avoid for Now"}:
        assumptions.append("The decision label is weak, so valuation should not override risk warnings.")
    for method in methods:
        assumptions.extend(method.assumptions)
    return _dedupe(assumptions)


def _valuation_metrics(
    metrics: dict,
    fundamental: NgxPulseFundamental | None,
    statement: FinancialStatement | None,
) -> dict:
    eps, eps_source = _eps(fundamental, statement, metrics)
    return {
        **metrics,
        "eps": _json_decimal(eps),
        "eps_source": eps_source,
        "forward_pe": _json_decimal(fundamental.forward_pe if fundamental else None),
        "pb_ratio": _json_decimal(fundamental.pb_ratio if fundamental else None),
        "dividend_per_share": _json_decimal(
            fundamental.dividend_per_share if fundamental else None
        ),
        "latest_statement_period_end": statement.period_end.isoformat() if statement else None,
    }


def _latest_intelligence_date(session: Session) -> date | None:
    return session.scalar(select(func.max(CompanyIntelligenceSnapshot.as_of_date)))


def _latest_fundamental(
    session: Session,
    company_id: int,
    as_of_date: date,
) -> NgxPulseFundamental | None:
    return session.scalar(
        select(NgxPulseFundamental)
        .where(
            NgxPulseFundamental.company_id == company_id,
            NgxPulseFundamental.as_of_date <= as_of_date,
        )
        .order_by(desc(NgxPulseFundamental.as_of_date), desc(NgxPulseFundamental.id))
        .limit(1)
    )


def _latest_statement(
    session: Session,
    company_id: int,
    as_of_date: date,
) -> FinancialStatement | None:
    return session.scalar(
        select(FinancialStatement)
        .where(
            FinancialStatement.company_id == company_id,
            FinancialStatement.period_end <= as_of_date,
        )
        .order_by(desc(FinancialStatement.period_end), desc(FinancialStatement.id))
        .limit(1)
    )


def _eps(
    fundamental: NgxPulseFundamental | None,
    statement: FinancialStatement | None,
    metrics: dict,
) -> tuple[Decimal | None, str]:
    if fundamental and fundamental.eps is not None:
        return fundamental.eps, "NGX Pulse EPS"
    if statement and statement.eps is not None:
        return statement.eps, "financial statement EPS"
    latest_price = _decimal(metrics.get("latest_price"))
    pe_ratio = _decimal(metrics.get("pe_ratio"))
    if latest_price is not None and pe_ratio is not None and pe_ratio > 0:
        return (latest_price / pe_ratio).quantize(Decimal("0.0001")), "price/P/E implied EPS"
    return None, "missing EPS"


def _quality_adjusted_multiple(
    sector_pe: Decimal,
    intelligence: CompanyIntelligenceSnapshot,
) -> Decimal:
    quality_adjustment = Decimal("0.85") + (intelligence.business_quality_score / HUNDRED) * Decimal("0.30")
    growth_adjustment = Decimal("0.90") + (intelligence.growth_score / HUNDRED) * Decimal("0.20")
    risk_adjustment = Decimal("0.85") + (intelligence.financial_risk_score / HUNDRED) * Decimal("0.20")
    multiple = sector_pe * quality_adjustment * growth_adjustment * risk_adjustment
    return _clamp_multiple(multiple, sector_pe * Decimal("0.60"), sector_pe * Decimal("1.30"))


def _justified_pe(
    sector: str | None,
    sector_pe: Decimal | None,
    intelligence: CompanyIntelligenceSnapshot,
) -> Decimal:
    base = sector_pe if sector_pe and sector_pe > 0 else _default_sector_pe(sector)
    return _quality_adjusted_multiple(base, intelligence)


def _default_sector_pe(sector: str | None) -> Decimal:
    sector_key = (sector or "").lower()
    if "financial" in sector_key or "bank" in sector_key:
        return Decimal(7)
    if "oil" in sector_key or "gas" in sector_key:
        return Decimal(8)
    if "industrial" in sector_key or "cement" in sector_key:
        return Decimal(12)
    if "consumer" in sector_key:
        return Decimal(14)
    if "ict" in sector_key or "telecom" in sector_key:
        return Decimal(13)
    return Decimal(10)


def _target_dividend_yield(
    sector: str | None,
    intelligence: CompanyIntelligenceSnapshot,
) -> Decimal:
    sector_key = (sector or "").lower()
    target = Decimal("4.50")
    if "financial" in sector_key or "bank" in sector_key:
        target = Decimal("5.00")
    elif "oil" in sector_key or "gas" in sector_key:
        target = Decimal("5.50")
    elif "ict" in sector_key or "telecom" in sector_key:
        target = Decimal("4.00")
    elif "industrial" in sector_key or "cement" in sector_key:
        target = Decimal("4.25")
    if "Blue chip candidate" in intelligence.stock_types:
        target -= Decimal("0.35")
    if intelligence.financial_risk_score < Decimal(55):
        target += Decimal("1.00")
    return max(target, Decimal("2.50"))


def _justified_pb(
    roe: Decimal,
    intelligence: CompanyIntelligenceSnapshot,
) -> Decimal:
    if roe >= Decimal(30):
        fair_pb = Decimal("1.80")
    elif roe >= Decimal(20):
        fair_pb = Decimal("1.45")
    elif roe >= Decimal(12):
        fair_pb = Decimal("1.05")
    else:
        fair_pb = Decimal("0.75")
    if intelligence.financial_risk_score < Decimal(60):
        fair_pb *= Decimal("0.85")
    if intelligence.business_quality_score >= Decimal(75):
        fair_pb *= Decimal("1.10")
    return fair_pb.quantize(Decimal("0.0001"))


def _method_confidence(
    intelligence: CompanyIntelligenceSnapshot,
    base: Decimal,
) -> Decimal:
    score = base * Decimal("0.55") + intelligence.data_confidence_score * Decimal("0.45")
    if intelligence.final_label == "Needs Data":
        score -= Decimal(15)
    if intelligence.final_label == "Avoid for Now":
        score -= Decimal(8)
    return _clamp(score).quantize(Decimal("0.01"))


def _percent_return(
    fair_value: Decimal | None,
    latest_price: Decimal | None,
) -> Decimal | None:
    if fair_value is None or latest_price in (None, Decimal(0)):
        return None
    return (((fair_value - latest_price) / latest_price) * HUNDRED).quantize(Decimal("0.0001"))


def _valuation_read(
    valuation: CompanyValuationSnapshot,
    company: Company,
) -> CompanyValuationRead:
    return CompanyValuationRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        as_of_date=valuation.as_of_date,
        latest_price=valuation.latest_price,
        latest_price_date=valuation.latest_price_date,
        fair_value_low=valuation.fair_value_low,
        fair_value_mid=valuation.fair_value_mid,
        fair_value_high=valuation.fair_value_high,
        margin_of_safety_percent=valuation.margin_of_safety_percent,
        expected_return_low_percent=valuation.expected_return_low_percent,
        expected_return_high_percent=valuation.expected_return_high_percent,
        valuation_label=valuation.valuation_label,
        valuation_confidence=valuation.valuation_confidence,
        confidence_score=valuation.confidence_score,
        methods=[_method_read(method) for method in valuation.methods],
        assumptions=valuation.assumptions,
        reasons=valuation.reasons,
        warnings=valuation.warnings,
        missing_data=valuation.missing_data,
        metrics=valuation.metrics,
        source_summary=valuation.source_summary,
    )


def _method_json(method: MethodValuation) -> dict:
    return {
        "name": method.name,
        "fair_value_low": _json_decimal(method.fair_value_low),
        "fair_value_mid": _json_decimal(method.fair_value_mid),
        "fair_value_high": _json_decimal(method.fair_value_high),
        "confidence_score": _json_decimal(method.confidence_score),
        "reason": method.reason,
        "assumptions": method.assumptions,
        "warnings": method.warnings,
    }


def _method_read(value: dict) -> ValuationMethodRead:
    return ValuationMethodRead(
        name=str(value.get("name") or "Unnamed valuation method"),
        fair_value_low=_decimal(value.get("fair_value_low")),
        fair_value_mid=_decimal(value.get("fair_value_mid")),
        fair_value_high=_decimal(value.get("fair_value_high")),
        confidence_score=_decimal(value.get("confidence_score")) or Decimal(0),
        reason=str(value.get("reason") or "No reason recorded."),
        assumptions=list(value.get("assumptions") or []),
        warnings=list(value.get("warnings") or []),
    )


def _is_financial_sector(sector: str | None) -> bool:
    sector_key = (sector or "").lower()
    return "financial" in sector_key or "bank" in sector_key or "insurance" in sector_key


def _is_cyclical_sector(sector: str | None) -> bool:
    sector_key = (sector or "").lower()
    return any(keyword in sector_key for keyword in ("oil", "gas", "agric", "industrial"))


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal(0), min(HUNDRED, value))


def _clamp_multiple(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value)).quantize(Decimal("0.0001"))


def _money(value: Decimal | None) -> Decimal | None:
    return value.quantize(Decimal("0.0001")) if value is not None else None


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _json_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _fmt_money(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return "₦" + f"{value.quantize(Decimal('0.01')):,.2f}"


def _fmt_number(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value.quantize(Decimal('0.01')):,}"


def _fmt_percent(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
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
