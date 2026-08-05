import argparse
import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from ngx_research.config import settings
from ngx_research.database import SessionLocal, init_db
from ngx_research.models import Company, SourceDocument
from ngx_research.services.alerts import evaluate_alert_rules
from ngx_research.services.intelligence_engine import run_intelligence_engine
from ngx_research.services.ngxpulse_client import (
    NgxPulseError,
    sync_all_dividend_histories,
    sync_all_stocks,
    sync_bond_auctions,
    sync_bonds,
    sync_disclosures,
    sync_etfs,
    sync_fundamentals,
    sync_indices,
    sync_market_news,
    sync_nasd_otc_stocks,
    sync_symbol_prices,
)
from ngx_research.services.peer_comparison_engine import run_peer_comparison_engine
from ngx_research.services.public_errors import public_error_message
from ngx_research.services.scanner import run_market_scan
from ngx_research.services.valuation_engine import run_valuation_engine

VALID_SYNC_MODES = {"daily", "full"}
DAILY_SYNC_STEP_LABELS = ("stocks", "fundamentals", "disclosures", "market-news")
FULL_SYNC_STEP_LABELS = (
    "stocks",
    "fundamentals",
    "disclosures",
    "indices",
    "etfs",
    "bonds",
    "bond-auctions",
    "nasd-otc-stocks",
    "market-news",
)


async def daily_market_sync(days: int = 2, symbols: list[str] | None = None) -> int:
    init_db()
    with SessionLocal() as session:
        if symbols:
            for symbol in symbols:
                result = await sync_symbol_prices(session, symbol=symbol, days=days)
                _print_sync_result(f"prices:{symbol.upper()}", result)
        else:
            result = await sync_all_stocks(session)
            _print_sync_result("stocks", result)

        scan = run_market_scan(session)
        print(
            "scan "
            f"run_id={scan.scan_run_id} scored={scan.scored} "
            f"insufficient_data={scan.insufficient_data}"
        )

        alerts = evaluate_alert_rules(session)
        print(
            "alerts "
            f"evaluated={alerts.evaluated_rules} triggered={alerts.triggered}"
        )
    return 0


