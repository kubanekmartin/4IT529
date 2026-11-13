"""Stránka Log komunikace: zprávy ze sběrnice seskupené po konverzacích.

Konverzace začíná Zadáním a patří do ní všechno, co z něj vzešlo: odpověď, a u Briefu
i Dotazy analytického agenta, Zadání z Dotazu a odpovědi na ně. Nahoře je konverzace
s nejnovější zprávou, uvnitř jdou zprávy v pořadí, jak byly odeslány.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import class_row

from lihoviny.bus import MESSAGE_COLUMNS, Kind, Message
from lihoviny.web.common import db, templates

router = APIRouter()

KIND_LABELS = {"REQUEST": "Zadání", "INFORM": "Odpověď", "QUERY": "Dotaz", "FAILURE": "Selhání"}
CONVERSATION_LIMIT = 50


@dataclass(frozen=True)
class Conversation:
    id: uuid.UUID
    messages: list[Message]

    @property
    def agents(self) -> list[str]:
        """Účastníci v pořadí, jak se v konverzaci poprvé objevili."""
        names = (name for m in self.messages for name in (m.sender, m.recipient))
        return list(dict.fromkeys(names))

    @property
    def dotazy(self) -> int:
        return sum(m.kind is Kind.QUERY for m in self.messages)


@router.get("/log")
def log_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=class_row(Message)) as cursor:
        messages = cursor.execute(
            f"""
            SELECT {MESSAGE_COLUMNS} FROM messages
            WHERE conversation_id IN (
                SELECT conversation_id FROM messages
                GROUP BY conversation_id
                ORDER BY max(id) DESC
                LIMIT %s
            )
            ORDER BY id
            """,
            [CONVERSATION_LIMIT],
        ).fetchall()
    by_id: dict[uuid.UUID, list[Message]] = {}
    for message in messages:
        by_id.setdefault(message.conversation_id, []).append(message)
    conversations = sorted(
        (Conversation(conversation_id, items) for conversation_id, items in by_id.items()),
        key=lambda conversation: conversation.messages[-1].id,
        reverse=True,
    )
    return templates.TemplateResponse(
        request,
        "log.html",
        {
            "conversations": conversations,
            "kind_labels": KIND_LABELS,
            "limit": CONVERSATION_LIMIT,
        },
    )
