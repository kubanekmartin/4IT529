"""Sdílená tabule (blackboard): snímky zdrojů a nálezy, ze kterých čte analytický agent.

Sběrný agent po stažení zdroje zapíše jeden snímek (odkazuje na surová data na disku)
a k němu nálezy. Snímky se nepřepisují; historie vzniká z jejich řady.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from lihoviny.clock import Clock
from lihoviny.fetch import FetchResult

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


_FINDING_COLUMNS = "id, kiq, agent, type, product_id, snapshot_id, source_url, content, found_at"


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
                """
                INSERT INTO snapshots (agent, source, taken_at, raw_paths, item_count)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, agent, source, taken_at, raw_paths, item_count
                """,
                [agent, source, self._clock.now(), [str(f.raw_path) for f in fetches], item_count],
            ).fetchone()

    def snapshot(self, snapshot_id: int) -> Snapshot:
        with self._connect(Snapshot) as conn:
            return conn.execute(
                """
                SELECT id, agent, source, taken_at, raw_paths, item_count
                FROM snapshots WHERE id = %s
                """,
                [snapshot_id],
            ).fetchone()

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
