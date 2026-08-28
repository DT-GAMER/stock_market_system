import json

import anyio
import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from ngx_research.services.openai_client import (
    OpenAIExtractionError,
    _extract_with_responses_api,
    _parse_json_content,
    _prepare_pdf_for_openai,
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
                company_symbol="GTCO",
                company_name="GTCO Plc",
                selected_pages=[10, 11, 12],
            )

    assert anyio.run(request) == "{\"revenue\": 100}"
    assert captured["url"] == "https://api.openai.test/v1/responses"
    payload = json.loads(captured["json"])
    assert "10, 11, 12" in payload["input"][0]["content"][1]["text"]
    file_part = payload["input"][0]["content"][2]
    assert file_part["type"] == "input_file"
    assert file_part["file_id"] == "file-test"
    assert file_part["detail"] == "low"
    assert "file_data" not in file_part
    assert "filename" not in file_part


def test_prepare_pdf_for_openai_reduces_large_report(monkeypatch, tmp_path) -> None:
    pdf = tmp_path / "report.pdf"
    writer = PdfWriter()
    for _ in range(6):
        writer.add_blank_page(width=72, height=72)
    with pdf.open("wb") as file:
        writer.write(file)

    def fake_extract_pdf_text(path: str):
        assert path == str(pdf)
        return (
            (
                "--- Page 1 ---\nChairman statement\n\n"
                "--- Page 2 ---\nCorporate governance\n\n"
                "--- Page 3 ---\nStatement of profit or loss Revenue 1,000 Profit after tax 200\n\n"
                "--- Page 4 ---\nStatement of financial position Total assets 5,000\n\n"
                "--- Page 5 ---\nStatement of cash flows operating activities 300\n\n"
                "--- Page 6 ---\nIndependent auditor key audit matters"
            ),
            6,
            [],
        )

    monkeypatch.setattr("ngx_research.services.openai_client.settings.openai_pdf_max_pages", 3)
    monkeypatch.setattr("ngx_research.services.openai_client.extract_pdf_text", fake_extract_pdf_text)

    selected_pdf, cleanup, selected_pages = _prepare_pdf_for_openai(pdf)

    try:
        assert cleanup is True
        assert len(selected_pages) == 3
        assert 3 in selected_pages
        assert 6 in selected_pages
        assert len(PdfReader(str(selected_pdf)).pages) == 3
    finally:
        selected_pdf.unlink(missing_ok=True)


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
