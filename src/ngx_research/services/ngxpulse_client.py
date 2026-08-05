import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ngx_research.config import settings
from ngx_research.models import (
    BondAuctionSnapshot,
    BondSnapshot,
    Company,
    CompanyRatio,
    CorporateDisclosure,
    Dividend,
    EtfSnapshot,
    MarketIndexSnapshot,
    MarketNewsItem,
    MarketStatusSnapshot,
    NasdOtcStockSnapshot,
    NgxPulseFundamental,
    Price,
    SourceDocument,
)
from ngx_research.schemas import NgxPulseSyncResult
from ngx_research.services.source_trust import is_trusted_document_type

NGXPULSE_DOCUMENT_TYPE = "ngxpulse_market_data"


class NgxPulseError(Exception):
    pass


async def fetch_market_overview() -> dict[str, Any]:
    return await _request_json("/api/ngxdata/market")


async def fetch_market_status(session: Session) -> dict[str, Any]:
    endpoint = "/api/ngxdata/market-status"
    payload = await _request_json(endpoint)
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    snapshot = MarketStatusSnapshot(
        status=str(payload.get("status") or "unknown"),
        message=str(payload["message"]) if payload.get("message") else None,
        provider_timestamp=_optional_datetime(payload.get("timestamp")),
        raw_payload=payload if isinstance(payload, dict) else {"payload": payload},
        source_document_id=source.id,
    )
    session.add(snapshot)
    session.commit()
    return payload


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


async def sync_fundamentals(
    session: Session,
    symbols: list[str] | None = None,
    as_of_date: date | None = None,
) -> NgxPulseSyncResult:
    endpoint = "/api/ngxdata/fundamentals"
    params = {"symbols": ",".join(symbol.upper() for symbol in symbols)} if symbols else None
    payload = await _request_json(endpoint, params=params)
    rows = _fundamental_payload_rows(payload)
    source = _get_or_create_source(session, endpoint, as_of_date or datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_fundamental(session, row, source, as_of_date)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
    )


async def sync_dividend_history(session: Session, symbol: str) -> NgxPulseSyncResult:
    normalized = symbol.upper()
    endpoint = f"/api/ngxdata/dividends/{normalized}"
    payload = await _request_json(endpoint)
    rows = _payload_rows(payload, "dividends", "history", "data")
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    company = _upsert_company(session, {"symbol": normalized, "name": normalized}, normalized)
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_dividend(session, company, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
    )


async def sync_all_dividend_histories(
    session: Session,
    *,
    symbols: list[str] | None = None,
    limit: int | None = None,
    pause_seconds: float = 0,
    progress_callback=None,
) -> NgxPulseSyncResult:
    active_symbols = symbols or list(
        session.scalars(
            select(Company.symbol).where(Company.is_active.is_(True)).order_by(Company.symbol)
        )
    )
    if limit is not None:
        active_symbols = active_symbols[:limit]

    imported = updated = skipped = 0
    errors: list[str] = []
    total = len(active_symbols)
    for index, symbol in enumerate(active_symbols, start=1):
        normalized = symbol.strip().upper()
        if not normalized:
            skipped += 1
            continue
        if progress_callback:
            progress_callback(f"dividends:{normalized}", index, total)
        try:
            result = await sync_dividend_history(session, normalized)
        except NgxPulseError as exc:
            skipped += 1
            errors.append(f"{normalized}: {exc}")
        else:
            imported += result.imported
            updated += result.updated_companies
            skipped += result.skipped
            errors.extend(f"{normalized}: {error}" for error in result.errors)
        if pause_seconds > 0 and index < total:
            await asyncio.sleep(pause_seconds)

    return NgxPulseSyncResult(
        endpoint="/api/ngxdata/dividends/*",
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
    )


