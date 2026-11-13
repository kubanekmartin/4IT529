"""Připojení k Postgresu pro sběrnici a tabuli.

Každá operace sběrnice (`Bus`) a tabule (`Board`) jde vlastním krátkým spojením, takže je
po návratu uložená. Uvnitř bloku `with transaction(database_url):` jdou všechny jejich
operace nad touž databází jedním spojením a uloží se až na konci bloku, najednou; když blok
skončí výjimkou, neuloží se nic. Sběrný agent tak po pádu uprostřed zápisu nenechá na tabuli
polovinu snímku a řídicí agent nespotřebuje termín rozvrhu bez odeslaných Zadání.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import psycopg
from psycopg.rows import class_row, tuple_row

# Otevřená transakce v tomto vlákně: (adresa databáze, její spojení).
_open: ContextVar[tuple[str, psycopg.Connection] | None] = ContextVar("transakce", default=None)


@contextmanager
def transaction(database_url: str) -> Iterator[None]:
    """Operace sběrnice a tabule uvnitř bloku se uloží všechny, nebo žádná."""
    current = _open.get()
    if current is not None and current[0] == database_url:
        yield  # vnořený blok je součástí vnější transakce
        return
    with psycopg.connect(database_url) as conn:
        token = _open.set((database_url, conn))
        try:
            yield
        finally:
            _open.reset(token)


@contextmanager
def cursor(database_url: str, row_class: type | None = None) -> Iterator[psycopg.Cursor]:
    """Kurzor v otevřené transakci, jinak v novém spojení, které se na konci bloku uloží."""
    row_factory = class_row(row_class) if row_class else tuple_row
    current = _open.get()
    if current is not None and current[0] == database_url:
        with current[1].cursor(row_factory=row_factory) as cur:
            yield cur
        return
    with psycopg.connect(database_url) as conn, conn.cursor(row_factory=row_factory) as cur:
        yield cur
