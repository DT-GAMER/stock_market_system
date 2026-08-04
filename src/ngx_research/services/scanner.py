from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyRatio,
    CompanyScore,
    Dividend,
    FinancialStatement,
    NgxPulseFundamental,
    Price,
    ScanResult,
    ScanRun,
)

HUNDRED = Decimal(100)


@dataclass
class ScanSummary:
    scan_run_id: int
    scored: int
    insufficient_data: int


@dataclass(frozen=True)
class SectorProfile:
    group: str
    quality_weight: Decimal
    valuation_weight: Decimal
    growth_weight: Decimal
    dividend_weight: Decimal
    risk_weight: Decimal
    attractive_pe: Decimal
    expensive_pe: Decimal
    strong_roe: Decimal
    strong_margin: Decimal


@dataclass
class ScanContext:
    latest_prices: dict[int, Price]
    latest_statements: dict[int, FinancialStatement]
    prior_statements: dict[int, FinancialStatement]
    latest_fundamentals: dict[int, NgxPulseFundamental]
    dividend_totals: dict[int, Decimal]


def run_market_scan(session: Session, as_of_date: date | None = None) -> ScanSummary:
    scan_date = as_of_date or datetime.now(UTC).date()
    _clear_existing_scan_for_date(session, scan_date)

    scan_run = ScanRun(as_of_date=scan_date, universe="all")
    session.add(scan_run)
    session.flush()

    scores: list[CompanyScore] = []
    ratio_score_pairs: list[tuple[CompanyRatio, CompanyScore]] = []
    insufficient_data = 0
    companies = session.scalars(select(Company).where(Company.is_active.is_(True))).all()
    context = _scan_context(session, scan_date)

    for company in companies:
        ratio = _calculate_ratios_from_context(company, scan_date, context)
        score = score_company(company, ratio)
        if score.status == "Insufficient data":
            insufficient_data += 1
        session.add(ratio)
        ratio_score_pairs.append((ratio, score))
        scores.append(score)

    session.flush()

    for ratio, score in ratio_score_pairs:
        score.ratio_id = ratio.id
        session.add(score)

    session.flush()

    ranked_scores = sorted(scores, key=lambda item: item.overall_score, reverse=True)
    for rank, score in enumerate(ranked_scores, start=1):
        session.add(
            ScanResult(
                scan_run_id=scan_run.id,
                company_id=score.company_id,
                score_id=score.id,
                rank=rank,
            )
        )

    session.commit()
    return ScanSummary(
        scan_run_id=scan_run.id,
        scored=len(scores),
        insufficient_data=insufficient_data,
    )


def _scan_context(session: Session, as_of_date: date) -> ScanContext:
    latest_prices: dict[int, Price] = {}
    prices = session.scalars(
        select(Price)
        .where(Price.trade_date <= as_of_date)
        .order_by(Price.company_id, desc(Price.trade_date), desc(Price.id))
    )
    for price in prices:
        latest_prices.setdefault(price.company_id, price)

    latest_statements: dict[int, FinancialStatement] = {}
    prior_statements: dict[int, FinancialStatement] = {}
    statements = session.scalars(
        select(FinancialStatement)
        .where(FinancialStatement.period_end <= as_of_date)
        .order_by(FinancialStatement.company_id, desc(FinancialStatement.period_end), desc(FinancialStatement.id))
    )
    for statement in statements:
        latest = latest_statements.get(statement.company_id)
        if latest is None:
            latest_statements[statement.company_id] = statement
            continue
        if (
            statement.company_id not in prior_statements
            and statement.period_type == latest.period_type
            and statement.period_end < latest.period_end
        ):
            prior_statements[statement.company_id] = statement

    dividend_since = as_of_date - timedelta(days=365)
    dividend_totals = {
        company_id: total
        for company_id, total in session.execute(
            select(Dividend.company_id, func.sum(Dividend.amount_per_share)).where(
                Dividend.payment_date.is_not(None),
                Dividend.payment_date >= dividend_since,
                Dividend.payment_date <= as_of_date,
            ).group_by(Dividend.company_id)
        )
    }

    latest_fundamentals: dict[int, NgxPulseFundamental] = {}
    fundamentals = session.scalars(
        select(NgxPulseFundamental)
        .where(NgxPulseFundamental.as_of_date <= as_of_date)
        .order_by(
            NgxPulseFundamental.company_id,
            desc(NgxPulseFundamental.as_of_date),
            desc(NgxPulseFundamental.id),
        )
    )
    for fundamental in fundamentals:
        latest_fundamentals.setdefault(fundamental.company_id, fundamental)

    return ScanContext(
        latest_prices=latest_prices,
        latest_statements=latest_statements,
        prior_statements=prior_statements,
        latest_fundamentals=latest_fundamentals,
        dividend_totals=dividend_totals,
    )


