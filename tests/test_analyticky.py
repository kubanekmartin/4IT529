"""Analytický agent přes sběrnici a tabuli: Zadání Briefu → Brief uložený → stránka ho ukáže.

Nálezy zapisují testy na tabuli stejně jako sběrní agenti (snímek, produkt, nález). LLM
nahrazuje `FakeLlm` s připraveným textem Briefu; čísla nálezů v něm jsou ID z tabule.
Poslední test projde celý běh se skutečnými agenty ve smyčkách, jako běží v compose.
"""

import time
from datetime import UTC, datetime
from decimal import Decimal

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from test_cenovy import make_cenovy

from lihoviny.agents.analyticky import Analyticky, BriefText
from lihoviny.agents.medialni import Medialni
from lihoviny.agents.regulatorni import Regulatorni
from lihoviny.agents.ridici import ANSWER_LIMIT, FINISH_TIME, Ridici
from lihoviny.agents.sortimentni import Sortimentni
from lihoviny.bus import Kind
from lihoviny.cli import build_agent
from lihoviny.clock import FakeClock
from lihoviny.llm import TIMEOUT, FakeLlm
from lihoviny.web.app import create_app

ROHLIK = "https://www.rohlik.cz/"
AKCNI_CENY = "https://www.akcniceny.cz/akce/"


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


def record_novinka(board, *, name, brand, manufacturer, category="rum_tuzemak", source_id=1):
    """Novinka, jak ji zapíše sortimentní agent: snímek, produkt a nález s odkazem na produkt."""
    url = f"{ROHLIK}{source_id}"
    snapshot = board.record_snapshot(
        agent="sortimentni", source="rohlik.cz", fetches=[], item_count=1
    )
    product = board.record_product(
        normalized_key=f"{name.lower()}|500|37.5",
        manufacturer=manufacturer,
        brand=brand,
        name=name,
        volume_ml=500,
        abv=Decimal("37.5"),
        category=category,
        first_seen_url=url,
        first_seen_snapshot_id=snapshot.id,
        source_product_id=source_id,
    )
    return board.record_finding(
        kiq=1,
        agent="sortimentni",
        type="novinka",
        snapshot_id=snapshot.id,
        product_id=product.id,
        source_url=url,
        content={
            "name": name,
            "brand": brand,
            "manufacturer": manufacturer,
            "category": category,
            "volume_ml": 500,
            "abv": 37.5,
            "seller_label": None,
        },
    )


def record_akcni_ceny(board, *offers):
    """Snímek cenového agenta s akčními cenami `(produkt, řetězec, cena, sleva %, Kč/l etanolu)`."""
    snapshot = board.record_snapshot(
        agent="cenovy", source="akcniceny.cz", fetches=[], item_count=len(offers)
    )
    findings = []
    for product, chain, price, discount, ethanol in offers:
        slug = product.lower().replace(" ", "-").replace(",", "-")
        findings.append(
            board.record_finding(
                kiq=2,
                agent="cenovy",
                type="akcni_cena",
                snapshot_id=snapshot.id,
                source_url=f"{AKCNI_CENY}{slug}/",
                content={
                    "product": product,
                    "chain": chain,
                    "price": price,
                    "card_price": None,
                    "card_label": None,
                    "discount_percent": discount,
                    "valid_from": None,
                    "valid_until": "2026-11-22",
                    "volume_l": 0.5,
                    "abv_percent": None,
                    "price_per_l": round(price / 0.5, 2),
                    "price_per_l_ethanol": ethanol,
                    "source_category": "bylinné destiláty",
                    "flyer_url": None,
                },
            )
        )
    board.record_snapshot_findings(snapshot.id, [f.id for f in findings])
    return findings


def send_brief_zadani(bus, board, collection=None):
    """Zadání Briefu tak, jak ho posílá řídicí agent: běh a výsledky posledního sběru."""
    beh = board.start_beh("brief")
    return bus.send(
        kind=Kind.REQUEST,
        sender="ridici",
        recipient="analyticky",
        content={"beh_id": beh.id, "collection": collection},
    )


