"""Provider factory — config → adapter (design/09). allow_cloud guard merkezi burada."""

from __future__ import annotations

from src.application.dtos.llm import Caps
from src.application.ports.embedding import EmbeddingProvider
from src.application.ports.llm import LLMProvider
from src.infrastructure.embedding.bge_m3 import BgeM3Embedding
from src.infrastructure.embedding.cloud import OpenAIEmbedding
from src.infrastructure.llm.anthropic import AnthropicChat
from src.infrastructure.llm.openai_compatible import OpenAICompatibleChat
from src.infrastructure.llm.vertex import VertexChat
from src.infrastructure.settings.config import ChatModelConfig, EmbeddingConfig

_CLOUD_CHAT = {"openai", "anthropic", "vertex"}
_CLOUD_EMBED = {"openai", "vertex"}
_DEFAULT_BASE = {
    "vllm": "http://localhost:8000/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
}


class CloudBlockedError(RuntimeError):
    """allow_cloud=false iken cloud provider istendi (design/14 sigortası)."""


def build_chat(cfg: ChatModelConfig, *, allow_cloud: bool) -> LLMProvider:
    provider = cfg.provider
    if provider in _CLOUD_CHAT and not allow_cloud:
        raise CloudBlockedError(f"allow_cloud=false → cloud chat '{provider}' engellendi")

    if provider in ("vllm", "openai", "ollama"):
        base_url = cfg.base_url or _DEFAULT_BASE[provider]
        # Küçük lokal modeller (ollama) için native json_schema/tool-call'a güvenme.
        caps = (
            Caps(tool_calling=True, json_mode=True, max_context=cfg.max_context)
            if provider in ("vllm", "openai")
            else Caps(tool_calling=False, json_mode=False, max_context=cfg.max_context)
        )
        return OpenAICompatibleChat(
            base_url=base_url, model=cfg.model, api_key=cfg.api_key(),
            caps=caps, is_cloud=(provider == "openai"),
        )
    if provider == "anthropic":
        key = cfg.api_key()
        if not key:
            raise RuntimeError("Anthropic için api_key_env gerekli (.env)")
        return AnthropicChat(model=cfg.model, api_key=key, max_context=cfg.max_context)
    if provider == "vertex":
        if not cfg.project:
            raise RuntimeError("Vertex için 'project' config gerekli")
        return VertexChat(project=cfg.project, location=cfg.location, model=cfg.model,
                          max_context=cfg.max_context)
    raise ValueError(f"Bilinmeyen chat provider: {provider}")


def build_embedding(cfg: EmbeddingConfig, *, allow_cloud: bool) -> EmbeddingProvider:
    provider = cfg.provider
    if provider in _CLOUD_EMBED and not allow_cloud:
        raise CloudBlockedError(f"allow_cloud=false → cloud embedding '{provider}' engellendi")

    if provider == "local":
        return BgeM3Embedding(model="BAAI/bge-m3", dim=cfg.dim)
    if provider == "openai":
        key = cfg.api_key()
        if not key:
            raise RuntimeError("OpenAI embedding için api_key_env gerekli (.env)")
        base = cfg.base_url or "https://api.openai.com/v1"
        return OpenAIEmbedding(api_key=key, model=cfg.model, dim=cfg.dim, base_url=base)
    raise ValueError(f"Bilinmeyen embedding provider: {provider}")
