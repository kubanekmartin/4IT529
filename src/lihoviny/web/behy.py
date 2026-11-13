"""Stránka Běhy: poslední běhy řídicího agenta a varování, když agent selhal nebo nic nenašel."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import class_row

from lihoviny.agents.ridici import ANSWER_LIMIT
from lihoviny.board import Run, RunTask
from lihoviny.web.common import db, prague_time, templates

router = APIRouter()

KIND_LABELS = {"collection": "Sběr", "brief": "Brief"}
_TASK_COLUMNS = (
    "run_id, agent, conversation_id, sent_at, outcome, new_findings, reason, answered_at"
)


@dataclass(frozen=True)
class TaskView:
    task: RunTask
    result: str
    tone: str  # třída CSS: ok, warning, failure, waiting


@dataclass(frozen=True)
class RunView:
    run: Run
    tasks: list[TaskView]

    @property
    def finished_at(self) -> datetime | None:
        """Čas poslední odpovědi; None, dokud některý agent ještě neodpověděl."""
        if any(view.task.outcome is None for view in self.tasks):
            return None
        return max((view.task.answered_at for view in self.tasks), default=self.run.started_at)


@router.get("/behy")
def runs_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=class_row(Run)) as cursor:
        runs = cursor.execute(
            "SELECT id, kind, scheduled_for, started_at FROM runs ORDER BY id DESC LIMIT 20"
        ).fetchall()
    with conn.cursor(row_factory=class_row(RunTask)) as cursor:
        tasks = cursor.execute(
            f"SELECT {_TASK_COLUMNS} FROM run_tasks WHERE run_id = ANY(%s) ORDER BY agent",
            [[run.id for run in runs]],
        ).fetchall()
        # Poslední běh každého agenta, ve kterém už je znám výsledek.
        latest = cursor.execute(
            f"""
            SELECT DISTINCT ON (agent) {_TASK_COLUMNS} FROM run_tasks
            WHERE outcome IS NOT NULL
            ORDER BY agent, run_id DESC
            """
        ).fetchall()
    views = [
        RunView(run, [_task_view(task) for task in tasks if task.run_id == run.id]) for run in runs
    ]
    warnings = [task for task in latest if task.outcome == "FAILURE" or task.new_findings == 0]
    return templates.TemplateResponse(
        request,
        "behy.html",
        {"runs": views, "warnings": warnings, "kind_labels": KIND_LABELS},
    )


def _task_view(task: RunTask) -> TaskView:
    if task.outcome is None:
        deadline = prague_time(task.sent_at + ANSWER_LIMIT)
        return TaskView(task, f"čeká na odpověď, nejdéle do {deadline}", "waiting")
    if task.outcome == "FAILURE":
        return TaskView(task, f"Selhání: {task.reason or 'bez důvodu'}", "failure")
    if task.new_findings is None:
        return TaskView(task, "odpověděl", "ok")
    if task.new_findings == 0:
        return TaskView(task, "0 nálezů", "warning")
    return TaskView(task, _new_findings(task.new_findings), "ok")


def _new_findings(count: int) -> str:
    if count == 1:
        return "1 nový nález"
    if 2 <= count <= 4:
        return f"{count} nové nálezy"
    return f"{count} nových nálezů"
