"""Vzor sběrného agenta: Zadání → stažení → snímek a nálezy na tabuli → INFORM se souhrnem.

Skuteční sběrní agenti (cenový, sortimentní, mediální, regulatorní) se testují stejně:
náhražka stahování vrací uložené ukázky z `tests/fixtures/<zdroj>/` a test kontroluje
odpověď na sběrnici a nálezy na tabuli.
"""

import re
from pathlib import Path

from lihoviny.agent import Agent, OdpovedSberu
from lihoviny.bus import Kind
from lihoviny.fetch import FakeFetcher, FetchError, read_raw

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://zdroj.example/akce"


class UkazkovySberny(Agent):
    name = "ukazkovy"
    role = "vzor sběrného agenta pro testy"

    def handle_request(self, request):
        page = self.fetcher.get(URL)
        items = re.findall(r'<li data-cena="(\d+)">(.*?)</li>', page.text)
        snapshot = self.board.record_snapshot(
            agent=self.name, source="zdroj.example", fetches=[page], item_count=len(items)
        )
        for price, product in items:
            self.board.record_finding(
                kiq=2,
                agent=self.name,
                type="akcni_cena",
                snapshot_id=snapshot.id,
                source_url=page.url,
                content={"produkt": product, "cena": int(price)},
            )
        return OdpovedSberu(snapshot_id=snapshot.id, new_findings=len(items))


def send_zadani(bus):
    return bus.send(kind=Kind.REQUEST, sender="ridici", recipient="ukazkovy", content={})


def test_sberny_agent_answers_with_summary_and_puts_findings_on_board(
    bus, board, clock, raw_dir, make_agent
):
    fixture = FIXTURES / "ukazka" / "akce.html"
    agent = make_agent(
        UkazkovySberny, fetcher=FakeFetcher({URL: fixture}, raw_dir=raw_dir, clock=clock)
    )
    zadani = send_zadani(bus)

    agent.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    snapshot = board.snapshot(reply.content["snapshot_id"])
    assert reply.content == {"snapshot_id": snapshot.id, "new_findings": 2}
    assert (snapshot.agent, snapshot.source, snapshot.item_count) == (
        "ukazkovy",
        "zdroj.example",
        2,
    )
    assert read_raw(snapshot.raw_paths[0])[1] == fixture.read_bytes()
    findings = board.findings(snapshot_id=snapshot.id)
    assert [(f.kiq, f.type, f.content, f.found_at) for f in findings] == [
        (2, "akcni_cena", {"produkt": "Božkov Original 0,5 l", "cena": 199}, clock.now()),
        (2, "akcni_cena", {"produkt": "Becherovka 0,7 l", "cena": 349}, clock.now()),
    ]


def test_source_failure_becomes_failure_and_leaves_board_empty(
    bus, board, clock, raw_dir, make_agent
):
    error = FetchError(f"{URL}: 3 pokusy selhaly, naposledy HTTP 429")
    agent = make_agent(
        UkazkovySberny, fetcher=FakeFetcher({URL: error}, raw_dir=raw_dir, clock=clock)
    )
    zadani = send_zadani(bus)

    agent.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert (reply.kind, reply.content) == (Kind.FAILURE, {"reason": str(error)})
    assert board.findings(kiq=2) == []
