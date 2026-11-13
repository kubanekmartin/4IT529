"""Stránky Brief: aktuální (poslední) Brief, kterýkoli starší podle čísla a archiv Briefů.

Brief je uložený celý i s grafy (SVG), stránka ho jen vykreslí. Grafy se vkládají přímo
do stránky; jejich text při psaní Briefu prošel `escape` (lihoviny.charts).
"""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import class_row

from lihoviny.board import BRIEF_COLUMNS, Brief
from lihoviny.web.common import db, templates

router = APIRouter()


@router.get("/brief")
def current_brief(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=class_row(Brief)) as cursor:
        brief = cursor.execute(
            f"SELECT {BRIEF_COLUMNS} FROM briefs ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return templates.TemplateResponse(request, "brief.html", {"brief": brief, "latest": True})


@router.get("/brief/archiv")
def brief_archive(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=class_row(Brief)) as cursor:
        briefs = cursor.execute(
            f"SELECT {BRIEF_COLUMNS} FROM briefs ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return templates.TemplateResponse(request, "brief_archiv.html", {"briefs": briefs})


@router.get("/brief/{brief_id}")
def archived_brief(
    brief_id: int, request: Request, conn: Annotated[psycopg.Connection, Depends(db)]
):
    with conn.cursor(row_factory=class_row(Brief)) as cursor:
        brief = cursor.execute(
            f"SELECT {BRIEF_COLUMNS} FROM briefs WHERE id = %s", [brief_id]
        ).fetchone()
    if brief is None:
        raise HTTPException(status_code=404, detail="Brief neexistuje")
    newer = conn.execute(
        "SELECT 1 FROM briefs WHERE (created_at, id) > (%s, %s) LIMIT 1",
        [brief.created_at, brief.id],
    ).fetchone()
    return templates.TemplateResponse(
        request, "brief.html", {"brief": brief, "latest": newer is None}
    )
