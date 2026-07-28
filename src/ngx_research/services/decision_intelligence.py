from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyRatio,
    CompanyScore,
    InvestmentGoal,
    Price,
    Watchlist,
    WatchlistItem,
)
from ngx_research.schemas import (
    ExitSignalRead,
    InvestmentGoalRead,
    PortfolioExitIntelligenceRead,
    PriceRangeRead,
    WatchlistEntrySignalRead,
    WatchlistIntelligenceRead,
)
from ngx_research.services.investment_rules import investment_rules
from ngx_research.services.portfolio import portfolio_summary

WATCHLIST_FOCUS_LIMIT = 10


def create_investment_goal(
    session: Session,
    company: Company,
    goal_type: str,
    reason: str,
    target_price: Decimal | None = None,
    target_return_percent: Decimal | None = None,
    target_dividend_yield: Decimal | None = None,
    target_date: date | None = None,
    review_date: date | None = None,
    sell_rule: str | None = None,
) -> InvestmentGoalRead:
    normalized_type = goal_type.strip().lower().replace(" ", "_")
    if normalized_type not in {"capital_gain", "dividend_income", "balanced", "learning"}:
        raise ValueError("goal_type must be capital_gain, dividend_income, balanced, or learning")
    if not reason.strip():
        raise ValueError("reason is required")

    goal = InvestmentGoal(
        company_id=company.id,
        goal_type=normalized_type,
        target_price=target_price,
        target_return_percent=target_return_percent,
        target_dividend_yield=target_dividend_yield,
        target_date=target_date,
        review_date=review_date,
        reason=reason.strip(),
        sell_rule=sell_rule.strip() if sell_rule else None,
        status="active",
    )
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return _goal_read(goal, company)


def list_investment_goals(
    session: Session,
    status: str | None = "active",
    limit: int = 100,
) -> list[InvestmentGoalRead]:
    statement = (
        select(InvestmentGoal, Company)
        .join(Company, Company.id == InvestmentGoal.company_id)
        .order_by(desc(InvestmentGoal.created_at))
        .limit(limit)
    )
    if status:
        statement = statement.where(InvestmentGoal.status == status.lower())
    return [_goal_read(goal, company) for goal, company in session.execute(statement)]


def watchlist_intelligence(session: Session, watchlist: Watchlist) -> WatchlistIntelligenceRead:
    items = list(
        session.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist.id)
            .order_by(WatchlistItem.created_at, WatchlistItem.id)
        )
    )
    signals: list[WatchlistEntrySignalRead] = []
    for item in items:
        company = session.get(Company, item.company_id)
        if not company:
            continue
        signals.append(_entry_signal(session, watchlist, item, company))

    return WatchlistIntelligenceRead(
        watchlist_id=watchlist.id,
        watchlist_name=watchlist.name,
        member_count=len(signals),
        focus_warning=_focus_warning(len(signals)),
        signals=sorted(signals, key=_entry_sort_key),
    )


def portfolio_exit_intelligence(session: Session) -> PortfolioExitIntelligenceRead:
    summary = portfolio_summary(session)
    goals = _active_goals_by_symbol(session)
    latest_scores = _latest_scores(session)
    latest_ratios = _latest_ratios(session)
    better_opportunities = _better_opportunities(session)
    signals: list[ExitSignalRead] = []

    for position in summary.positions:
        if position.quantity <= 0:
            continue
        company = session.scalar(select(Company).where(Company.symbol == position.symbol))
        if not company:
            continue
        goal = goals.get(company.symbol)
        score = latest_scores.get(company.id)
        ratio = latest_ratios.get(company.id)
        action, confidence, reasons, risks, next_action = _exit_decision(
            position=position,
            goal=goal,
            score=score,
            ratio=ratio,
            better_opportunities=better_opportunities,
        )
        signals.append(
            ExitSignalRead(
                symbol=company.symbol,
                name=company.name,
                sector=company.sector,
                action=action,
                confidence=confidence,
                latest_price=position.latest_price,
                average_cost=position.average_cost,
                unrealized_gain_loss_percent=position.unrealized_gain_loss_percent,
                portfolio_weight=position.portfolio_weight,
                goal=_goal_read(goal, company) if goal else None,
                reasons=reasons,
                risks=risks,
                next_action=next_action,
            )
        )

    return PortfolioExitIntelligenceRead(
        generated_date=datetime.now(UTC).date(),
        signals=sorted(signals, key=_exit_sort_key),
    )


