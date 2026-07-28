from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ngx_research.models import Company, PortfolioTransaction, Price
from ngx_research.schemas import (
    PortfolioPositionRead,
    PortfolioSummaryRead,
    PortfolioTransactionRead,
    SectorAllocationRead,
)

BUY = "BUY"
SELL = "SELL"
DIVIDEND = "DIVIDEND"


@dataclass
class PositionState:
    quantity: Decimal = Decimal(0)
    cost_basis: Decimal = Decimal(0)
    dividends_received: Decimal = Decimal(0)


def create_transaction(
    session: Session,
    company: Company,
    transaction_date: date,
    transaction_type: str,
    quantity: Decimal,
    price_per_share: Decimal | None,
    fees: Decimal,
    cash_amount: Decimal | None,
    notes: str | None,
) -> PortfolioTransaction:
    tx_type = transaction_type.upper()
    _validate_transaction(tx_type, quantity, price_per_share, fees, cash_amount)
    transaction = PortfolioTransaction(
        company_id=company.id,
        transaction_date=transaction_date,
        transaction_type=tx_type,
        quantity=quantity,
        price_per_share=price_per_share,
        fees=fees,
        cash_amount=cash_amount,
        notes=notes,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def list_transactions(session: Session, limit: int = 100) -> list[PortfolioTransactionRead]:
    rows = session.execute(
        select(PortfolioTransaction, Company.symbol)
        .join(Company, Company.id == PortfolioTransaction.company_id)
        .order_by(desc(PortfolioTransaction.transaction_date), desc(PortfolioTransaction.id))
        .limit(limit)
    )
    return [
        PortfolioTransactionRead(
            id=transaction.id,
            symbol=symbol,
            transaction_date=transaction.transaction_date,
            transaction_type=transaction.transaction_type,
            quantity=transaction.quantity,
            price_per_share=transaction.price_per_share,
            fees=transaction.fees,
            cash_amount=transaction.cash_amount,
            notes=transaction.notes,
        )
        for transaction, symbol in rows
    ]


def portfolio_summary(session: Session) -> PortfolioSummaryRead:
    companies = {company.id: company for company in session.scalars(select(Company))}
    states: dict[int, PositionState] = defaultdict(PositionState)
    transactions = session.scalars(
        select(PortfolioTransaction).order_by(
            PortfolioTransaction.transaction_date,
            PortfolioTransaction.id,
        )
    )
    for transaction in transactions:
        _apply_transaction(states[transaction.company_id], transaction)

    raw_positions = []
    total_cost_basis = Decimal(0)
    total_market_value = Decimal(0)
    total_dividends = Decimal(0)
    for company_id, state in states.items():
        if state.quantity <= 0 and state.dividends_received <= 0:
            continue
        company = companies.get(company_id)
        if not company:
            continue
        latest_price = _latest_price(session, company_id)
        market_value = (
            (state.quantity * latest_price.close_price).quantize(Decimal("0.0001"))
            if latest_price and state.quantity > 0
            else None
        )
        total_cost_basis += state.cost_basis
        if market_value is not None:
            total_market_value += market_value
        total_dividends += state.dividends_received
        raw_positions.append((company, state, latest_price, market_value))

    positions: list[PortfolioPositionRead] = []
    sector_values: dict[str, Decimal] = defaultdict(Decimal)
    for company, state, latest_price, market_value in raw_positions:
        average_cost = _safe_div(state.cost_basis, state.quantity)
        unrealized = market_value - state.cost_basis if market_value is not None else None
        unrealized_pct = _safe_percent(unrealized, state.cost_basis)
        weight = _safe_percent(market_value, total_market_value) if market_value is not None else None
        sector = company.sector or "Unknown"
        if market_value is not None:
            sector_values[sector] += market_value
        positions.append(
            PortfolioPositionRead(
                symbol=company.symbol,
                name=company.name,
                sector=company.sector,
                quantity=state.quantity,
                average_cost=average_cost,
                cost_basis=state.cost_basis,
                latest_price=latest_price.close_price if latest_price else None,
                market_value=market_value,
                unrealized_gain_loss=unrealized,
                unrealized_gain_loss_percent=unrealized_pct,
                portfolio_weight=weight,
                dividends_received=state.dividends_received,
            )
        )

    allocation = [
        SectorAllocationRead(
            sector=sector,
            market_value=value,
            portfolio_weight=_safe_percent(value, total_market_value) or Decimal(0),
        )
        for sector, value in sorted(sector_values.items(), key=lambda item: item[1], reverse=True)
    ]
    total_unrealized = total_market_value - total_cost_basis
    return PortfolioSummaryRead(
        total_cost_basis=total_cost_basis,
        total_market_value=total_market_value,
        total_unrealized_gain_loss=total_unrealized,
        total_unrealized_gain_loss_percent=_safe_percent(total_unrealized, total_cost_basis),
        total_dividends_received=total_dividends,
        positions=sorted(positions, key=lambda item: item.market_value or Decimal(0), reverse=True),
        sector_allocation=allocation,
        warnings=_warnings(positions, allocation),
    )


def _validate_transaction(
    tx_type: str,
    quantity: Decimal,
    price_per_share: Decimal | None,
    fees: Decimal,
    cash_amount: Decimal | None,
) -> None:
    if tx_type not in {BUY, SELL, DIVIDEND}:
        raise ValueError("transaction_type must be BUY, SELL, or DIVIDEND")
    if fees < 0:
        raise ValueError("fees cannot be negative")
    if tx_type in {BUY, SELL}:
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero for BUY/SELL")
        if price_per_share is None or price_per_share <= 0:
            raise ValueError("price_per_share must be greater than zero for BUY/SELL")
        if cash_amount is not None and cash_amount < 0:
            raise ValueError("cash_amount cannot be negative")
    if tx_type == DIVIDEND and (cash_amount is None or cash_amount <= 0):
        raise ValueError("cash_amount must be greater than zero for DIVIDEND")


def _apply_transaction(state: PositionState, transaction: PortfolioTransaction) -> None:
    if transaction.transaction_type == BUY:
        state.quantity += transaction.quantity
        state.cost_basis += transaction.quantity * (transaction.price_per_share or Decimal(0)) + transaction.fees
        return
    if transaction.transaction_type == SELL:
        if state.quantity <= 0:
            return
        sold_quantity = min(transaction.quantity, state.quantity)
        average_cost = _safe_div(state.cost_basis, state.quantity) or Decimal(0)
        state.quantity -= sold_quantity
        state.cost_basis -= sold_quantity * average_cost
        state.cost_basis = max(state.cost_basis, Decimal(0))
        return
    if transaction.transaction_type == DIVIDEND:
        state.dividends_received += transaction.cash_amount or Decimal(0)


def _latest_price(session: Session, company_id: int) -> Price | None:
    return session.scalar(
        select(Price).where(Price.company_id == company_id).order_by(desc(Price.trade_date)).limit(1)
    )


def _safe_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal(0)):
        return None
    return (numerator / denominator).quantize(Decimal("0.0001"))


def _safe_percent(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    value = _safe_div(numerator, denominator)
    if value is None:
        return None
    return (value * Decimal(100)).quantize(Decimal("0.0001"))


def _warnings(
    positions: list[PortfolioPositionRead],
    allocation: list[SectorAllocationRead],
) -> list[str]:
    warnings: list[str] = []
    for position in positions:
        if position.portfolio_weight is not None and position.portfolio_weight > Decimal(30):
            warnings.append(f"{position.symbol} is above 30% of portfolio value.")
        if position.latest_price is None and position.quantity > 0:
            warnings.append(f"{position.symbol} has no latest price; market value is incomplete.")
    for sector in allocation:
        if sector.portfolio_weight > Decimal(50):
            warnings.append(f"{sector.sector} exposure is above 50% of portfolio value.")
    return warnings
