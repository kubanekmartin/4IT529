"""Grafy vývoje pro stránky Sortiment, Komunikace, Regulace, Běhy a sparklines Přehledu.

Každá funkce si přečte, co potřebuje, z tabule a vrátí graf (lihoviny.charts_extra) s textem
„Jak to číst“. Dny jsou pražské; období se počítají k poslednímu dni s daty, ne k dnešku.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

import psycopg
from psycopg.rows import dict_row

from lihoviny.agents.medialni import TOPICS
from lihoviny.charts import Chart, line_chart
from lihoviny.charts_extra import area_chart, sparkline, stacked_bar_chart, status_calendar
from lihoviny.clock import PRAGUE
from lihoviny.zadavatel import WATCHED_COMPETITORS

TOP_TOPICS = 3  # + „Ostatní“ = 4 řady, víc barev paleta nemá
CALENDAR_DAYS = 42
SPARK_DAYS = 30
COLLECTOR_LABELS = {
    "sortimentni": "Sortiment",
    "cenovy": "Akční ceny",
    "medialni": "Komunikace",
    "regulatorni": "Regulace",
}
# Fáze projednávání sněmovního tisku podle textu stavu z psp.cz, v pořadí legislativního procesu.
STAGES = [
    ("Předloženo", ()),
    ("1. čtení", ("1. čtení",)),
    ("Ve výboru", ("výbor",)),
    ("2. čtení", ("2. čtení",)),
    ("3. čtení", ("3. čtení",)),
    ("Senát", ("senát",)),
    ("Prezident", ("prezident", "podpis")),
    ("Vyhlášeno", ("vyhlášen", "sb.")),
]
IMPACTS = ["vysoký", "střední", "nízký"]


def _day(value) -> date:
    return value.astimezone(PRAGUE).date()


def _label(day: date) -> str:
    return f"{day.day}. {day.month}."


def _count(value: float) -> str:
    return f"{value:.0f}"


def novinky_cumulative(conn: psycopg.Connection) -> tuple[Chart, str]:
    """Kumulativní počet Novinek po dnech: sledovaní konkurenti zvlášť, ostatní dohromady."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            SELECT f.found_at, p.manufacturer FROM findings f
            JOIN products p ON p.id = f.product_id
            WHERE f.kiq = 1 AND f.type = 'novinka'
            """
        ).fetchall()
    names = list(WATCHED_COMPETITORS)
    if not rows:
        return line_chart("Novinky celkem v čase", "Den", [], [(n, []) for n in names], _count), ""
    first = min(_day(r["found_at"]) for r in rows)
    last = max(_day(r["found_at"]) for r in rows)
    per_day = Counter(
        (_day(r["found_at"]), r["manufacturer"]) for r in rows if r["manufacturer"] in names
    )
    days = [first + timedelta(days=d) for d in range((last - first).days + 1)]
    series = []
    for name in names:
        total, values = 0, []
        for day in days:
            total += per_day[day, name]
            values.append(total)
        series.append((name, values))
    chart = line_chart("Novinky celkem v čase", "Den", [_label(d) for d in days], series, _count)
    return chart, (
        "Kolik Novinek každý sledovaný konkurent uvedl od začátku sledování (čára jen roste; "
        "ostatní výrobci jsou na stránce Přehled). "
        "Strmá čára = konkurent rychle rozšiřuje sortiment; vodorovná = v portfoliu nic nemění."
    )


def topics_by_week(conn: psycopg.Connection) -> tuple[Chart, str]:
    """Články o lihovinách po týdnech podle tématu (nejčastější témata, zbytek „Ostatní“)."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            SELECT content->>'topic' AS topic, (content->>'published_at')::timestamptz AS at
            FROM findings WHERE kiq = 3 AND type = 'clanek' AND content ? 'published_at'
            """
        ).fetchall()
    top = [t for t, _ in Counter(r["topic"] for r in rows).most_common(TOP_TOPICS)]
    weeks: dict[date, Counter] = defaultdict(Counter)
    for row in rows:
        day = _day(row["at"])
        weeks[day - timedelta(days=day.weekday())][row["topic"] if row["topic"] in top else ""] += 1
    mondays = []
    if weeks:
        monday, last = min(weeks), max(weeks)
        while monday <= last:
            mondays.append(monday)
            monday += timedelta(days=7)
    series = [(TOPICS.get(t, t), [weeks[m][t] for m in mondays]) for t in top]
    if any(weeks[m][""] for m in mondays):
        series.append(("Ostatní", [weeks[m][""] for m in mondays]))
    chart = area_chart(
        "Témata článků po týdnech", "Týden od", [_label(m) for m in mondays], series, _count
    )
    return chart, (
        "Kolik článků o lihovinách vyšlo v každém týdnu, rozdělených podle tématu; horní hrana "
        "je počet všech. Rostoucí vrstva ukazuje téma, kterým konkurence teď komunikuje "
        "(před Vánoci typicky promo a sezónní nabídky)."
    )


def stage_of(state: str | None) -> str:
    text = (state or "").casefold()
    for name, keys in reversed(STAGES):
        if any(key in text for key in keys):
            return name
    return STAGES[0][0]


def tisky_pipeline(tisky: list[dict[str, Any]]) -> tuple[Chart, str]:
    """Sněmovní tisky podle fáze projednávání, skládané podle dopadu."""
    counts = Counter((stage_of(t.get("state")), t.get("impact_level")) for t in tisky)
    items = [(name, [counts[name, impact] for impact in IMPACTS]) for name, _ in STAGES]
    # Fáze za nejpokročilejším tiskem nic neříkají; dřívější prázdné fáze zůstanou.
    reached = max((i for i, (_, values) in enumerate(items) if sum(values)), default=0)
    items = items[: reached + 1]
    chart = stacked_bar_chart(
        "Kde ve Sněmovně tisky jsou",
        "Fáze",
        [f"dopad {impact}" for impact in IMPACTS],
        items,
        _count,
    )
    return chart, (
        "Fáze projednávání tisků od předložení po vyhlášení ve Sbírce, barevně podle odhadu "
        "dopadu. Tisk s vysokým dopadem ve 2. nebo 3. čtení je blízko schválení: jeho termín "
        "účinnosti patří do plánu promo a cen."
    )


def collection_calendar(conn: psycopg.Connection) -> tuple[Chart, str]:
    """Výsledek sběru každého agenta v každém dni (posledních CALENDAR_DAYS dní s během)."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            SELECT b.started_at, z.agent, z.outcome, z.reason FROM zadani z
            JOIN behy b ON b.id = z.beh_id
            WHERE b.kind = 'collection' AND z.outcome IS NOT NULL
            ORDER BY b.started_at
            """
        ).fetchall()
    agents = list(COLLECTOR_LABELS)
    state: dict[tuple[date, str], tuple[str, str]] = {}
    for row in rows:
        if row["agent"] in COLLECTOR_LABELS:
            ok = row["outcome"] == "INFORM"
            state[_day(row["started_at"]), row["agent"]] = (
                "ok" if ok else "fail",
                "" if ok else row["reason"] or "bez důvodu",
            )
    days: list[date] = []
    if state:
        last = max(day for day, _ in state)
        first = max(min(day for day, _ in state), last - timedelta(days=CALENDAR_DAYS - 1))
        days = [first + timedelta(days=d) for d in range((last - first).days + 1)]
    cells = [[state.get((d, a), (None, ""))[0] for d in days] for a in agents]
    notes = [[state.get((d, a), (None, ""))[1] for d in days] for a in agents]
    chart = status_calendar(
        "Spolehlivost sběru po dnech",
        [COLLECTOR_LABELS[a] for a in agents],
        days,
        cells,
        notes,
    )
    ok = sum(s == "ok" for row in cells for s in row)
    ran = sum(s is not None for row in cells for s in row)
    share = f" Za období uspělo {ok} z {ran} sběrů ({ok / ran * 100:.0f} %)." if ran else ""
    return chart, (
        "Jeden čtvereček = jeden den jednoho sběrného agenta: zelený uspěl, červený Selhání "
        "(důvod je v bublině), šedý neběžel. Červený den znamená, že grafy na stránce té KIQ "
        f"mají v ten den mezeru.{share}"
    )


def tile_sparklines(conn: psycopg.Connection, daily_offers) -> dict[str, str]:
    """Sparklines do dlaždic Přehledu (klíč = popisek dlaždice), za posledních SPARK_DAYS dní."""
    with conn.cursor(row_factory=dict_row) as cursor:
        products = [
            _day(r["first_seen_at"])
            for r in cursor.execute("SELECT first_seen_at FROM products").fetchall()
        ]
        novinky = [
            _day(r["found_at"])
            for r in cursor.execute(
                "SELECT found_at FROM findings WHERE kiq = 1 AND type = 'novinka'"
            ).fetchall()
        ]
        clanky = [
            _day(r["at"])
            for r in cursor.execute(
                """
                SELECT (content->>'published_at')::timestamptz AS at FROM findings
                WHERE kiq = 3 AND type = 'clanek' AND content ? 'published_at'
                """
            ).fetchall()
        ]
    sparks: dict[str, str] = {}
    if products:
        days = _last_days(max(products))
        sparks["sledovaných produktů"] = sparkline(
            [sum(p <= d for p in products) for d in days], "Počet produktů za posledních 30 dní"
        )
    if novinky:
        days = _last_days(max(novinky))
        per_day = Counter(novinky)
        sparks["novinek za 7 dní"] = sparkline(
            [sum(per_day[d - timedelta(days=k)] for k in range(7)) for d in days],
            "Novinky za 7 dní, vývoj za posledních 30 dní",
        )
    if daily_offers:
        recent = daily_offers[-SPARK_DAYS:]
        sparks["platných akčních cen"] = sparkline(
            [len(offers) for _, offers in recent], "Akční ceny po dnech, posledních 30 dní"
        )
        discounts = [
            median(ds) if (ds := [d for o in offers if (d := _discount(o)) is not None]) else 0
            for _, offers in recent
        ]
        sparks["průměrná sleva"] = sparkline(discounts, "Medián slevy po dnech, posledních 30 dní")
    if clanky:
        days = _last_days(max(clanky))
        per_day = Counter(clanky)
        sparks["článků za 30 dní"] = sparkline(
            [sum(per_day[d - timedelta(days=k)] for k in range(30)) for d in days],
            "Články za 30 dní, vývoj za posledních 30 dní",
        )
    return {key: value for key, value in sparks.items() if value}


def _discount(offer: dict[str, Any]) -> float | None:
    value = offer["content"].get("discount_percent")
    return float(value) if isinstance(value, int | float) else None


def _last_days(last: date) -> list[date]:
    return [last - timedelta(days=SPARK_DAYS - 1 - d) for d in range(SPARK_DAYS)]
