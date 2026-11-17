"""Sortimentní agent (KIQ 1) přes sběrnici a tabuli nad uloženými ukázkami rohlik.cz.

`den1/` jsou zkrácené skutečné stránky kategorie Lihoviny a jejích podkategorií,
`den2/` jejich upravené kopie pro další snímek (vyprodaný produkt, nové produkty ve výpisu).
"""

import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from lihoviny.agents.sortimentni import Sortimentni
from lihoviny.bus import Kind
from lihoviny.fetch import FakeFetcher, FetchError
from lihoviny.llm import FakeLlm

FIXTURES = Path(__file__).parent / "fixtures" / "rohlik"
PAGES = {
    "lihoviny": "https://www.rohlik.cz/c300108072-lihoviny",
    "darkova-baleni": "https://www.rohlik.cz/c300119057-darkova-baleni",
    "rum": "https://www.rohlik.cz/c300108075-rum-rumove-likery-a-rumove-elixiry",
    "vodka": "https://www.rohlik.cz/c300108080-vodka",
    "likery": "https://www.rohlik.cz/c300108078-likery-a-medoviny",
}

# Odpovědi LLM k výchozímu snímku: kategorie a značka nejasných produktů, pak výrobci značek.
BASELINE_CLASSIFICATION = {
    "products": [
        {"id": "1422473", "category": "vodka", "brand": "Pražská"},
        {"id": "1352557", "category": "horke_likery", "brand": "Fernet Stock"},
        {"id": "1485101", "category": "ostatni_likery", "brand": "Božkov"},
        {"id": "1447904", "category": "horke_likery", "brand": "Jägermeister"},
        {"id": "1358451", "category": "brandy", "brand": "Hennessy"},
    ]
}
BASELINE_MANUFACTURERS = {
    "brands": [
        {"brand": "Božkov", "manufacturer": "Stock"},
        {"brand": "Pražská", "manufacturer": None},
        {"brand": "Fernet Stock", "manufacturer": "Stock"},
        {"brand": "R. Jelínek", "manufacturer": "Rudolf Jelínek"},
        {"brand": "Becherovka", "manufacturer": "Jan Becher"},
        {"brand": "Jägermeister", "manufacturer": "Mast-Jägermeister"},
        {"brand": "Hennessy", "manufacturer": "Moët Hennessy"},
    ]
}
# Ve druhém dni přibyly v likérech dva produkty; značka Fernet Stock už výrobce má.
SECOND_DAY_LLM = (
    {
        "products": [
            {"id": "1485268", "category": "ostatni_likery", "brand": "Metelka Likéry"},
            {"id": "1352613", "category": "horke_likery", "brand": "Fernet Stock"},
        ]
    },
    {"brands": [{"brand": "Metelka Likéry", "manufacturer": "Metelka"}]},
)


def rohlik(raw_dir, clock, *, day=None, broken=None):
    """Náhražka rohlik.cz: stránky z `den1/`, vybrané (`day={"vodka": "den2"}`) z jiného dne,
    nebo místo stránky výjimka či jiné bajty (`broken={"vodka": FetchError(...)}`)."""
    day = day or {}
    routes = {url: FIXTURES / day.get(page, "den1") / f"{page}.html" for page, url in PAGES.items()}
    for page, route in (broken or {}).items():
        routes[PAGES[page]] = route
    return FakeFetcher(routes, raw_dir=raw_dir, clock=clock)


def run_zadani(bus, agent):
    zadani = bus.send(kind=Kind.REQUEST, sender="ridici", recipient="sortimentni", content={})
    agent.process_pending()
    return bus.conversation(zadani.conversation_id)[-1]


def baseline(bus, clock, raw_dir, make_agent):
    """Výchozí snímek, po kterém hodiny poskočí o den."""
    return next_day(
        bus,
        clock,
        raw_dir,
        make_agent,
        llm=FakeLlm(BASELINE_CLASSIFICATION, BASELINE_MANUFACTURERS),
    )


def next_day(bus, clock, raw_dir, make_agent, *, day=None, llm=None):
    """Jeden denní snímek (stránky podle `day`), po kterém hodiny poskočí o den."""
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock, day=day), llm=llm or FakeLlm())
    reply = run_zadani(bus, agent)
    clock.advance(days=1)
    return reply


def novinky(board, snapshot_id):
    return [
        (f.content["name"], f.content.get("note"))
        for f in board.findings(kiq=1, snapshot_id=snapshot_id)
        if f.type == "novinka"
    ]


