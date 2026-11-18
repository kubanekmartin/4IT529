"""Mediální agent přes sběrnici a tabuli nad uloženými ukázkami WordPress REST API zboziaprodej.cz.

Ukázky v `tests/fixtures/zboziaprodej/` jsou skutečné články z WP REST API (stažené 13. 11. 2026),
zmenšené na pár odstavců: `clanky-den1.json` je výpis jednoho dne, `clanky-den2.json` výpis
o pár dní později se dvěma novými články. LLM nahrazují připravené odpovědi.
"""

import hashlib
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from lihoviny.agents.medialni import Medialni
from lihoviny.bus import Kind
from lihoviny.fetch import FakeFetcher, FetchError, read_raw
from lihoviny.llm import FakeLlm, LlmError
from lihoviny.products import plain_text

FIXTURES = Path(__file__).parent / "fixtures" / "zboziaprodej"
POSTS_URL = (
    "https://www.zboziaprodej.cz/wp-json/wp/v2/posts?per_page=100&_fields=id,date_gmt,title,content"
)


def irrelevant():
    return {
        "relevant": False,
        "people": [],
        "title": "",
        "manufacturers": [],
        "brands": [],
        "channel": "pr",
        "topic": "jine",
        "summary": "",
    }


STOCK_MODERNIZUJE = {
    "relevant": True,
    "people": ["Romana Jourdren"],
    "title": "Stock Plzeň-Božkov modernizuje výrobu a rozšiřuje produktovou nabídku",
    "manufacturers": ["Stock"],
    "brands": ["Božkov", "Fernet Stock", "Limoncè"],
    "channel": "pr",
    "topic": "portfolio",
    "summary": (
        "Stock Plzeň-Božkov navýšil o 70 % kapacitu linky na RTD a rozšířil nabídku o nové"
        " příchutě a aperitiv. Výrobu tequily přesunul do Plzně."
    ),
}


def make_medialni(make_agent, clock, raw_dir, page, *answers):
    fetcher = FakeFetcher({POSTS_URL: FIXTURES / page}, raw_dir=raw_dir, clock=clock)
    llm = FakeLlm(*answers)
    return make_agent(Medialni, fetcher=fetcher, llm=llm), llm


def send_zadani(bus):
    return bus.send(kind=Kind.REQUEST, sender="ridici", recipient="medialni", content={})


def test_zadani_puts_relevant_clanky_on_board_and_skips_the_rest(
    bus, board, clock, raw_dir, make_agent
):
    # Ve výpisu je personální zpráva z družstva obchodníků (nerelevantní) a článek o Stocku.
    medialni, llm = make_medialni(
        make_agent, clock, raw_dir, "clanky-den1.json", STOCK_MODERNIZUJE, irrelevant()
    )
    zadani = send_zadani(bus)

    medialni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    snapshot = board.snapshot(reply.content["snapshot_id"])
    assert reply.content == {"snapshot_id": snapshot.id, "new_findings": 1, "item_count": 2}
    assert (snapshot.agent, snapshot.source, snapshot.item_count) == (
        "medialni",
        "zboziaprodej.cz",
        2,
    )
    (clanek,) = board.findings(kiq=3)
    assert (clanek.agent, clanek.type, clanek.snapshot_id, clanek.found_at) == (
        "medialni",
        "clanek",
        snapshot.id,
        clock.now(),
    )
    # Krátký odkaz s ID článku: adresa se slugem může nést jméno osoby z titulku.
    assert clanek.source_url == "https://www.zboziaprodej.cz/?p=83123"
    assert clanek.content == {
        "source": "zboziaprodej.cz",
        "article_id": 83123,
        "published_at": "2026-10-22T07:31:41+00:00",
        "title": "Stock Plzeň-Božkov modernizuje výrobu a rozšiřuje produktovou nabídku",
        "manufacturers": ["Stock"],
        "brands": ["Božkov", "Fernet Stock", "Limoncè"],
        "channel": "pr",
        "topic": "portfolio",
        "summary": STOCK_MODERNIZUJE["summary"],
    }
    # Každý článek posoudí LLM zvlášť, od nejstaršího, s nízkým effortem a celým textem.
    assert [call.effort for call in llm.calls] == ["low", "low"]
    assert "Božkov Republica Mango" in llm.calls[0].prompt
    assert "Eso Market" in llm.calls[1].prompt


