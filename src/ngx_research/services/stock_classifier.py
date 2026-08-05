from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ClassificationContext:
    sector: str | None
    latest_price: Decimal | None
    market_cap: Decimal | None
    pe_ratio: Decimal | None
    sector_pe_median: Decimal | None
    roe: Decimal | None
    sector_roe_median: Decimal | None
    profit_margin: Decimal | None
    dividend_yield: Decimal | None
    dividend_years: int
    revenue_growth: Decimal | None
    profit_growth: Decimal | None
    eps_growth: Decimal | None
    debt_to_equity: Decimal | None
    cash_flow_to_profit: Decimal | None
    price_drawdown_percent: Decimal | None
    liquidity_score: Decimal
    data_confidence: Decimal


@dataclass(frozen=True)
class StockClassification:
    stock_types: list[str]
    reasons: list[str]
    risks: list[str]


def classify_stock(context: ClassificationContext) -> StockClassification:
    types: list[str] = []
    reasons: list[str] = []
    risks: list[str] = []
    sector = context.sector or "Unknown sector"

    _add(types, f"Sector specific stock: {sector}")

    if _is_growth(context):
        _add(types, "Growth stock")
        reasons.append("Growth metrics are positive relative to available history.")

    if _is_value(context):
        _add(types, "Value stock")
        reasons.append("Valuation is attractive relative to earnings and sector context.")

    if _is_dividend(context):
        _add(types, "Dividend stock")
        reasons.append("Dividend yield and dividend record support income-focused review.")
    elif _has_dividend_yield_evidence(context):
        _add(types, "Dividend yield watch")
        reasons.append(
            "Dividend yield is visible in fundamentals, but multi-year payment history still needs confirmation."
        )
    elif context.dividend_years >= 2:
        _add(types, "Dividend history stock")
        reasons.append("Company has a multi-year dividend record, even if yield is not high today.")

    if _is_blue_chip_candidate(context):
        _add(types, "Blue chip candidate")
        reasons.append("Large price level, good liquidity, and quality metrics support blue-chip review.")

    if context.latest_price is not None and context.latest_price < Decimal(5):
        _add(types, "Penny/speculative stock")
        risks.append("Low absolute share price increases speculative and liquidity-risk sensitivity.")

    if _is_turnaround(context):
        _add(types, "Turnaround candidate")
        reasons.append("Recent profitability/growth signal is improving from a weak base.")

    if _is_quality_compounder(context):
        _add(types, "Quality compounder")
        reasons.append("ROE, margin, leverage, and dividend record suggest durable quality.")

    if _is_cyclical(context):
        _add(types, "Cyclical/commodity stock")
        risks.append("Sector economics may depend heavily on commodity cycles or macro conditions.")

    if _is_weak(context):
        _add(types, "Weak/avoid candidate")
        risks.append("Available metrics show weak profitability, risk, or data quality.")

    return StockClassification(stock_types=types, reasons=reasons, risks=risks)


def _is_growth(context: ClassificationContext) -> bool:
    growth_values = [context.revenue_growth, context.profit_growth, context.eps_growth]
    positive = [value for value in growth_values if value is not None and value >= Decimal(12)]
    return len(positive) >= 1 and (context.roe is None or context.roe >= Decimal(10))


def _is_value(context: ClassificationContext) -> bool:
    if context.pe_ratio is None or context.pe_ratio <= 0:
        return False
    if context.sector_pe_median and context.pe_ratio <= context.sector_pe_median * Decimal("0.8"):
        return True
    return context.pe_ratio <= Decimal(8)


def _is_dividend(context: ClassificationContext) -> bool:
    return (
        context.dividend_yield is not None
        and context.dividend_yield >= Decimal(4)
        and context.dividend_years >= 2
    )


def _has_dividend_yield_evidence(context: ClassificationContext) -> bool:
    return context.dividend_yield is not None and context.dividend_yield >= Decimal(4)


def _is_blue_chip_candidate(context: ClassificationContext) -> bool:
    return (
        context.latest_price is not None
        and context.latest_price >= Decimal(50)
        and context.liquidity_score >= Decimal(65)
        and context.roe is not None
        and context.roe >= Decimal(15)
        and context.data_confidence >= Decimal(70)
    )


def _is_turnaround(context: ClassificationContext) -> bool:
    return bool(
        context.profit_growth is not None
        and context.profit_growth >= Decimal(20)
        and context.price_drawdown_percent is not None
        and context.price_drawdown_percent >= Decimal(25)
    )


def _is_quality_compounder(context: ClassificationContext) -> bool:
    leverage_ok = context.debt_to_equity is None or context.debt_to_equity <= Decimal(2)
    return bool(
        context.roe is not None
        and context.roe >= Decimal(20)
        and context.profit_margin is not None
        and context.profit_margin >= Decimal(18)
        and leverage_ok
        and context.dividend_years >= 2
    )


def _is_cyclical(context: ClassificationContext) -> bool:
    sector = (context.sector or "").lower()
    return any(keyword in sector for keyword in ("oil", "gas", "agriculture", "industrial"))


def _is_weak(context: ClassificationContext) -> bool:
    if context.data_confidence < Decimal(45):
        return True
    if context.roe is not None and context.roe < Decimal(0):
        return True
    if context.profit_margin is not None and context.profit_margin < Decimal(0):
        return True
    return bool(context.pe_ratio is not None and context.pe_ratio <= Decimal(0))


def _add(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)
