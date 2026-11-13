"""Řídicí agent: podle rozvrhu nebo na ruční pokyn zahájí běh a hlídá odpovědi.

Dotaz analytického agenta převede na Zadání z Dotazu pro sběrného agenta dotčené KIQ (LLM jen
převádí text Dotazu) a jeho výsledek pošle zpět jako odpověď na Dotaz. Celá výměna zůstává
v konverzaci Zadání Briefu, takže ji log komunikace ukáže pohromadě.
"""

import logging
import uuid
from datetime import datetime, time, timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from lihoviny.agent import Agent
from lihoviny.board import Beh, Zadani
from lihoviny.bus import Kind, Message
from lihoviny.clock import PRAGUE

log = logging.getLogger(__name__)

COLLECTION_AT = time(6, 0)  # denní sběr, pražský čas
BRIEF_AT = time(7, 0)  # Brief v pondělí, pražský čas
MONDAY = 0
ANSWER_LIMIT = timedelta(minutes=30)
# Časový rozpočet Briefu: 30 minut na Zadání Briefu platí i s kolem Dotazů. Zadání z Dotazu
# proto musí odpovědět FINISH_TIME před termínem Zadání, ze kterého Dotaz vzešel (u Briefu
# do 15 minut od Zadání), a analytickému agentovi zbude 15 minut na dopsání Briefu: jedno
# volání LLM trvá nejvýš 12 minut (Analyticky.llm_timeout × 3 pokusy).
FINISH_TIME = timedelta(minutes=15)
MAX_DOTAZY = 3  # na jeden běh, v jednom kole

_DOTAZ_SYSTEM = """\
Jsi řídicí agent systému konkurenčního zpravodajství pro českého výrobce lihovin. Analytický
agent ti poslal Dotaz: k Briefu mu chybí data jedné KIQ. Převeď ho na Zadání pro sběrného
agenta této KIQ: jednou nebo dvěma větami česky řekni, co a kde má zjistit. Drž se Dotazu,
nic nepřidávej a neuváděj jména osob."""


class BehError(Exception):
    pass


class DotazError(Exception):
    """Z Dotazu nemůže vzniknout Zadání; důvod dostane tazatel jako Selhání."""


class ZadaniZDotazu(BaseModel):
    instruction: str = Field(
        description="Zadání pro sběrného agenta česky, jedna až dvě věty: co a kde má zjistit."
    )