def _entry_signal(
    session: Session,
    watchlist: Watchlist,
    item: WatchlistItem,
    company: Company,
) -> WatchlistEntrySignalRead:
    latest_price = _latest_price(session, company.id)
    price_when_added = _price_on_or_before(session, company.id, item.created_at.date())
    high, low = _price_range(session, company.id)
    position = _range_position(latest_price.close_price if latest_price else None, high, low)
    ratio = _latest_ratio(session, company.id)
    score = _latest_score(session, company.id)
    rules = investment_rules(session, company)
    reasons: list[str] = []
    risks: list[str] = []

    if position is not None:
        if position <= Decimal(35):
            reasons.append("Current price is closer to its 52-week low than its high.")
        elif position >= Decimal(80):
            risks.append("Current price is close to its 52-week high; entry may be expensive.")
    else:
        risks.append("Not enough price history to judge 52-week entry range.")

    if ratio and ratio.pe_ratio is not None:
        if ratio.pe_ratio <= Decimal(8):
            reasons.append("P/E ratio looks inexpensive on a first-pass valuation check.")
        elif ratio.pe_ratio >= Decimal(18):
            risks.append("P/E ratio is elevated; confirm earnings can justify the price.")
    else:
        risks.append("P/E ratio is unavailable; fundamentals need more work.")

    if score:
        if score.overall_score >= Decimal(70):
            reasons.append("Scanner score suggests the company deserves research attention.")
        if score.status in {"Insufficient data", "Needs source review"}:
            risks.append(f"Scanner status is {score.status}.")
    else:
        risks.append("Run a market scan before making an entry decision.")

    if rules.ngx_market_rules.price_band_status in {"Limit Up", "Near Limit Up"}:
        risks.append("Price is at or near the NGX +10% daily band; avoid chasing.")
    if rules.decision_guardrails and not rules.decision_guardrails[0].startswith("No hard"):
        risks.extend(rules.decision_guardrails[:2])

    entry_quality, decision_label, next_action = _entry_decision(position, ratio, score, risks)

    return WatchlistEntrySignalRead(
        watchlist_id=watchlist.id,
        watchlist_name=watchlist.name,
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        stock_types=rules.stock_types,
        entry_quality=entry_quality,
        decision_label=decision_label,
        next_action=next_action,
        price_range=PriceRangeRead(
            latest_price=latest_price.close_price if latest_price else None,
            price_when_added=price_when_added.close_price if price_when_added else None,
            fifty_two_week_high=high,
            fifty_two_week_low=low,
            position_in_range_percent=position,
        ),
        pe_ratio=ratio.pe_ratio if ratio else None,
        overall_score=score.overall_score if score else None,
        valuation_score=score.valuation_score if score else None,
        data_confidence=ratio.data_confidence if ratio else None,
        reasons=reasons or ["No strong positive signal yet; keep monitoring."],
        risks=risks,
    )


