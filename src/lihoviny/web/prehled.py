"""Stránka Přehled: dlaždice s hlavními čísly, grafy po KIQ a stav sběrných agentů.

Grafy se počítají při každém zobrazení z nálezů na tabuli (lihoviny.charts), na rozdíl
od grafů Briefu, které se ukládají s Briefem. Období („za 7 dní“) se počítají k času
posledního snímku daného agenta, ne k dnešku: když sběr vypadne, čísla ukážou stav, jaký
byl, a čas dat u dlaždice to přizná (stejně jako stránka Akční ceny).
"""

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row

from lihoviny.agents.medialni import CHANNELS, TOPICS
from lihoviny.charts import bar_chart, timeline_chart
from lihoviny.web.akcni_ceny import current_offers
from lihoviny.web.akcni_ceny_grafy import daily_offers
from lihoviny.web.common import db, prague_date, templates
from lihoviny.web.trendy import tile_sparklines
from lihoviny.zadavatel import WATCHED_COMPETITORS

router = APIRouter()

NOVINKY_PERIOD = timedelta(days=7)
CLANKY_PERIOD = timedelta(days=30)
TOP_ITEMS = 8
# Sběrní agenti v pořadí KIQ: (agent, KIQ, zdroj).
COLLECTORS = [
    ("sortimentni", "KIQ 1 Sortiment", "rohlik.cz"),
    ("cenovy", "KIQ 2 Akční ceny", "akcniceny.cz, kupi.cz"),
    ("medialni", "KIQ 3 Komunikace", "zboziaprodej.cz"),
    ("regulatorni", "KIQ 4 Regulace", "psp.cz"),
]
# Úroveň dopadu sněmovního tisku → řada v grafu; legenda je ve stejném pořadí.
IMPACT_LEVELS = {"vysoký": 1, "střední": 2, "nízký": 3}
UNKNOWN_MAKER = "neznámý výrobce"


@router.get("/prehled")
def prehled_page(request: Request, conn: Annotated[psycopg.Connection, Depends(db)]):
    with conn.cursor(row_factory=dict_row) as cursor:
        snapshots = {
            row["agent"]: row
            for row in cursor.execute(
                """
                SELECT DISTINCT ON (agent) agent, taken_at, item_count FROM snapshots
                ORDER BY agent, taken_at DESC, id DESC
                """
            ).fetchall()
        }
        product_count = cursor.execute("SELECT count(*) AS n FROM products").fetchone()["n"]
        novinky = cursor.execute(
            """
            SELECT f.found_at, p.category, p.manufacturer
            FROM findings f JOIN products p ON p.id = f.product_id
            WHERE f.kiq = 1 AND f.type = 'novinka'
            """
        ).fetchall()
        clanky = cursor.execute(
            """
            SELECT content, (content->>'published_at')::timestamptz AS published_at
            FROM findings WHERE kiq = 3 AND type = 'clanek'
            """
        ).fetchall()
        tisky = [
            row["content"]
            for row in cursor.execute(
                "SELECT content FROM findings WHERE kiq = 4 AND type = 'snemovni_tisk'"
            ).fetchall()
        ]
        replies = {
            row["sender"]: row
            for row in cursor.execute(
                """
                SELECT DISTINCT ON (sender) sender, kind, content, sent_at FROM messages
                WHERE kind IN ('INFORM', 'FAILURE') AND sender = ANY(%s)
                ORDER BY sender, sent_at DESC, id DESC
                """,
                [[agent for agent, _, _ in COLLECTORS]],
            ).fetchall()
        }
    _, offers = current_offers(conn)

    sortiment_at = _taken_at(snapshots, "sortimentni")
    medialni_at = _taken_at(snapshots, "medialni")
    recent_novinky = _since(novinky, "found_at", sortiment_at, NOVINKY_PERIOD)
    recent_clanky = _since(clanky, "published_at", medialni_at, CLANKY_PERIOD)
    discounts = [
        o["content"]["discount_percent"]
        for o in offers
        if isinstance(o["content"].get("discount_percent"), int | float)
    ]
    chains = {o["content"]["chain"] for o in offers}
    high_impact = sum(t.get("impact_level") == "vysoký" for t in tisky)

    tiles = [
        {
            "value": product_count,
            "label": "sledovaných produktů",
            "note": _as_of(sortiment_at),
            "href": "/sortiment",
        },
        {
            "value": len(recent_novinky),
            "label": "novinek za 7 dní",
            "note": f"do {prague_date(sortiment_at)}" if sortiment_at else "zatím bez dat",
            "href": "/sortiment",
        },
        {
            "value": len(offers),
            "label": "platných akčních cen",
            "note": f"u {len(chains)} řetězců" if offers else "zatím bez dat",
            "href": "/akcni-ceny",
        },
        {
            "value": f"{round(sum(discounts) / len(discounts))} %" if discounts else "—",
            "label": "průměrná sleva",
            "note": f"z {len(discounts)} akčních cen se slevou" if discounts else "bez slev",
            "href": "/akcni-ceny",
        },
        {
            "value": len(recent_clanky),
            "label": "článků za 30 dní",
            "note": f"do {prague_date(medialni_at)}" if medialni_at else "zatím bez dat",
            "href": "/komunikace",
        },
        {
            "value": len(tisky),
            "label": "sněmovních tisků o alkoholu",
            "note": f"z toho {high_impact} s vysokým dopadem",
            "href": "/regulace",
        },
    ]
    sparks = tile_sparklines(conn, daily_offers(conn))
    for tile in tiles:
        tile["spark"] = sparks.get(tile["label"], "")
    kiqs = [
        {
            "title": "KIQ 1: Kde konkurence rozšiřuje sortiment",
            "href": "/sortiment",
            "taken_at": sortiment_at,
            "source": "rohlik.cz",
            "charts": [
                (
                    novinky_by_manufacturer(novinky),
                    "Novinky podle výrobce. Barevně jsou sledovaní konkurenti "
                    f"({', '.join(WATCHED_COMPETITORS)}), šedě ostatní výrobci. "
                    "Výrobce, který uvádí hodně novinek, aktivně mění portfolio.",
                ),
            ],
        },
        {
            "title": "KIQ 2: Kdo zlevňuje a za kolik",
            "href": "/akcni-ceny",
            "taken_at": _taken_at(snapshots, "cenovy"),
            "source": "akcniceny.cz, kupi.cz",
            # Akční ceny podle řetězce a portfolio proti akcím má Brief; Přehled je neopakuje.
            "charts": [],
            "elsewhere": "Grafy akčních cen podle řetězce a vašeho portfolia proti nejlevnější "
            "akci jsou v Briefu, rozhodovací tabulka a všechny akční ceny na stránce Akční ceny.",
        },
        {
            "title": "KIQ 3: O čem a kde konkurence mluví",
            "href": "/komunikace",
            "taken_at": medialni_at,
            "source": "zboziaprodej.cz",
            "charts": [
                (
                    clanky_by(clanky, "topic", TOPICS, "Články podle tématu", "Téma"),
                    "Témata článků o lihovinách v oborovém tisku. Převaha témat jako "
                    "portfolio nebo promo ukazuje, čím konkurence podporuje prodej.",
                ),
                (
                    clanky_by(clanky, "channel", CHANNELS, "Články podle kanálu", "Kanál"),
                    "Kanál komunikace, o kterém článek píše (leták, e-shop, sociální sítě, "
                    "PR…). Pomáhá rozhodnout, kde má vlastní komunikace smysl.",
                ),
            ],
        },
        {
            "title": "KIQ 4: Co a kdy změní pravidla trhu",
            "href": "/regulace",
            "taken_at": _taken_at(snapshots, "regulatorni"),
            "source": "psp.cz",
            "charts": [
                (
                    tisky_timeline(tisky),
                    "Sněmovní tisky o alkoholu na časové ose podle odhadovaného termínu "
                    "účinnosti, s odhadem dopadu na výrobce lihovin. Změny s vysokým dopadem "
                    "a blízkým termínem patří do plánu promo a cen jako první.",
                ),
            ],
        },
    ]
    health = [
        {
            "label": label,
            "source": source,
            "snapshot": snapshots.get(agent),
            "reply": replies.get(agent),
        }
        for agent, label, source in COLLECTORS
    ]
    return templates.TemplateResponse(
        request,
        "prehled.html",
        {"tiles": tiles, "kiqs": kiqs, "health": health, "watched": WATCHED_COMPETITORS},
    )


