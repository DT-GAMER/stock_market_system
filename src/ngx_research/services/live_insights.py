from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ngx_research.models import (
    Company,
    CompanyIntelligenceSnapshot,
    CorporateDisclosure,
    MarketNewsItem,
    Price,
)
from ngx_research.schemas import (
    CompanyLiveInsightCardRead,
    CompanyLiveInsightsRead,
    CompanyLiveNewsItemRead,
    CompanyLivePerformanceRead,
    CompanyLivePriceRead,
    CompanyPerformanceWindowRead,
)

HUNDRED = Decimal(100)
WINDOWS = (
    ("1W", 7),
    ("1M", 30),
    ("3M", 90),
    ("YTD", None),
    ("1Y", 365),
)


def company_live_insights(session: Session, symbol: str) -> CompanyLiveInsightsRead:
    normalized = symbol.strip().upper()
    company = session.scalar(select(Company).where(Company.symbol == normalized))
    if not company:
        raise ValueError(f"{normalized} is not in the company universe.")

    prices = _company_prices(session, company.id, limit=280)
    snapshot = _latest_snapshot(session, company.id)
    disclosures = _company_disclosures(session, company)
    news = _company_news(session, company)
    price_read = _price_read(company, prices)
    performance = _performance_read(session, company, prices)
    cards = _insight_cards(
        company=company,
        prices=prices,
        snapshot=snapshot,
        disclosures=disclosures,
        news=news,
        performance=performance,
        price=price_read,
    )
    notes = _data_notes(prices, snapshot, disclosures, news)
    return CompanyLiveInsightsRead(
        symbol=company.symbol,
        name=company.name,
        sector=company.sector,
        generated_at=datetime.now(UTC),
        price=price_read,
        performance=performance,
        cards=cards,
        recent_news=[_news_read(item) for item in news[:6]],
        recent_disclosures=[_disclosure_read(item) for item in disclosures[:6]],
        data_notes=notes,
    )


def _company_prices(session: Session, company_id: int, limit: int) -> list[Price]:
    rows = list(
        session.scalars(
            select(Price)
            .where(Price.company_id == company_id)
            .order_by(desc(Price.trade_date), desc(Price.id))
            .limit(limit)
        )
    )
    return sorted(rows, key=lambda price: (price.trade_date, price.id))


def _latest_snapshot(session: Session, company_id: int) -> CompanyIntelligenceSnapshot | None:
    return session.scalar(
        select(CompanyIntelligenceSnapshot)
        .where(CompanyIntelligenceSnapshot.company_id == company_id)
        .order_by(desc(CompanyIntelligenceSnapshot.as_of_date), desc(CompanyIntelligenceSnapshot.id))
        .limit(1)
    )


def _company_disclosures(session: Session, company: Company) -> list[CorporateDisclosure]:
    return list(
        session.scalars(
            select(CorporateDisclosure)
            .where(
                (CorporateDisclosure.company_id == company.id)
                | (CorporateDisclosure.symbol == company.symbol)
            )
            .order_by(desc(CorporateDisclosure.published_at), desc(CorporateDisclosure.id))
            .limit(20)
        )
    )


def _company_news(session: Session, company: Company) -> list[MarketNewsItem]:
    aliases = _company_aliases(company)
    recent = session.scalars(
        select(MarketNewsItem).order_by(desc(MarketNewsItem.published_at), desc(MarketNewsItem.id)).limit(250)
    )
    matched: list[MarketNewsItem] = []
    for item in recent:
        text = _search_text(item.title, item.summary)
        if any(alias in text for alias in aliases):
            matched.append(item)
        if len(matched) >= 20:
            break
    return matched


def _price_read(company: Company, prices: list[Price]) -> CompanyLivePriceRead:
    latest = prices[-1] if prices else None
    previous = _previous_price(prices, latest)
    if not latest:
        return CompanyLivePriceRead(
            direction="unknown",
            label="No latest NGX price",
            summary=f"{company.symbol} does not yet have a synced latest NGX price.",
        )

    change = latest.close_price - previous.close_price if previous else None
    change_percent = _percent_change(latest.close_price, previous.close_price) if previous else None
    direction = _direction(change)
    label = _movement_label(direction, change_percent)
    summary = _movement_summary(company, latest, previous, change, change_percent)
    return CompanyLivePriceRead(
        latest_price=latest.close_price,
        previous_close=previous.close_price if previous else None,
        price_change=change,
        price_change_percent=change_percent,
        trade_date=latest.trade_date,
        direction=direction,
        label=label,
        summary=summary,
    )


