from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ngx_research.database import Base
from ngx_research.main import (
    create_my_journal_entry,
    create_my_portfolio_transaction,
    delete_report,
    my_journal,
    my_portfolio_plan,
    my_portfolio_summary,
    my_portfolio_transactions,
    my_profile,
    my_watchlist,
    save_my_portfolio_plan,
    save_my_profile,
    save_my_watchlist,
)
from ngx_research.models import (
    Company,
    ExtractionDraft,
    Price,
    ReportTextExtraction,
    SourceDocument,
    UploadedReport,
)
from ngx_research.schemas import (
    PortfolioTransactionCreate,
    UserJournalEntryCreate,
    UserPortfolioPlanItemUpsert,
    UserPortfolioPlanUpsert,
    UserProfileUpsert,
    UserRead,
    UserWatchlistUpsert,
)
from ngx_research.services.auth import create_user


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


def test_user_research_data_is_persisted_and_isolated(session: Session) -> None:
    seed_company(session, "GTCO", "Guaranty Trust Holding Company Plc")
    user_a = create_test_user(session, "ada@example.com")
    user_b = create_test_user(session, "bayo@example.com")

    profile = save_my_profile(
        UserProfileUpsert(
            investor_goal="capital_growth",
            experience_level="beginner",
            capital_range="100k-500k",
            preferred_sectors=["Banking"],
            onboarding_completed=True,
        ),
        session,
        user_a,
    )
    watchlist = save_my_watchlist(
        UserWatchlistUpsert(name="Starter Watchlist", symbols=["GTCO"]),
        session,
        user_a,
    )
    journal_entry = create_my_journal_entry(
        UserJournalEntryCreate(
            symbol="GTCO",
            thesis="Strong candidate under review.",
            goal="Capital growth",
            horizon="3 to 5 years",
            status="Watching",
        ),
        session,
        user_a,
    )
    plan = save_my_portfolio_plan(
        UserPortfolioPlanUpsert(
            name="Default Plan",
            items=[
                UserPortfolioPlanItemUpsert(symbol="GTCO", planned_amount=Decimal(100000))
            ],
        ),
        session,
        user_a,
    )

    assert profile.onboarding_completed is True
    assert watchlist.symbols == ["GTCO"]
    assert journal_entry.symbol == "GTCO"
    assert plan.items[0].planned_amount == Decimal("100000.0000")
    assert my_journal(session, user_a)[0].symbol == "GTCO"
    assert my_portfolio_plan(session, user_a).items[0].symbol == "GTCO"

    assert my_profile(session, user_b).onboarding_completed is False
    assert my_watchlist(session, user_b).symbols == []
    assert my_journal(session, user_b) == []
    assert my_portfolio_plan(session, user_b).items == []


def test_user_portfolio_summary_calculates_positions_and_warnings(session: Session) -> None:
    seed_company(session, "GTCO", "Guaranty Trust Holding Company Plc")
    seed_price(session, "GTCO", Decimal("150.00"))
    user = create_test_user(session, "portfolio@example.com")

    buy = create_my_portfolio_transaction(
        PortfolioTransactionCreate(
            symbol="GTCO",
            transaction_date=date(2026, 7, 29),
            transaction_type="BUY",
            quantity=Decimal(100),
            price_per_share=Decimal(100),
            fees=Decimal(50),
        ),
        session,
        user,
    )
    sell = create_my_portfolio_transaction(
        PortfolioTransactionCreate(
            symbol="GTCO",
            transaction_date=date(2026, 7, 30),
            transaction_type="SELL",
            quantity=Decimal(20),
            price_per_share=Decimal(120),
            fees=Decimal(10),
        ),
        session,
        user,
    )
    dividend = create_my_portfolio_transaction(
        PortfolioTransactionCreate(
            symbol="GTCO",
            transaction_date=date(2026, 8, 1),
            transaction_type="DIVIDEND",
            cash_amount=Decimal(500),
        ),
        session,
        user,
    )

    transactions = my_portfolio_transactions(session, user)
    summary = my_portfolio_summary(session, user)
    position = summary.positions[0]

    assert buy.symbol == "GTCO"
    assert sell.transaction_type == "SELL"
    assert dividend.cash_amount == Decimal(500)
    assert len(transactions) == 3
    assert position.symbol == "GTCO"
    assert position.quantity == Decimal("80.000000")
    assert position.cost_basis == Decimal("8040.0000")
    assert position.average_cost == Decimal("100.5000")
    assert position.market_value == Decimal("12000.0000")
    assert position.unrealized_gain_loss == Decimal("3960.0000")
    assert position.dividends_received == Decimal("500.0000")
    assert "GTCO is above 30% of portfolio value." in summary.warnings
    assert "Financial Services exposure is above 50% of portfolio value." in summary.warnings


def test_delete_report_removes_related_extraction_records(session: Session, tmp_path) -> None:
    company = seed_company(session, "SEPLAT", "Seplat Energy Plc")
    source = SourceDocument(name="SEPLAT 2017 Annual Report", document_type="financial_report")
    session.add(source)
    session.flush()
    stored_file = tmp_path / "seplat-2017.pdf"
    stored_file.write_text("pdf")
    report = UploadedReport(
        source_document_id=source.id,
        company_id=company.id,
        original_filename="seplat-2017.pdf",
        stored_path=str(stored_file),
        content_type="application/pdf",
        file_size=3,
        sha256="a" * 64,
        status="text_extracted",
    )
    session.add(report)
    session.flush()
    session.add(
        ReportTextExtraction(
            uploaded_report_id=report.id,
            extraction_method="pypdf",
            page_count=1,
            character_count=3,
            text="pdf",
        )
    )
    session.add(
        ExtractionDraft(
            uploaded_report_id=report.id,
            source_document_id=source.id,
            company_id=company.id,
            model="deepseek",
            prompt_text="prompt",
            raw_response="{}",
            parsed_data={},
        )
    )
    session.commit()

    result = delete_report(report.id, session)

    assert result == {"status": "deleted"}
    assert session.get(UploadedReport, report.id) is None
    assert session.get(SourceDocument, source.id) is None
    assert session.query(ReportTextExtraction).count() == 0
    assert session.query(ExtractionDraft).count() == 0
    assert not stored_file.exists()


def create_test_user(session: Session, email: str) -> UserRead:
    user, _, _ = create_user(
        session=session,
        email=email,
        password="Password123",
        full_name=email.split("@")[0],
    )
    return UserRead.model_validate(user)


def seed_company(
    session: Session,
    symbol: str,
    name: str,
    sector: str = "Financial Services",
) -> Company:
    company = Company(symbol=symbol, name=name, sector=sector, is_active=True)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def seed_price(session: Session, symbol: str, close_price: Decimal) -> None:
    company = session.query(Company).filter_by(symbol=symbol).one()
    session.add(
        Price(
            company_id=company.id,
            trade_date=date(2026, 7, 29),
            close_price=close_price,
            reviewed=True,
        )
    )
    session.commit()