def _entry_decision(
    position: Decimal | None,
    ratio: CompanyRatio | None,
    score: CompanyScore | None,
    risks: list[str],
) -> tuple[str, str, str]:
    if not score or score.status == "Insufficient data":
        return "Unknown", "Needs Research", "Collect fundamentals or run a fresh scan before buying."
    if any("avoid chasing" in risk.lower() for risk in risks):
        return "Risky", "Do Not Chase", "Wait for price discovery after the sharp daily move."
    if position is not None and position >= Decimal(80):
        return "Expensive", "Watch for Better Entry", "Keep it on watchlist and wait for valuation support."
    if ratio and ratio.pe_ratio is not None and ratio.pe_ratio >= Decimal(18):
        return "Expensive", "Good Company, Pricey", "Compare with sector peers before entering."
    if score.overall_score >= Decimal(70) and (position is None or position <= Decimal(65)):
        return "Good", "Research Now", "Open the company brief and confirm the thesis before buying."
    if score.valuation_score >= Decimal(70):
        return "Fair", "Watch Closely", "Monitor price, P/E, and the next earnings update."
    return "Fair", "Still Watching", "Keep tracking until the reason to buy becomes clearer."


def _exit_decision(
    position,
    goal: InvestmentGoal | None,
    score: CompanyScore | None,
    ratio: CompanyRatio | None,
    better_opportunities: list[str],
) -> tuple[str, str, list[str], list[str], str]:
    reasons: list[str] = []
    risks: list[str] = []
    action = "Hold and Monitor"
    confidence = "Medium"

    if goal:
        if goal.target_price and position.latest_price and position.latest_price >= goal.target_price:
            reasons.append("Target price has been reached.")
            action = "Goal Achieved"
            confidence = "High"
        if (
            goal.target_return_percent
            and position.unrealized_gain_loss_percent
            and position.unrealized_gain_loss_percent >= goal.target_return_percent
        ):
            reasons.append("Target return has been reached.")
            action = "Goal Achieved"
            confidence = "High"
        if goal.review_date and goal.review_date <= datetime.now(UTC).date():
            risks.append("Goal review date has arrived; reassess whether the thesis still holds.")
    else:
        risks.append("No investment goal is recorded for this holding.")

    if position.portfolio_weight is not None and position.portfolio_weight > Decimal(30):
        risks.append("This holding is above 30% of portfolio value.")
        if action == "Hold and Monitor":
            action = "Trim Candidate"

    if score and score.status in {"Insufficient data", "Needs source review"}:
        risks.append(f"Scanner status is {score.status}; do not hold blindly.")
    if score and score.overall_score < Decimal(45):
        risks.append("Overall score is weak relative to the market scan.")
        action = "Review for Exit"
    if ratio and ratio.pe_ratio is not None and ratio.pe_ratio >= Decimal(20):
        risks.append("Valuation appears stretched on P/E.")
        if action == "Hold and Monitor":
            action = "Take Profit Review"

    peer_options = [symbol for symbol in better_opportunities if symbol != position.symbol][:3]
    if peer_options:
        reasons.append(f"Compare against stronger current opportunities: {', '.join(peer_options)}.")

    if action == "Goal Achieved":
        next_action = "Decide whether to take profit, trim, or set a new goal."
    elif action in {"Trim Candidate", "Take Profit Review"}:
        next_action = "Review portfolio weight and decide whether to reduce exposure."
    elif action == "Review for Exit":
        next_action = "Check the original thesis and compare with better opportunities."
        confidence = "High"
    else:
        reasons.append("No hard sell signal detected.")
        next_action = "Keep monitoring price, fundamentals, and portfolio fit."

    return action, confidence, reasons, risks, next_action


def _focus_warning(member_count: int) -> str | None:
    if member_count <= WATCHLIST_FOCUS_LIMIT:
        return None
    return (
        f"This watchlist has {member_count} companies. Keep beginner watchlists near "
        f"{WATCHLIST_FOCUS_LIMIT} names so attention stays focused."
    )


def _latest_price(session: Session, company_id: int) -> Price | None:
    return session.scalar(
        select(Price)
        .where(Price.company_id == company_id)
        .order_by(desc(Price.trade_date), desc(Price.id))
        .limit(1)
    )


def _price_on_or_before(session: Session, company_id: int, as_of_date: date) -> Price | None:
    return session.scalar(
        select(Price)
        .where(Price.company_id == company_id, Price.trade_date <= as_of_date)
        .order_by(desc(Price.trade_date), desc(Price.id))
        .limit(1)
    )


