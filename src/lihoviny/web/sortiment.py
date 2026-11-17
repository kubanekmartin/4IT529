"""Stránka Sortiment (KIQ 1): Novinky konkurence a produkty podle devíti kategorií.

U produktu jsou jeho akční ceny platné k poslednímu snímku cenového agenta (spárované
akční ceny, viz `lihoviny.pairing`), takže Novinka, kategorie a akční cena jsou pohromadě.
"""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import class_row, dict_row

from lihoviny.board import PRODUCT_COLUMNS, Product
from lihoviny.products import CATEGORIES
from lihoviny.web.akcni_ceny import current_offers
from lihoviny.web.common import db, templates
from lihoviny.web.trendy import novinky_cumulative

router = APIRouter()


@router.get("/sortiment")
def sortiment_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=dict_row) as cursor:
        novinky = cursor.execute(
            """
            SELECT f.found_at, f.source_url, f.content ->> 'note' AS note,
                   p.id AS product_id, p.name, p.brand, p.manufacturer, p.category,
                   p.volume_ml, p.abv
            FROM findings f JOIN products p ON p.id = f.product_id
            WHERE f.kiq = 1 AND f.type = 'novinka'
            ORDER BY f.found_at DESC, f.id DESC
            LIMIT 100
            """
        ).fetchall()
        last_snapshot = cursor.execute(
            """
            SELECT taken_at, item_count FROM snapshots
            WHERE agent = 'sortimentni' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    with conn.cursor(row_factory=class_row(Product)) as cursor:
        products = cursor.execute(
            f"SELECT {PRODUCT_COLUMNS} FROM products ORDER BY brand, name, volume_ml"
        ).fetchall()
    by_category: dict[str, list[Product]] = {code: [] for code in CATEGORIES}
    for product in products:
        if product.category in by_category:  # starší RTD z doby před zúžením na lihoviny ne
            by_category[product.category].append(product)
    offers_by_product: dict[int, list[dict]] = {}
    for offer in current_offers(conn)[1]:
        if offer["product_id"] is not None:
            offers_by_product.setdefault(offer["product_id"], []).append(offer)
    for offers in offers_by_product.values():
        offers.sort(key=lambda o: o["content"]["price"])  # u produktu nejlevnější nahoře
    return templates.TemplateResponse(
        request,
        "sortiment.html",
        {
            "novinky": novinky,
            "by_category": by_category,
            "offers_by_product": offers_by_product,
            "categories": CATEGORIES,
            "product_count": len(products),
            "last_snapshot": last_snapshot,
            "trend": novinky_cumulative(conn),
        },
    )
