"""structured() doğrula/retry + factory allow_cloud guard (design/09)."""

import pytest
from pydantic import BaseModel

from src.application.dtos.llm import Caps, LLMResponse, Msg
from src.application.llm.structured import structured
from src.infrastructure.llm.factory import CloudBlockedError, build_chat, build_embedding
from src.infrastructure.settings.config import ChatModelConfig, EmbeddingConfig


class Category(BaseModel):
    category: str
    confidence: float


class ScriptedProvider:
    """LLMProvider fake — sıralı yanıt döndürür (json_mode kapalı → prompt yolu)."""

    def __init__(self, responses, json_mode=False):
        self._responses = list(responses)
        self._caps = Caps(tool_calling=False, json_mode=json_mode)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self._responses.pop(0)

    @property
    def caps(self):
        return self._caps

    @property
    def model_id(self):
        return "scripted"

    @property
    def is_cloud(self):
        return False


def test_structured_retries_then_succeeds():
    provider = ScriptedProvider([
        LLMResponse(text="bozuk yanıt, json değil"),
        LLMResponse(text='{"category": "teklif", "confidence": 0.9}'),
    ])
    result = structured(provider, [Msg("user", "sınıflandır")], Category)
    assert result is not None
    assert result.category == "teklif"
    assert provider.calls == 2  # ilk geçersiz → retry


def test_structured_returns_none_when_all_invalid():
    provider = ScriptedProvider([LLMResponse(text="x"), LLMResponse(text="y"), LLMResponse(text="z")])
    assert structured(provider, [Msg("user", "?")], Category, max_retries=2) is None


def test_structured_uses_parsed_when_present():
    provider = ScriptedProvider([LLMResponse(parsed={"category": "police", "confidence": 0.5})])
    result = structured(provider, [Msg("user", "?")], Category)
    assert result.category == "police"
    assert provider.calls == 1


def test_allow_cloud_blocks_cloud_chat():
    cfg = ChatModelConfig(provider="anthropic", model="claude", api_key_env="X")
    with pytest.raises(CloudBlockedError):
        build_chat(cfg, allow_cloud=False)


def test_allow_cloud_blocks_cloud_embedding():
    cfg = EmbeddingConfig(provider="openai", model="text-embedding-3-small", api_key_env="X")
    with pytest.raises(CloudBlockedError):
        build_embedding(cfg, allow_cloud=False)


def test_local_chat_builds_without_cloud():
    cfg = ChatModelConfig(provider="ollama", model="qwen2.5:3b")
    provider = build_chat(cfg, allow_cloud=False)
    assert provider.is_cloud is False
    assert provider.caps.json_mode is False  # ollama → prompt-tabanlı structured
