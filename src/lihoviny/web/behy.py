"""Stránka Běhy: poslední běhy řídicího agenta a varování, když agent selhal nebo vrátil
prázdný snímek. Počet nových nálezů je jen informace: den bez Novinky je běžný."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import class_row

from lihoviny.board import BEH_COLUMNS, ZADANI_COLUMNS, Beh, Zadani
from lihoviny.bus import Kind
from lihoviny.web.common import czech_count, db, prague_time, templates
from lihoviny.web.trendy import collection_calendar

router = APIRouter()

KIND_LABELS = {"collection": "Sběr", "brief": "Brief"}


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
            f"SELECT {BEH_COLUMNS} FROM behy ORDER BY id DESC LIMIT 20"
        ).fetchall()
    with conn.cursor(row_factory=class_row(Zadani)) as cursor:
        zadani = cursor.execute(
            f"SELECT {ZADANI_COLUMNS} FROM zadani WHERE beh_id = ANY(%s) ORDER BY agent",
            [[beh.id for beh in behy]],
        ).fetchall()
        # Poslední běh každého agenta, ve kterém už je znám výsledek.
        latest = cursor.execute(
            f"""
            SELECT DISTINCT ON (agent) {ZADANI_COLUMNS} FROM zadani
            WHERE outcome IS NOT NULL
            ORDER BY agent, beh_id DESC
            """
        ).fetchall()
    views = [
        BehView(beh, [_zadani_view(item) for item in zadani if item.beh_id == beh.id])
        for beh in behy
    ]
    warnings = [item for item in latest if item.outcome == Kind.FAILURE or item.item_count == 0]
    return templates.TemplateResponse(
        request,
        "behy.html",
        {
            "behy": views,
            "warnings": warnings,
            "kind_labels": KIND_LABELS,
            "trend": collection_calendar(conn),
        },
    )


def _zadani_view(zadani: Zadani) -> ZadaniView:
    if zadani.outcome is None:
        deadline = prague_time(zadani.deadline)
        return ZadaniView(zadani, f"čeká na odpověď, nejdéle do {deadline}", "waiting")
    if zadani.outcome == Kind.FAILURE:
        return ZadaniView(zadani, f"Selhání: {zadani.reason or 'bez důvodu'}", "failure")
    if zadani.item_count == 0:
        return ZadaniView(zadani, "prázdný snímek (0 položek)", "warning")
    if zadani.new_findings is None:
        return ZadaniView(zadani, "odpověděl", "ok")
    result = czech_count(zadani.new_findings, "nový nález", "nové nálezy", "nových nálezů")
    if zadani.item_count is not None:
        items = czech_count(zadani.item_count, "položka", "položky", "položek")
        result += f", ve snímku {items}"
    return ZadaniView(zadani, result, "ok")
