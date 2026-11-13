"""Sdílené části stránek: šablony, formátování a připojení k databázi jen pro čtení."""

import json
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from fastapi import Request
from fastapi.templating import Jinja2Templates

from lihoviny.clock import PRAGUE

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def czech_date(value: date) -> str:
    """Datum česky: 16. 11. 2026."""
    return f"{value.day}. {value.month}. {value.year}"


def prague_time(value: datetime | None) -> str:
    if value is None:
        return ""
    local = value.astimezone(PRAGUE)
    return f"{czech_date(local)} {local.hour}:{local:%M:%S}"


def prague_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return czech_date(value.astimezone(PRAGUE))


def czech_count(count: int, one: str, few: str, many: str) -> str:
    """Počet s českým tvarem: 1 položka, 2–4 položky, 0 a 5+ položek."""
    if count == 1:
        return f"1 {one}"
    if 2 <= count <= 4:
        return f"{count} {few}"
    return f"{count} {many}"


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
templates.env.filters["pocet"] = czech_count


def db(request: Request) -> Iterator[psycopg.Connection]:
    """Závislost FastAPI: připojení přes roli jen pro čtení (`WEB_DATABASE_URL`)."""
    with psycopg.connect(request.app.state.database_url) as conn:
        yield conn
