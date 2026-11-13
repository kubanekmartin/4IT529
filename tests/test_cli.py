"""Příkaz, kterým člen týmu pošle Zadání a počká na odpověď."""

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