MLADI_A_ALKOHOL = {
    "relevant": True,
    "people": ["Hana Holubová"],
    "title": "Zboží&Prodej 10/2026: Mladí přepisují pravidla pití alkoholu",
    "manufacturers": ["Rudolf Jelínek"],
    "brands": [],
    "channel": "pr",
    "topic": "trh",
    "summary": (
        "Trh s alkoholickými nápoji meziročně klesl o dvě procenta a mladí pijí méně, ale"
        " kvalitněji. Výrobci odpovídají lehčími nápoji včetně RTD v plechovce."
    ),
}


def test_only_clanky_new_since_last_snapshot_are_assessed(bus, board, clock, raw_dir, make_agent):
    day1, _ = make_medialni(
        make_agent, clock, raw_dir, "clanky-den1.json", STOCK_MODERNIZUJE, irrelevant()
    )
    send_zadani(bus)
    day1.process_pending()
    clock.advance(days=1)
    # Den 2: přibyly dva články (o trhu alkoholu a o plýtvání potravinami), dva už známe.
    day2, llm = make_medialni(
        make_agent, clock, raw_dir, "clanky-den2.json", irrelevant(), MLADI_A_ALKOHOL
    )
    zadani = send_zadani(bus)

    day2.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert reply.content["new_findings"] == 1
    assert reply.content["item_count"] == 4
    # LLM dostal jen nové články; nerelevantní článek ze dne 1 znovu neposuzuje.
    assert len(llm.calls) == 2
    assert "Od charity k systémové prevenci" in llm.calls[0].prompt
    assert "Mladí přepisují pravidla pití alkoholu" in llm.calls[1].prompt
    clanky = board.findings(kiq=3)
    first_snapshot = clanky[0].snapshot_id
    assert [(f.content["article_id"], f.snapshot_id) for f in clanky] == [
        (83123, first_snapshot),
        (83182, reply.content["snapshot_id"]),
    ]
    assert clanky[1].found_at == clock.now()
    assert clanky[1].content["title"] == MLADI_A_ALKOHOL["title"]


def stored_text(database_url):
    """Obsah všech tabulek databáze jako jeden text."""
    with psycopg.connect(database_url) as conn:
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return "\n".join(
            conn.execute(
                sql.SQL("SELECT coalesce(string_agg(t::text, ' '), '') FROM {} t").format(
                    sql.Identifier(table)
                )
            ).fetchone()[0]
            for (table,) in tables.fetchall()
        )


def test_no_person_names_are_stored(bus, board, clock, raw_dir, make_agent, database_url):
    # Personální zprávy: jména jsou v titulku, v adrese článku i v textu. LLM v odpovědi
    # pokyn poruší a jméno napíše do titulku i shrnutí.
    novy_marketing = {
        "relevant": True,
        "people": ["Vladimíra Formánková", "Romana Jourdren"],
        "title": "Marketing společnosti Stock Plzeň-Božkov povede Vladimíra Formánková",
        "manufacturers": ["Stock"],
        "brands": ["Božkov"],
        "channel": "pr",
        "topic": "firma",
        "summary": (
            "Marketingovou ředitelkou Stock Plzeň-Božkov pro ČR a SR se stala Vladimíra"
            " Formánková. Formánková přichází z Jacobs Douwe Egberts a dříve vedla marketing"
            " Staropramenu."
        ),
    }
    eso_market = irrelevant() | {"people": ["Jiří Štorch", "Jaroslav Paim"]}
    medialni, llm = make_medialni(
        make_agent, clock, raw_dir, "clanky-se-jmeny.json", novy_marketing, eso_market
    )
    zadani = send_zadani(bus)

    medialni.process_pending()

    assert bus.conversation(zadani.conversation_id)[-1].kind is Kind.INFORM
    assert "Vladimíra Formánková" in llm.calls[0].prompt  # LLM jména vidí, aby je vynechal
    (clanek,) = board.findings(kiq=3)
    assert clanek.content["title"] == "Marketing společnosti Stock Plzeň-Božkov povede [osoba]"
    assert clanek.content["summary"] == (
        "Marketingovou ředitelkou Stock Plzeň-Božkov pro ČR a SR se stala [osoba]. [osoba]"
        " přichází z Jacobs Douwe Egberts a dříve vedla marketing Staropramenu."
    )
    # Bez diakritiky a malými písmeny, aby test zachytil i jméno ve slugu adresy.
    stored = plain_text(stored_text(database_url))
    assert "stock plzen bozkov" in stored
    # Na disk jde jen zmenšená odpověď API (ID, čas vydání, odkaz) a otisk původní.
    saved = [read_raw(path) for path in sorted(raw_dir.rglob("*.gz"))]
    assert [meta["original_sha256"] for meta, _ in saved] == [
        hashlib.sha256((FIXTURES / "clanky-se-jmeny.json").read_bytes()).hexdigest()
    ]
    on_disk = plain_text(b" ".join(body for _, body in saved).decode())
    assert "zboziaprodej cz p " in on_disk
    for name in ["vladimir", "formank", "jourdren", "jiri", "storch", "jaroslav", "paim"]:
        assert name not in stored
        assert name not in on_disk


