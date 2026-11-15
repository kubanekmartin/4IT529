"""Statistiky a vývoj akčních cen pro stránku Akční ceny: rozdělení slev, slevy po řetězcích,
cena proti slevě, řetězce × kategorie, podíl konkurentů, kalendář akcí a vývoj v čase.

Grafy „teď“ počítají z akčních cen platných k poslednímu snímku (`current_offers`), grafy
vývoje z denních snímků cenového agenta: za den platí jeho poslední snímek, stejně jako
v Briefu. Každý graf má vysvětlení „Jak to číst“ psané pro rozhodnutí zadavatele.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median, quantiles
from typing import Any

import psycopg
from psycopg.rows import dict_row

from lihoviny.charts import Chart, line_chart
from lihoviny.charts_extra import (
    band_chart,
    box_chart,
    column_chart,
    gantt_chart,
    heatmap_chart,
    scatter_chart,
    stacked_bar_chart,
)
from lihoviny.clock import PRAGUE
from lihoviny.products import CATEGORIES
from lihoviny.zadavatel import PORTFOLIO, WATCHED_COMPETITORS

DISCOUNT_BUCKETS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 101)]
TOP_CHAINS = 8
CALENDAR_DAYS = 14  # kalendář akcí: kolik dní před a po posledním snímku
OTHERS = "ostatní a nespárované"


def daily_offers(conn: psycopg.Connection) -> list[tuple[date, list[dict[str, Any]]]]:
    """Akční ceny v posledním snímku cenového agenta každého dne (pražské datum)."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            WITH last_of_day AS (
                SELECT DISTINCT ON ((taken_at AT TIME ZONE 'Europe/Prague')::date)
                       id, (taken_at AT TIME ZONE 'Europe/Prague')::date AS day
                FROM snapshots WHERE agent = 'cenovy'
                ORDER BY (taken_at AT TIME ZONE 'Europe/Prague')::date, taken_at DESC, id DESC
            )
            SELECT d.day, f.content, p.manufacturer, p.category
            FROM last_of_day d
            JOIN snapshot_findings sf ON sf.snapshot_id = d.id
            JOIN findings f ON f.id = sf.finding_id AND f.type = 'akcni_cena'
            LEFT JOIN products p ON p.id = f.product_id
            ORDER BY d.day
            """
        ).fetchall()
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[row["day"]].append(row)
    return sorted(by_day.items())


def calendar_offers(conn: psycopg.Connection, day: date) -> list[dict[str, Any]]:
    """Akční ceny s platností, která zasahuje do okna ± CALENDAR_DAYS kolem `day`."""
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """
            SELECT DISTINCT ON (f.content->>'product', f.content->>'chain',
                                f.content->>'valid_from', f.content->>'valid_until')
                   f.content, p.manufacturer
            FROM findings f LEFT JOIN products p ON p.id = f.product_id
            WHERE f.type = 'akcni_cena' AND f.content->>'valid_until' IS NOT NULL
              AND f.content->>'valid_until' >= %(start)s
              AND COALESCE(f.content->>'valid_from', f.content->>'valid_until') <= %(end)s
            """,
            {
                "start": (day - timedelta(days=CALENDAR_DAYS)).isoformat(),
                "end": (day + timedelta(days=CALENDAR_DAYS)).isoformat(),
            },
        ).fetchall()


def offer_charts(
    offers: list[dict[str, Any]],
    history: list[tuple[date, list[dict[str, Any]]]],
    calendar: list[dict[str, Any]],
    today: date | None,
    category_of,
) -> dict[str, list[tuple[Chart, str]]]:
    """Grafy stránky po skupinách: „teď“ (z platných akčních cen) a „vývoj“ (z historie)."""
    now = [
        (
            discount_histogram(offers),
            "Kolik akčních cen má jakou slevu. Čárkovaná čára je medián: polovina akcí "
            "konkurence zlevňuje méně, polovina víc. Je to měřítko pro hloubku vlastní akce: "
            "sleva pod mediánem v regálu nevynikne.",
        ),
        (
            discount_by_chain(offers),
            "Rozpětí slev v každém řetězci: krabice ukazuje prostřední polovinu akcí, svislá "
            "čárka medián, tečky jednotlivé akce. Řetězec s krabicí vpravo zlevňuje nejhlouběji; "
            "tam čekejte nejtvrdší cenový souboj, nebo vyjednejte vlastní akci jinde.",
        ),
        (
            price_vs_discount(offers),
            "Každá tečka je jedna akční cena: vodorovně sleva, svisle cena za litr. Oranžové "
            "čáry jsou běžné ceny vašich produktů za litr. Tečky pod vaší čárou jsou akce, se "
            "kterými se v regálu srovnáváte; barevné jsou sledovaní konkurenti.",
        ),
        (
            chains_by_category(offers, category_of),
            "Kolik akčních cen má který řetězec v které kategorii; tmavší buňka = víc akcí. "
            "Tmavý sloupec je kategorie, kde je konkurence v akcích nejaktivnější; prázdná "
            "buňka u vaší kategorie je řetězec, kde vaše akce nebude mít konkurenci.",
        ),
        (
            competitor_share(offers, history),
            "Podíl sledovaných konkurentů na akčních cenách teď a za celé sledované období. "
            "Když podíl některého konkurenta teď roste, tlačí víc promo než obvykle.",
        ),
        (
            promo_calendar(calendar, today),
            f"Akce po řetězcích {CALENDAR_DAYS} dní před a po posledním snímku: pruh je doba "
            "platnosti, tmavší místo je víc akcí naráz, barevně sledovaní konkurenti. Mezery "
            "v řádku řetězce jsou termíny, kdy vaše akce v něm nebude soupeřit s konkurencí.",
        ),
    ]
    trend = [
        (
            competitors_by_day(history),
            "Kolik akčních cen měl každý sledovaný konkurent v denním snímku. Rostoucí čára "
            "znamená, že konkurent přidává promo; výkyvy po týdnech kopírují letáky řetězců.",
        ),
        (
            discount_by_week(history),
            "Medián slevy po týdnech (čára) a pás, ve kterém je prostřední polovina akcí. "
            "Stoupající čára znamená, že trh zlevňuje hlouběji, a vaše akce musí být hlubší, "
            "aby obstála.",
        ),
    ]
    return {"now": now, "trend": trend}


def _discount(offer: dict[str, Any]) -> float | None:
    value = offer["content"].get("discount_percent")
    return float(value) if isinstance(value, int | float) else None


def _maker(offer: dict[str, Any]) -> str:
    maker = offer.get("manufacturer")
    return maker if maker in WATCHED_COMPETITORS else OTHERS


def _series(maker: str) -> int:
    return WATCHED_COMPETITORS.index(maker) + 1 if maker in WATCHED_COMPETITORS else 0


def _pct(value: float) -> str:
    return f"{value:.0f} %"


def _count(value: float) -> str:
    return f"{value:.0f}"


def _kc(value: float) -> str:
    return f"{value:,.0f}".replace(",", "\xa0") + " Kč"


def discount_histogram(offers: list[dict[str, Any]]) -> Chart:
    discounts = [d for o in offers if (d := _discount(o)) is not None]
    counts = [sum(low <= d < high for d in discounts) for low, high in DISCOUNT_BUCKETS]
    labels = [f"{low}–{high} %" if high <= 100 else f"{low}+ %" for low, high in DISCOUNT_BUCKETS]
    marker = None
    if discounts:
        mid = median(discounts)
        index = next(i for i, (low, high) in enumerate(DISCOUNT_BUCKETS) if low <= mid < high)
        low, high = DISCOUNT_BUCKETS[index]
        position = index + (mid - low) / (min(high, 60) - low)
        marker = (position, f"medián {mid:.0f} %")
    return column_chart(
        "Rozdělení slev akčních cen",
        ("Sleva", "Akční ceny"),
        list(zip(labels, counts, strict=True)),
        _count,
        marker=marker,
    )


def discount_by_chain(offers: list[dict[str, Any]]) -> Chart:
    by_chain: dict[str, list[float]] = defaultdict(list)
    for offer in offers:
        if (d := _discount(offer)) is not None:
            by_chain[str(offer["content"]["chain"])].append(d)
    items = sorted(by_chain.items(), key=lambda item: (-median(item[1]), item[0]))
    items = [item for item in items if len(item[1]) >= 2][:TOP_CHAINS] or items[:TOP_CHAINS]
    return box_chart("Slevy po řetězcích", "Řetězec", items, _pct)


def price_vs_discount(offers: list[dict[str, Any]]) -> Chart:
    points = []
    for offer in offers:
        litre, discount = offer["content"].get("price_per_l"), _discount(offer)
        if not isinstance(litre, int | float) or discount is None:
            continue
        maker = _maker(offer)
        label = f"{offer['content']['product']}, {offer['content']['chain']}"
        points.append((discount, float(litre), 1 if maker in WATCHED_COMPETITORS else 0, label))
    categories = {o.get("category") for o in offers}
    references = [(float(p.price_per_l), p.name) for p in PORTFOLIO if p.category in categories]
    return scatter_chart(
        "Cena za litr proti slevě",
        ("Sleva %", "Kč za litr"),
        points,
        _pct,
        _kc,
        legend=[(1, "sledovaný konkurent"), (0, "ostatní")],
        references=references,
    )


def chains_by_category(offers: list[dict[str, Any]], category_of) -> Chart:
    counts: Counter[tuple[str, str]] = Counter()
    for offer in offers:
        category = category_of(offer)
        if category is None:
            continue
        counts[str(offer["content"]["chain"]), category] += 1
    chains = Counter(chain for chain, _ in counts.elements())
    rows = [chain for chain, _ in chains.most_common(TOP_CHAINS)]
    cols = [code for code in CATEGORIES if any(c == code for _, c in counts)]
    cells = [[counts[chain, code] for code in cols] for chain in rows]
    return heatmap_chart(
        "Akční ceny: řetězce × kategorie",
        "Řetězec",
        rows,
        [CATEGORIES[code] for code in cols],
        cells,
        _count,
    )


def competitor_share(
    offers: list[dict[str, Any]], history: list[tuple[date, list[dict[str, Any]]]]
) -> Chart:
    names = [*WATCHED_COMPETITORS, OTHERS]
    now = Counter(_maker(o) for o in offers)
    rows = [("Platné teď", [now[name] for name in names])]
    if len(history) > 1:
        period = Counter(_maker(o) for _, day_offers in history for o in day_offers)
        rows.append((f"Za {len(history)} dní (akce × dny)", [period[name] for name in names]))
    return stacked_bar_chart(
        "Podíl konkurentů na akčních cenách",
        "Období",
        names,
        rows,
        _count,
        percent=True,
        series=[*range(1, len(WATCHED_COMPETITORS) + 1), 0],
    )


def promo_calendar(calendar: list[dict[str, Any]], today: date | None) -> Chart:
    items = []
    for offer in calendar:
        content = offer["content"]
        until = date.fromisoformat(content["valid_until"])
        since = date.fromisoformat(content["valid_from"]) if content.get("valid_from") else until
        if today is not None:
            since = max(since, today - timedelta(days=CALENDAR_DAYS))
            until = min(until, today + timedelta(days=CALENDAR_DAYS))
        maker = _maker(offer)
        note = content["product"] + (f" ({maker})" if maker in WATCHED_COMPETITORS else "")
        items.append((str(content["chain"]), since, until, 1 if _series(maker) else 0, note))
    counts = Counter(row for row, *_ in items)
    order = {chain: i for i, (chain, _) in enumerate(counts.most_common(TOP_CHAINS))}
    items = sorted((i for i in items if i[0] in order), key=lambda i: (order[i[0]], i[1]))
    return gantt_chart(
        "Kalendář akcí po řetězcích",
        "Řetězec",
        items,
        today=today,
        legend=[(1, "sledovaný konkurent"), (0, "ostatní")],
    )


def competitors_by_day(history: list[tuple[date, list[dict[str, Any]]]]) -> Chart:
    labels = [f"{day.day}. {day.month}." for day, _ in history]
    counts = [Counter(_maker(o) for o in day_offers) for _, day_offers in history]
    series = [(name, [c[name] for c in counts]) for name in WATCHED_COMPETITORS]
    return line_chart("Akční ceny sledovaných konkurentů v čase", "Den", labels, series, _count)


def discount_by_week(history: list[tuple[date, list[dict[str, Any]]]]) -> Chart:
    weeks: dict[date, list[float]] = defaultdict(list)
    for day, day_offers in history:
        monday = day - timedelta(days=day.weekday())
        weeks[monday] += [d for o in day_offers if (d := _discount(o)) is not None]
    labels: list[str] = []
    middle: list[float | None] = []
    low: list[float | None] = []
    high: list[float | None] = []
    for monday, values in sorted(weeks.items()):
        labels.append(f"{monday.day}. {monday.month}.")
        if not values:
            stats: tuple[float | None, ...] = (None, None, None)
        elif len(values) == 1:
            stats = (values[0], values[0], values[0])
        else:
            q1, _, q3 = quantiles(values, n=4, method="inclusive")
            stats = (median(values), q1, q3)
        middle.append(stats[0])
        low.append(stats[1])
        high.append(stats[2])
    return band_chart(
        "Sleva po týdnech: medián a prostřední polovina",
        "Týden od",
        labels,
        middle,
        low,
        high,
        _pct,
        ("medián slevy", "1.–3. kvartil"),
    )


def snapshot_day(taken_at) -> date | None:
    return taken_at.astimezone(PRAGUE).date() if taken_at else None
