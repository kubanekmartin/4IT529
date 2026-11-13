"""Stránka Regulace (KIQ 4): sněmovní tisky, které se podle regulatorního agenta týkají lihovin.

Zdroj se uvádí, jak to chtějí podmínky otevřených dat Sněmovny: zdroj dat a datum zpracování,
tedy datum posledního snímku regulatorního agenta.
"""

from datetime import date
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from lihoviny.web.common import czech_date, db, templates

router = APIRouter()


@router.get("/regulace")
def regulace_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=dict_row) as cursor:
        snapshot = cursor.execute(
            """
            SELECT taken_at, item_count FROM snapshots
            WHERE agent = 'regulatorni' ORDER BY taken_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        tisky = cursor.execute(
            """
            SELECT source_url, content, found_at FROM findings
            WHERE kiq = 4 AND type = 'snemovni_tisk'
            ORDER BY found_at DESC, content->>'submitted_on' DESC NULLS LAST, id DESC
            """
        ).fetchall()
    return templates.TemplateResponse(
        request, "regulace.html", {"snapshot": snapshot, "tisky": tisky}
    )


def iso_to_czech(value: str | None) -> str:
    """„2027-01-01“ → „1. 1. 2027“; bez data „—“."""
    return czech_date(date.fromisoformat(value)) if value else "—"


templates.env.filters["iso_datum"] = iso_to_czech