def _clear_existing_scan_for_date(session: Session, scan_date: date) -> None:
    score_ids = select(CompanyScore.id).where(CompanyScore.as_of_date == scan_date)
    session.execute(delete(ScanResult).where(ScanResult.score_id.in_(score_ids)))
    session.execute(delete(ScanRun).where(ScanRun.as_of_date == scan_date))
    session.execute(delete(CompanyScore).where(CompanyScore.as_of_date == scan_date))
    session.execute(delete(CompanyRatio).where(CompanyRatio.as_of_date == scan_date))
    session.flush()


def calculate_ratios(session: Session, company: Company, as_of_date: date) -> CompanyRatio:
    latest_price = session.scalar(
        select(Price)
        .where(Price.company_id == company.id, Price.trade_date <= as_of_date)
        .order_by(desc(Price.trade_date))
        .limit(1)
    )
    latest_statement = session.scalar(
        select(FinancialStatement)
        .where(FinancialStatement.company_id == company.id, FinancialStatement.period_end <= as_of_date)
        .order_by(desc(FinancialStatement.period_end))
        .limit(1)
    )
    latest_fundamental = session.scalar(
        select(NgxPulseFundamental)
        .where(NgxPulseFundamental.company_id == company.id, NgxPulseFundamental.as_of_date <= as_of_date)
        .order_by(desc(NgxPulseFundamental.as_of_date), desc(NgxPulseFundamental.id))
        .limit(1)
    )
    prior_statement = None
    if latest_statement:
        prior_statement = session.scalar(
            select(FinancialStatement)
            .where(
                FinancialStatement.company_id == company.id,
                FinancialStatement.period_end < latest_statement.period_end,
                FinancialStatement.period_type == latest_statement.period_type,
            )
            .order_by(desc(FinancialStatement.period_end))
            .limit(1)
        )

    dividend_since = as_of_date - timedelta(days=365)
    dividend_total = session.scalar(
        select(func.sum(Dividend.amount_per_share)).where(
            Dividend.company_id == company.id,
            Dividend.payment_date.is_not(None),
            Dividend.payment_date >= dividend_since,
            Dividend.payment_date <= as_of_date,
        )
    )

    return _ratio_from_values(
        company=company,
        as_of_date=as_of_date,
        latest_price=latest_price,
        latest_statement=latest_statement,
        prior_statement=prior_statement,
        latest_fundamental=latest_fundamental,
        dividend_total=dividend_total,
    )


def _calculate_ratios_from_context(
    company: Company,
    as_of_date: date,
    context: ScanContext,
) -> CompanyRatio:
    return _ratio_from_values(
        company=company,
        as_of_date=as_of_date,
        latest_price=context.latest_prices.get(company.id),
        latest_statement=context.latest_statements.get(company.id),
        prior_statement=context.prior_statements.get(company.id),
        latest_fundamental=context.latest_fundamentals.get(company.id),
        dividend_total=context.dividend_totals.get(company.id),
    )


