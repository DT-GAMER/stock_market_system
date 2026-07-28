from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CompanyCreate(BaseModel):
    symbol: str
    name: str
    sector: str | None = None
    market_board: str | None = None


class CompanyRead(CompanyCreate):
    id: int
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class ImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


class ReviewAction(BaseModel):
    notes: str | None = None


class ReviewResult(BaseModel):
    record_type: str
    record_id: int
    action: str
    reviewed: bool


class ReviewLogRead(BaseModel):
    id: int
    record_type: str
    record_id: int
    action: str
    notes: str | None

    model_config = ConfigDict(from_attributes=True)


class PendingReviewItem(BaseModel):
    record_type: str
    record_id: int
    symbol: str
    summary: str
    source_name: str | None = None
    source_url: str | None = None


class SourceDocumentCreate(BaseModel):
    name: str
    url: str | None = None
    document_type: str
    notes: str | None = None


class SourceDocumentRead(BaseModel):
    id: int
    name: str
    url: str | None
    document_type: str
    notes: str | None

    model_config = ConfigDict(from_attributes=True)


class UploadedReportRead(BaseModel):
    id: int
    source_document_id: int
    company_id: int | None
    original_filename: str
    stored_path: str
    content_type: str | None
    file_size: int
    sha256: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class ExtractionDraftCreate(BaseModel):
    symbol: str | None = None
    source_document_id: int | None = None
    uploaded_report_id: int | None = None
    report_text: str
    notes: str | None = None


class ExtractionDraftRead(BaseModel):
    id: int
    company_id: int | None
    source_document_id: int | None
    uploaded_report_id: int | None
    extraction_type: str
    provider: str
    model: str
    parsed_data: dict | None
    status: str
    notes: str | None

    model_config = ConfigDict(from_attributes=True)


class ApplyDraftResult(BaseModel):
    draft_id: int
    financial_statement_id: int
    reviewed: bool


class ReportTextExtractionRead(BaseModel):
    id: int
    uploaded_report_id: int
    extraction_method: str
    page_count: int
    character_count: int
    status: str
    warnings: str | None
    text_preview: str


class ReportTextExtractionFullRead(ReportTextExtractionRead):
    text: str


class CoverageItem(BaseModel):
    label: str
    status: str
    detail: str | None = None
    reviewed: bool | None = None


class CompanyCoverageRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    overall_status: str
    coverage_score: int
    next_actions: list[str]
    price: CoverageItem
    fy_report: CoverageItem
    current_periods: list[CoverageItem]
    dividend: CoverageItem
    uploaded_reports: CoverageItem


class PriceRead(BaseModel):
    id: int
    symbol: str
    trade_date: date
    close_price: Decimal
    volume: int | None
    reviewed: bool


class PriceImportValidationRow(BaseModel):
    row_number: int
    symbol: str | None = None
    trade_date: date | None = None
    close_price: Decimal | None = None
    status: str
    action: str | None = None
    errors: list[str]
    warnings: list[str]


