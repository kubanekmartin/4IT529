"""Stránka Přehled přes HTTP: dlaždice, grafy po KIQ a stav sběru z nálezů na tabuli.

Akční ceny, články a sněmovní tisky zapíší skuteční agenti z uložených ukázek (jako
v testech jednotlivých stránek), produkty a Novinky test (jako sortimentní agent).
"""

import pytest
from fastapi.testclient import TestClient
from test_cenovy import make_cenovy
from test_cenovy import send_zadani as send_cenovy
from test_medialni import STOCK_MODERNIZUJE, irrelevant, make_medialni
from test_medialni import send_zadani as send_medialni
from test_regulatorni import BASELINE_ANSWER, make_regulatorni
from test_regulatorni import send_zadani as send_regulatorni
from test_web_sortiment import add_product

from lihoviny.bus import Kind
from lihoviny.llm import FakeLlm
from lihoviny.web.app import create_app


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


@pytest.fixture
def collected(bus, board, clock, raw_dir, make_agent):
    """Snímky všech čtyř sběrných agentů 16. 11. 2026: dvě Novinky (Stock a Metelka),
    akční ceny z akcniceny.cz, článek o Stocku a sněmovní tisky."""
    snapshot = board.record_snapshot(
        agent="sortimentni", source="rohlik.cz", fetches=[], item_count=3
    )
    add_product(
        board, snapshot, 1287783, "Božkov Originál Tuzemský 37,5% obj.", "Božkov", "Stock",
        500, "37.5", "rum_tuzemak",
    )  # fmt: skip
    for source_id, name, brand, maker in [
        (1485101, "Božkov Modrá 19 % obj.", "Božkov", "Stock"),
        (1485268, "Metelka Štrůdl 14 % obj.", "Metelka Likéry", "Metelka"),
    ]:
        product = add_product(
            board, snapshot, source_id, name, brand, maker, 500, "19", "ostatni_likery"
        )
        board.record_finding(
            kiq=1,
            agent="sortimentni",
            type="novinka",
            source_url=product.first_seen_url,
            content={"name": name},
            snapshot_id=snapshot.id,
            product_id=product.id,
        )
    send_cenovy(bus)
    make_cenovy(make_agent, clock, raw_dir).process_pending()
    send_medialni(bus)
    medialni, _ = make_medialni(
        make_agent, clock, raw_dir, "clanky-den1.json", STOCK_MODERNIZUJE, irrelevant()
    )
    medialni.process_pending()
    send_regulatorni(bus)
    make_regulatorni(make_agent, clock, raw_dir, FakeLlm(BASELINE_ANSWER)).process_pending()


def section(html, heading):
    """Část stránky od nadpisu sekce (odkaz v `<h2>`) po další sekci."""
    start = html.index(f">{heading}</a></h2>")
    end = html.find("<section", start)
    return html[start : end if end != -1 else len(html)]


def chart(html, title):
    """Graf (figure) s daným titulkem."""
    start = html.index(f"<figcaption>{title}</figcaption>")
    return html[start : html.index("</figure>", start)]


def tile(html, label):
    """Dlaždice s daným popiskem."""
    end = html.index(f'<span class="label">{label}</span>')
    return html[html.rindex('class="tile"', 0, end) : html.index("</a>", end)]


def test_home_is_prehled_with_its_link_in_navigation(web):
    assert web.get("/", follow_redirects=False).headers["location"] == "/prehled"
    page = web.get("/prehled")
    assert page.status_code == 200
    assert '<a href="/prehled" aria-current="page">Přehled</a>' in page.text


def test_tiles_show_headline_numbers_and_link_to_detail_pages(web, collected):
    html = web.get("/prehled").text

    assert '<span class="value">3</span>' in tile(html, "sledovaných produktů")
    assert 'href="/sortiment"' in tile(html, "sledovaných produktů")
    assert '<span class="value">2</span>' in tile(html, "novinek za 7 dní")
    assert "do 16. 11. 2026" in tile(html, "novinek za 7 dní")
    offers = tile(html, "platných akčních cen")
    assert 'href="/akcni-ceny"' in offers
    assert "řetězců" in offers
    assert " %</span>" in tile(html, "průměrná sleva")
    assert '<span class="value">1</span>' in tile(html, "článků za 30 dní")
    tisky = tile(html, "sněmovních tisků o alkoholu")
    assert '<span class="value">4</span>' in tisky
    assert "z toho 2 s vysokým dopadem" in tisky