def brief_text(what_happened=(), unusual=(), recommendations=()):
    """Odpověď LLM ve tvaru, který analytický agent žádá."""
    return {
        "what_happened": [
            {"kiq": kiq, "claims": [{"text": text, "sources": sources}]}
            for kiq, text, sources in what_happened
        ],
        "unusual": [{"text": text, "sources": sources} for text, sources in unusual],
        "recommendations": [
            {"product": product, "text": text, "sources": sources}
            for product, text, sources in recommendations
        ],
    }


def section(html, heading):
    """Část stránky od nadpisu `<h2>heading</h2>` po další nadpis h2."""
    start = html.index(f"<h2>{heading}</h2>")
    end = html.find("<h2", start + 1)
    return html[start : end if end != -1 else len(html)]


def kiq_block(html, kiq):
    """Blok jedné KIQ v části Co se stalo."""
    start = html.index(f"<h3>KIQ {kiq} ")
    return html[start : html.index("</article>", start)]


def chart(html, title):
    """Graf s daným titulkem: SVG a tabulka dat pod ním."""
    start = html.index(f"<figcaption>{title}</figcaption>")
    return html[start : html.index("</figure>", start)]


def wait_until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not (result := condition()) and time.monotonic() < deadline:
        time.sleep(0.05)
    return result


def test_brief_zadani_stores_brief_whose_claims_link_to_the_findings_sources(
    bus, board, make_agent, web
):
    novinka = record_novinka(
        board, name="Božkov Na pečení", brand="Božkov", manufacturer="Stock", source_id=1485101
    )
    [fernet] = record_akcni_ceny(board, ("Fernet Stock 0,5l", "BILLA", 169.9, 30, 969.71))
    llm = FakeLlm(
        brief_text(
            what_happened=[
                (1, "Stock uvedl na Rohlíku Božkov Na pečení.", [novinka.id]),
                (2, "BILLA zlevnila Fernet Stock o 30 %.", [fernet.id]),
            ],
            recommendations=[
                ("bylinny-05", "Bylinný likér zvýhodnit proti Fernetu Stock.", [fernet.id]),
            ],
        )
    )
    zadani = send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=llm).process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert (reply.kind, reply.sender, reply.recipient) == (Kind.INFORM, "analyticky", "ridici")
    assert set(reply.content) == {"brief_id"}
    assert llm.calls[0].effort == "high"
    page = web.get("/brief")
    assert page.status_code == 200
    what_happened = section(page.text, "Co se stalo podle KIQ")
    assert "Stock uvedl na Rohlíku Božkov Na pečení." in what_happened
    assert f'href="{ROHLIK}1485101"' in what_happened
    assert f'href="{AKCNI_CENY}fernet-stock-0-5l/"' in what_happened
    recommendations = section(page.text, "Doporučení k portfoliu zadavatele")
    assert "Valnovský bylinný likér 35 % 0,5 l" in recommendations
    assert "Bylinný likér zvýhodnit proti Fernetu Stock." in recommendations
    assert f'href="{AKCNI_CENY}fernet-stock-0-5l/"' in recommendations


def test_brief_keeps_only_claims_backed_by_findings_from_the_board(bus, board, make_agent, web):
    [fernet, becherovka] = record_akcni_ceny(
        board,
        ("Fernet Stock 0,5l", "BILLA", 169.9, 30, 969.71),
        ("Becherovka 0,5l", "Albert", 199.9, 25, 1052.11),
    )
    llm = FakeLlm(
        brief_text(
            what_happened=[
                (2, "BILLA zlevnila Fernet Stock.", [fernet.id, 999]),
                (2, "Vymyšlená akční cena.", [999]),
                (2, "Tvrzení bez zdroje.", []),
            ],
            unusual=[("Netypický nález bez zdroje.", [12345])],
            recommendations=[
                ("bylinny-05", "Zlevnit bylinný likér.", [becherovka.id]),
                ("neexistuje-07", "Produkt mimo portfolio.", [fernet.id]),
            ],
        )
    )
    zadani = send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=llm).process_pending()

    html = web.get("/brief").text
    assert "BILLA zlevnila Fernet Stock." in html
    assert "Zlevnit bylinný likér." in html
    for invented in ["Vymyšlená", "bez zdroje", "Produkt mimo portfolio"]:
        assert invented not in html
    brief_id = bus.conversation(zadani.conversation_id)[-1].content["brief_id"]
    assert board.brief_finding_ids(brief_id) == [fernet.id, becherovka.id]


