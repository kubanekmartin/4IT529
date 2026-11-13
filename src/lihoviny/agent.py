"""Společný základ agenta.

Agent má jméno (adresa na sběrnici) a roli. Zadání (REQUEST) obslouží `handle_request`:
vrácený slovník odejde odesílateli jako INFORM, vyhozená výjimka jako FAILURE s důvodem.
Ostatní druhy zpráv dostane `handle_message`, kterou si agent přepíše podle své role.
Práci podle času (rozvrh, limity) dělá `on_idle`, kterou smyčka volá v každém kole.
"""

import logging
import threading
import time
from typing import Any, ClassVar, TypedDict

from lihoviny.board import Board
from lihoviny.bus import Bus, Kind, Message
from lihoviny.clock import Clock
from lihoviny.fetch import Fetcher
from lihoviny.llm import Llm

log = logging.getLogger(__name__)


class CollectorResult(TypedDict):
    """Obsah INFORM od sběrného agenta: jen souhrn, samotné nálezy jsou na tabuli."""

    snapshot_id: int
    new_findings: int


class Agent:
    name: ClassVar[str]
    role: ClassVar[str]
    # Pauza mezi požadavky na zdroj v sekundách: alespoň crawl-delay z robots.txt zdroje.
    fetch_delay: ClassVar[float] = 1.0

    def __init__(self, *, bus: Bus, board: Board, fetcher: Fetcher, llm: Llm, clock: Clock):
        self.bus = bus
        self.board = board
        self.fetcher = fetcher
        self.llm = llm
        self.clock = clock

    def handle_request(self, request: Message) -> dict[str, Any]:
        raise NotImplementedError

    def handle_message(self, message: Message) -> None:
        log.info("%s: zpráva %s od %s nemá obsluhu", self.name, message.kind, message.sender)

    def on_idle(self) -> None:
        """Volá se v každém kole smyčky, nejméně jednou za `poll_interval`; čas z `self.clock`."""

    def process_pending(self) -> int:
        """Vyzvedne a obslouží všechny čekající zprávy. Vrátí jejich počet."""
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
        if message.kind is not Kind.REQUEST:
            try:
                self.handle_message(message)
            except Exception:
                log.exception("%s: obsluha zprávy %s selhala", self.name, message.id)
            return
        try:
            result = self.handle_request(message)
        except Exception as error:
            log.exception("%s: Zadání %s selhalo", self.name, message.id)
            self.bus.reply(message, Kind.FAILURE, {"reason": str(error) or type(error).__name__})
        else:
            self.bus.reply(message, Kind.INFORM, result)
