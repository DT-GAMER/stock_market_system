from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ngx_research.config import settings
from ngx_research.models import Company, Price, SourceDocument
from ngx_research.schemas import NgxPulseSyncResult
from ngx_research.services.source_trust import is_trusted_document_type

NGXPULSE_DOCUMENT_TYPE = "ngxpulse_market_data"


class NgxPulseError(Exception):
    pass


async def fetch_market_overview() -> dict[str, Any]:
    return await _request_json("/api/ngxdata/market")


async def sync_all_stocks(session: Session, trade_date: date | None = None) -> NgxPulseSyncResult:
    payload = await _request_json("/api/ngxdata/stocks")
    rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("stocks") or []
    if not isinstance(rows, list):
        raise NgxPulseError("unexpected NGX Pulse stocks response shape")
    return _sync_stock_rows(
        session=session,
        rows=rows,
        endpoint="/api/ngxdata/stocks",
        trade_date=trade_date or datetime.now(UTC).date(),
    )


async def sync_symbol_prices(
    session: Session,
    symbol: str,
    days: int | None = None,
    trade_date: date | None = None,
) -> NgxPulseSyncResult:
    path = f"/api/ngxdata/prices/{symbol.upper()}"
    params = {"days": days} if days else None
    payload = await _request_json(path, params=params)
    rows = _price_payload_rows(payload)
    return _sync_stock_rows(
        session=session,
        rows=rows,
        endpoint=path,
        trade_date=trade_date or datetime.now(UTC).date(),
    )


