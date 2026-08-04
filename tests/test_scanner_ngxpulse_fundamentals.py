from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ngx_research.database import Base
from ngx_research.models import Company, NgxPulseFundamental, Price, SourceDocument
from ngx_research.services.investment_rules import investment_rules
from ngx_research.services.scanner import run_market_scan


def test_market_scan_uses_ngxpulse_fundamentals_when_uploaded_statement_is_missing():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as session:
            _seed_provider_fundamentals(session)

            summary = run_market_scan(session, as_of_date=date(2026, 8, 3))

            company = session.query(Company).filter_by(symbol="ARADEL").one()
            rules = investment_rules(session, company)
            assert summary.insufficient_data == 0
            assert rules.checklist[0].passed is True
            assert "Insufficient data" not in rules.data_warnings
            assert "high ROE" in rules.fundamental_style
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _seed_provider_fundamentals(session: Session) -> None:
    company = Company(symbol="ARADEL", name="Aradel Holdings Plc", sector="OIL AND GAS")
    source = SourceDocument(
        name="NGX Pulse API 2026-07-31",
        document_type="ngxpulse_market_data",
        url="https://www.ngxpulse.ng/api/ngxdata/fundamentals",
    )
    session.add_all([company, source])
    session.flush()
    session.add_all(
        [
            Price(
                company_id=company.id,
                trade_date=date(2026, 7, 30),
                close_price=Decimal("1526.8000"),
                volume=1_000_000,
                source_document_id=source.id,
                reviewed=True,
            ),
            Price(
                company_id=company.id,
                trade_date=date(2026, 7, 31),
                close_price=Decimal("1526.8000"),
                volume=2_322_504,
                source_document_id=source.id,
                reviewed=True,
            ),
            NgxPulseFundamental(
                company_id=company.id,
                as_of_date=date(2026, 7, 31),
                eps=Decimal("220.0000"),
                pe_ratio=Decimal("6.9400"),
                roe=Decimal("28.5000"),
                profit_margin=Decimal("31.2000"),
                debt_equity=Decimal("0.4500"),
                dividend_yield=Decimal("3.5000"),
                raw_payload={"symbol": "ARADEL"},
                source_document_id=source.id,
            ),
        ]
    )
    session.commit()
