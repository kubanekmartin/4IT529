"""Stránka Log komunikace: přímo tabulka zpráv ze sběrnice, nejnovější nahoře."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import class_row

from lihoviny.bus import Message
from lihoviny.web.common import db, templates

router = APIRouter()

KIND_LABELS = {"REQUEST": "Zadání", "INFORM": "Odpověď", "QUERY": "Dotaz", "FAILURE": "Selhání"}


@router.get("/log")
def log_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=class_row(Message)) as cursor:
        messages = cursor.execute(
            """
            SELECT id, conversation_id, kind, sender, recipient, content, sent_at, claimed_at
            FROM messages ORDER BY id DESC LIMIT 200
            """
        ).fetchall()
    return templates.TemplateResponse(
        request, "log.html", {"messages": messages, "kind_labels": KIND_LABELS}
    )
