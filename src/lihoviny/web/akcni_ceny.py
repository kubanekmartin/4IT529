"""Stránka Akční ceny (KIQ 2): akční ceny lihovin a RTD platné k času posledního snímku.

„Platné“ se počítá k datu posledního snímku cenového agenta, ne k dnešku: když sběr
vypadne, stránka ukáže stav, jaký byl, a čas snímku nad tabulkou to přizná. Akční cena bez
data konce platí, dokud ji poslední snímek obsahuje (tabulka `snapshot_findings`).

Spárovaná akční cena ukazuje kategorii svého produktu ze sortimentu. Nespárované akční ceny
jsou ještě zvlášť na `/akcni-ceny/nesparovane`, jednou za každý název u zdroje, aby šlo
párování zlepšit. Akční ceny u produktů ukazuje stránka Sortiment (`current_offers`).
"""

from datetime import date
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from lihoviny.clock import PRAGUE
from lihoviny.products import CATEGORIES
from lihoviny.web.common import czech_date, db, templates

router = APIRouter()


@router.get("/akcni-ceny")
def akcni_ceny_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    snapshot, offers = current_offers(conn)
    return templates.TemplateResponse(
        request,
        "akcni_ceny.html",
        {
            "snapshot": snapshot,
            "offers": offers,
            "categories": CATEGORIES,
            "unpaired_count": sum(offer["product_id"] is None for offer in offers),
        },
    )


@router.get("/akcni-ceny/nesparovane")
def unpaired_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    snapshot, offers = current_offers(conn)
    # Páruje se podle názvu u zdroje: jedna položka za název, s řetězci, které ho nabízejí.
    by_name: dict[str, dict[str, Any]] = {}
    for offer in offers:
        if offer["product_id"] is not None:
            continue
        content = offer["content"]
        item = by_name.setdefault(
            content["product"],
            {"source_url": offer["source_url"], "content": content, "chains": set()},
        )
        item["chains"].add(content["chain"])
    unpaired = [
        item | {"chains": sorted(item["chains"], key=str.casefold)} for item in by_name.values()
    ]
    return templates.TemplateResponse(
        request,
        "akcni_ceny_nesparovane.html",
        {"snapshot": snapshot, "unpaired": unpaired, "offer_count": len(offers)},
    )


def current_offers(conn: psycopg.Connection) -> tuple[dict | None, list[dict]]:
    """Poslední snímek cenového agenta a akční ceny platné k jeho datu, seřazené podle
    produktu a řetězce. U spárovaných je `product_id`, `category` a `product_name`."""
    with conn.cursor(row_factory=dict_row) as cursor:
        snapshot = cursor.execute(
            """
            SELECT id, taken_at, item_count FROM snapshots
            WHERE agent = 'cenovy' ORDER BY taken_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        if not snapshot:
            return None, []
        snapshot_day = snapshot["taken_at"].astimezone(PRAGUE).date()
        offers = cursor.execute(
            """
            SELECT f.source_url, f.content, f.product_id, p.category, p.name AS product_name
            FROM findings f LEFT JOIN products p ON p.id = f.product_id
            WHERE f.type = 'akcni_cena'
              AND (f.content->>'valid_until' >= %(day)s
                   OR (f.content->>'valid_until' IS NULL AND f.id IN (
                       SELECT finding_id FROM snapshot_findings
                       WHERE snapshot_id = %(snapshot)s)))
            """,
            {"day": snapshot_day.isoformat(), "snapshot": snapshot["id"]},
        ).fetchall()
    offers.sort(
        key=lambda o: (o["content"]["product"].casefold(), o["content"]["chain"].casefold())
    )
    return snapshot, offers


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
    until = _iso_date(content["valid_until"])
    since = _iso_date(content["valid_from"])
    if until is None:
        return f"od {czech_date(since)}" if since else "neuvedeno"
    if since is None:
        return f"do {czech_date(until)}"
    start = f"{since.day}. {since.month}." if since.year == until.year else czech_date(since)
    return f"{start} – {czech_date(until)}"


def _iso_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


templates.env.filters["cislo"] = czech_number
templates.env.filters["kratce"] = short_number
templates.env.filters["platnost"] = validity
