import json
import re
import tempfile
from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter

from ngx_research.config import settings
from ngx_research.services.financial_section_extractor import (
    ReportPage,
    parse_report_pages,
    select_financial_section,
)
from ngx_research.services.pdf_extractor import PdfExtractionError, extract_pdf_text


class OpenAIExtractionError(RuntimeError):
    pass


OPENAI_SUPPORTING_PATTERNS = (
    re.compile(r"\bindependent auditor", re.IGNORECASE),
    re.compile(r"\bkey audit matters?\b", re.IGNORECASE),
    re.compile(r"\bqualified opinion\b", re.IGNORECASE),
    re.compile(r"\bemphasis of matter\b", re.IGNORECASE),
    re.compile(r"\bmaterial uncertainty\b", re.IGNORECASE),
    re.compile(r"\bgoing concern\b", re.IGNORECASE),
    re.compile(r"\bfive[- ]year\b", re.IGNORECASE),
    re.compile(r"\bfinancial highlights?\b", re.IGNORECASE),
    re.compile(r"\bdividends?\b", re.IGNORECASE),
    re.compile(r"\brisk management\b", re.IGNORECASE),
    re.compile(r"\bprincipal risks?\b", re.IGNORECASE),
    re.compile(r"\bcorporate governance\b", re.IGNORECASE),
    re.compile(r"\bsegment information\b", re.IGNORECASE),
    re.compile(r"\bmanagement discussion\b", re.IGNORECASE),
)


async def extract_financial_statement_from_pdf(
    pdf_path: str,
    filename: str,
    company_symbol: str | None = None,
    company_name: str | None = None,
) -> tuple[str, dict]:
    if not settings.openai_api_key:
        raise OpenAIExtractionError("OPENAI_API_KEY is not configured")

    file_path = Path(pdf_path)
    if not file_path.exists():
        raise OpenAIExtractionError("uploaded report file is missing from local storage")
    if file_path.suffix.lower() != ".pdf":
        raise OpenAIExtractionError("uploaded report is not a PDF")

    upload_path, cleanup_upload_path, selected_pages = _prepare_pdf_for_openai(file_path)
    async with httpx.AsyncClient(timeout=180) as client:
        file_id = await _upload_file(client, upload_path, filename)
        try:
            raw_content = await _extract_with_responses_api(
                client=client,
                file_id=file_id,
                company_symbol=company_symbol,
                company_name=company_name,
                selected_pages=selected_pages,
            )
        finally:
            await _delete_file(client, file_id)
            if cleanup_upload_path:
                upload_path.unlink(missing_ok=True)

    return raw_content, _parse_json_content(raw_content)


def _prepare_pdf_for_openai(file_path: Path) -> tuple[Path, bool, list[int]]:
    reader = PdfReader(str(file_path))
    page_count = len(reader.pages)
    max_pages = max(1, settings.openai_pdf_max_pages)
    if page_count <= max_pages:
        return file_path, False, list(range(1, page_count + 1))

    selected_numbers = _select_openai_pdf_pages(file_path, page_count, max_pages)
    if not selected_numbers:
        selected_numbers = list(range(max(1, page_count - max_pages + 1), page_count + 1))

    slim_path = _write_selected_pdf(reader, selected_numbers)
    return slim_path, True, selected_numbers


def _select_openai_pdf_pages(file_path: Path, page_count: int, max_pages: int) -> list[int]:
    try:
        report_text, _, _ = extract_pdf_text(str(file_path))
    except PdfExtractionError:
        return []

    pages = parse_report_pages(report_text)
    if not pages:
        return []

    selected_text, _ = select_financial_section(
        report_text,
        max_chars=settings.openai_pdf_selection_max_chars,
    )
    core_numbers = [page.number for page in parse_report_pages(selected_text)]
    extra_pages = sorted(
        (
            (page, _openai_supporting_page_score(page))
            for page in pages
            if page.number not in set(core_numbers)
        ),
        key=lambda item: (item[1], -item[0].number),
        reverse=True,
    )
    selected = _ordered_unique(core_numbers)
    for page, score in extra_pages:
        if len(selected) >= max_pages:
            break
        if score <= 0:
            continue
        selected.append(page.number)

    if len(selected) > max_pages:
        selected_pages = [page for page in pages if page.number in set(selected)]
        ranked = sorted(
            selected_pages,
            key=lambda page: (_openai_supporting_page_score(page), -page.number),
            reverse=True,
        )[:max_pages]
        selected = [page.number for page in ranked]

    return sorted(number for number in _ordered_unique(selected) if 1 <= number <= page_count)


