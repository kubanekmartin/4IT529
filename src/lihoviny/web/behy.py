"""Stránka Běhy: poslední běhy řídicího agenta a varování, když agent selhal nebo nic nenašel."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import class_row

from lihoviny.agents.ridici import ANSWER_LIMIT
from lihoviny.board import Beh, Zadani
from lihoviny.web.common import db, prague_time, templates

router = APIRouter()

KIND_LABELS = {"collection": "Sběr", "brief": "Brief"}
_ZADANI_COLUMNS = (
    "beh_id, agent, conversation_id, sent_at, outcome, new_findings, reason, answered_at"
)


@dataclass(frozen=True)
class ZadaniView:
    zadani: Zadani
    result: str
    tone: str  # třída CSS: ok, warning, failure, waiting


@dataclass(frozen=True)
class BehView:
    beh: Beh
    zadani: list[ZadaniView]

    @property
    def finished_at(self) -> datetime | None:
        """Čas poslední odpovědi; None, dokud některý agent ještě neodpověděl."""
        if any(view.zadani.outcome is None for view in self.zadani):
            return None
        return max((view.zadani.answered_at for view in self.zadani), default=self.beh.started_at)


@router.get("/behy")
def behy_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=class_row(Beh)) as cursor:
        behy = cursor.execute(
            "SELECT id, kind, scheduled_for, started_at FROM behy ORDER BY id DESC LIMIT 20"
        ).fetchall()
    with conn.cursor(row_factory=class_row(Zadani)) as cursor:
        zadani = cursor.execute(
            f"SELECT {_ZADANI_COLUMNS} FROM zadani WHERE beh_id = ANY(%s) ORDER BY agent",
            [[beh.id for beh in behy]],
        ).fetchall()
        # Poslední běh každého agenta, ve kterém už je znám výsledek.
        latest = cursor.execute(
            f"""
            SELECT DISTINCT ON (agent) {_ZADANI_COLUMNS} FROM zadani
            WHERE outcome IS NOT NULL
            ORDER BY agent, beh_id DESC
            """
        ).fetchall()
    views = [
        BehView(beh, [_zadani_view(item) for item in zadani if item.beh_id == beh.id])
        for beh in behy
    ]
    warnings = [item for item in latest if item.outcome == "FAILURE" or item.new_findings == 0]
    return templates.TemplateResponse(
        request,
        "behy.html",
        {"behy": views, "warnings": warnings, "kind_labels": KIND_LABELS},
    )


def _zadani_view(zadani: Zadani) -> ZadaniView:
    if zadani.outcome is None:
        deadline = prague_time(zadani.sent_at + ANSWER_LIMIT)
        return ZadaniView(zadani, f"čeká na odpověď, nejdéle do {deadline}", "waiting")
    if zadani.outcome == "FAILURE":
        return ZadaniView(zadani, f"Selhání: {zadani.reason or 'bez důvodu'}", "failure")
    if zadani.new_findings is None:
        return ZadaniView(zadani, "odpověděl", "ok")
    if zadani.new_findings == 0:
        return ZadaniView(zadani, "0 nálezů", "warning")
    return ZadaniView(zadani, _new_findings(zadani.new_findings), "ok")


def _new_findings(count: int) -> str:
    if count == 1:
        return "1 nový nález"
    if 2 <= count <= 4:
        return f"{count} nové nálezy"
    return f"{count} nových nálezů"
