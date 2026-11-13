"""Párování akčních cen na produkty ze sortimentu přes sběrnici a tabuli.

Produkty zapisuje na tabuli test (jako by je viděl sortimentní agent na rohlik.cz), akční ceny
cenový agent z uložených ukázek akcniceny.cz (`test_cenovy`).
"""

import json
from decimal import Decimal

import pytest
from test_cenovy import FIXTURES, LISTING, akcni_ceny, make_cenovy, send_zadani, source_routes

from lihoviny.agents.cenovy import Cenovy
from lihoviny.bus import Kind
from lihoviny.fetch import FakeFetcher
from lihoviny.llm import FakeLlm, LlmError
from lihoviny.products import product_key


def record_product(
    board, name, volume_ml, abv, *, brand, category, manufacturer=None, source_id=1287783
):
    """Produkt na tabuli tak, jak ho zapíše sortimentní agent."""
    snapshot = board.record_snapshot(
        agent="sortimentni", source="rohlik.cz", fetches=[], item_count=1
    )
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


def collect(bus, cenovy):
    """Cenový agent obslouží jedno Zadání; vrátí odpověď."""
    zadani = send_zadani(bus)
    cenovy.process_pending()
    return bus.conversation(zadani.conversation_id)[-1]


class PairingModel:
    """Náhražka LLM, která odpovídá jako model: z dotazu přečte akční ceny s kandidáty
    (JSON na řádek) a odpoví podle `answers` (název akční ceny → produkt, nebo None).
    `questions` drží každý dotaz jako {název akční ceny: [názvy kandidátů]}."""

    def __init__(self, answers):
        self.answers = answers
        self.questions = []

    def ask(self, prompt, schema, *, effort, system=""):
        assert effort == "low"
        offers = [json.loads(line) for line in prompt.splitlines() if line.startswith("{")]
        self.questions.append(
            {o["nazev"]: sorted(c["nazev"] for c in o["kandidati"]) for o in offers}
        )
        chosen = {name: str(p.id) if p else None for name, p in self.answers.items()}
        return schema.model_validate(
            {"offers": [{"offer": o["id"], "product": chosen[o["nazev"]]} for o in offers]}
        )


class BrokenModel:
    """Náhražka LLM, jehož volání selže s danou chybou."""

    def __init__(self, error):
        self.error = error

    def ask(self, prompt, schema, *, effort, system=""):
        raise self.error


def cenovy_with(make_agent, clock, raw_dir, *, routes=None, llm=None):
    fetcher = FakeFetcher(routes or source_routes(), raw_dir=raw_dir, clock=clock)
    return make_agent(Cenovy, fetcher=fetcher, llm=llm)


def without_volume_in_cointreau_name():
    """Zdroj, který u Cointreau neuvádí objem: „Cointreau Likér“."""
    routes = source_routes()
    routes[LISTING.format("likery")] = (
        (FIXTURES / "likery.html")
        .read_bytes()
        .replace(b'content="Cointreau Lik\xc3\xa9r 0,7l"', b'content="Cointreau Lik\xc3\xa9r"')
    )
    return routes


def record_fernet_and_cointreau(board):
    return {
        "cointreau": record_product(
            board, "Cointreau 40% obj.", 700, "40",
            brand="Cointreau", category="ostatni_likery", source_id=731500,
        ),
        "original": record_product(
            board, "Fernet Stock Original 35% obj.", 500, "35",
            brand="Fernet Stock", category="horke_likery", source_id=1352557,
        ),
        "citrus": record_product(
            board, "Fernet Stock Citrus Bylinný likér 27% obj.", 500, "27",
            brand="Fernet Stock", category="horke_likery", source_id=1352613,
        ),
    }  # fmt: skip


def test_akcni_cena_is_paired_with_product_of_same_normalized_key(
    bus, board, clock, raw_dir, make_agent
):
    # Zdroje píší název jinak („Originál“ / „Original“, „obj.“, objem v názvu), klíč je týž.
    tuzemak = record_product(
        board, "Božkov Originál Tuzemský 37,5% obj.", 500, "37.5",
        brand="Božkov", category="rum_tuzemak",
    )  # fmt: skip

    reply = collect(bus, make_cenovy(make_agent, clock, raw_dir))

    assert reply.kind is Kind.INFORM
    offers = akcni_ceny(board, "Božkov Original Tuzemský 37,5% 0,5l")
    assert len(offers) == 5  # pět řetězců, všechny akční ceny téhož produktu
    assert {f.product_id for f in offers} == {tuzemak.id}
    lidl = next(f for f in offers if f.content["chain"] == "Lidl")
    assert lidl.content["paired_by"] == "key"
    assert lidl.content["from_product"] == []
    assert lidl.content["price_per_l_ethanol"] == 692.8  # obsah alkoholu uvádí sám zdroj


