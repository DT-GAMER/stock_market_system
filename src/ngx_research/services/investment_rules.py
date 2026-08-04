from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyRatio,
    CompanyScore,
    Dividend,
    FinancialStatement,
    InvestmentNote,
    Price,
)
from ngx_research.schemas import (
    InvestmentChecklistItemRead,
    InvestmentRuleRead,
    NgxMarketRuleRead,
)

DAILY_BAND = Decimal(10)


def ngx_market_rules(
    session: Session,
    company: Company,
) -> NgxMarketRuleRead:
    latest, previous = _latest_two_prices(session, company.id)
    warnings: list[str] = []
    latest_close = latest.close_price if latest else None
    previous_close = previous.close_price if previous else None
    change_percent = _safe_percent(
        latest.close_price - previous.close_price if latest and previous else None,
        previous.close_price if previous else None,
    )
    upper_limit = (previous.close_price * Decimal("1.10")).quantize(Decimal("0.0001")) if previous else None
    lower_limit = (previous.close_price * Decimal("0.90")).quantize(Decimal("0.0001")) if previous else None
    band_status = _price_band_status(change_percent)
    group, threshold, tick_size = _price_movement_group(latest_close)

    threshold_met = None
    if latest and threshold is not None:
        threshold_met = latest.volume >= threshold if latest.volume is not None else False
        if not threshold_met:
            warnings.append("Latest volume may be too low to move the official published price.")

    if band_status in {"Limit Up", "Near Limit Up"}:
        warnings.append("Avoid chasing after a sharp same-day rally; wait for price discovery.")
    if band_status in {"Limit Down", "Near Limit Down"}:
        warnings.append("Review news and liquidity before buying into a sharp same-day drop.")
    if not latest or not previous:
        warnings.append("At least two price records are required to evaluate the NGX daily band.")

    return NgxMarketRuleRead(
        symbol=company.symbol,
        name=company.name,
        trade_date=latest.trade_date if latest else None,
        previous_close=previous_close,
        latest_close=latest_close,
        daily_change_percent=change_percent,
        upper_price_limit=upper_limit,
        lower_price_limit=lower_limit,
        price_band_status=band_status,
        price_movement_group=group,
        minimum_volume_to_move_price=threshold,
        latest_volume=latest.volume if latest else None,
        volume_threshold_met=threshold_met,
        tick_size=tick_size,
        warnings=warnings,
    )


def investment_rules(
    session: Session,
    company: Company,
) -> InvestmentRuleRead:
    ratio = _latest_ratio(session, company.id)
    score = _latest_score(session, company.id)
    statement = _latest_statement(session, company.id)
    note = _latest_note(session, company.id)
    market_rules = ngx_market_rules(session, company)
    stock_types = _stock_types(session, company, ratio, score, market_rules.latest_close)
    checklist = _checklist(company, ratio, score, statement, note)
    data_warnings = _data_warnings(ratio, score, market_rules)
    return InvestmentRuleRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        stock_types=stock_types,
        fundamental_style=_fundamental_style(ratio),
        technical_signal=_technical_signal(market_rules),
        ngx_market_rules=market_rules,
        checklist=checklist,
        decision_guardrails=_decision_guardrails(session, company, checklist, market_rules),
        data_warnings=data_warnings,
    )


def list_investment_rules(session: Session, limit: int = 100) -> list[InvestmentRuleRead]:
    companies = session.scalars(select(Company).where(Company.is_active.is_(True)).order_by(Company.symbol))
    return [investment_rules(session, company) for company in list(companies)[:limit]]


def _latest_two_prices(session: Session, company_id: int) -> tuple[Price | None, Price | None]:
    prices = list(
        session.scalars(
            select(Price)
            .where(Price.company_id == company_id)
            .order_by(desc(Price.trade_date), desc(Price.id))
            .limit(2)
        )
    )
    latest = prices[0] if prices else None
    previous = prices[1] if len(prices) > 1 else None
    return latest, previous


def _latest_ratio(session: Session, company_id: int) -> CompanyRatio | None:
    return session.scalar(
        select(CompanyRatio)
        .where(CompanyRatio.company_id == company_id)
        .order_by(desc(CompanyRatio.as_of_date), desc(CompanyRatio.id))
        .limit(1)
    )


