"""Stránka Sortiment (KIQ 1): Novinky konkurence a produkty podle deseti kategorií."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import class_row, dict_row

from lihoviny.board import Product
from lihoviny.products import CATEGORIES
from lihoviny.web.common import db, templates

router = APIRouter()


@router.get("/sortiment")
def sortiment_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=dict_row) as cursor:
        novinky = cursor.execute(
            """
            SELECT f.found_at, f.source_url, f.content ->> 'note' AS note,
                   p.name, p.brand, p.manufacturer, p.category, p.volume_ml, p.abv
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
            """
            SELECT id, normalized_key, manufacturer, brand, name, volume_ml, abv, category,
                   first_seen_at, first_seen_url, first_seen_snapshot_id, source_product_id
            FROM products ORDER BY brand, name, volume_ml
            """
        ).fetchall()
    by_category: dict[str, list[Product]] = {code: [] for code in CATEGORIES}
    for product in products:
        by_category[product.category].append(product)
    return templates.TemplateResponse(
        request,
        "sortiment.html",
        {
            "novinky": novinky,
            "by_category": by_category,
            "categories": CATEGORIES,
            "product_count": len(products),
            "last_snapshot": last_snapshot,
        },
    )