def test_paired_akcni_cena_takes_abv_from_product_and_gets_price_per_litre_of_ethanol(
    bus, board, clock, raw_dir, make_agent
):
    # Název akční ceny obsah alkoholu neuvádí; shoda názvu a objemu s jediným produktem stačí.
    captain = record_product(
        board, "Captain Morgan Original Spiced Gold 35% obj.", 700, "35",
        brand="Captain Morgan", category="rum_tuzemak",
    )  # fmt: skip

    collect(bus, make_cenovy(make_agent, clock, raw_dir))

    (tesco,) = [
        f
        for f in akcni_ceny(board, "Captain Morgan Original Spiced Gold 0,7l")
        if f.content["chain"] == "Tesco"
    ]
    assert tesco.product_id == captain.id
    content = tesco.content
    assert (content["volume_l"], content["abv_percent"], content["from_product"]) == (
        0.7,
        35.0,
        ["abv_percent"],
    )
    assert content["price_per_l"] == 428.43
    assert content["price_per_l_ethanol"] == 1224.08  # 299,90 / (0,7 l × 0,35)


@pytest.mark.parametrize(
    ("product_name", "offer_name"),
    [
        ("Jägermeister 35 % obj. dárkové balení se 2 panáky", "Jägermeister 700ml"),
        ("Jägermeister 35% obj.", "Jägermeister 35% dárkové balení se 2 panáky 700ml"),
    ],
    ids=["darkove-baleni-v-sortimentu", "darkove-baleni-v-akcni-cene"],
)
def test_gift_pack_of_same_volume_is_paired_with_the_same_product(
    bus, board, clock, raw_dir, make_agent, product_name, offer_name
):
    jagermeister = record_product(
        board, product_name, 700, "35", brand="Jägermeister", category="horke_likery"
    )
    routes = source_routes()
    for listing in ["likery", "bylinne-destilaty"]:
        routes[LISTING.format(listing)] = (
            (FIXTURES / f"{listing}.html")
            .read_bytes()
            .replace(b'content="J\xc3\xa4germeister 700ml"', f'content="{offer_name}"'.encode())
        )

    collect(bus, make_cenovy(make_agent, clock, raw_dir, routes))

    offers = akcni_ceny(board, offer_name)
    assert len(offers) == 3
    assert {f.product_id for f in offers} == {jagermeister.id}
    assert {f.content["abv_percent"] for f in offers} == {35.0}


def test_akcni_cena_without_its_product_in_sortiment_stays_unpaired_without_asking_llm(
    bus, board, clock, raw_dir, make_agent
):
    record_product(board, "Hennessy V.S 40% obj.", 700, "40", brand="Hennessy", category="brandy")

    llm = FakeLlm()
    reply = collect(bus, cenovy_with(make_agent, clock, raw_dir, llm=llm))

    assert reply.kind is Kind.INFORM
    assert llm.calls == []  # žádná akční cena nemá v sortimentu kandidáta
    offers = board.findings(kiq=2)
    assert len(offers) == 24
    assert {f.product_id for f in offers} == {None}
    assert all("paired_by" not in f.content for f in offers)


def test_llm_pairs_akcni_cena_whose_name_differs_and_product_fills_volume_and_abv(
    bus, board, clock, raw_dir, make_agent
):
    products = record_fernet_and_cointreau(board)
    llm = PairingModel(
        {"Cointreau Likér": products["cointreau"], "Fernet Stock 0,5l, vybrané druhy": None}
    )

    reply = collect(
        bus,
        cenovy_with(make_agent, clock, raw_dir, routes=without_volume_in_cointreau_name(), llm=llm),
    )

    assert reply.kind is Kind.INFORM
    # Na LLM jdou jen akční ceny s kandidáty: stejná značka, objem a obsah alkoholu, pokud je
    # název uvádí. Ostatních 20 akčních cen nemá v sortimentu žádného kandidáta.
    assert llm.questions == [
        {
            "Cointreau Likér": ["Cointreau 40% obj."],
            "Fernet Stock 0,5l, vybrané druhy": [
                "Fernet Stock Citrus Bylinný likér 27% obj.",
                "Fernet Stock Original 35% obj.",
            ],
        }
    ]
    (cointreau,) = akcni_ceny(board, "Cointreau Likér")
    assert cointreau.product_id == products["cointreau"].id
    content = cointreau.content
    assert (content["paired_by"], content["from_product"]) == ("llm", ["abv_percent", "volume_l"])
    assert (content["volume_l"], content["abv_percent"]) == (0.7, 40.0)
    assert content["price_per_l"] == 641.43  # 449 / 0,7 l
    assert content["price_per_l_ethanol"] == 1603.57  # 449 / (0,7 l × 0,4)
    # Akční cena na více druhů najednou není jeden produkt.
    fernet = akcni_ceny(board, "Fernet Stock 0,5l, vybrané druhy")
    assert {f.product_id for f in fernet} == {None}