def _latest_score(session: Session, company_id: int) -> CompanyScore | None:
    return session.scalar(
        select(CompanyScore)
        .where(CompanyScore.company_id == company_id)
        .order_by(desc(CompanyScore.as_of_date), desc(CompanyScore.id))
        .limit(1)
    )


def _latest_statement(session: Session, company_id: int) -> FinancialStatement | None:
    return session.scalar(
        select(FinancialStatement)
        .where(FinancialStatement.company_id == company_id)
        .order_by(desc(FinancialStatement.period_end), desc(FinancialStatement.id))
        .limit(1)
    )


def _latest_note(session: Session, company_id: int) -> InvestmentNote | None:
    return session.scalar(
        select(InvestmentNote)
        .where(InvestmentNote.company_id == company_id)
        .order_by(desc(InvestmentNote.note_date), desc(InvestmentNote.id))
        .limit(1)
    )


def _safe_percent(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal(0)):
        return None
    return ((numerator / denominator) * Decimal(100)).quantize(Decimal("0.0001"))


def _price_band_status(change_percent: Decimal | None) -> str:
    if change_percent is None:
        return "Insufficient price history"
    if change_percent >= Decimal("9.95"):
        return "Limit Up"
    if change_percent <= Decimal("-9.95"):
        return "Limit Down"
    if change_percent >= Decimal(8):
        return "Near Limit Up"
    if change_percent <= Decimal(-8):
        return "Near Limit Down"
    return "Normal"


def _price_movement_group(price: Decimal | None) -> tuple[str | None, int | None, Decimal | None]:
    if price is None:
        return None, None, None
    if price >= Decimal(1000):
        return "Group A", 10_000, Decimal("0.10")
    if price >= Decimal(500):
        return "Group B", 50_000, Decimal("0.05")
    return "Group C", 100_000, Decimal("0.01")


def _stock_types(
    session: Session,
    company: Company,
    ratio: CompanyRatio | None,
    score: CompanyScore | None,
    price: Decimal | None,
) -> list[str]:
    types: list[str] = []
    if ratio and (
        (ratio.revenue_growth is not None and ratio.revenue_growth >= Decimal(15))
        or (ratio.profit_growth is not None and ratio.profit_growth >= Decimal(15))
    ):
        types.append("Growth stock")
    if ratio and ratio.pe_ratio is not None and ratio.pe_ratio <= Decimal(8):
        types.append("Value stock")
    if ratio and ratio.dividend_yield is not None and ratio.dividend_yield >= Decimal(4):
        types.append("Dividend stock")
    if score and score.quality_score >= Decimal(70) and price and price >= Decimal(50):
        types.append("Blue chip candidate")
    if price and price < Decimal(5):
        types.append("Penny stock")
    if company.sector:
        types.append(f"Sector specific stock: {company.sector}")
    if _dividend_events(session, company.id) >= 2 and "Dividend stock" not in types:
        types.append("Dividend history stock")
    return types or ["Unclassified"]


def _dividend_events(session: Session, company_id: int) -> int:
    return session.scalar(select(func.count(Dividend.id)).where(Dividend.company_id == company_id)) or 0


def _fundamental_style(ratio: CompanyRatio | None) -> str:
    if not ratio:
        return "Insufficient fundamental data"
    strengths = []
    if ratio.roe is not None and ratio.roe >= Decimal(20):
        strengths.append("high ROE")
    if ratio.net_margin is not None and ratio.net_margin >= Decimal(20):
        strengths.append("strong margin")
    if ratio.pe_ratio is not None and ratio.pe_ratio <= Decimal(8):
        strengths.append("low P/E")
    if ratio.dividend_yield is not None and ratio.dividend_yield >= Decimal(4):
        strengths.append("dividend yield")
    return "Quantitative fundamental strength: " + ", ".join(strengths) if strengths else "No strong quantitative edge yet"