def _openai_supporting_page_score(page: ReportPage) -> int:
    score = 0
    score += 5 * sum(1 for pattern in OPENAI_SUPPORTING_PATTERNS if pattern.search(page.text))
    score += min(8, len(re.findall(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b", page.text)) // 8)
    return score


def _ordered_unique(numbers: list[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for number in numbers:
        if number in seen:
            continue
        seen.add(number)
        unique.append(number)
    return unique


def _write_selected_pdf(reader: PdfReader, selected_numbers: list[int]) -> Path:
    writer = PdfWriter()
    for number in selected_numbers:
        writer.add_page(reader.pages[number - 1])

    with tempfile.NamedTemporaryFile(prefix="equitykobo-openai-", suffix=".pdf", delete=False) as file:
        writer.write(file)
        return Path(file.name)


async def _upload_file(client: httpx.AsyncClient, file_path: Path, filename: str) -> str:
    with file_path.open("rb") as file:
        response = await client.post(
            f"{settings.openai_base_url.rstrip('/')}/files",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            data={"purpose": "user_data"},
            files={"file": (filename or file_path.name, file, "application/pdf")},
        )
    if response.status_code >= 400:
        raise OpenAIExtractionError(f"OpenAI file upload failed: {response.status_code} {response.text}")

    file_id = response.json().get("id")
    if not file_id:
        raise OpenAIExtractionError("OpenAI file upload did not return a file id")
    return file_id


async def _extract_with_responses_api(
    client: httpx.AsyncClient,
    file_id: str,
    company_symbol: str | None,
    company_name: str | None,
    selected_pages: list[int] | None = None,
) -> str:
    response = await client.post(
        f"{settings.openai_base_url.rstrip('/')}/responses",
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.openai_model,
            "reasoning": {"effort": settings.openai_reasoning_effort},
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _financial_pdf_extraction_prompt(company_symbol, company_name),
                        },
                        {
                            "type": "input_text",
                            "text": _selected_pages_note(selected_pages),
                        },
                        {
                            "type": "input_file",
                            "file_id": file_id,
                            "detail": settings.openai_pdf_detail,
                        },
                    ],
                }
            ],
        },
    )
    if response.status_code >= 400:
        raise OpenAIExtractionError(f"OpenAI extraction failed: {response.status_code} {response.text}")
    return _response_output_text(response.json())


def _selected_pages_note(selected_pages: list[int] | None) -> str:
    if not selected_pages:
        return "The attached PDF may be the full report."
    return (
        "The attached PDF may contain only selected pages from the original annual report. "
        "Original page numbers included: " + ", ".join(str(number) for number in selected_pages)
    )


async def _delete_file(client: httpx.AsyncClient, file_id: str) -> None:
    if not file_id:
        return
    try:
        await client.delete(
            f"{settings.openai_base_url.rstrip('/')}/files/{file_id}",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )
    except httpx.HTTPError:
        pass


def _response_output_text(body: dict) -> str:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    chunks: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    raw = "\n".join(chunks).strip()
    if not raw:
        raise OpenAIExtractionError("OpenAI response did not contain output text")
    return raw


def _parse_json_content(raw_content: str) -> dict:
    content = raw_content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OpenAIExtractionError("OpenAI returned non-JSON content") from exc
    if not isinstance(parsed, dict):
        raise OpenAIExtractionError("OpenAI returned JSON that was not an object")
    return parsed