async def sync_disclosures(session: Session, limit: int | None = None) -> NgxPulseSyncResult:
    endpoint = "/api/ngxdata/disclosures"
    payload = await _request_json(endpoint)
    rows = _payload_rows(payload, "disclosures", "data", "items")
    if limit:
        rows = rows[:limit]
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_disclosure(session, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(endpoint=endpoint, imported=imported, updated_companies=updated, skipped=skipped, errors=errors)


async def sync_indices(session: Session) -> NgxPulseSyncResult:
    endpoint = "/api/ngxdata/indices"
    payload = await _request_json(endpoint)
    rows = _payload_rows(payload, "data", "indices")
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_index(session, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(endpoint=endpoint, imported=imported, updated_companies=updated, skipped=skipped, errors=errors)


async def sync_etfs(session: Session) -> NgxPulseSyncResult:
    endpoint = "/api/ngxdata/etfs"
    payload = await _request_json(endpoint)
    rows = _payload_rows(payload, "data", "etfs", "funds")
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_etf(session, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
    )


async def sync_bonds(session: Session) -> NgxPulseSyncResult:
    endpoint = "/api/ngxdata/bonds"
    payload = await _request_json(endpoint)
    rows = _payload_rows(payload, "data", "bonds")
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_bond(session, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
    )


async def sync_bond_auctions(session: Session, limit: int | None = None) -> NgxPulseSyncResult:
    endpoint = "/api/ngxdata/bonds/auctions"
    params = {"limit": limit} if limit else None
    payload = await _request_json(endpoint, params=params)
    rows = _payload_rows(payload, "data", "auctions")
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_bond_auction(session, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
    )


async def sync_nasd_otc_stocks(session: Session) -> NgxPulseSyncResult:
    endpoint = "/api/nasddata/stocks"
    payload = await _request_json(endpoint)
    rows = _payload_rows(payload, "data", "stocks", "securities")
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_nasd_otc_stock(session, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
    )


async def sync_market_news(session: Session, limit: int | None = None) -> NgxPulseSyncResult:
    endpoint = "/api/news"
    params = {"limit": limit} if limit else None
    payload = await _request_json(endpoint, params=params)
    rows = _payload_rows(payload, "data", "news", "items", "articles")
    source = _get_or_create_source(session, endpoint, datetime.now(UTC).date())
    imported = updated = skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            was_created = _upsert_market_news(session, row, source)
            imported += int(was_created)
            updated += int(not was_created)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(str(exc))
    session.commit()
    return NgxPulseSyncResult(
        endpoint=endpoint,
        imported=imported,
        updated_companies=updated,
        skipped=skipped,
        errors=errors,
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


def _payload_rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        raise NgxPulseError("unexpected NGX Pulse response shape")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return [payload]


def _fundamental_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        raise NgxPulseError("unexpected NGX Pulse fundamentals response shape")
    fundamentals = payload.get("fundamentals")
    if isinstance(fundamentals, list):
        return [row for row in fundamentals if isinstance(row, dict)]
    if isinstance(fundamentals, dict):
        return [fundamentals]
    return _payload_rows(payload, "data", "items")


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


def _upsert_fundamental(
    session: Session,
    row: dict[str, Any],
    source: SourceDocument,
    fallback_date: date | None,
) -> bool:
    symbol = _symbol(row)
    company = _upsert_company(session, row, symbol)
    provider_updated_at = _optional_datetime(row.get("updated_at"))
    as_of_date = provider_updated_at.date() if provider_updated_at else fallback_date or datetime.now(UTC).date()
    values = {
        "pe_ratio": _decimal_field(row, "pe_ratio"),
        "forward_pe": _decimal_field(row, "forward_pe"),
        "eps": _decimal_field(row, "eps"),
        "dividend_per_share": _decimal_field(row, "dividend_per_share", "dividend"),
        "dividend_yield": _decimal_field(row, "dividend_yield"),
        "beta": _decimal_field(row, "beta"),
        "roa": _decimal_field(row, "roa"),
        "roe": _decimal_field(row, "roe"),
        "pb_ratio": _decimal_field(row, "pb_ratio", "price_to_book"),
        "debt_equity": _decimal_field(row, "debt_equity", "debt_to_equity"),
        "profit_margin": _decimal_field(row, "profit_margin", "net_margin"),
        "gross_margin": _decimal_field(row, "gross_margin"),
        "provider_updated_at": provider_updated_at,
        "extra": row.get("extra") if isinstance(row.get("extra"), dict) else None,
        "raw_payload": row,
        "source_document_id": source.id,
    }
    existing = session.scalar(
        select(NgxPulseFundamental).where(
            NgxPulseFundamental.company_id == company.id,
            NgxPulseFundamental.as_of_date == as_of_date,
        )
    )
    created = existing is None
    record = existing or NgxPulseFundamental(company_id=company.id, as_of_date=as_of_date, **values)
    if existing:
        for key, value in values.items():
            setattr(record, key, value)
    else:
        session.add(record)

    ratio_values = {
        "price": _decimal_field(row, "current_price", "price"),
        "eps": values["eps"],
        "pe_ratio": values["pe_ratio"],
        "roe": values["roe"],
        "net_margin": values["profit_margin"],
        "debt_to_equity": values["debt_equity"],
        "dividend_yield": values["dividend_yield"],
        "data_confidence": Decimal(100),
    }
    ratio = session.scalar(
        select(CompanyRatio).where(
            CompanyRatio.company_id == company.id,
            CompanyRatio.as_of_date == as_of_date,
        )
    )
    if ratio:
        for key, value in ratio_values.items():
            if value is not None:
                setattr(ratio, key, value)
    else:
        session.add(CompanyRatio(company_id=company.id, as_of_date=as_of_date, **ratio_values))
    return created


def _upsert_dividend(
    session: Session,
    company: Company,
    row: dict[str, Any],
    source: SourceDocument,
) -> bool:
    amount = _decimal_field(
        row,
        "amount_per_share",
        "cash_amount",
        "dividend_per_share",
        "amount",
        "dividend",
        required=True,
    )
    declared_date = _optional_date(row.get("declared_date") or row.get("declaration_date"))
    existing = session.scalar(
        select(Dividend).where(
            Dividend.company_id == company.id,
            Dividend.declared_date == declared_date,
            Dividend.amount_per_share == amount,
        )
    )
    values = {
        "ex_dividend_date": _optional_date(row.get("ex_dividend_date") or row.get("ex_date")),
        "payment_date": _optional_date(row.get("payment_date") or row.get("paid_date")),
        "currency": str(row.get("currency") or "NGN"),
        "source_document_id": source.id,
        "reviewed": is_trusted_document_type(source.document_type),
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(Dividend(company_id=company.id, declared_date=declared_date, amount_per_share=amount, **values))
    return True


def _upsert_disclosure(session: Session, row: dict[str, Any], source: SourceDocument) -> bool:
    raw_symbol = row.get("symbol") or row.get("ticker")
    symbol = str(raw_symbol).upper() if raw_symbol else None
    company = _upsert_company(session, row, symbol) if symbol else None
    title = str(row.get("title") or row.get("headline") or row.get("name") or "Untitled disclosure")
    published_at = _optional_datetime(
        row.get("published_at") or row.get("date") or row.get("created_at") or row.get("announcement_date")
    )
    existing = session.scalar(
        select(CorporateDisclosure).where(
            CorporateDisclosure.symbol == symbol,
            CorporateDisclosure.title == title,
            CorporateDisclosure.published_at == published_at,
        )
    )
    values = {
        "company_id": company.id if company else None,
        "symbol": symbol,
        "title": title,
        "disclosure_type": str(row["type"]) if row.get("type") else None,
        "published_at": published_at,
        "url": str(row["url"]) if row.get("url") else None,
        "raw_payload": row,
        "source_document_id": source.id,
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(CorporateDisclosure(**values))
    return True


def _upsert_index(session: Session, row: dict[str, Any], source: SourceDocument) -> bool:
    code = str(row.get("code") or row.get("symbol") or row.get("slug") or "").upper()
    if not code:
        raise ValueError("index row missing code")
    provider_dt = _optional_datetime(row.get("currentDateTime") or row.get("updated_at") or row.get("date"))
    as_of_date = provider_dt.date() if provider_dt else datetime.now(UTC).date()
    values = {
        "slug": str(row["slug"]) if row.get("slug") else None,
        "name": str(row["name"]) if row.get("name") else None,
        "current_price": _decimal_field(row, "currentPrice", "current_price", "value"),
        "change_percentage": _decimal_field(row, "changePercentage", "change_percentage", "pct_change"),
        "raw_payload": row,
        "source_document_id": source.id,
    }
    existing = session.scalar(
        select(MarketIndexSnapshot).where(
            MarketIndexSnapshot.code == code,
            MarketIndexSnapshot.as_of_date == as_of_date,
        )
    )
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(MarketIndexSnapshot(code=code, as_of_date=as_of_date, **values))
    return True


def _upsert_etf(session: Session, row: dict[str, Any], source: SourceDocument) -> bool:
    symbol = str(row.get("symbol") or row.get("canonical_symbol") or "").upper()
    if not symbol:
        raise ValueError("ETF row missing symbol")
    as_of_date = _as_of_date_from_row(row)
    values = {
        "canonical_symbol": str(row["canonical_symbol"]).upper() if row.get("canonical_symbol") else None,
        "slug": str(row["slug"]) if row.get("slug") else None,
        "name": str(row["name"]) if row.get("name") else None,
        "issuer": str(row["issuer"]) if row.get("issuer") else None,
        "isin": str(row["isin"]) if row.get("isin") else None,
        "close_price": _decimal_field(row, "close", "close_price", "current_price"),
        "change_percentage": _decimal_field(row, "change_percentage", "changePercentage", "change_percent"),
        "volume": _int_field(row, "volume"),
        "value_traded": _decimal_field(row, "value", "value_traded"),
        "raw_payload": row,
        "source_document_id": source.id,
    }
    existing = session.scalar(
        select(EtfSnapshot).where(
            EtfSnapshot.symbol == symbol,
            EtfSnapshot.as_of_date == as_of_date,
        )
    )
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(EtfSnapshot(symbol=symbol, as_of_date=as_of_date, **values))
    return True


def _upsert_bond(session: Session, row: dict[str, Any], source: SourceDocument) -> bool:
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
    if not ticker:
        raise ValueError("bond row missing ticker")
    latest_quote_date = _optional_date(row.get("latest_quote_date") or row.get("date"))
    as_of_date = latest_quote_date or datetime.now(UTC).date()
    values = {
        "name": str(row["name"]) if row.get("name") else None,
        "issuer": str(row["issuer"]) if row.get("issuer") else None,
        "issuer_type": str(row["issuer_type"]) if row.get("issuer_type") else None,
        "bond_type": str(row["bond_type"]) if row.get("bond_type") else None,
        "currency": str(row["currency"]) if row.get("currency") else None,
        "coupon_rate": _decimal_field(row, "coupon_rate", "coupon"),
        "maturity_date": _optional_date(row.get("maturity_date")),
        "clean_price": _decimal_field(row, "clean_price", "price", "current_price"),
        "raw_payload": row,
        "source_document_id": source.id,
    }
    existing = session.scalar(
        select(BondSnapshot).where(
            BondSnapshot.ticker == ticker,
            BondSnapshot.as_of_date == as_of_date,
        )
    )
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(BondSnapshot(ticker=ticker, as_of_date=as_of_date, **values))
    return True


def _upsert_bond_auction(session: Session, row: dict[str, Any], source: SourceDocument) -> bool:
    auction_date = _optional_date(row.get("auction_date") or row.get("date"))
    if not auction_date:
        raise ValueError("bond auction row missing auction date")
    instrument_type = str(row.get("instrument_type") or row.get("type") or "unknown")
    tenor_label = str(row["tenor_label"]) if row.get("tenor_label") else None
    values = {
        "tenor_days": _int_field(row, "tenor_days"),
        "stop_rate": _decimal_field(row, "stop_rate", "yield", "rate"),
        "offered_amount": _decimal_field(row, "offered_amount"),
        "allotted_amount": _decimal_field(row, "allotted_amount"),
        "subscription_rate": _decimal_field(row, "subscription_rate"),
        "currency": str(row["currency"]) if row.get("currency") else None,
        "raw_payload": row,
        "source_document_id": source.id,
    }
    existing = session.scalar(
        select(BondAuctionSnapshot).where(
            BondAuctionSnapshot.auction_date == auction_date,
            BondAuctionSnapshot.instrument_type == instrument_type,
            BondAuctionSnapshot.tenor_label == tenor_label,
        )
    )
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(
        BondAuctionSnapshot(
            auction_date=auction_date,
            instrument_type=instrument_type,
            tenor_label=tenor_label,
            **values,
        )
    )
    return True


def _upsert_nasd_otc_stock(session: Session, row: dict[str, Any], source: SourceDocument) -> bool:
    symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
    if not symbol:
        raise ValueError("NASD OTC row missing symbol")
    as_of_date = _as_of_date_from_row(row)
    values = {
        "name": str(row["name"]) if row.get("name") else None,
        "current_price": _decimal_field(row, "current_price", "price", "close"),
        "change_percentage": _decimal_field(row, "change_percent", "change_percentage"),
        "volume": _int_field(row, "volume"),
        "market_cap": _decimal_field(row, "market_cap", "market_capitalization"),
        "raw_payload": row,
        "source_document_id": source.id,
    }
    existing = session.scalar(
        select(NasdOtcStockSnapshot).where(
            NasdOtcStockSnapshot.symbol == symbol,
            NasdOtcStockSnapshot.as_of_date == as_of_date,
        )
    )
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(NasdOtcStockSnapshot(symbol=symbol, as_of_date=as_of_date, **values))
    return True


def _upsert_market_news(session: Session, row: dict[str, Any], source: SourceDocument) -> bool:
    title = str(row.get("title") or row.get("headline") or "").strip()
    if not title:
        raise ValueError("market news row missing title")
    published_at = _optional_datetime(row.get("published_at") or row.get("date") or row.get("created_at"))
    url = str(row["url"]) if row.get("url") else None
    existing = session.scalar(
        select(MarketNewsItem).where(
            MarketNewsItem.title == title,
            MarketNewsItem.published_at == published_at,
            MarketNewsItem.url == url,
        )
    )
    values = {
        "source_name": str(row.get("source") or row.get("source_name")) if row.get("source") or row.get("source_name") else None,
        "published_at": published_at,
        "url": url,
        "summary": str(row.get("summary") or row.get("description")) if row.get("summary") or row.get("description") else None,
        "raw_payload": row,
        "source_document_id": source.id,
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    session.add(MarketNewsItem(title=title, **values))
    return True


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


def _as_of_date_from_row(row: dict[str, Any]) -> date:
    provider_dt = _optional_datetime(
        row.get("currentDateTime")
        or row.get("updated_at")
        or row.get("timestamp")
        or row.get("date")
        or row.get("trade_date")
        or row.get("latest_quote_date")
    )
    return provider_dt.date() if provider_dt else datetime.now(UTC).date()


def _optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10])


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time())
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


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
