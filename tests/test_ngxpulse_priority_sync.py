from collections.abc import Iterator
from decimal import Decimal

import anyio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ngx_research.database import Base
from ngx_research.models import (
    BondAuctionSnapshot,
    BondSnapshot,
    Company,
    CompanyRatio,
    CorporateDisclosure,
    Dividend,
    EtfSnapshot,
    MarketIndexSnapshot,
    MarketNewsItem,
    NasdOtcStockSnapshot,
    NgxPulseFundamental,
)
from ngx_research.services import ngxpulse_client


@pytest.fixture()
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as current_session:
        yield current_session


def test_sync_fundamentals_stores_provider_data_and_updates_ratios(monkeypatch, session: Session):
    async def fake_request(path, params=None):
        assert path == "/api/ngxdata/fundamentals"
        return {
            "success": True,
            "fundamentals": [
                {
                    "symbol": "PRESCO",
                    "name": "Presco Plc",
                    "pe_ratio": 19.58,
                    "eps": 134.35,
                    "dividend_yield": 1.27,
                    "roe": 37.51,
                    "pb_ratio": 5.62,
                    "debt_equity": 0.29,
                    "profit_margin": 36.43,
                    "updated_at": "2026-07-12T05:04:51+00:00",
                    "extra": {"rsi": 61},
                }
            ],
        }

    monkeypatch.setattr(ngxpulse_client, "_request_json", fake_request)

    result = anyio.run(ngxpulse_client.sync_fundamentals, session)

    company = session.query(Company).filter_by(symbol="PRESCO").one()
    fundamental = session.query(NgxPulseFundamental).filter_by(company_id=company.id).one()
    ratio = session.query(CompanyRatio).filter_by(company_id=company.id).one()
    assert result.imported == 1
    assert fundamental.extra == {"rsi": 61}
    assert ratio.pe_ratio == fundamental.pe_ratio
    assert ratio.data_confidence == 100


def test_sync_dividend_history_imports_trusted_dividends(monkeypatch, session: Session):
    async def fake_request(path, params=None):
        assert path == "/api/ngxdata/dividends/GTCO"
        return {
            "dividends": [
                {
                    "declared_date": "2026-03-01",
                    "ex_dividend_date": "2026-03-15",
                    "payment_date": "2026-04-01",
                    "amount_per_share": 8.03,
                }
            ]
        }

    monkeypatch.setattr(ngxpulse_client, "_request_json", fake_request)

    result = anyio.run(ngxpulse_client.sync_dividend_history, session, "GTCO")

    dividend = session.query(Dividend).one()
    assert result.imported == 1
    assert dividend.reviewed is True
    assert dividend.amount_per_share == Decimal("8.0300")


def test_sync_disclosures_and_indices_store_snapshots(monkeypatch, session: Session):
    async def fake_request(path, params=None):
        if path == "/api/ngxdata/disclosures":
            return {
                "data": [
                    {
                        "symbol": "GTCO",
                        "title": "GTCO declares final dividend",
                        "type": "dividend",
                        "date": "2026-03-01T10:00:00Z",
                        "url": "https://example.com/gtco",
                    }
                ]
            }
        if path == "/api/ngxdata/indices":
            return {
                "data": [
                    {
                        "code": "ASI",
                        "slug": "asi",
                        "name": "NGX ALL SHARE INDEX",
                        "currentPrice": 251635.42,
                        "changePercentage": 0.57,
                        "currentDateTime": "2026-05-20T10:30:00Z",
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(ngxpulse_client, "_request_json", fake_request)

    disclosure_result = anyio.run(ngxpulse_client.sync_disclosures, session)
    index_result = anyio.run(ngxpulse_client.sync_indices, session)

    assert disclosure_result.imported == 1
    assert index_result.imported == 1
    assert session.query(CorporateDisclosure).one().symbol == "GTCO"
    assert session.query(MarketIndexSnapshot).one().code == "ASI"


def test_sync_priority_two_market_context_feeds(monkeypatch, session: Session):
    async def fake_request(path, params=None):
        if path == "/api/ngxdata/etfs":
            return {
                "data": [
                    {
                        "symbol": "NEWGOLD",
                        "canonical_symbol": "NEWGOLD",
                        "name": "NewGold ETF",
                        "issuer": "NewGold Issuer Limited",
                        "isin": "NGNEWGOLD001",
                        "close": 58000,
                        "change_percentage": 0.87,
                        "volume": 1200,
                        "value": 69600000,
                        "updated_at": "2026-05-20T10:30:00Z",
                    }
                ]
            }
        if path == "/api/ngxdata/bonds":
            return {
                "data": [
                    {
                        "ticker": "FG142037S2",
                        "name": "FGN 14% 2037 Series 2",
                        "issuer": "Federal Government of Nigeria",
                        "issuer_type": "sovereign",
                        "bond_type": "govt_local",
                        "currency": "NGN",
                        "coupon_rate": 14.29,
                        "maturity_date": "2037-04-18",
                        "clean_price": 130.0001,
                        "latest_quote_date": "2026-05-26",
                    }
                ]
            }
        if path == "/api/ngxdata/bonds/auctions":
            assert params == {"limit": 50}
            return {
                "data": [
                    {
                        "instrument_type": "tbill",
                        "tenor_days": 364,
                        "tenor_label": "364-day",
                        "auction_date": "2026-05-20",
                        "stop_rate": 16.149,
                        "offered_amount": 500000,
                        "allotted_amount": 683289.346,
                        "subscription_rate": 367.83,
                        "currency": "NGN",
                    }
                ]
            }
        if path == "/api/nasddata/stocks":
            return {
                "data": [
                    {
                        "symbol": "SDCSCSPLC",
                        "name": "Central Securities Clearing System Plc",
                        "current_price": 31.5,
                        "change_percent": 1.2,
                        "volume": 5000,
                        "market_cap": 157500000000,
                        "date": "2026-05-26",
                    }
                ]
            }
        if path == "/api/news":
            assert params == {"limit": 50}
            return {
                "data": [
                    {
                        "title": "NGX market breadth improves",
                        "source": "Nairametrics",
                        "published_at": "2026-05-26T08:00:00Z",
                        "url": "https://example.com/news",
                        "summary": "Market sentiment improved.",
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(ngxpulse_client, "_request_json", fake_request)

    etf_result = anyio.run(ngxpulse_client.sync_etfs, session)
    bond_result = anyio.run(ngxpulse_client.sync_bonds, session)
    auction_result = anyio.run(ngxpulse_client.sync_bond_auctions, session, 50)
    nasd_result = anyio.run(ngxpulse_client.sync_nasd_otc_stocks, session)
    news_result = anyio.run(ngxpulse_client.sync_market_news, session, 50)

    assert etf_result.imported == 1
    assert bond_result.imported == 1
    assert auction_result.imported == 1
    assert nasd_result.imported == 1
    assert news_result.imported == 1
    assert session.query(EtfSnapshot).one().symbol == "NEWGOLD"
    assert session.query(BondSnapshot).one().ticker == "FG142037S2"
    assert session.query(BondAuctionSnapshot).one().tenor_label == "364-day"
    assert session.query(NasdOtcStockSnapshot).one().symbol == "SDCSCSPLC"
    assert session.query(MarketNewsItem).one().source_name == "Nairametrics"
