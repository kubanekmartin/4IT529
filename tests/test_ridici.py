"""Řídicí agent přes sběrnici: běh pošle Zadání sběrným agentům a hlídá jejich odpovědi.

Sběrní agenti jsou tu náhražky se jmény `sberny-a` a `sberny-b`; skuteční se zapíšou
do `Ridici.collectors`. Čas posouvá `FakeClock`, takže limit 30 minut se netestuje čekáním.
Výsledek běhů (dokončen, varování) se kontroluje přes HTTP na stránce Běhy.
"""

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from lihoviny.agent import Agent, CollectorResult
from lihoviny.agents import AGENTS
from lihoviny.agents.ridici import Ridici
from lihoviny.bus import Kind
from lihoviny.clock import FakeClock
from lihoviny.web.app import create_app


class RidiciSNahrazkami(Ridici):
    collectors = ("sberny-a", "sberny-b")


class RidiciSAnalytikem(RidiciSNahrazkami):
    analyst = "analyticky"


class SbernyA(Agent):
    """Náhražka sběrného agenta pro běžící smyčku: na každé Zadání vrátí 2 nové nálezy."""

    name = "sberny-a"
    role = "náhražka sběrného agenta"

    def handle_request(self, request):
        return CollectorResult(snapshot_id=1, new_findings=2)


class SbernyB(SbernyA):
    name = "sberny-b"


def start_manual_run(bus, content=None):
    return bus.send(
        kind=Kind.REQUEST, sender="clen-tymu", recipient="ridici", content=content or {}
    )


def zadani_for(bus, agent):
    """Vyzvedne Zadání, které čeká na agenta (jako by si ho vyzvedl on sám)."""
    message = bus.claim(agent)
    assert message is not None, f"{agent} nedostal žádnou zprávu"
    assert message.kind is Kind.REQUEST
    return message


def new_zadani(bus, agent):
    """Vyzvedne všechny čekající zprávy agenta a vrátí z nich jen Zadání."""
    messages = []
    while (message := bus.claim(agent)) is not None:
        messages.append(message)
    return [m for m in messages if m.kind is Kind.REQUEST]


def answer(bus, agent, new_findings=3):
    """Sběrný agent vyzvedne Zadání a odpoví souhrnem podle kontraktu CollectorResult."""
    zadani = zadani_for(bus, agent)
    bus.reply(zadani, Kind.INFORM, {"snapshot_id": 1, "new_findings": new_findings})
    return zadani


def fail(bus, agent, reason):
    zadani = zadani_for(bus, agent)
    bus.reply(zadani, Kind.FAILURE, {"reason": reason})
    return zadani


@pytest.fixture
def web(web_database_url):
    return TestClient(create_app(web_database_url))


def warnings_on(page):
    """Text bloku varování na stránce Běhy, nebo prázdný řetězec, když tam žádný není."""
    html = page.text
    start = html.find('<section class="warnings"')
    return "" if start == -1 else html[start : html.index("</section>", start)]


def replies(bus, zadani):
    return [
        (m.kind, m.sender, m.recipient, m.content)
        for m in bus.conversation(zadani.conversation_id)[1:]
    ]


def test_ridici_is_registered_and_sends_zadani_only_to_registered_agents():
    recipients = [*Ridici.collectors, *([Ridici.analyst] if Ridici.analyst else [])]

    assert AGENTS["ridici"] is Ridici
    assert [name for name in recipients if name not in AGENTS] == []
    assert "ozvena" not in Ridici.collectors
    assert set(Ridici.collectors) == {"cenovy", "sortimentni"}


