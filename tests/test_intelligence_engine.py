from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ngx_research.database import Base
from ngx_research.models import Company, Dividend, NgxPulseFundamental, Price, SourceDocument
from ngx_research.services.intelligence_engine import (
    company_memory,
    latest_intelligence_opportunities,
    run_intelligence_engine,
)


def test_intelligence_engine_creates_evidence_backed_opportunities():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_intelligence_data(session)

            result = run_intelligence_engine(session, as_of_date=date(2026, 8, 3), limit=10)
            opportunities = {item.symbol: item for item in latest_intelligence_opportunities(session, limit=10)}

            aradel = opportunities["ARADEL"]
            penny = opportunities["TANTALIZER"]
            memory = company_memory(session, "ARADEL")
            assert result.generated == 2
            assert aradel.final_label in {"Top Research Candidate", "Research Now", "Dividend Candidate"}
            assert "Quality compounder" in aradel.stock_types
            assert "Sector specific stock: OIL AND GAS" in aradel.stock_types
            assert aradel.scores.overall > Decimal(60)
            assert aradel.memory.fundamentals_records == 2
            assert memory.price_records >= 30
            assert penny.final_label in {"Speculative", "Avoid for Now", "Needs Data"}
            assert "Penny/speculative stock" in penny.stock_types
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _seed_intelligence_data(session: Session) -> None:
    source = SourceDocument(
        name="NGX Pulse API 2026-08-03",
        document_type="ngxpulse_market_data",
        url="https://www.ngxpulse.ng/api",
    )
    aradel = Company(symbol="ARADEL", name="Aradel Holdings Plc", sector="OIL AND GAS")
    tant = Company(symbol="TANTALIZER", name="Tantalizers Plc", sector="SERVICES")
    session.add_all([source, aradel, tant])
    session.flush()
    _prices(session, aradel.id, source.id, Decimal(1500), Decimal("1526.8"), 2_000_000)
    _prices(session, tant.id, source.id, Decimal("5.2"), Decimal("4.65"), 25_000)
    session.add_all(
        [
            NgxPulseFundamental(
                company_id=aradel.id,
                as_of_date=date(2026, 8, 3),
                pe_ratio=Decimal("6.90"),
                eps=Decimal("221.00"),
                roe=Decimal("29.00"),
                profit_margin=Decimal("34.00"),
                debt_equity=Decimal("0.45"),
                dividend_yield=Decimal("5.10"),
                raw_payload={"symbol": "ARADEL"},
                source_document_id=source.id,
            ),
            NgxPulseFundamental(
                company_id=aradel.id,
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
                company_id=tant.id,
                as_of_date=date(2026, 8, 3),
                pe_ratio=None,
                eps=Decimal("-0.10"),
                roe=Decimal("-4.00"),
                profit_margin=Decimal("-3.00"),
                raw_payload={"symbol": "TANTALIZER"},
                source_document_id=source.id,
            ),
        ]
    )
    for year, amount in ((2026, Decimal(35)), (2025, Decimal(30)), (2024, Decimal(26))):
        session.add(
            Dividend(
                company_id=aradel.id,
                declared_date=date(year, 3, 1),
                payment_date=date(year, 4, 1),
                amount_per_share=amount,
                source_document_id=source.id,
                reviewed=True,
            )
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
