"""Sdílené části stránek: šablony, formátování a připojení k databázi jen pro čtení."""

import json
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
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


def prague_date(value: datetime | None) -> str:
    if value is None:
        return ""
    local = value.astimezone(PRAGUE)
    return f"{local.day}. {local.month}. {local.year}"


def pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def volume(ml: int) -> str:
    """Objem v litrech s desetinnou čárkou: 700 → „0,7 l“."""
    return f"{(Decimal(ml) / 1000).normalize():f}".replace(".", ",") + " l"


def percent(value: Decimal | float) -> str:
    """Obsah alkoholu: 37.5 → „37,5 %“, 40 → „40 %“."""
    return f"{Decimal(str(value)).normalize():f}".replace(".", ",") + " %"


templates.env.filters["cas"] = prague_time
templates.env.filters["datum"] = prague_date
templates.env.filters["json"] = pretty_json
templates.env.filters["objem"] = volume
templates.env.filters["procenta"] = percent


def db(request: Request) -> Iterator[psycopg.Connection]:
    """Závislost FastAPI: připojení přes roli jen pro čtení (`WEB_DATABASE_URL`)."""
    with psycopg.connect(request.app.state.database_url) as conn:
        yield conn
