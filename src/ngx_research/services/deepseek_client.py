import json
from decimal import Decimal

import httpx

from ngx_research.config import settings


class DeepSeekError(RuntimeError):
    pass


async def extract_financial_statement(report_text: str) -> tuple[str, dict]:
    if not settings.deepseek_api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY is not configured")

    prompt = _financial_extraction_prompt(report_text)
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You extract financial statement fields from Nigerian listed company "
                    "reports. Return only valid JSON. Do not guess missing values."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        raise DeepSeekError(f"DeepSeek request failed: {response.status_code} {response.text}")

    body = response.json()
    raw_content = body["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise DeepSeekError("DeepSeek returned non-JSON content") from exc
    return raw_content, _normalize_numbers(parsed)


def _financial_extraction_prompt(report_text: str) -> str:
    return f"""
Extract a financial statement draft from the report text below.

Return JSON with this exact shape:
{{
  "symbol": "ticker if explicitly present, otherwise null",
  "period_end": "YYYY-MM-DD or null",
  "period_type": "FY, Q1, Q2, Q3, Q4, H1, 9M, or null",
  "currency": "NGN unless explicitly different",
  "scale": "actual, thousands, millions, billions, or null",
  "revenue": number_or_null,
  "profit_after_tax": number_or_null,
  "total_assets": number_or_null,
  "total_liabilities": number_or_null,
  "total_equity": number_or_null,
  "cash_flow_operations": number_or_null,
  "eps": number_or_null,
  "statement_kind": "bank, insurance, industrial, oil_gas, telecom, consumer, or null",
  "gross_earnings": number_or_null,
  "interest_income": number_or_null,
  "net_interest_income": number_or_null,
  "customer_deposits": number_or_null,
  "loans_and_advances": number_or_null,
  "borrowings_total": number_or_null,
  "interest_expense": number_or_null,
  "npl_ratio": number_or_null,
  "capital_adequacy_ratio": number_or_null,
  "loan_to_deposit_ratio": number_or_null,
  "dividend_per_share": number_or_null,
  "dividend_currency": "NGN, USD, GBP, or null",
  "dividend_declared_date": "YYYY-MM-DD or null",
  "dividend_ex_dividend_date": "YYYY-MM-DD or null",
  "dividend_payment_date": "YYYY-MM-DD or null",
  "dividends": [
    {{
      "amount_per_share": number_or_null,
      "currency": "NGN, USD, GBP, or null",
      "declared_date": "YYYY-MM-DD or null",
      "ex_dividend_date": "YYYY-MM-DD or null",
      "payment_date": "YYYY-MM-DD or null",
      "period_label": "final, interim, quarterly, FY total, or null",
      "notes": "short note or null"
    }}
  ],
  "major_risks": ["risk phrases explicitly found in the report"],
  "business_summary": "plain-English business summary if present, otherwise null",
  "auditor_name": "auditor name if present, otherwise null",
  "auditor_opinion": "auditor opinion if present, otherwise null",
  "corporate_actions": ["material corporate actions explicitly found in the report"],
  "confidence": 0_to_100,
  "warnings": ["short warnings about missing/ambiguous fields"],
  "summary": "brief plain-English summary"
}}

Rules:
- Do not invent missing values.
- Preserve the scale in the scale field; do not silently multiply values.
- Prefer the current period, not comparative prior-year values.
- Prefer consolidated/group values over company-only values when both are present.
- Prefer the primary financial statements before notes or narrative summaries.
- If a value is ambiguous, set it to null and add a warning.
- Extract revenue and profit_after_tax from statement of profit or loss/comprehensive income.
- For banks, set statement_kind to "bank" and map revenue to gross earnings. Also extract
  gross_earnings, interest_income, net_interest_income, customer_deposits, loans_and_advances,
  borrowings_total, interest_expense, non-performing loan/NPL ratio, capital adequacy ratio/CAR,
  and loan-to-deposit ratio if explicitly available.
- For banks, do not treat negative operating cash flow as a warning by itself; changes in loans,
  advances, deposits, and treasury assets can make operating cash flow negative in normal banking.
- Extract total_assets, total_liabilities, and total_equity from statement of financial position
  or balance sheet. Accept equivalent labels such as assets, liabilities, equity, net assets,
  total equity and liabilities, shareholders' equity, or equity attributable to owners.
- If total_liabilities is not explicitly labelled but total_assets and total_equity are explicit,
  calculate total_liabilities as total_assets - total_equity and add a warning explaining this.
- Extract cash_flow_operations from statement of cash flows using net cash from/generated by
  operating activities, cash generated from operations, or operating cash flow.
- Extract eps from earnings per share/basic earnings per share/loss per share.
- Extract dividends from dividend notes, proposed/final/interim dividend text, or annual report
  dividend tables. If only a full-year total dividend per share is available, put it in
  dividend_per_share and also include one dividends item with period_label "FY total".
- Preserve dividend currency. Do not convert USD dividends into NGN.
- Extract qualitative evidence only when explicitly present in the text. Do not invent risks,
  business summaries, auditor opinions, or corporate actions.

Report text:
{report_text[:60000]}
""".strip()


def _normalize_numbers(value):
    if isinstance(value, dict):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_numbers(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    return value
