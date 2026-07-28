from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ngx_research.models import AlertEvent, AlertRule, Company, CompanyScore, Price
from ngx_research.schemas import AlertEvaluationRead, AlertEventRead, AlertRuleRead
from ngx_research.services.portfolio import portfolio_summary

PRICE_ABOVE = "PRICE_ABOVE"
PRICE_BELOW = "PRICE_BELOW"
SCORE_ABOVE = "SCORE_ABOVE"
SCORE_BELOW = "SCORE_BELOW"
STATUS_EQUALS = "STATUS_EQUALS"
PORTFOLIO_WEIGHT_ABOVE = "PORTFOLIO_WEIGHT_ABOVE"

NUMERIC_RULES = {
    PRICE_ABOVE,
    PRICE_BELOW,
    SCORE_ABOVE,
    SCORE_BELOW,
    PORTFOLIO_WEIGHT_ABOVE,
}
TEXT_RULES = {STATUS_EQUALS}
ALLOWED_RULES = NUMERIC_RULES | TEXT_RULES


def create_alert_rule(
    session: Session,
    company: Company,
    rule_type: str,
    threshold_value: Decimal | None,
    text_value: str | None,
    notes: str | None,
) -> AlertRuleRead:
    normalized_type = rule_type.upper()
    _validate_rule(normalized_type, threshold_value, text_value)
    rule = AlertRule(
        company_id=company.id,
        rule_type=normalized_type,
        threshold_value=threshold_value,
        text_value=text_value.strip() if text_value else None,
        notes=notes,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return _rule_read(rule, company)


def list_alert_rules(
    session: Session,
    active_only: bool = False,
    limit: int = 100,
) -> list[AlertRuleRead]:
    statement = (
        select(AlertRule, Company)
        .join(Company, Company.id == AlertRule.company_id)
        .order_by(desc(AlertRule.created_at))
        .limit(limit)
    )
    if active_only:
        statement = statement.where(AlertRule.is_active.is_(True))
    return [_rule_read(rule, company) for rule, company in session.execute(statement)]


def set_alert_rule_active(session: Session, rule: AlertRule, is_active: bool) -> AlertRuleRead:
    company = session.get(Company, rule.company_id)
    if not company:
        raise ValueError("alert rule company no longer exists")
    rule.is_active = is_active
    session.commit()
    session.refresh(rule)
    return _rule_read(rule, company)


def evaluate_alert_rules(session: Session) -> AlertEvaluationRead:
    companies = {company.id: company for company in session.scalars(select(Company))}
    latest_prices = _latest_prices(session)
    latest_scores = _latest_scores(session)
    positions = {position.symbol: position for position in portfolio_summary(session).positions}
    today = datetime.now(UTC).date()

    evaluated = 0
    events: list[AlertEventRead] = []
    rules = session.scalars(
        select(AlertRule).where(AlertRule.is_active.is_(True)).order_by(AlertRule.id)
    )
    for rule in rules:
        company = companies.get(rule.company_id)
        if not company:
            continue
        evaluated += 1
        triggered, observed_value, observed_text, message = _evaluate_rule(
            rule=rule,
            company=company,
            latest_price=latest_prices.get(company.id),
            latest_score=latest_scores.get(company.id),
            portfolio_weight=positions.get(company.symbol).portfolio_weight
            if positions.get(company.symbol)
            else None,
        )
        if not triggered:
            continue
        event = AlertEvent(
            alert_rule_id=rule.id,
            company_id=company.id,
            alert_date=today,
            rule_type=rule.rule_type,
            observed_value=observed_value,
            observed_text=observed_text,
            message=message,
            status="open",
        )
        session.add(event)
        session.flush()
        events.append(_event_read(event, company))

    session.commit()
    return AlertEvaluationRead(evaluated_rules=evaluated, triggered=len(events), events=events)


def list_alert_events(
    session: Session,
    status: str | None = None,
    limit: int = 100,
) -> list[AlertEventRead]:
    statement = (
        select(AlertEvent, Company)
        .join(Company, Company.id == AlertEvent.company_id)
        .order_by(desc(AlertEvent.alert_date), desc(AlertEvent.id))
        .limit(limit)
    )
    if status:
        statement = statement.where(AlertEvent.status == status.lower())
    return [_event_read(event, company) for event, company in session.execute(statement)]


def set_alert_event_status(session: Session, event: AlertEvent, status: str) -> AlertEventRead:
    normalized = status.lower()
    if normalized not in {"open", "acknowledged", "dismissed"}:
        raise ValueError("status must be open, acknowledged, or dismissed")
    company = session.get(Company, event.company_id)
    if not company:
        raise ValueError("alert event company no longer exists")
    event.status = normalized
    session.commit()
    session.refresh(event)
    return _event_read(event, company)


def _validate_rule(
    rule_type: str,
    threshold_value: Decimal | None,
    text_value: str | None,
) -> None:
    if rule_type not in ALLOWED_RULES:
        allowed = ", ".join(sorted(ALLOWED_RULES))
        raise ValueError(f"rule_type must be one of: {allowed}")
    if rule_type in NUMERIC_RULES and threshold_value is None:
        raise ValueError("threshold_value is required for numeric alert rules")
    if rule_type in TEXT_RULES and not text_value:
        raise ValueError("text_value is required for text alert rules")


def _evaluate_rule(
    rule: AlertRule,
    company: Company,
    latest_price: Price | None,
    latest_score: CompanyScore | None,
    portfolio_weight: Decimal | None,
) -> tuple[bool, Decimal | None, str | None, str]:
    if rule.rule_type == PRICE_ABOVE:
        return _numeric_check(company.symbol, "price", latest_price.close_price if latest_price else None, rule, ">=")
    if rule.rule_type == PRICE_BELOW:
        return _numeric_check(company.symbol, "price", latest_price.close_price if latest_price else None, rule, "<=")
    if rule.rule_type == SCORE_ABOVE:
        value = latest_score.overall_score if latest_score else None
        return _numeric_check(company.symbol, "score", value, rule, ">=")
    if rule.rule_type == SCORE_BELOW:
        value = latest_score.overall_score if latest_score else None
        return _numeric_check(company.symbol, "score", value, rule, "<=")
    if rule.rule_type == PORTFOLIO_WEIGHT_ABOVE:
        return _numeric_check(company.symbol, "portfolio weight", portfolio_weight, rule, ">=")
    if rule.rule_type == STATUS_EQUALS:
        status = latest_score.status if latest_score else None
        target = rule.text_value or ""
        triggered = bool(status and status.lower() == target.lower())
        message = f"{company.symbol} status is {status}; target is {target}."
        return triggered, None, status, message
    return False, None, None, ""


def _numeric_check(
    symbol: str,
    label: str,
    observed: Decimal | None,
    rule: AlertRule,
    operator: str,
) -> tuple[bool, Decimal | None, str | None, str]:
    threshold = rule.threshold_value
    if observed is None or threshold is None:
        return False, observed, None, f"{symbol} has no observed {label}."
    if operator == ">=":
        triggered = observed >= threshold
    else:
        triggered = observed <= threshold
    message = f"{symbol} {label} is {observed}; alert threshold is {operator} {threshold}."
    return triggered, observed, None, message


def _latest_prices(session: Session) -> dict[int, Price]:
    prices: dict[int, Price] = {}
    rows = session.scalars(select(Price).order_by(Price.company_id, desc(Price.trade_date), desc(Price.id)))
    for price in rows:
        prices.setdefault(price.company_id, price)
    return prices


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


def _rule_read(rule: AlertRule, company: Company) -> AlertRuleRead:
    return AlertRuleRead(
        id=rule.id,
        symbol=company.symbol,
        name=company.name,
        rule_type=rule.rule_type,
        threshold_value=rule.threshold_value,
        text_value=rule.text_value,
        is_active=rule.is_active,
        notes=rule.notes,
    )


def _event_read(event: AlertEvent, company: Company) -> AlertEventRead:
    return AlertEventRead(
        id=event.id,
        alert_rule_id=event.alert_rule_id,
        symbol=company.symbol,
        name=company.name,
        alert_date=event.alert_date,
        rule_type=event.rule_type,
        observed_value=event.observed_value,
        observed_text=event.observed_text,
        message=event.message,
        status=event.status,
    )