async def _request_json(path: str, params: dict[str, Any] | None = None) -> Any:
    if not settings.ngxpulse_api_key:
        raise NgxPulseError("NGXPULSE_API_KEY is not configured")
    url = f"{settings.ngxpulse_base_url.rstrip('/')}{path}"
    headers = {"X-API-Key": settings.ngxpulse_api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers=headers, params=params)
    if response.status_code == 429:
        raise NgxPulseError("NGX Pulse rate limit exceeded")
    if response.status_code >= 400:
        raise NgxPulseError(f"NGX Pulse request failed with status {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        preview = response.text[:120].replace("\n", " ")
        raise NgxPulseError(f"NGX Pulse returned a non-JSON response: {preview}") from exc


def _price_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise NgxPulseError("unexpected NGX Pulse price response shape")
    for key in ("history", "prices", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return [payload]


def _sync_stock_rows(
    session: Session,
    rows: list[dict[str, Any]],
    endpoint: str,
    trade_date: date,
) -> NgxPulseSyncResult:
    source = _get_or_create_source(
        session=session,
        endpoint=endpoint,
        trade_date=trade_date,
    )

    imported = 0
    updated_prices = 0
    updated_companies = 0
    skipped = 0
    errors: list[str] = []
    parsed_rows: list[dict[str, Any]] = []

    for row in rows:
        try:
            symbol = _symbol(row)
            row_trade_date = _trade_date(row) or trade_date
            parsed_rows.append(
                {
                    "symbol": symbol,
                    "name": str(row.get("name") or symbol),
                    "sector": str(row["sector"]) if row.get("sector") else None,
                    "trade_date": row_trade_date,
                    "price_values": {
                        "close_price": _decimal_field(
                            row,
                            "current_price",
                            "close",
                            "close_price",
                            "price",
                            required=True,
                        ),
                        "open_price": _decimal_field(row, "open", "open_price"),
                        "high_price": _decimal_field(row, "high", "high_price"),
                        "low_price": _decimal_field(row, "low", "low_price"),
                        "volume": _int_field(row, "volume"),
                        "value_traded": _decimal_field(row, "value", "value_traded"),
                        "source_document_id": source.id,
                        "reviewed": is_trusted_document_type(source.document_type),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))

    if not parsed_rows:
        return NgxPulseSyncResult(
            endpoint=endpoint,
            imported=imported,
            updated_prices=updated_prices,
            updated_companies=updated_companies,
            skipped=skipped,
            errors=errors,
        )

    symbols = sorted({str(row["symbol"]) for row in parsed_rows})
    companies = {
        company.symbol: company
        for company in session.scalars(select(Company).where(Company.symbol.in_(symbols)))
    }
    for row in parsed_rows:
        symbol = str(row["symbol"])
        company = companies.get(symbol)
        if company:
            company.name = str(row["name"]) or company.name
            company.sector = str(row["sector"]) if row.get("sector") else company.sector
        else:
            company = Company(
                symbol=symbol,
                name=str(row["name"]),
                sector=str(row["sector"]) if row.get("sector") else None,
            )
            session.add(company)
            companies[symbol] = company
        updated_companies += 1
    session.commit()

    company_ids = [companies[str(row["symbol"])].id for row in parsed_rows]
    trade_dates = sorted({row["trade_date"] for row in parsed_rows})
    existing_prices = {
        (price.company_id, price.trade_date): price
        for price in session.scalars(
            select(Price).where(
                Price.company_id.in_(company_ids),
                Price.trade_date.in_(trade_dates),
            )
        )
    }

    for row in parsed_rows:
        company = companies[str(row["symbol"])]
        row_trade_date = row["trade_date"]
        price_values = row["price_values"]
        existing_price = existing_prices.get((company.id, row_trade_date))
        if existing_price:
            if _price_matches(existing_price, price_values):
                skipped += 1
            else:
                _update_price(existing_price, price_values)
                updated_prices += 1
            continue

        price = Price(company_id=company.id, trade_date=row_trade_date, **price_values)
        session.add(price)
        imported += 1
    session.commit()

    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_prices=updated_prices,
        updated_companies=updated_companies,
        skipped=skipped,
        errors=errors,
    )


def _get_or_create_source(session: Session, endpoint: str, trade_date: date) -> SourceDocument:
    source_url = f"{settings.ngxpulse_base_url.rstrip('/')}{endpoint}"
    source_name = f"NGX Pulse API {trade_date.isoformat()}"
    source = session.scalar(
        select(SourceDocument).where(
            SourceDocument.name == source_name,
            SourceDocument.url == source_url,
            SourceDocument.document_type == NGXPULSE_DOCUMENT_TYPE,
        )
    )
    if source:
        return source

    source = SourceDocument(
        name=source_name,
        url=source_url,
        document_type=NGXPULSE_DOCUMENT_TYPE,
        notes="Imported via NGX Pulse API. Treated as trusted market data for daily prices.",
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _price_matches(price: Price, values: dict[str, Any]) -> bool:
    return all(getattr(price, key) == value for key, value in values.items())


def _update_price(price: Price, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(price, key, value)


def _upsert_company(session: Session, row: dict[str, Any], symbol: str) -> Company:
    company = session.scalar(select(Company).where(Company.symbol == symbol))
    name = str(row.get("name") or symbol)
    sector = row.get("sector")
    if company:
        company.name = name or company.name
        company.sector = str(sector) if sector else company.sector
        return company
    company = Company(symbol=symbol, name=name, sector=str(sector) if sector else None)
    session.add(company)
    session.flush()
    return company


def _symbol(row: dict[str, Any]) -> str:
    value = row.get("symbol") or row.get("ticker")
    if not value:
        raise ValueError("NGX Pulse row missing symbol")
    return str(value).upper()


def _trade_date(row: dict[str, Any]) -> date | None:
    raw = row.get("date") or row.get("trade_date") or row.get("latest_quote_date")
    if not raw:
        return None
    return date.fromisoformat(str(raw)[:10])


def _decimal_field(
    row: dict[str, Any],
    *keys: str,
    required: bool = False,
) -> Decimal | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            try:
                return Decimal(str(value).replace(",", ""))
            except InvalidOperation as exc:
                raise ValueError(f"{key} must be numeric") from exc
    if required:
        raise ValueError(f"missing required numeric field: {'/'.join(keys)}")
    return None


def _int_field(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return int(str(value).replace(",", ""))
    return None
