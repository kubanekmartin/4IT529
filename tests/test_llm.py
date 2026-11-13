"""Adaptér LLM proti simulovanému Claude API (httpx2.MockTransport), bez skutečného volání."""

import json

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from lihoviny.llm import ClaudeLlm, FakeLlm, LlmError


class Relevance(BaseModel):
    relevantni: bool
    shrnuti: str


def fake_claude(answer_text, *, stop_reason="end_turn"):
    """Klient SDK, jehož HTTP požadavky se zapisují a dostanou připravenou odpověď."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": json.loads(request.content)["model"],
                "content": [{"type": "text", "text": answer_text}] if answer_text else [],
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        )

    client = anthropic.Anthropic(
        api_key="test-bez-hodnoty",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    return client, requests


ANSWER = json.dumps({"relevantni": True, "shrnuti": "Nová kampaň na rum."})


def test_each_agent_uses_its_configured_model_and_sonnet_by_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_ANALYTICKY", "claude-opus-5-5")
    client, requests = fake_claude(ANSWER)

    ClaudeLlm.for_agent("analyticky", client=client).ask("Napiš Brief.", Relevance)
    ClaudeLlm.for_agent("medialni", client=client).ask("Je článek o lihovinách?", Relevance)

    assert [json.loads(r.content)["model"] for r in requests] == [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
    ]


def test_request_asks_for_structured_output_with_server_side_fallback(monkeypatch):
    monkeypatch.delenv("LLM_MODEL_MEDIALNI", raising=False)
    client, requests = fake_claude(ANSWER)

    ClaudeLlm.for_agent("medialni", client=client).ask(
        "Je článek o lihovinách?", Relevance, system="Třídíš články.", effort="low"
    )

    body = json.loads(requests[0].content)
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in requests[0].headers["anthropic-beta"]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"] == "Třídíš články."


def test_structured_answer_comes_back_as_typed_object():
    client, _ = fake_claude(ANSWER)

    answer = ClaudeLlm(model="claude-sonnet-5-5", client=client).ask("Je to o rumu?", Relevance)

    assert answer == Relevance(relevantni=True, shrnuti="Nová kampaň na rum.")


def test_refusal_raises_so_the_agent_reports_failure():
    client, _ = fake_claude(None, stop_reason="refusal")

    with pytest.raises(LlmError, match="refusal"):
        ClaudeLlm(model="claude-sonnet-5-5", client=client).ask("…", Relevance)


def test_fake_llm_returns_prepared_answers_in_order_and_remembers_prompts():
    llm = FakeLlm({"relevantni": True, "shrnuti": "první"}, Relevance(relevantni=False, shrnuti=""))

    first = llm.ask("Článek 1", Relevance)
    second = llm.ask("Článek 2", Relevance)

    assert (first.shrnuti, second.relevantni) == ("první", False)
    assert [call.prompt for call in llm.calls] == ["Článek 1", "Článek 2"]
    with pytest.raises(AssertionError, match="došly"):
        llm.ask("Článek 3", Relevance)
