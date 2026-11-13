"""Sortimentní agent (KIQ 1) přes sběrnici a tabuli nad uloženými ukázkami rohlik.cz.

`den1/` jsou zkrácené skutečné stránky kategorie Lihoviny a jejích podkategorií,
`den2/` jejich upravené kopie pro další snímek (vyprodaný produkt, nové produkty ve výpisu).
"""

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
    "rtd": "https://www.rohlik.cz/c300108082-ready-to-drink-hard-seltzer-koktejly-a-prislusenstvi",
}

# Odpovědi LLM k výchozímu snímku: kategorie a značka nejasných produktů, pak výrobci značek.
BASELINE_CLASSIFICATION = {
    "products": [
        {"id": "1440853", "category": "rtd", "brand": "Božkov"},
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
    assert reply.content == {"snapshot_id": snapshot.id, "new_findings": 2, "item_count": 11}
    assert (snapshot.agent, snapshot.source, snapshot.taken_at) == (
        "sortimentni",
        "rohlik.cz",
        clock.now() - timedelta(days=1),
    )
    assert len(snapshot.raw_paths) == 6  # Lihoviny a pět podkategorií, bez nealko nápojů
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
    # Brčka a nealko drink z RTD nejsou lihoviny (nemají objem nebo obsah alkoholu).
    assert described == {
        "Božkov Originál Tuzemský 37,5% obj.": (
            "Stock",
            "Božkov",
            500,
            Decimal("37.5"),
            "rum_tuzemak",
        ),
        "Božkov Na pečení 20 % obj.": ("Stock", "Božkov", 500, Decimal(20), "rum_tuzemak"),
        "Božkov Republica Cuba Libre 4,5 % obj.": ("Stock", "Božkov", 250, Decimal("4.5"), "rtd"),
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
        "R. JELÍNEK Kozičky 3,1% obj. plech": (
            "Rudolf Jelínek",
            "R. Jelínek",
            250,
            Decimal("3.1"),
            "rtd",
        ),
        "Becherovka Cocktail Rose & Elderflower 4,5% – plech": (
            "Jan Becher",
            "Becherovka",
            250,
            Decimal("4.5"),
            "rtd",
        ),
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
        "1358451",
    )
    # Třídění a určení výrobce je extrakce: nízký effort podle specu.
    assert [call.effort for call in llm.calls] == ["low", "low"]
    # LLM dostane jen nejasné produkty (podle ID u zdroje): slabý nápoj mimo RTD, značka mimo
    # filtr zdroje, likéry (hořké, nebo ostatní?) a dárková balení.
    asked_about_category = llm.calls[0].prompt
    for unclear in [
        "1440853",  # Božkov Republica Cuba Libre 4,5 % v rumech
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
        "1462285",  # R. Jelínek Kozičky v likérech i v RTD
        "1482102",  # Becherovka Cocktail v RTD
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
        "https://www.rohlik.cz/1485268-metelka-likery-cesky-jablecny-strudl-14-obj",
        seen_at,
    )
    assert finding.content == {
        "name": "Metelka Likéry Český jablečný štrůdl 14 % obj.",
        "brand": "Metelka Likéry",
        "manufacturer": "Metelka",
        "category": "ostatni_likery",
        "volume_ml": 500,
        "abv": 14.0,
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
