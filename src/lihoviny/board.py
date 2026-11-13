"""Sdílená tabule (blackboard): snímky zdrojů a nálezy, ze kterých čte analytický agent.

Sběrný agent po stažení zdroje zapíše jeden snímek (odkazuje na surová data na disku)
a k němu nálezy. Snímky se nepřepisují; historie vzniká z jejich řady.

Řídicí agent sem zapisuje běhy: komu v běhu poslal Zadání a jak každé dopadlo.
"""

import uuid
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
RunKind = Literal["collection", "brief"]


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


@dataclass(frozen=True)
class Run:
    id: int
    kind: RunKind
    scheduled_for: datetime | None
    started_at: datetime


_RUN_COLUMNS = "id, kind, scheduled_for, started_at"


@dataclass(frozen=True)
class RunTask:
    """Zadání v běhu; `outcome` None = agent ještě neodpověděl."""

    run_id: int
    agent: str
    conversation_id: uuid.UUID
    sent_at: datetime
    outcome: Literal["INFORM", "FAILURE"] | None
    new_findings: int | None
    reason: str | None
    answered_at: datetime | None


_RUN_TASK_COLUMNS = (
    "run_id, agent, conversation_id, sent_at, outcome, new_findings, reason, answered_at"
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

    def start_run(self, kind: RunKind, *, scheduled_for: datetime | None = None) -> Run | None:
        """Zahájí běh. Termín rozvrhu, za který už běh proběhl, vrátí None."""
        with self._connect(Run) as conn:
            # NOT EXISTS místo samotného ON CONFLICT: to by při každém pokusu spotřebovalo
            # číslo běhu, a řídicí agent se na termín ptá každých 30 s.
            return conn.execute(
                f"""
                INSERT INTO runs (kind, scheduled_for, started_at)
                SELECT %(kind)s, %(scheduled_for)s, %(now)s
                WHERE NOT EXISTS (
                    SELECT 1 FROM runs WHERE kind = %(kind)s AND scheduled_for = %(scheduled_for)s
                )
                ON CONFLICT (kind, scheduled_for) DO NOTHING
                RETURNING {_RUN_COLUMNS}
                """,
                {"kind": kind, "scheduled_for": scheduled_for, "now": self._clock.now()},
            ).fetchone()

    def add_run_task(self, *, run_id: int, agent: str, conversation_id: uuid.UUID) -> None:
        """Zapíše Zadání do běhu; od této chvíle běží agentovi limit na odpověď."""
        with psycopg.connect(self._database_url) as conn:
            conn.execute(
                """
                INSERT INTO run_tasks (run_id, agent, conversation_id, sent_at)
                VALUES (%s, %s, %s, %s)
                """,
                [run_id, agent, conversation_id, self._clock.now()],
            )

    def record_outcome(
        self,
        conversation_id: uuid.UUID,
        outcome: Literal["INFORM", "FAILURE"],
        *,
        new_findings: int | None = None,
        reason: str | None = None,
    ) -> RunTask | None:
        """Zapíše, jak Zadání dopadlo. Platí první výsledek; jinak (a mimo běh) vrátí None."""
        with self._connect(RunTask) as conn:
            return conn.execute(
                f"""
                UPDATE run_tasks
                SET outcome = %s, new_findings = %s, reason = %s, answered_at = %s
                WHERE conversation_id = %s AND outcome IS NULL
                RETURNING {_RUN_TASK_COLUMNS}
                """,
                [outcome, new_findings, reason, self._clock.now(), conversation_id],
            ).fetchone()

    def latest_run(self, kind: RunKind) -> Run | None:
        with self._connect(Run) as conn:
            return conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM runs WHERE kind = %s ORDER BY id DESC LIMIT 1",
                [kind],
            ).fetchone()

    def run_tasks(self, run_id: int) -> list[RunTask]:
        with self._connect(RunTask) as conn:
            return conn.execute(
                f"SELECT {_RUN_TASK_COLUMNS} FROM run_tasks WHERE run_id = %s ORDER BY agent",
                [run_id],
            ).fetchall()

    def waiting_tasks(self, *, sent_before: datetime) -> list[RunTask]:
        with self._connect(RunTask) as conn:
            return conn.execute(
                f"""
                SELECT {_RUN_TASK_COLUMNS} FROM run_tasks
                WHERE outcome IS NULL AND sent_at <= %s
                ORDER BY sent_at, agent
                """,
                [sent_before],
            ).fetchall()

    def _connect(self, row_class: type) -> psycopg.Connection:
        return psycopg.connect(self._database_url, row_factory=class_row(row_class))
