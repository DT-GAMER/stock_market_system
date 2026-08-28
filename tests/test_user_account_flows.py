from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import anyio
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ngx_research.database import Base
from ngx_research.main import (
    apply_extraction_draft,
    create_extraction_draft_from_report,
    create_extraction_draft_from_text,
    create_gpt_extraction_draft_from_report,
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
    Dividend,
    ExtractionDraft,
    FinancialStatement,
    Price,
    ReportTextExtraction,
    SourceDocument,
    UploadedReport,
)
from ngx_research.schemas import (
    ExtractionDraftCreate,
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


def test_report_draft_endpoint_reuses_linked_manual_draft(monkeypatch, session: Session, tmp_path) -> None:
    company = seed_company(session, "SEPLAT", "Seplat Energy Plc")
    source = SourceDocument(name="SEPLAT 2017 Annual Report", document_type="financial_report")
    session.add(source)
    session.flush()
    report = UploadedReport(
        source_document_id=source.id,
        company_id=company.id,
        original_filename="seplat-2017.pdf",
        stored_path=str(tmp_path / "seplat-2017.pdf"),
        content_type="application/pdf",
        file_size=3,
        sha256="b" * 64,
        status="uploaded",
    )
    session.add(report)
    session.commit()

    async def fake_extract_financial_statement(report_text: str):
        assert "Consolidated statement" in report_text
        return (
            "{}",
            {
                "symbol": "SEPLAT",
                "period_end": "2017-12-31",
                "period_type": "FY",
                "currency": "NGN",
                "scale": "millions",
                "revenue": 138281,
                "profit_after_tax": 81111,
                "total_assets": 799553,
                "total_liabilities": 339907,
                "total_equity": 459646,
                "cash_flow_operations": 118414,
                "eps": 143.96,
                "confidence": 100,
                "warnings": [],
                "summary": "Manual statement extraction.",
            },
        )

    monkeypatch.setattr(
        "ngx_research.main.extract_financial_statement",
        fake_extract_financial_statement,
    )

    manual_draft = anyio.run(
        create_extraction_draft_from_text,
        ExtractionDraftCreate(
            symbol="SEPLAT",
            source_document_id=source.id,
            uploaded_report_id=report.id,
            report_text="Consolidated statement of profit or loss\nRevenue 138,281",
            notes="Manual financial text pasted from annual report.",
        ),
        session,
    )
    reused_draft = anyio.run(create_extraction_draft_from_report, report.id, session)

    assert reused_draft.id == manual_draft.id
    assert reused_draft.parsed_data["revenue"] == 138281
    assert session.get(UploadedReport, report.id).status == "manual_draft_created"


def test_gpt_report_draft_endpoint_creates_openai_draft(monkeypatch, session: Session, tmp_path) -> None:
    company = seed_company(session, "SEPLAT", "Seplat Energy Plc")
    source = SourceDocument(name="SEPLAT 2017 Annual Report", document_type="financial_report")
    session.add(source)
    session.flush()
    stored_file = tmp_path / "seplat-2017.pdf"
    stored_file.write_bytes(b"%PDF-1.7")
    report = UploadedReport(
        source_document_id=source.id,
        company_id=company.id,
        original_filename="seplat-2017.pdf",
        stored_path=str(stored_file),
        content_type="application/pdf",
        file_size=8,
        sha256="d" * 64,
        status="uploaded",
    )
    session.add(report)
    session.commit()

    async def fake_extract_financial_statement_from_pdf(
        pdf_path: str,
        filename: str,
        company_symbol: str | None = None,
        company_name: str | None = None,
    ):
        assert pdf_path == str(stored_file)
        assert filename == "seplat-2017.pdf"
        assert company_symbol == "SEPLAT"
        assert company_name == "Seplat Energy Plc"
        return (
            "{}",
            {
                "period_end": "2017-12-31",
                "period_type": "FY",
                "currency": "NGN",
                "scale": "millions",
                "revenue": 138281,
                "profit_after_tax": 81111,
                "total_assets": 799553,
                "total_liabilities": 339907,
                "total_equity": 459646,
                "cash_flow_operations": 118414,
                "eps": 143.96,
                "dividend_per_share": 2.5,
                "profit_before_tax": 91600,
                "cash_and_cash_equivalents": 180000,
                "finance_cost": 12000,
                "major_risks": ["commodity price risk"],
                "business_summary": "Integrated Nigerian energy company.",
                "auditor_opinion": "Unqualified opinion.",
            },
        )

    monkeypatch.setattr(
        "ngx_research.main.extract_financial_statement_from_pdf",
        fake_extract_financial_statement_from_pdf,
    )

    draft = anyio.run(create_gpt_extraction_draft_from_report, report.id, session)

    assert draft.provider == "openai"
    assert draft.model == "gpt-5.6-luna"
    assert draft.uploaded_report_id == report.id
    assert draft.parsed_data["symbol"] == "SEPLAT"
    assert draft.parsed_data["profit_before_tax"] == 91600
    assert session.get(UploadedReport, report.id).status == "gpt_draft_created"

    result = apply_extraction_draft(draft.id, session)
    statement = session.get(FinancialStatement, result.financial_statement_id)

    assert statement is not None
    assert statement.interest_expense == Decimal("12000.0000")
    assert statement.business_summary == "Integrated Nigerian energy company."


def test_manual_draft_without_pdf_creates_manual_source(monkeypatch, session: Session) -> None:
    seed_company(session, "GTCO", "Guaranty Trust Holding Company Plc")

    async def fake_extract_financial_statement(report_text: str):
        assert "Gross earnings" in report_text
        return (
            "{}",
            {
                "symbol": "GTCO",
                "period_end": "2025-12-31",
                "period_type": "FY",
                "currency": "NGN",
                "scale": "millions",
                "revenue": 2500000,
                "profit_after_tax": 700000,
                "dividend_per_share": 8.03,
                "dividend_currency": "NGN",
                "confidence": 90,
                "warnings": [],
                "summary": "Standalone manual statement extraction.",
            },
        )

    monkeypatch.setattr(
        "ngx_research.main.extract_financial_statement",
        fake_extract_financial_statement,
    )

    draft = anyio.run(
        create_extraction_draft_from_text,
        ExtractionDraftCreate(
            symbol="GTCO",
            source_name="GTCO annual report",
            report_year=2025,
            report_text="Gross earnings 2,500,000\nProfit after tax 700,000",
            notes="Copied from official annual report PDF.",
        ),
        session,
    )
    source = session.get(SourceDocument, draft.source_document_id)

    assert draft.uploaded_report_id is None
    assert source is not None
    assert source.name == "GTCO annual report 2025"
    assert source.document_type == "manual_financial_text"
    assert "Financial year: 2025" in source.notes

    result = apply_extraction_draft(draft.id, session)
    dividend = session.get(Dividend, result.dividend_ids[0])

    assert result.financial_statement_id
    assert len(result.dividend_ids) == 1
    assert dividend is not None
    assert dividend.amount_per_share == Decimal("8.0300")
    assert dividend.currency == "NGN"
    assert dividend.payment_date == date(2025, 12, 31)
    assert dividend.reviewed is False


def test_manual_bank_draft_applies_bank_specific_metrics(monkeypatch, session: Session) -> None:
    seed_company(session, "ZENITHBANK", "Zenith Bank Plc")

    async def fake_extract_financial_statement(report_text: str):
        assert "Non-performing loan" in report_text
        return (
            "{}",
            {
                "symbol": "ZENITHBANK",
                "period_end": "2016-12-31",
                "period_type": "FY",
                "currency": "NGN",
                "scale": "millions",
                "statement_kind": "bank",
                "revenue": 507997,
                "gross_earnings": 507997,
                "interest_income": 384557,
                "net_interest_income": 240179,
                "profit_after_tax": 129652,
                "total_assets": 4739825,
                "total_liabilities": 4035360,
                "total_equity": 704465,
                "cash_flow_operations": -1660,
                "eps": 4.12,
                "customer_deposits": 2983621,
                "loans_and_advances": 2289365,
                "borrowings_total": 767227,
                "interest_expense": 144378,
                "npl_ratio": 3.02,
                "capital_adequacy_ratio": 23,
                "loan_to_deposit_ratio": 76.7,
                "dividend_per_share": 2.02,
                "dividend_currency": "NGN",
                "major_risks": [
                    "oil price/FX scarcity impact on loan impairment",
                    "NPL ratio rising",
                ],
                "business_summary": "Large Nigerian bank with resilient recession-year earnings.",
                "auditor_name": "KPMG Professional Services",
                "auditor_opinion": "Unqualified opinion; KAMs: loan impairment and derivatives.",
                "corporate_actions": ["Final dividend proposed"],
                "confidence": 95,
                "warnings": [],
                "summary": "Zenith Bank FY2016 bank extraction.",
            },
        )

    monkeypatch.setattr(
        "ngx_research.main.extract_financial_statement",
        fake_extract_financial_statement,
    )

    draft = anyio.run(
        create_extraction_draft_from_text,
        ExtractionDraftCreate(
            symbol="ZENITHBANK",
            source_name="Zenith Bank annual report",
            report_year=2016,
            report_text="Non-performing loan ratio 3.02%. Capital adequacy ratio 23%.",
        ),
        session,
    )
    result = apply_extraction_draft(draft.id, session)
    statement = session.get(FinancialStatement, result.financial_statement_id)
    dividend = session.get(Dividend, result.dividend_ids[0])

    assert statement is not None
    assert statement.statement_kind == "bank"
    assert statement.gross_earnings == Decimal("507997.0000")
    assert statement.customer_deposits == Decimal("2983621.0000")
    assert statement.loans_and_advances == Decimal("2289365.0000")
    assert statement.npl_ratio == Decimal("3.0200")
    assert statement.capital_adequacy_ratio == Decimal("23.0000")
    assert statement.business_summary == "Large Nigerian bank with resilient recession-year earnings."
    assert statement.auditor_name == "KPMG Professional Services"
    assert statement.major_risks == [
        "oil price/FX scarcity impact on loan impairment",
        "NPL ratio rising",
    ]
    assert dividend is not None
    assert dividend.amount_per_share == Decimal("2.0200")
    assert dividend.currency == "NGN"


def test_manual_draft_without_pdf_requires_source_name_and_year(session: Session) -> None:
    seed_company(session, "GTCO", "Guaranty Trust Holding Company Plc")

    with pytest.raises(HTTPException) as missing_name:
        anyio.run(
            create_extraction_draft_from_text,
            ExtractionDraftCreate(
                symbol="GTCO",
                report_year=2025,
                report_text="Gross earnings 2,500,000",
            ),
            session,
        )

    with pytest.raises(HTTPException) as missing_year:
        anyio.run(
            create_extraction_draft_from_text,
            ExtractionDraftCreate(
                symbol="GTCO",
                source_name="GTCO annual report",
                report_text="Gross earnings 2,500,000",
            ),
            session,
        )

    assert missing_name.value.status_code == 400
    assert "source_name is required" in missing_name.value.detail
    assert missing_year.value.status_code == 400
    assert "report_year is required" in missing_year.value.detail


def test_report_draft_endpoint_explains_missing_report_text(session: Session, tmp_path) -> None:
    company = seed_company(session, "GTCO", "Guaranty Trust Holding Company Plc")
    source = SourceDocument(name="GTCO Annual Report", document_type="financial_report")
    session.add(source)
    session.flush()
    report = UploadedReport(
        source_document_id=source.id,
        company_id=company.id,
        original_filename="gtco.pdf",
        stored_path=str(tmp_path / "gtco.pdf"),
        content_type="application/pdf",
        file_size=3,
        sha256="c" * 64,
        status="uploaded",
    )
    session.add(report)
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(create_extraction_draft_from_report, report.id, session)

    assert exc_info.value.status_code == 400
    assert "Extract PDF text first" in exc_info.value.detail
    assert "Manual financial text" in exc_info.value.detail


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
