"""Stránka Regulace (KIQ 4): sněmovní tisky a zprávy úřadů, které se podle regulatorního agenta
týkají lihovin.

Zdroj se uvádí, jak to chtějí podmínky otevřených dat Sněmovny: zdroj dat a datum zpracování,
tedy datum posledního snímku regulatorního agenta.
"""

from datetime import date
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from lihoviny.web.common import czech_date, db, templates
from lihoviny.web.trendy import tisky_pipeline

router = APIRouter()


# Řazení tisků: klíč v URL → popisek a funkce, která z tisku udělá klíč. Tisky bez údaje
# (bez data předložení nebo odhadu účinnosti) jdou vždy na konec.
SORTS = {
    "predlozeno": ("Předloženo: nejnovější", lambda c: c.get("submitted_on"), True),
    "predlozeno-nejstarsi": ("Předloženo: nejstarší", lambda c: c.get("submitted_on"), False),
    "dopad": ("Dopad: nejvyšší", lambda c: IMPACT_ORDER.get(c.get("impact_level")), False),
    "ucinnost": ("Účinnost: nejbližší", lambda c: c.get("effective_date"), False),
}
DEFAULT_SORT = "predlozeno"
IMPACT_ORDER = {"vysoký": 0, "střední": 1, "nízký": 2}


@router.get("/regulace")
def regulace_page(
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(db)],
    razeni: str = DEFAULT_SORT,
):
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
        zpravy = cursor.execute(
            """
            SELECT source_url, content, found_at FROM findings
            WHERE kiq = 4 AND type = 'uredni_zprava' ORDER BY found_at DESC, id DESC LIMIT 50
            """
        ).fetchall()
    sort = razeni if razeni in SORTS else DEFAULT_SORT
    return templates.TemplateResponse(
        request,
        "regulace.html",
        {
            "snapshot": snapshot,
            "tisky": sort_tisky(tisky, sort),
            "zpravy": zpravy,
            "sort": sort,
            "trend": tisky_pipeline([t["content"] for t in tisky]) if tisky else None,
            "sorts": {key: label for key, (label, _, _) in SORTS.items()},
        },
    )


def sort_tisky(tisky: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Tisky podle `sort`; bez údaje na konci, při shodě zůstává pořadí z databáze."""
    _, key, descending = SORTS[sort]
    known = [t for t in tisky if key(t["content"]) is not None]
    missing = [t for t in tisky if key(t["content"]) is None]
    return sorted(known, key=lambda t: key(t["content"]), reverse=descending) + missing


def iso_to_czech(value: str | None) -> str:
    """„2027-01-01“ → „1. 1. 2027“; bez data „—“."""
    return czech_date(date.fromisoformat(value)) if value else "—"


templates.env.filters["iso_datum"] = iso_to_czech