def test_watched_competitors_come_first_and_other_manufacturers_only_as_unusual_findings(
    bus, board, make_agent, web
):
    # Výrobce akční ceny zná Brief podle značky produktu, který viděl sortimentní agent.
    record_novinka(
        board, name="Fernet Stock Citrus", brand="Fernet Stock", manufacturer="Stock Plzeň-Božkov"
    )
    record_novinka(
        board, name="Jägermeister Orange", brand="Jägermeister", manufacturer="Mast-Jägermeister"
    )
    jager, captain, fernet = record_akcni_ceny(
        board,
        ("Jägermeister 0,7l", "Tesco", 299.9, 40, 1224.08),
        ("Captain Morgan Spiced 0,7l", "Lidl", 299.9, 35, None),
        ("Fernet Stock 0,5l", "BILLA", 169.9, 30, 969.71),
    )
    llm = FakeLlm(
        {
            "what_happened": [
                {
                    "kiq": 2,
                    "claims": [
                        {"text": "Tesco zlevnilo Jägermeister o 40 %.", "sources": [jager.id]},
                        {"text": "Lidl zlevnil Captain Morgan.", "sources": [captain.id]},
                        {"text": "BILLA zlevnila Fernet Stock.", "sources": [fernet.id]},
                    ],
                }
            ],
            "unusual": [],
            "recommendations": [],
        }
    )
    send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=llm).process_pending()

    html = web.get("/brief").text
    promo = kiq_block(html, 2)
    assert promo.index("BILLA zlevnila Fernet Stock.") < promo.index("Lidl zlevnil Captain")
    assert '<span class="tag">Stock</span> BILLA zlevnila Fernet Stock.' in promo
    assert "Jägermeister" not in promo
    assert "Tesco zlevnilo Jägermeister o 40 %." in section(html, "Netypické nálezy")
    prompt = llm.calls[0].prompt
    assert "Fernet Stock 0,5l, BILLA, 169,90 Kč, sleva 30 %" in prompt
    assert "výrobce Stock (sledovaný konkurent)" in prompt
    assert "výrobce Mast-Jägermeister\n" in prompt
    # Sledovaný konkurent je v zadání pro LLM před ostatními, i s menší slevou.
    assert prompt.index(f"[F{fernet.id}]") < prompt.index(f"[F{jager.id}]")


def test_brief_has_four_svg_charts_computed_from_findings_each_with_a_data_table(
    bus, board, clock, make_agent, web
):
    record_novinka(board, name="Fernet Stock Citrus", brand="Fernet Stock", manufacturer="Stock")
    fernet, _ = record_akcni_ceny(
        board,
        ("Fernet Stock 0,5l", "BILLA", 169.9, 30, 969.71),
        ("Becherovka 0,5l", "BILLA", 199.9, 25, 1052.11),
    )
    clock.advance(days=1)
    record_akcni_ceny(board, ("Jägermeister 0,7l", "<script>alert(1)</script>", 299.9, 40, None))
    [second_day] = board.snapshots(agent="cenovy")[1:]
    board.record_snapshot_findings(second_day.id, [fernet.id])  # Fernet platí i druhý den
    clock.advance(hours=1)
    send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=FakeLlm(brief_text())).process_pending()

    html = web.get("/brief").text
    assert html.count('<svg xmlns="http://www.w3.org/2000/svg" class="graf"') == 4
    assert "<script" not in html
    chains = chart(html, "Akční ceny podle řetězce")
    assert "<td>BILLA</td><td>2</td>" in chains
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in chains
    days = chart(html, "Akční ceny sledovaných konkurentů podle dne")
    assert '<th scope="col">Stock</th><th scope="col">Rudolf Jelínek</th>' in days
    assert "<td>16. 11.</td><td>1</td><td>0</td><td>0</td><td>0</td>" in days
    assert "<td>17. 11.</td><td>1</td><td>0</td><td>0</td><td>0</td>" in days
    portfolio = chart(
        html, "Cena za litr etanolu: portfolio zadavatele a nejnižší akční cena v kategorii"
    )
    assert (
        "<td>Valnovský bylinný likér 35 % 0,5 l</td><td>1\xa0257 Kč</td>"
        "<td>970 Kč (Fernet Stock 0,5l, BILLA)</td>"
    ) in portfolio
    assert "<td>Valnov Dry Gin 40 % 0,7 l</td><td>1\xa0607 Kč</td>" in portfolio
    assert "bez akční ceny se známým obsahem alkoholu" in portfolio
    assert "<td>Rum a tuzemák</td><td>1</td>" in chart(html, "Novinky podle kategorie")


