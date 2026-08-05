from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ngx_research.config import settings
from ngx_research.database import get_session, init_db
from ngx_research.models import (
    AlertEvent,
    AlertRule,
    Company,
    CompanyRatio,
    CompanyScore,
    DataReviewLog,
    Dividend,
    ExtractionDraft,
    FinancialStatement,
    PortfolioTransaction,
    Price,
    ReportTextExtraction,
    ScanResult,
    ScanRun,
    SourceDocument,
    UploadedReport,
    UserJournalEntry,
    UserPortfolioPlan,
    UserPortfolioPlanItem,
    UserPortfolioTransaction,
    UserProfile,
    UserWatchlist,
    UserWatchlistItem,
    Watchlist,
)
from ngx_research.schemas import (
    AlertEvaluationRead,
    AlertEventRead,
    AlertRuleCreate,
    AlertRuleRead,
    ApplyDraftResult,
    AuthTokenRead,
    CompanyCoverageRead,
    CompanyCreate,
    CompanyLiveInsightsRead,
    CompanyMemoryRead,
    CompanyPeerComparisonRead,
    CompanyRead,
    CompanyValuationRead,
    DecisionCardRead,
    DecisionDashboardRead,
    DividendCandidateRead,
    DividendHistoryRead,
    DividendImportValidationResult,
    DividendRead,
    ExtractionDraftCreate,
    ExtractionDraftRead,
    FinancialStatementCreate,
    FinancialStatementRead,
    ImportResult,
    IntelligenceOpportunityRead,
    IntelligenceRunRead,
    InvestmentBriefRead,
    InvestmentGoalCreate,
    InvestmentGoalRead,
    InvestmentNoteCreate,
    InvestmentNoteRead,
    InvestmentRuleRead,
    LatestPriceRead,
    LiquidityRead,
    NgxMarketRuleRead,
    NgxPulseMarketOverviewRead,
    NgxPulseSyncResult,
    PeerComparisonRunRead,
    PendingReviewItem,
    PortfolioExitIntelligenceRead,
    PortfolioPositionRead,
    PortfolioSummaryRead,
    PortfolioTransactionCreate,
    PortfolioTransactionRead,
    PriceImportValidationResult,
    PriceRead,
    RatioRead,
    ReportTextExtractionFullRead,
    ReportTextExtractionRead,
    ResearchDigestRead,
    ReviewAction,
    ReviewLogRead,
    ReviewResult,
    ScanRunRead,
    ScoreRead,
    SectorAllocationRead,
    SourceDocumentCreate,
    SourceDocumentRead,
    UploadedReportRead,
    UserCreate,
    UserJournalEntryCreate,
    UserJournalEntryRead,
    UserLogin,
    UserPortfolioPlanItemRead,
    UserPortfolioPlanRead,
    UserPortfolioPlanUpsert,
    UserProfileRead,
    UserProfileUpsert,
    UserRead,
    UserWatchlistRead,
    UserWatchlistUpsert,
    ValuationRunRead,
    WatchlistActionRead,
    WatchlistCreate,
    WatchlistDetailRead,
    WatchlistIntelligenceRead,
    WatchlistItemCreate,
    WatchlistRead,
)
from ngx_research.services.alerts import (
    create_alert_rule,
    evaluate_alert_rules,
    list_alert_events,
    list_alert_rules,
    set_alert_event_status,
    set_alert_rule_active,
)
from ngx_research.services.auth import (
    AuthError,
    create_user,
    login_user,
    revoke_bearer_token,
    user_from_bearer_token,
)
from ngx_research.services.automation_scheduler import (
    automation_status,
    run_automation_once,
    start_automation_scheduler,
)
from ngx_research.services.csv_importer import (
    import_companies,
    import_dividends,
    import_financial_statements,
    import_prices,
)
from ngx_research.services.decision_card_engine import decision_card
from ngx_research.services.decision_dashboard import decision_opportunity_dashboard
from ngx_research.services.decision_intelligence import (
    create_investment_goal,
    list_investment_goals,
    portfolio_exit_intelligence,
    watchlist_intelligence,
)
from ngx_research.services.deepseek_client import DeepSeekError, extract_financial_statement
from ngx_research.services.dividend_engine import (
    dividend_candidates,
    dividend_history,
    validate_dividend_csv,
)
from ngx_research.services.exports import export_dataset_csv
from ngx_research.services.financial_section_extractor import select_financial_section
from ngx_research.services.intelligence_engine import (
    company_memory,
    latest_intelligence_opportunities,
    run_intelligence_engine,
)
from ngx_research.services.investment_journal import (
    company_brief,
    create_note,
    list_notes,
)
from ngx_research.services.investment_rules import (
    investment_rules,
    list_investment_rules,
    ngx_market_rules,
)
from ngx_research.services.live_insights import company_live_insights
from ngx_research.services.ngxpulse_client import (
    NgxPulseError,
    fetch_market_overview,
    fetch_market_status,
    sync_all_stocks,
    sync_all_dividend_histories,
    sync_bond_auctions,
    sync_bonds,
    sync_disclosures,
    sync_dividend_history,
    sync_etfs,
    sync_fundamentals,
    sync_indices,
    sync_market_news,
    sync_nasd_otc_stocks,
    sync_symbol_prices,
)
from ngx_research.services.pdf_extractor import PdfExtractionError, extract_pdf_text
from ngx_research.services.peer_comparison_engine import (
    company_peer_comparison,
    latest_peer_comparisons,
    run_peer_comparison_engine,
)
from ngx_research.services.portfolio import create_transaction, list_transactions, portfolio_summary
from ngx_research.services.price_workflow import (
    latest_prices,
    liquidity_metrics,
    price_history,
    validate_price_csv,
)
from ngx_research.services.report_storage import save_upload
from ngx_research.services.research_digest import build_research_digest
from ngx_research.services.scanner import run_market_scan
from ngx_research.services.source_coverage import build_company_coverage, build_coverage
from ngx_research.services.valuation_engine import (
    company_valuation,
    latest_valuations,
    run_valuation_engine,
)
from ngx_research.services.watchlists import (
    add_to_watchlist,
    create_watchlist,
    list_watchlists,
    remove_from_watchlist,
    watchlist_detail,
)

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    start_automation_scheduler()


SessionDep = Annotated[Session, Depends(get_session)]
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]
CsvUpload = Annotated[UploadFile, File(...)]
ReportUploadFile = Annotated[UploadFile, File(...)]
ReportSymbol = Annotated[str | None, Form()]
ReportName = Annotated[str | None, Form()]
ReportType = Annotated[str, Form()]
ReportNotes = Annotated[str | None, Form()]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/automation/status")
def get_automation_status() -> dict:
    return automation_status()


@app.post("/automation/run-now")
async def run_automation_now() -> dict:
    return await run_automation_once()