def _financial_pdf_extraction_prompt(company_symbol: str | None, company_name: str | None) -> str:
    company_hint = " ".join(item for item in [company_symbol, company_name] if item) or "the company"
    return f"""
You are extracting annual-report facts for EquityKobo from the attached PDF for {company_hint}.

From every annual report, extract two things:
1. Hard numbers
2. Business meaning

The hard numbers feed the scoring engine. The business meaning helps the system explain whether the company is trustworthy.

Return only valid JSON. Do not wrap it in markdown. Do not guess missing values.

Use this exact JSON shape:
{{
  "symbol": "ticker if explicitly present, otherwise null",
  "company": "company name if present, otherwise null",
  "financial_year": 2025,
  "period_end": "YYYY-MM-DD or null",
  "period_type": "FY",
  "currency": "NGN unless explicitly different",
  "scale": "actual, thousands, millions, billions, or null",
  "revenue": number_or_null,
  "profit_before_tax": number_or_null,
  "profit_after_tax": number_or_null,
  "eps": number_or_null,
  "total_assets": number_or_null,
  "total_liabilities": number_or_null,
  "total_equity": number_or_null,
  "cash_flow_operations": number_or_null,
  "cash_and_cash_equivalents": number_or_null,
  "borrowings_total": number_or_null,
  "finance_cost": number_or_null,
  "dividend_per_share": number_or_null,
  "dividend_currency": "NGN, USD, GBP, or null",
  "dividend_declared_date": "YYYY-MM-DD or null",
  "dividend_ex_dividend_date": "YYYY-MM-DD or null",
  "dividend_payment_date": "YYYY-MM-DD or null",
  "income_statement": {{
    "cost_of_sales_or_interest_expense": number_or_null,
    "gross_profit": number_or_null,
    "operating_profit": number_or_null,
    "gross_earnings": number_or_null,
    "interest_income": number_or_null,
    "net_interest_income": number_or_null,
    "net_fee_and_commission_income": number_or_null
  }},
  "statement_kind": "bank, insurance, industrial, oil_gas, telecom, consumer, or null",
  "gross_earnings": number_or_null,
  "interest_income": number_or_null,
  "net_interest_income": number_or_null,
  "customer_deposits": number_or_null,
  "loans_and_advances": number_or_null,
  "interest_expense": number_or_null,
  "npl_ratio": number_or_null,
  "capital_adequacy_ratio": number_or_null,
  "loan_to_deposit_ratio": number_or_null,
  "cash_flow": {{
    "net_cash_used_in_investing_activities": number_or_null,
    "net_cash_from_financing_activities": number_or_null,
    "free_cash_flow": number_or_null
  }},
  "dividends": [
    {{
      "amount_per_share": number_or_null,
      "currency": "NGN, USD, GBP, or null",
      "declared_date": "YYYY-MM-DD or null",
      "ex_dividend_date": "YYYY-MM-DD or null",
      "payment_date": "YYYY-MM-DD or null",
      "period_label": "final, interim, quarterly, FY total, or null",
      "payout_ratio": number_or_null,
      "notes": "short note or null"
    }}
  ],
  "five_year_summary": [
    {{
      "financial_year": 2025,
      "period_end": "YYYY-MM-DD or null",
      "revenue": number_or_null,
      "profit_before_tax": number_or_null,
      "profit_after_tax": number_or_null,
      "eps": number_or_null,
      "total_assets": number_or_null,
      "total_liabilities": number_or_null,
      "total_equity": number_or_null,
      "dividend_per_share": number_or_null
    }}
  ],
  "business_quality": {{
    "main_business_segments": ["explicit segment names"],
    "revenue_sources": ["explicit revenue sources"],
    "market_position": "explicit market position evidence or null",
    "major_customers": ["explicit major customers"],
    "expansion_plans": ["explicit expansion plans"],
    "new_products": ["explicit new products"],
    "cost_pressures": ["explicit cost pressures"],
    "competitive_advantages": ["explicit competitive advantages"],
    "management_commentary": "brief management commentary or null",
    "strategy": "explicit strategy summary or null"
  }},
  "major_risks": ["FX risk, interest rate risk, regulatory risk, credit risk, liquidity risk, commodity price risk, security risk, competition, debt refinancing risk, legal cases, going concern warnings"],
  "business_summary": "plain-English business summary using only report evidence, otherwise null",
  "auditor_name": "auditor name if present, otherwise null",
  "auditor_opinion": "qualified opinion, emphasis of matter, material uncertainty, going concern warning, KAMs, restatements, or clean/unqualified opinion if stated",
  "corporate_actions": ["bonus issue, rights issue, share split, buyback, acquisition, merger, disposal, capital raise"],
  "management_and_governance": {{
    "board_changes": ["explicit board changes"],
    "executive_changes": ["CEO/CFO changes or resignations"],
    "insider_ownership": "explicit insider ownership evidence or null",
    "related_party_transactions": "explicit related-party transaction evidence or null",
    "governance_issues": ["explicit governance issues"],
    "remuneration": "remuneration evidence or null"
  }},
  "confidence": 0_to_100,
  "warnings": ["short warnings about missing or ambiguous fields"],
  "summary": "brief plain-English summary"
}}

Extraction rules:
- The minimum extraction target is revenue, profit_after_tax, total_assets, total_liabilities,
  total_equity, cash_flow_operations, eps, dividend_per_share, major_risks,
  business_summary, and auditor_opinion.
- Preserve the scale in the scale field; do not silently multiply values.
- Prefer the current period, not comparative prior-year values.
- Prefer consolidated/group values over company-only values when both are present.
- Prefer the primary financial statements before notes or narrative summaries.
- If a value is ambiguous, set it to null and add a warning.
- Extract revenue and profit_after_tax from statement of profit or loss/comprehensive income.
- For banks, revenue may appear as gross earnings, interest income, net interest income, or
  net fee and commission income. Set statement_kind to "bank" when appropriate.
- For non-banks, revenue usually appears as revenue, turnover, or sales.
- Extract total assets, total liabilities, total equity, cash and cash equivalents, borrowings,
  debt securities, customer deposits, and loans and advances from the balance sheet.
- Extract cash_flow_operations from net cash generated from operating activities, net cash used
  in operating activities, cash generated from operations, or operating cash flow.
- Extract dividend declared, dividend paid, dividend per share, final dividend, interim dividend,
  payout ratio, and payment dates where available.
- Extract five-year summaries when available.
- Extract qualitative business quality, risk, auditor, corporate action, management, and
  governance evidence only when explicitly present in the PDF.
""".strip()
