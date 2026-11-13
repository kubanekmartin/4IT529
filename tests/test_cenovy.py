"""Cenový agent přes sběrnici a tabuli nad uloženými ukázkami výpisů z akcniceny.cz.

Ukázky v `tests/fixtures/akcniceny/` jsou skutečné stránky z 13. 11. 2026 zmenšené na pár
produktů. `vypis-bez-akcnich-cen.html` je skutečný výpis s odstraněnými produkty: dostanou ho
výpisy, na kterých v daném testu nezáleží.
"""

from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import httpx2
import pytest

from lihoviny.agents.cenovy import Cenovy
from lihoviny.bus import Kind
from lihoviny.fetch import FakeFetcher, HttpFetcher

FIXTURES = Path(__file__).parent / "fixtures" / "akcniceny"
LISTING = "https://www.akcniceny.cz/zbozi/napoje-alkoholicke/{}/"
# Výpisy podle klíčových slov, ze kterých cenový agent bere akční ceny lihovin a RTD.
LISTINGS = [
    "tuzemak-rum",
    "vodky",
    "whisky",
    "gin",
    "brandy",
    "tequily",
    "likery",
    "bylinne-destilaty",
    "ovocne-destilaty",
    "michany-napoj",
]


def source_routes():
    routes = {LISTING.format(slug): FIXTURES / "vypis-bez-akcnich-cen.html" for slug in LISTINGS}
    routes |= {
        LISTING.format("tuzemak-rum"): FIXTURES / "tuzemak-rum.html",
        LISTING.format("tuzemak-rum") + "strana-2/": FIXTURES / "tuzemak-rum-strana-2.html",
        LISTING.format("bylinne-destilaty"): FIXTURES / "bylinne-destilaty.html",
        LISTING.format("likery"): FIXTURES / "likery.html",
        LISTING.format("michany-napoj"): FIXTURES / "michany-napoj.html",
    }
    return routes


def make_cenovy(make_agent, clock, raw_dir, routes=None):
    fetcher = FakeFetcher(routes or source_routes(), raw_dir=raw_dir, clock=clock)
    return make_agent(Cenovy, fetcher=fetcher)


def send_zadani(bus):
    return bus.send(kind=Kind.REQUEST, sender="ridici", recipient="cenovy", content={})


def akcni_ceny(board, product):
    return [f for f in board.findings(kiq=2) if f.content["product"] == product]


def test_zadani_puts_akcni_ceny_with_chain_validity_and_litre_prices_on_board(
    bus, board, clock, raw_dir, make_agent
):
    cenovy = make_cenovy(make_agent, clock, raw_dir)
    zadani = send_zadani(bus)

    cenovy.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    snapshot = board.snapshot(reply.content["snapshot_id"])
    assert reply.content == {"snapshot_id": snapshot.id, "new_findings": 24, "item_count": 24}
    assert (snapshot.agent, snapshot.source, snapshot.item_count) == (
        "cenovy",
        "akcniceny.cz",
        24,
    )
    lidl = next(
        f
        for f in akcni_ceny(board, "Božkov Original Tuzemský 37,5% 0,5l")
        if f.content["chain"] == "Lidl"
    )
    assert (lidl.kiq, lidl.agent, lidl.type, lidl.snapshot_id, lidl.found_at) == (
        2,
        "cenovy",
        "akcni_cena",
        snapshot.id,
        clock.now(),
    )
    assert lidl.source_url == "https://www.akcniceny.cz/akce/bozkov-original-tuzemsky-37-5-0-5l/"
    assert lidl.content == {
        "product": "Božkov Original Tuzemský 37,5% 0,5l",
        "chain": "Lidl",
        "price": 129.9,
        "card_price": None,
        "card_label": None,
        "discount_percent": 41,
        "valid_from": None,
        "valid_until": "2026-11-15",
        "volume_l": 0.5,
        "abv_percent": 37.5,
        "price_per_l": 259.8,
        "price_per_l_ethanol": 692.8,  # 129,90 / (0,5 l × 0,375)
        "source_category": "tuzemák, rum",
        "flyer_url": "https://www.akcniceny.cz/letak/lidl-198273/strana-1/#letPg2",
    }


