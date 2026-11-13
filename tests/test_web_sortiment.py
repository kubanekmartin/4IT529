"""Web přes HTTP: stránka Sortiment ukazuje Novinky a produkty podle kategorie."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from lihoviny.products import product_key
from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


def add_product(board, snapshot, source_id, name, brand, manufacturer, volume_ml, abv, category):
    return board.record_product(
        normalized_key=product_key(name, volume_ml, Decimal(abv)),
        manufacturer=manufacturer,
        brand=brand,
        name=name,
        volume_ml=volume_ml,
        abv=Decimal(abv),
        category=category,
        first_seen_url=f"https://www.rohlik.cz/{source_id}-produkt",
        first_seen_snapshot_id=snapshot.id,
        source_product_id=source_id,
    )


def test_sortiment_shows_novinky_and_products_by_category(web, board):
    snapshot = board.record_snapshot(
        agent="sortimentni", source="rohlik.cz", fetches=[], item_count=3
    )
    add_product(
        board, snapshot, "1287783", "Božkov Originál Tuzemský 37,5% obj.", "Božkov", "Stock",
        500, "37.5", "rum_tuzemak",
    )  # fmt: skip
    hennessy = add_product(
        board, snapshot, "1358451", "Hennessy V.S 40% obj.", "Hennessy", "Moët Hennessy",
        700, "40", "brandy",
    )  # fmt: skip
    modra = add_product(
        board, snapshot, "1485101", "Božkov Modrá 19 % obj.", "Božkov", "Stock",
        500, "19", "ostatni_likery",
    )  # fmt: skip
    board.record_finding(
        kiq=1,
        agent="sortimentni",
        type="novinka",
        source_url=modra.first_seen_url,
        content={"name": modra.name, "note": "podle prodejce"},
        snapshot_id=snapshot.id,
        product_id=modra.id,
    )

    page = web.get("/sortiment")

    assert page.status_code == 200
    html = page.text
    assert '<a href="/sortiment" aria-current="page">Sortiment</a>' in html
    novinky = html[html.index("<h2>Novinky") : html.index("<h2>Produkty podle kategorie")]
    assert "Božkov Modrá 19 % obj." in novinky
    assert "podle prodejce" in novinky
    assert 'href="https://www.rohlik.cz/1485101-produkt"' in novinky
    assert "Hennessy" not in novinky
    by_category = html[html.index("<h2>Produkty podle kategorie") :]
    # Kategorie v pořadí podle glosáře, produkt pod svou kategorií, i prázdné kategorie s nulou.
    order = [
        "Rum a tuzemák",
        "Božkov Originál Tuzemský",
        "Ostatní likéry",
        "Božkov Modrá",
        "Brandy a vinné destiláty",
        "Hennessy V.S",
        "RTD",
    ]
    positions = [by_category.index(text) for text in order]
    assert positions == sorted(positions)
    assert "Moët Hennessy" in by_category
    assert "0,7 l" in by_category and "40 %" in by_category and "37,5 %" in by_category
    assert "16. 11. 2026" in by_category  # poprvé viděn (pražský čas)
    assert hennessy.first_seen_url in by_category


def test_sortiment_says_so_when_agent_has_not_seen_any_products_yet(web):
    html = web.get("/sortiment").text

    assert "Zatím žádné novinky" in html
    assert "Zatím žádné produkty" in html
