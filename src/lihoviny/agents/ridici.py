"""Řídicí agent: podle rozvrhu nebo na ruční pokyn zahájí běh a hlídá odpovědi."""

import logging
import uuid
from datetime import datetime, time, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from lihoviny.agent import Agent
from lihoviny.board import Run
from lihoviny.bus import Kind, Message

log = logging.getLogger(__name__)

PRAGUE = ZoneInfo("Europe/Prague")
COLLECTION_AT = time(6, 0)  # denní sběr, pražský čas
BRIEF_AT = time(7, 0)  # Brief v pondělí, pražský čas
MONDAY = 0
ANSWER_LIMIT = timedelta(minutes=30)


class RunError(Exception):
    pass


class Ridici(Agent):
    """Pošle Zadání sběrným agentům a za ty, kdo do 30 minut neodpoví, zapíše Selhání.

    Ruční běh je Zadání pro řídicího agenta: `{}` nebo `{"run": "collection"}` zahájí sběr.
    """

    name = "ridici"
    role = "plánování: rozvrh běhů, Zadání sběrným agentům, Selhání po 30 minutách"

    # Sběrní agenti, kterým běh sběru pošle Zadání. Nový sběrný agent = jeho jméno sem
    # (a třída v AGENTS). Ozvěna sem nepatří, není sběrná.
    collectors: ClassVar[tuple[str, ...]] = ("sortimentni", "cenovy")
    # Analytický agent, kterému běh Briefu pošle Zadání; dokud není, Brief se nezadává.
    analyst: ClassVar[str | None] = None

    def handle_request(self, request: Message) -> dict[str, Any]:
        match request.content.get("run", "collection"):
            case "collection":
                run = self._start_collection()
                sent_to = list(self.collectors)
            case "brief":
                if not self.analyst:
                    raise RunError("Brief nemá komu zadat: analytický agent zatím neexistuje")
                run = self._start_brief()
                sent_to = [self.analyst]
            case unknown:
                raise RunError(f"neznámý běh {unknown!r}, znám collection a brief")
        return {"run_id": run.id, "run": run.kind, "sent_to": sent_to}

    def _start_collection(self, scheduled_for: datetime | None = None) -> Run | None:
        run = self.board.start_run("collection", scheduled_for=scheduled_for)
        if run is not None:
            for agent in self.collectors:
                self._send_zadani(run, agent, {"run_id": run.id})
        return run

    def _start_brief(self, scheduled_for: datetime | None = None) -> Run | None:
        run = self.board.start_run("brief", scheduled_for=scheduled_for)
        if run is not None:
            content = {"run_id": run.id, "collection": self._last_collection()}
            self._send_zadani(run, self.analyst, content)
        return run

    def _last_collection(self) -> dict[str, Any] | None:
        """Výsledky posledního sběru pro Brief, aby mohl Selhání výslovně uvést."""
        run = self.board.latest_run("collection")
        if run is None:
            return None
        results = {}
        for task in self.board.run_tasks(run.id):
            result = {"outcome": task.outcome}
            if task.new_findings is not None:
                result["new_findings"] = task.new_findings
            if task.reason is not None:
                result["reason"] = task.reason
            results[task.agent] = result
        return {"run_id": run.id, "results": results}

    def _send_zadani(self, run: Run, agent: str, content: dict[str, Any]) -> None:
        # Nejdřív záznam v běhu, pak zpráva: když agent mezi tím spadne, limit 30 minut
        # běh stejně ukončí (Selhání), místo aby Zadání odešlo a nikdo na odpověď nečekal.
        conversation_id = uuid.uuid4()
        self.board.add_run_task(run_id=run.id, agent=agent, conversation_id=conversation_id)
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
        task = self.board.record_outcome(
            message.conversation_id,
            message.kind,
            new_findings=new_findings if type(new_findings) is int else None,
            reason=None if reason is None else str(reason),
        )
        if task is None:
            log.info(
                "%s: odpověď %s od %s mimo čekající Zadání", self.name, message.id, message.sender
            )

    def on_idle(self) -> None:
        self._run_schedule()
        self._fail_overdue()

    def _run_schedule(self) -> None:
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
        for task in self.board.waiting_tasks(sent_before=self.clock.now() - ANSWER_LIMIT):
            reason = "bez odpovědi do 30 minut"
            if self.board.record_outcome(task.conversation_id, "FAILURE", reason=reason):
                self.bus.send(
                    kind=Kind.FAILURE,
                    sender=self.name,
                    recipient=task.agent,
                    content={"reason": reason},
                    conversation_id=task.conversation_id,
                )