def test_source_failure_is_failure_and_board_stays_empty(bus, board, clock, raw_dir, make_agent):
    error = FetchError(f"{POSTS_URL}: 3 pokusy selhaly, naposledy HTTP 429")
    fetcher = FakeFetcher({POSTS_URL: error}, raw_dir=raw_dir, clock=clock)
    medialni = make_agent(Medialni, fetcher=fetcher)
    zadani = send_zadani(bus)

    medialni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert (reply.kind, reply.content) == (Kind.FAILURE, {"reason": str(error)})
    assert board.snapshots(agent="medialni") == []


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"[]", "nevrátilo žádný článek"),
        (b"<!doctype html><title>Maintenance</title>", "nečekaná odpověď"),
        (b'{"code": "rest_disabled"}', "nečekaná odpověď"),
    ],
)
def test_response_without_clanky_is_failure(bus, board, clock, raw_dir, make_agent, body, reason):
    # Prázdný snímek by vypadal jako den bez článků o lihovinách, rozbitý zdroj je Selhání.
    fetcher = FakeFetcher({POSTS_URL: body}, raw_dir=raw_dir, clock=clock)
    medialni = make_agent(Medialni, fetcher=fetcher)
    zadani = send_zadani(bus)

    medialni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.FAILURE
    assert reason in reply.content["reason"]
    assert board.snapshots(agent="medialni") == []


class RefusesSecondArticle(FakeLlm):
    def ask(self, prompt, schema, *, effort, system=""):
        if len(self.calls) == 1:
            raise LlmError("model odmítl odpovědět (refusal, kategorie None)")
        return super().ask(prompt, schema, effort=effort, system=system)


class AlwaysRefuses(FakeLlm):
    def ask(self, prompt, schema, *, effort, system=""):
        raise LlmError("model odmítl odpovědět (refusal, kategorie None)")


