"""Stránka Akční ceny (KIQ 2): akční ceny lihovin a RTD platné k času posledního snímku.

„Platné“ se počítá k datu posledního snímku cenového agenta, ne k dnešku: když sběr
vypadne, stránka ukáže stav, jaký byl, a čas snímku nad tabulkou to přizná. Akční cena bez
data konce platí, dokud ji poslední snímek obsahuje (tabulka `snapshot_findings`).

Spárovaná akční cena ukazuje kategorii svého produktu ze sortimentu. Nespárované akční ceny
jsou ještě zvlášť na `/akcni-ceny/nesparovane`, jednou za každý název u zdroje, aby šlo
párování zlepšit. Akční ceny u produktů ukazuje stránka Sortiment (`current_offers`).
"""

from collections.abc import Callable
from datetime import date
from statistics import median
from typing import Annotated, Any
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from lihoviny.agents.analyticky import LISTING_CATEGORIES
from lihoviny.clock import PRAGUE
from lihoviny.products import CATEGORIES
from lihoviny.web.common import czech_date, db, templates
from lihoviny.zadavatel import PORTFOLIO, WATCHED_COMPETITORS

router = APIRouter()


# Cenový tlak na produkt zadavatele: o kolik je nejlevnější akční cena konkurence v kategorii
# (za litr) pod jeho běžnou cenou. Hranice je na stránce i vysvětlená.
HIGH_PRESSURE = 0.15
TOP_CHAINS = 5


# Sloupce tabulky, podle kterých jde řadit: klíč v URL → (popisek, hodnota z akční ceny).
SORT_COLUMNS: dict[str, tuple[str, Callable[[dict[str, Any]], Any]]] = {
    "produkt": ("Produkt", lambda o: o["content"]["product"].casefold()),
    "kategorie": ("Kategorie", lambda o: CATEGORIES.get(o["category"]) if o["category"] else None),
    "retezec": ("Řetězec", lambda o: o["content"]["chain"].casefold()),
    "cena": ("Akční cena", lambda o: o["content"].get("price")),
    "platnost": ("Platnost", lambda o: o["content"].get("valid_until")),
    "objem": ("Objem", lambda o: o["content"].get("volume_l")),
    "alkohol": ("Alkohol", lambda o: o["content"].get("abv_percent")),
    "litr": ("Kč za litr", lambda o: o["content"].get("price_per_l")),
}
# Číselné sloupce s filtrem od–do: klíč v URL → pole akční ceny.
RANGE_FILTERS = {
    "cena": "price",
    "alkohol": "abv_percent",
    "litr": "price_per_l",
}
UNPAIRED = "nesparovano"


@router.get("/akcni-ceny")
def akcni_ceny_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    snapshot, offers = current_offers(conn)
    params = {key: value.strip() for key, value in request.query_params.items() if value.strip()}
    shown = sort_offers(filter_offers(offers, params), params)
    sort, direction = _sort_param(params)
    return templates.TemplateResponse(
        request,
        "akcni_ceny.html",
        {
            "snapshot": snapshot,
            "offers": shown,
            "total": len(offers),
            "params": params,
            "filtered": any(k not in ("razeni", "smer") for k in params),
            "sort": sort,
            "direction": direction,
            "sort_columns": {key: label for key, (label, _) in SORT_COLUMNS.items()},
            "sort_url": lambda column: _sort_url(params, column),
            "chain_options": sorted({o["content"]["chain"] for o in offers}, key=str.casefold),
            "category_options": [
                (code, label)
                for code, label in CATEGORIES.items()
                if any(o["category"] == code for o in offers)
            ],
            "volume_options": sorted(
                {o["content"]["volume_l"] for o in offers if o["content"].get("volume_l")}
            ),
            "categories": CATEGORIES,
            "unpaired_count": sum(offer["product_id"] is None for offer in offers),
            "pressure": portfolio_pressure(offers),
            "chains": chains_summary(offers),
            "watched": WATCHED_COMPETITORS,
            "high_pressure": round(HIGH_PRESSURE * 100),
        },
    )


def filter_offers(offers: list[dict[str, Any]], params: dict[str, str]) -> list[dict[str, Any]]:
    """Akční ceny, které projdou filtry z URL; neplatná hodnota filtru se ignoruje."""
    result = offers
    if text := params.get("produkt"):
        result = [o for o in result if text.casefold() in o["content"]["product"].casefold()]
    if category := params.get("kategorie"):
        wanted = None if category == UNPAIRED else category
        result = [o for o in result if o["category"] == wanted]
    if chain := params.get("retezec"):
        result = [o for o in result if o["content"]["chain"] == chain]
    if (volume := _number(params.get("objem"))) is not None:
        result = [o for o in result if o["content"].get("volume_l") == volume]
    if (day := _iso(params.get("plati"))) is not None:
        result = [o for o in result if _valid_on(o["content"], day)]
    for key, field in RANGE_FILTERS.items():
        low, high = _number(params.get(f"{key}_od")), _number(params.get(f"{key}_do"))
        if low is not None:
            result = [o for o in result if (v := o["content"].get(field)) is not None and v >= low]
        if high is not None:
            result = [o for o in result if (v := o["content"].get(field)) is not None and v <= high]
    return result