def _current_user(session: SessionDep, authorization: AuthorizationHeader = None) -> UserRead:
    try:
        user = user_from_bearer_token(session, authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return UserRead.model_validate(user)


@app.post("/auth/signup", response_model=AuthTokenRead)
def signup(payload: UserCreate, session: SessionDep) -> AuthTokenRead:
    try:
        user, token, expires_at = create_user(
            session=session,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AuthTokenRead(access_token=token, expires_at=expires_at, user=UserRead.model_validate(user))


@app.post("/auth/login", response_model=AuthTokenRead)
def login(payload: UserLogin, session: SessionDep) -> AuthTokenRead:
    try:
        user, token, expires_at = login_user(session, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return AuthTokenRead(access_token=token, expires_at=expires_at, user=UserRead.model_validate(user))


@app.get("/auth/me", response_model=UserRead)
def me(current_user: Annotated[UserRead, Depends(_current_user)]) -> UserRead:
    return current_user


@app.post("/auth/logout")
def logout(session: SessionDep, authorization: AuthorizationHeader = None) -> dict[str, str]:
    try:
        revoke_bearer_token(session, authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"status": "logged_out"}


@app.get("/me/profile", response_model=UserProfileRead)
def my_profile(
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> UserProfile:
    return _user_profile(session, current_user.id)


@app.put("/me/profile", response_model=UserProfileRead)
def save_my_profile(
    payload: UserProfileUpsert,
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> UserProfile:
    profile = _user_profile(session, current_user.id)
    profile.investor_goal = payload.investor_goal
    profile.experience_level = payload.experience_level
    profile.capital_range = payload.capital_range
    profile.preferred_sectors = payload.preferred_sectors
    profile.onboarding_completed = payload.onboarding_completed
    session.commit()
    session.refresh(profile)
    return profile


@app.get("/me/watchlist", response_model=UserWatchlistRead)
def my_watchlist(
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> UserWatchlistRead:
    watchlist = _user_watchlist(session, current_user.id)
    symbols = list(
        session.scalars(
            select(UserWatchlistItem.symbol)
            .where(UserWatchlistItem.user_watchlist_id == watchlist.id)
            .order_by(UserWatchlistItem.created_at)
        )
    )
    return UserWatchlistRead(id=watchlist.id, name=watchlist.name, symbols=symbols)


@app.put("/me/watchlist", response_model=UserWatchlistRead)
def save_my_watchlist(
    payload: UserWatchlistUpsert,
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> UserWatchlistRead:
    watchlist = _user_watchlist(session, current_user.id, name=payload.name)
    session.query(UserWatchlistItem).filter(
        UserWatchlistItem.user_watchlist_id == watchlist.id
    ).delete()
    seen: set[str] = set()
    for symbol in payload.symbols:
        normalized = symbol.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            session.add(UserWatchlistItem(user_watchlist_id=watchlist.id, symbol=normalized))
    session.commit()
    return UserWatchlistRead(id=watchlist.id, name=watchlist.name, symbols=list(seen))


@app.get("/me/journal", response_model=list[UserJournalEntryRead])
def my_journal(
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> list[UserJournalEntry]:
    return list(
        session.scalars(
            select(UserJournalEntry)
            .where(UserJournalEntry.user_id == current_user.id)
            .order_by(desc(UserJournalEntry.created_at))
        )
    )


@app.post("/me/journal", response_model=UserJournalEntryRead)
def create_my_journal_entry(
    payload: UserJournalEntryCreate,
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> UserJournalEntry:
    entry = UserJournalEntry(
        user_id=current_user.id,
        symbol=payload.symbol.strip().upper(),
        thesis=payload.thesis.strip(),
        goal=payload.goal,
        horizon=payload.horizon,
        target_entry=payload.target_entry,
        exit_rule=payload.exit_rule,
        risk=payload.risk,
        status=payload.status,
    )
    if not entry.symbol or not entry.thesis:
        raise HTTPException(status_code=400, detail="symbol and thesis are required")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@app.delete("/me/journal/{entry_id}")
def delete_my_journal_entry(
    entry_id: int,
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> dict[str, str]:
    entry = session.get(UserJournalEntry, entry_id)
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="journal entry not found")
    session.delete(entry)
    session.commit()
    return {"status": "deleted"}


@app.get("/me/portfolio-plan", response_model=UserPortfolioPlanRead)
def my_portfolio_plan(
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> UserPortfolioPlanRead:
    plan = _user_portfolio_plan(session, current_user.id)
    return _portfolio_plan_read(session, plan)


@app.put("/me/portfolio-plan", response_model=UserPortfolioPlanRead)
def save_my_portfolio_plan(
    payload: UserPortfolioPlanUpsert,
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> UserPortfolioPlanRead:
    plan = _user_portfolio_plan(session, current_user.id, name=payload.name)
    session.query(UserPortfolioPlanItem).filter(
        UserPortfolioPlanItem.user_portfolio_plan_id == plan.id
    ).delete()
    for item in payload.items:
        normalized = item.symbol.strip().upper()
        if normalized:
            session.add(
                UserPortfolioPlanItem(
                    user_portfolio_plan_id=plan.id,
                    symbol=normalized,
                    planned_amount=item.planned_amount,
                )
            )
    session.commit()
    return _portfolio_plan_read(session, plan)


@app.post("/me/portfolio/transactions", response_model=PortfolioTransactionRead)
def create_my_portfolio_transaction(
    payload: PortfolioTransactionCreate,
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> PortfolioTransactionRead:
    company = _company_by_symbol(session, payload.symbol)
    try:
        _validate_portfolio_transaction(
            payload.transaction_type,
            payload.quantity,
            payload.price_per_share,
            payload.fees,
            payload.cash_amount,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    transaction = UserPortfolioTransaction(
        user_id=current_user.id,
        company_id=company.id,
        transaction_date=payload.transaction_date,
        transaction_type=payload.transaction_type.upper(),
        quantity=payload.quantity,
        price_per_share=payload.price_per_share,
        fees=payload.fees,
        cash_amount=payload.cash_amount,
        notes=payload.notes,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return _user_portfolio_transaction_read(transaction, company.symbol)


@app.get("/me/portfolio/transactions", response_model=list[PortfolioTransactionRead])
def my_portfolio_transactions(
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
    limit: int = 100,
) -> list[PortfolioTransactionRead]:
    rows = session.execute(
        select(UserPortfolioTransaction, Company.symbol)
        .join(Company, Company.id == UserPortfolioTransaction.company_id)
        .where(UserPortfolioTransaction.user_id == current_user.id)
        .order_by(
            desc(UserPortfolioTransaction.transaction_date),
            desc(UserPortfolioTransaction.id),
        )
        .limit(limit)
    )
    return [
        _user_portfolio_transaction_read(transaction, symbol) for transaction, symbol in rows
    ]


@app.get("/me/portfolio/summary", response_model=PortfolioSummaryRead)
def my_portfolio_summary(
    session: SessionDep,
    current_user: Annotated[UserRead, Depends(_current_user)],
) -> PortfolioSummaryRead:
    return _user_portfolio_summary(session, current_user.id)


@app.post("/companies", response_model=CompanyRead)
def create_company(payload: CompanyCreate, session: SessionDep) -> Company:
    company = Company(
        symbol=payload.symbol.upper(),
        name=payload.name,
        sector=payload.sector,
        market_board=payload.market_board,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@app.get("/companies", response_model=list[CompanyRead])
def list_companies(session: SessionDep) -> list[Company]:
    return list(session.scalars(select(Company).order_by(Company.symbol)))


@app.post("/portfolio/transactions", response_model=PortfolioTransactionRead)
def create_portfolio_transaction(
    payload: PortfolioTransactionCreate,
    session: SessionDep,
) -> PortfolioTransactionRead:
    company = _company_by_symbol(session, payload.symbol)
    try:
        transaction = create_transaction(
            session=session,
            company=company,
            transaction_date=payload.transaction_date,
            transaction_type=payload.transaction_type,
            quantity=payload.quantity,
            price_per_share=payload.price_per_share,
            fees=payload.fees,
            cash_amount=payload.cash_amount,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _portfolio_transaction_read(transaction, company.symbol)


@app.get("/portfolio/transactions", response_model=list[PortfolioTransactionRead])
def portfolio_transactions(session: SessionDep, limit: int = 100) -> list[PortfolioTransactionRead]:
    return list_transactions(session, limit=limit)


@app.get("/portfolio/summary", response_model=PortfolioSummaryRead)
def portfolio_summary_view(session: SessionDep) -> PortfolioSummaryRead:
    return portfolio_summary(session)


@app.get("/portfolio/exit-intelligence", response_model=PortfolioExitIntelligenceRead)
def portfolio_exit_intelligence_view(session: SessionDep) -> PortfolioExitIntelligenceRead:
    return portfolio_exit_intelligence(session)


@app.post("/research/notes", response_model=InvestmentNoteRead)
def create_research_note(
    payload: InvestmentNoteCreate,
    session: SessionDep,
) -> InvestmentNoteRead:
    company = _company_by_symbol(session, payload.symbol)
    try:
        return create_note(
            session=session,
            company=company,
            thesis=payload.thesis,
            risks=payload.risks,
            decision=payload.decision,
            note_date=payload.note_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/research/notes", response_model=list[InvestmentNoteRead])
def research_notes(
    session: SessionDep,
    symbol: str | None = None,
    limit: int = 100,
) -> list[InvestmentNoteRead]:
    return list_notes(session, symbol=symbol, limit=limit)


@app.post("/research/goals", response_model=InvestmentGoalRead)
def create_research_goal(
    payload: InvestmentGoalCreate,
    session: SessionDep,
) -> InvestmentGoalRead:
    company = _company_by_symbol(session, payload.symbol)
    try:
        return create_investment_goal(
            session=session,
            company=company,
            goal_type=payload.goal_type,
            reason=payload.reason,
            target_price=payload.target_price,
            target_return_percent=payload.target_return_percent,
            target_dividend_yield=payload.target_dividend_yield,
            target_date=payload.target_date,
            review_date=payload.review_date,
            sell_rule=payload.sell_rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/research/goals", response_model=list[InvestmentGoalRead])
def research_goals(
    session: SessionDep,
    status: str | None = "active",
    limit: int = 100,
) -> list[InvestmentGoalRead]:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    return list_investment_goals(session, status=status, limit=limit)


@app.get("/research/{symbol}/brief", response_model=InvestmentBriefRead)
def research_brief(symbol: str, session: SessionDep) -> InvestmentBriefRead:
    company = _company_by_symbol(session, symbol)
    return company_brief(session, company)


@app.get("/rules/investment", response_model=list[InvestmentRuleRead])
def investment_rule_list(session: SessionDep, limit: int = 100) -> list[InvestmentRuleRead]:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    return list_investment_rules(session, limit=limit)


@app.get("/rules/investment/{symbol}", response_model=InvestmentRuleRead)
def company_investment_rules(symbol: str, session: SessionDep) -> InvestmentRuleRead:
    company = _company_by_symbol(session, symbol)
    return investment_rules(session, company)


@app.get("/rules/ngx/{symbol}", response_model=NgxMarketRuleRead)
def company_ngx_market_rules(symbol: str, session: SessionDep) -> NgxMarketRuleRead:
    company = _company_by_symbol(session, symbol)
    return ngx_market_rules(session, company)


@app.post("/watchlists", response_model=WatchlistRead)
def create_research_watchlist(
    payload: WatchlistCreate,
    session: SessionDep,
) -> WatchlistRead:
    try:
        return create_watchlist(session, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/watchlists", response_model=list[WatchlistRead])
def research_watchlists(session: SessionDep) -> list[WatchlistRead]:
    return list_watchlists(session)


@app.get("/watchlists/{watchlist_id}", response_model=WatchlistDetailRead)
def research_watchlist_detail(watchlist_id: int, session: SessionDep) -> WatchlistDetailRead:
    watchlist = _watchlist_by_id(session, watchlist_id)
    return watchlist_detail(session, watchlist)


@app.get("/watchlists/{watchlist_id}/intelligence", response_model=WatchlistIntelligenceRead)
def research_watchlist_intelligence(
    watchlist_id: int,
    session: SessionDep,
) -> WatchlistIntelligenceRead:
    watchlist = _watchlist_by_id(session, watchlist_id)
    return watchlist_intelligence(session, watchlist)


@app.post("/watchlists/{watchlist_id}/items", response_model=WatchlistActionRead)
def add_research_watchlist_item(
    watchlist_id: int,
    payload: WatchlistItemCreate,
    session: SessionDep,
) -> WatchlistActionRead:
    watchlist = _watchlist_by_id(session, watchlist_id)
    company = _company_by_symbol(session, payload.symbol)
    try:
        return add_to_watchlist(session, watchlist, company)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/watchlists/{watchlist_id}/items/{symbol}", response_model=WatchlistActionRead)
def remove_research_watchlist_item(
    watchlist_id: int,
    symbol: str,
    session: SessionDep,
) -> WatchlistActionRead:
    watchlist = _watchlist_by_id(session, watchlist_id)
    company = _company_by_symbol(session, symbol)
    try:
        return remove_from_watchlist(session, watchlist, company)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/alerts/rules", response_model=AlertRuleRead)
def create_research_alert_rule(
    payload: AlertRuleCreate,
    session: SessionDep,
) -> AlertRuleRead:
    company = _company_by_symbol(session, payload.symbol)
    try:
        return create_alert_rule(
            session=session,
            company=company,
            rule_type=payload.rule_type,
            threshold_value=payload.threshold_value,
            text_value=payload.text_value,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/alerts/rules", response_model=list[AlertRuleRead])
def research_alert_rules(
    session: SessionDep,
    active_only: bool = False,
    limit: int = 100,
) -> list[AlertRuleRead]:
    return list_alert_rules(session, active_only=active_only, limit=limit)


@app.post("/alerts/rules/{rule_id}/activate", response_model=AlertRuleRead)
def activate_research_alert_rule(rule_id: int, session: SessionDep) -> AlertRuleRead:
    rule = _alert_rule_by_id(session, rule_id)
    try:
        return set_alert_rule_active(session, rule, True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/alerts/rules/{rule_id}/deactivate", response_model=AlertRuleRead)
def deactivate_research_alert_rule(rule_id: int, session: SessionDep) -> AlertRuleRead:
    rule = _alert_rule_by_id(session, rule_id)
    try:
        return set_alert_rule_active(session, rule, False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/alerts/evaluate", response_model=AlertEvaluationRead)
def evaluate_research_alerts(session: SessionDep) -> AlertEvaluationRead:
    return evaluate_alert_rules(session)


@app.get("/alerts/events", response_model=list[AlertEventRead])
def research_alert_events(
    session: SessionDep,
    status: str | None = None,
    limit: int = 100,
) -> list[AlertEventRead]:
    return list_alert_events(session, status=status, limit=limit)


@app.post("/alerts/events/{event_id}/acknowledge", response_model=AlertEventRead)
def acknowledge_research_alert_event(event_id: int, session: SessionDep) -> AlertEventRead:
    event = _alert_event_by_id(session, event_id)
    try:
        return set_alert_event_status(session, event, "acknowledged")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/alerts/events/{event_id}/dismiss", response_model=AlertEventRead)
def dismiss_research_alert_event(event_id: int, session: SessionDep) -> AlertEventRead:
    event = _alert_event_by_id(session, event_id)
    try:
        return set_alert_event_status(session, event, "dismissed")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/digest/weekly", response_model=ResearchDigestRead)
def weekly_research_digest(session: SessionDep, limit: int = 10) -> ResearchDigestRead:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    return build_research_digest(session, limit=limit)


@app.get("/exports/{dataset}.csv")
def export_research_dataset(dataset: str, session: SessionDep, limit: int = 100) -> Response:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    try:
        filename, content = export_dataset_csv(session, dataset, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/integrations/ngxpulse/market", response_model=NgxPulseMarketOverviewRead)
async def ngxpulse_market_overview() -> NgxPulseMarketOverviewRead:
    try:
        return NgxPulseMarketOverviewRead(data=await fetch_market_overview())
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/integrations/ngxpulse/market-status", response_model=NgxPulseMarketOverviewRead)
async def ngxpulse_market_status(session: SessionDep) -> NgxPulseMarketOverviewRead:
    try:
        return NgxPulseMarketOverviewRead(data=await fetch_market_status(session))
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/stocks", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_stocks(
    session: SessionDep,
    trade_date: date | None = None,
) -> NgxPulseSyncResult:
    try:
        return await sync_all_stocks(session, trade_date=trade_date)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/fundamentals", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_fundamentals(
    session: SessionDep,
    symbols: str | None = None,
    as_of_date: date | None = None,
) -> NgxPulseSyncResult:
    symbol_list = [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()] if symbols else None
    try:
        return await sync_fundamentals(session, symbols=symbol_list, as_of_date=as_of_date)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/dividends/{symbol}", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_dividends(symbol: str, session: SessionDep) -> NgxPulseSyncResult:
    try:
        return await sync_dividend_history(session, symbol)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/dividends", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_all_dividends(
    session: SessionDep,
    symbols: str | None = None,
    limit: int | None = None,
) -> NgxPulseSyncResult:
    if limit is not None and limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    symbol_list = (
        [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]
        if symbols
        else None
    )
    try:
        return await sync_all_dividend_histories(
            session,
            symbols=symbol_list,
            limit=limit,
            pause_seconds=settings.ngxpulse_request_pause_seconds,
        )
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/disclosures", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_disclosures(session: SessionDep, limit: int | None = None) -> NgxPulseSyncResult:
    if limit is not None and limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    try:
        return await sync_disclosures(session, limit=limit)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/indices", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_indices(session: SessionDep) -> NgxPulseSyncResult:
    try:
        return await sync_indices(session)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/etfs", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_etfs(session: SessionDep) -> NgxPulseSyncResult:
    try:
        return await sync_etfs(session)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/bonds", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_bonds(session: SessionDep) -> NgxPulseSyncResult:
    try:
        return await sync_bonds(session)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/bond-auctions", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_bond_auctions(
    session: SessionDep,
    limit: int | None = 50,
) -> NgxPulseSyncResult:
    if limit is not None and limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    try:
        return await sync_bond_auctions(session, limit=limit)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/nasd-otc/stocks", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_nasd_otc_stocks(session: SessionDep) -> NgxPulseSyncResult:
    try:
        return await sync_nasd_otc_stocks(session)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/news", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_market_news(
    session: SessionDep,
    limit: int | None = 50,
) -> NgxPulseSyncResult:
    if limit is not None and limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    try:
        return await sync_market_news(session, limit=limit)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/integrations/ngxpulse/sync/prices/{symbol}", response_model=NgxPulseSyncResult)
async def sync_ngxpulse_symbol_prices(
    symbol: str,
    session: SessionDep,
    days: int | None = None,
    trade_date: date | None = None,
) -> NgxPulseSyncResult:
    if days is not None and days <= 0:
        raise HTTPException(status_code=400, detail="days must be greater than zero")
    try:
        return await sync_symbol_prices(session, symbol=symbol, days=days, trade_date=trade_date)
    except NgxPulseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/coverage/source", response_model=list[CompanyCoverageRead])
def source_coverage(session: SessionDep, limit: int = 100) -> list[CompanyCoverageRead]:
    return build_coverage(session)[:limit]


@app.get("/coverage/source/{symbol}", response_model=CompanyCoverageRead)
def company_source_coverage(symbol: str, session: SessionDep) -> CompanyCoverageRead:
    coverage = build_company_coverage(session, symbol)
    if not coverage:
        raise HTTPException(status_code=404, detail=f"unknown company symbol {symbol.upper()}")
    return coverage


@app.get("/sources", response_model=list[SourceDocumentRead])
def list_sources(session: SessionDep, limit: int = 100) -> list[SourceDocument]:
    return list(
        session.scalars(select(SourceDocument).order_by(desc(SourceDocument.created_at)).limit(limit))
    )


@app.post("/sources", response_model=SourceDocumentRead)
def create_source(payload: SourceDocumentCreate, session: SessionDep) -> SourceDocument:
    source = SourceDocument(
        name=payload.name,
        url=payload.url,
        document_type=payload.document_type,
        notes=payload.notes,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


@app.post("/reports/upload", response_model=UploadedReportRead)
async def upload_report(
    session: SessionDep,
    file: ReportUploadFile,
    symbol: ReportSymbol = None,
    name: ReportName = None,
    document_type: ReportType = "financial_report",
    notes: ReportNotes = None,
) -> UploadedReport:
    company = _company_by_symbol(session, symbol) if symbol else None
    stored_path, file_size, digest = await save_upload(file)
    source = SourceDocument(
        name=name or file.filename or "Uploaded report",
        document_type=document_type,
        notes=notes,
    )
    session.add(source)
    session.flush()
    report = UploadedReport(
        source_document_id=source.id,
        company_id=company.id if company else None,
        original_filename=file.filename or "report",
        stored_path=stored_path,
        content_type=file.content_type,
        file_size=file_size,
        sha256=digest,
        status="uploaded",
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


@app.get("/reports", response_model=list[UploadedReportRead])
def list_reports(session: SessionDep, limit: int = 100) -> list[UploadedReport]:
    return list(
        session.scalars(select(UploadedReport).order_by(desc(UploadedReport.created_at)).limit(limit))
    )


@app.delete("/reports/{report_id}")
def delete_report(report_id: int, session: SessionDep) -> dict[str, str]:
    report = session.get(UploadedReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="uploaded report not found")

    source_id = report.source_document_id
    stored_path = report.stored_path
    session.query(ExtractionDraft).filter(ExtractionDraft.uploaded_report_id == report.id).delete()
    session.query(ReportTextExtraction).filter(ReportTextExtraction.uploaded_report_id == report.id).delete()
    session.delete(report)
    session.flush()

    source_has_other_records = session.scalar(
        select(UploadedReport.id).where(UploadedReport.source_document_id == source_id).limit(1)
    )
    if not source_has_other_records:
        source = session.get(SourceDocument, source_id)
        if source:
            session.delete(source)

    session.commit()
    _delete_uploaded_file(stored_path)
    return {"status": "deleted"}


@app.post("/reports/{report_id}/extract-text", response_model=ReportTextExtractionRead)
def extract_report_text(report_id: int, session: SessionDep) -> ReportTextExtractionRead:
    report = session.get(UploadedReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="uploaded report not found")

    try:
        text, page_count, warnings = extract_pdf_text(report.stored_path)
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    extraction = ReportTextExtraction(
        uploaded_report_id=report.id,
        extraction_method="pypdf",
        page_count=page_count,
        character_count=len(text),
        text=text,
        status="extracted",
        warnings="\n".join(warnings) if warnings else None,
    )
    report.status = "text_extracted"
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    return _text_extraction_read(extraction)


@app.get("/reports/{report_id}/text", response_model=ReportTextExtractionFullRead)
def get_report_text(report_id: int, session: SessionDep) -> ReportTextExtractionFullRead:
    extraction = _latest_report_text(session, report_id)
    return _text_extraction_full_read(extraction)


@app.post("/llm/extraction-drafts/from-text", response_model=ExtractionDraftRead)
async def create_extraction_draft_from_text(
    payload: ExtractionDraftCreate,
    session: SessionDep,
) -> ExtractionDraft:
    company = _company_by_symbol(session, payload.symbol) if payload.symbol else None
    _ensure_source_refs(session, payload.source_document_id, payload.uploaded_report_id)
    draft = await _create_extraction_draft(
        session=session,
        report_text=payload.report_text,
        company_id=company.id if company else None,
        source_document_id=payload.source_document_id,
        uploaded_report_id=payload.uploaded_report_id,
        notes=payload.notes,
    )
    return draft


@app.post("/reports/{report_id}/extraction-drafts", response_model=ExtractionDraftRead)
async def create_extraction_draft_from_report(report_id: int, session: SessionDep) -> ExtractionDraft:
    report = session.get(UploadedReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="uploaded report not found")

    extraction = _latest_report_text(session, report.id)

    return await _create_extraction_draft(
        session=session,
        report_text=extraction.text,
        company_id=report.company_id,
        source_document_id=report.source_document_id,
        uploaded_report_id=report.id,
        notes=f"Generated from report text extraction {extraction.id}.",
    )


@app.get("/llm/extraction-drafts", response_model=list[ExtractionDraftRead])
def list_extraction_drafts(session: SessionDep, limit: int = 100) -> list[ExtractionDraft]:
    return list(
        session.scalars(select(ExtractionDraft).order_by(desc(ExtractionDraft.created_at)).limit(limit))
    )


@app.post("/llm/extraction-drafts/{draft_id}/apply", response_model=ApplyDraftResult)
def apply_extraction_draft(draft_id: int, session: SessionDep) -> ApplyDraftResult:
    draft = session.get(ExtractionDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="extraction draft not found")
    if not draft.parsed_data:
        raise HTTPException(status_code=400, detail="draft has no parsed data")

    payload = _financial_statement_from_draft(session, draft)
    statement = FinancialStatement(**payload, reviewed=False)
    session.add(statement)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="financial statement already exists for this company, period, and type",
        ) from exc

    draft.status = "applied"
    session.add(
        DataReviewLog(
            record_type="extraction_drafts",
            record_id=draft.id,
            action="applied",
            notes=f"Created financial statement {statement.id}",
        )
    )
    session.commit()
    return ApplyDraftResult(
        draft_id=draft.id,
        financial_statement_id=statement.id,
        reviewed=statement.reviewed,
    )


@app.get("/prices", response_model=list[PriceRead])
def list_prices(session: SessionDep, limit: int = 100) -> list[PriceRead]:
    rows = session.execute(
        select(Price.id, Company.symbol, Price.trade_date, Price.close_price, Price.volume, Price.reviewed)
        .join(Price, Price.company_id == Company.id)
        .order_by(desc(Price.trade_date), Company.symbol)
        .limit(limit)
    )
    return [PriceRead.model_validate(row._mapping) for row in rows]


@app.post("/prices/validate-import", response_model=PriceImportValidationResult)
async def validate_price_import(session: SessionDep, file: CsvUpload) -> PriceImportValidationResult:
    return validate_price_csv(_text_file(file), session)


@app.get("/prices/latest", response_model=list[LatestPriceRead])
def latest_price_list(session: SessionDep, limit: int = 100) -> list[LatestPriceRead]:
    return latest_prices(session, limit=limit)


@app.get("/prices/{symbol}/history", response_model=list[PriceRead])
def company_price_history(symbol: str, session: SessionDep, limit: int = 100) -> list[PriceRead]:
    history = price_history(session, symbol, limit=limit)
    if history is None:
        raise HTTPException(status_code=404, detail=f"unknown company symbol {symbol.upper()}")
    return history


@app.get("/prices/liquidity", response_model=list[LiquidityRead])
def price_liquidity(
    session: SessionDep,
    window_days: int = 90,
    limit: int = 100,
) -> list[LiquidityRead]:
    if window_days <= 0:
        raise HTTPException(status_code=400, detail="window_days must be greater than zero")
    return liquidity_metrics(session, window_days=window_days, limit=limit)


@app.get("/financial-statements", response_model=list[FinancialStatementRead])
def list_financial_statements(session: SessionDep, limit: int = 100) -> list[FinancialStatementRead]:
    rows = session.execute(
        select(
            FinancialStatement.id,
            Company.symbol,
            FinancialStatement.period_end,
            FinancialStatement.period_type,
            FinancialStatement.revenue,
            FinancialStatement.profit_after_tax,
            FinancialStatement.total_equity,
            FinancialStatement.eps,
            FinancialStatement.reviewed,
        )
        .join(FinancialStatement, FinancialStatement.company_id == Company.id)
        .order_by(desc(FinancialStatement.period_end), Company.symbol)
        .limit(limit)
    )
    return [FinancialStatementRead.model_validate(row._mapping) for row in rows]


@app.post("/financial-statements", response_model=FinancialStatementRead)
def create_financial_statement(
    payload: FinancialStatementCreate,
    session: SessionDep,
) -> FinancialStatementRead:
    company = _company_by_symbol(session, payload.symbol)
    if payload.source_document_id and not session.get(SourceDocument, payload.source_document_id):
        raise HTTPException(status_code=404, detail="source document not found")

    statement = FinancialStatement(
        company_id=company.id,
        period_end=payload.period_end,
        period_type=payload.period_type.upper(),
        currency=payload.currency,
        revenue=payload.revenue,
        profit_after_tax=payload.profit_after_tax,
        total_assets=payload.total_assets,
        total_liabilities=payload.total_liabilities,
        total_equity=payload.total_equity,
        cash_flow_operations=payload.cash_flow_operations,
        eps=payload.eps,
        source_document_id=payload.source_document_id,
        reviewed=False,
    )
    session.add(statement)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="financial statement already exists for this company, period, and type",
        ) from exc
    session.refresh(statement)
    return FinancialStatementRead(
        id=statement.id,
        symbol=company.symbol,
        period_end=statement.period_end,
        period_type=statement.period_type,
        revenue=statement.revenue,
        profit_after_tax=statement.profit_after_tax,
        total_equity=statement.total_equity,
        eps=statement.eps,
        reviewed=statement.reviewed,
    )


@app.get("/dividends", response_model=list[DividendRead])
def list_dividends(session: SessionDep, limit: int = 100) -> list[DividendRead]:
    rows = session.execute(
        select(
            Dividend.id,
            Company.symbol,
            Dividend.declared_date,
            Dividend.ex_dividend_date,
            Dividend.payment_date,
            Dividend.amount_per_share,
            Dividend.reviewed,
        )
        .join(Dividend, Dividend.company_id == Company.id)
        .order_by(desc(Dividend.declared_date), Company.symbol)
        .limit(limit)
    )
    return [DividendRead.model_validate(row._mapping) for row in rows]


@app.post("/dividends/validate-import", response_model=DividendImportValidationResult)
async def validate_dividend_import(
    session: SessionDep,
    file: CsvUpload,
) -> DividendImportValidationResult:
    return validate_dividend_csv(_text_file(file), session)


@app.get("/dividends/{symbol}/history", response_model=list[DividendHistoryRead])
def company_dividend_history(
    symbol: str,
    session: SessionDep,
    limit: int = 100,
) -> list[DividendHistoryRead]:
    history = dividend_history(session, symbol, limit=limit)
    if history is None:
        raise HTTPException(status_code=404, detail=f"unknown company symbol {symbol.upper()}")
    return history


@app.get("/dividends/candidates", response_model=list[DividendCandidateRead])
def dividend_candidate_list(
    session: SessionDep,
    lookback_years: int = 5,
    limit: int = 100,
) -> list[DividendCandidateRead]:
    if lookback_years <= 0:
        raise HTTPException(status_code=400, detail="lookback_years must be greater than zero")
    return dividend_candidates(session, lookback_years=lookback_years, limit=limit)


@app.get("/review/pending", response_model=list[PendingReviewItem])
def pending_review(
    session: SessionDep,
    record_type: str | None = None,
    limit: int = 100,
) -> list[PendingReviewItem]:
    allowed = ("prices", "financial_statements", "dividends")
    if record_type and record_type not in allowed:
        raise HTTPException(status_code=400, detail=f"record_type must be one of {allowed}")

    items: list[PendingReviewItem] = []
    record_types = [record_type] if record_type else list(allowed)
    for current_type in record_types:
        items.extend(_pending_items(session, current_type, limit))
    return items[:limit]


@app.post("/review/{record_type}/{record_id}/approve", response_model=ReviewResult)
def approve_record(
    record_type: str,
    record_id: int,
    session: SessionDep,
    payload: ReviewAction | None = None,
) -> ReviewResult:
    return _set_review_status(session, record_type, record_id, True, "approved", payload)


@app.post("/review/{record_type}/{record_id}/flag", response_model=ReviewResult)
def flag_record(
    record_type: str,
    record_id: int,
    session: SessionDep,
    payload: ReviewAction | None = None,
) -> ReviewResult:
    return _set_review_status(session, record_type, record_id, False, "flagged", payload)


@app.get("/review/logs", response_model=list[ReviewLogRead])
def review_logs(session: SessionDep, limit: int = 100) -> list[DataReviewLog]:
    return list(
        session.scalars(select(DataReviewLog).order_by(desc(DataReviewLog.created_at)).limit(limit))
    )


@app.get("/ratios", response_model=list[RatioRead])
def list_ratios(session: SessionDep, limit: int = 100) -> list[RatioRead]:
    rows = session.execute(
        select(
            Company.symbol,
            CompanyRatio.as_of_date,
            CompanyRatio.price,
            CompanyRatio.pe_ratio,
            CompanyRatio.roe,
            CompanyRatio.net_margin,
            CompanyRatio.debt_to_equity,
            CompanyRatio.cash_flow_to_profit,
            CompanyRatio.revenue_growth,
            CompanyRatio.profit_growth,
            CompanyRatio.dividend_yield,
            CompanyRatio.data_confidence,
        )
        .join(CompanyRatio, CompanyRatio.company_id == Company.id)
        .order_by(desc(CompanyRatio.as_of_date), Company.symbol)
        .limit(limit)
    )
    return [RatioRead.model_validate(row._mapping) for row in rows]


@app.get("/scores", response_model=list[ScoreRead])
def list_scores(session: SessionDep, limit: int = 100) -> list[ScoreRead]:
    rows = session.execute(_score_select().order_by(desc(CompanyScore.overall_score)).limit(limit))
    return [ScoreRead.model_validate(row._mapping) for row in rows]


@app.post("/scans/run")
def run_scan(session: SessionDep) -> dict[str, int]:
    summary = run_market_scan(session)
    intelligence = run_intelligence_engine(session, as_of_date=datetime.now(UTC).date(), limit=100)
    valuation = run_valuation_engine(session, as_of_date=intelligence.as_of_date, limit=100)
    comparison = run_peer_comparison_engine(session, as_of_date=intelligence.as_of_date, limit=100)
    return {
        "scan_run_id": summary.scan_run_id,
        "scored": summary.scored,
        "insufficient_data": summary.insufficient_data,
        "intelligence_generated": intelligence.generated,
        "valuations_generated": valuation.generated,
        "comparisons_generated": comparison.generated,
    }


@app.post("/intelligence/run", response_model=IntelligenceRunRead)
def run_intelligence(session: SessionDep) -> IntelligenceRunRead:
    result = run_intelligence_engine(session)
    run_valuation_engine(session, as_of_date=result.as_of_date, limit=100)
    run_peer_comparison_engine(session, as_of_date=result.as_of_date, limit=100)
    return result


@app.get("/intelligence/opportunities", response_model=list[IntelligenceOpportunityRead])
def intelligence_opportunities(session: SessionDep, limit: int = 100) -> list[IntelligenceOpportunityRead]:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    return latest_intelligence_opportunities(session, limit=limit)


@app.get("/decision/opportunities", response_model=DecisionDashboardRead)
def decision_opportunities(session: SessionDep, limit: int | None = None) -> DecisionDashboardRead:
    if limit is not None and limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    return decision_opportunity_dashboard(session, limit=limit)


@app.get("/intelligence/company/{symbol}/decision-card", response_model=DecisionCardRead)
def intelligence_company_decision_card(symbol: str, session: SessionDep) -> DecisionCardRead:
    try:
        return decision_card(session, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/intelligence/company/{symbol}/live-insights", response_model=CompanyLiveInsightsRead)
def intelligence_company_live_insights(symbol: str, session: SessionDep) -> CompanyLiveInsightsRead:
    try:
        return company_live_insights(session, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/intelligence/company/{symbol}/memory", response_model=CompanyMemoryRead)
def intelligence_company_memory(symbol: str, session: SessionDep) -> CompanyMemoryRead:
    try:
        return company_memory(session, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/valuation/run", response_model=ValuationRunRead)
def run_valuation(session: SessionDep) -> ValuationRunRead:
    result = run_valuation_engine(session)
    run_peer_comparison_engine(session, as_of_date=result.as_of_date, limit=100)
    return result


@app.post("/comparison/run", response_model=PeerComparisonRunRead)
def run_comparison(session: SessionDep) -> PeerComparisonRunRead:
    return run_peer_comparison_engine(session)


@app.get("/comparison/latest", response_model=list[CompanyPeerComparisonRead])
def comparison_latest(session: SessionDep, limit: int = 100) -> list[CompanyPeerComparisonRead]:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    return latest_peer_comparisons(session, limit=limit)


@app.get("/comparison/company/{symbol}", response_model=CompanyPeerComparisonRead)
def comparison_company(symbol: str, session: SessionDep) -> CompanyPeerComparisonRead:
    try:
        return company_peer_comparison(session, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/valuation/latest", response_model=list[CompanyValuationRead])
def valuation_latest(session: SessionDep, limit: int = 100) -> list[CompanyValuationRead]:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")
    return latest_valuations(session, limit=limit)


@app.get("/valuation/company/{symbol}", response_model=CompanyValuationRead)
def valuation_company(symbol: str, session: SessionDep) -> CompanyValuationRead:
    try:
        return company_valuation(session, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/scans/latest", response_model=ScanRunRead)
def latest_scan(session: SessionDep, limit: int = 20) -> ScanRunRead:
    scan_run = session.scalar(select(ScanRun).order_by(desc(ScanRun.created_at)).limit(1))
    if not scan_run:
        return ScanRunRead(scan_run_id=0, as_of_date=datetime.now(UTC).date(), results=[])

    rows = session.execute(
        _score_select()
        .join(ScanResult, ScanResult.score_id == CompanyScore.id)
        .where(ScanResult.scan_run_id == scan_run.id)
        .order_by(ScanResult.rank)
        .limit(limit)
    )
    return ScanRunRead(
        scan_run_id=scan_run.id,
        as_of_date=scan_run.as_of_date,
        results=[ScoreRead.model_validate(row._mapping) for row in rows],
    )


@app.post("/imports/companies", response_model=ImportResult)
async def upload_companies(session: SessionDep, file: CsvUpload) -> ImportResult:
    result = import_companies(_text_file(file), session)
    return ImportResult(imported=result.imported, skipped=result.skipped, errors=result.errors)


@app.post("/imports/prices", response_model=ImportResult)
async def upload_prices(session: SessionDep, file: CsvUpload) -> ImportResult:
    result = import_prices(_text_file(file), session)
    return ImportResult(imported=result.imported, skipped=result.skipped, errors=result.errors)


@app.post("/imports/financial-statements", response_model=ImportResult)
async def upload_financials(session: SessionDep, file: CsvUpload) -> ImportResult:
    result = import_financial_statements(_text_file(file), session)
    return ImportResult(imported=result.imported, skipped=result.skipped, errors=result.errors)


@app.post("/imports/dividends", response_model=ImportResult)
async def upload_dividends(session: SessionDep, file: CsvUpload) -> ImportResult:
    result = import_dividends(_text_file(file), session)
    return ImportResult(imported=result.imported, skipped=result.skipped, errors=result.errors)


def _text_file(upload: UploadFile):
    return (line.decode("utf-8") for line in upload.file.readlines())


def _score_select():
    return select(
        Company.symbol,
        Company.name,
        Company.sector,
        CompanyScore.as_of_date,
        CompanyScore.quality_score,
        CompanyScore.growth_score,
        CompanyScore.valuation_score,
        CompanyScore.dividend_score,
        CompanyScore.risk_score,
        CompanyScore.overall_score,
        CompanyScore.status,
        CompanyScore.reasons,
        CompanyScore.risks,
    ).join(CompanyScore, CompanyScore.company_id == Company.id)


def _portfolio_transaction_read(
    transaction: PortfolioTransaction,
    symbol: str,
) -> PortfolioTransactionRead:
    return PortfolioTransactionRead(
        id=transaction.id,
        symbol=symbol,
        transaction_date=transaction.transaction_date,
        transaction_type=transaction.transaction_type,
        quantity=transaction.quantity,
        price_per_share=transaction.price_per_share,
        fees=transaction.fees,
        cash_amount=transaction.cash_amount,
        notes=transaction.notes,
    )


def _company_by_symbol(session: Session, symbol: str | None) -> Company:
    if not symbol:
        raise HTTPException(status_code=400, detail="company symbol is required")
    company = session.scalar(select(Company).where(Company.symbol == symbol.upper()))
    if not company:
        raise HTTPException(status_code=404, detail=f"unknown company symbol {symbol.upper()}")
    return company


def _user_profile(session: Session, user_id: int) -> UserProfile:
    profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile:
        return profile
    profile = UserProfile(
        user_id=user_id,
        preferred_sectors=[],
        onboarding_completed=False,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def _user_watchlist(
    session: Session,
    user_id: int,
    name: str = "Starter Watchlist",
) -> UserWatchlist:
    watchlist = session.scalar(
        select(UserWatchlist).where(
            UserWatchlist.user_id == user_id,
            UserWatchlist.name == name,
        )
    )
    if watchlist:
        return watchlist
    watchlist = UserWatchlist(user_id=user_id, name=name)
    session.add(watchlist)
    session.commit()
    session.refresh(watchlist)
    return watchlist


def _user_portfolio_plan(
    session: Session,
    user_id: int,
    name: str = "Default Plan",
) -> UserPortfolioPlan:
    plan = session.scalar(
        select(UserPortfolioPlan).where(
            UserPortfolioPlan.user_id == user_id,
            UserPortfolioPlan.name == name,
        )
    )
    if plan:
        return plan
    plan = UserPortfolioPlan(user_id=user_id, name=name)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def _portfolio_plan_read(session: Session, plan: UserPortfolioPlan) -> UserPortfolioPlanRead:
    items = list(
        session.scalars(
            select(UserPortfolioPlanItem)
            .where(UserPortfolioPlanItem.user_portfolio_plan_id == plan.id)
            .order_by(UserPortfolioPlanItem.symbol)
        )
    )
    return UserPortfolioPlanRead(
        id=plan.id,
        name=plan.name,
        items=[UserPortfolioPlanItemRead.model_validate(item) for item in items],
    )


def _user_portfolio_transaction_read(
    transaction: UserPortfolioTransaction,
    symbol: str,
) -> PortfolioTransactionRead:
    return PortfolioTransactionRead(
        id=transaction.id,
        symbol=symbol,
        transaction_date=transaction.transaction_date,
        transaction_type=transaction.transaction_type,
        quantity=transaction.quantity,
        price_per_share=transaction.price_per_share,
        fees=transaction.fees,
        cash_amount=transaction.cash_amount,
        notes=transaction.notes,
    )


def _user_portfolio_summary(session: Session, user_id: int) -> PortfolioSummaryRead:
    rows = session.execute(
        select(UserPortfolioTransaction, Company)
        .join(Company, Company.id == UserPortfolioTransaction.company_id)
        .where(UserPortfolioTransaction.user_id == user_id)
        .order_by(UserPortfolioTransaction.transaction_date, UserPortfolioTransaction.id)
    )
    states: dict[int, dict[str, Decimal]] = {}
    companies: dict[int, Company] = {}
    for transaction, company in rows:
        companies[company.id] = company
        state = states.setdefault(
            company.id,
            {
                "quantity": Decimal(0),
                "cost_basis": Decimal(0),
                "dividends_received": Decimal(0),
            },
        )
        _apply_user_portfolio_transaction(state, transaction)

    raw_positions = []
    total_cost_basis = Decimal(0)
    total_market_value = Decimal(0)
    total_dividends = Decimal(0)
    for company_id, state in states.items():
        if state["quantity"] <= 0 and state["dividends_received"] <= 0:
            continue
        company = companies[company_id]
        latest_price = _latest_price_for_company(session, company_id)
        market_value = (
            (state["quantity"] * latest_price.close_price).quantize(Decimal("0.0001"))
            if latest_price and state["quantity"] > 0
            else None
        )
        total_cost_basis += state["cost_basis"]
        total_dividends += state["dividends_received"]
        if market_value is not None:
            total_market_value += market_value
        raw_positions.append((company, state, latest_price, market_value))

    positions: list[PortfolioPositionRead] = []
    sector_values: dict[str, Decimal] = {}
    for company, state, latest_price, market_value in raw_positions:
        unrealized = market_value - state["cost_basis"] if market_value is not None else None
        sector = company.sector or "Unknown"
        if market_value is not None:
            sector_values[sector] = sector_values.get(sector, Decimal(0)) + market_value
        positions.append(
            PortfolioPositionRead(
                symbol=company.symbol,
                name=company.name,
                sector=company.sector,
                quantity=state["quantity"],
                average_cost=_decimal_div(state["cost_basis"], state["quantity"]),
                cost_basis=state["cost_basis"],
                latest_price=latest_price.close_price if latest_price else None,
                market_value=market_value,
                unrealized_gain_loss=unrealized,
                unrealized_gain_loss_percent=_decimal_percent(unrealized, state["cost_basis"]),
                portfolio_weight=_decimal_percent(market_value, total_market_value)
                if market_value is not None
                else None,
                dividends_received=state["dividends_received"],
            )
        )

    allocation = [
        SectorAllocationRead(
            sector=sector,
            market_value=value,
            portfolio_weight=_decimal_percent(value, total_market_value) or Decimal(0),
        )
        for sector, value in sorted(sector_values.items(), key=lambda item: item[1], reverse=True)
    ]
    total_unrealized = total_market_value - total_cost_basis
    return PortfolioSummaryRead(
        total_cost_basis=total_cost_basis,
        total_market_value=total_market_value,
        total_unrealized_gain_loss=total_unrealized,
        total_unrealized_gain_loss_percent=_decimal_percent(total_unrealized, total_cost_basis),
        total_dividends_received=total_dividends,
        positions=sorted(positions, key=lambda item: item.market_value or Decimal(0), reverse=True),
        sector_allocation=allocation,
        warnings=_user_portfolio_warnings(positions, allocation),
    )


def _validate_portfolio_transaction(
    transaction_type: str,
    quantity: Decimal,
    price_per_share: Decimal | None,
    fees: Decimal,
    cash_amount: Decimal | None,
) -> None:
    tx_type = transaction_type.upper()
    if tx_type not in {"BUY", "SELL", "DIVIDEND"}:
        raise ValueError("transaction_type must be BUY, SELL, or DIVIDEND")
    if fees < 0:
        raise ValueError("fees cannot be negative")
    if tx_type in {"BUY", "SELL"}:
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero for BUY/SELL")
        if price_per_share is None or price_per_share <= 0:
            raise ValueError("price_per_share must be greater than zero for BUY/SELL")
    if tx_type == "DIVIDEND" and (cash_amount is None or cash_amount <= 0):
        raise ValueError("cash_amount must be greater than zero for DIVIDEND")


def _apply_user_portfolio_transaction(
    state: dict[str, Decimal],
    transaction: UserPortfolioTransaction,
) -> None:
    if transaction.transaction_type == "BUY":
        state["quantity"] += transaction.quantity
        state["cost_basis"] += (
            transaction.quantity * (transaction.price_per_share or Decimal(0)) + transaction.fees
        )
        return
    if transaction.transaction_type == "SELL":
        if state["quantity"] <= 0:
            return
        sold_quantity = min(transaction.quantity, state["quantity"])
        average_cost = _decimal_div(state["cost_basis"], state["quantity"]) or Decimal(0)
        state["quantity"] -= sold_quantity
        state["cost_basis"] = max(state["cost_basis"] - (sold_quantity * average_cost), Decimal(0))
        return
    if transaction.transaction_type == "DIVIDEND":
        state["dividends_received"] += transaction.cash_amount or Decimal(0)


def _latest_price_for_company(session: Session, company_id: int) -> Price | None:
    return session.scalar(
        select(Price).where(Price.company_id == company_id).order_by(desc(Price.trade_date)).limit(1)
    )


def _decimal_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal(0)):
        return None
    return (numerator / denominator).quantize(Decimal("0.0001"))


def _decimal_percent(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    value = _decimal_div(numerator, denominator)
    if value is None:
        return None
    return (value * Decimal(100)).quantize(Decimal("0.0001"))


def _user_portfolio_warnings(
    positions: list[PortfolioPositionRead],
    allocation: list[SectorAllocationRead],
) -> list[str]:
    warnings: list[str] = []
    for position in positions:
        if position.portfolio_weight is not None and position.portfolio_weight > Decimal(30):
            warnings.append(f"{position.symbol} is above 30% of portfolio value.")
        if position.latest_price is None and position.quantity > 0:
            warnings.append(f"{position.symbol} has no latest price; market value is incomplete.")
    for sector in allocation:
        if sector.portfolio_weight > Decimal(50):
            warnings.append(f"{sector.sector} exposure is above 50% of portfolio value.")
    return warnings


def _watchlist_by_id(session: Session, watchlist_id: int) -> Watchlist:
    watchlist = session.get(Watchlist, watchlist_id)
    if not watchlist:
        raise HTTPException(status_code=404, detail="watchlist not found")
    return watchlist


def _alert_rule_by_id(session: Session, rule_id: int) -> AlertRule:
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="alert rule not found")
    return rule


def _alert_event_by_id(session: Session, event_id: int) -> AlertEvent:
    event = session.get(AlertEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="alert event not found")
    return event


def _ensure_source_refs(
    session: Session,
    source_document_id: int | None,
    uploaded_report_id: int | None,
) -> None:
    if source_document_id and not session.get(SourceDocument, source_document_id):
        raise HTTPException(status_code=404, detail="source document not found")
    if uploaded_report_id and not session.get(UploadedReport, uploaded_report_id):
        raise HTTPException(status_code=404, detail="uploaded report not found")


async def _create_extraction_draft(
    session: Session,
    report_text: str,
    company_id: int | None,
    source_document_id: int | None,
    uploaded_report_id: int | None,
    notes: str | None,
) -> ExtractionDraft:
    selected_text, selection_warnings = select_financial_section(report_text)
    try:
        raw_response, parsed = await extract_financial_statement(selected_text)
    except DeepSeekError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    draft_notes = "\n".join(
        item
        for item in [
            notes,
            "Financial-section selector: " + " ".join(selection_warnings),
        ]
        if item
    )
    draft = ExtractionDraft(
        company_id=company_id,
        source_document_id=source_document_id,
        uploaded_report_id=uploaded_report_id,
        extraction_type="financial_statement",
        provider="deepseek",
        model=settings.deepseek_model,
        prompt_text=selected_text[:60000],
        raw_response=raw_response,
        parsed_data=parsed,
        status="draft",
        notes=draft_notes,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def _latest_report_text(session: Session, report_id: int) -> ReportTextExtraction:
    extraction = session.scalar(
        select(ReportTextExtraction)
        .where(ReportTextExtraction.uploaded_report_id == report_id)
        .order_by(desc(ReportTextExtraction.created_at))
        .limit(1)
    )
    if not extraction:
        raise HTTPException(
            status_code=404,
            detail="report text has not been extracted yet; call /reports/{report_id}/extract-text first",
        )
    return extraction


def _delete_uploaded_file(stored_path: str) -> None:
    try:
        Path(stored_path).unlink(missing_ok=True)
    except OSError:
        pass


def _text_extraction_read(extraction: ReportTextExtraction) -> ReportTextExtractionRead:
    return ReportTextExtractionRead(
        id=extraction.id,
        uploaded_report_id=extraction.uploaded_report_id,
        extraction_method=extraction.extraction_method,
        page_count=extraction.page_count,
        character_count=extraction.character_count,
        status=extraction.status,
        warnings=extraction.warnings,
        text_preview=extraction.text[:1200],
    )


def _text_extraction_full_read(extraction: ReportTextExtraction) -> ReportTextExtractionFullRead:
    return ReportTextExtractionFullRead(
        **_text_extraction_read(extraction).model_dump(),
        text=extraction.text,
    )


def _financial_statement_from_draft(session: Session, draft: ExtractionDraft) -> dict:
    parsed = draft.parsed_data or {}
    symbol = parsed.get("symbol")
    if draft.company_id:
        company_id = draft.company_id
    elif symbol:
        company = _company_by_symbol(session, symbol)
        company_id = company.id
    else:
        raise HTTPException(status_code=400, detail="draft has no linked company or extracted symbol")

    period_end = parsed.get("period_end")
    period_type = parsed.get("period_type")
    if not period_end or not period_type:
        raise HTTPException(status_code=400, detail="draft is missing period_end or period_type")

    return {
        "company_id": company_id,
        "period_end": _parse_iso_date(period_end, "period_end"),
        "period_type": period_type.upper(),
        "currency": parsed.get("currency") or "NGN",
        "revenue": _optional_decimal(parsed.get("revenue"), "revenue"),
        "profit_after_tax": _optional_decimal(parsed.get("profit_after_tax"), "profit_after_tax"),
        "total_assets": _optional_decimal(parsed.get("total_assets"), "total_assets"),
        "total_liabilities": _optional_decimal(parsed.get("total_liabilities"), "total_liabilities"),
        "total_equity": _optional_decimal(parsed.get("total_equity"), "total_equity"),
        "cash_flow_operations": _optional_decimal(
            parsed.get("cash_flow_operations"), "cash_flow_operations"
        ),
        "eps": _optional_decimal(parsed.get("eps"), "eps"),
        "source_document_id": draft.source_document_id,
    }


def _parse_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD") from exc


def _optional_decimal(value, field_name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be numeric") from exc


def _pending_items(session: Session, record_type: str, limit: int) -> list[PendingReviewItem]:
    if record_type == "prices":
        rows = session.execute(
            select(
                Price.id.label("record_id"),
                Company.symbol,
                Price.trade_date,
                Price.close_price,
                SourceDocument.name.label("source_name"),
                SourceDocument.url.label("source_url"),
            )
            .join(Company, Company.id == Price.company_id)
            .outerjoin(SourceDocument, SourceDocument.id == Price.source_document_id)
            .where(Price.reviewed.is_(False))
            .order_by(desc(Price.trade_date), Company.symbol)
            .limit(limit)
        )
        return [
            PendingReviewItem(
                record_type=record_type,
                record_id=row.record_id,
                symbol=row.symbol,
                summary=f"{row.trade_date}: close price {row.close_price}",
                source_name=row.source_name,
                source_url=row.source_url,
            )
            for row in rows
        ]

    if record_type == "financial_statements":
        rows = session.execute(
            select(
                FinancialStatement.id.label("record_id"),
                Company.symbol,
                FinancialStatement.period_end,
                FinancialStatement.period_type,
                FinancialStatement.revenue,
                FinancialStatement.profit_after_tax,
                SourceDocument.name.label("source_name"),
                SourceDocument.url.label("source_url"),
            )
            .join(Company, Company.id == FinancialStatement.company_id)
            .outerjoin(SourceDocument, SourceDocument.id == FinancialStatement.source_document_id)
            .where(FinancialStatement.reviewed.is_(False))
            .order_by(desc(FinancialStatement.period_end), Company.symbol)
            .limit(limit)
        )
        return [
            PendingReviewItem(
                record_type=record_type,
                record_id=row.record_id,
                symbol=row.symbol,
                summary=(
                    f"{row.period_end} {row.period_type}: "
                    f"revenue {row.revenue}, PAT {row.profit_after_tax}"
                ),
                source_name=row.source_name,
                source_url=row.source_url,
            )
            for row in rows
        ]

    if record_type == "dividends":
        rows = session.execute(
            select(
                Dividend.id.label("record_id"),
                Company.symbol,
                Dividend.declared_date,
                Dividend.ex_dividend_date,
                Dividend.payment_date,
                Dividend.amount_per_share,
                SourceDocument.name.label("source_name"),
                SourceDocument.url.label("source_url"),
            )
            .join(Company, Company.id == Dividend.company_id)
            .outerjoin(SourceDocument, SourceDocument.id == Dividend.source_document_id)
            .where(Dividend.reviewed.is_(False))
            .order_by(desc(Dividend.declared_date), Company.symbol)
            .limit(limit)
        )
        return [
            PendingReviewItem(
                record_type=record_type,
                record_id=row.record_id,
                symbol=row.symbol,
                summary=(
                    f"declared {row.declared_date}, ex {row.ex_dividend_date}, "
                    f"paid {row.payment_date}: {row.amount_per_share}"
                ),
                source_name=row.source_name,
                source_url=row.source_url,
            )
            for row in rows
        ]

    raise HTTPException(status_code=400, detail="unsupported record type")


def _set_review_status(
    session: Session,
    record_type: str,
    record_id: int,
    reviewed: bool,
    action: str,
    payload: ReviewAction | None,
) -> ReviewResult:
    model = _review_model(record_type)
    record = session.get(model, record_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"{record_type} record not found")

    record.reviewed = reviewed
    session.add(
        DataReviewLog(
            record_type=record_type,
            record_id=record_id,
            action=action,
            notes=payload.notes if payload else None,
        )
    )
    session.commit()
    return ReviewResult(
        record_type=record_type,
        record_id=record_id,
        action=action,
        reviewed=reviewed,
    )


def _review_model(record_type: str):
    models = {
        "prices": Price,
        "financial_statements": FinancialStatement,
        "dividends": Dividend,
    }
    model = models.get(record_type)
    if not model:
        raise HTTPException(
            status_code=400,
            detail=f"record_type must be one of {tuple(models)}",
        )
    return model