def test_llm_failure_on_one_clanek_keeps_the_others_and_next_zadani_assesses_only_it(
    bus, board, clock, raw_dir, make_agent
):
    fetcher = FakeFetcher({POSTS_URL: FIXTURES / "clanky-den1.json"}, raw_dir=raw_dir, clock=clock)
    refusing = make_agent(Medialni, fetcher=fetcher, llm=RefusesSecondArticle(STOCK_MODERNIZUJE))
    first = send_zadani(bus)
    refusing.process_pending()

    reply = bus.conversation(first.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert (reply.content["new_findings"], reply.content["item_count"]) == (1, 2)
    assert len(board.findings(kiq=3)) == 1

    medialni, llm = make_medialni(make_agent, clock, raw_dir, "clanky-den1.json", irrelevant())
    retried = send_zadani(bus)
    medialni.process_pending()

    assert bus.conversation(retried.conversation_id)[-1].content["new_findings"] == 0
    assert len(llm.calls) == 1  # jen článek, který minule LLM neposoudil


def test_llm_failure_on_every_clanek_is_failure_and_board_stays_empty(
    bus, board, clock, raw_dir, make_agent
):
    fetcher = FakeFetcher({POSTS_URL: FIXTURES / "clanky-den1.json"}, raw_dir=raw_dir, clock=clock)
    medialni = make_agent(Medialni, fetcher=fetcher, llm=AlwaysRefuses())
    zadani = send_zadani(bus)

    medialni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.FAILURE
    assert "refusal" in reply.content["reason"]
    assert board.snapshots(agent="medialni") == []


MEDIAR_POSTS_URL = (
    "https://www.mediar.cz/wp-json/wp/v2/posts?per_page=50&_fields=id,date_gmt,title,content"
)
MEDIAGURU_RSS_URL = "https://www.mediaguru.cz/rss/nejnovejsi"
BECHEROVKA = STOCK_MODERNIZUJE | {
    "people": ["Petr Vymyšlený"],
    "title": "Nový šéf marketingu Becherovky",
    "manufacturers": ["Jan Becher"],
    "brands": ["Becherovka"],
    "summary": "Jan Becher posiluje marketing Becherovky před Vánoci.",
}


def all_sources(**broken):
    """Ukázky všech tří zdrojů. `mediar.json` a `rss.xml` jsou skutečné výpisy (17. 11. 2026)
    zkrácené na pár článků; poslední položka RSS je upravená na článek o Becherovce."""
    routes = {
        POSTS_URL: FIXTURES / "clanky-den1.json",
        MEDIAR_POSTS_URL: FIXTURES.parent / "mediar" / "clanky.json",
        MEDIAGURU_RSS_URL: FIXTURES.parent / "mediaguru" / "rss.xml",
    }
    return routes | broken


def test_mediar_and_mediaguru_give_llm_only_clanky_about_lihoviny(
    bus, board, clock, raw_dir, make_agent
):
    # zboziaprodej: 2 články (LLM vždy), Mediář: 1 ze 2 o lihovinách (Brown-Forman),
    # MediaGuru: 1 ze 3 (Becherovka). LLM tedy dostane 4 články, ne 7.
    fetcher = FakeFetcher(all_sources(), raw_dir=raw_dir, clock=clock)
    llm = FakeLlm(STOCK_MODERNIZUJE, irrelevant(), STOCK_MODERNIZUJE, BECHEROVKA)
    medialni = make_agent(Medialni, fetcher=fetcher, llm=llm)
    zadani = send_zadani(bus)

    medialni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert reply.content["item_count"] == 7
    assert reply.content["new_findings"] == 3
    snapshot = board.snapshot(reply.content["snapshot_id"])
    assert snapshot.source == "zboziaprodej.cz, mediar.cz, mediaguru.cz"
    assert len(llm.calls) == 4
    assert "Brown-Forman" in llm.calls[2].prompt and "mediar.cz" in llm.calls[2].prompt
    by_source = {f.content["source"]: f for f in board.findings(kiq=3)}
    assert by_source["mediar.cz"].source_url == "https://www.mediar.cz/?p=295359"
    # Adresa článku MediaGuru nese jméno z titulku: nález odkáže jen na web zdroje.
    assert by_source["mediaguru.cz"].source_url == "https://www.mediaguru.cz"
    # Články bez lihovin si agent pamatuje jako viděné: příště je nebere znovu.
    assert len(board.article_ids(source="mediar.cz")) == 2
    assert len(board.article_ids(source="mediaguru.cz")) == 3


def test_outage_of_a_secondary_source_does_not_fail_the_zadani(
    bus, board, clock, raw_dir, make_agent
):
    routes = all_sources(**{MEDIAGURU_RSS_URL: FetchError("503")})
    fetcher = FakeFetcher(routes, raw_dir=raw_dir, clock=clock)
    llm = FakeLlm(STOCK_MODERNIZUJE, irrelevant(), STOCK_MODERNIZUJE)
    medialni = make_agent(Medialni, fetcher=fetcher, llm=llm)
    zadani = send_zadani(bus)

    medialni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert board.snapshot(reply.content["snapshot_id"]).source == "zboziaprodej.cz, mediar.cz"
