"""Stránka Akční ceny (KIQ 2): akční ceny lihovin a RTD platné k času posledního snímku.

„Platné“ se počítá k datu posledního snímku cenového agenta, ne k dnešku: když sběr
vypadne, stránka ukáže stav, jaký byl, a čas snímku nad tabulkou to přizná.
"""

from datetime import date
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from lihoviny.web.common import PRAGUE, db, templates

router = APIRouter()


@router.get("/akcni-ceny")
def akcni_ceny_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=dict_row) as cursor:
        snapshot = cursor.execute(
            """
            SELECT id, taken_at, item_count FROM snapshots
            WHERE agent = 'cenovy' ORDER BY taken_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        offers = []
        if snapshot:
            snapshot_day = snapshot["taken_at"].astimezone(PRAGUE).date()
            offers = cursor.execute(
                """
                SELECT source_url, content FROM findings
                WHERE type = 'akcni_cena'
                  AND (content->>'valid_until' >= %(day)s
                       OR (content->>'valid_until' IS NULL AND snapshot_id = %(snapshot)s))
                """,
                {"day": snapshot_day.isoformat(), "snapshot": snapshot["id"]},
            ).fetchall()
    offers.sort(
        key=lambda o: (o["content"]["product"].casefold(), o["content"]["chain"].casefold())
    )
    return templates.TemplateResponse(
        request, "akcni_ceny.html", {"snapshot": snapshot, "offers": offers}
    )


def czech_number(value: float | None, decimals: int = 2) -> str:
    """1066.13 → „1 066,13“ (tisíce odděluje nezlomitelná mezera)."""
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", "\xa0").replace(".", ",")


def short_number(value: float | None) -> str:
    """0.5 → „0,5“, 35.0 → „35“: bez zbytečných nul."""
    if value is None:
        return "—"
    return f"{value:g}".replace(".", ",")


def validity(content: dict[str, Any]) -> str:
    """Období platnosti: „do 17. 11. 2026“, se začátkem „16. 11. – 17. 11. 2026“."""
    until = content.get("valid_until") and date.fromisoformat(content["valid_until"])
    since = content.get("valid_from") and date.fromisoformat(content["valid_from"])
    if not until:
        return f"od {_day(since)}" if since else "neuvedeno"
    if not since:
        return f"do {_day(until)}"
    start = f"{since.day}. {since.month}." if since.year == until.year else _day(since)
    return f"{start} – {_day(until)}"


def _day(value: date) -> str:
    return f"{value.day}. {value.month}. {value.year}"


templates.env.filters["cislo"] = czech_number
templates.env.filters["kratce"] = short_number
templates.env.filters["platnost"] = validity