def test_first_snapshot_reports_only_novinky_labelled_by_seller(
    bus, board, clock, raw_dir, make_agent
):
    reply = baseline(bus, clock, raw_dir, make_agent)

    assert reply.kind is Kind.INFORM
    snapshot = board.snapshot(reply.content["snapshot_id"])
    assert reply.content == {"snapshot_id": snapshot.id, "new_findings": 2, "item_count": 8}
    assert (snapshot.agent, snapshot.source, snapshot.taken_at) == (
        "sortimentni",
        "rohlik.cz",
        clock.now() - timedelta(days=1),
    )
    assert len(snapshot.raw_paths) == 5  # Lihoviny a čtyři podkategorie, bez nealko nápojů a RTD
    assert sorted(novinky(board, snapshot.id)) == [
        ("Božkov Modrá 19 % obj.", "podle prodejce"),
        ("Božkov Na pečení 20 % obj.", "podle prodejce"),
    ]


def test_products_get_category_from_source_and_llm_decides_only_unclear_ones(
    bus, board, clock, raw_dir, make_agent
):
    llm = FakeLlm(BASELINE_CLASSIFICATION, BASELINE_MANUFACTURERS)
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock), llm=llm)

    reply = run_zadani(bus, agent)

    products = board.products()
    described = {
        p.name: (p.manufacturer, p.brand, p.volume_ml, p.abv, p.category) for p in products
    }
    # Jen lihoviny (aspoň 15 % obj.): Cuba Libre 4,5 % mezi rumy ani Kozičky 3,1 % mezi likéry
    # nejsou, stránka RTD se nestahuje.
    assert described == {
        "Božkov Originál Tuzemský 37,5% obj.": (
            "Stock",
            "Božkov",
            500,
            Decimal("37.5"),
            "rum_tuzemak",
        ),
        "Božkov Na pečení 20 % obj.": ("Stock", "Božkov", 500, Decimal(20), "rum_tuzemak"),
        "Božkov Vodka 37,5% obj.": ("Stock", "Božkov", 500, Decimal("37.5"), "vodka"),
        "Pražská Jemná 30% obj.": (None, "Pražská", 500, Decimal(30), "vodka"),
        "Fernet Stock Original 35% obj.": (
            "Stock",
            "Fernet Stock",
            500,
            Decimal(35),
            "horke_likery",
        ),
        "Božkov Modrá 19 % obj.": ("Stock", "Božkov", 500, Decimal(19), "ostatni_likery"),
        "Jägermeister 35 % obj. dárkové balení se 2 panáky": (
            "Mast-Jägermeister",
            "Jägermeister",
            700,
            Decimal(35),
            "horke_likery",
        ),
        "Hennessy V.S 40% obj.": ("Moët Hennessy", "Hennessy", 700, Decimal(40), "brandy"),
    }
    assert board.snapshot(reply.content["snapshot_id"]).item_count == len(products)
    hennessy = next(p for p in products if p.brand == "Hennessy")
    assert (hennessy.first_seen_at, hennessy.first_seen_url, hennessy.source_product_id) == (
        clock.now(),
        "https://www.rohlik.cz/1358451-hennessy-v-s-40-obj",
        1358451,
    )
    # Třídění a určení výrobce je extrakce: nízký effort podle specu.
    assert [call.effort for call in llm.calls] == ["low", "low"]
    # LLM dostane jen nejasné produkty (podle ID u zdroje): značka mimo filtr zdroje,
    # likéry (hořké, nebo ostatní?) a dárková balení.
    asked_about_category = llm.calls[0].prompt
    for unclear in [
        "1422473",  # Pražská Jemná: ve filtru je jen značka „Pražská vodka“
        "1352557",  # Fernet Stock Original
        "1485101",  # Božkov Modrá
        "1447904",  # Jägermeister v dárkovém balení
        "1358451",  # Hennessy V.S v dárkových baleních
    ]:
        assert f'"{unclear}"' in asked_about_category
    for clear in [
        "1287783",  # Božkov Originál Tuzemský v rumech
        "1485102",  # Božkov Na pečení v rumech
        "731079",  # Božkov Vodka ve vodkách
        "1440853",  # Božkov Republica Cuba Libre 4,5 % v rumech: není lihovina
        "1462285",  # R. Jelínek Kozičky 3,1 % v likérech: není lihovina
    ]:
        assert f'"{clear}"' not in asked_about_category


