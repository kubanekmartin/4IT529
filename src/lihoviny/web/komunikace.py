"""Stránka Komunikace (KIQ 3): články o lihovinách z oborového tisku, nejnovější první."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from lihoviny.agents.medialni import CHANNELS, TOPICS
from lihoviny.web.common import db, templates

router = APIRouter()


@router.get("/komunikace")
def komunikace_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=dict_row) as cursor:
        clanky = cursor.execute(
            """
            SELECT source_url, content, (content->>'published_at')::timestamptz AS published_at
            FROM findings
            WHERE kiq = 3 AND type = 'clanek'
            ORDER BY published_at DESC, id DESC
            LIMIT 100
            """
        ).fetchall()
        last_snapshot = cursor.execute(
            """
            SELECT taken_at, item_count FROM snapshots
            WHERE agent = 'medialni' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    return templates.TemplateResponse(
        request,
        "komunikace.html",
        {
            "clanky": clanky,
            "last_snapshot": last_snapshot,
            "channels": CHANNELS,
            "topics": TOPICS,
        },
    )
