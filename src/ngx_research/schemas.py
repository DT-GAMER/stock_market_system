from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    full_name: str | None = None


class UserLogin(BaseModel):
    email: str
    password: str


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str | None
    plan: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthTokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserRead


class UserProfileUpsert(BaseModel):
    investor_goal: str | None = None
    experience_level: str | None = None
    capital_range: str | None = None
    preferred_sectors: list[str] = Field(default_factory=list)
    onboarding_completed: bool = False


class UserProfileRead(UserProfileUpsert):
    id: int
    user_id: int

    model_config = ConfigDict(from_attributes=True)


class UserWatchlistUpsert(BaseModel):
    name: str = "Starter Watchlist"
    symbols: list[str] = Field(default_factory=list, max_length=10)


class UserWatchlistRead(BaseModel):
    id: int
    name: str
    symbols: list[str]


class UserJournalEntryCreate(BaseModel):
    symbol: str
    thesis: str
    goal: str = "Capital growth"
    horizon: str = "3 to 5 years"
    target_entry: str | None = None
    exit_rule: str | None = None
    risk: str | None = None
    status: str = "Watching"


class UserJournalEntryRead(UserJournalEntryCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserPortfolioPlanItemUpsert(BaseModel):
    symbol: str
    planned_amount: Decimal = Decimal(0)


class UserPortfolioPlanUpsert(BaseModel):
    name: str = "Default Plan"
    items: list[UserPortfolioPlanItemUpsert] = Field(default_factory=list)


class UserPortfolioPlanItemRead(UserPortfolioPlanItemUpsert):
    id: int

    model_config = ConfigDict(from_attributes=True)


class UserPortfolioPlanRead(BaseModel):
    id: int
    name: str
    items: list[UserPortfolioPlanItemRead]


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


class NgxPulseSyncResult(BaseModel):
    endpoint: str
    imported: int
    updated_prices: int = 0
    updated_companies: int
    skipped: int
    errors: list[str]


class NgxPulseMarketOverviewRead(BaseModel):
    data: dict


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
    source_name: str | None = None
    report_year: int | None = None
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


class InvestmentGoalCreate(BaseModel):
    symbol: str
    goal_type: str
    reason: str
    target_price: Decimal | None = None
    target_return_percent: Decimal | None = None
    target_dividend_yield: Decimal | None = None
    target_date: date | None = None
    review_date: date | None = None
    sell_rule: str | None = None


class InvestmentGoalRead(BaseModel):
    id: int
    symbol: str
    name: str
    goal_type: str
    target_price: Decimal | None
    target_return_percent: Decimal | None
    target_dividend_yield: Decimal | None
    target_date: date | None
    review_date: date | None
    reason: str
    sell_rule: str | None
    status: str


class PriceRangeRead(BaseModel):
    latest_price: Decimal | None
    price_when_added: Decimal | None = None
    fifty_two_week_high: Decimal | None
    fifty_two_week_low: Decimal | None
    position_in_range_percent: Decimal | None


class WatchlistEntrySignalRead(BaseModel):
    watchlist_id: int
    watchlist_name: str
    symbol: str
    name: str
    sector: str | None
    stock_types: list[str]
    entry_quality: str
    decision_label: str
    next_action: str
    price_range: PriceRangeRead
    pe_ratio: Decimal | None
    overall_score: Decimal | None
    valuation_score: Decimal | None
    data_confidence: Decimal | None
    reasons: list[str]
    risks: list[str]


class WatchlistIntelligenceRead(BaseModel):
    watchlist_id: int
    watchlist_name: str
    member_count: int
    focus_warning: str | None
    signals: list[WatchlistEntrySignalRead]


class ExitSignalRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    action: str
    confidence: str
    latest_price: Decimal | None
    average_cost: Decimal | None
    unrealized_gain_loss_percent: Decimal | None
    portfolio_weight: Decimal | None
    goal: InvestmentGoalRead | None
    reasons: list[str]
    risks: list[str]
    next_action: str


class PortfolioExitIntelligenceRead(BaseModel):
    generated_date: date
    signals: list[ExitSignalRead]


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


class IntelligenceScoreBreakdownRead(BaseModel):
    business_quality: Decimal
    growth: Decimal
    valuation: Decimal
    dividend: Decimal
    financial_risk: Decimal
    momentum: Decimal
    liquidity: Decimal
    data_confidence: Decimal
    overall: Decimal


class CompanyMemoryRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    market_board: str | None
    latest_price: Decimal | None
    latest_price_date: date | None
    price_records: int
    dividend_records: int
    fundamentals_records: int
    financial_statement_records: int
    disclosure_records: int
    annual_report_records: int
    latest_fundamental_date: date | None
    latest_statement_period_end: date | None


class IntelligenceOpportunityRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    as_of_date: date
    final_label: str
    stock_types: list[str]
    scores: IntelligenceScoreBreakdownRead
    reasons: list[str]
    risks: list[str]
    missing_data: list[str]
    next_actions: list[str]
    decision_change_triggers: list[str]
    metrics: dict
    memory: CompanyMemoryRead


class IntelligenceRunRead(BaseModel):
    as_of_date: date
    generated: int
    opportunities: list[IntelligenceOpportunityRead]


class DecisionCardMetricRead(BaseModel):
    label: str
    status: str
    score: Decimal | None = None
    detail: str
    evidence: list[str] = Field(default_factory=list)


class DecisionCardSectionRead(BaseModel):
    title: str
    summary: str
    points: list[str]


class DecisionCardValuationDisplayRead(BaseModel):
    is_available: bool
    latest_price: Decimal | None = None
    fair_value_low: Decimal | None = None
    fair_value_mid: Decimal | None = None
    fair_value_high: Decimal | None = None
    valuation_label: str
    valuation_tone: str
    margin_of_safety_percent: Decimal | None = None
    expected_return_low_percent: Decimal | None = None
    expected_return_high_percent: Decimal | None = None
    valuation_confidence: str
    confidence_score: Decimal
    price_position_percent: Decimal | None = None
    methods_used: list[str]
    explanation: str
    warnings: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class DecisionCardHealthDisplayRead(BaseModel):
    label: str
    status: str
    tone: str
    detail: str
    score: Decimal | None = None
    evidence: list[str] = Field(default_factory=list)


class DecisionCardDividendYearRead(BaseModel):
    year: int
    amount_per_share: Decimal
    event_count: int


class DecisionCardDividendDisplayRead(BaseModel):
    is_available: bool
    current_yield: Decimal | None = None
    dividend_strength: str
    payout_safety: str
    projected_next_payout: Decimal | None = None
    years_with_dividends: int
    annual_history: list[DecisionCardDividendYearRead]
    explanation: str
    warnings: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class DecisionCardMoatDisplayRead(BaseModel):
    rating: str
    label: str
    tone: str
    peer_strength_score: Decimal | None = None
    summary: str
    factors: list[str]
    warnings: list[str] = Field(default_factory=list)


class DecisionCardSourceGapRead(BaseModel):
    data_layer: str
    status: str
    priority: str
    why_it_matters: str
    current_coverage: str
    suggested_source: str
    next_step: str


class ValuationMethodRead(BaseModel):
    name: str
    fair_value_low: Decimal | None = None
    fair_value_mid: Decimal | None = None
    fair_value_high: Decimal | None = None
    confidence_score: Decimal
    reason: str
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CompanyValuationRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    as_of_date: date
    latest_price: Decimal | None
    latest_price_date: date | None
    fair_value_low: Decimal | None
    fair_value_mid: Decimal | None
    fair_value_high: Decimal | None
    margin_of_safety_percent: Decimal | None
    expected_return_low_percent: Decimal | None
    expected_return_high_percent: Decimal | None
    valuation_label: str
    valuation_confidence: str
    confidence_score: Decimal
    methods: list[ValuationMethodRead]
    assumptions: list[str]
    reasons: list[str]
    warnings: list[str]
    missing_data: list[str]
    metrics: dict
    source_summary: dict


class ValuationRunRead(BaseModel):
    as_of_date: date
    generated: int
    valuations: list[CompanyValuationRead]


class PeerCategoryWinnerRead(BaseModel):
    category: str
    symbol: str | None = None
    name: str | None = None
    value: Decimal | None = None
    detail: str


class PeerMetricComparisonRead(BaseModel):
    metric: str
    company_value: Decimal | None = None
    sector_median: Decimal | None = None
    best_symbol: str | None = None
    best_value: Decimal | None = None
    rank: int | None = None
    peer_count: int
    interpretation: str


class PeerComparisonRowRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    final_label: str
    stock_types: list[str]
    sector_rank: int | None
    peer_score: Decimal
    overall_score: Decimal
    business_quality_score: Decimal
    growth_score: Decimal
    valuation_score: Decimal
    dividend_score: Decimal
    financial_risk_score: Decimal
    liquidity_score: Decimal
    data_confidence_score: Decimal
    latest_price: Decimal | None = None
    pe_ratio: Decimal | None = None
    roe: Decimal | None = None
    profit_margin: Decimal | None = None
    dividend_yield: Decimal | None = None
    margin_of_safety_percent: Decimal | None = None
    valuation_label: str | None = None


class CompanyPeerComparisonRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    as_of_date: date
    peer_count: int
    sector_rank: int | None
    sector_percentile: Decimal | None
    comparison_label: str
    best_overall_peer_symbol: str | None = None
    best_overall_peer_name: str | None = None
    category_winners: list[PeerCategoryWinnerRead]
    metric_comparisons: list[PeerMetricComparisonRead]
    peers: list[PeerComparisonRowRead]
    strengths: list[str]
    weaknesses: list[str]
    reasons: list[str]
    warnings: list[str]
    next_actions: list[str]
    metrics: dict
    source_summary: dict


class PeerComparisonRunRead(BaseModel):
    as_of_date: date
    generated: int
    comparisons: list[CompanyPeerComparisonRead]


class DecisionDashboardSummaryRead(BaseModel):
    companies_scanned: int
    research_candidates: int
    dividend_candidates: int
    undervalued_quality: int
    sector_leaders: int
    watch_for_entry: int
    avoid_or_needs_data: int


class DecisionDashboardOpportunityRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    as_of_date: date
    answer: str
    tone: str
    final_label: str
    invest_score: Decimal
    confidence: str
    confidence_score: Decimal
    risk_level: str
    suggested_horizon: str
    latest_price: Decimal | None = None
    latest_price_date: date | None = None
    fair_value_mid: Decimal | None = None
    margin_of_safety_percent: Decimal | None = None
    valuation_label: str | None = None
    valuation_confidence: str | None = None
    peer_rank: int | None = None
    peer_count: int | None = None
    peer_label: str | None = None
    best_peer_symbol: str | None = None
    stock_types: list[str]
    category_tags: list[str]
    why_attention: str
    main_risk: str
    next_action: str
    reasons: list[str]
    risks: list[str]
    next_actions: list[str]
    missing_data: list[str]
    scores: IntelligenceScoreBreakdownRead
    metrics: dict


class DecisionDashboardSpotlightRead(BaseModel):
    key: str
    title: str
    subtitle: str
    opportunity: DecisionDashboardOpportunityRead | None = None


class DecisionDashboardCategoryRead(BaseModel):
    key: str
    title: str
    summary: str
    items: list[DecisionDashboardOpportunityRead]


class DecisionDashboardRead(BaseModel):
    as_of_date: date
    generated_at: datetime
    market_summary: DecisionDashboardSummaryRead
    spotlight_cards: list[DecisionDashboardSpotlightRead]
    categories: list[DecisionDashboardCategoryRead]
    ranked: list[DecisionDashboardOpportunityRead]
    data_notes: list[str]


class DecisionCardRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    as_of_date: date
    latest_price: Decimal | None
    latest_price_date: date | None
    stock_types: list[str]
    answer: str
    invest_score: Decimal
    confidence: str
    confidence_score: Decimal
    risk_level: str
    suggested_horizon: str
    valuation_status: str
    financial_health: str
    dividend_quality: str
    moat_rating: str
    one_paragraph_summary: str
    decision_summary: str
    score_breakdown: IntelligenceScoreBreakdownRead
    valuation_snapshot: CompanyValuationRead | None = None
    peer_comparison: CompanyPeerComparisonRead | None = None
    health_checks: list[DecisionCardMetricRead]
    valuation_display: DecisionCardValuationDisplayRead
    health_display: list[DecisionCardHealthDisplayRead]
    dividend_display: DecisionCardDividendDisplayRead
    moat_display: DecisionCardMoatDisplayRead
    source_gaps: list[DecisionCardSourceGapRead]
    valuation: DecisionCardSectionRead
    why_buy: DecisionCardSectionRead
    why_not_buy: DecisionCardSectionRead
    growth_drivers: DecisionCardSectionRead
    threats: DecisionCardSectionRead
    dividend: DecisionCardSectionRead
    moat: DecisionCardSectionRead
    future_outlook: DecisionCardSectionRead
    stress_test: DecisionCardSectionRead
    portfolio_fit: DecisionCardSectionRead
    what_changed: DecisionCardSectionRead
    what_would_change_decision: DecisionCardSectionRead
    missing_data: list[str]
    data_quality_notes: list[str]


class CompanyLivePriceRead(BaseModel):
    latest_price: Decimal | None = None
    previous_close: Decimal | None = None
    price_change: Decimal | None = None
    price_change_percent: Decimal | None = None
    trade_date: date | None = None
    direction: str
    label: str
    summary: str


class CompanyPerformanceWindowRead(BaseModel):
    window: str
    available: bool
    start_date: date | None = None
    end_date: date | None = None
    start_price: Decimal | None = None
    end_price: Decimal | None = None
    return_percent: Decimal | None = None
    summary: str


class CompanyLiveNewsItemRead(BaseModel):
    title: str
    source_name: str | None = None
    published_at: datetime | None = None
    url: str | None = None
    summary: str | None = None
    item_type: str


class CompanyLiveInsightCardRead(BaseModel):
    key: str
    title: str
    tone: str
    summary: str
    points: list[str]
    source_count: int
    generated_from: list[str]


class CompanyLivePerformanceRead(BaseModel):
    headline: str
    summary: str
    sector_rank_1m: int | None = None
    sector_peer_count: int | None = None
    fifty_two_week_high: Decimal | None = None
    fifty_two_week_low: Decimal | None = None
    position_in_52_week_range_percent: Decimal | None = None
    windows: list[CompanyPerformanceWindowRead]


class CompanyLiveInsightsRead(BaseModel):
    symbol: str
    name: str
    sector: str | None
    generated_at: datetime
    price: CompanyLivePriceRead
    performance: CompanyLivePerformanceRead
    cards: list[CompanyLiveInsightCardRead]
    recent_news: list[CompanyLiveNewsItemRead]
    recent_disclosures: list[CompanyLiveNewsItemRead]
    data_notes: list[str]