def novinky_by_manufacturer(novinky: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(n["manufacturer"] or UNKNOWN_MAKER for n in novinky)
    return bar_chart(
        "Novinky podle výrobce",
        ("Výrobce", "Novinky"),
        _top(counts),
        _count,
        highlight=WATCHED_COMPETITORS,
        legend=("sledovaný konkurent", "ostatní výrobci"),
    ).as_dict()


def clanky_by(
    clanky: list[dict[str, Any]], field: str, names: dict[str, str], title: str, column: str
) -> dict[str, Any]:
    counts = Counter(names.get(c["content"].get(field), "Jiné") for c in clanky)
    return bar_chart(title, (column, "Články"), _top(counts), _count).as_dict()


def tisky_timeline(tisky: list[dict[str, Any]]) -> dict[str, Any]:
    items = [
        (
            f"Tisk {t.get('number')}: {t.get('title')}",
            date.fromisoformat(t["effective_date"]) if t.get("effective_date") else None,
            IMPACT_LEVELS.get(t.get("impact_level"), 3),
            f"dopad {t.get('impact_level')}",
        )
        for t in tisky
    ]
    items.sort(key=lambda item: (item[1] is None, item[1] or date.max, item[2], item[0]))
    return timeline_chart(
        "Termíny účinnosti sněmovních tisků",
        ("Tisk", "Účinnost asi od", "Odhad dopadu"),
        items,
        [f"{level} dopad" for level in IMPACT_LEVELS],
        "termín účinnosti neuveden",
    ).as_dict()


def _taken_at(snapshots: dict[str, dict[str, Any]], agent: str) -> datetime | None:
    snapshot = snapshots.get(agent)
    return snapshot["taken_at"] if snapshot else None


def _since(
    rows: list[dict[str, Any]], field: str, until: datetime | None, period: timedelta
) -> list[dict[str, Any]]:
    if until is None:
        return []
    return [row for row in rows if row[field] is not None and until - period < row[field]]


def _as_of(taken_at: datetime | None) -> str:
    return f"k {prague_date(taken_at)}" if taken_at else "zatím bez dat"


def _top(counts: Counter[str]) -> list[tuple[str, float]]:
    """Nejčastější položky (při shodě abecedně), nejvýš `TOP_ITEMS`."""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_ITEMS]


def _count(value: float) -> str:
    return f"{value:.0f}"