def test_abv_marked_obj_wins_over_other_percentages_in_the_name(
    bus, board, clock, raw_dir, make_agent
):
    rum = (FIXTURES / "den1" / "rum.html").read_bytes()
    rum = rum.replace(
        '"Božkov Originál Tuzemský 37,5% obj."'.encode(),
        '"Božkov Originál Tuzemský 100% z cukrové třtiny 37,5% obj."'.encode(),
    )
    llm = FakeLlm(BASELINE_CLASSIFICATION, BASELINE_MANUFACTURERS)
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock, broken={"rum": rum}), llm=llm)

    run_zadani(bus, agent)

    (tuzemak,) = [p for p in board.products() if "cukrové třtiny" in p.name]
    assert (tuzemak.abv, tuzemak.normalized_key) == (
        Decimal("37.5"),
        "bozkov original tuzemsky z cukrove trtiny|500|37.5",
    )


def test_sold_out_product_that_comes_back_is_not_novinka(bus, board, clock, raw_dir, make_agent):
    baseline(bus, clock, raw_dir, make_agent)

    sold_out = next_day(bus, clock, raw_dir, make_agent, day={"vodka": "den2"})
    back = next_day(bus, clock, raw_dir, make_agent)

    assert (sold_out.kind, sold_out.content["new_findings"]) == (Kind.INFORM, 0)
    assert (back.kind, back.content["new_findings"]) == (Kind.INFORM, 0)
    assert novinky(board, back.content["snapshot_id"]) == []
    assert [p.name for p in board.products()].count("Pražská Jemná 30% obj.") == 1


def test_product_first_seen_after_baseline_is_novinka(bus, board, clock, raw_dir, make_agent):
    baseline(bus, clock, raw_dir, make_agent)
    seen_at = clock.now()

    reply = next_day(
        bus, clock, raw_dir, make_agent, day={"likery": "den2"}, llm=FakeLlm(*SECOND_DAY_LLM)
    )

    assert reply.kind is Kind.INFORM
    assert reply.content["new_findings"] == 1
    (finding,) = board.findings(kiq=1, snapshot_id=reply.content["snapshot_id"])
    assert (finding.type, finding.source_url, finding.found_at) == (
        "novinka",
        "https://www.rohlik.cz/1485268-metelka-likery-cesky-jablecny-strudl-15-obj",
        seen_at,
    )
    assert finding.content == {
        "name": "Metelka Likéry Český jablečný štrůdl 15 % obj.",
        "brand": "Metelka Likéry",
        "manufacturer": "Metelka",
        "category": "ostatni_likery",
        "volume_ml": 500,
        "abv": 15.0,
        "seller_label": True,
    }
    product = next(p for p in board.products() if p.id == finding.product_id)
    assert (product.name, product.first_seen_at) == (finding.content["name"], seen_at)


def test_product_listed_before_tracking_is_not_novinka_when_it_first_shows_up_later(
    bus, board, clock, raw_dir, make_agent
):
    """Fernet Stock Citrus má u zdroje starší ID než výchozí snímek, jen dřív nebyl ve výpisu."""
    baseline(bus, clock, raw_dir, make_agent)
    llm = FakeLlm(*SECOND_DAY_LLM)

    reply = next_day(bus, clock, raw_dir, make_agent, day={"likery": "den2"}, llm=llm)

    assert "Fernet Stock Citrus Bylinný likér 27% obj." not in [
        name for name, _ in novinky(board, reply.content["snapshot_id"])
    ]
    citrus = next(p for p in board.products() if "Citrus" in p.name)
    assert (citrus.manufacturer, citrus.category) == ("Stock", "horke_likery")
    assert "Fernet Stock" not in llm.calls[1].prompt  # výrobce značky už tabule zná


def test_gift_pack_of_same_volume_is_the_same_product(bus, board, clock, raw_dir, make_agent):
    baseline(bus, clock, raw_dir, make_agent)

    reply = next_day(
        bus, clock, raw_dir, make_agent, day={"likery": "den2"}, llm=FakeLlm(*SECOND_DAY_LLM)
    )

    assert reply.kind is Kind.INFORM  # „Jägermeister 35% obj.“ 0,7 l už je známý produkt
    (jagermeister,) = [p for p in board.products() if p.brand == "Jägermeister"]
    assert jagermeister.name == "Jägermeister 35 % obj. dárkové balení se 2 panáky"


def test_source_failure_becomes_failure_and_leaves_board_untouched(
    bus, board, clock, raw_dir, make_agent
):
    error = FetchError(f"{PAGES['vodka']}: 3 pokusy selhaly, naposledy HTTP 429")
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock, broken={"vodka": error}))

    reply = run_zadani(bus, agent)

    assert (reply.kind, reply.content) == (Kind.FAILURE, {"reason": str(error)})
    assert board.snapshots(agent="sortimentni") == []
    assert board.products() == []