def record_clanek(board, article_id, title, manufacturers):
    """Článek, jak ho zapíše mediální agent (#23): výrobců může být víc."""
    snapshot = board.record_snapshot(
        agent="medialni", source="zboziaprodej.cz", fetches=[], item_count=30
    )
    return board.record_finding(
        kiq=3,
        agent="medialni",
        type="clanek",
        snapshot_id=snapshot.id,
        source_url=f"https://www.zboziaprodej.cz/?p={article_id}",
        content={
            "article_id": article_id,
            "published_at": "2026-11-12T07:31:41+00:00",
            "title": title,
            "manufacturers": manufacturers,
            "brands": [],
            "channel": "pr",
            "topic": "portfolio",
            "summary": "Výrobce rozšiřuje nabídku. Investuje do výroby.",
        },
    )


def test_articles_and_snemovni_tisky_follow_their_agents_contracts(bus, board, make_agent, web):
    stock = record_clanek(board, 83123, "Stock modernizuje výrobu", ["Stock", "Diageo"])
    jager = record_clanek(board, 83200, "Jägermeister mění láhev", ["Mast-Jägermeister"])
    snapshot = board.record_snapshot(agent="regulatorni", source="psp.cz", fetches=[], item_count=1)
    tisk = board.record_finding(
        kiq=4,
        agent="regulatorni",
        type="snemovni_tisk",
        snapshot_id=snapshot.id,
        source_url="https://www.psp.cz/sqw/historie.sqw?o=10&t=133",
        content={
            "number": "133",
            "title": "Novela z. o spotřebních daních",
            "kind": "Návrh zákona",
            "proposer": "skupina poslanců (ODS)",
            "state": "1. čtení",
            "impact": "Vyšší daň z lihu zdraží lihoviny.",
            "impact_level": "vysoký",
            "effective_date": "2027-01-01",
            "baseline": True,
        },
    )
    llm = FakeLlm(
        brief_text(
            what_happened=[
                (3, "Stock investuje do výroby.", [stock.id]),
                (3, "Jägermeister mění láhev.", [jager.id]),
                (4, "Sněmovna projednává vyšší daň z lihu.", [tisk.id]),
            ]
        )
    )
    send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=llm).process_pending()

    html = web.get("/brief").text
    assert '<span class="tag">Stock</span> Stock investuje do výroby.' in kiq_block(html, 3)
    assert "Jägermeister mění láhev." in section(html, "Netypické nálezy")
    assert (
        "Tisk 133: Novela z. o spotřebních daních (Poslanecká sněmovna PČR, otevřená data psp.cz)"
        in kiq_block(html, 4)
    )
    assert "Stock modernizuje výrobu (zboziaprodej.cz)" in kiq_block(html, 3)
    prompt = llm.calls[0].prompt
    assert (
        f"[F{stock.id}] článek: Stock modernizuje výrobu, vyšel 12. 11. 2026, "
        "Výrobce rozšiřuje nabídku. Investuje do výroby., "
        "výrobce Stock (sledovaný konkurent), Diageo"
    ) in prompt
    assert (
        f"[F{tisk.id}] sněmovní tisk 133: Novela z. o spotřebních daních, Návrh zákona, "
        "skupina poslanců (ODS), stav 1. čtení, dopad vysoký: Vyšší daň z lihu zdraží lihoviny., "
        "účinnost asi od 2027-01-01, projednává se už od začátku sledování"
    ) in prompt