def _performance_read(
    session: Session,
    company: Company,
    prices: list[Price],
) -> CompanyLivePerformanceRead:
    latest = prices[-1] if prices else None
    windows = [_window_read(prices, label, days, latest) for label, days in WINDOWS]
    one_month = next((item for item in windows if item.window == "1M"), None)
    sector_rank, sector_count = _sector_performance_rank(session, company)
    low, high, position = _stored_range(prices)
    headline = _performance_headline(one_month, sector_rank, sector_count)
    summary = _performance_summary(company, one_month, sector_rank, sector_count, position)
    return CompanyLivePerformanceRead(
        headline=headline,
        summary=summary,
        sector_rank_1m=sector_rank,
        sector_peer_count=sector_count,
        fifty_two_week_high=high,
        fifty_two_week_low=low,
        position_in_52_week_range_percent=position,
        windows=windows,
    )


def _window_read(
    prices: list[Price],
    label: str,
    days: int | None,
    latest: Price | None,
) -> CompanyPerformanceWindowRead:
    if not latest:
        return CompanyPerformanceWindowRead(
            window=label,
            available=False,
            summary=f"{label} performance is unavailable because no latest price is stored.",
        )
    if label == "YTD":
        target = date(latest.trade_date.year, 1, 1)
    else:
        target = latest.trade_date - timedelta(days=days or 0)
    start = _price_on_or_before(prices, target)
    approximated = False
    if not start and prices and prices[0].trade_date < latest.trade_date:
        start = prices[0]
        approximated = True
    if not start or start.id == latest.id:
        return CompanyPerformanceWindowRead(
            window=label,
            available=False,
            end_date=latest.trade_date,
            end_price=latest.close_price,
            summary=f"{label} performance needs more historical price records.",
        )
    return_percent = _percent_change(latest.close_price, start.close_price)
    summary = (
        f"{label} return is {_fmt_percent(return_percent)} from {_fmt_money(start.close_price)} "
        f"to {_fmt_money(latest.close_price)}."
    )
    if approximated:
        summary += " This uses the earliest stored price because the full window is not available."
    return CompanyPerformanceWindowRead(
        window=label,
        available=True,
        start_date=start.trade_date,
        end_date=latest.trade_date,
        start_price=start.close_price,
        end_price=latest.close_price,
        return_percent=return_percent,
        summary=summary,
    )


def _sector_performance_rank(session: Session, company: Company) -> tuple[int | None, int | None]:
    if not company.sector:
        return None, None
    peers = list(
        session.scalars(
            select(Company).where(
                Company.sector == company.sector,
                Company.is_active.is_(True),
            )
        )
    )
    if len(peers) < 2:
        return None, len(peers) or None

    peer_ids = [peer.id for peer in peers]
    latest_trade_date = session.scalar(
        select(Price.trade_date)
        .where(Price.company_id == company.id)
        .order_by(desc(Price.trade_date), desc(Price.id))
        .limit(1)
    )
    if not latest_trade_date:
        return None, len(peers)

    since = latest_trade_date - timedelta(days=45)
    rows = list(
        session.scalars(
            select(Price)
            .where(Price.company_id.in_(peer_ids), Price.trade_date >= since)
            .order_by(Price.company_id, Price.trade_date, Price.id)
        )
    )
    by_company: dict[int, list[Price]] = defaultdict(list)
    for price in rows:
        by_company[price.company_id].append(price)

    returns: list[tuple[int, Decimal]] = []
    for peer in peers:
        peer_prices = by_company.get(peer.id, [])
        latest = peer_prices[-1] if peer_prices else None
        window = _window_read(peer_prices, "1M", 30, latest)
        if window.available and window.return_percent is not None:
            returns.append((peer.id, window.return_percent))

    if len(returns) < 2:
        return None, len(returns) or len(peers)
    ranked = sorted(returns, key=lambda row: row[1], reverse=True)
    for index, (company_id, _) in enumerate(ranked, start=1):
        if company_id == company.id:
            return index, len(ranked)
    return None, len(ranked)


