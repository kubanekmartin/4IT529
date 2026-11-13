"""Příkaz `lihoviny`: ruční Zadání a ověření LLM ze serveru."""

import json

import anthropic
import httpx2

from lihoviny.agents.ozvena import Ozvena
from lihoviny.cli import main


def test_zadani_command_sends_request_and_prints_the_reply(
    monkeypatch, capsys, database_url, make_agent, start_agent
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    start_agent(make_agent(Ozvena))

    exit_code = main(["zadani", "ozvena", '{"text": "ahoj"}', "--cekat", "5"])

    assert exit_code == 0
    assert 'INFORM ozvena → clen-tymu: {"text": "ahoj"}' in capsys.readouterr().out


def test_zadani_command_exits_with_error_when_agent_reports_failure(
    monkeypatch, capsys, database_url, make_agent, start_agent
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    start_agent(make_agent(Ozvena))

    exit_code = main(["zadani", "ozvena", '{"fail": "test"}', "--cekat", "5"])

    assert exit_code == 1
    assert 'FAILURE ozvena → clen-tymu: {"reason": "test"}' in capsys.readouterr().out


def fake_claude_api(monkeypatch, answer_text, *, stop_reason="end_turn", status=200):
    """Claude API bez sítě: SDK dostane připravenou odpověď, požadavky se zapisují."""
    requests = []

    def handler(request):
        requests.append(request)
        if status != 200:
            return httpx2.Response(
                status,
                json={
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid x-api-key"},
                },
            )
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
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-bez-hodnoty")
    return requests


def test_llm_check_makes_one_real_call_with_the_agents_model(monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODEL_ANALYTICKY", "claude-opus-5-5")
    requests = fake_claude_api(monkeypatch, json.dumps({"odpoved": "Praha"}))

    exit_code = main(["overit-llm", "--agent", "analyticky"])

    assert exit_code == 0
    assert len(requests) == 1
    assert json.loads(requests[0].content)["model"] == "claude-opus-5-5"
    assert "LLM odpovídá (claude-opus-5-5): Praha" in capsys.readouterr().out


def test_llm_check_reports_rejected_api_key_without_traceback(monkeypatch, capsys):
    fake_claude_api(monkeypatch, None, status=401)

    exit_code = main(["overit-llm"])

    assert exit_code == 1
    error_output = capsys.readouterr().err
    assert "LLM neodpovídá" in error_output
    assert "invalid x-api-key" in error_output


def test_llm_check_reports_refusal_as_failure(monkeypatch, capsys):
    fake_claude_api(monkeypatch, None, stop_reason="refusal")

    exit_code = main(["overit-llm"])

    assert exit_code == 1
    assert "refusal" in capsys.readouterr().err


def test_llm_check_says_the_api_key_is_missing_without_calling_the_api(monkeypatch, capsys):
    requests = fake_claude_api(monkeypatch, json.dumps({"odpoved": "Praha"}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    exit_code = main(["overit-llm"])

    assert exit_code == 1
    assert requests == []
    assert "chybí ANTHROPIC_API_KEY" in capsys.readouterr().err
