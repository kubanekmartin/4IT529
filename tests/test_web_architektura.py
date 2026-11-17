"""Web přes HTTP: stránka Architektura popisuje agenty, zdroje a komunikaci (ADR 0001)."""

import re

import pytest
from fastapi.testclient import TestClient

from lihoviny.agents import AGENTS
from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


def block(html: str, element_id: str) -> str:
    """Obsah prvku s daným id až po jeho konec (karty na stránce se nevnořují)."""
    match = re.search(rf'<(\w+)[^>]*\bid="{element_id}"[^>]*>(.*?)</\1>', html, re.DOTALL)
    assert match, f"na stránce chybí prvek id={element_id}"
    return match.group(2)


# Podle specu #15 a KIT-KIQ.md: (jméno na sběrnici, popisek, fáze cyklu CI, KIQ a zdroj).
SIX_AGENTS = [
    ("ridici", "Řídicí agent", "Planning", []),
    ("sortimentni", "Sortimentní agent", "Collection a Processing", ["KIQ 1", "rohlik.cz"]),
    ("cenovy", "Cenový agent", "Collection a Processing", ["KIQ 2", "akcniceny.cz"]),
    ("medialni", "Mediální agent", "Collection a Processing", ["KIQ 3", "zboziaprodej.cz"]),
    ("regulatorni", "Regulatorní agent", "Collection a Processing", ["KIQ 4", "psp.cz"]),
    ("analyticky", "Analytický agent", "Analysis a Dissemination", []),
]


@pytest.mark.parametrize(
    ("name", "label", "phase", "source"), SIX_AGENTS, ids=[agent[0] for agent in SIX_AGENTS]
)
def test_architektura_describes_each_agent_with_role_ci_phase_and_source(
    web, name, label, phase, source
):
    page = web.get("/architektura")

    assert page.status_code == 200
    card = block(page.text, f"agent-{name}")
    assert label in card
    assert f"<code>{name}</code>" in card
    assert "Role" in card
    assert phase in card
    for fact in source:
        assert fact in card


@pytest.mark.parametrize(
    ("name", "page", "label"),
    [
        ("sortimentni", "/sortiment", "Sortiment"),
        ("cenovy", "/akcni-ceny", "Akční ceny"),
        ("medialni", "/komunikace", "Komunikace"),
        ("regulatorni", "/regulace", "Regulace"),
        ("analyticky", "/brief", "Brief"),
    ],
)
def test_agent_card_links_to_the_page_with_its_output(web, name, page, label):
    card = block(web.get("/architektura").text, f"agent-{name}")

    assert f'<a href="{page}">{label}</a>' in card


def test_every_registered_agent_except_ozvena_has_a_card(web):
    html = web.get("/architektura").text

    for name in AGENTS.keys() - {"ozvena"}:
        assert f'id="agent-{name}"' in html


def test_architektura_is_in_navigation_on_every_page(web):
    assert '<a href="/architektura">Architektura</a>' in web.get("/log").text
    assert '<a href="/architektura" aria-current="page">' in web.get("/architektura").text


def test_communication_diagram_is_inline_svg_without_javascript(web):
    html = web.get("/architektura").text

    diagram = block(html, "diagram")
    assert "<svg" in diagram
    assert "<title" in diagram
    for label in ["Řídicí agent", "Sortimentní", "Cenový", "Mediální", "Regulatorní", "Analytický"]:
        assert label in diagram
    for part in ["Sběrnice zpráv", "Sdílená tabule"]:
        assert part in diagram
    for kind in ["Zadání", "Odpověď", "Dotaz", "Selhání"]:
        assert kind in diagram
    assert "<script" not in html
    assert not re.search(r"\son[a-z]+=", html), "obsluha události je JavaScript"


@pytest.mark.parametrize(
    ("kind", "label"),
    [("REQUEST", "Zadání"), ("INFORM", "Odpověď"), ("QUERY", "Dotaz"), ("FAILURE", "Selhání")],
    ids=["REQUEST", "INFORM", "QUERY", "FAILURE"],
)
def test_page_explains_each_message_kind_by_its_glossary_name(web, kind, label):
    card = block(web.get("/architektura").text, f"druh-{kind}")

    assert label in card
    assert kind in card


def test_inform_carries_only_a_summary_because_findings_live_on_the_shared_board(web):
    html = web.get("/architektura").text

    inform = block(html, "druh-INFORM")
    assert "snapshot_id" in inform
    assert "tabul" in inform
    board = block(html, "tabule")
    assert "Nález" in board
    assert "nepřepisují" in board


def test_dotaz_becomes_new_zadani_and_the_log_is_the_live_proof(web):
    html = web.get("/architektura").text

    assert "nové Zadání" in block(html, "druh-QUERY")
    assert '<a href="/log">' in html


# Zdroje sběrných agentů po rozšíření (PR #31–#35): (agent, jak je jmenuje karta, doména).
COLLECTOR_SOURCES = [
    ("sortimentni", ["Rohlík", "alkohol.cz"], ["rohlik.cz", "alkohol.cz"]),
    ("cenovy", ["akcniceny.cz", "Kupi"], ["akcniceny.cz", "kupi.cz"]),
    (
        "regulatorni",
        ["Poslanecké sněmovny", "Ministerstvo financí", "Celní správa"],
        ["psp.cz", "mf.gov.cz", "celnisprava.gov.cz"],
    ),
]


@pytest.mark.parametrize(
    ("name", "on_card", "domains"), COLLECTOR_SOURCES, ids=[c[0] for c in COLLECTOR_SOURCES]
)
def test_collector_role_names_its_real_sources(web, name, on_card, domains):
    card = block(web.get("/architektura").text, f"agent-{name}")
    role = card[card.index("<dt>Role</dt>") : card.index("<dt>Vnější zdroj</dt>")]

    for source in on_card:
        assert source in role
    # Stejnou roli agent píše při startu do logu (stránka Log).
    for domain in domains:
        assert domain in AGENTS[name].role


def test_web_speaks_of_nine_categories_not_ten(web):
    html = web.get("/architektura").text

    assert "devíti kategorií" in block(html, "agent-sortimentni")
    for page in ["/architektura", "/prehled", "/sortiment"]:
        assert "deset" not in web.get(page).text


def test_regulatorni_card_says_it_assesses_prints_and_office_news(web):
    card = block(web.get("/architektura").text, "agent-regulatorni")

    assert "Ke každému tisku i zprávě úřadu odhadne dopad" in " ".join(card.split())
