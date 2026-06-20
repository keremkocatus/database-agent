"""Chat adapter normalize mapping — sahte sağlayıcı yanıtı → LLMResponse (HTTP'siz)."""

from src.infrastructure.llm.anthropic import normalize_response as anthropic_norm
from src.infrastructure.llm.openai_compatible import build_request, normalize_response as openai_norm
from src.infrastructure.llm.vertex import normalize_response as vertex_norm
from src.application.dtos.llm import Msg


def test_openai_normalize_text_and_usage():
    data = {
        "model": "qwen",
        "choices": [{"message": {"content": '{"x": 1}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    r = openai_norm(data)
    assert r.text == '{"x": 1}'
    assert r.parsed == {"x": 1}
    assert r.usage.total_tokens == 13
    assert r.finish_reason == "stop"


def test_openai_normalize_tool_calls():
    data = {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "t1", "function": {"name": "search", "arguments": '{"q": "teklif"}'}}
        ]}}]
    }
    r = openai_norm(data)
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "search"
    assert r.tool_calls[0].arguments == {"q": "teklif"}


def test_openai_build_request_schema_and_tools():
    payload = build_request(
        model="m", messages=[Msg("system", "sys"), Msg("user", "hi")],
        tools=None, schema={"type": "object"}, temperature=0.0, seed=7, max_tokens=64,
    )
    assert payload["seed"] == 7
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["messages"][0]["role"] == "system"


def test_anthropic_normalize_text_and_tool_use():
    data = {
        "model": "claude",
        "content": [
            {"type": "text", "text": "merhaba"},
            {"type": "tool_use", "id": "u1", "name": "fn", "input": {"a": 1}},
        ],
        "usage": {"input_tokens": 5, "output_tokens": 2},
        "stop_reason": "end_turn",
    }
    r = anthropic_norm(data)
    assert r.text == "merhaba"
    assert r.tool_calls[0].name == "fn" and r.tool_calls[0].arguments == {"a": 1}
    assert r.usage.prompt_tokens == 5


def test_vertex_normalize_json_text():
    data = {
        "candidates": [{"content": {"parts": [{"text": '{"category": "teklif"}'}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 4},
    }
    r = vertex_norm(data, "gemini")
    assert r.parsed == {"category": "teklif"}
    assert r.usage.completion_tokens == 4
    assert r.model_id == "gemini"
