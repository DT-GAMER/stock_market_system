from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ngx_research.database import Base
from ngx_research.models import (
    Company,
    CompanyIntelligenceSnapshot,
    CompanyPeerComparisonSnapshot,
    CompanyValuationSnapshot,
    CorporateDisclosure,
    Dividend,
    FinancialStatement,
    MarketNewsItem,
    NgxPulseFundamental,
    Price,
    SourceDocument,
)
from ngx_research.services.decision_card_engine import decision_card
from ngx_research.services.decision_dashboard import decision_opportunity_dashboard
from ngx_research.services.intelligence_engine import run_intelligence_engine
from ngx_research.services.live_insights import company_live_insights
from ngx_research.services.peer_comparison_engine import (
    company_peer_comparison,
    run_peer_comparison_engine,
)
from ngx_research.services.valuation_engine import company_valuation, run_valuation_engine


def _sqlite_engine(*, enforce_foreign_keys: bool = False):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if enforce_foreign_keys:
        @event.listens_for(engine, "connect")
        def _set_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def test_decision_card_explains_company_without_vague_research_label():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            run_valuation_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            run_peer_comparison_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            card = decision_card(session, "ARADEL")

            assert card.answer.startswith("YES")
            assert card.invest_score > Decimal(70)
            assert "Unclassified" not in card.stock_types
            assert "Quality compounder" in card.stock_types
            assert card.confidence in {"High", "Very High"}
            assert len(card.health_checks) >= 6
            assert any(item.label == "Cash flow" and item.status == "Strong" for item in card.health_checks)
            assert any("P/E" in point for point in card.valuation.points)
            assert card.valuation_snapshot is not None
            assert card.valuation_snapshot.fair_value_low is not None
            assert card.peer_comparison is not None
            assert card.peer_comparison.peer_count == 2
            assert card.peer_comparison.sector_rank in {1, 2}
            assert card.peer_comparison.metric_comparisons
            assert "Fair value range" in card.valuation.points[0]
            assert card.valuation_display.is_available
            assert card.valuation_display.fair_value_low is not None
            assert card.valuation_display.methods_used
            assert card.valuation_display.price_position_percent is not None
            assert card.health_display
            assert any(item.label == "Cash flow" and item.status == "Healthy" for item in card.health_display)
            assert card.dividend_display.is_available
            assert card.dividend_display.current_yield is not None
            assert len(card.dividend_display.annual_history) == 3
            assert card.dividend_display.projected_next_payout is not None
            assert card.moat_display.label in {"Durable advantage", "Developing advantage"}
            assert card.moat_display.peer_strength_score is not None
            assert any(gap.data_layer == "sector peer set" for gap in card.source_gaps)
            assert card.why_buy.points
            assert card.why_not_buy.points
            assert card.what_would_change_decision.points
            assert "No strong positive edge" not in "\n".join(card.why_buy.points)
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_intelligence_engine_rerun_clears_valuation_and_peer_children_first():
    engine = _sqlite_engine(enforce_foreign_keys=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            run_date = date(2026, 8, 3)
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=run_date, limit=10)
            run_valuation_engine(session, as_of_date=run_date, limit=10)
            run_peer_comparison_engine(session, as_of_date=run_date, limit=10)

            assert session.query(CompanyIntelligenceSnapshot).count() == 2
            assert session.query(CompanyValuationSnapshot).count() == 2
            assert session.query(CompanyPeerComparisonSnapshot).count() == 2

            rerun = run_intelligence_engine(session, as_of_date=run_date, limit=10)

            assert rerun.generated == 2
            assert session.query(CompanyIntelligenceSnapshot).count() == 2
            assert session.query(CompanyValuationSnapshot).count() == 0
            assert session.query(CompanyPeerComparisonSnapshot).count() == 0
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_valuation_engine_rerun_clears_peer_children_first():
    engine = _sqlite_engine(enforce_foreign_keys=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            run_date = date(2026, 8, 3)
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=run_date, limit=10)
            run_valuation_engine(session, as_of_date=run_date, limit=10)
            run_peer_comparison_engine(session, as_of_date=run_date, limit=10)

            assert session.query(CompanyValuationSnapshot).count() == 2
            assert session.query(CompanyPeerComparisonSnapshot).count() == 2

            rerun = run_valuation_engine(session, as_of_date=run_date, limit=10)

            assert rerun.generated == 2
            assert session.query(CompanyValuationSnapshot).count() == 2
            assert session.query(CompanyPeerComparisonSnapshot).count() == 0
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_decision_card_uses_live_dividends_when_snapshot_metrics_are_stale():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            snapshot = session.scalar(
                select(CompanyIntelligenceSnapshot)
                .join(Company)
                .where(Company.symbol == "ARADEL")
            )
            assert snapshot is not None
            snapshot.metrics = {
                **(snapshot.metrics or {}),
                "dividend_years": 0,
                "dividend_growth": None,
            }
            session.commit()

            card = decision_card(session, "ARADEL")

            assert card.dividend_quality != "No dividend evidence yet"
            assert card.dividend_display.dividend_strength != "No dividend evidence yet"
            assert card.dividend_display.years_with_dividends == 3
            assert len(card.dividend_display.annual_history) == 3
            assert card.dividend.points[1] == "Dividend years currently stored: 3."
            assert any(
                item.label == "Dividend safety" and "no dividend evidence" not in item.detail.lower()
                for item in card.health_checks
            )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_valuation_engine_creates_method_based_fair_value_range():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            result = run_valuation_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            rerun = run_valuation_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            valuation = company_valuation(session, "ARADEL")

            assert result.generated == 2
            assert rerun.generated == 2
            assert session.query(CompanyValuationSnapshot).count() == 2
            assert valuation.latest_price == Decimal("1526.8000")
            assert valuation.fair_value_low is not None
            assert valuation.fair_value_mid is not None
            assert valuation.fair_value_high is not None
            assert valuation.fair_value_low < valuation.fair_value_high
            assert valuation.margin_of_safety_percent is not None
            assert valuation.confidence_score > Decimal(50)
            assert valuation.valuation_label in {
                "Deeply Undervalued",
                "Undervalued",
                "Fairly Valued",
            }
            assert {method.name for method in valuation.methods} >= {
                "Sector P/E comparison",
                "Earnings power valuation",
                "Dividend yield support",
            }
            assert valuation.assumptions
            assert valuation.warnings
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_peer_comparison_engine_creates_sector_rank_and_is_rerunnable():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            run_valuation_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            result = run_peer_comparison_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            rerun = run_peer_comparison_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            comparison = company_peer_comparison(session, "ARADEL")

            assert result.generated == 2
            assert rerun.generated == 2
            assert session.query(CompanyPeerComparisonSnapshot).count() == 2
            assert comparison.peer_count == 2
            assert comparison.sector_rank in {1, 2}
            assert comparison.comparison_label in {
                "Sector Leader",
                "Top Sector Contender",
                "Above Sector Average",
                "Insufficient Peer Set",
            }
            assert comparison.best_overall_peer_symbol in {"ARADEL", "SEPLAT"}
            assert comparison.category_winners
            assert comparison.metric_comparisons
            assert comparison.peers
            assert comparison.strengths
            assert comparison.reasons
            assert comparison.next_actions
            assert all(row.stock_types for row in comparison.peers)
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_decision_dashboard_shapes_login_opportunity_desk():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            run_valuation_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            run_peer_comparison_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            dashboard = decision_opportunity_dashboard(session, limit=10)

            assert dashboard.as_of_date == date(2026, 8, 3)
            assert dashboard.market_summary.companies_scanned == 2
            assert dashboard.ranked
            assert dashboard.spotlight_cards
            assert dashboard.spotlight_cards[0].opportunity is not None
            assert dashboard.categories
            assert any(category.key == "top_research" for category in dashboard.categories)
            aradel = next(item for item in dashboard.ranked if item.symbol == "ARADEL")
            assert aradel.answer.startswith("YES")
            assert aradel.confidence in {"Medium", "High", "Very High"}
            assert aradel.valuation_label is not None
            assert aradel.peer_rank in {1, 2}
            assert aradel.why_attention
            assert aradel.main_risk
            assert aradel.next_action
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_live_insights_create_price_news_performance_and_risk_cards():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_decision_card_data(session)

            run_intelligence_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            insights = company_live_insights(session, "ARADEL")

            assert insights.price.latest_price == Decimal("1526.8000")
            assert insights.price.price_change_percent is not None
            assert insights.price.direction == "up"
            assert insights.performance.windows
            assert insights.performance.sector_rank_1m in {1, 2}
            assert {card.key for card in insights.cards} >= {
                "performance",
                "news",
                "risks",
                "decision_context",
            }
            risk_card = next(card for card in insights.cards if card.key == "risks")
            news_card = next(card for card in insights.cards if card.key == "news")
            assert any("capital" in point.lower() for point in risk_card.points)
            assert news_card.source_count >= 2
            assert insights.recent_news
            assert insights.recent_disclosures
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _seed_decision_card_data(session: Session) -> None:
    source = SourceDocument(
        name="NGX Pulse API 2026-08-03",
        document_type="ngxpulse_market_data",
        url="https://www.ngxpulse.ng/api",
    )
    company = Company(symbol="ARADEL", name="Aradel Holdings Plc", sector="OIL AND GAS")
    peer = Company(symbol="SEPLAT", name="Seplat Energy Plc", sector="OIL AND GAS")
    session.add_all([source, company, peer])
    session.flush()

    _prices(session, company.id, source.id, Decimal(1400), Decimal("1526.80"), 2_300_000)
    _prices(session, peer.id, source.id, Decimal(5600), Decimal("5800.00"), 1_500_000)

    session.add_all(
        [
            FinancialStatement(
                company_id=company.id,
                period_end=date(2026, 6, 30),
                period_type="Q2",
                revenue=Decimal(900000),
                profit_after_tax=Decimal(240000),
                total_assets=Decimal(2000000),
                total_liabilities=Decimal(800000),
                total_equity=Decimal(1200000),
                cash_flow_operations=Decimal(290000),
                eps=Decimal(221),
                source_document_id=source.id,
                reviewed=True,
            ),
            FinancialStatement(
                company_id=company.id,
                period_end=date(2025, 6, 30),
                period_type="Q2",
                revenue=Decimal(700000),
                profit_after_tax=Decimal(150000),
                total_assets=Decimal(1800000),
                total_liabilities=Decimal(700000),
                total_equity=Decimal(1100000),
                cash_flow_operations=Decimal(155000),
                eps=Decimal(160),
                source_document_id=source.id,
                reviewed=True,
            ),
            FinancialStatement(
                company_id=peer.id,
                period_end=date(2026, 6, 30),
                period_type="Q2",
                revenue=Decimal(1200000),
                profit_after_tax=Decimal(100000),
                total_assets=Decimal(4000000),
                total_liabilities=Decimal(2200000),
                total_equity=Decimal(1800000),
                cash_flow_operations=Decimal(120000),
                eps=Decimal(450),
                source_document_id=source.id,
                reviewed=True,
            ),
        ]
    )
    session.add_all(
        [
            NgxPulseFundamental(
                company_id=company.id,
                as_of_date=date(2026, 8, 3),
                pe_ratio=Decimal("6.90"),
                eps=Decimal("221.00"),
                roe=Decimal("29.00"),
                profit_margin=Decimal("34.00"),
                debt_equity=Decimal("0.45"),
                dividend_yield=Decimal("5.10"),
                raw_payload={"symbol": "ARADEL", "market_cap": "3000000000000"},
                source_document_id=source.id,
            ),
            NgxPulseFundamental(
                company_id=company.id,
                as_of_date=date(2025, 8, 3),
                pe_ratio=Decimal("8.50"),
                eps=Decimal("160.00"),
                roe=Decimal("24.00"),
                profit_margin=Decimal("29.00"),
                debt_equity=Decimal("0.60"),
                dividend_yield=Decimal("4.80"),
                raw_payload={"symbol": "ARADEL"},
                source_document_id=source.id,
            ),
            NgxPulseFundamental(
                company_id=peer.id,
                as_of_date=date(2026, 8, 3),
                pe_ratio=Decimal("12.40"),
                eps=Decimal("450.00"),
                roe=Decimal("11.00"),
                profit_margin=Decimal("8.00"),
                debt_equity=Decimal("1.20"),
                dividend_yield=Decimal("2.00"),
                raw_payload={"symbol": "SEPLAT"},
                source_document_id=source.id,
            ),
        ]
    )
    for year, amount in ((2026, Decimal(35)), (2025, Decimal(30)), (2024, Decimal(26))):
        session.add(
            Dividend(
                company_id=company.id,
                declared_date=date(year, 3, 1),
                payment_date=date(year, 4, 1),
                amount_per_share=amount,
                source_document_id=source.id,
                reviewed=True,
            )
        )
    session.add_all(
        [
            CorporateDisclosure(
                company_id=company.id,
                symbol="ARADEL",
                title="Aradel announces capital raise for expansion programme",
                disclosure_type="corporate action",
                published_at=datetime(2026, 8, 2, 10, 0, 0),
                url="https://example.com/aradel-capital-raise",
                raw_payload={"symbol": "ARADEL"},
                source_document_id=source.id,
            ),
            MarketNewsItem(
                title="Aradel gains as investors price in stronger oil and gas earnings",
                source_name="NGX Pulse",
                published_at=datetime(2026, 8, 2, 12, 0, 0),
                url="https://example.com/aradel-news",
                summary="The company remains watched after its capital raise and expansion plans.",
                raw_payload={"title": "Aradel gains"},
                source_document_id=source.id,
            ),
        ]
    )
    session.commit()


def _prices(
    session: Session,
    company_id: int,
    source_id: int,
    start: Decimal,
    latest: Decimal,
    volume: int,
) -> None:
    base_date = date(2026, 8, 3)
    for index in range(35):
        price = start + ((latest - start) * Decimal(index) / Decimal(34))
        session.add(
            Price(
                company_id=company_id,
                trade_date=base_date - timedelta(days=34 - index),
                close_price=price.quantize(Decimal("0.0001")),
                volume=volume,
                source_document_id=source_id,
                reviewed=True,
            )
        )
