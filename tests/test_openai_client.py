import pytest

from ngx_research.services.openai_client import (
    OpenAIExtractionError,
    _parse_json_content,
    _response_output_text,
)


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
