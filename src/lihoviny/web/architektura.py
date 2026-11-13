"""Stránka Architektura: agenti, jejich role, zdroje a komunikace podle ADR 0001.

Text i diagram jsou v šabloně; stránka nic nečte z databáze, živý důkaz je log komunikace.
"""

from fastapi import APIRouter, Request

from lihoviny.web.common import templates

router = APIRouter()


@router.get("/architektura")
def architektura_page(request: Request):
    return templates.TemplateResponse(request, "architektura.html", {})
