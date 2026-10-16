from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str
    timeout_seconds: float
    max_tokens: int = 384
    thinking: str = "auto"

    @property
    def is_enabled(self) -> bool:
        return self.provider != "local"


def load_llm_config() -> LlmConfig:
    provider = os.getenv("XBLAGO_LLM_PROVIDER", "local").strip().lower()
    model = os.getenv("XBLAGO_LLM_MODEL", "gpt-5-mini").strip()
    api_key = os.getenv("XBLAGO_LLM_API_KEY") or os.getenv(
        "HF_TOKEN" if provider in {"hf", "huggingface", "huggingface-inference"} else "OPENAI_API_KEY"
    )
    default_base_url = (
        "https://router.huggingface.co/v1"
        if provider in {"hf", "huggingface", "huggingface-inference"}
        else "https://api.openai.com/v1"
    )
    base_url = os.getenv("XBLAGO_LLM_BASE_URL", default_base_url).rstrip("/")
    timeout_seconds = _float_from_env("XBLAGO_LLM_TIMEOUT_SECONDS", 30.0)
    max_tokens = _int_from_env("XBLAGO_LLM_MAX_TOKENS", 384, minimum=1, maximum=2048)
    thinking = _thinking_from_env(
        "XBLAGO_LLM_THINKING",
        default="off" if provider in {"hf", "huggingface", "huggingface-inference"} else "auto",
    )

    return LlmConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        thinking=thinking,
    )


def _float_from_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def _int_from_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, min(parsed, maximum))


def _thinking_from_env(name: str, default: str) -> str:
    value = (os.getenv(name) or default).strip().lower()
    return value if value in {"auto", "on", "off"} else default
