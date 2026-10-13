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

    @property
    def is_enabled(self) -> bool:
        return self.provider != "local"


def load_llm_config() -> LlmConfig:
    provider = os.getenv("XBLAGO_LLM_PROVIDER", "local").strip().lower()
    model = os.getenv("XBLAGO_LLM_MODEL", "gpt-5-mini").strip()
    api_key = os.getenv("XBLAGO_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("XBLAGO_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    timeout_seconds = _float_from_env("XBLAGO_LLM_TIMEOUT_SECONDS", 30.0)

    return LlmConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )


def _float_from_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default