def test_source_link_is_only_http_or_https(bus, board, make_agent, web):
    snapshot = board.record_snapshot(agent="medialni", source="zdroj", fetches=[], item_count=1)
    article = board.record_finding(
        kiq=3,
        agent="medialni",
        type="clanek",
        snapshot_id=snapshot.id,
        source_url="javascript:alert(1)",
        content={"title": "Kampaň na rum"},
    )
    llm = FakeLlm(brief_text(what_happened=[(3, "Výrobce spustil kampaň.", [article.id])]))
    send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=llm).process_pending()

    html = kiq_block(web.get("/brief").text, 3)
    assert "Výrobce spustil kampaň." in html
    assert "Kampaň na rum" in html
    assert "javascript:" not in html


def test_kiq_without_findings_is_bez_dat_and_the_llm_writes_only_about_the_others(
    bus, board, make_agent, web
):
    [fernet] = record_akcni_ceny(board, ("Fernet Stock 0,5l", "BILLA", 169.9, 30, 969.71))
    llm = FakeLlm(brief_text(what_happened=[(2, "BILLA zlevnila Fernet Stock.", [fernet.id])]))
    send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=llm).process_pending()

    html = web.get("/brief").text
    assert "BILLA zlevnila Fernet Stock." in kiq_block(html, 2)
    assert "bez dat" not in kiq_block(html, 2)
    for kiq in (1, 3, 4):
        assert "<strong>bez dat</strong>: sběr pro tuto KIQ zatím nezachytil žádný snímek" in (
            kiq_block(html, kiq)
        )
    prompt = llm.calls[0].prompt
    assert "KIQ 3 Komunikace: bez dat" in prompt
    assert f"[F{fernet.id}] akční cena: Fernet Stock 0,5l, BILLA, 169,90 Kč" in prompt


def test_brief_without_any_findings_still_comes_out_without_asking_the_llm(
    bus, board, make_agent, web
):
    board.record_snapshot(agent="sortimentni", source="rohlik.cz", fetches=[], item_count=150)
    zadani = send_brief_zadani(bus, board)

    make_agent(Analyticky, llm=FakeLlm()).process_pending()

    assert bus.conversation(zadani.conversation_id)[-1].kind is Kind.INFORM
    html = web.get("/brief").text
    assert "<strong>bez dat</strong>: za období žádný nový nález" in kiq_block(html, 1)
    assert html.count("<strong>bez dat</strong>") == 4
    assert "Bez nálezů za období Brief nic nedoporučuje." in html
    assert html.count('<svg xmlns="http://www.w3.org/2000/svg" class="graf"') == 4


def test_web_says_so_when_no_brief_exists_yet(web):
    page = web.get("/brief")

    assert page.status_code == 200
    assert "Zatím žádný Brief." in page.text
    assert "Zatím žádný Brief." in web.get("/brief/archiv").text
    assert web.get("/brief/1").status_code == 404
    assert web.get("/", follow_redirects=False).headers["location"] == "/brief"