def litre_prices(board, product, chain):
    (finding,) = [f for f in akcni_ceny(board, product) if f.content["chain"] == chain]
    content = finding.content
    return (
        content["volume_l"],
        content["abv_percent"],
        content["price_per_l"],
        content["price_per_l_ethanol"],
    )


def test_price_per_litre_of_ethanol_needs_known_abv(bus, board, clock, raw_dir, make_agent):
    cenovy = make_cenovy(make_agent, clock, raw_dir)
    send_zadani(bus)

    cenovy.process_pending()

    # RTD v plechovce: 39,90 / (0,25 l × 0,045)
    assert litre_prices(board, "Míchaný drink 4,5% 250 ml", "Albert") == (
        0.25,
        4.5,
        159.6,
        3546.67,
    )
    # obsah alkoholu s mezerou před procentem: 139,90 / (0,5 l × 0,35)
    assert litre_prices(board, "Božkov Tradiční 35 % 0,5l", "BILLA") == (0.5, 35.0, 279.8, 799.43)
    # obsah alkoholu v názvu není: cena za litr ano, za litr etanolu ne
    assert litre_prices(board, "Captain Morgan Original Spiced Gold 0,7l", "Tesco") == (
        0.7,
        None,
        428.43,
        None,
    )
    # více druhů s různým obsahem alkoholu v jedné akční ceně: obsah alkoholu neznáme
    heffron = (
        "Heffron Panama Original 5YO 35% Elixir Coconut 30% Heritage rum 5YO 38% "
        "Elixir Coffee 30% 0,5l"
    )
    assert litre_prices(board, heffron, "JIP") == (0.5, None, 419.8, None)


def test_abv_marked_obj_wins_over_other_percentages_in_the_name(
    bus, board, clock, raw_dir, make_agent
):
    routes = source_routes()
    rum = (FIXTURES / "tuzemak-rum.html").read_bytes()
    routes[LISTING.format("tuzemak-rum")] = rum.replace(
        b'itemprop="name" content="Captain Morgan Original Spiced Gold 0,7l"',
        b'itemprop="name" content="Olmeca Gold 100% Agave 38% obj. 0,7l"',
    )
    cenovy = make_cenovy(make_agent, clock, raw_dir, routes)
    send_zadani(bus)

    cenovy.process_pending()

    # 299,90 / (0,7 l × 0,38)
    assert litre_prices(board, "Olmeca Gold 100% Agave 38% obj. 0,7l", "Tesco") == (
        0.7,
        38.0,
        428.43,
        1127.44,
    )


def test_akcni_cena_keeps_card_price_and_start_of_validity_when_source_gives_them(
    bus, board, clock, raw_dir, make_agent
):
    cenovy = make_cenovy(make_agent, clock, raw_dir)
    send_zadani(bus)

    cenovy.process_pending()

    billa = [
        f.content
        for f in akcni_ceny(board, "Fernet Stock 0,5l, vybrané druhy")
        if f.content["chain"] == "BILLA"
    ]
    assert [
        (c["price"], c["card_price"], c["card_label"], c["valid_from"], c["valid_until"])
        for c in billa
    ] == [
        (169.9, None, None, None, "2026-12-01"),
        (189.9, 99.9, "Billa Bonus Club", "2026-11-16", "2026-11-17"),
    ]


