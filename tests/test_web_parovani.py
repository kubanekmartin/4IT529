"""Web přes HTTP: spárované akční ceny u produktu, kategorie u akční ceny a nespárované zvlášť.

Produkty zapisuje test, akční ceny cenový agent z uložených ukázek (snímek 16. 11. 2026).
"""

import pytest
from fastapi.testclient import TestClient
from test_parovani import cenovy_with, collect, record_product
from test_web_akcni_ceny import row

from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


@pytest.fixture
def products(bus, board, clock, raw_dir, make_agent):
    """Dva rumy v sortimentu a jedno Zadání cenovému agentovi; ostatní akční ceny produkt nemají."""
    paired = {
        "tuzemak": record_product(
            board, "Božkov Originál Tuzemský 37,5% obj.", 500, "37.5",
            brand="Božkov", category="rum_tuzemak", manufacturer="Stock", source_id=1287783,
        ),
        "captain": record_product(
            board, "Captain Morgan Original Spiced Gold 35% obj.", 700, "35",
            brand="Captain Morgan", category="rum_tuzemak", manufacturer="Diageo",
            source_id=1287790,
        ),
        "hennessy": record_product(
            board, "Hennessy V.S 40% obj.", 700, "40",
            brand="Hennessy", category="brandy", source_id=1358451,
        ),
    }  # fmt: skip
    collect(bus, cenovy_with(make_agent, clock, raw_dir))
    return paired


def test_akcni_ceny_page_shows_category_of_paired_akcni_cena_and_marks_unpaired(web, products):
    html = web.get("/akcni-ceny").text

    tuzemak = row(html, "Božkov Original Tuzemský 37,5% 0,5l", "Albert")
    assert "Rum a tuzemák" in tuzemak
    assert f'href="/sortiment#produkt-{products["tuzemak"].id}"' in tuzemak
    # Obsah alkoholu doplnil produkt ze sortimentu.
    captain = row(html, "Captain Morgan Original Spiced Gold 0,7l", "Tesco")
    assert "35 %" in captain and "ze sortimentu" in captain
    assert "428,43" in captain
    assert "nespárováno" in row(html, "Jägermeister 700ml", "Kaufland")
    assert '<a href="/akcni-ceny/nesparovane">' in html


def test_unpaired_akcni_ceny_are_listed_separately_once_per_product_name(web, products):
    page = web.get("/akcni-ceny/nesparovane")

    assert page.status_code == 200
    html = page.text
    assert "Jägermeister 700ml" in html
    assert "Fernet Stock 0,5l, vybrané druhy" in html
    jagermeister = html[html.index("Jägermeister 700ml") :]
    jagermeister = jagermeister[: jagermeister.index("</li>")]
    assert "Albert, Kaufland, Penny Market" in jagermeister  # řetězce s akční cenou
    assert "likéry" in jagermeister  # výpis u zdroje, ve kterém akční cena byla
    # Spárované akční ceny ani ty, které skončily před posledním snímkem, tu nejsou.
    assert "Božkov Original Tuzemský" not in html
    assert "Captain Morgan" not in html
    assert "Cointreau" not in html


def product_item(html, product):
    """Položka produktu v seznamu podle kategorie (až po další produkt nebo konec kategorie)."""
    start = html.index(f'id="produkt-{product.id}"')
    ends = [html.find(end, start) for end in ['<li class="item"', "</details>"]]
    return html[start : min(end for end in ends if end != -1)]


def test_sortiment_shows_current_akcni_ceny_at_their_product(web, products):
    html = web.get("/sortiment").text

    item = product_item(html, products["tuzemak"])
    assert "Božkov Originál Tuzemský 37,5% obj." in item
    for text in ["Albert", "119,90 Kč", "109,90 Kč", "do 17. 11. 2026", "Ratio", "169,90 Kč"]:
        assert text in item
    assert "Lidl" not in item  # skončila 15. 11., před posledním snímkem
    assert "Kč" not in product_item(html, products["hennessy"])
    rum = html[html.index("Rum a tuzemák") :]
    assert "2 s akční cenou" in rum[: rum.index("</summary>")]
