"""Web přes HTTP: log komunikace čte z Postgresu přes roli jen pro čtení."""

import pytest
from fastapi.testclient import TestClient

from lihoviny.agents.ozvena import Ozvena
from lihoviny.bus import Kind
from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


def test_log_groups_messages_by_conversation_newest_conversation_first(web, bus, make_agent):
    first = bus.send(
        kind=Kind.REQUEST, sender="clen-tymu", recipient="ozvena", content={"text": "ahoj"}
    )
    second = bus.send(
        kind=Kind.REQUEST, sender="clen-tymu", recipient="ozvena", content={"text": "znovu"}
    )
    make_agent(Ozvena).process_pending()

    page = web.get("/log")

    assert page.status_code == 200
    html = page.text
    assert html.count('class="conversation"') == 2
    newer = html.index(f'id="konverzace-{second.conversation_id}"')
    older = html.index(f'id="konverzace-{first.conversation_id}"')
    assert newer < older
    # Uvnitř konverzace jdou zprávy v pořadí odeslání: Zadání, pak Odpověď.
    in_newer = html[newer:older]
    assert in_newer.index("Zadání") < in_newer.index("Odpověď")
    assert in_newer.count("&#34;text&#34;: &#34;znovu&#34;") == 2
    assert "2 zprávy: clen-tymu, ozvena" in in_newer
    assert "16. 11. 2026 7:00" in html  # 6:00 UTC je 7:00 pražského času


def test_log_says_so_when_agents_have_not_talked_yet(web):
    assert "Zatím žádné zprávy" in web.get("/log").text


@pytest.mark.parametrize("path", ["/", "/log", "/neexistuje"])
def test_every_response_asks_search_engines_not_to_index(web, path):
    assert web.get(path, follow_redirects=False).headers["x-robots-tag"] == "noindex"