def test_sponsored_products_and_products_in_several_listings_are_not_counted_twice(
    bus, board, clock, raw_dir, make_agent
):
    cenovy = make_cenovy(make_agent, clock, raw_dir)
    send_zadani(bus)

    cenovy.process_pending()

    # Sekt je ve výpisu rumů jen jako placená reklama (TOP) nahoře stránky.
    assert akcni_ceny(board, "Mucha Sekt šumivé víno bílé 0,75l, vybrané druhy") == []
    # Jägermeister je ve výpisu likérů i bylinných destilátů; uloží se jednou, s prvním výpisem.
    jagermeister = akcni_ceny(board, "Jägermeister 700ml")
    assert sorted((f.content["chain"], f.content["price"]) for f in jagermeister) == [
        ("Albert", 329.0),
        ("Kaufland", 299.9),
        ("Penny Market", 299.9),
    ]
    assert {f.content["source_category"] for f in jagermeister} == {"likéry"}


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "https://zlo.example/akce/x/", "http://www.akcniceny.cz/akce/x/"],
    ids=["javascript", "cizi-web", "bez-https"],
)
def test_product_link_that_is_not_https_akcniceny_is_replaced_by_the_listing_page(
    bus, board, clock, raw_dir, make_agent, url
):
    # Odkaz ze zdroje jde na web jako href: jiný než https://www.akcniceny.cz/… se nepoužije.
    page = LISTING.format("tuzemak-rum") + "strana-2/"
    routes = source_routes()
    routes[page] = (
        (FIXTURES / "tuzemak-rum-strana-2.html")
        .read_bytes()
        .replace(b'content="/akce/bozkov-tradicni-35-0-5l/"', f'content="{url}"'.encode())
    )
    cenovy = make_cenovy(make_agent, clock, raw_dir, routes)
    send_zadani(bus)

    cenovy.process_pending()

    assert {f.source_url for f in akcni_ceny(board, "Božkov Tradiční 35 % 0,5l")} == {page}


def test_e_shop_offer_has_no_flyer(bus, board, clock, raw_dir, make_agent):
    cenovy = make_cenovy(make_agent, clock, raw_dir)
    send_zadani(bus)

    cenovy.process_pending()

    (cointreau,) = akcni_ceny(board, "Cointreau Likér 0,7l")
    assert (cointreau.content["chain"], cointreau.content["flyer_url"]) == ("Košík.cz", None)


def test_zadani_reads_every_page_of_a_listing_and_keeps_raw_pages(
    bus, board, clock, raw_dir, make_agent
):
    routes = source_routes()
    cenovy = make_cenovy(make_agent, clock, raw_dir, routes)
    zadani = send_zadani(bus)

    cenovy.process_pending()

    assert sorted(cenovy.fetcher.requested) == sorted(routes)
    assert akcni_ceny(board, "Božkov Tradiční 35 % 0,5l") != []  # z druhé strany výpisu rumů
    snapshot = board.snapshot(bus.conversation(zadani.conversation_id)[-1].content["snapshot_id"])
    assert len(snapshot.raw_paths) == len(routes)


def test_next_day_snapshot_keeps_the_previous_one_and_adds_only_changed_akcni_ceny(
    bus, board, clock, raw_dir, make_agent
):
    cenovy = make_cenovy(make_agent, clock, raw_dir)
    first = send_zadani(bus)
    cenovy.process_pending()
    first_snapshot = board.snapshot(
        bus.conversation(first.conversation_id)[-1].content["snapshot_id"]
    )

    clock.advance(days=1)
    routes = source_routes()
    rum = (FIXTURES / "tuzemak-rum.html").read_bytes()
    # BILLA zlevnila Captain Morgan z 319,90 na 289,90 Kč; zbytek zdroje je beze změny.
    routes[LISTING.format("tuzemak-rum")] = rum.replace(
        b'itemprop="price" content="319.90"', b'itemprop="price" content="289.90"'
    )
    cenovy = make_cenovy(make_agent, clock, raw_dir, routes)
    second = send_zadani(bus)
    cenovy.process_pending()

    reply = bus.conversation(second.conversation_id)[-1]
    second_snapshot = board.snapshot(reply.content["snapshot_id"])
    assert reply.content == {
        "snapshot_id": second_snapshot.id,
        "new_findings": 1,
        "item_count": 24,
    }
    assert board.snapshot(first_snapshot.id) == first_snapshot
    assert second_snapshot.taken_at - first_snapshot.taken_at == timedelta(days=1)
    assert second_snapshot.item_count == first_snapshot.item_count == 24
    assert set(second_snapshot.raw_paths).isdisjoint(first_snapshot.raw_paths)
    assert len(board.findings(snapshot_id=first_snapshot.id)) == 24
    (changed,) = board.findings(snapshot_id=second_snapshot.id)
    assert (changed.content["product"], changed.content["chain"], changed.content["price"]) == (
        "Captain Morgan Original Spiced Gold 0,7l",
        "BILLA",
        289.9,
    )


