"""Sdílené části stránek: šablony, formátování a připojení k databázi jen pro čtení."""

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Request
from fastapi.templating import Jinja2Templates

PRAGUE = ZoneInfo("Europe/Prague")

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def prague_time(value: datetime | None) -> str:
    if value is None:
        return ""
    local = value.astimezone(PRAGUE)
    return f"{local.day}. {local.month}. {local.year} {local.hour}:{local:%M:%S}"


def pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


templates.env.filters["cas"] = prague_time
templates.env.filters["json"] = pretty_json


def db(request: Request) -> Iterator[psycopg.Connection]:
    """Závislost FastAPI: připojení přes roli jen pro čtení (`WEB_DATABASE_URL`)."""
    with psycopg.connect(request.app.state.database_url) as conn:
        yield conn
