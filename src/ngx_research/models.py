from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ngx_research.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True)
    market_board: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    prices: Mapped[list["Price"]] = relationship(back_populates="company")
    financial_statements: Mapped[list["FinancialStatement"]] = relationship(back_populates="company")
    dividends: Mapped[list["Dividend"]] = relationship(back_populates="company")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String(80), default="free", index=True)
    role: Mapped[str] = mapped_column(String(80), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)


class AuthToken(Base, TimestampMixin):
    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    investor_goal: Mapped[str | None] = mapped_column(String(120), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(120), nullable=True)
    capital_range: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preferred_sectors: Mapped[list[str]] = mapped_column(JSON, default=list)
    onboarding_completed: Mapped[bool] = mapped_column(default=False, index=True)


class UserWatchlist(Base, TimestampMixin):
    __tablename__ = "user_watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_watchlist_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Starter Watchlist")


class UserWatchlistItem(Base, TimestampMixin):
    __tablename__ = "user_watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_watchlist_id", "symbol", name="uq_user_watchlist_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_watchlist_id: Mapped[int] = mapped_column(ForeignKey("user_watchlists.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)


class UserJournalEntry(Base, TimestampMixin):
    __tablename__ = "user_journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    thesis: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(String(120))
    horizon: Mapped[str] = mapped_column(String(120))
    target_entry: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(80), default="Watching", index=True)


class UserPortfolioPlan(Base, TimestampMixin):
    __tablename__ = "user_portfolio_plans"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_portfolio_plan_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Default Plan")