def test_page_without_next_data_becomes_failure_naming_the_page(
    bus, board, clock, raw_dir, make_agent
):
    maintenance = b"<html><body>Probiha udrzba</body></html>"
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock, broken={"rum": maintenance}))

    reply = run_zadani(bus, agent)

    assert reply.kind is Kind.FAILURE
    assert reply.content["reason"] == f"{PAGES['rum']}: stránka nemá __NEXT_DATA__"
    assert board.products() == []


def test_write_failure_midway_leaves_nothing_from_the_zadani_on_board(
    bus, board, clock, raw_dir, make_agent, reject_writes
):
    reject_writes("findings", after=1)  # první Novinka se zapíše, druhá už ne
    llm = FakeLlm(BASELINE_CLASSIFICATION, BASELINE_MANUFACTURERS)
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock), llm=llm)

    reply = run_zadani(bus, agent)

    assert reply.kind is Kind.FAILURE
    assert board.snapshots(agent="sortimentni") == []
    assert board.products() == []
    assert board.findings(kiq=1) == []


@pytest.mark.parametrize(
    "change",
    [
        (b'"categoryProductCards"', b'"productCardsV2"'),  # Rohlík přejmenoval dotaz
        (b'"cardsData": [', b'"cardsData": [], "puvodni": ['),  # výpis bez karet
    ],
    ids=["prejmenovany-dotaz", "prazdny-vypis"],
)
def test_subcategory_without_product_cards_is_failure_not_an_empty_snapshot(
    bus, board, clock, raw_dir, make_agent, change
):
    vodka = (FIXTURES / "den1" / "vodka.html").read_bytes().replace(*change)
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock, broken={"vodka": vodka}))

    reply = run_zadani(bus, agent)

    assert reply.kind is Kind.FAILURE
    assert reply.content["reason"].startswith(f"{PAGES['vodka']}: ")
    assert board.snapshots(agent="sortimentni") == []


def test_listings_without_any_spirit_are_failure_not_an_empty_snapshot(
    bus, board, clock, raw_dir, make_agent
):
    # Kdyby Rohlík změnil zápis množství, žádná karta by neměla objem a nebyla by lihovinou.
    without_volume = {
        page: (FIXTURES / "den1" / f"{page}.html")
        .read_bytes()
        .replace(b'"textualAmount": ', b'"textualAmount": "1 ks", "puvodni": ')
        for page in PAGES
        if page != "lihoviny"
    }
    agent = make_agent(Sortimentni, fetcher=rohlik(raw_dir, clock, broken=without_volume))

    reply = run_zadani(bus, agent)

    assert reply.kind is Kind.FAILURE
    assert "žádná lihovina" in reply.content["reason"]
    assert board.snapshots(agent="sortimentni") == []


ALKOHOL_CZ_NOVINKY = "https://www.alkohol.cz/produkty/novinky/"
ALKOHOL_CZ_LLM = (
    {
        "products": [
            {"id": "26846", "category": "ostatni", "brand": "Dolská"},
            {"id": "26822", "category": "ostatni_likery", "brand": "Lázeňský Likér"},
            {"id": "26821", "category": "whisky", "brand": "Skrewball"},
            {"id": "26808", "category": "brandy", "brand": "Bache Gabrielsen"},
        ]
    },
    {
        "brands": [
            {"brand": "Dolská", "manufacturer": None},
            {"brand": "Lázeňský Likér", "manufacturer": None},
            {"brand": "Skrewball", "manufacturer": None},
            {"brand": "Bache Gabrielsen", "manufacturer": None},
        ]
    },
)