def _ratio_from_values(
    company: Company,
    as_of_date: date,
    latest_price: Price | None,
    latest_statement: FinancialStatement | None,
    prior_statement: FinancialStatement | None,
    latest_fundamental: NgxPulseFundamental | None,
    dividend_total: Decimal | None,
) -> CompanyRatio:
    price = latest_price.close_price if latest_price else None
    eps = _annualized_eps(latest_statement) or (
        latest_fundamental.eps if latest_fundamental else None
    )
    pe_ratio = _safe_div(price, eps) or (latest_fundamental.pe_ratio if latest_fundamental else None)
    roe = _safe_percent(
        latest_statement.profit_after_tax if latest_statement else None,
        latest_statement.total_equity if latest_statement else None,
    ) or (latest_fundamental.roe if latest_fundamental else None)
    net_margin = _safe_percent(
        latest_statement.profit_after_tax if latest_statement else None,
        latest_statement.revenue if latest_statement else None,
    ) or (latest_fundamental.profit_margin if latest_fundamental else None)
    debt_to_equity = _safe_div(
        latest_statement.total_liabilities if latest_statement else None,
        latest_statement.total_equity if latest_statement else None,
    ) or (latest_fundamental.debt_equity if latest_fundamental else None)
    cash_flow_to_profit = _safe_div(
        latest_statement.cash_flow_operations if latest_statement else None,
        latest_statement.profit_after_tax if latest_statement else None,
    )
    revenue_growth = _safe_growth(
        latest_statement.revenue if latest_statement else None,
        prior_statement.revenue if prior_statement else None,
    )
    profit_growth = _safe_growth(
        latest_statement.profit_after_tax if latest_statement else None,
        prior_statement.profit_after_tax if prior_statement else None,
    )
    dividend_yield = _safe_percent(dividend_total, price) or (
        latest_fundamental.dividend_yield if latest_fundamental else None
    )
    data_confidence = _data_confidence(latest_price, latest_statement, latest_fundamental)

    return CompanyRatio(
        company_id=company.id,
        as_of_date=as_of_date,
        price=price,
        eps=eps,
        pe_ratio=pe_ratio,
        roe=roe,
        net_margin=net_margin,
        debt_to_equity=debt_to_equity,
        cash_flow_to_profit=cash_flow_to_profit,
        revenue_growth=revenue_growth,
        profit_growth=profit_growth,
        dividend_yield=dividend_yield,
        data_confidence=data_confidence,
    )


