"""Sdílená tabule (blackboard): snímky zdrojů a nálezy, ze kterých čte analytický agent.

Sběrný agent po stažení zdroje zapíše jeden snímek (odkazuje na surová data na disku)
a k němu nálezy. Snímky se nepřepisují; historie vzniká z jejich řady. Produkty zapisuje
sortimentní agent, když produkt poprvé uvidí; nález se na produkt může odkazovat.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from lihoviny.clock import Clock
from lihoviny.fetch import FetchResult
from lihoviny.products import Category

FindingType = Literal["novinka", "akcni_cena", "clanek", "snemovni_tisk"]


@dataclass(frozen=True)
class Snapshot:
    id: int
    agent: str
    source: str
    taken_at: datetime
    raw_paths: list[str]
    item_count: int


@dataclass(frozen=True)
class Finding:
    id: int
    kiq: int
    agent: str
    type: FindingType
    product_id: int | None
    snapshot_id: int | None
    source_url: str
    content: dict[str, Any]
    found_at: datetime


@dataclass(frozen=True)
class Product:
    """Konkrétní balení lihoviny; `normalized_key` viz `lihoviny.products.product_key`."""

    id: int
    normalized_key: str
    manufacturer: str | None
    brand: str
    name: str
    volume_ml: int
    abv: Decimal
    category: Category
    first_seen_at: datetime
    first_seen_url: str
    first_seen_snapshot_id: int
    source_product_id: str


_FINDING_COLUMNS = "id, kiq, agent, type, product_id, snapshot_id, source_url, content, found_at"
_SNAPSHOT_COLUMNS = "id, agent, source, taken_at, raw_paths, item_count"
_PRODUCT_COLUMNS = (
    "id, normalized_key, manufacturer, brand, name, volume_ml, abv, category, first_seen_at,"
    " first_seen_url, first_seen_snapshot_id, source_product_id"
)


class Board:
    def __init__(self, database_url: str, clock: Clock):
        self._database_url = database_url
        self._clock = clock

    def record_snapshot(
        self, *, agent: str, source: str, fetches: Sequence[FetchResult], item_count: int
    ) -> Snapshot:
        """Zapíše snímek zdroje; `fetches` jsou všechna stažení, ze kterých snímek vznikl."""
        with self._connect(Snapshot) as conn:
            return conn.execute(
                f"""
                INSERT INTO snapshots (agent, source, taken_at, raw_paths, item_count)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_SNAPSHOT_COLUMNS}
                """,
                [agent, source, self._clock.now(), [str(f.raw_path) for f in fetches], item_count],
            ).fetchone()

    def snapshot(self, snapshot_id: int) -> Snapshot:
        with self._connect(Snapshot) as conn:
            return conn.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE id = %s", [snapshot_id]
            ).fetchone()

    def snapshots(self, *, agent: str) -> list[Snapshot]:
        """Všechny snímky agenta od nejstaršího."""
        with self._connect(Snapshot) as conn:
            return conn.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE agent = %s ORDER BY id", [agent]
            ).fetchall()

    def record_product(
        self,
        *,
        normalized_key: str,
        manufacturer: str | None,
        brand: str,
        name: str,
        volume_ml: int,
        abv: Decimal,
        category: Category,
        first_seen_url: str,
        first_seen_snapshot_id: int,
        source_product_id: str,
    ) -> Product:
        """Zapíše nově viděný produkt; `first_seen_at` = clock.now()."""
        with self._connect(Product) as conn:
            return conn.execute(
                f"""
                INSERT INTO products
                    (normalized_key, manufacturer, brand, name, volume_ml, abv, category,
                     first_seen_at, first_seen_url, first_seen_snapshot_id, source_product_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_PRODUCT_COLUMNS}
                """,
                [
                    normalized_key,
                    manufacturer,
                    brand,
                    name,
                    volume_ml,
                    abv,
                    category,
                    self._clock.now(),
                    first_seen_url,
                    first_seen_snapshot_id,
                    source_product_id,
                ],
            ).fetchone()

    def products(self) -> list[Product]:
        """Všechny produkty v pořadí, jak byly poprvé viděny."""
        with self._connect(Product) as conn:
            return conn.execute(f"SELECT {_PRODUCT_COLUMNS} FROM products ORDER BY id").fetchall()

    def record_finding(
        self,
        *,
        kiq: int,
        agent: str,
        type: FindingType,
        source_url: str,
        content: dict[str, Any],
        snapshot_id: int | None = None,
        product_id: int | None = None,
    ) -> Finding:
        with self._connect(Finding) as conn:
            return conn.execute(
                f"""
                INSERT INTO findings
                    (kiq, agent, type, product_id, snapshot_id, source_url, content, found_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_FINDING_COLUMNS}
                """,
                [
                    kiq,
                    agent,
                    type,
                    product_id,
                    snapshot_id,
                    source_url,
                    Jsonb(content),
                    self._clock.now(),
                ],
            ).fetchone()

    def findings(self, *, kiq: int | None = None, snapshot_id: int | None = None) -> list[Finding]:
        with self._connect(Finding) as conn:
            return conn.execute(
                f"""
                SELECT {_FINDING_COLUMNS} FROM findings
                WHERE (%(kiq)s::smallint IS NULL OR kiq = %(kiq)s)
                  AND (%(snapshot_id)s::bigint IS NULL OR snapshot_id = %(snapshot_id)s)
                ORDER BY id
                """,
                {"kiq": kiq, "snapshot_id": snapshot_id},
            ).fetchall()

    def _connect(self, row_class: type) -> psycopg.Connection:
        return psycopg.connect(self._database_url, row_factory=class_row(row_class))
