"""Stránka Akční ceny přes HTTP: data na tabuli zapíše cenový agent z uložených ukázek."""

import pytest
from fastapi.testclient import TestClient
from test_cenovy import FIXTURES, LISTING, make_cenovy, send_zadani, source_routes

from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


@pytest.fixture
def collected(bus, clock, raw_dir, make_agent):
    """Cenový agent obslouží jedno Zadání nad ukázkami (snímek 16. 11. 2026 7:00 pražského času)."""
    send_zadani(bus)
    make_cenovy(make_agent, clock, raw_dir).process_pending()


def row(html, product, chain):
    """Řádek tabulky s akční cenou daného produktu v daném řetězci."""
    (found,) = [r for r in html.split("<tr>") if f">{product}</a>" in r and f">{chain}<" in r]
    return found[: found.index("</tr>")]


def test_akcni_ceny_page_shows_chain_validity_and_litre_prices(web, collected):
    page = web.get("/akcni-ceny")

    assert page.status_code == 200
    html = page.text
    assert '<a href="/akcni-ceny" aria-current="page">Akční ceny</a>' in html
    assert "16. 11. 2026 7:00" in html  # čas posledního snímku
    tradicni = row(html, "Božkov Tradiční 35 % 0,5l", "BILLA")
    for text in ["139,90 Kč", "do 17. 11. 2026", "0,5 l", "35 %", "279,80", "799,43"]:
        assert text in tradicni
    assert 'href="https://www.akcniceny.cz/akce/bozkov-tradicni-35-0-5l/"' in tradicni


def test_akcni_ceny_page_shows_card_price_future_start_and_unknown_ethanol_price(web, collected):
    html = web.get("/akcni-ceny").text

    assert "3\xa0546,67" in row(html, "Míchaný drink 4,5% 250 ml", "Albert")
    captain = row(html, "Captain Morgan Original Spiced Gold 0,7l", "Tesco")
    assert "428,43" in captain
    assert captain.count("—") == 2  # obsah alkoholu ani cenu za litr etanolu neznáme
    fernet = [r for r in html.split("<tr>") if "Fernet Stock 0,5l" in r and "Billa Bonus Club" in r]
    assert len(fernet) == 1
    assert "Billa Bonus Club: 99,90 Kč" in fernet[0]
    assert "16. 11. – 17. 11. 2026" in fernet[0]


def test_akcni_ceny_page_hides_akcni_ceny_that_ended_before_the_last_snapshot(web, collected):
    html = web.get("/akcni-ceny").text

    # Lidl měl Božkov Original jen do 15. 11., Košík.cz Cointreau do 13. 11.
    assert "Lidl" not in html
    assert "Cointreau" not in html
    assert "Albert" in html


def test_akcni_cena_without_end_date_stays_on_the_page_while_the_latest_snapshot_has_it(
    web, bus, clock, raw_dir, make_agent
):
    def collect(routes):
        send_zadani(bus)
        make_cenovy(make_agent, clock, raw_dir, routes).process_pending()
        clock.advance(days=1)

    without_end = source_routes()
    without_end[LISTING.format("michany-napoj")] = (
        (FIXTURES / "michany-napoj.html").read_bytes().replace(b"Plat\xc3\xad do:", b"Platnost:")
    )
    collect(without_end)
    collect(without_end)  # nález vznikl v prvním snímku, druhý ho obsahuje znovu

    assert "neuvedeno" in row(web.get("/akcni-ceny").text, "Míchaný drink 4,5% 250 ml", "Albert")

    collect(source_routes())  # zdroj už uvádí konec platnosti: jiná akční cena

    assert "neuvedeno" not in web.get("/akcni-ceny").text


def test_akcni_ceny_page_says_so_before_first_snapshot(web):
    html = web.get("/akcni-ceny").text

    assert "Cenový agent zatím nezachytil žádné akční ceny." in html
