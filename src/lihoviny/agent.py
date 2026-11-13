"""Společný základ agenta.

Agent má jméno (adresa na sběrnici) a roli. Zadání (REQUEST) obslouží `handle_request`:
vrácený slovník odejde odesílateli jako INFORM, vyhozená výjimka jako FAILURE s důvodem.
Agent, který před odpovědí potřebuje ještě něco zjistit od jiného agenta (analytický agent
čeká na odpovědi na své Dotazy), vrátí `ANSWER_LATER` a na Zadání odpoví později přes
`answer()`, až zprávy, na které čekal, dostane v `handle_message`.
Ostatní druhy zpráv dostane `handle_message`, kterou si agent přepíše podle své role.
Práci podle času (rozvrh, limity) dělá `on_idle`, kterou smyčka volá v každém kole.
"""

import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, ClassVar, TypedDict

from lihoviny.board import Board
from lihoviny.bus import Bus, Kind, Message
from lihoviny.clock import Clock
from lihoviny.db import transaction
from lihoviny.fetch import Fetcher
from lihoviny.llm import MAX_TOKENS, RETRIES, TIMEOUT, Llm

log = logging.getLogger(__name__)


class OdpovedSberu(TypedDict):
    """Obsah INFORM od sběrného agenta: jen přehled, samotné nálezy jsou na tabuli.

    `item_count` je počet položek ve snímku; prázdný snímek web hlásí jako varování.
    """

    snapshot_id: int
    new_findings: int
    item_count: int


class AnswerLater:
    """Výsledek `handle_request`: odpověď na Zadání pošle agent později přes `answer()`."""


ANSWER_LATER = AnswerLater()


class Agent:
    name: ClassVar[str]
    role: ClassVar[str]
    # Pauza mezi požadavky na zdroj v sekundách: alespoň crawl-delay z robots.txt zdroje.
    fetch_delay: ClassVar[float] = 1.0
    # Limity jednoho volání LLM (sekundy na pokus, opakování, tokeny odpovědi); psaní Briefu
    # potřebuje víc, převod Dotazu méně (`lihoviny.planning`).
    llm_timeout: ClassVar[float] = TIMEOUT
    llm_retries: ClassVar[int] = RETRIES
    llm_max_tokens: ClassVar[int] = MAX_TOKENS

    def __init__(self, *, bus: Bus, board: Board, fetcher: Fetcher, llm: Llm, clock: Clock):
        self.bus = bus
        self.board = board
        self.fetcher = fetcher
        self.llm = llm
        self.clock = clock

    def handle_request(self, request: Message) -> dict[str, Any] | AnswerLater:
        raise NotImplementedError

    def answer(self, request: Message, work: Callable[[], dict[str, Any] | AnswerLater]) -> None:
        """Odpoví na Zadání podle výsledku `work()`: slovník odejde jako INFORM, výjimka jako
        FAILURE s důvodem, `ANSWER_LATER` zatím nic (agent pak zavolá `answer` znovu)."""
        try:
            result = work()
        except Exception as error:
            log.exception("%s: Zadání %s selhalo", self.name, request.id)
            self.bus.reply(request, Kind.FAILURE, {"reason": str(error) or type(error).__name__})
            return
        if not isinstance(result, AnswerLater):
            self.bus.reply(request, Kind.INFORM, result)

    def handle_message(self, message: Message) -> None:
        if message.kind is Kind.FAILURE:
            # Řídicí agent uzavřel Zadání, na které agent do termínu neodpověděl (30 minut,
            # Zadání z Dotazu dřív). Na Selhání se neodpovídá; agent ho jen vezme na vědomí.
            log.info(
                "%s: %s uzavřel Zadání v konverzaci %s jako Selhání (%s)",
                self.name,
                message.sender,
                message.conversation_id,
                message.content.get("reason", "bez důvodu"),
            )
            return
        log.info("%s: zpráva %s od %s nemá obsluhu", self.name, message.kind, message.sender)

    def on_idle(self) -> None:
        """Volá se v každém kole smyčky, nejméně jednou za `poll_interval`; čas z `self.clock`."""

    def transaction(self) -> AbstractContextManager[None]:
        """Zápisy na tabuli i zprávy na sběrnici uvnitř bloku se uloží všechny, nebo žádný."""
        return transaction(self.board.database_url)

    def process_pending(self) -> int:
        handled = 0
        while (message := self.bus.claim(self.name)) is not None:
            self._handle(message)
            handled += 1
        return handled

    def run(self, *, poll_interval: float = 30.0, stop: threading.Event | None = None) -> None:
        """Smyčka služby: probouzí ji NOTIFY, pojistkou je kontrola každých `poll_interval` s."""
        stop = stop or threading.Event()
        log.info("%s: běží (%s)", self.name, self.role)
        with self.bus.listen(self.name) as inbox:
            while not stop.is_set():
                self.process_pending()
                self._idle()
                deadline = time.monotonic() + poll_interval
                # Čeká po krátkých úsecích, aby šlo smyčku zastavit bez čekání na celý interval.
                while not stop.is_set() and (remaining := deadline - time.monotonic()) > 0:
                    if inbox.wait(min(remaining, 0.5)):
                        break

    def _idle(self) -> None:
        try:
            self.on_idle()
        except Exception:
            # Chyba v jednom kole (např. výpadek databáze) nesmí zastavit službu.
            log.exception("%s: práce podle času selhala", self.name)

    def _handle(self, message: Message) -> None:
        if message.kind is Kind.REQUEST:
            self.answer(message, lambda: self.handle_request(message))
            return
        try:
            self.handle_message(message)
        except Exception:
            log.exception("%s: obsluha zprávy %s selhala", self.name, message.id)