async def full_market_research_sync(
    *,
    sync_mode: str = "full",
    include_dividends: bool | None = None,
    dividend_symbols: list[str] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    init_db()
    normalized_mode = _normalize_sync_mode(sync_mode)
    include_dividends = _include_dividends_for_mode(normalized_mode, include_dividends)
    totals: dict[str, Any] = {
        "sync_mode": normalized_mode,
        "steps": 0,
        "imported": 0,
        "updated_prices": 0,
        "updated_companies": 0,
        "skipped": 0,
        "errors": 0,
        "dividend_symbols": 0,
        "scan_run_id": 0,
        "scored": 0,
        "insufficient_data": 0,
        "intelligence_generated": 0,
        "valuations_generated": 0,
        "comparisons_generated": 0,
        "alerts_evaluated": 0,
        "alerts_triggered": 0,
    }

    with SessionLocal() as session:
        print(f"sync_mode={normalized_mode} dividends_enabled={include_dividends}")
        sync_steps = _sync_step_factories(session, normalized_mode)
        for label, step_factory in sync_steps:
            _report_progress(progress_callback, label)
            try:
                result = await step_factory()
            except NgxPulseError as exc:
                totals["steps"] += 1
                totals["errors"] += 1
                print(f"sync_error:{label} {public_error_message(exc, action=f'sync {label}')}")
                continue
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                totals["steps"] += 1
                totals["errors"] += 1
                print(f"sync_error:{label} {public_error_message(exc, action=f'sync {label}')}")
                continue
            _print_sync_result(label, result)
            _add_sync_result(totals, result)

        if include_dividends:
            symbols = dividend_symbols or _dividend_symbols_due(session)
            _report_progress(progress_callback, "dividends", 0, len(symbols))
            try:
                result = await sync_all_dividend_histories(
                    session,
                    symbols=symbols,
                    pause_seconds=settings.ngxpulse_request_pause_seconds,
                    progress_callback=progress_callback,
                )
            except NgxPulseError as exc:
                totals["steps"] += 1
                totals["errors"] += 1
                print(f"sync_error:dividends {public_error_message(exc, action='sync dividends')}")
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                totals["steps"] += 1
                totals["errors"] += 1
                print(f"sync_error:dividends {public_error_message(exc, action='sync dividends')}")
            else:
                _print_sync_result("dividends", result)
                _add_sync_result(totals, result)
                totals["dividend_symbols"] = len(symbols)

        _release_session_connection(session)

        _report_progress(progress_callback, "scan")
        scan = _run_db_stage(session, "scan", lambda: run_market_scan(session))
        totals["scan_run_id"] = scan.scan_run_id
        totals["scored"] = scan.scored
        totals["insufficient_data"] = scan.insufficient_data
        print(
            "scan "
            f"run_id={scan.scan_run_id} scored={scan.scored} "
            f"insufficient_data={scan.insufficient_data}"
        )

        _report_progress(progress_callback, "intelligence")
        intelligence = _run_db_stage(
            session, "intelligence", lambda: run_intelligence_engine(session, limit=100)
        )
        totals["intelligence_generated"] = intelligence.generated
        print(f"intelligence generated={intelligence.generated}")

        _report_progress(progress_callback, "valuation")
        valuation = _run_db_stage(
            session,
            "valuation",
            lambda: run_valuation_engine(session, as_of_date=intelligence.as_of_date, limit=100),
        )
        totals["valuations_generated"] = valuation.generated
        print(f"valuation generated={valuation.generated}")

        _report_progress(progress_callback, "peer-comparison")
        comparison = _run_db_stage(
            session,
            "peer-comparison",
            lambda: run_peer_comparison_engine(
                session, as_of_date=intelligence.as_of_date, limit=100
            ),
        )
        totals["comparisons_generated"] = comparison.generated
        print(f"peer_comparison generated={comparison.generated}")

        _report_progress(progress_callback, "alerts")
        alerts = _run_db_stage(session, "alerts", lambda: evaluate_alert_rules(session))
        totals["alerts_evaluated"] = alerts.evaluated_rules
        totals["alerts_triggered"] = alerts.triggered
        print(
            "alerts "
            f"evaluated={alerts.evaluated_rules} triggered={alerts.triggered}"
        )

    return totals


def _print_sync_result(label: str, result) -> None:
    print(
        f"sync:{label} "
        f"endpoint={result.endpoint} imported={result.imported} "
        f"updated_prices={result.updated_prices} "
        f"updated_companies={result.updated_companies} "
        f"skipped={result.skipped} errors={len(result.errors)}"
    )
    for error in result.errors:
        print(f"sync_error:{label} {error}")


def _add_sync_result(totals: dict[str, Any], result) -> None:
    totals["steps"] += 1
    totals["imported"] += result.imported
    totals["updated_prices"] += result.updated_prices
    totals["updated_companies"] += result.updated_companies
    totals["skipped"] += result.skipped
    totals["errors"] += len(result.errors)


def _report_progress(progress_callback, step: str, current: int | None = None, total: int | None = None) -> None:
    if progress_callback:
        progress_callback(step, current, total)


def _active_company_symbols(session) -> list[str]:
    return list(
        session.scalars(
            select(Company.symbol).where(Company.is_active.is_(True)).order_by(Company.symbol)
        )
    )


def _normalize_sync_mode(sync_mode: str | None) -> str:
    normalized = (sync_mode or "full").strip().lower()
    if normalized not in VALID_SYNC_MODES:
        raise ValueError("sync mode must be 'daily' or 'full'")
    return normalized


def _include_dividends_for_mode(sync_mode: str, include_dividends: bool | None) -> bool:
    if include_dividends is not None:
        return include_dividends
    if sync_mode == "daily":
        return settings.automation_daily_dividend_sync_enabled
    return settings.automation_dividend_sync_enabled


def _sync_step_labels_for_mode(sync_mode: str) -> tuple[str, ...]:
    return DAILY_SYNC_STEP_LABELS if sync_mode == "daily" else FULL_SYNC_STEP_LABELS


def _sync_step_factories(session, sync_mode: str):
    factories = {
        "stocks": lambda: sync_all_stocks(session),
        "fundamentals": lambda: sync_fundamentals(session),
        "disclosures": lambda: sync_disclosures(session, limit=50),
        "indices": lambda: sync_indices(session),
        "etfs": lambda: sync_etfs(session),
        "bonds": lambda: sync_bonds(session),
        "bond-auctions": lambda: sync_bond_auctions(session, limit=50),
        "nasd-otc-stocks": lambda: sync_nasd_otc_stocks(session),
        "market-news": lambda: sync_market_news(session, limit=50),
    }
    return [(label, factories[label]) for label in _sync_step_labels_for_mode(sync_mode)]


def _dividend_symbols_due(session) -> list[str]:
    symbols = _active_company_symbols(session)
    if not symbols:
        return []

    today = datetime.now(UTC).date().isoformat()
    source_name = f"NGX Pulse API {today}"
    source_url_prefix = f"{settings.ngxpulse_base_url.rstrip('/')}/api/ngxdata/dividends/"
    synced_urls = session.scalars(
        select(SourceDocument.url).where(
            SourceDocument.document_type == "ngxpulse_market_data",
            SourceDocument.name == source_name,
            SourceDocument.url.like(f"{source_url_prefix}%"),
        )
    )
    synced_symbols = {
        str(url).rsplit("/", 1)[-1].strip().upper()
        for url in synced_urls
        if url
    }
    return [symbol for symbol in symbols if symbol.strip().upper() not in synced_symbols]


def _release_session_connection(session) -> None:
    session.close()


def _run_db_stage(session, label: str, action):
    try:
        return action()
    except OperationalError as exc:
        session.rollback()
        session.close()
        print(f"db_retry:{label} {public_error_message(exc, action=f'run {label}')}")
        return action()


def main() -> int:
    parser = argparse.ArgumentParser(prog="equitykobo-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser("daily-market", help="sync NGX Pulse market data and refresh scans")
    daily.add_argument(
        "--days",
        type=int,
        default=2,
        help="recent trading days to sync when symbols are provided",
    )
    daily.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="sync a specific symbol; repeat for multiple symbols",
    )
    full = subparsers.add_parser(
        "full-market",
        help="sync all NGX Pulse Starter feeds, dividend histories, scans, and alerts",
    )
    full.add_argument(
        "--mode",
        choices=sorted(VALID_SYNC_MODES),
        default="full",
        help="daily syncs only core equity feeds; full syncs all Starter feeds",
    )
    full.add_argument(
        "--skip-dividends",
        action="store_true",
        help="skip per-company dividend history sync",
    )
    full.add_argument(
        "--dividend-symbol",
        action="append",
        default=[],
        help="limit dividend sync to a specific symbol; repeat for multiple symbols",
    )

    args = parser.parse_args()
    started_at = datetime.now(UTC).isoformat()
    print(f"equitykobo_sync_started command={args.command} at={started_at}")

    try:
        if args.command == "daily-market":
            return asyncio.run(daily_market_sync(days=args.days, symbols=args.symbol))
        if args.command == "full-market":
            asyncio.run(
                full_market_research_sync(
                    sync_mode=args.mode,
                    include_dividends=False if args.skip_dividends else None,
                    dividend_symbols=args.dividend_symbol,
                )
            )
            return 0
    except NgxPulseError as exc:
        print(f"equitykobo_sync_failed reason={public_error_message(exc, action='sync market data')}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"equitykobo_sync_failed reason={public_error_message(exc, action='run market sync')}")
        return 1

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