def score_company(company: Company, ratio: CompanyRatio) -> CompanyScore:
    reasons: list[str] = []
    risks: list[str] = []
    profile = _sector_profile(company)
    hard_rejections = _hard_rejections(company, ratio)

    if hard_rejections:
        status = "Insufficient data" if _has_missing_data(hard_rejections) else "Avoid for now"
        risks.extend(hard_rejections)
    elif ratio.data_confidence < Decimal(70):
        status = "Needs source review"
        reasons.append("Data confidence is too low for a reliable scan.")
    elif ratio.data_confidence < Decimal(85):
        status = "Watch"
        risks.append("Some latest data is unreviewed or incomplete.")
    elif ratio.data_confidence < Decimal(50):
        status = "Insufficient data"
        reasons.append("Missing latest price or financial statement data.")
    else:
        status = "Watch"

    quality = _quality_score(company, ratio, profile)
    growth = _average(
        _score_higher_better(ratio.revenue_growth, Decimal(0), Decimal(25)),
        _score_higher_better(ratio.profit_growth, Decimal(0), Decimal(25)),
    )
    valuation = _score_pe(ratio.pe_ratio, profile)
    dividend = _score_higher_better(ratio.dividend_yield, Decimal(0), Decimal(10))
    risk = _score_risk(company, ratio)

    if ratio.roe is not None and ratio.roe >= profile.strong_roe:
        reasons.append("Strong return on equity.")
    if ratio.net_margin is not None and ratio.net_margin >= profile.strong_margin:
        reasons.append("Strong profit margin for its sector.")
    if ratio.pe_ratio is not None and ratio.pe_ratio <= profile.attractive_pe:
        reasons.append(f"Valuation appears reasonable for {profile.group} based on P/E.")
    if ratio.dividend_yield is not None and ratio.dividend_yield >= Decimal(5):
        reasons.append("Dividend yield is meaningful at the latest price.")
    if ratio.cash_flow_to_profit is not None and ratio.cash_flow_to_profit >= Decimal(1):
        reasons.append("Operating cash flow supports reported profit.")

    if (
        not _is_financial_company(company)
        and ratio.debt_to_equity is not None
        and ratio.debt_to_equity > Decimal(5)
    ):
        risks.append("High liabilities relative to equity.")
    if ratio.cash_flow_to_profit is not None and ratio.cash_flow_to_profit < Decimal("0.5"):
        risks.append("Weak cash-flow conversion.")
    if ratio.profit_growth is not None and ratio.profit_growth < Decimal(0):
        risks.append("Profit declined versus the previous comparable period.")
    if ratio.pe_ratio is not None and ratio.pe_ratio > profile.expensive_pe:
        risks.append(f"Stock may be expensive relative to {profile.group} earnings.")
    if ratio.data_confidence < Decimal(85):
        risks.append(f"Data confidence is {ratio.data_confidence}; review source records before relying on score.")

    overall = (
        quality * profile.quality_weight
        + valuation * profile.valuation_weight
        + growth * profile.growth_weight
        + dividend * profile.dividend_weight
        + risk * profile.risk_weight
    )

    if status not in {"Insufficient data", "Needs source review", "Avoid for now"}:
        status = _status(overall, quality, valuation, risk, risks)

    return CompanyScore(
        company_id=company.id,
        as_of_date=ratio.as_of_date,
        quality_score=quality,
        growth_score=growth,
        valuation_score=valuation,
        dividend_score=dividend,
        risk_score=risk,
        overall_score=overall.quantize(Decimal("0.01")),
        status=status,
        reasons="\n".join(reasons) or "No strong positive signal yet.",
        risks="\n".join(risks) or "No major rule-based risk flag.",
    )


def _status(
    overall: Decimal,
    quality: Decimal,
    valuation: Decimal,
    risk: Decimal,
    risks: list[str],
) -> str:
    if risk < Decimal(35):
        return "Possible value trap"
    if overall >= Decimal(75) and quality >= Decimal(65) and valuation >= Decimal(55):
        return "Attractive - further research"
    if quality >= Decimal(70) and valuation < Decimal(45):
        return "Good company, expensive"
    if risks:
        return "Watch with risks"
    if overall >= Decimal(55):
        return "Watch for better price"
    return "Avoid for now"


def _safe_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal(0)):
        return None
    return (numerator / denominator).quantize(Decimal("0.0001"))