def sort_offers(offers: list[dict[str, Any]], params: dict[str, str]) -> list[dict[str, Any]]:
    """Seřadí podle sloupce z URL; akční ceny bez hodnoty jsou vždy na konci."""
    column, direction = _sort_param(params)
    key = SORT_COLUMNS[column][1]
    known = [o for o in offers if key(o) is not None]
    missing = [o for o in offers if key(o) is None]
    return sorted(known, key=key, reverse=direction == "desc") + missing


def _sort_param(params: dict[str, str]) -> tuple[str, str]:
    column = params.get("razeni", "produkt")
    if column not in SORT_COLUMNS:
        column = "produkt"
    return column, "desc" if params.get("smer") == "desc" else "asc"


def _sort_url(params: dict[str, str], column: str) -> str:
    """Odkaz v hlavičce sloupce: řadit podle něj, opakované kliknutí obrátí směr."""
    current, direction = _sort_param(params)
    flip = "desc" if current == column and direction == "asc" else "asc"
    return "/akcni-ceny?" + urlencode(params | {"razeni": column, "smer": flip}) + "#tabulka"


def _number(value: str | None) -> float | None:
    """„1 066,5“ i „1066.5“ → 1066.5; prázdné nebo nečíselné → None."""
    if not value:
        return None
    try:
        return float(value.replace("\xa0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _valid_on(content: dict[str, Any], day: date) -> bool:
    since, until = _iso_date(content.get("valid_from")), _iso_date(content.get("valid_until"))
    return (since is None or since <= day) and (until is None or day <= until)


def offer_category(offer: dict[str, Any]) -> str | None:
    """Kategorie akční ceny: kategorie spárovaného produktu, jinak kategorie výpisu u zdroje."""
    return offer["category"] or LISTING_CATEGORIES.get(offer["content"].get("source_category"))


def cheapest_by_category(offers: list[dict[str, Any]]) -> dict[str, tuple[float, dict[str, Any]]]:
    """Nejnižší cena za litr v každé kategorii a akční cena, která ji má."""
    cheapest: dict[str, tuple[float, dict[str, Any]]] = {}
    for offer in offers:
        category, value = offer_category(offer), offer["content"].get("price_per_l")
        if category is None or not isinstance(value, int | float):
            continue
        if category not in cheapest or value < cheapest[category][0]:
            cheapest[category] = (float(value), offer["content"])
    return cheapest


def portfolio_pressure(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pro každý produkt zadavatele: jeho běžná cena za litr proti nejlevnější akční
    ceně konkurence v kategorii, kolik akcí v kategorii je, obvyklá sleva a cenový tlak."""
    cheapest = cheapest_by_category(offers)
    rows = []
    for product in PORTFOLIO:
        in_category = [o for o in offers if offer_category(o) == product.category]
        discounts = [
            o["content"]["discount_percent"]
            for o in in_category
            if isinstance(o["content"].get("discount_percent"), int | float)
        ]
        own = float(product.price_per_l)
        row: dict[str, Any] = {
            "product": product,
            "category": CATEGORIES.get(product.category, product.category),
            "own": own,
            "offer_count": len(in_category),
            "typical_discount": round(median(discounts)) if discounts else None,
            "cheapest": None,
            "gap": None,
            "level": "bez_akce",
        }
        if product.category in cheapest:
            litre, content = cheapest[product.category]
            gap = (litre - own) / own
            row |= {"cheapest": (litre, content), "gap": round(gap * 100)}
            row["level"] = "vysoky" if gap <= -HIGH_PRESSURE else "stredni" if gap < 0 else "nizky"
        rows.append(row)
    return rows


def chains_summary(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Řetězce s nejvíc akčními cenami: počet, z toho sledovaných konkurentů, průměrná sleva."""
    by_chain: dict[str, list[dict[str, Any]]] = {}
    for offer in offers:
        by_chain.setdefault(offer["content"]["chain"], []).append(offer)
    rows = []
    for chain, chain_offers in by_chain.items():
        discounts = [
            o["content"]["discount_percent"]
            for o in chain_offers
            if isinstance(o["content"].get("discount_percent"), int | float)
        ]
        rows.append(
            {
                "chain": chain,
                "count": len(chain_offers),
                "watched": sum(o["manufacturer"] in WATCHED_COMPETITORS for o in chain_offers),
                "discount": round(sum(discounts) / len(discounts)) if discounts else None,
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["chain"].casefold()))
    return rows[:TOP_CHAINS]


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
    produktu a řetězce. U spárovaných je `product_id`, `category`, `product_name`
    a `manufacturer`."""
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
            SELECT f.source_url, f.content, f.product_id, p.category, p.name AS product_name,
                   p.manufacturer
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