class UserPortfolioPlanItem(Base, TimestampMixin):
    __tablename__ = "user_portfolio_plan_items"
    __table_args__ = (
        UniqueConstraint("user_portfolio_plan_id", "symbol", name="uq_user_portfolio_plan_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_portfolio_plan_id: Mapped[int] = mapped_column(
        ForeignKey("user_portfolio_plans.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    planned_amount: Mapped[Decimal] = mapped_column(Numeric(24, 4), default=0)


class UserPortfolioTransaction(Base, TimestampMixin):
    __tablename__ = "user_portfolio_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    transaction_type: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    price_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    cash_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceDocument(Base, TimestampMixin):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class UploadedReport(Base, TimestampMixin):
    __tablename__ = "uploaded_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_size: Mapped[int] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(80), default="uploaded", index=True)


class ExtractionDraft(Base, TimestampMixin):
    __tablename__ = "extraction_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id"), nullable=True, index=True
    )
    uploaded_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploaded_reports.id"), nullable=True, index=True
    )
    extraction_type: Mapped[str] = mapped_column(String(80), default="financial_statement")
    provider: Mapped[str] = mapped_column(String(80), default="deepseek")
    model: Mapped[str] = mapped_column(String(120))
    prompt_text: Mapped[str] = mapped_column(Text)
    raw_response: Mapped[str] = mapped_column(Text)
    parsed_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(80), default="draft", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReportTextExtraction(Base, TimestampMixin):
    __tablename__ = "report_text_extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_report_id: Mapped[int] = mapped_column(ForeignKey("uploaded_reports.id"), index=True)
    extraction_method: Mapped[str] = mapped_column(String(80), default="pypdf")
    page_count: Mapped[int] = mapped_column(default=0)
    character_count: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(80), default="extracted", index=True)
    warnings: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataReviewLog(Base, TimestampMixin):
    __tablename__ = "data_review_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[str] = mapped_column(String(80), index=True)
    record_id: Mapped[int] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Price(Base, TimestampMixin):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("company_id", "trade_date", name="uq_price_company_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    open_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    high_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    low_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    volume: Mapped[int | None] = mapped_column(nullable=True)
    value_traded: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    reviewed: Mapped[bool] = mapped_column(default=False)

    company: Mapped[Company] = relationship(back_populates="prices")


class FinancialStatement(Base, TimestampMixin):
    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("company_id", "period_end", "period_type", name="uq_financial_company_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    period_type: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    profit_after_tax: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    total_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    total_equity: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    cash_flow_operations: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    reviewed: Mapped[bool] = mapped_column(default=False)

    company: Mapped[Company] = relationship(back_populates="financial_statements")


class Dividend(Base, TimestampMixin):
    __tablename__ = "dividends"
    __table_args__ = (
        UniqueConstraint("company_id", "declared_date", "amount_per_share", name="uq_dividend_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    declared_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ex_dividend_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_per_share: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    reviewed: Mapped[bool] = mapped_column(default=False)

    company: Mapped[Company] = relationship(back_populates="dividends")


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class WatchlistItem(Base, TimestampMixin):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "company_id", name="uq_watchlist_company"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)


class PortfolioHolding(Base, TimestampMixin):
    __tablename__ = "portfolio_holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    average_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PortfolioTransaction(Base, TimestampMixin):
    __tablename__ = "portfolio_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    transaction_type: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    price_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    cash_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class InvestmentNote(Base, TimestampMixin):
    __tablename__ = "investment_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    note_date: Mapped[date] = mapped_column(Date, default=date.today)
    thesis: Mapped[str] = mapped_column(Text)
    risks: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(80), nullable=True)


class InvestmentGoal(Base, TimestampMixin):
    __tablename__ = "investment_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    goal_type: Mapped[str] = mapped_column(String(80), index=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    target_return_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    target_dividend_yield: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    sell_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(80), default="active", index=True)


class AlertRule(Base, TimestampMixin):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    rule_type: Mapped[str] = mapped_column(String(80), index=True)
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    text_value: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AlertEvent(Base, TimestampMixin):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    alert_date: Mapped[date] = mapped_column(Date, index=True)
    rule_type: Mapped[str] = mapped_column(String(80), index=True)
    observed_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    observed_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(80), default="open", index=True)


class CompanyRatio(Base, TimestampMixin):
    __tablename__ = "company_ratios"
    __table_args__ = (UniqueConstraint("company_id", "as_of_date", name="uq_ratio_company_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    roe: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    cash_flow_to_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    revenue_growth: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    profit_growth: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    dividend_yield: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    data_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)


class NgxPulseFundamental(Base, TimestampMixin):
    __tablename__ = "ngxpulse_fundamentals"
    __table_args__ = (UniqueConstraint("company_id", "as_of_date", name="uq_ngxpulse_fundamental_company_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    pe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    forward_pe: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    dividend_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    dividend_yield: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    beta: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    roa: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    roe: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pb_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    debt_equity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    profit_margin: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class CorporateDisclosure(Base, TimestampMixin):
    __tablename__ = "corporate_disclosures"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    disclosure_type: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class MarketIndexSnapshot(Base, TimestampMixin):
    __tablename__ = "market_index_snapshots"
    __table_args__ = (UniqueConstraint("code", "as_of_date", name="uq_market_index_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    slug: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    change_percentage: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class MarketStatusSnapshot(Base, TimestampMixin):
    __tablename__ = "market_status_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(80), index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class EtfSnapshot(Base, TimestampMixin):
    __tablename__ = "etf_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_etf_symbol_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    canonical_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    slug: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    change_percentage: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    value_traded: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class BondSnapshot(Base, TimestampMixin):
    __tablename__ = "bond_snapshots"
    __table_args__ = (UniqueConstraint("ticker", "as_of_date", name="uq_bond_ticker_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    issuer_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    bond_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    coupon_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    clean_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class BondAuctionSnapshot(Base, TimestampMixin):
    __tablename__ = "bond_auction_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "auction_date",
            "instrument_type",
            "tenor_label",
            name="uq_bond_auction_date_instrument_tenor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    auction_date: Mapped[date] = mapped_column(Date, index=True)
    instrument_type: Mapped[str] = mapped_column(String(80), index=True)
    tenor_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tenor_label: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    stop_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    offered_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    allotted_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    subscription_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class NasdOtcStockSnapshot(Base, TimestampMixin):
    __tablename__ = "nasd_otc_stock_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "as_of_date", name="uq_nasd_otc_symbol_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    change_percentage: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class MarketNewsItem(Base, TimestampMixin):
    __tablename__ = "market_news_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


class CompanyScore(Base, TimestampMixin):
    __tablename__ = "company_scores"
    __table_args__ = (UniqueConstraint("company_id", "as_of_date", name="uq_score_company_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    ratio_id: Mapped[int | None] = mapped_column(ForeignKey("company_ratios.id"), nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    quality_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    growth_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    valuation_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    dividend_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), index=True)
    status: Mapped[str] = mapped_column(String(80), index=True)
    reasons: Mapped[str] = mapped_column(Text)
    risks: Mapped[str] = mapped_column(Text)


class ScanRun(Base, TimestampMixin):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    universe: Mapped[str] = mapped_column(String(120), default="all")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanResult(Base, TimestampMixin):
    __tablename__ = "scan_results"
    __table_args__ = (UniqueConstraint("scan_run_id", "company_id", name="uq_scan_company"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    score_id: Mapped[int] = mapped_column(ForeignKey("company_scores.id"), index=True)
    rank: Mapped[int] = mapped_column(index=True)


class CompanyIntelligenceSnapshot(Base, TimestampMixin):
    __tablename__ = "company_intelligence_snapshots"
    __table_args__ = (
        UniqueConstraint("company_id", "as_of_date", name="uq_company_intelligence_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    final_label: Mapped[str] = mapped_column(String(80), index=True)
    stock_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    business_quality_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    growth_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    valuation_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    dividend_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    financial_risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    momentum_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    liquidity_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    data_confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), index=True)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_data: Mapped[list[str]] = mapped_column(JSON, default=list)
    next_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    decision_change_triggers: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    source_summary: Mapped[dict] = mapped_column(JSON, default=dict)


class CompanyValuationSnapshot(Base, TimestampMixin):
    __tablename__ = "company_valuation_snapshots"
    __table_args__ = (
        UniqueConstraint("company_id", "as_of_date", name="uq_company_valuation_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    intelligence_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_intelligence_snapshots.id"), nullable=True, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    latest_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    latest_price_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fair_value_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fair_value_mid: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fair_value_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    margin_of_safety_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    expected_return_low_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    expected_return_high_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    valuation_label: Mapped[str] = mapped_column(String(80), index=True)
    valuation_confidence: Mapped[str] = mapped_column(String(80), index=True)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    methods: Mapped[list[dict]] = mapped_column(JSON, default=list)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_data: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    source_summary: Mapped[dict] = mapped_column(JSON, default=dict)


class CompanyPeerComparisonSnapshot(Base, TimestampMixin):
    __tablename__ = "company_peer_comparison_snapshots"
    __table_args__ = (
        UniqueConstraint("company_id", "as_of_date", name="uq_company_peer_comparison_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    intelligence_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_intelligence_snapshots.id"), nullable=True, index=True
    )
    valuation_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_valuation_snapshots.id"), nullable=True, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    peer_count: Mapped[int] = mapped_column(default=0)
    sector_rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sector_percentile: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    comparison_label: Mapped[str] = mapped_column(String(80), index=True)
    best_overall_peer_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    best_overall_peer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_winners: Mapped[list[dict]] = mapped_column(JSON, default=list)
    metric_comparisons: Mapped[list[dict]] = mapped_column(JSON, default=list)
    peer_rows: Mapped[list[dict]] = mapped_column(JSON, default=list)
    strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    next_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    source_summary: Mapped[dict] = mapped_column(JSON, default=dict)
