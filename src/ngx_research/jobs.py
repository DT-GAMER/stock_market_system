import argparse
import asyncio
from datetime import UTC, datetime

from ngx_research.database import SessionLocal, init_db
from ngx_research.services.alerts import evaluate_alert_rules
from ngx_research.services.ngxpulse_client import NgxPulseError, sync_all_stocks, sync_symbol_prices
from ngx_research.services.scanner import run_market_scan


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

    args = parser.parse_args()
    started_at = datetime.now(UTC).isoformat()
    print(f"equitykobo_sync_started command={args.command} at={started_at}")

    try:
        if args.command == "daily-market":
            return asyncio.run(daily_market_sync(days=args.days, symbols=args.symbol))
    except NgxPulseError as exc:
        print(f"equitykobo_sync_failed reason={exc}")
        return 1

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