def test_unpaired_akcni_cena_is_paired_later_when_sortiment_records_its_product(
    bus, board, clock, raw_dir, make_agent
):
    collect(bus, cenovy_with(make_agent, clock, raw_dir))
    assert {
        f.product_id for f in akcni_ceny(board, "Captain Morgan Original Spiced Gold 0,7l")
    } == {None}

    clock.advance(days=1)
    captain = record_product(
        board, "Captain Morgan Original Spiced Gold 35% obj.", 700, "35",
        brand="Captain Morgan", category="rum_tuzemak",
    )  # fmt: skip
    reply = collect(bus, cenovy_with(make_agent, clock, raw_dir))

    assert reply.content["new_findings"] == 0  # párování nové nálezy nevytváří
    offers = akcni_ceny(board, "Captain Morgan Original Spiced Gold 0,7l")
    assert len(offers) == 4
    assert {f.product_id for f in offers} == {captain.id}
    assert {f.content["abv_percent"] for f in offers} == {35.0}
    assert all(f.content["price_per_l_ethanol"] for f in offers)


def test_llm_is_asked_about_akcni_cena_again_only_when_it_has_a_new_candidate(
    bus, board, clock, raw_dir, make_agent
):
    products = record_fernet_and_cointreau(board)
    fernet = "Fernet Stock 0,5l, vybrané druhy"
    first = PairingModel({"Cointreau Likér 0,7l": products["cointreau"], fernet: None})
    collect(bus, cenovy_with(make_agent, clock, raw_dir, llm=first))

    clock.advance(days=1)
    second = PairingModel({})
    reply = collect(bus, cenovy_with(make_agent, clock, raw_dir, llm=second))

    assert reply.kind is Kind.INFORM
    assert second.questions == []  # Fernet už LLM posoudil se stejnými kandidáty

    clock.advance(days=1)
    record_product(
        board, "Fernet Stock Z-Generation 30% obj.", 500, "30",
        brand="Fernet Stock", category="horke_likery", source_id=1490001,
    )  # fmt: skip
    third = PairingModel({fernet: None})
    collect(bus, cenovy_with(make_agent, clock, raw_dir, llm=third))

    assert third.questions == [
        {
            fernet: [
                "Fernet Stock Citrus Bylinný likér 27% obj.",
                "Fernet Stock Original 35% obj.",
                "Fernet Stock Z-Generation 30% obj.",
            ]
        }
    ]


@pytest.mark.parametrize(
    "error",
    [
        LlmError("model odmítl odpovědět (refusal, kategorie None)"),
        # Tak selže SDK, když v prostředí chybí ANTHROPIC_API_KEY (lokální běh bez klíče).
        TypeError("Could not resolve authentication method. Expected one of api_key, ..."),
    ],
    ids=["odmitnuti", "chybi-klic"],
)
def test_llm_failure_leaves_akcni_ceny_unpaired_and_next_zadani_tries_again(
    bus, board, clock, raw_dir, make_agent, error
):
    # Párování je doplněk: bez LLM se akční ceny pořád sbírají, jen zůstanou nespárované.
    products = record_fernet_and_cointreau(board)
    reply = collect(bus, cenovy_with(make_agent, clock, raw_dir, llm=BrokenModel(error)))

    assert reply.kind is Kind.INFORM
    assert reply.content["new_findings"] == 24
    assert {f.product_id for f in board.findings(kiq=2)} == {None}

    clock.advance(days=1)
    llm = PairingModel(
        {"Cointreau Likér 0,7l": products["cointreau"], "Fernet Stock 0,5l, vybrané druhy": None}
    )
    collect(bus, cenovy_with(make_agent, clock, raw_dir, llm=llm))

    assert len(llm.questions) == 1
    (cointreau,) = akcni_ceny(board, "Cointreau Likér 0,7l")
    assert cointreau.product_id == products["cointreau"].id