def test_brief_states_selhani_of_the_last_collection_explicitly(bus, board, make_agent, web):
    [fernet] = record_akcni_ceny(board, ("Fernet Stock 0,5l", "BILLA", 169.9, 30, 969.71))
    collection = {
        "beh_id": 7,
        "results": {
            "cenovy": {"outcome": "INFORM", "new_findings": 1, "item_count": 330},
            "sortimentni": {"outcome": "FAILURE", "reason": "rohlik.cz: HTTP 429"},
        },
    }
    llm = FakeLlm(brief_text(what_happened=[(2, "BILLA zlevnila Fernet Stock.", [fernet.id])]))
    send_brief_zadani(bus, board, collection)
    analyticky = make_agent(Analyticky, llm=llm)
    analyticky.process_pending()
    # Na Dotaz o KIQ 1 odpoví řídicí agent Selháním (smyčka Dotazů viz test_dotazy.py).
    dotaz = bus.claim("ridici")
    assert (dotaz.kind, dotaz.content["kiq"]) == (Kind.QUERY, 1)
    bus.reply(dotaz, Kind.FAILURE, {"dotaz_id": dotaz.id, "reason": "rohlik.cz: HTTP 503"})

    analyticky.process_pending()

    html = web.get("/brief").text
    gaps = section(html, "Selhání sběru")
    assert (
        "<strong>KIQ 1</strong> (sortimentni): rohlik.cz: HTTP 429;"
        " doplňující sběr na Dotaz selhal: rohlik.cz: HTTP 503"
    ) in gaps
    assert "cenovy" not in gaps
    assert "Selhání sběru: rohlik.cz: HTTP 429" in kiq_block(html, 1)
    assert "KIQ 1 (sortimentni): rohlik.cz: HTTP 429" in llm.calls[0].prompt


@pytest.mark.parametrize(
    ("content", "gap"),
    [
        ({"beh_id": None, "collection": None}, "zatím neproběhl žádný sběr"),
        ({}, "Zadání nepřišlo od řídicího agenta, výsledky sběru nejsou známé"),
        (
            {"collection": {"beh_id": 3, "results": {"cenovy": {"outcome": None}}}},
            "<strong>KIQ 2</strong> (cenovy): v době psaní Briefu sběr ještě neodpověděl",
        ),
        (
            {"collection": {"results": {"cenovy": {"outcome": "INFORM", "item_count": 0}}}},
            "<strong>KIQ 2</strong> (cenovy): prázdný snímek (0 položek)",
        ),
    ],
    ids=["bez-sberu", "zadani-od-clena-tymu", "sber-neodpovedel", "prazdny-snimek"],
)
def test_brief_names_every_gap_in_the_collection(bus, make_agent, web, content, gap):
    bus.send(kind=Kind.REQUEST, sender="clen-tymu", recipient="analyticky", content=content)

    make_agent(Analyticky).process_pending()

    assert gap in section(web.get("/brief").text, "Selhání sběru")


def test_archive_lists_briefs_newest_first_and_an_older_brief_stays_readable(
    bus, board, clock, make_agent, web
):
    analyticky = make_agent(Analyticky)
    first = send_brief_zadani(bus, board, {"beh_id": 1, "results": {}})
    analyticky.process_pending()
    clock.advance(days=7)
    second = send_brief_zadani(bus, board, {"beh_id": 2, "results": {"cenovy": {"outcome": None}}})
    analyticky.process_pending()
    first_id, second_id = (
        bus.conversation(z.conversation_id)[-1].content["brief_id"] for z in (first, second)
    )

    archive = web.get("/brief/archiv").text
    assert archive.index(f'href="/brief/{second_id}">Brief 23. 11. 2026') < archive.index(
        f'href="/brief/{first_id}">Brief 16. 11. 2026'
    )
    assert "Selhání sběru: 1" in archive
    current = web.get("/brief").text
    assert "<h1>Brief 23. 11. 2026</h1>" in current
    assert '<a href="/brief" aria-current="page">Brief</a>' in current
    older = web.get(f"/brief/{first_id}").text
    assert "<h1>Brief 16. 11. 2026</h1>" in older
    assert "Starší Brief z archivu." in older
    assert "Starší Brief" not in web.get(f"/brief/{second_id}").text


