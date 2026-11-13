"""Sdílená tabule (blackboard): snímky zdrojů a nálezy, ze kterých čte analytický agent.

Sběrný agent po stažení zdroje zapíše jeden snímek (odkazuje na surová data na disku)
a k němu nálezy. Snímky se nepřepisují; historie vzniká z jejich řady. Produkty zapisuje
sortimentní agent, když produkt poprvé uvidí; nález se na produkt může odkazovat. Cenový
agent akční cenu spáruje s produktem i dodatečně (`record_pairing`), jakmile produkt přibude.
Mediální agent si eviduje viděné články (jen jejich ID u zdroje).

Řídicí agent sem zapisuje běhy: komu v běhu poslal Zadání a jak každé dopadlo.
"""

import uuid
from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from lihoviny import db
from lihoviny.bus import Kind
from lihoviny.clock import Clock
from lihoviny.fetch import FetchResult
from lihoviny.products import Category

FindingType = Literal["novinka", "akcni_cena", "clanek", "snemovni_tisk"]
DruhBehu = Literal["collection", "brief"]


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
    source_product_id: int


_FINDING_COLUMNS = "id, kiq, agent, type, product_id, snapshot_id, source_url, content, found_at"
_SNAPSHOT_COLUMNS = "id, agent, source, taken_at, raw_paths, item_count"
PRODUCT_COLUMNS = (
    "id, normalized_key, manufacturer, brand, name, volume_ml, abv, category, first_seen_at,"
    " first_seen_url, first_seen_snapshot_id, source_product_id"
)


@dataclass(frozen=True)
class Beh:
    id: int
    kind: DruhBehu
    scheduled_for: datetime | None
    started_at: datetime


BEH_COLUMNS = "id, kind, scheduled_for, started_at"


@dataclass(frozen=True)
class Zadani:
    """Zadání v běhu; `outcome` None = agent ještě neodpověděl."""

    beh_id: int
    agent: str
    conversation_id: uuid.UUID
    sent_at: datetime
    outcome: Literal["INFORM", "FAILURE"] | None
    new_findings: int | None
    item_count: int | None
    reason: str | None
    answered_at: datetime | None


ZADANI_COLUMNS = (
    "beh_id, agent, conversation_id, sent_at, outcome, new_findings, item_count, reason,"
    " answered_at"
)