def _safe_percent(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    value = _safe_div(numerator, denominator)
    if value is None:
        return None
    return (value * HUNDRED).quantize(Decimal("0.0001"))


def _safe_growth(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous in (None, Decimal(0)):
        return None
    return (((current - previous) / abs(previous)) * HUNDRED).quantize(Decimal("0.0001"))


def _data_confidence(
    price: Price | None,
    statement: FinancialStatement | None,
    fundamental: NgxPulseFundamental | None,
) -> Decimal:
    score = Decimal(0)
    if price:
        score += Decimal(35) if price.reviewed else Decimal(20)
    if statement:
        score += Decimal(45) if statement.reviewed else Decimal(25)
        fields = [
            statement.revenue,
            statement.profit_after_tax,
            statement.total_equity,
            statement.cash_flow_operations,
            statement.eps,
        ]
        score += Decimal(sum(1 for field in fields if field is not None) * 4)
    elif fundamental:
        score += Decimal(45)
        fields = [
            fundamental.eps,
            fundamental.pe_ratio,
            fundamental.roe,
            fundamental.profit_margin,
            fundamental.debt_equity,
            fundamental.dividend_yield,
        ]
        score += Decimal(sum(1 for field in fields if field is not None) * 4)
    return min(score, HUNDRED)


def _annualized_eps(statement: FinancialStatement | None) -> Decimal | None:
    if statement is None or statement.eps is None:
        return None
    multipliers = {
        "Q1": Decimal(4),
        "Q2": Decimal(2),
        "H1": Decimal(2),
        "Q3": Decimal("1.3333"),
        "9M": Decimal("1.3333"),
        "Q4": Decimal(1),
        "FY": Decimal(1),
    }
    multiplier = multipliers.get(statement.period_type.upper(), Decimal(1))
    return (statement.eps * multiplier).quantize(Decimal("0.0001"))


def _score_higher_better(value: Decimal | None, low: Decimal, high: Decimal) -> Decimal:
    if value is None:
        return Decimal(0)
    if value <= low:
        return Decimal(0)
    if value >= high:
        return HUNDRED
    return (((value - low) / (high - low)) * HUNDRED).quantize(Decimal("0.01"))


def _quality_score(company: Company, ratio: CompanyRatio, profile: SectorProfile) -> Decimal:
    if _is_financial_company(company):
        return _average(
            _score_higher_better(ratio.roe, Decimal(5), Decimal(30)),
            _score_higher_better(ratio.net_margin, Decimal(5), Decimal(45)),
        )
    if profile.group == "Oil and Gas":
        return _average(
            _score_higher_better(ratio.roe, Decimal(5), Decimal(25)),
            _score_higher_better(ratio.net_margin, Decimal(5), Decimal(35)),
            _score_higher_better(ratio.cash_flow_to_profit, Decimal("0.6"), Decimal("1.5")),
        )
    return _average(
        _score_higher_better(ratio.roe, Decimal(5), Decimal(25)),
        _score_higher_better(ratio.net_margin, Decimal(5), Decimal(30)),
        _score_higher_better(ratio.cash_flow_to_profit, Decimal("0.4"), Decimal("1.2")),
    )


def _score_pe(pe_ratio: Decimal | None, profile: SectorProfile) -> Decimal:
    if pe_ratio is None or pe_ratio <= Decimal(0):
        return Decimal(0)
    if pe_ratio <= profile.attractive_pe * Decimal("0.6"):
        return Decimal(90)
    if pe_ratio <= profile.attractive_pe:
        return Decimal(80)
    if pe_ratio <= (profile.attractive_pe + profile.expensive_pe) / Decimal(2):
        return Decimal(60)
    if pe_ratio <= profile.expensive_pe:
        return Decimal(40)
    return Decimal(15)


def _score_risk(company: Company, ratio: CompanyRatio) -> Decimal:
    score = HUNDRED
    if (
        not _is_financial_company(company)
        and ratio.debt_to_equity is not None
        and ratio.debt_to_equity > Decimal(5)
    ):
        score -= Decimal(35)
    if ratio.cash_flow_to_profit is not None and ratio.cash_flow_to_profit < Decimal("0.5"):
        score -= Decimal(25)
    if ratio.profit_growth is not None and ratio.profit_growth < Decimal(0):
        score -= Decimal(20)
    if ratio.data_confidence < Decimal(70):
        score -= Decimal(30)
    elif ratio.data_confidence < Decimal(85):
        score -= Decimal(15)
    return max(Decimal(0), score)


def _is_financial_company(company: Company) -> bool:
    return bool(company.sector and "financial" in company.sector.lower())


def _sector_profile(company: Company) -> SectorProfile:
    sector = (company.sector or "").lower()
    if "financial" in sector:
        return SectorProfile(
            group="Financial Services",
            quality_weight=Decimal("0.35"),
            valuation_weight=Decimal("0.25"),
            growth_weight=Decimal("0.15"),
            dividend_weight=Decimal("0.15"),
            risk_weight=Decimal("0.10"),
            attractive_pe=Decimal(8),
            expensive_pe=Decimal(15),
            strong_roe=Decimal(20),
            strong_margin=Decimal(25),
        )
    if "ict" in sector or "telecom" in sector:
        return SectorProfile(
            group="ICT/Telecom",
            quality_weight=Decimal("0.30"),
            valuation_weight=Decimal("0.20"),
            growth_weight=Decimal("0.25"),
            dividend_weight=Decimal("0.15"),
            risk_weight=Decimal("0.10"),
            attractive_pe=Decimal(12),
            expensive_pe=Decimal(25),
            strong_roe=Decimal(20),
            strong_margin=Decimal(20),
        )
    if "oil" in sector or "gas" in sector:
        return SectorProfile(
            group="Oil and Gas",
            quality_weight=Decimal("0.25"),
            valuation_weight=Decimal("0.20"),
            growth_weight=Decimal("0.20"),
            dividend_weight=Decimal("0.20"),
            risk_weight=Decimal("0.15"),
            attractive_pe=Decimal(8),
            expensive_pe=Decimal(18),
            strong_roe=Decimal(18),
            strong_margin=Decimal(25),
        )
    if "agriculture" in sector:
        return SectorProfile(
            group="Agriculture",
            quality_weight=Decimal("0.30"),
            valuation_weight=Decimal("0.20"),
            growth_weight=Decimal("0.25"),
            dividend_weight=Decimal("0.15"),
            risk_weight=Decimal("0.10"),
            attractive_pe=Decimal(10),
            expensive_pe=Decimal(22),
            strong_roe=Decimal(18),
            strong_margin=Decimal(22),
        )
    if "consumer" in sector:
        return SectorProfile(
            group="Consumer Goods",
            quality_weight=Decimal("0.35"),
            valuation_weight=Decimal("0.20"),
            growth_weight=Decimal("0.20"),
            dividend_weight=Decimal("0.15"),
            risk_weight=Decimal("0.10"),
            attractive_pe=Decimal(12),
            expensive_pe=Decimal(25),
            strong_roe=Decimal(20),
            strong_margin=Decimal(15),
        )
    return SectorProfile(
        group="General",
        quality_weight=Decimal("0.30"),
        valuation_weight=Decimal("0.25"),
        growth_weight=Decimal("0.20"),
        dividend_weight=Decimal("0.15"),
        risk_weight=Decimal("0.10"),
        attractive_pe=Decimal(10),
        expensive_pe=Decimal(20),
        strong_roe=Decimal(20),
        strong_margin=Decimal(20),
    )


def _hard_rejections(company: Company, ratio: CompanyRatio) -> list[str]:
    rejections: list[str] = []
    if ratio.price is None:
        rejections.append("Missing latest price.")
    if ratio.eps is None:
        rejections.append("Missing EPS from latest financial data.")
    if ratio.data_confidence < Decimal(50):
        rejections.append("Data confidence is too low for scoring.")
    if ratio.eps is not None and ratio.eps <= Decimal(0):
        rejections.append("EPS is zero or negative.")
    if ratio.roe is not None and ratio.roe < Decimal(0):
        rejections.append("Return on equity is negative.")
    if ratio.net_margin is not None and ratio.net_margin < Decimal(0):
        rejections.append("Net margin is negative.")
    if (
        not _is_financial_company(company)
        and ratio.debt_to_equity is not None
        and ratio.debt_to_equity > Decimal(8)
    ):
        rejections.append("Debt-to-equity is severely high for a non-financial company.")
    return rejections


def _has_missing_data(rejections: list[str]) -> bool:
    return any("Missing" in rejection or "confidence" in rejection for rejection in rejections)


def _average(*values: Decimal) -> Decimal:
    if not values:
        return Decimal(0)
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.01"))
