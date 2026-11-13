"""Stránka Regulace přes HTTP: nálezy zapíše regulatorní agent z ukázky otevřených dat."""

import pytest
from fastapi.testclient import TestClient
from test_regulatorni import (
    BASELINE_ANSWER,
    NAMES,
    TISK_321,
    assessment,
    make_regulatorni,
    send_zadani,
    source_routes,
)

from lihoviny.llm import FakeLlm
from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


@pytest.fixture
def collected(bus, clock, raw_dir, make_agent):
    """Výchozí snímek 16. 11. 2026 a další snímek 17. 11., ve kterém přibyl tisk 321."""
    llm = FakeLlm(BASELINE_ANSWER, {"tisky": [assessment(TISK_321, level="vysoký")]})
    send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm).process_pending()
    clock.advance(days=1)
    send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm, source_routes(day=2)).process_pending()


def card(html, number):
    """Kartička tisku s daným číslem."""
    (found,) = [c for c in html.split("<article") if f">Tisk {number}<" in c]
    return found[: found.index("</article>")]


def test_regulace_page_shows_tisky_with_impact_effective_date_and_source(web, collected):
    page = web.get("/regulace")

    assert page.status_code == 200
    html = page.text
    assert '<a href="/regulace" aria-current="page">Regulace</a>' in html
    spotrebni_dane = card(html, "133")
    assert 'href="https://www.psp.cz/sqw/historie.sqw?o=10&amp;t=133"' in spotrebni_dane
    for text in [
        "Novela z. o spotřebních daních",
        "Návrh zákona",
        "skupina poslanců (ODS)",
        "10. 3. 2026",
        "1. čtení (§ 90, odst. 2) - ob. rozprava",
        "vysoký",
        "Odhad dopadu tisku 43256 na výrobce lihovin.",
        "1. 1. 2027",
        "obvyklý termín u daňových novel",
        "spotřební daň",
        "výchozí snímek",
    ]:
        assert text in spotrebni_dane
    assert "výchozí snímek" not in card(html, "321")
    assert "Tisk 243" not in html  # prodejní doba: podle LLM se lihovin netýká
    # Nejnovější nález první.
    assert html.index(">Tisk 321<") < html.index(">Tisk 133<")


def test_regulace_page_cites_source_as_open_data_licence_requires(web, collected):
    html = web.get("/regulace").text

    assert "Poslanecká sněmovna Parlamentu České republiky" in html
    assert 'href="https://www.psp.cz/sqw/hp.sqw?k=1300"' in html
    assert "data zpracována 17. 11. 2026" in html  # datum posledního snímku


def test_regulace_page_shows_party_instead_of_names_of_mps(web, collected):
    html = web.get("/regulace").text

    assert "poslanec (ANO 2011)" in card(html, "193")
    for name in NAMES:
        assert name not in html


def test_regulace_page_says_so_before_first_snapshot(web):
    html = web.get("/regulace").text

    assert "Regulatorní agent zatím nestáhl data Sněmovny." in html
