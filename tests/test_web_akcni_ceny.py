"""Stránka Akční ceny přes HTTP: data na tabuli zapíše cenový agent z uložených ukázek."""

import re

import pytest
from fastapi.testclient import TestClient
from test_cenovy import FIXTURES, LISTING, make_cenovy, send_zadani, source_routes

from lihoviny.web.akcni_ceny import portfolio_pressure
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


def pressure_row(html, product):
    """Řádek produktu zadavatele v tabulce Co to znamená pro vás, i s radou pod ním."""
    start = html.index(f"<strong>{product}</strong>")
    return html[start : html.index("</td></tr>", start)]


def test_decision_box_shows_price_pressure_on_each_portfolio_product(web, collected):
    html = web.get("/akcni-ceny").text

    assert "Co to znamená pro vás" in html
    # 959 Kč/l etanolu proti Božkovu Original v Albertu za 639 Kč/l: o 33 % levněji.
    tuzemak = pressure_row(html, "Valnovský tuzemák 37,5 % 0,5 l")
    assert "▲ vysoký" in tuzemak
    assert "Božkov Original Tuzemský 37,5% 0,5l, Albert" in tuzemak
    assert "−33 %" in tuzemak
    assert "Zvažte vlastní akci" in tuzemak and "obvykle zlevňuje o" in tuzemak
    # Míchaný drink je i v akci dražší než Rum & Cola zadavatele běžně.
    rtd = pressure_row(html, "Valnov Rum &amp; Cola 5 % 0,33 l")
    assert "▼ nízký" in rtd and "+30 %" in rtd
    gin = pressure_row(html, "Valnov Dry Gin 40 % 0,7 l")
    assert "– bez akce konkurence" in gin and "v kategorii teď žádná" in gin


def test_decision_box_lists_chains_with_most_offers_and_explains_the_rules(web, collected):
    html = web.get("/akcni-ceny").text

    chains = html[html.index("Kde konkurence zlevňuje nejvíc") : html.index("Jak počítáme")]
    rows = chains.split('<tr class="chain-row">')[1:]
    assert 1 <= len(rows) <= 5
    counts = [int(r.split('<td class="num">')[1].split("<")[0]) for r in rows]
    assert counts == sorted(counts, reverse=True)
    assert "o 15 % a víc levnější" in html
    assert "Stock, Rudolf Jelínek, Fruko-Schulz, Jan Becher" in html


def offer(category, ethanol, discount=None):
    """Platná akční cena, jak ji vrací `current_offers` (spárovaná s produktem dané kategorie)."""
    return {
        "category": category,
        "manufacturer": None,
        "content": {
            "product": f"{category} v akci",
            "chain": "BILLA",
            "price_per_l_ethanol": ethanol,
            "discount_percent": discount,
        },
    }


def test_pressure_levels_follow_the_gap_to_the_regular_price():
    # Běžné ceny zadavatele za litr etanolu: tuzemák 959,47, vodka 906,13, bylinný 1 256,57.
    rows = {
        row["product"].code: row
        for row in portfolio_pressure(
            [
                offer("rum_tuzemak", 800.0, 30),  # o 17 % levněji
                offer("rum_tuzemak", 900.0, 20),
                offer("vodka", 850.0, 25),  # o 6 % levněji
                offer("horke_likery", 1300.0),  # dražší
            ]
        )
    }

    assert (rows["tuzemak-05"]["level"], rows["tuzemak-05"]["gap"]) == ("vysoky", -17)
    assert rows["tuzemak-05"]["offer_count"] == 2
    assert rows["tuzemak-05"]["typical_discount"] == 25
    assert (rows["vodka-05"]["level"], rows["vodka-05"]["gap"]) == ("stredni", -6)
    assert (rows["bylinny-05"]["level"], rows["bylinny-05"]["typical_discount"]) == ("nizky", None)
    assert (rows["gin-07"]["level"], rows["gin-07"]["cheapest"]) == ("bez_akce", None)


def main_table(html):
    return html[html.index('<table class="offers">') :]


def products_in_table(html):
    """Názvy produktů v hlavní tabulce akčních cen v pořadí řádků."""
    return re.findall(r'<td class="product">\s*<a [^>]*>([^<]+)</a>', main_table(html))


def chains_in_table(html):
    return re.findall(r'class="chain">(?:<a [^>]*>)?([^<]+)<', main_table(html))


def prices_in_table(html):
    return [
        float(p.replace("\xa0", "").replace(",", "."))
        for p in re.findall(r'<span class="price">([^<]+) Kč</span>', main_table(html))
    ]


def test_table_filters_by_chain_text_and_price_range(web, collected):
    billa = web.get("/akcni-ceny", params={"retezec": "BILLA"}).text
    assert "Zobrazeno 4 z 22 akčních cen podle filtru" in billa
    assert chains_in_table(billa) == ["BILLA"] * 4

    bozkov = products_in_table(web.get("/akcni-ceny", params={"produkt": "božkov"}).text)
    assert bozkov and all("Božkov" in name for name in bozkov)

    cheap = web.get("/akcni-ceny", params={"cena_od": "100", "cena_do": "150,5"}).text
    assert prices_in_table(cheap) and all(100 <= p <= 150.5 for p in prices_in_table(cheap))


def test_table_sorts_by_any_column_both_ways_with_missing_values_last(web, collected):
    up = prices_in_table(web.get("/akcni-ceny", params={"razeni": "cena"}).text)
    down = prices_in_table(web.get("/akcni-ceny", params={"razeni": "cena", "smer": "desc"}).text)
    assert up == sorted(up) and down == sorted(down, reverse=True)

    html = web.get("/akcni-ceny", params={"razeni": "etanol", "smer": "desc"}).text
    cells = re.findall(r'class="num ethanol">([^<]+)<', main_table(html))
    known = [float(c.replace("\xa0", "").replace(",", ".")) for c in cells if c != "—"]
    # Bez obsahu alkoholu (cena za litr etanolu „—“) jsou i při řazení sestupně až na konci.
    assert "—" in cells and cells[len(known) :] == ["—"] * (len(cells) - len(known))
    assert known == sorted(known, reverse=True)
    assert 'aria-sort="descending"' in html


def test_sort_links_keep_filters_and_flip_direction(web, collected):
    html = web.get("/akcni-ceny", params={"retezec": "BILLA", "razeni": "cena"}).text

    assert 'href="/akcni-ceny?retezec=BILLA&amp;razeni=cena&amp;smer=desc"' in html
    assert 'href="/akcni-ceny?retezec=BILLA&amp;razeni=produkt&amp;smer=asc"' in html


def test_invalid_filter_values_are_ignored_and_empty_result_says_so(web, collected):
    assert "Zobrazeno 22 z 22" in web.get("/akcni-ceny?cena_od=abc&razeni=xyz").text
    html = web.get("/akcni-ceny", params={"produkt": "neexistuje"}).text
    assert "Filtru neodpovídá žádná akční cena." in html
    # Rady pro zadavatele počítají se všemi akčními cenami, ne jen s vyfiltrovanými.
    assert "▲ vysoký" in html
