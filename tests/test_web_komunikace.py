"""Web přes HTTP: stránka Komunikace ukazuje články o lihovinách od mediálního agenta."""

import pytest
from fastapi.testclient import TestClient

from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


def add_clanek(board, snapshot, article_id, published_at, **content):
    return board.record_finding(
        kiq=3,
        agent="medialni",
        type="clanek",
        snapshot_id=snapshot.id,
        source_url=f"https://www.zboziaprodej.cz/?p={article_id}",
        content={"article_id": article_id, "published_at": published_at, **content},
    )


def test_komunikace_shows_clanky_newest_first_with_channel_topic_and_summary(web, board):
    snapshot = board.record_snapshot(
        agent="medialni", source="zboziaprodej.cz", fetches=[], item_count=30
    )
    add_clanek(
        board,
        snapshot,
        83123,
        "2026-10-22T07:31:41+00:00",
        title="Stock Plzeň-Božkov modernizuje výrobu a rozšiřuje produktovou nabídku",
        manufacturers=["Stock"],
        brands=["Božkov", "Limoncè"],
        channel="pr",
        topic="portfolio",
        summary="Stock navýšil kapacitu linky na RTD o 70 %. Výrobu tequily přesunul do Plzně.",
    )
    add_clanek(
        board,
        snapshot,
        83182,
        "2026-10-26T14:07:48+00:00",
        title="Zboží&Prodej 10/2026: Mladí přepisují pravidla pití alkoholu",
        manufacturers=["Rudolf Jelínek", "Jan Becher"],
        brands=[],
        channel="socialni_site",
        topic="trh",
        summary="Trh s alkoholem klesl o dvě procenta. Výrobci odpovídají lehčími nápoji.",
    )
    board.record_finding(
        kiq=2,
        agent="cenovy",
        type="akcni_cena",
        source_url="https://www.akcniceny.cz/akce/fernet/",
        content={"product": "Fernet Stock 0,5l"},
    )

    page = web.get("/komunikace")

    assert page.status_code == 200
    html = page.text
    assert '<a href="/komunikace" aria-current="page">Komunikace</a>' in html
    assert "30 článků" in html
    assert "Fernet Stock 0,5l" not in html
    # Novější článek první; u každého datum vydání, výrobci, značky, kanál, téma a shrnutí.
    first, second = html.split('<li class="item">')[1:]
    assert "Mladí přepisují pravidla pití alkoholu" in first
    assert "Stock Plzeň-Božkov modernizuje výrobu" in second
    assert "26. 10. 2026" in first
    assert "Rudolf Jelínek, Jan Becher" in first
    assert "Sociální sítě" in first
    assert "Trh a spotřebitelé" in first
    assert "Trh s alkoholem klesl o dvě procenta." in first
    assert 'href="https://www.zboziaprodej.cz/?p=83123"' in second
    assert "22. 10. 2026" in second
    assert "Stock · Božkov, Limoncè" in second
    assert "PR a oborový tisk" in second
    assert "Produkty a portfolio" in second


def test_komunikace_without_clanky_says_so(web):
    page = web.get("/komunikace")

    assert page.status_code == 200
    assert "Zatím žádné články o lihovinách." in page.text
