import json

import anyio
import httpx
import pytest

from ngx_research.services.openai_client import (
    OpenAIExtractionError,
    _extract_with_responses_api,
    _parse_json_content,
    _response_output_text,
    _upload_file,
)


def test_upload_file_uses_user_data_purpose(monkeypatch, tmp_path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        body = await request.aread()
        captured["body"] = body
        return httpx.Response(200, json={"id": "file-test"})

    monkeypatch.setattr("ngx_research.services.openai_client.settings.openai_api_key", "test-key")
    monkeypatch.setattr("ngx_research.services.openai_client.settings.openai_base_url", "https://api.openai.test/v1")

    async def request() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _upload_file(client, pdf, "report.pdf")

    assert anyio.run(request) == "file-test"
    assert captured["url"] == "https://api.openai.test/v1/files"
    assert b'name="purpose"\r\n\r\nuser_data' in captured["body"]


def test_responses_request_uses_file_id_and_low_detail(monkeypatch) -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = await request.aread()
        return httpx.Response(200, json={"output_text": "{\"revenue\": 100}"})

    monkeypatch.setattr("ngx_research.services.openai_client.settings.openai_api_key", "test-key")
    monkeypatch.setattr("ngx_research.services.openai_client.settings.openai_base_url", "https://api.openai.test/v1")
    monkeypatch.setattr("ngx_research.services.openai_client.settings.openai_pdf_detail", "low")
    transport = httpx.MockTransport(handler)

    async def request() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await _extract_with_responses_api(
                client=client,
                file_id="file-test",
                filename="report.pdf",
                company_symbol="GTCO",
                company_name="GTCO Plc",
            )

    assert anyio.run(request) == "{\"revenue\": 100}"
    assert captured["url"] == "https://api.openai.test/v1/responses"
    payload = json.loads(captured["json"])
    file_part = payload["input"][0]["content"][1]
    assert file_part["type"] == "input_file"
    assert file_part["file_id"] == "file-test"
    assert file_part["detail"] == "low"
    assert "file_data" not in file_part


def test_response_output_text_reads_top_level_output_text() -> None:
    assert _response_output_text({"output_text": "{\"ok\": true}"}) == "{\"ok\": true}"


def test_response_output_text_reads_nested_output_text() -> None:
    body = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "{\"revenue\": 100}"},
                ]
            }
        ]
    }

    assert _response_output_text(body) == "{\"revenue\": 100}"


def test_parse_json_content_accepts_markdown_fence() -> None:
    assert _parse_json_content("```json\n{\"revenue\": 100}\n```") == {"revenue": 100}


def test_parse_json_content_rejects_non_json() -> None:
    with pytest.raises(OpenAIExtractionError):
        _parse_json_content("revenue is 100")
