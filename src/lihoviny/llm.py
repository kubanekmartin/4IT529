"""Adaptér LLM."""

from typing import Protocol


class Llm(Protocol):
    pass


class FakeLlm:
    pass