def test_every_kiq_has_freshness_and_charts_or_a_link_to_them(web, collected):
    html = web.get("/prehled").text

    for heading, href, source in [
        ("KIQ 1: Kde konkurence rozšiřuje sortiment", "/sortiment", "rohlik.cz, alkohol.cz"),
        ("KIQ 2: Kdo zlevňuje a za kolik", "/akcni-ceny", "akcniceny.cz"),
        ("KIQ 3: O čem a kde konkurence mluví", "/komunikace", "zboziaprodej.cz"),
        ("KIQ 4: Co a kdy změní pravidla trhu", "/regulace", "psp.cz"),
    ]:
        block = section(html, heading)
        assert f'<a href="{href}">{heading}</a>' in html
        assert f"Data k 16. 11. 2026 7:00:00 ({source})" in block
        if heading.startswith("KIQ 2"):
            # Grafy akčních cen má Brief; Přehled je neopakuje a odkáže na ně.
            assert "<svg" not in block and '<a href="/brief">Brief →</a>' in block
        else:
            assert "<summary>Jak to číst</summary>" in block
            assert "<summary>Data grafu</summary>" in block

    # Co je v Briefu, Přehled neopakuje.
    for title in ["Novinky podle kategorie", "Platné akční ceny podle řetězce", "Cena za litr"]:
        assert f"<figcaption>{title}" not in html


def test_novinky_by_manufacturer_highlights_watched_competitors(web, collected):
    makers = chart(web.get("/prehled").text, "Novinky podle výrobce")

    assert "sledovaný konkurent" in makers and "ostatní výrobci" in makers
    # Zvýraznění je i textem v tabulce dat, nejen barvou pruhu.
    assert "<td>Stock (sledovaný konkurent)</td>" in makers
    assert "<td>Metelka</td>" in makers
    assert 'class="s1"' in makers and 'class="s0"' in makers


def test_tisky_timeline_orders_by_effective_date_and_names_the_impact(web, collected):
    timeline = chart(web.get("/prehled").text, "Termíny účinnosti sněmovních tisků")

    rows = timeline[timeline.index("<tbody>") :].split("<tr>")[1:]
    assert len(rows) == 4
    assert "Tisk 133" in rows[0] and "1. 1. 2027" in rows[0] and "dopad vysoký" in rows[0]
    # Tisk bez termínu účinnosti je poslední a říká to.
    assert "Tisk 193" in rows[-1] and "termín účinnosti neuveden" in rows[-1]


def test_stav_sberu_shows_last_snapshot_and_reply_of_each_collector(web, bus, collected):
    bus.send(
        kind=Kind.FAILURE,
        sender="sortimentni",
        recipient="ridici",
        content={"reason": "rohlik.cz nedostupný"},
    )

    health = section(web.get("/prehled").text, "Stav sběru")

    rows = health[health.index("<tbody>") :].split("<tr>")[1:]
    assert [r.split("<td>")[1].split("</td>")[0] for r in rows] == [
        "KIQ 1 Sortiment",
        "KIQ 2 Akční ceny",
        "KIQ 3 Komunikace",
        "KIQ 4 Regulace",
    ]
    assert "✕ Selhání" in rows[0] and "rohlik.cz nedostupný" in rows[0]
    assert all("✓ v pořádku" in row for row in rows[1:])
    assert "16. 11. 2026 7:00:00" in rows[1]


def test_prehled_without_any_data_says_so_everywhere(web):
    page = web.get("/prehled")

    assert page.status_code == 200
    html = page.text
    assert '<span class="value">0</span>' in tile(html, "sledovaných produktů")
    assert "zatím bez dat" in tile(html, "sledovaných produktů")
    assert '<span class="value">—</span>' in tile(html, "průměrná sleva")
    assert html.count("Zatím bez dat") == 4
    assert "bez dat" in chart(html, "Novinky podle výrobce")
    assert "zatím žádný" in section(html, "Stav sběru")


def test_prehled_explains_what_the_tool_does_and_how_to_read_it(web):
    html = web.get("/prehled").text
    about = html[html.index('<section class="about"') : html.index("</section>")]

    assert "Co nástroj dělá" in about and "Jak číst tuto stránku" in about
    for kiq in ["KIQ 1 Sortiment", "KIQ 2 Akční ceny", "KIQ 3 Komunikace", "KIQ 4 Regulace"]:
        assert kiq in about
    assert "Stock, Rudolf Jelínek, Fruko-Schulz, Jan Becher" in about
    assert "<summary>Pojmy</summary>" in about
    assert "<summary>Co nástroj nevidí</summary>" in about
