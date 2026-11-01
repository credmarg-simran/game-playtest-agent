import os

from .base import LLM
from .mock_llm import MockLLM


def make_llm(provider: str = "auto", model: str | None = None, strong_model: str | None = None) -> LLM:
    """`auto` uses OpenAI when OPENAI_API_KEY is set, otherwise the offline mock."""
    if provider == "auto":
        provider = "openai" if os.environ.get("OPENAI_API_KEY") else "mock"
    if provider == "mock":
        return MockLLM()
    if provider == "openai":
        from .openai_llm import OpenAILLM

        return OpenAILLM(model or os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
                         strong_model or os.environ.get("OPENAI_STRONG_MODEL"))
    raise ValueError(f"unknown llm provider {provider!r}")
