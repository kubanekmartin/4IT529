"""Smyčka Dotazů přes sběrnici: Dotaz → Zadání z Dotazu → Odpověď → dopsaný Brief.

Řídicí i analytický agent jsou skuteční (`process_pending` a `on_idle` místo smyčky služby),
cenový agent čte uložené ukázky akcniceny.cz. Ostatní sběrné agenty, a kde je potřeba
i analytického agenta, hraje test na sběrnici. Pondělí jde podle rozvrhu: sběr v 6:00,
Brief v 7:00. Celá výměna kolem Briefu má jedno ID konverzace, takže ji test čte přes
`bus.conversation(...)` a log komunikace ji ukazuje pohromadě.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_analyticky import brief_text, kiq_block, section
from test_cenovy import make_cenovy
from test_ridici import answer, fail, new_zadani, zadani_for

from lihoviny.agents.analyticky import Analyticky
from lihoviny.agents.ridici import Ridici
from lihoviny.bus import Kind
from lihoviny.clock import FakeClock
from lihoviny.llm import FakeLlm
from lihoviny.planning import KIQS, SBERNY_AGENT_OF_KIQ
from lihoviny.web.app import create_app

SBERNI_AGENTI = ["sortimentni", "cenovy", "medialni", "regulatorni"]
ZADANI_Z_DOTAZU = {"instruction": "Zopakuj sběr akčních cen lihovin na akcniceny.cz."}


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 11, 16, 5, 0, tzinfo=UTC))  # pondělí 6:00 v Praze


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


def monday_brief(bus, board, clock, ridici, *, failed):
    """Pondělí podle rozvrhu: sběr v 6:00 (agenti z `failed` ohlásí Selhání s daným důvodem,
    ostatní odpoví souhrnem), v 7:00 Zadání Briefu. Vrátí záznam Zadání Briefu."""
    ridici.on_idle()
    for agent in SBERNI_AGENTI:
        if agent in failed:
            fail(bus, agent, failed[agent])
        else:
            answer(bus, agent, new_findings=0, item_count=10)
    ridici.process_pending()
    clock.advance(hours=1)
    ridici.on_idle()
    [zadani] = board.zadani(board.latest_beh("brief").id)
    return zadani


def route(conversation):
    return [(m.kind, m.sender, m.recipient) for m in conversation]


def test_dotaz_becomes_zadani_for_the_sberny_agent_and_its_answer_completes_the_brief(
    bus, board, clock, raw_dir, make_agent, web
):
    ridici_llm = FakeLlm(ZADANI_Z_DOTAZU)
    ridici = make_agent(Ridici, llm=ridici_llm)
    brief = monday_brief(bus, board, clock, ridici, failed={"cenovy": "akcniceny.cz: HTTP 429"})

    make_agent(Analyticky, llm=FakeLlm()).process_pending()  # Brief zatím nepíše, ptá se
    ridici.process_pending()  # Dotaz → Zadání z Dotazu pro cenového agenta
    make_cenovy(make_agent, clock, raw_dir).process_pending()  # doplní akční ceny na tabuli
    ridici.process_pending()  # Odpověď cenového agenta předá analytickému agentovi
    first = board.findings(kiq=2)[0]
    brief_llm = FakeLlm(brief_text(what_happened=[(2, "Řetězce zlevnily tuzemák.", [first.id])]))
    # Nová instance: na co analytický agent čeká, ví ze sběrnice, ne z paměti.
    make_agent(Analyticky, llm=brief_llm).process_pending()
    ridici.process_pending()

    conversation = bus.conversation(brief.conversation_id)
    assert route(conversation) == [
        (Kind.REQUEST, "ridici", "analyticky"),  # Zadání Briefu
        (Kind.QUERY, "analyticky", "ridici"),  # Dotaz
        (Kind.REQUEST, "ridici", "cenovy"),  # Zadání z Dotazu
        (Kind.INFORM, "cenovy", "ridici"),
        (Kind.INFORM, "ridici", "analyticky"),  # odpověď na Dotaz
        (Kind.INFORM, "analyticky", "ridici"),  # dopsaný Brief
    ]
    _, dotaz, zadani, cenovy_answer, dotaz_answer, brief_answer = conversation
    assert dotaz.content["kiq"] == 2
    assert "akcniceny.cz: HTTP 429" in dotaz.content["missing"]
    assert "cenovy" in dotaz.content["proposal"]
    assert zadani.content == {
        "beh_id": brief.beh_id,
        "kiq": 2,
        "instruction": ZADANI_Z_DOTAZU["instruction"],
        "dotaz_id": dotaz.id,
    }
    [conversion] = ridici_llm.calls
    assert conversion.effort == "low"
    assert dotaz.content["missing"] in conversion.prompt
    assert dotaz_answer.content == {
        "dotaz_id": dotaz.id,
        "agent": "cenovy",
        "new_findings": cenovy_answer.content["new_findings"],
        "item_count": cenovy_answer.content["item_count"],
    }
    assert set(brief_answer.content) == {"brief_id"}
    html = web.get("/brief").text
    assert "Řetězce zlevnily tuzemák." in kiq_block(html, 2)
    assert f'href="{first.source_url}"' in kiq_block(html, 2)
    assert "Selhání sběru" not in html  # mezeru KIQ 2 doplnění zaplnilo
    assert "Mezery ve sběru (Brief je uvede sám): žádné" in brief_llm.calls[0].prompt
    assert {(z.agent, z.outcome) for z in board.zadani(brief.beh_id)} == {
        ("analyticky", "INFORM"),
        ("cenovy", "INFORM"),
    }
    behy = web.get("/behy").text
    assert '<span class="agent">cenovy <span class="muted">z Dotazu</span></span>' in behy
    # Doplnění zaplnilo i varování: poslední výsledek cenového agenta je úspěšný.
    assert 'class="warnings"' not in behy


def test_brief_says_so_when_the_doplneni_from_dotaz_fails_too(bus, board, clock, make_agent, web):
    ridici = make_agent(Ridici, llm=FakeLlm(ZADANI_Z_DOTAZU))
    brief = monday_brief(bus, board, clock, ridici, failed={"cenovy": "akcniceny.cz: HTTP 429"})
    analyticky = make_agent(Analyticky)
    analyticky.process_pending()
    ridici.process_pending()

    fail(bus, "cenovy", "akcniceny.cz: HTTP 503")
    ridici.process_pending()
    analyticky.process_pending()

    conversation = bus.conversation(brief.conversation_id)
    assert route(conversation)[-3:] == [
        (Kind.FAILURE, "cenovy", "ridici"),
        (Kind.FAILURE, "ridici", "analyticky"),  # odpověď na Dotaz
        (Kind.INFORM, "analyticky", "ridici"),  # Brief přesto vyšel
    ]
    gaps = section(web.get("/brief").text, "Selhání sběru")
    assert (
        "<strong>KIQ 2</strong> (cenovy): akcniceny.cz: HTTP 429; doplňující sběr na Dotaz"
        " selhal: akcniceny.cz: HTTP 503"
    ) in gaps


def test_brief_comes_out_after_restart_when_analyst_fell_right_after_taking_the_last_answer(
    bus, board, clock, make_agent
):
    ridici = make_agent(Ridici, llm=FakeLlm(ZADANI_Z_DOTAZU))
    brief = monday_brief(bus, board, clock, ridici, failed={"cenovy": "akcniceny.cz: HTTP 429"})
    make_agent(Analyticky).process_pending()
    ridici.process_pending()
    fail(bus, "cenovy", "akcniceny.cz: HTTP 503")
    ridici.process_pending()
    assert bus.claim("analyticky").kind is Kind.FAILURE  # vyzvedl odpověď na Dotaz a spadl

    restarted = make_agent(Analyticky)
    restarted.process_pending()
    restarted.on_idle()
    restarted.on_idle()

    replies = [m for m in bus.conversation(brief.conversation_id) if m.sender == "analyticky"]
    assert [m.kind for m in replies] == [Kind.QUERY, Kind.INFORM]  # Brief právě jednou


def test_silent_zadani_z_dotazu_fails_at_15_minutes_so_the_brief_still_comes_out_in_time(
    bus, board, clock, make_agent, web
):
    ridici = make_agent(Ridici, llm=FakeLlm(ZADANI_Z_DOTAZU))
    brief = monday_brief(bus, board, clock, ridici, failed={"cenovy": "akcniceny.cz: HTTP 429"})
    analyticky = make_agent(Analyticky)
    analyticky.process_pending()
    ridici.process_pending()
    zadani_for(bus, "cenovy")  # vyzvedl ho a spadl, odpověď nepřijde

    clock.advance(minutes=14, seconds=59)
    ridici.on_idle()
    analyticky.process_pending()
    assert route(bus.conversation(brief.conversation_id))[-1] == (
        Kind.REQUEST,
        "ridici",
        "cenovy",
    )

    clock.advance(seconds=1)
    ridici.on_idle()
    analyticky.process_pending()
    ridici.process_pending()

    reason = "bez odpovědi do 15 minut od Zadání Briefu"
    conversation = bus.conversation(brief.conversation_id)
    assert [
        (m.kind, m.sender, m.recipient, m.content.get("reason")) for m in conversation[-3:]
    ] == [
        (Kind.FAILURE, "ridici", "cenovy", reason),
        (Kind.FAILURE, "ridici", "analyticky", reason),
        (Kind.INFORM, "analyticky", "ridici", None),
    ]
    assert f"doplňující sběr na Dotaz selhal: {reason}" in section(
        web.get("/brief").text, "Selhání sběru"
    )
    # Zadání Briefu skončilo odpovědí, ne Selháním: limit 30 minut ještě neuplynul.
    assert {(z.agent, z.outcome) for z in board.zadani(brief.beh_id)} == {
        ("analyticky", "INFORM"),
        ("cenovy", "FAILURE"),
    }


def test_analyst_asks_at_most_three_dotazy_in_a_single_round(bus, board, clock, make_agent, web):
    ridici = make_agent(Ridici, llm=FakeLlm(*[ZADANI_Z_DOTAZU] * 3))
    failed = dict.fromkeys(SBERNI_AGENTI, "HTTP 503")
    brief = monday_brief(bus, board, clock, ridici, failed=failed)
    analyticky = make_agent(Analyticky)
    analyticky.process_pending()
    ridici.process_pending()
    for agent in ["sortimentni", "cenovy", "medialni"]:
        fail(bus, agent, "HTTP 503 znovu")
    ridici.process_pending()

    analyticky.process_pending()
    ridici.process_pending()

    conversation = bus.conversation(brief.conversation_id)
    dotazy = [m for m in conversation if m.kind is Kind.QUERY]
    assert [dotaz.content["kiq"] for dotaz in dotazy] == [1, 2, 3]
    assert route(conversation)[-1] == (Kind.INFORM, "analyticky", "ridici")
    assert new_zadani(bus, "regulatorni") == []  # KIQ 4 je čtvrtá, Dotaz na ni nejde
    gaps = section(web.get("/brief").text, "Selhání sběru")
    assert gaps.count("doplňující sběr na Dotaz selhal: HTTP 503 znovu") == 3
    assert "<strong>KIQ 4</strong> (regulatorni): HTTP 503</li>" in gaps


class SlowLlm(FakeLlm):
    """Náhražka LLM, jejíž každé volání trvá 8 minut času `clock`."""

    def __init__(self, clock, *answers):
        super().__init__(*answers)
        self.clock = clock

    def ask(self, prompt, schema, *, effort, system=""):
        self.clock.advance(minutes=8)
        return super().ask(prompt, schema, effort=effort, system=system)


def test_slow_dotaz_conversion_still_keeps_the_15_minute_deadline_between_dotazy(
    bus, board, clock, make_agent
):
    # Zadání Briefu v 7:00, Zadání z Dotazu musí skončit v 7:15. Převod prvního Dotazu skončí
    # v 7:08, druhého až v 7:16: na ten už je pozdě. Před třetím Dotazem řídicí agent uzavře
    # Selháním Zadání z prvního, které sběrný agent nestihl, a třetí odmítne bez volání LLM.
    llm = SlowLlm(clock, *[ZADANI_Z_DOTAZU] * 3)
    ridici = make_agent(Ridici, llm=llm)
    failed = dict.fromkeys(SBERNI_AGENTI, "HTTP 503")
    brief = monday_brief(bus, board, clock, ridici, failed=failed)
    make_agent(Analyticky).process_pending()

    ridici.process_pending()

    assert len(llm.calls) == 2
    assert new_zadani(bus, "cenovy") == []  # na druhý Dotaz Zadání nevzniklo
    answers = [
        (m.kind, m.content.get("reason"))
        for m in bus.conversation(brief.conversation_id)
        if m.sender == "ridici" and m.recipient == "analyticky" and "dotaz_id" in m.content
    ]
    assert answers == [
        (Kind.FAILURE, "na doplňující sběr už nezbývá čas, Brief se musí dopsat"),
        (Kind.FAILURE, "bez odpovědi do 15 minut od Zadání Briefu"),
        (Kind.FAILURE, "na doplňující sběr už nezbývá čas, Brief se musí dopsat"),
    ]


def ask(bus, ridici, request, kiq):
    """Test hraje analytického agenta: pošle Dotaz v konverzaci Zadání Briefu."""
    dotaz = bus.send(
        kind=Kind.QUERY,
        sender="analyticky",
        recipient="ridici",
        content={"kiq": kiq, "missing": f"data KIQ {kiq}", "proposal": "zopakovat sběr"},
        conversation_id=request.conversation_id,
    )
    ridici.process_pending()
    return dotaz


def turned_down(bus, dotaz):
    """Důvod, se kterým řídicí agent Dotaz odmítl, nebo None, když z něj udělal Zadání."""
    for message in bus.conversation(dotaz.conversation_id):
        if message.kind is Kind.FAILURE and message.content.get("dotaz_id") == dotaz.id:
            return message.content["reason"]
    return None


def test_ridici_turns_down_a_fourth_dotaz_a_repeated_kiq_and_an_unknown_kiq(
    bus, board, clock, make_agent
):
    ridici = make_agent(Ridici, llm=FakeLlm(*[ZADANI_Z_DOTAZU] * 3))
    monday_brief(bus, board, clock, ridici, failed={})
    request = zadani_for(bus, "analyticky")

    accepted = [ask(bus, ridici, request, kiq) for kiq in (1, 2)]
    repeated = ask(bus, ridici, request, 2)
    unknown = ask(bus, ridici, request, 7)
    accepted.append(ask(bus, ridici, request, 3))
    fourth = ask(bus, ridici, request, 4)

    assert [turned_down(bus, dotaz) for dotaz in accepted] == [None, None, None]
    assert turned_down(bus, repeated) == "KIQ 2: cenovy už v tomto běhu Zadání dostal"
    assert turned_down(bus, unknown) == "Dotaz bez platné KIQ (7), řídicí agent zná KIQ 1–4"
    assert turned_down(bus, fourth) == "nejvýš 3 Dotazy na běh"
    for agent in ["sortimentni", "cenovy", "medialni"]:
        assert len(new_zadani(bus, agent)) == 1
    assert new_zadani(bus, "regulatorni") == []


def test_ridici_turns_down_a_second_round_of_dotazy_and_a_dotaz_after_the_brief(
    bus, board, clock, make_agent
):
    ridici = make_agent(Ridici, llm=FakeLlm(ZADANI_Z_DOTAZU))
    monday_brief(bus, board, clock, ridici, failed={})
    request = zadani_for(bus, "analyticky")
    ask(bus, ridici, request, 1)
    answer(bus, "sortimentni")
    ridici.process_pending()

    second_round = ask(bus, ridici, request, 2)
    bus.reply(request, Kind.INFORM, {"brief_id": 1})
    ridici.process_pending()
    after_brief = ask(bus, ridici, request, 3)

    assert turned_down(bus, second_round) == (
        "jen jedno kolo Dotazů na běh: na předchozí Dotazy už přišla odpověď"
    )
    assert turned_down(bus, after_brief) == (
        "Dotaz nepatří k žádnému Zadání, na které by tazatel odpovídal"
    )
    for agent in ["cenovy", "medialni"]:
        assert new_zadani(bus, agent) == []


def test_ridici_turns_down_a_dotaz_that_leaves_no_time_to_finish_the_brief(
    bus, board, clock, make_agent
):
    ridici = make_agent(Ridici, llm=FakeLlm())  # LLM se na pozdní Dotaz ani nevolá
    monday_brief(bus, board, clock, ridici, failed={})
    request = zadani_for(bus, "analyticky")
    clock.advance(minutes=15)

    late = ask(bus, ridici, request, 2)

    assert turned_down(bus, late) == "na doplňující sběr už nezbývá čas, Brief se musí dopsat"
    assert new_zadani(bus, "cenovy") == []


def test_answer_to_zadani_z_dotazu_whose_dotaz_is_elsewhere_is_still_recorded(
    bus, board, clock, make_agent
):
    # Zadání z Dotazu, jehož Dotaz v konverzaci není (poškozená data): odpověď sběrného agenta
    # se přesto zapíše, jinak by Zadání zůstalo otevřené a každé kolo by selhalo znovu.
    ridici = make_agent(Ridici)
    beh = board.start_beh("brief")
    stray = bus.send(kind=Kind.QUERY, sender="analyticky", recipient="nikdo", content={"kiq": 2})
    request = bus.send(kind=Kind.REQUEST, sender="ridici", recipient="cenovy", content={})
    board.add_zadani(
        beh_id=beh.id,
        agent="cenovy",
        conversation_id=request.conversation_id,
        deadline=clock.now() + timedelta(minutes=15),
        dotaz_id=stray.id,
    )
    answer(bus, "cenovy")

    ridici.process_pending()

    assert [(z.agent, z.outcome) for z in board.zadani(beh.id)] == [("cenovy", "INFORM")]


def test_every_kiq_has_a_sberny_agent_that_ridici_sends_zadani_to():
    assert set(SBERNY_AGENT_OF_KIQ) == set(KIQS)
    assert set(SBERNY_AGENT_OF_KIQ.values()) == set(Ridici.sberni_agenti)


def test_log_shows_the_whole_dotaz_exchange_together_as_one_conversation(
    bus, board, clock, make_agent, web
):
    ridici = make_agent(Ridici, llm=FakeLlm(ZADANI_Z_DOTAZU))
    brief = monday_brief(bus, board, clock, ridici, failed={"cenovy": "akcniceny.cz: HTTP 429"})
    analyticky = make_agent(Analyticky)
    analyticky.process_pending()
    ridici.process_pending()
    fail(bus, "cenovy", "akcniceny.cz: HTTP 503")
    ridici.process_pending()
    analyticky.process_pending()
    ridici.process_pending()

    page = web.get("/log")

    assert page.status_code == 200
    html = page.text
    start = html.index(f'id="konverzace-{brief.conversation_id}"')
    block = html[start : html.index("</ol>", start)]
    routes = [
        'Zadání</span> <span class="route">ridici → analyticky',
        'Dotaz</span> <span class="route">analyticky → ridici',
        'Zadání</span> <span class="route">ridici → cenovy',
        'Selhání</span> <span class="route">cenovy → ridici',
        'Selhání</span> <span class="route">ridici → analyticky',
        'Odpověď</span> <span class="route">analyticky → ridici',
    ]
    positions = [block.index(text) for text in routes]
    assert positions == sorted(positions)  # v pořadí, jak zprávy šly
    assert "Zopakuj sběr akčních cen lihovin na akcniceny.cz." in block
    assert "6 zpráv" in block
    # Konverzace s nejnovější zprávou je nahoře; ranní Zadání sběru jsou každé zvlášť pod ní.
    assert html.index(f'id="konverzace-{brief.conversation_id}"') < html.index(
        'class="route">ridici → sortimentni'
    )
    assert html.count('class="conversation"') == 1 + len(SBERNI_AGENTI)
