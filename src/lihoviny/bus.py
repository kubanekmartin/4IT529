"""Sběrnice zpráv nad Postgresem (ADR 0001).

Odeslání je vložení řádku do `messages`; trigger pošle `NOTIFY messages, '<příjemce>'`.
Vyzvednutí řádek hned označí (`claimed_at`), takže každou zprávu zpracuje nejvýš jeden agent.
"""

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from lihoviny.clock import Clock


class Kind(StrEnum):
    """Druh zprávy podle FIPA ACL; české názvy z GLOSSARY.md."""

    REQUEST = "REQUEST"  # Zadání
    INFORM = "INFORM"  # odpověď s výsledkem
    QUERY = "QUERY"  # Dotaz
    FAILURE = "FAILURE"  # Selhání


@dataclass(frozen=True)
class Message:
    id: int
    conversation_id: uuid.UUID
    kind: Kind
    sender: str
    recipient: str
    content: dict[str, Any]
    sent_at: datetime
    claimed_at: datetime | None

    def __post_init__(self):
        object.__setattr__(self, "kind", Kind(self.kind))


_COLUMNS = "id, conversation_id, kind, sender, recipient, content, sent_at, claimed_at"


class Bus:
    def __init__(self, database_url: str, clock: Clock):
        self._database_url = database_url
        self._clock = clock

    def send(
        self,
        *,
        kind: Kind,
        sender: str,
        recipient: str,
        content: dict[str, Any],
        conversation_id: uuid.UUID | None = None,
    ) -> Message:
        """Pošle zprávu; bez `conversation_id` začne novou konverzaci."""
        with self._connect() as conn:
            return conn.execute(
                f"""
                INSERT INTO messages (conversation_id, kind, sender, recipient, content, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING {_COLUMNS}
                """,
                [
                    conversation_id or uuid.uuid4(),
                    kind,
                    sender,
                    recipient,
                    Jsonb(content),
                    self._clock.now(),
                ],
            ).fetchone()

    def reply(self, to: Message, kind: Kind, content: dict[str, Any]) -> Message:
        return self.send(
            kind=kind,
            sender=to.recipient,
            recipient=to.sender,
            content=content,
            conversation_id=to.conversation_id,
        )

    def claim(self, recipient: str) -> Message | None:
        """Vyzvedne nejstarší nevyzvednutou zprávu pro příjemce, nebo vrátí None."""
        with self._connect() as conn:
            return conn.execute(
                f"""
                UPDATE messages SET claimed_at = %s
                WHERE id = (
                    SELECT id FROM messages
                    WHERE recipient = %s AND claimed_at IS NULL
                    ORDER BY id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING {_COLUMNS}
                """,
                [self._clock.now(), recipient],
            ).fetchone()

    def conversation(self, conversation_id: uuid.UUID) -> list[Message]:
        with self._connect() as conn:
            return conn.execute(
                f"SELECT {_COLUMNS} FROM messages WHERE conversation_id = %s ORDER BY id",
                [conversation_id],
            ).fetchall()

    @contextmanager
    def listen(self, recipient: str) -> Iterator["Inbox"]:
        with psycopg.connect(self._database_url, autocommit=True) as conn:
            conn.execute("LISTEN messages")
            yield Inbox(conn, recipient)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._database_url, row_factory=class_row(Message))


class Inbox:
    """Čeká na `NOTIFY` pro jednoho příjemce; oznámení pro ostatní agenty přeskočí."""

    def __init__(self, conn: psycopg.Connection, recipient: str):
        self._conn = conn
        self._recipient = recipient

    def wait(self, timeout: float) -> bool:
        """Vrátí True, když přišlo oznámení pro příjemce, False po vypršení `timeout` sekund."""
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            for notify in self._conn.notifies(timeout=remaining, stop_after=1):
                if notify.payload == self._recipient:
                    return True
        return False