def test_alkohol_cz_novinky_are_novinky_by_its_label_even_after_baseline(
    bus, board, clock, raw_dir, make_agent
):
    """Výpis Novinky z alkohol.cz (zkrácený skutečný, 17. 11. 2026): víno, pivo a papírová
    taška vypadnou, lihoviny jsou Novinky „podle prodejce alkohol.cz“."""
    baseline(bus, clock, raw_dir, make_agent)
    routes = {url: FIXTURES / "den1" / f"{page}.html" for page, url in PAGES.items()}
    routes[ALKOHOL_CZ_NOVINKY] = FIXTURES.parent / "alkohol_cz" / "novinky.html"
    fetcher = FakeFetcher(routes, raw_dir=raw_dir, clock=clock)
    agent = make_agent(Sortimentni, fetcher=fetcher, llm=FakeLlm(*ALKOHOL_CZ_LLM))

    reply = run_zadani(bus, agent)

    assert reply.kind is Kind.INFORM
    assert reply.content["new_findings"] == 4
    assert board.snapshot(reply.content["snapshot_id"]).source == "rohlik.cz, alkohol.cz"
    found = novinky(board, reply.content["snapshot_id"])
    assert ("Bache Gabrielsen Cognac VS 0,7l 40%", "podle prodejce alkohol.cz") in found
    assert not any("Ampelos" in name or "Geuze" in name or "taška" in name for name, _ in found)
    cognac = next(p for p in board.products() if p.brand == "Bache Gabrielsen")
    assert (
        cognac.first_seen_url == "https://www.alkohol.cz/produkty/bache-gabrielsen-cognac-vs-26808/"
    )


def test_alkohol_cz_outage_does_not_fail_the_zadani(bus, board, clock, raw_dir, make_agent):
    reply = baseline(bus, clock, raw_dir, make_agent)  # bez ukázky alkohol.cz = výpadek

    assert reply.kind is Kind.INFORM
    assert board.snapshot(reply.content["snapshot_id"]).source == "rohlik.cz"


def test_product_or_brand_left_out_by_llm_is_skipped_not_failure(
    bus, board, clock, raw_dir, make_agent, caplog
):
    """LLM vynechá kategorii jednoho produktu z alkohol.cz a výrobce jedné Značky: produkt
    se přeskočí a zaloguje, Značka zůstane bez výrobce, zbytek Zadání (i Rohlík) proběhne."""
    routes = {url: FIXTURES / "den1" / f"{page}.html" for page, url in PAGES.items()}
    routes[ALKOHOL_CZ_NOVINKY] = FIXTURES.parent / "alkohol_cz" / "novinky.html"
    fetcher = FakeFetcher(routes, raw_dir=raw_dir, clock=clock)
    classification, manufacturers = ALKOHOL_CZ_LLM
    llm = FakeLlm(
        {
            "products": BASELINE_CLASSIFICATION["products"]
            + [p for p in classification["products"] if p["brand"] != "Dolská"]
        },
        {
            "brands": BASELINE_MANUFACTURERS["brands"]
            + [b for b in manufacturers["brands"] if b["brand"] != "Skrewball"]
        },
    )
    agent = make_agent(Sortimentni, fetcher=fetcher, llm=llm)
    caplog.set_level(logging.WARNING)

    reply = run_zadani(bus, agent)

    assert reply.kind is Kind.INFORM
    products = {p.name: p for p in board.products()}
    assert "Božkov Modrá 19 % obj." in products  # Rohlík
    assert "Bache Gabrielsen Cognac VS 0,7l 40%" in products
    assert not any(p.brand == "Dolská" for p in products.values())
    assert "alkohol.cz" in caplog.text and "Dolská" in caplog.text
    skrewball = next(p for p in products.values() if p.brand == "Skrewball")
    assert skrewball.manufacturer is None


def test_llm_classifying_none_of_the_unclear_products_is_failure(
    bus, board, clock, raw_dir, make_agent
):
    """Vynechat jeden produkt je v pořádku, nezařadit žádný je výpadek LLM: Selhání."""
    baseline(bus, clock, raw_dir, make_agent)

    reply = next_day(
        bus,
        clock,
        raw_dir,
        make_agent,
        day={"likery": "den2"},
        llm=FakeLlm({"products": []}, SECOND_DAY_LLM[1]),
    )

    assert reply.kind is Kind.FAILURE
    assert "LLM" in reply.content["reason"]
    assert len(board.snapshots(agent="sortimentni")) == 1


def test_rohlik_product_left_out_by_llm_in_baseline_is_failure(
    bus, board, clock, raw_dir, make_agent
):
    """Výchozí snímek bez vynechaného produktu z Rohlíku by z něj později udělal falešnou
    Novinku (jeho ID u zdroje by přesáhlo výchozí stav), proto Selhání, ne neúplný snímek."""
    classification = {
        "products": [p for p in BASELINE_CLASSIFICATION["products"] if p["brand"] != "Hennessy"]
    }
    llm = FakeLlm(classification, BASELINE_MANUFACTURERS)

    reply = next_day(bus, clock, raw_dir, make_agent, llm=llm)

    assert reply.kind is Kind.FAILURE
    assert "výchozí snímek" in reply.content["reason"]
    assert board.snapshots(agent="sortimentni") == []
    assert board.products() == []
