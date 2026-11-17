"""Stránka Regulace přes HTTP: nálezy zapíše regulatorní agent z ukázky otevřených dat."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from test_regulatorni import (
    BASELINE_ANSWER,
    NAMES,
    STIHANI,
    TISK_321,
    assessment,
    make_regulatorni,
    send_zadani,
    source_routes,
    with_offices,
)

from lihoviny.llm import FakeLlm
from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


@pytest.fixture
def collected(bus, clock, raw_dir, make_agent):
    """Výchozí snímek 16. 11. 2026 a další snímek 17. 11., ve kterém přibyl tisk 321. V datech
    je i žádost o stíhání poslance se jménem v názvu, která na stránku nepatří."""
    llm = FakeLlm(BASELINE_ANSWER, {"tisky": [assessment(TISK_321, level="vysoký")]})
    send_zadani(bus)
    day1 = source_routes(day=1, extra_tisky=[STIHANI])
    make_regulatorni(make_agent, clock, raw_dir, llm, day1).process_pending()
    clock.advance(days=1)
    send_zadani(bus)
    day2 = source_routes(day=2, extra_tisky=[STIHANI])
    make_regulatorni(make_agent, clock, raw_dir, llm, day2).process_pending()


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


def submitted_dates(html):
    """Data předložení tisků v pořadí kartiček (bez data jako None)."""
    dates = re.findall(r"<dt>Předloženo</dt><dd>([^<]+)</dd>", html)
    return [None if d == "—" else date(*map(int, reversed(d.split(". ")))) for d in dates]


def test_regulace_sorts_by_submission_date_newest_first_by_default(web, collected):
    html = web.get("/regulace").text

    dates = submitted_dates(html)
    known = [d for d in dates if d is not None]
    assert len(known) >= 2 and known == sorted(known, reverse=True)
    assert dates[len(known) :] == [None] * (len(dates) - len(known))
    assert '<strong aria-current="true">Předloženo: nejnovější</strong>' in html
    assert 'href="/regulace?razeni=dopad"' in html

    oldest = [
        d for d in submitted_dates(web.get("/regulace?razeni=predlozeno-nejstarsi").text) if d
    ]
    assert oldest == sorted(known)


def test_regulace_sorts_by_impact_and_ignores_unknown_sort(web, collected):
    levels = re.findall(
        r"dopad (vysoký|střední|nízký)</span>", web.get("/regulace?razeni=dopad").text
    )
    assert levels == sorted(levels, key=["vysoký", "střední", "nízký"].index)

    html = web.get("/regulace?razeni=nesmysl").text
    assert '<strong aria-current="true">Předloženo: nejnovější</strong>' in html


def test_regulace_shows_tisky_by_stage_and_impact(web, collected):
    html = web.get("/regulace").text

    pipeline = html[html.index("<figcaption>Kde ve Sněmovně tisky jsou</figcaption>") :]
    pipeline = pipeline[: pipeline.index("</figure>")]
    assert "dopad vysoký" in pipeline and "<summary>Jak to číst</summary>" in pipeline
    assert "<td>1. čtení</td>" in pipeline


def test_regulace_page_has_office_news_section_even_when_empty(web, collected):
    html = web.get("/regulace").text

    assert "Zprávy úřadů" in html
    assert "Žádná zpráva úřadů se zatím lihovin netýká." in html


@pytest.fixture
def collected_with_offices(bus, clock, raw_dir, make_agent):
    """Výchozí snímek 16. 11. 2026 se sněmovními tisky i zprávami MF a Celní správy; LLM
    posoudí jako relevantní zprávu MF 65559 o spotřební dani z nafty."""
    office_answer = {"tisky": [assessment(65559, level="vysoký", effective=None)]}
    llm = FakeLlm(BASELINE_ANSWER, office_answer)
    send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm, with_offices()).process_pending()


def test_regulace_heading_names_tisky_and_office_news(web, collected_with_offices):
    html = web.get("/regulace").text

    assert "<h1>Regulace: sněmovní tisky a zprávy úřadů o alkoholu</h1>" in html
    office_news = html[html.index('id="zpravy-uradu"') :]
    assert 'href="https://mf.gov.cz/' in office_news and "65559" in office_news


def test_regulace_footer_cites_all_three_kiq4_sources(web, collected_with_offices):
    html = web.get("/regulace").text
    footer = html[html.index('<p class="source muted">') :]
    footer = footer[: footer.index("</p>")]

    assert "Poslanecká sněmovna Parlamentu České republiky" in footer
    assert "Ministerstvo financí" in footer and 'href="https://mf.gov.cz/' in footer
    assert "Celní správa" in footer and 'href="https://celnisprava.gov.cz/' in footer
    assert "data zpracována 16. 11. 2026" in footer