def http_source(clock, raw_dir, *, too_many_requests):
    """Skutečný adaptér stahování proti simulovanému zdroji, který prvních
    `too_many_requests` požadavků odmítne s HTTP 429. Vrací adaptér a časy požadavků."""
    routes = source_routes()
    requested_at = []

    def handler(request):
        requested_at.append(clock.now())
        if len(requested_at) <= too_many_requests:
            return httpx2.Response(429, headers={"retry-after": "5"})
        return httpx2.Response(
            200,
            content=routes[str(request.url)].read_bytes(),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    fetcher = HttpFetcher(
        raw_dir=raw_dir,
        clock=clock,
        min_delay=Cenovy.fetch_delay,
        transport=httpx2.MockTransport(handler),
    )
    return fetcher, requested_at


def test_source_keeps_crawl_delay_and_waits_after_429_before_trying_again(
    bus, board, clock, raw_dir, make_agent
):
    fetcher, requested_at = http_source(clock, raw_dir, too_many_requests=1)
    cenovy = make_agent(Cenovy, fetcher=fetcher)
    zadani = send_zadani(bus)

    cenovy.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.content["new_findings"] == 24
    pauses = [(b - a).total_seconds() for a, b in pairwise(requested_at)]
    assert pauses[0] == 5  # Retry-After po HTTP 429
    assert min(pauses) >= 1  # Crawl-delay akcniceny.cz


def test_source_failure_after_three_attempts_becomes_failure_and_leaves_board_empty(
    bus, board, clock, raw_dir, make_agent
):
    fetcher, requested_at = http_source(clock, raw_dir, too_many_requests=3)
    cenovy = make_agent(Cenovy, fetcher=fetcher)
    zadani = send_zadani(bus)

    cenovy.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.FAILURE
    assert reply.content == {
        "reason": f"{LISTING.format('tuzemak-rum')}: 3 pokusy selhaly, naposledy HTTP 429"
    }
    assert len(requested_at) == 3
    assert board.findings(kiq=2) == []


def test_listings_without_any_offer_are_failure_not_zero_akcni_ceny(
    bus, board, clock, raw_dir, make_agent
):
    # Kdyby zdroj změnil stránku a parser nic nenašel, nesmí to vypadat jako týden bez akčních cen.
    routes = {LISTING.format(slug): FIXTURES / "vypis-bez-akcnich-cen.html" for slug in LISTINGS}
    cenovy = make_cenovy(make_agent, clock, raw_dir, routes)
    zadani = send_zadani(bus)

    cenovy.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.FAILURE
    assert "žádná akční cena" in reply.content["reason"]
    assert board.findings(kiq=2) == []


def test_write_failure_midway_leaves_nothing_from_the_zadani_on_board(
    bus, board, clock, raw_dir, make_agent, reject_writes
):
    reject_writes("findings", after=1)  # první akční cena se zapíše, druhá už ne
    cenovy = make_cenovy(make_agent, clock, raw_dir)
    zadani = send_zadani(bus)

    cenovy.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.FAILURE
    assert board.snapshots(agent="cenovy") == []
    assert board.findings(kiq=2) == []