class Ridici(Agent):
    """Pošle Zadání sběrným agentům a za ty, kdo do 30 minut neodpoví, zapíše Selhání.

    Ruční běh je Zadání pro řídicího agenta: `{}` nebo `{"beh": "collection"}` zahájí sběr.
    """

    name = "ridici"
    role = "plánování: rozvrh běhů, Zadání sběrným agentům, Selhání po 30 minutách, Dotazy"

    # Sběrní agenti, kterým běh sběru pošle Zadání. Nový sběrný agent = jeho jméno sem
    # (a třída v AGENTS). Ozvěna sem nepatří, není sběrná.
    sberni_agenti: ClassVar[tuple[str, ...]] = ("sortimentni", "cenovy", "medialni", "regulatorni")
    # Analytický agent, kterému běh Briefu pošle Zadání; bez něj se Brief nezadává.
    analyst: ClassVar[str | None] = "analyticky"
    # Kdo sbírá data pro kterou KIQ (jeden sběrný agent na KIQ): jemu jde Zadání z Dotazu.
    sberni_agenti_podle_kiq: ClassVar[dict[int, str]] = {
        1: "sortimentni",
        2: "cenovy",
        3: "medialni",
        4: "regulatorni",
    }

    def handle_request(self, request: Message) -> dict[str, Any]:
        match request.content.get("beh", "collection"):
            case "collection":
                beh = self._start_collection()
                sent_to = list(self.sberni_agenti)
            case "brief":
                if not self.analyst:
                    raise BehError("Brief nemá komu zadat: řídicí agent nemá analytického agenta")
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
            if zadani.item_count is not None:
                result["item_count"] = zadani.item_count
            if zadani.reason is not None:
                result["reason"] = zadani.reason
            results[zadani.agent] = result
        return {"beh_id": beh.id, "results": results}

    def _send_zadani(self, beh: Beh, agent: str, content: dict[str, Any]) -> None:
        conversation_id = uuid.uuid4()
        self.board.add_zadani(
            beh_id=beh.id,
            agent=agent,
            conversation_id=conversation_id,
            deadline=self.clock.now() + ANSWER_LIMIT,
        )
        self.bus.send(
            kind=Kind.REQUEST,
            sender=self.name,
            recipient=agent,
            content=content,
            conversation_id=conversation_id,
        )

    def handle_message(self, message: Message) -> None:
        match message.kind:
            case Kind.QUERY:
                self._take_dotaz(message)
            case Kind.INFORM | Kind.FAILURE:
                self._record_answer(message)
            case _:
                super().handle_message(message)

    def _record_answer(self, message: Message) -> None:
        # Odpověď je vstup od jiného agenta: co neodpovídá kontraktu, se nezapíše.
        reason = message.content.get("reason")
        with self.transaction():
            zadani = self.board.record_outcome(
                message.conversation_id,
                message.sender,
                message.kind,
                new_findings=_count(message.content.get("new_findings")),
                item_count=_count(message.content.get("item_count")),
                reason=None if reason is None else str(reason),
            )
            if zadani is not None and zadani.dotaz_id is not None:
                self._answer_dotaz(zadani)
        if zadani is None:
            log.info(
                "%s: odpověď %s od %s mimo čekající Zadání", self.name, message.id, message.sender
            )

    def _take_dotaz(self, dotaz: Message) -> None:
        """Dotaz → Zadání z Dotazu. Na Dotaz odpoví, až odpoví sběrný agent (nebo mu vyprší
        termín). Dotaz, ze kterého Zadání vzniknout nemůže, hned odmítne Selháním s důvodem."""
        try:
            self._send_zadani_from_dotaz(dotaz)
            return
        except DotazError as error:
            log.info("%s: Dotaz %s odmítnut: %s", self.name, dotaz.id, error)
            reason = str(error)
        except Exception as error:
            log.exception("%s: Dotaz %s se nepodařilo převést na Zadání", self.name, dotaz.id)
            reason = f"Dotaz se nepodařilo převést na Zadání: {error or type(error).__name__}"
        self.bus.reply(dotaz, Kind.FAILURE, {"dotaz_id": dotaz.id, "reason": reason})

    def _send_zadani_from_dotaz(self, dotaz: Message) -> None:
        kiq = dotaz.content.get("kiq")
        agent = self.sberni_agenti_podle_kiq.get(kiq) if type(kiq) is int else None
        if agent is None:
            raise DotazError(f"Dotaz bez platné KIQ ({kiq!r}), řídicí agent zná KIQ 1–4")
        # Dotaz patří k Zadání, na které tazatel v téže konverzaci ještě neodpověděl.
        in_conversation = self.board.conversation_zadani(dotaz.conversation_id)
        asking = next(
            (
                z
                for z in in_conversation
                if z.agent == dotaz.sender and z.dotaz_id is None and z.outcome is None
            ),
            None,
        )
        if asking is None:
            raise DotazError("Dotaz nepatří k žádnému Zadání, na které by tazatel odpovídal")
        earlier = [z for z in in_conversation if z.dotaz_id is not None]
        if any(z.outcome is not None for z in earlier):
            raise DotazError("jen jedno kolo Dotazů na běh: na předchozí Dotazy už přišla odpověď")
        if len(earlier) >= MAX_DOTAZY:
            raise DotazError(f"nejvýš {MAX_DOTAZY} Dotazy na běh")
        if any(z.agent == agent for z in self.board.zadani(asking.beh_id)):
            raise DotazError(f"KIQ {kiq}: {agent} už v tomto běhu Zadání dostal")
        deadline = asking.deadline - FINISH_TIME
        if deadline <= self.clock.now():
            raise DotazError("na doplňující sběr už nezbývá čas, Brief se musí dopsat")
        text = self.llm.ask(
            _dotaz_prompt(dotaz, agent), ZadaniZDotazu, effort="low", system=_DOTAZ_SYSTEM
        )
        if not text.instruction.strip():
            raise DotazError("LLM z Dotazu nenapsal žádné Zadání")
        with self.transaction():
            self.board.add_zadani(
                beh_id=asking.beh_id,
                agent=agent,
                conversation_id=dotaz.conversation_id,
                deadline=deadline,
                dotaz_id=dotaz.id,
            )
            self.bus.send(
                kind=Kind.REQUEST,
                sender=self.name,
                recipient=agent,
                content={
                    "beh_id": asking.beh_id,
                    "kiq": kiq,
                    "instruction": text.instruction.strip(),
                    "dotaz_id": dotaz.id,
                },
                conversation_id=dotaz.conversation_id,
            )

    def _answer_dotaz(self, zadani: Zadani) -> None:
        """Výsledek Zadání z Dotazu pošle tazateli jako odpověď na jeho Dotaz."""
        conversation = self.bus.conversation(zadani.conversation_id)
        dotaz = next((m for m in conversation if m.id == zadani.dotaz_id), None)
        if dotaz is None:
            # Výsledek Zadání se zapíše i tak; bez Dotazu není komu odpovědět.
            log.warning(
                "%s: Dotaz %s není v konverzaci %s, odpověď nemá komu poslat",
                self.name,
                zadani.dotaz_id,
                zadani.conversation_id,
            )
            return
        content: dict[str, Any] = {"dotaz_id": dotaz.id, "agent": zadani.agent}
        if zadani.outcome == Kind.INFORM:
            if zadani.new_findings is not None:
                content["new_findings"] = zadani.new_findings
            if zadani.item_count is not None:
                content["item_count"] = zadani.item_count
        else:
            content["reason"] = zadani.reason or "bez důvodu"
        self.bus.reply(dotaz, Kind(zadani.outcome), content)

    def on_idle(self) -> None:
        # Nejdřív Selhání po termínu: uzavřený sběr pak pustí Brief ještě v témže kole.
        self._fail_overdue()
        self._check_schedule()

    def _check_schedule(self) -> None:
        """Termín z rozvrhu, který dnes už nastal a ještě neproběhl, spustí (i se zpožděním).

        Zmeškaný termín se dožene jen týž den, aby restart nespustil včerejší běh. Brief
        počká, až je poslední sběr uzavřený (všechny odpovědi nebo Selhání po termínu): po
        zmeškaném pondělním ránu by jinak vyšel současně se sběrem a bez jeho dat.
        """
        now = self.clock.now().astimezone(PRAGUE)
        collection = datetime.combine(now.date(), COLLECTION_AT, tzinfo=PRAGUE)
        if now >= collection:
            self._start_collection(scheduled_for=collection)
        brief = datetime.combine(now.date(), BRIEF_AT, tzinfo=PRAGUE)
        if self.analyst and now.weekday() == MONDAY and now >= brief and self._collection_closed():
            self._start_brief(scheduled_for=brief)

    def _collection_closed(self) -> bool:
        beh = self.board.latest_beh("collection")
        return beh is None or all(z.outcome is not None for z in self.board.zadani(beh.id))

    def _fail_overdue(self) -> None:
        """Za Zadání po termínu zapíše Selhání a pošle ho agentovi; u Zadání z Dotazu
        dostane Selhání jako odpověď na Dotaz i tazatel."""
        for zadani in self.board.overdue_zadani(self.clock.now()):
            if zadani.dotaz_id is None:
                reason = f"bez odpovědi do {_minutes(ANSWER_LIMIT)} minut"
            else:
                within = _minutes(ANSWER_LIMIT - FINISH_TIME)
                reason = f"bez odpovědi do {within} minut od Zadání Briefu"
            with self.transaction():
                closed = self.board.record_outcome(
                    zadani.conversation_id, zadani.agent, Kind.FAILURE, reason=reason
                )
                if closed is None:
                    continue
                self.bus.send(
                    kind=Kind.FAILURE,
                    sender=self.name,
                    recipient=zadani.agent,
                    content={"reason": reason},
                    conversation_id=zadani.conversation_id,
                )
                if closed.dotaz_id is not None:
                    self._answer_dotaz(closed)


def _dotaz_prompt(dotaz: Message, agent: str) -> str:
    """Zadání pro LLM z Dotazu; obsah Dotazu je vstup od jiného agenta, proto jen jako text."""
    return "\n".join(
        [
            f"Sběrný agent: {agent} (KIQ {dotaz.content['kiq']})",
            f"Co chybí: {dotaz.content.get('missing')}",
            f"Návrh doplňujícího sběru: {dotaz.content.get('proposal')}",
        ]
    )


def _minutes(limit: timedelta) -> int:
    return int(limit.total_seconds() // 60)


def _count(value: Any) -> int | None:
    """Počet z odpovědi jiného agenta, jen když je to opravdu celé číslo.

    `type(...) is int` schválně: `True` je v Pythonu také int, ale počet to není.
    """
    return value if type(value) is int else None