def _stored_range(prices: list[Price]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if not prices:
        return None, None, None
    closes = [price.close_price for price in prices]
    low = min(closes)
    high = max(closes)
    latest = prices[-1].close_price
    if high <= low:
        return low, high, None
    position = ((latest - low) / (high - low) * HUNDRED).quantize(Decimal("0.01"))
    return low, high, _clamp(position)


def _insight_cards(
    company: Company,
    prices: list[Price],
    snapshot: CompanyIntelligenceSnapshot | None,
    disclosures: list[CorporateDisclosure],
    news: list[MarketNewsItem],
    performance: CompanyLivePerformanceRead,
    price: CompanyLivePriceRead,
) -> list[CompanyLiveInsightCardRead]:
    return [
        _performance_card(company, performance, price),
        _news_card(company, news, disclosures),
        _risk_card(company, prices, snapshot, news, disclosures),
        _decision_bridge_card(company, snapshot, performance),
    ]


def _performance_card(
    company: Company,
    performance: CompanyLivePerformanceRead,
    price: CompanyLivePriceRead,
) -> CompanyLiveInsightCardRead:
    points = [price.summary]
    points.extend(window.summary for window in performance.windows if window.available and window.window in {"1W", "1M", "YTD"})
    if performance.sector_rank_1m and performance.sector_peer_count:
        points.append(
            f"Recent 1-month momentum ranks {performance.sector_rank_1m} of "
            f"{performance.sector_peer_count} stored {company.sector or 'sector'} peers."
        )
    if performance.position_in_52_week_range_percent is not None:
        points.append(
            f"The latest price sits around {_fmt_percent(performance.position_in_52_week_range_percent)} "
            "of the stored 52-week price range."
        )
    return CompanyLiveInsightCardRead(
        key="performance",
        title="Performance",
        tone=_performance_tone(performance),
        summary=performance.summary,
        points=_dedupe(points)[:5],
        source_count=sum(1 for item in performance.windows if item.available),
        generated_from=["NGX Pulse price history", "Sector price comparison"],
    )


def _news_card(
    company: Company,
    news: list[MarketNewsItem],
    disclosures: list[CorporateDisclosure],
) -> CompanyLiveInsightCardRead:
    source_count = len(news) + len(disclosures)
    items = [_clean_title(item.title) for item in news[:2]]
    items.extend(_clean_title(item.title) for item in disclosures[:2])
    points = [item for item in items if item]
    if not points:
        points = [
            "No recent company-specific NGX Pulse news or disclosures are stored for this company yet.",
            "Sync market news and disclosures to keep this card current.",
        ]
    summary = _news_summary(company, news, disclosures)
    return CompanyLiveInsightCardRead(
        key="news",
        title="In the news",
        tone="neutral" if source_count else "warning",
        summary=summary,
        points=_dedupe(points)[:5],
        source_count=source_count,
        generated_from=["NGX Pulse market news", "NGX Pulse disclosures"],
    )


def _risk_card(
    company: Company,
    prices: list[Price],
    snapshot: CompanyIntelligenceSnapshot | None,
    news: list[MarketNewsItem],
    disclosures: list[CorporateDisclosure],
) -> CompanyLiveInsightCardRead:
    evidence_text = " ".join(
        [item.title for item in news[:8]]
        + [item.summary or "" for item in news[:8]]
        + [item.title for item in disclosures[:8]]
    )
    generated_risks = _keyword_risks(evidence_text)
    snapshot_risks = list(snapshot.risks if snapshot else [])
    volatility_risk = _volatility_risk(prices)
    specific_points = _dedupe(
        generated_risks + ([volatility_risk] if volatility_risk else []) + snapshot_risks
    )[:5]
    points = specific_points
    if not specific_points:
        points = [
            "No urgent company-specific risk event is visible in the stored news or disclosure set.",
            "Still review earnings quality, valuation, liquidity, and sector exposure before buying.",
        ]
    summary = (
        "Investors should watch "
        + " ".join(specific_points[:2])[:1].lower()
        + " ".join(specific_points[:2])[1:]
        if specific_points
        else "No urgent company-specific risk event is visible in the stored news or disclosure set."
    )
    return CompanyLiveInsightCardRead(
        key="risks",
        title="Key risks",
        tone="warning" if specific_points else "neutral",
        summary=summary,
        points=points,
        source_count=len(news) + len(disclosures) + int(bool(snapshot)),
        generated_from=["Recent news/disclosures", "EquityKobo intelligence snapshot", "Price volatility"],
    )


def _decision_bridge_card(
    company: Company,
    snapshot: CompanyIntelligenceSnapshot | None,
    performance: CompanyLivePerformanceRead,
) -> CompanyLiveInsightCardRead:
    one_month = next((item for item in performance.windows if item.window == "1M"), None)
    if not snapshot:
        return CompanyLiveInsightCardRead(
            key="decision_context",
            title="Decision context",
            tone="warning",
            summary=(
                f"{company.symbol} has live market context, but no intelligence snapshot yet. "
                "Run the intelligence, valuation, and peer engines before relying on the decision card."
            ),
            points=[
                "Price movement alone is not enough to decide whether a business is worth buying.",
                "The missing layer is long-term company memory: fundamentals, dividends, valuation, and peer rank.",
            ],
            source_count=0,
            generated_from=["EquityKobo intelligence snapshot"],
        )

    points = [
        f"Long-term decision label: {snapshot.final_label}.",
        f"Invest score: {_fmt_score(snapshot.overall_score)} with data confidence {_fmt_score(snapshot.data_confidence_score)}.",
    ]
    if one_month and one_month.available and one_month.return_percent is not None:
        points.append(
            "Recent price action is positive, but the buy decision still depends on valuation, earnings, "
            "cash flow, and portfolio fit."
            if one_month.return_percent > 0
            else "Recent price action is weak, so the system separates a possible cheaper entry from a weak business."
        )
    if snapshot.next_actions:
        points.append(snapshot.next_actions[0])
    return CompanyLiveInsightCardRead(
        key="decision_context",
        title="What this means for the decision",
        tone=_snapshot_tone(snapshot.final_label),
        summary=(
            f"{company.symbol}'s market performance is only one layer. EquityKobo still weighs it against "
            f"business quality, valuation, dividend evidence, risk, liquidity, and source confidence."
        ),
        points=_dedupe(points)[:5],
        source_count=1,
        generated_from=["EquityKobo intelligence snapshot", "Live performance layer"],
    )


def _keyword_risks(text: str) -> list[str]:
    normalized = text.lower()
    rules = [
        (
            ("capital raise", "rights issue", "share sale", "private placement", "public offer", "new shares"),
            "possible dilution from capital raising or new share issuance.",
        ),
        (
            ("interest rate", "mpr", "treasury yield", "yield environment"),
            "earnings and valuation sensitivity to interest-rate changes.",
        ),
        (
            ("fx", "foreign exchange", "exchange rate", "naira weakness", "currency"),
            "foreign-exchange pressure that can affect costs, debt, or reported earnings.",
        ),
        (
            ("regulation", "regulatory", "cbn", "sec", "ngx", "compliance"),
            "regulatory or compliance changes that could affect operations or investor sentiment.",
        ),
        (
            ("expansion", "acquisition", "merger", "integration", "new plant", "new branch"),
            "execution risk from ambitious expansion, acquisitions, or integration plans.",
        ),
        (
            ("loss", "decline", "profit warning", "impairment", "restructure"),
            "earnings deterioration or one-off losses that need confirmation in the financial statements.",
        ),
        (
            ("suspension", "delisting", "late filing", "restatement", "audit qualification"),
            "governance, filing, or audit-quality risk.",
        ),
    ]
    risks: list[str] = []
    for keywords, risk in rules:
        if any(keyword in normalized for keyword in keywords):
            risks.append(risk)
    return risks


def _volatility_risk(prices: list[Price]) -> str | None:
    latest = prices[-1] if prices else None
    if not latest:
        return None
    recent = prices[-12:]
    if len(recent) < 5:
        return None
    daily_moves: list[Decimal] = []
    for previous, current in zip(recent, recent[1:]):
        move = _percent_change(current.close_price, previous.close_price)
        if move is not None:
            daily_moves.append(abs(move))
    if not daily_moves:
        return None
    average_move = sum(daily_moves, Decimal(0)) / Decimal(len(daily_moves))
    if average_move >= Decimal(5):
        return "high recent price volatility; avoid chasing sharp moves without a written entry rule."
    if average_move >= Decimal(3):
        return "moderate recent price volatility; use staged entries instead of committing all capital at once."
    return None


def _data_notes(
    prices: list[Price],
    snapshot: CompanyIntelligenceSnapshot | None,
    disclosures: list[CorporateDisclosure],
    news: list[MarketNewsItem],
) -> list[str]:
    notes: list[str] = []
    if len(prices) < 30:
        notes.append("Price history is shorter than 30 records; performance windows may be thin.")
    if not snapshot:
        notes.append("No intelligence snapshot yet; run the intelligence engine for a full decision layer.")
    if not disclosures:
        notes.append("No stored company disclosures found for this symbol.")
    if not news:
        notes.append("No symbol-matched market news found yet.")
    return notes


def _previous_price(prices: list[Price], latest: Price | None) -> Price | None:
    if not latest:
        return None
    for price in reversed(prices[:-1]):
        if price.trade_date < latest.trade_date:
            return price
    return prices[-2] if len(prices) >= 2 else None


def _price_on_or_before(prices: list[Price], target: date) -> Price | None:
    candidates = [price for price in prices if price.trade_date <= target]
    return candidates[-1] if candidates else None


def _percent_change(current: Decimal, previous: Decimal | None) -> Decimal | None:
    if previous is None or previous == 0:
        return None
    return ((current - previous) / abs(previous) * HUNDRED).quantize(Decimal("0.01"))


def _direction(change: Decimal | None) -> str:
    if change is None:
        return "unknown"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _movement_label(direction: str, change_percent: Decimal | None) -> str:
    if direction == "up":
        return f"Up {_fmt_percent(change_percent)}"
    if direction == "down":
        return f"Down {_fmt_percent(change_percent)}"
    if direction == "flat":
        return "Flat today"
    return "No daily movement yet"


def _movement_summary(
    company: Company,
    latest: Price,
    previous: Price | None,
    change: Decimal | None,
    change_percent: Decimal | None,
) -> str:
    if not previous or change is None or change_percent is None:
        return (
            f"{company.symbol} last closed at {_fmt_money(latest.close_price)} on "
            f"{latest.trade_date.isoformat()}, but there is no previous close to calculate a daily move."
        )
    return (
        f"{company.symbol} last closed at {_fmt_money(latest.close_price)}, "
        f"{_fmt_signed_money(change)} or {_fmt_signed_percent(change_percent)} versus the previous close."
    )


def _performance_headline(
    one_month: CompanyPerformanceWindowRead | None,
    sector_rank: int | None,
    sector_count: int | None,
) -> str:
    one_month_return = one_month.return_percent if one_month and one_month.available else None
    if sector_rank == 1 and sector_count and sector_count >= 3:
        return "Sector-leading recent momentum"
    if one_month_return is not None and one_month_return >= Decimal(20):
        return "Strong recent performer"
    if one_month_return is not None and one_month_return >= Decimal(5):
        return "Positive recent momentum"
    if one_month_return is not None and one_month_return <= Decimal(-10):
        return "Price under pressure"
    return "Performance needs context"


def _performance_summary(
    company: Company,
    one_month: CompanyPerformanceWindowRead | None,
    sector_rank: int | None,
    sector_count: int | None,
    position: Decimal | None,
) -> str:
    one_month_return = one_month.return_percent if one_month and one_month.available else None
    pieces: list[str] = []
    if one_month_return is not None:
        if one_month_return >= Decimal(20):
            pieces.append(
                f"{company.symbol} is a standout recent performer, up {_fmt_percent(one_month_return)} over the stored 1-month window."
            )
        elif one_month_return > 0:
            pieces.append(
                f"{company.symbol} has positive recent momentum, up {_fmt_percent(one_month_return)} over the stored 1-month window."
            )
        else:
            pieces.append(
                f"{company.symbol} is under recent price pressure, down {_fmt_percent(abs(one_month_return))} over the stored 1-month window."
            )
    else:
        pieces.append(f"{company.symbol} needs more price history before recent performance can be judged sharply.")
    if sector_rank and sector_count:
        pieces.append(f"It ranks {sector_rank} of {sector_count} stored {company.sector or 'sector'} peers by 1-month return.")
    if position is not None:
        pieces.append(f"The latest close sits at {_fmt_percent(position)} of its stored 52-week range.")
    return " ".join(pieces)


def _performance_tone(performance: CompanyLivePerformanceRead) -> str:
    one_month = next((item for item in performance.windows if item.window == "1M"), None)
    value = one_month.return_percent if one_month and one_month.available else None
    if value is None:
        return "neutral"
    if value >= Decimal(5):
        return "positive"
    if value <= Decimal(-10):
        return "danger"
    if value < 0:
        return "warning"
    return "neutral"


def _news_summary(
    company: Company,
    news: list[MarketNewsItem],
    disclosures: list[CorporateDisclosure],
) -> str:
    latest_titles = [_clean_title(item.title) for item in news[:2]]
    disclosure_titles = [_clean_title(item.title) for item in disclosures[:2]]
    if latest_titles or disclosure_titles:
        events = _dedupe(latest_titles + disclosure_titles)[:3]
        return (
            f"Recent stored news and disclosures for {company.symbol} centre on "
            f"{'; '.join(events)}. Treat this as a current-context layer and open the source before acting."
        )
    return (
        f"No recent company-specific market news is stored for {company.symbol}. "
        "Sync NGX Pulse market news and disclosures to keep this card current."
    )


def _company_aliases(company: Company) -> set[str]:
    stops = {
        "plc",
        "limited",
        "ltd",
        "holdings",
        "holding",
        "company",
        "companies",
        "bank",
        "nigeria",
        "group",
        "the",
        "and",
    }
    aliases = {_search_text(company.symbol)}
    compact_symbol = re.sub(r"[^a-z0-9]", "", company.symbol.lower())
    if compact_symbol:
        aliases.add(compact_symbol)
    name_text = _search_text(company.name)
    aliases.add(name_text)
    for token in name_text.split():
        if len(token) >= 5 and token not in stops:
            aliases.add(token)
    return {alias for alias in aliases if alias}


def _search_text(*parts: str | None) -> str:
    text = " ".join(part or "" for part in parts).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _news_read(item: MarketNewsItem) -> CompanyLiveNewsItemRead:
    return CompanyLiveNewsItemRead(
        title=item.title,
        source_name=item.source_name,
        published_at=item.published_at,
        url=item.url,
        summary=item.summary,
        item_type="news",
    )


def _disclosure_read(item: CorporateDisclosure) -> CompanyLiveNewsItemRead:
    return CompanyLiveNewsItemRead(
        title=item.title,
        source_name="NGX Disclosure",
        published_at=item.published_at,
        url=item.url,
        summary=item.disclosure_type,
        item_type="disclosure",
    )


def _snapshot_tone(label: str) -> str:
    if label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}:
        return "positive"
    if label in {"Watch for Better Entry", "Good Company, Expensive"}:
        return "warning"
    if label in {"Avoid for Now", "Speculative"}:
        return "danger"
    return "neutral"


def _clean_title(value: str) -> str:
    return " ".join(value.split())


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        cleaned = " ".join(str(item).split())
        if not cleaned:
            continue
        marker = cleaned.lower()
        if marker in seen:
            continue
        seen.add(marker)
        output.append(cleaned)
    return output


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal(0), min(HUNDRED, value))


def _fmt_money(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"NGN {value:,.2f}"


def _fmt_signed_money(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}NGN {value:,.2f}"


def _fmt_percent(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}%"


def _fmt_signed_percent(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}%"


def _fmt_score(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.1f}/100"
