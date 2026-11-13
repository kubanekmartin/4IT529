"""Adaptér LLM: Claude API přes oficiální SDK, vždy se strukturovaným výstupem.

Model se volí pro každého agenta zvlášť: `LLM_MODEL_<AGENT>` (např. `LLM_MODEL_ANALYTICKY`),
jinak `LLM_MODEL`, jinak Claude Sonnet 5.5. Při odmítnutí modelem zkusí API samo záložní model
(`fallbacks: "default"`). V testech ho nahradí `FakeLlm` s předem připravenými odpověďmi.
"""

import os
from dataclasses import dataclass
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel

DEFAULT_MODEL = "claude-sonnet-5-5"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LlmError(Exception):
    """LLM nevrátil použitelnou odpověď; agent z ní udělá Selhání."""


class Llm(Protocol):
    def ask[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str = "", effort: Effort = "low"
    ) -> T:
        """Třídění a extrakce s `effort="low"`, psaní Briefu s vyšším."""
        ...


def model_for(agent_name: str) -> str:
    agent_key = "LLM_MODEL_" + agent_name.upper().replace("-", "_")
    return os.environ.get(agent_key) or os.environ.get("LLM_MODEL") or DEFAULT_MODEL


class ClaudeLlm:
    def __init__(
        self, *, model: str, client: anthropic.Anthropic | None = None, max_tokens: int = 16000
    ):
        self.model = model
        self._client = client
        self._max_tokens = max_tokens

    @classmethod
    def for_agent(cls, agent_name: str, **kwargs) -> "ClaudeLlm":
        return cls(model=model_for(agent_name), **kwargs)

    def ask[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str = "", effort: Effort = "low"
    ) -> T:
        response = self._get_client().beta.messages.parse(
            model=self.model,
            max_tokens=self._max_tokens,
            betas=[_FALLBACK_BETA],
            fallbacks="default",
            system=system or anthropic.omit,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort},
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LlmError(f"model odmítl odpovědět (refusal, kategorie {category})")
        if response.stop_reason == "max_tokens":
            raise LlmError(f"odpověď se nevešla do {self._max_tokens} tokenů")
        if response.parsed_output is None:
            raise LlmError(f"odpověď neodpovídá schématu {schema.__name__}")
        return response.parsed_output

    def _get_client(self) -> anthropic.Anthropic:
        # Klient až při prvním volání: agenti bez LLM (a testy) se obejdou bez API klíče.
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client


@dataclass(frozen=True)
class LlmCall:
    prompt: str
    schema: type[BaseModel]
    system: str
    effort: Effort


class FakeLlm:
    """Náhražka pro testy: vrací připravené odpovědi (slovníky nebo modely) v daném pořadí."""

    def __init__(self, *answers: dict | BaseModel):
        self._answers = list(answers)
        self.calls: list[LlmCall] = []

    def ask[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str = "", effort: Effort = "low"
    ) -> T:
        self.calls.append(LlmCall(prompt, schema, system, effort))
        if not self._answers:
            raise AssertionError(f"FakeLlm: došly připravené odpovědi (dotaz: {prompt[:60]!r})")
        answer = self._answers.pop(0)
        if isinstance(answer, BaseModel):
            answer = answer.model_dump()
        return schema.model_validate(answer)
