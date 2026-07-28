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
  "confidence": 0_to_100,
  "warnings": ["short warnings about missing/ambiguous fields"],
  "summary": "brief plain-English summary"
}}

Rules:
- Do not invent missing values.
- Preserve the scale in the scale field; do not silently multiply values.
- Prefer the current period, not comparative prior-year values.
- If a value is ambiguous, set it to null and add a warning.

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