def test_writing_the_brief_takes_longer_than_extraction_but_fits_after_the_dotazy(
    monkeypatch, database_url
):
    attempts = []

    def hanging_api(request):
        attempts.append(request.extensions["timeout"]["read"])
        raise httpx2.ReadTimeout("API neodpovídá", request=request)

    real_client = anthropic.Anthropic
    monkeypatch.setattr(
        anthropic,
        "Anthropic",
        lambda **kwargs: real_client(
            **kwargs,
            api_key="test-bez-hodnoty",
            http_client=httpx2.Client(transport=httpx2.MockTransport(hanging_api)),
        ),
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    analyticky = build_agent("analyticky")  # tak, jak ho spouští služba v compose

    with pytest.raises(anthropic.APITimeoutError):
        analyticky.llm.ask("Napiš Brief.", BriefText, effort="high")

    assert min(attempts) > TIMEOUT  # delší než volání sběrných agentů
    # Odpovědi na Dotazy přijdou nejpozději FINISH_TIME před koncem limitu řídicího agenta;
    # i nejhorší případ psaní Briefu (všechny pokusy vyprší) se do té doby vejde i s rezervou
    # na kolo smyčky služby (30 s), než si agent poslední odpověď vyzvedne.
    assert sum(attempts) + 30 < FINISH_TIME.total_seconds()
    assert FINISH_TIME < ANSWER_LIMIT


@pytest.mark.parametrize(
    "clock", [FakeClock(datetime(2026, 11, 16, 5, 0, tzinfo=UTC))], ids=["pondeli-6-00"]
)
def test_monday_beh_runs_from_ridici_through_sberni_agenti_and_dotazy_to_the_brief_on_the_web(
    board, bus, clock, raw_dir, make_agent, start_agent, web
):
    # Cenový agent čte uložené ukázky akcniceny.cz; ostatní sběrní agenti nedostanou stránku
    # zdroje a ohlásí Selhání, a to i na Zadání z Dotazu analytického agenta. Nálezy čísluje
    # čerstvá databáze od 1; připravený text Briefu cituje první dvě akční ceny.
    llm = FakeLlm(
        brief_text(
            what_happened=[(2, "Řetězce zlevnily tuzemák.", [1, 2])],
            recommendations=[("tuzemak-05", "Tuzemák držet bez akční ceny.", [1])],
        )
    )
    ridici_llm = FakeLlm(*[{"instruction": "Zopakuj sběr svého zdroje."}] * 3)
    for agent in [
        make_cenovy(make_agent, clock, raw_dir),
        make_agent(Sortimentni),
        make_agent(Medialni),
        make_agent(Regulatorni),
        make_agent(Analyticky, llm=llm),
        make_agent(Ridici, llm=ridici_llm),
    ]:
        start_agent(agent, poll_interval=0.1)

    def collection_finished():
        beh = board.latest_beh("collection")
        return beh and all(zadani.outcome for zadani in board.zadani(beh.id))

    assert wait_until(collection_finished)
    clock.advance(hours=1)  # pondělí 7:00: řídicí agent zadá Brief
    assert wait_until(lambda: "Řetězce zlevnily tuzemák." in web.get("/brief").text)

    html = web.get("/brief").text
    first, second = board.findings(kiq=2)[:2]
    promo = kiq_block(html, 2)
    assert f'href="{first.source_url}"' in promo
    assert f'href="{second.source_url}"' in promo
    gaps = section(html, "Selhání sběru")
    assert "<strong>KIQ 1</strong> (sortimentni): https://www.rohlik.cz/c300108072-lihoviny" in gaps
    for kiq, agent in [(1, "sortimentni"), (3, "medialni"), (4, "regulatorni")]:
        assert gaps.count(f"<strong>KIQ {kiq}</strong> ({agent}): ") == 1
    assert gaps.count("doplňující sběr na Dotaz selhal") == 3
    assert "cenovy" not in gaps
    assert "Tuzemák držet bez akční ceny." in section(html, "Doporučení k portfoliu zadavatele")
    assert "<strong>bez dat</strong>" in kiq_block(html, 3)
    beh_id = board.latest_beh("brief").id
    assert wait_until(
        lambda: (
            {(z.agent, z.outcome) for z in board.zadani(beh_id)}
            == {
                ("analyticky", "INFORM"),
                ("sortimentni", "FAILURE"),
                ("medialni", "FAILURE"),
                ("regulatorni", "FAILURE"),
            }
        )
    )
    [brief_zadani] = [z for z in board.zadani(beh_id) if z.agent == "analyticky"]
    kinds = [m.kind for m in bus.conversation(brief_zadani.conversation_id)]
    assert kinds.count(Kind.QUERY) == 3
    assert len(ridici_llm.calls) == 3