def _price_range(session: Session, company_id: int) -> tuple[Decimal | None, Decimal | None]:
    latest_price = _latest_price(session, company_id)
    if not latest_price:
        return None, None
    since = latest_price.trade_date - timedelta(days=365)
    high, low = session.execute(
        select(func.max(Price.close_price), func.min(Price.close_price)).where(
            Price.company_id == company_id,
            Price.trade_date >= since,
            Price.trade_date <= latest_price.trade_date,
        )
    ).one()
    return high, low


def _range_position(
    latest_price: Decimal | None,
    high: Decimal | None,
    low: Decimal | None,
) -> Decimal | None:
    if latest_price is None or high is None or low is None or high == low:
        return None
    return (((latest_price - low) / (high - low)) * Decimal(100)).quantize(Decimal("0.0001"))


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


def _latest_ratios(session: Session) -> dict[int, CompanyRatio]:
    ratios: dict[int, CompanyRatio] = {}
    rows = session.scalars(
        select(CompanyRatio).order_by(
            CompanyRatio.company_id,
            desc(CompanyRatio.as_of_date),
            desc(CompanyRatio.id),
        )
    )
    for ratio in rows:
        ratios.setdefault(ratio.company_id, ratio)
    return ratios


def _latest_scores(session: Session) -> dict[int, CompanyScore]:
    scores: dict[int, CompanyScore] = {}
    rows = session.scalars(
        select(CompanyScore).order_by(
            CompanyScore.company_id,
            desc(CompanyScore.as_of_date),
            desc(CompanyScore.id),
        )
    )
    for score in rows:
        scores.setdefault(score.company_id, score)
    return scores


def _active_goals_by_symbol(session: Session) -> dict[str, InvestmentGoal]:
    rows = session.execute(
        select(InvestmentGoal, Company)
        .join(Company, Company.id == InvestmentGoal.company_id)
        .where(InvestmentGoal.status == "active")
        .order_by(desc(InvestmentGoal.created_at))
    )
    goals: dict[str, InvestmentGoal] = {}
    for goal, company in rows:
        goals.setdefault(company.symbol, goal)
    return goals


def _better_opportunities(session: Session) -> list[str]:
    rows = session.execute(
        select(Company.symbol)
        .join(CompanyScore, CompanyScore.company_id == Company.id)
        .where(
            CompanyScore.overall_score >= Decimal(70),
            CompanyScore.status.notin_(["Insufficient data", "Needs source review"]),
        )
        .order_by(desc(CompanyScore.overall_score))
        .limit(5)
    )
    return list(rows.scalars())


def _entry_sort_key(signal: WatchlistEntrySignalRead) -> tuple[int, Decimal]:
    priority = {
        "Research Now": 0,
        "Watch Closely": 1,
        "Still Watching": 2,
        "Watch for Better Entry": 3,
        "Good Company, Pricey": 4,
        "Do Not Chase": 5,
        "Needs Research": 6,
    }
    return priority.get(signal.decision_label, 9), -(signal.overall_score or Decimal(0))


def _exit_sort_key(signal: ExitSignalRead) -> tuple[int, Decimal]:
    priority = {
        "Goal Achieved": 0,
        "Review for Exit": 1,
        "Trim Candidate": 2,
        "Take Profit Review": 3,
        "Hold and Monitor": 4,
    }
    return priority.get(signal.action, 9), -(signal.portfolio_weight or Decimal(0))


def _goal_read(goal: InvestmentGoal, company: Company) -> InvestmentGoalRead:
    return InvestmentGoalRead(
        id=goal.id,
        symbol=company.symbol,
        name=company.name,
        goal_type=goal.goal_type,
        target_price=goal.target_price,
        target_return_percent=goal.target_return_percent,
        target_dividend_yield=goal.target_dividend_yield,
        target_date=goal.target_date,
        review_date=goal.review_date,
        reason=goal.reason,
        sell_rule=goal.sell_rule,
        status=goal.status,
    )
