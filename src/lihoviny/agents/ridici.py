"""Řídicí agent: podle rozvrhu nebo na ruční pokyn zahájí běh a hlídá odpovědi."""

import logging
import uuid
from datetime import datetime, time, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from lihoviny.agent import Agent
from lihoviny.board import Beh
from lihoviny.bus import Kind, Message

log = logging.getLogger(__name__)

PRAGUE = ZoneInfo("Europe/Prague")
COLLECTION_AT = time(6, 0)  # denní sběr, pražský čas
BRIEF_AT = time(7, 0)  # Brief v pondělí, pražský čas
MONDAY = 0
ANSWER_LIMIT = timedelta(minutes=30)


class BehError(Exception):
    pass


class Ridici(Agent):
    """Pošle Zadání sběrným agentům a za ty, kdo do 30 minut neodpoví, zapíše Selhání.

    Ruční běh je Zadání pro řídicího agenta: `{}` nebo `{"beh": "collection"}` zahájí sběr.
    """

    name = "ridici"
    role = "plánování: rozvrh běhů, Zadání sběrným agentům, Selhání po 30 minutách"

    # Sběrní agenti, kterým běh sběru pošle Zadání. Nový sběrný agent = jeho jméno sem
    # (a třída v AGENTS). Ozvěna sem nepatří, není sběrná.
    sberni_agenti: ClassVar[tuple[str, ...]] = ("sortimentni", "cenovy")
    # Analytický agent, kterému běh Briefu pošle Zadání; dokud není, Brief se nezadává.
    analyst: ClassVar[str | None] = None

    def handle_request(self, request: Message) -> dict[str, Any]:
        match request.content.get("beh", "collection"):
            case "collection":
                beh = self._start_collection()
                sent_to = list(self.sberni_agenti)
            case "brief":
                if not self.analyst:
                    raise BehError("Brief nemá komu zadat: analytický agent zatím neexistuje")
                beh = self._start_brief()
                sent_to = [self.analyst]
            case unknown:
                raise BehError(f"neznámý běh {unknown!r}, znám collection a brief")
        return {"beh_id": beh.id, "beh": beh.kind, "sent_to": sent_to}

    # Běh, jeho Zadání a zprávy jsou jedna transakce: kdyby řídicí agent spadl uprostřed,
    # termín rozvrhu by byl spotřebovaný a část agentů by Zadání nikdy nedostala.
    def _start_collection(self, scheduled_for: datetime | None = None) -> Beh | None:
        with self.transaction():
            beh = self.board.start_beh("collection", scheduled_for=scheduled_for)
            if beh is not None:
                for agent in self.sberni_agenti:
                    self._send_zadani(beh, agent, {"beh_id": beh.id})
        return beh

    def _start_brief(self, scheduled_for: datetime | None = None) -> Beh | None:
        with self.transaction():
            beh = self.board.start_beh("brief", scheduled_for=scheduled_for)
            if beh is not None:
                content = {"beh_id": beh.id, "collection": self._last_collection()}
                self._send_zadani(beh, self.analyst, content)
        return beh

    def _last_collection(self) -> dict[str, Any] | None:
        """Výsledky posledního sběru pro Brief, aby mohl Selhání výslovně uvést."""
        beh = self.board.latest_beh("collection")
        if beh is None:
            return None
        results = {}
        for zadani in self.board.zadani(beh.id):
            result = {"outcome": zadani.outcome}
            if zadani.new_findings is not None:
                result["new_findings"] = zadani.new_findings
            if zadani.reason is not None:
                result["reason"] = zadani.reason
            results[zadani.agent] = result
        return {"beh_id": beh.id, "results": results}

    def _send_zadani(self, beh: Beh, agent: str, content: dict[str, Any]) -> None:
        conversation_id = uuid.uuid4()
        self.board.add_zadani(beh_id=beh.id, agent=agent, conversation_id=conversation_id)
        self.bus.send(
            kind=Kind.REQUEST,
            sender=self.name,
            recipient=agent,
            content=content,
            conversation_id=conversation_id,
        )

    def handle_message(self, message: Message) -> None:
        if message.kind not in (Kind.INFORM, Kind.FAILURE):
            super().handle_message(message)
            return
        # Odpověď je vstup od jiného agenta: co neodpovídá kontraktu, se nezapíše.
        new_findings = message.content.get("new_findings")
        reason = message.content.get("reason")
        zadani = self.board.record_outcome(
            message.conversation_id,
            message.kind,
            new_findings=new_findings if type(new_findings) is int else None,
            reason=None if reason is None else str(reason),
        )
        if zadani is None:
            log.info(
                "%s: odpověď %s od %s mimo čekající Zadání", self.name, message.id, message.sender
            )

    def on_idle(self) -> None:
        self._check_schedule()
        self._fail_overdue()

    def _check_schedule(self) -> None:
        """Termín z rozvrhu, který dnes už nastal a ještě neproběhl, spustí (i se zpožděním).

        Zmeškaný termín se dožene jen týž den, aby restart nespustil včerejší běh.
        """
        now = self.clock.now().astimezone(PRAGUE)
        collection = datetime.combine(now.date(), COLLECTION_AT, tzinfo=PRAGUE)
        if now >= collection:
            self._start_collection(scheduled_for=collection)
        brief = datetime.combine(now.date(), BRIEF_AT, tzinfo=PRAGUE)
        if self.analyst and now.weekday() == MONDAY and now >= brief:
            self._start_brief(scheduled_for=brief)

    def _fail_overdue(self) -> None:
        for zadani in self.board.waiting_zadani(sent_before=self.clock.now() - ANSWER_LIMIT):
            reason = "bez odpovědi do 30 minut"
            if self.board.record_outcome(zadani.conversation_id, "FAILURE", reason=reason):
                self.bus.send(
                    kind=Kind.FAILURE,
                    sender=self.name,
                    recipient=zadani.agent,
                    content={"reason": reason},
                    conversation_id=zadani.conversation_id,
                )