class PriceImportValidationResult(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    rows: list[PriceImportValidationRow]


class LatestPriceRead(BaseModel):
    symbol: str
    name: str
    trade_date: date
    close_price: Decimal
    previous_close: Decimal | None = None
    price_change: Decimal | None = None
    price_change_percent: Decimal | None = None
    volume: int | None = None
    value_traded: Decimal | None = None
    reviewed: bool


class LiquidityRead(BaseModel):
    symbol: str
    name: str
    window_days: int
    trading_days: int
    average_volume: Decimal | None
    average_value_traded: Decimal | None
    total_value_traded: Decimal | None
    latest_trade_date: date | None
    liquidity_status: str


class FinancialStatementRead(BaseModel):
    id: int
    symbol: str
    period_end: date
    period_type: str
    revenue: Decimal | None
    profit_after_tax: Decimal | None
    total_equity: Decimal | None
    eps: Decimal | None
    reviewed: bool


class DividendRead(BaseModel):
    id: int
    symbol: str
    declared_date: date | None
    ex_dividend_date: date | None
    payment_date: date | None
    amount_per_share: Decimal
    reviewed: bool


class DividendImportValidationRow(BaseModel):
    row_number: int
    symbol: str | None = None
    declared_date: date | None = None
    payment_date: date | None = None
    amount_per_share: Decimal | None = None
    status: str
    action: str | None = None
    errors: list[str]
    warnings: list[str]


class DividendImportValidationResult(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    rows: list[DividendImportValidationRow]


class DividendHistoryRead(BaseModel):
    id: int
    symbol: str
    declared_date: date | None
    ex_dividend_date: date | None
    payment_date: date | None
    amount_per_share: Decimal
    currency: str
    reviewed: bool


class DividendCandidateRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    latest_price: Decimal | None
    trailing_dividend: Decimal | None
    dividend_yield: Decimal | None
    dividend_events: int
    years_with_dividends: int
    latest_payment_date: date | None
    latest_eps: Decimal | None
    payout_ratio: Decimal | None
    dividend_cover: Decimal | None
    safety_score: int
    status: str
    warnings: list[str]


class PortfolioTransactionCreate(BaseModel):
    symbol: str
    transaction_date: date
    transaction_type: str
    quantity: Decimal = Decimal(0)
    price_per_share: Decimal | None = None
    fees: Decimal = Decimal(0)
    cash_amount: Decimal | None = None
    notes: str | None = None


class PortfolioTransactionRead(BaseModel):
    id: int
    symbol: str
    transaction_date: date
    transaction_type: str
    quantity: Decimal
    price_per_share: Decimal | None
    fees: Decimal
    cash_amount: Decimal | None
    notes: str | None


class PortfolioPositionRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    quantity: Decimal
    average_cost: Decimal | None
    cost_basis: Decimal
    latest_price: Decimal | None
    market_value: Decimal | None
    unrealized_gain_loss: Decimal | None
    unrealized_gain_loss_percent: Decimal | None
    portfolio_weight: Decimal | None
    dividends_received: Decimal


class SectorAllocationRead(BaseModel):
    sector: str
    market_value: Decimal
    portfolio_weight: Decimal


class PortfolioSummaryRead(BaseModel):
    total_cost_basis: Decimal
    total_market_value: Decimal
    total_unrealized_gain_loss: Decimal
    total_unrealized_gain_loss_percent: Decimal | None
    total_dividends_received: Decimal
    positions: list[PortfolioPositionRead]
    sector_allocation: list[SectorAllocationRead]
    warnings: list[str]


class InvestmentNoteCreate(BaseModel):
    symbol: str
    thesis: str
    risks: str | None = None
    decision: str | None = None
    note_date: date | None = None


class InvestmentNoteRead(BaseModel):
    id: int
    symbol: str
    name: str
    sector: str | None
    note_date: date
    thesis: str
    risks: str | None
    decision: str | None
    latest_score: Decimal | None = None
    latest_status: str | None = None
    portfolio_quantity: Decimal | None = None
    portfolio_weight: Decimal | None = None
    portfolio_unrealized_gain_loss_percent: Decimal | None = None


class FinancialStatementCreate(BaseModel):
    symbol: str
    period_end: date
    period_type: str
    currency: str = "NGN"
    revenue: Decimal | None = None
    profit_after_tax: Decimal | None = None
    total_assets: Decimal | None = None
    total_liabilities: Decimal | None = None
    total_equity: Decimal | None = None
    cash_flow_operations: Decimal | None = None
    eps: Decimal | None = None
    source_document_id: int | None = None


class RatioRead(BaseModel):
    symbol: str
    as_of_date: date
    price: Decimal | None
    pe_ratio: Decimal | None
    roe: Decimal | None
    net_margin: Decimal | None
    debt_to_equity: Decimal | None
    cash_flow_to_profit: Decimal | None
    revenue_growth: Decimal | None
    profit_growth: Decimal | None
    dividend_yield: Decimal | None
    data_confidence: Decimal


class ScoreRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    as_of_date: date
    quality_score: Decimal
    growth_score: Decimal
    valuation_score: Decimal
    dividend_score: Decimal
    risk_score: Decimal
    overall_score: Decimal
    status: str
    reasons: str
    risks: str


class InvestmentBriefRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    latest_note: InvestmentNoteRead | None = None
    note_count: int
    latest_score: ScoreRead | None = None
    portfolio_position: PortfolioPositionRead | None = None
    checklist: list[str]


class WatchlistCreate(BaseModel):
    name: str


class WatchlistRead(BaseModel):
    id: int
    name: str
    member_count: int


class WatchlistItemCreate(BaseModel):
    symbol: str


class WatchlistMemberRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    latest_score: Decimal | None = None
    latest_status: str | None = None
    portfolio_quantity: Decimal | None = None
    portfolio_weight: Decimal | None = None


class WatchlistDetailRead(BaseModel):
    id: int
    name: str
    members: list[WatchlistMemberRead]


class WatchlistActionRead(BaseModel):
    watchlist_id: int
    symbol: str
    action: str


class AlertRuleCreate(BaseModel):
    symbol: str
    rule_type: str
    threshold_value: Decimal | None = None
    text_value: str | None = None
    notes: str | None = None


class AlertRuleRead(BaseModel):
    id: int
    symbol: str
    name: str
    rule_type: str
    threshold_value: Decimal | None
    text_value: str | None
    is_active: bool
    notes: str | None


class AlertEventRead(BaseModel):
    id: int
    alert_rule_id: int
    symbol: str
    name: str
    alert_date: date
    rule_type: str
    observed_value: Decimal | None
    observed_text: str | None
    message: str
    status: str


class AlertEvaluationRead(BaseModel):
    evaluated_rules: int
    triggered: int
    events: list[AlertEventRead]


class ScanRunRead(BaseModel):
    scan_run_id: int
    as_of_date: date
    results: list[ScoreRead]


class PendingReviewSummaryRead(BaseModel):
    prices: int
    financial_statements: int
    dividends: int
    total: int


class ResearchDigestRead(BaseModel):
    generated_date: date
    portfolio: PortfolioSummaryRead
    pending_review: PendingReviewSummaryRead
    open_alerts: list[AlertEventRead]
    latest_scan: ScanRunRead
    dividend_candidates: list[DividendCandidateRead]
    watchlists: list[WatchlistRead]
    next_actions: list[str]


class NgxMarketRuleRead(BaseModel):
    symbol: str
    name: str
    trade_date: date | None
    previous_close: Decimal | None
    latest_close: Decimal | None
    daily_change_percent: Decimal | None
    upper_price_limit: Decimal | None
    lower_price_limit: Decimal | None
    price_band_status: str
    price_movement_group: str | None
    minimum_volume_to_move_price: int | None
    latest_volume: int | None
    volume_threshold_met: bool | None
    tick_size: Decimal | None
    warnings: list[str]


class InvestmentChecklistItemRead(BaseModel):
    question: str
    passed: bool
    detail: str


class InvestmentRuleRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    stock_types: list[str]
    fundamental_style: str
    technical_signal: str
    ngx_market_rules: NgxMarketRuleRead
    checklist: list[InvestmentChecklistItemRead]
    decision_guardrails: list[str]
    data_warnings: list[str]