class Board:
    def __init__(self, database_url: str, clock: Clock):
        self.database_url = database_url
        self._clock = clock

    def record_snapshot(
        self, *, agent: str, source: str, fetches: Sequence[FetchResult], item_count: int
    ) -> Snapshot:
        """Zapíše snímek zdroje; `fetches` jsou všechna stažení, ze kterých snímek vznikl."""
        with self._cursor(Snapshot) as cur:
            return cur.execute(
                f"""
                INSERT INTO snapshots (agent, source, taken_at, raw_paths, item_count)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_SNAPSHOT_COLUMNS}
                """,
                [agent, source, self._clock.now(), [str(f.raw_path) for f in fetches], item_count],
            ).fetchone()

    def snapshot(self, snapshot_id: int) -> Snapshot:
        with self._cursor(Snapshot) as cur:
            return cur.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE id = %s", [snapshot_id]
            ).fetchone()

    def snapshots(self, *, agent: str) -> list[Snapshot]:
        with self._cursor(Snapshot) as cur:
            return cur.execute(
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
        source_product_id: int,
    ) -> Product:
        """Zapíše nově viděný produkt; `first_seen_at` = clock.now()."""
        with self._cursor(Product) as cur:
            return cur.execute(
                f"""
                INSERT INTO products
                    (normalized_key, manufacturer, brand, name, volume_ml, abv, category,
                     first_seen_at, first_seen_url, first_seen_snapshot_id, source_product_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {PRODUCT_COLUMNS}
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
        with self._cursor(Product) as cur:
            return cur.execute(f"SELECT {PRODUCT_COLUMNS} FROM products ORDER BY id").fetchall()

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
        with self._cursor(Finding) as cur:
            return cur.execute(
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
        with self._cursor(Finding) as cur:
            return cur.execute(
                f"""
                SELECT {_FINDING_COLUMNS} FROM findings
                WHERE (%(kiq)s::smallint IS NULL OR kiq = %(kiq)s)
                  AND (%(snapshot_id)s::bigint IS NULL OR snapshot_id = %(snapshot_id)s)
                ORDER BY id
                """,
                {"kiq": kiq, "snapshot_id": snapshot_id},
            ).fetchall()

    def record_pairing(self, finding_id: int, *, product_id: int, content: dict[str, Any]) -> None:
        """Spáruje dřívější nespárovaný nález s produktem; obsah nahradí doplněným."""
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE findings SET product_id = %s, content = %s
                WHERE id = %s AND product_id IS NULL
                """,
                [product_id, Jsonb(content), finding_id],
            )

    def rejected_pairings(self) -> set[tuple[str, int]]:
        """Dvojice (název akční ceny, ID produktu), které LLM posoudil jako různé produkty."""
        with self._cursor() as cur:
            return set(cur.execute("SELECT offer_name, product_id FROM rejected_pairings"))

    def record_rejected_pairings(self, pairs: Iterable[tuple[str, int]]) -> None:
        with self._cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rejected_pairings (offer_name, product_id) VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                list(pairs),
            )

    def record_snapshot_findings(self, snapshot_id: int, finding_ids: Sequence[int]) -> None:
        """Zapíše, které nálezy (i dřívější) snímek u zdroje znovu viděl."""
        with self._cursor() as cur:
            cur.executemany(
                "INSERT INTO snapshot_findings (snapshot_id, finding_id) VALUES (%s, %s)",
                [(snapshot_id, finding_id) for finding_id in finding_ids],
            )

    def record_article(
        self,
        *,
        source: str,
        source_article_id: int,
        first_seen_snapshot_id: int,
        finding_id: int | None,
    ) -> None:
        """Zapíše článek, který mediální agent viděl poprvé; `finding_id` None = nerelevantní."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO articles
                    (source, source_article_id, first_seen_snapshot_id, finding_id)
                VALUES (%s, %s, %s, %s)
                """,
                [source, source_article_id, first_seen_snapshot_id, finding_id],
            )

    def article_ids(self, *, source: str) -> set[int]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT source_article_id FROM articles WHERE source = %s", [source]
            ).fetchall()
        return {row[0] for row in rows}

    def start_beh(self, kind: DruhBehu, *, scheduled_for: datetime | None = None) -> Beh | None:
        """Zahájí běh. Termín rozvrhu, za který už běh proběhl, vrátí None."""
        with self._cursor(Beh) as cur:
            # NOT EXISTS místo samotného ON CONFLICT: to by při každém pokusu spotřebovalo
            # číslo běhu, a řídicí agent se na termín ptá každých 30 s.
            return cur.execute(
                f"""
                INSERT INTO behy (kind, scheduled_for, started_at)
                SELECT %(kind)s, %(scheduled_for)s, %(now)s
                WHERE NOT EXISTS (
                    SELECT 1 FROM behy WHERE kind = %(kind)s AND scheduled_for = %(scheduled_for)s
                )
                ON CONFLICT (kind, scheduled_for) DO NOTHING
                RETURNING {BEH_COLUMNS}
                """,
                {"kind": kind, "scheduled_for": scheduled_for, "now": self._clock.now()},
            ).fetchone()

    def add_zadani(self, *, beh_id: int, agent: str, conversation_id: uuid.UUID) -> None:
        """Zapíše Zadání do běhu; od této chvíle běží agentovi limit na odpověď."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO zadani (beh_id, agent, conversation_id, sent_at)
                VALUES (%s, %s, %s, %s)
                """,
                [beh_id, agent, conversation_id, self._clock.now()],
            )

    def record_outcome(
        self,
        conversation_id: uuid.UUID,
        outcome: Literal[Kind.INFORM, Kind.FAILURE],
        *,
        new_findings: int | None = None,
        item_count: int | None = None,
        reason: str | None = None,
    ) -> Zadani | None:
        """Zapíše, jak Zadání dopadlo. Platí první výsledek; jinak (a mimo běh) vrátí None."""
        with self._cursor(Zadani) as cur:
            return cur.execute(
                f"""
                UPDATE zadani
                SET outcome = %s, new_findings = %s, item_count = %s, reason = %s,
                    answered_at = %s
                WHERE conversation_id = %s AND outcome IS NULL
                RETURNING {ZADANI_COLUMNS}
                """,
                [
                    outcome,
                    new_findings,
                    item_count,
                    reason,
                    self._clock.now(),
                    conversation_id,
                ],
            ).fetchone()

    def latest_beh(self, kind: DruhBehu) -> Beh | None:
        with self._cursor(Beh) as cur:
            return cur.execute(
                f"SELECT {BEH_COLUMNS} FROM behy WHERE kind = %s ORDER BY id DESC LIMIT 1",
                [kind],
            ).fetchone()

    def zadani(self, beh_id: int) -> list[Zadani]:
        with self._cursor(Zadani) as cur:
            return cur.execute(
                f"SELECT {ZADANI_COLUMNS} FROM zadani WHERE beh_id = %s ORDER BY agent",
                [beh_id],
            ).fetchall()

    def waiting_zadani(self, *, sent_before: datetime) -> list[Zadani]:
        with self._cursor(Zadani) as cur:
            return cur.execute(
                f"""
                SELECT {ZADANI_COLUMNS} FROM zadani
                WHERE outcome IS NULL AND sent_at <= %s
                ORDER BY sent_at, agent
                """,
                [sent_before],
            ).fetchall()

    def _cursor(self, row_class: type | None = None) -> AbstractContextManager[psycopg.Cursor]:
        return db.cursor(self.database_url, row_class)