def _technical_signal(market_rules: NgxMarketRuleRead) -> str:
    if market_rules.price_band_status == "Limit Up":
        return "Stretched intraday move - do not chase blindly"
    if market_rules.price_band_status == "Limit Down":
        return "Sharp selloff - investigate cause before acting"
    if market_rules.daily_change_percent is None:
        return "Insufficient price history"
    if market_rules.daily_change_percent > 0:
        return "Positive daily momentum"
    if market_rules.daily_change_percent < 0:
        return "Negative daily momentum"
    return "Flat daily price action"


def _checklist(
    company: Company,
    ratio: CompanyRatio | None,
    score: CompanyScore | None,
    statement: FinancialStatement | None,
    note: InvestmentNote | None,
) -> list[InvestmentChecklistItemRead]:
    return [
        InvestmentChecklistItemRead(
            question="Is the company making money and growing?",
            passed=bool(
                ratio
                and (
                    (
                        statement
                        and statement.profit_after_tax
                        and statement.profit_after_tax > 0
                    )
                    or (ratio.eps is not None and ratio.eps > Decimal(0))
                    or (
                        ratio.roe is not None
                        and ratio.roe > Decimal(0)
                        and ratio.net_margin is not None
                        and ratio.net_margin > Decimal(0)
                    )
                )
            ),
            detail="Uses latest PAT where available, otherwise EPS, ROE and margin from trusted fundamentals.",
        ),
        InvestmentChecklistItemRead(
            question="Do I understand the business?",
            passed=bool(note and note.thesis),
            detail="Write a research thesis explaining how the company makes money.",
        ),
        InvestmentChecklistItemRead(
            question="Does it have something unique?",
            passed=bool(score and score.quality_score >= Decimal(70)),
            detail="Uses quality score as a proxy; confirm qualitative moat in your thesis.",
        ),
        InvestmentChecklistItemRead(
            question="Is the price fair compared to earnings?",
            passed=bool(ratio and ratio.pe_ratio is not None and ratio.pe_ratio <= Decimal(12)),
            detail="Uses P/E as the first valuation check.",
        ),
        InvestmentChecklistItemRead(
            question="Can I hold it for 5+ years?",
            passed=bool(note and note.risks),
            detail="A 5+ year hold needs explicit risks and a durable thesis in the journal.",
        ),
    ]


def _decision_guardrails(
    session: Session,
    company: Company,
    checklist: list[InvestmentChecklistItemRead],
    market_rules: NgxMarketRuleRead,
) -> list[str]:
    guardrails: list[str] = []
    failed_questions = {item.question for item in checklist if not item.passed}
    if "Is the company making money and growing?" in failed_questions:
        guardrails.append("Research required: earnings or growth evidence is not strong enough yet.")
    if "Is the price fair compared to earnings?" in failed_questions:
        guardrails.append("Valuation check needed: P/E support is missing or not attractive yet.")
    if "Do I understand the business?" in failed_questions:
        guardrails.append("Personal thesis needed: explain how this business makes money before buying.")
    if "Can I hold it for 5+ years?" in failed_questions:
        guardrails.append("Personal plan needed: write your holding period, risks, and exit rule.")
    if market_rules.price_band_status in {"Limit Up", "Near Limit Up"}:
        guardrails.append("Do not chase: stock is at or near the NGX +10% daily band.")
    if market_rules.price_band_status in {"Limit Down", "Near Limit Down"}:
        guardrails.append("Investigate first: stock is at or near the NGX -10% daily band.")
    if market_rules.volume_threshold_met is False:
        guardrails.append("Treat price move carefully: latest volume may not meet NGX price-movement threshold.")
    return guardrails or ["No hard rule-based block, but still review source data and your thesis."]


def _data_warnings(
    ratio: CompanyRatio | None,
    score: CompanyScore | None,
    market_rules: NgxMarketRuleRead,
) -> list[str]:
    warnings = list(market_rules.warnings)
    if not ratio:
        warnings.append("Run a market scan to create latest ratios.")
    elif ratio.data_confidence < Decimal(85):
        warnings.append(f"Ratio data confidence is {ratio.data_confidence}; approve or verify source records.")
    if score and score.status in {"Insufficient data", "Needs source review"}:
        warnings.append(f"Scanner status is {score.status}.")
    return warnings
