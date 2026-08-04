import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from ngx_research.config import settings
from ngx_research.database import SessionLocal, init_db
from ngx_research.models import Company
from ngx_research.services.alerts import evaluate_alert_rules
from ngx_research.services.intelligence_engine import run_intelligence_engine
from ngx_research.services.ngxpulse_client import (
    NgxPulseError,
    sync_all_stocks,
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
from ngx_research.services.peer_comparison_engine import run_peer_comparison_engine
from ngx_research.services.scanner import run_market_scan
from ngx_research.services.valuation_engine import run_valuation_engine


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
    include_dividends: bool | None = None,
    dividend_symbols: list[str] | None = None,
    progress_callback=None,
) -> dict[str, int]:
    init_db()
    include_dividends = (
        settings.automation_dividend_sync_enabled if include_dividends is None else include_dividends
    )
    totals = {
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
        sync_steps = [
            ("stocks", lambda: sync_all_stocks(session)),
            ("fundamentals", lambda: sync_fundamentals(session)),
            ("disclosures", lambda: sync_disclosures(session, limit=50)),
            ("indices", lambda: sync_indices(session)),
            ("etfs", lambda: sync_etfs(session)),
            ("bonds", lambda: sync_bonds(session)),
            ("bond-auctions", lambda: sync_bond_auctions(session, limit=50)),
            ("nasd-otc-stocks", lambda: sync_nasd_otc_stocks(session)),
            ("market-news", lambda: sync_market_news(session, limit=50)),
        ]
        for label, step_factory in sync_steps:
            _report_progress(progress_callback, label)
            try:
                result = await step_factory()
            except NgxPulseError as exc:
                totals["steps"] += 1
                totals["errors"] += 1
                print(f"sync_error:{label} {exc}")
                continue
            _print_sync_result(label, result)
            _add_sync_result(totals, result)

        _report_progress(progress_callback, "scan")
        scan = run_market_scan(session)
        totals["scan_run_id"] = scan.scan_run_id
        totals["scored"] = scan.scored
        totals["insufficient_data"] = scan.insufficient_data
        print(
            "scan "
            f"run_id={scan.scan_run_id} scored={scan.scored} "
            f"insufficient_data={scan.insufficient_data}"
        )

        _report_progress(progress_callback, "intelligence")
        intelligence = run_intelligence_engine(session, limit=100)
        totals["intelligence_generated"] = intelligence.generated
        print(f"intelligence generated={intelligence.generated}")

        _report_progress(progress_callback, "valuation")
        valuation = run_valuation_engine(session, as_of_date=intelligence.as_of_date, limit=100)
        totals["valuations_generated"] = valuation.generated
        print(f"valuation generated={valuation.generated}")

        _report_progress(progress_callback, "peer-comparison")
        comparison = run_peer_comparison_engine(session, as_of_date=intelligence.as_of_date, limit=100)
        totals["comparisons_generated"] = comparison.generated
        print(f"peer_comparison generated={comparison.generated}")

        _report_progress(progress_callback, "alerts")
        alerts = evaluate_alert_rules(session)
        totals["alerts_evaluated"] = alerts.evaluated_rules
        totals["alerts_triggered"] = alerts.triggered
        print(
            "alerts "
            f"evaluated={alerts.evaluated_rules} triggered={alerts.triggered}"
        )

        if include_dividends:
            symbols = dividend_symbols or _active_company_symbols(session)
            for index, symbol in enumerate(symbols, start=1):
                _report_progress(progress_callback, f"dividends:{symbol}", index, len(symbols))
                try:
                    result = await sync_dividend_history(session, symbol)
                except NgxPulseError as exc:
                    totals["errors"] += 1
                    print(f"sync_error:dividends:{symbol} {exc}")
                    await asyncio.sleep(settings.ngxpulse_request_pause_seconds)
                    continue
                _print_sync_result(f"dividends:{symbol}", result)
                _add_sync_result(totals, result)
                totals["dividend_symbols"] += 1
                await asyncio.sleep(settings.ngxpulse_request_pause_seconds)
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


def _add_sync_result(totals: dict[str, int], result) -> None:
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
                    include_dividends=not args.skip_dividends,
                    dividend_symbols=args.dividend_symbol,
                )
            )
            return 0
    except NgxPulseError as exc:
        print(f"equitykobo_sync_failed reason={exc}")
        return 1

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