def test_manual_run_sends_zadani_to_every_collector_and_answers_with_the_run(bus, make_agent):
    request = start_manual_run(bus)

    make_agent(RidiciSNahrazkami).process_pending()

    reply = bus.conversation(request.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert reply.content == {
        "run_id": reply.content["run_id"],
        "run": "collection",
        "sent_to": ["sberny-a", "sberny-b"],
    }
    for collector in ["sberny-a", "sberny-b"]:
        zadani = zadani_for(bus, collector)
        assert (zadani.sender, zadani.content) == ("ridici", {"run_id": reply.content["run_id"]})


def test_silent_collector_gets_selhani_after_30_minutes_and_answering_one_does_not(
    bus, clock, make_agent
):
    ridici = make_agent(RidiciSNahrazkami)
    start_manual_run(bus)
    ridici.process_pending()
    answered = answer(bus, "sberny-a")
    silent = zadani_for(bus, "sberny-b")  # vyzvedl ho a spadl, odpověď nepřijde
    ridici.process_pending()

    clock.advance(minutes=29, seconds=59)
    ridici.on_idle()
    assert replies(bus, silent) == []

    clock.advance(seconds=1)
    ridici.on_idle()
    ridici.on_idle()

    assert replies(bus, silent) == [
        (Kind.FAILURE, "ridici", "sberny-b", {"reason": "bez odpovědi do 30 minut"})
    ]
    assert [kind for kind, *_ in replies(bus, answered)] == [Kind.INFORM]


@pytest.mark.parametrize(
    "clock",
    [
        FakeClock(datetime(2026, 11, 17, 4, 59, tzinfo=UTC)),  # úterý 5:59 v Praze, zimní čas
        FakeClock(datetime(2027, 6, 15, 3, 59, tzinfo=UTC)),  # úterý 5:59 v Praze, letní čas
    ],
    ids=["zimni-cas", "letni-cas"],
)
def test_daily_run_sends_zadani_once_at_six_prague_time(bus, clock, make_agent):
    ridici = make_agent(RidiciSNahrazkami)
    ridici.on_idle()
    assert new_zadani(bus, "sberny-a") == []

    clock.advance(minutes=1)
    ridici.on_idle()
    ridici.on_idle()

    for collector in ["sberny-a", "sberny-b"]:
        assert [m.sent_at for m in new_zadani(bus, collector)] == [clock.now()]


def test_daily_run_missed_while_ridici_was_down_still_runs_that_day_but_not_the_next(
    bus, clock, make_agent
):
    clock.advance(hours=7)  # pondělí 14:00 v Praze, řídicí agent v 6:00 neběžel
    make_agent(RidiciSNahrazkami).on_idle()
    zadani_for(bus, "sberny-a")

    clock.advance(hours=15, minutes=59)  # úterý 5:59, restart před termínem
    make_agent(RidiciSNahrazkami).on_idle()
    assert new_zadani(bus, "sberny-a") == []


@pytest.mark.parametrize(
    "clock", [FakeClock(datetime(2026, 11, 16, 5, 0, tzinfo=UTC))], ids=["pondeli-6-00"]
)
def test_monday_brief_zadani_reaches_analyst_at_seven_with_results_of_the_collection(
    bus, clock, make_agent
):
    ridici = make_agent(RidiciSAnalytikem)
    ridici.on_idle()
    collection_zadani = answer(bus, "sberny-a", new_findings=0)
    fail(bus, "sberny-b", "HTTP 429")
    ridici.process_pending()

    clock.advance(minutes=59)
    ridici.on_idle()
    assert new_zadani(bus, "analyticky") == []

    clock.advance(minutes=1)
    ridici.on_idle()
    ridici.on_idle()

    [brief] = new_zadani(bus, "analyticky")
    assert brief.content == {
        "run_id": brief.content["run_id"],
        "collection": {
            "run_id": collection_zadani.content["run_id"],
            "results": {
                "sberny-a": {"outcome": "INFORM", "new_findings": 0},
                "sberny-b": {"outcome": "FAILURE", "reason": "HTTP 429"},
            },
        },
    }


@pytest.mark.parametrize(
    ("ridici_class", "clock"),
    [
        (RidiciSNahrazkami, FakeClock(datetime(2026, 11, 16, 6, 0, tzinfo=UTC))),
        (RidiciSAnalytikem, FakeClock(datetime(2026, 11, 17, 6, 0, tzinfo=UTC))),
    ],
    ids=["bez-analytickeho-agenta", "v-utery"],
)
def test_no_brief_zadani_without_analyst_or_outside_monday(bus, ridici_class, make_agent):
    make_agent(ridici_class).on_idle()

    assert new_zadani(bus, "analyticky") == []


def test_manual_brief_run_sends_zadani_to_analyst(bus, make_agent):
    request = start_manual_run(bus, {"run": "brief"})

    make_agent(RidiciSAnalytikem).process_pending()

    reply = bus.conversation(request.conversation_id)[-1]
    run_id = reply.content["run_id"]
    assert (reply.kind, reply.content) == (
        Kind.INFORM,
        {"run_id": run_id, "run": "brief", "sent_to": ["analyticky"]},
    )
    assert [m.content for m in new_zadani(bus, "analyticky")] == [
        {"run_id": run_id, "collection": None}
    ]


@pytest.mark.parametrize(
    ("ridici_class", "content", "reason"),
    [
        (
            RidiciSNahrazkami,
            {"run": "brief"},
            "Brief nemá komu zadat: analytický agent zatím neexistuje",
        ),
        (
            RidiciSAnalytikem,
            {"run": "inventura"},
            "neznámý běh 'inventura', znám collection a brief",
        ),
    ],
    ids=["brief-bez-analytickeho-agenta", "neznamy-beh"],
)
def test_manual_run_that_cannot_start_answers_with_selhani(
    bus, make_agent, ridici_class, content, reason
):
    request = start_manual_run(bus, content)

    make_agent(ridici_class).process_pending()

    reply = bus.conversation(request.conversation_id)[-1]
    assert (reply.kind, reply.content) == (Kind.FAILURE, {"reason": reason})
    assert new_zadani(bus, "analyticky") == []


def test_web_warns_about_collectors_that_found_nothing_or_failed_in_the_last_run(
    bus, clock, make_agent, web
):
    ridici = make_agent(RidiciSNahrazkami)
    start_manual_run(bus)
    ridici.process_pending()
    answer(bus, "sberny-a", new_findings=0)
    zadani_for(bus, "sberny-b")  # mlčí
    ridici.process_pending()
    clock.advance(minutes=30)
    ridici.on_idle()

    page = web.get("/behy")

    assert page.status_code == 200
    warnings = warnings_on(page)
    assert "<strong>sberny-a</strong> vrátil 0 nálezů" in warnings
    assert "<strong>sberny-b</strong> selhal: bez odpovědi do 30 minut" in warnings


def test_web_warning_disappears_once_the_collector_succeeds_in_a_later_run(bus, make_agent, web):
    ridici = make_agent(RidiciSNahrazkami)
    start_manual_run(bus)
    ridici.process_pending()
    answer(bus, "sberny-a", new_findings=0)
    fail(bus, "sberny-b", "HTTP 429")
    ridici.process_pending()
    assert warnings_on(web.get("/behy")) != ""

    start_manual_run(bus)
    ridici.process_pending()
    answer(bus, "sberny-a", new_findings=2)
    answer(bus, "sberny-b", new_findings=1)
    ridici.process_pending()

    assert warnings_on(web.get("/behy")) == ""


def test_web_shows_run_in_progress_until_every_collector_answers(bus, make_agent, web):
    ridici = make_agent(RidiciSNahrazkami)
    start_manual_run(bus)
    ridici.process_pending()
    answer(bus, "sberny-a", new_findings=1)
    ridici.process_pending()

    in_progress = web.get("/behy").text
    assert "probíhá" in in_progress
    assert "čeká na odpověď" in in_progress

    answer(bus, "sberny-b", new_findings=5)
    ridici.process_pending()

    finished = web.get("/behy").text
    assert "probíhá" not in finished
    assert "dokončen" in finished
    assert "1 nový nález" in finished
    assert "5 nových nálezů" in finished


def test_web_says_so_when_no_run_has_happened_yet(web):
    assert "Zatím žádný běh" in web.get("/behy").text


def test_running_ridici_starts_the_daily_run_by_itself_and_it_finishes_when_collectors_answer(
    make_agent, start_agent, web
):
    start_agent(make_agent(SbernyA))
    start_agent(make_agent(SbernyB))
    start_agent(make_agent(RidiciSNahrazkami))  # pondělí 7:00 v Praze, termín 6:00 už nastal

    deadline = time.monotonic() + 5
    while "dokončen" not in (page := web.get("/behy").text) and time.monotonic() < deadline:
        time.sleep(0.05)

    assert "dokončen" in page
    assert "podle rozvrhu" in page
    assert page.count("2 nové nálezy") == 2
