"""Další druhy grafů k `lihoviny.charts`: rozdělení, vztahy, podíly, kalendáře a vývoj.

Stejná pravidla jako v `lihoviny.charts`: ručně psané SVG, vodorovně v procentech šířky,
svisle v pixelech, barvy přes třídy `s0`–`s4`, text ze zdroje přes `_e` a ke každému grafu
tabulka dat. Plochy (pás, skládaná plocha, sparkline) potřebují souřadnice, které procenta
neumí, proto je kreslí vnořené `<svg>` s `viewBox` 0–100 a `preserveAspectRatio="none"`;
čáry v něm mají `vector-effect="non-scaling-stroke"`, aby se při roztažení nezúžily.
Heatmapy a kalendář jsou HTML mřížka (`grid`), která se na mobilu zalomí lépe než SVG.
"""

import statistics
from collections.abc import Callable, Sequence
from datetime import date

from lihoviny.charts import (
    BAR_MAX,
    LEGEND_ROW,
    ROW,
    Chart,
    _date,
    _e,
    _empty,
    _legend,
    _nice_max,
    _short,
    _svg,
)

COLUMN_PLOT = 150  # výška plochy sloupcového grafu
SCATTER_PLOT = 220
AREA_PLOT = 150
HEAT_LEVELS = 5  # odstíny heatmapy od nejsvětlejšího po nejtmavší
MAX_TICKS = 6  # nejvíc popisků na ose x, ať se nepřekrývají


def column_chart(
    title: str,
    columns: tuple[str, str],
    items: Sequence[tuple[str, float]],
    fmt: Callable[[float], str],
    *,
    marker: tuple[float, str] | None = None,
) -> Chart:
    """Svislé sloupce (histogram): `items` jsou (popisek sloupce, hodnota).

    `marker` je (poloha v jednotkách sloupců, text), např. medián: 2,5 = půlka třetího
    sloupce. Kreslí se jako svislá přerušovaná čára s popiskem."""
    if not items or not any(value for _, value in items):
        return _empty(title, list(columns))
    top = _nice_max(max(value for _, value in items))
    plot_top, plot_bottom = 20, 20 + COLUMN_PLOT
    width = 100 / len(items)
    parts = [
        f'<line class="grid" x1="0" x2="100%" y1="{plot_bottom}" y2="{plot_bottom}"/>',
        f'<text class="tick" x="0" y="{plot_top - 6}">{_e(fmt(top))}</text>',
        f'<line class="grid" x1="0" x2="100%" y1="{plot_top}" y2="{plot_top}"/>',
    ]
    for index, (label, value) in enumerate(items):
        height = value / top * COLUMN_PLOT
        x = index * width + width * 0.12
        parts.append(
            f"<g><title>{_e(label)}: {_e(fmt(value))}</title>"
            f'<rect class="s1" x="{x:.2f}%" y="{plot_bottom - height:.1f}" '
            f'width="{width * 0.76:.2f}%" height="{height:.1f}" rx="4"/>'
            f'<text class="value" x="{x + width * 0.38:.2f}%" y="{plot_bottom - height - 5:.1f}" '
            f'text-anchor="middle">{_e(fmt(value)) if value else ""}</text>'
            f'<text class="tick" x="{x + width * 0.38:.2f}%" y="{plot_bottom + 16}" '
            f'text-anchor="middle">{_e(label)}</text></g>'
        )
    if marker is not None:
        position, text = marker
        x = min(max(position * width, 0), 100)
        anchor = "end" if x > 60 else "start"
        dx = -6 if x > 60 else 6
        parts.append(
            f'<line class="marker" x1="{x:.2f}%" x2="{x:.2f}%" y1="{plot_top}" '
            f'y2="{plot_bottom}" stroke-dasharray="4 3"/>'
            f'<text class="label" x="{x:.2f}%" dx="{dx}" y="{plot_top + 14}" '
            f'text-anchor="{anchor}">{_e(text)}</text>'
        )
    rows = [[label, fmt(value)] for label, value in items]
    return Chart(title, _svg(title, plot_bottom + 26, parts), list(columns), rows)


def box_chart(
    title: str,
    label_column: str,
    items: Sequence[tuple[str, Sequence[float]]],
    fmt: Callable[[float], str],
) -> Chart:
    """Krabicový graf po řádcích: `items` jsou (popisek, hodnoty).

    Čára od minima po maximum, krabice od 1. do 3. kvartilu, svislá čárka v mediánu
    a jednotlivé hodnoty jako tečky, aby bylo vidět, z kolika čísel krabice vznikla."""
    columns = [label_column, "Počet", "Min", "1. kvartil", "Medián", "3. kvartil", "Max"]
    items = [(label, sorted(values)) for label, values in items if values]
    if not items:
        return _empty(title, columns)
    top = _nice_max(max(values[-1] for _, values in items))
    axis = 18
    parts = [
        f'<text class="tick" x="0" y="{axis - 6}">{_e(fmt(0))}</text>'
        f'<text class="tick" x="{BAR_MAX}%" y="{axis - 6}" text-anchor="end">{_e(fmt(top))}</text>'
    ]
    rows = []
    for index, (label, values) in enumerate(items):
        q1, median, q3 = _quartiles(values)
        low, high = values[0], values[-1]
        y = axis + index * ROW

        def x(value: float) -> float:
            return value / top * BAR_MAX

        summary = (
            f"{label}: {len(values)} hodnot, medián {fmt(median)}, "
            f"polovina mezi {fmt(q1)} a {fmt(q3)}, rozpětí {fmt(low)}–{fmt(high)}"
        )
        dots = "".join(
            f'<circle class="s1 dot" cx="{x(v):.2f}%" cy="{y + 27}" r="2.5"/>' for v in values
        )
        parts.append(
            f"<g><title>{_e(summary)}</title>"
            f'<text class="label" x="0" y="{y + 14}">{_e(_short(label))}</text>'
            f'<line class="whisker" x1="{x(low):.2f}%" x2="{x(high):.2f}%" '
            f'y1="{y + 27}" y2="{y + 27}"/>'
            f'<rect class="s1 box" x="{x(q1):.2f}%" y="{y + 20}" '
            f'width="{max(x(q3) - x(q1), 0.4):.2f}%" height="14" rx="3"/>'
            f'<line class="median" x1="{x(median):.2f}%" x2="{x(median):.2f}%" '
            f'y1="{y + 18}" y2="{y + 36}"/>'
            f"{dots}"
            f'<text class="value" x="{BAR_MAX}%" dx="8" y="{y + 31}">{_e(fmt(median))}</text>'
            "</g>"
        )
        rows.append([label, str(len(values)), *(fmt(v) for v in (low, q1, median, q3, high))])
    return Chart(title, _svg(title, axis + len(items) * ROW + 4, parts), columns, rows)


def scatter_chart(
    title: str,
    axes: tuple[str, str],
    points: Sequence[tuple[float, float, int, str]],
    fmt_x: Callable[[float], str],
    fmt_y: Callable[[float], str],
    *,
    legend: Sequence[tuple[int, str]] = (),
    references: Sequence[tuple[float, str]] = (),
) -> Chart:
    """Bodový graf: `points` jsou (x, y, řada, popisek); `axes` jsou názvy os x a y.

    `legend` jsou (řada, název) a `references` vodorovné čáry (y, popisek), např. běžné
    ceny zadavatele, ke kterým se body vztahují."""
    columns = ["Položka", axes[0], axes[1]]
    if not points:
        return _empty(title, columns)
    x_top = _nice_max(max(x for x, _, _, _ in points))
    y_values = [y for _, y, _, _ in points] + [y for y, _ in references]
    y_top = _nice_max(max(y_values))
    legend_rows = 1 if legend else 0
    plot_top = legend_rows * LEGEND_ROW + 22
    plot_bottom = plot_top + SCATTER_PLOT
    left = 8  # % šířky pro popisky osy y

    def px(value: float) -> float:
        return left + value / x_top * (100 - left - 2)

    def py(value: float) -> float:
        return plot_bottom - value / y_top * SCATTER_PLOT

    parts = []
    if legend:
        parts.append(_legend([name for _, name in legend], len(legend), [s for s, _ in legend]))
    for tick in (0, y_top / 2, y_top):
        y = py(tick)
        parts.append(
            f'<line class="grid" x1="{left}%" x2="100%" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="tick" x="0" y="{y + 4:.1f}">{_e(fmt_y(tick))}</text>'
        )
    for tick in (0, x_top / 2, x_top):
        anchor = "start" if tick == 0 else "end" if tick == x_top else "middle"
        parts.append(
            f'<text class="tick" x="{px(tick):.2f}%" y="{plot_bottom + 16}" '
            f'text-anchor="{anchor}">{_e(fmt_x(tick))}</text>'
        )
    parts.append(
        f'<text class="tick axis-name" x="100%" y="{plot_bottom + 32}" text-anchor="end">'
        f"{_e(axes[0])} →</text>"
        f'<text class="tick axis-name" x="0" y="{plot_top - 8}">↑ {_e(axes[1])}</text>'
    )
    for value, label in references:
        y = py(value)
        parts.append(
            f"<g><title>{_e(label)}: {_e(fmt_y(value))}</title>"
            f'<line class="reference" x1="{left}%" x2="100%" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke-dasharray="5 4"/>'
            f'<text class="tick" x="100%" y="{y - 4:.1f}" text-anchor="end">'
            f"{_e(_short(label, 30))}</text></g>"
        )
    for x, y, series, label in points:
        parts.append(
            f'<circle class="s{series} point" cx="{px(x):.2f}%" cy="{py(y):.1f}" r="5" '
            f'stroke-width="1.5"><title>{_e(label)}: {_e(axes[0])} {_e(fmt_x(x))}, '
            f"{_e(axes[1])} {_e(fmt_y(y))}</title></circle>"
        )
    rows = [[label, fmt_x(x), fmt_y(y)] for x, y, _, label in points]
    return Chart(title, _svg(title, plot_bottom + 38, parts), columns, rows)


def stacked_bar_chart(
    title: str,
    label_column: str,
    names: Sequence[str],
    items: Sequence[tuple[str, Sequence[float]]],
    fmt: Callable[[float], str],
    *,
    percent: bool = False,
    series: Sequence[int] | None = None,
) -> Chart:
    """Vodorovné skládané pruhy: `items` jsou (popisek, hodnota každé řady z `names`).

    S `percent` je každý pruh 100 % a části jsou podíly. Části dělí 2px mezera v barvě
    pozadí; hodnota je v bublině, v legendě a v tabulce, ne jen barvou."""
    columns = [label_column, *names]
    if not items or not any(sum(values) for _, values in items):
        return _empty(title, columns)
    numbers = list(series) if series is not None else list(range(1, len(names) + 1))
    per_row = min(len(names), 3)
    legend_height = -(-len(names) // per_row) * LEGEND_ROW + 8
    parts = [_legend(names, per_row, numbers)]
    top = max(sum(values) for _, values in items)
    for index, (label, values) in enumerate(items):
        total = sum(values)
        y = legend_height + index * ROW
        scale = (BAR_MAX / total if total else 0) if percent else BAR_MAX / top
        parts.append(f'<text class="label" x="0" y="{y + 14}">{_e(_short(label))}</text>')
        x = 0.0
        for number, name, value in zip(numbers, names, values, strict=True):
            if not value:
                continue
            width = value * scale
            share = f" ({value / total * 100:.0f} %)" if total else ""
            parts.append(
                f"<g><title>{_e(label)}, {_e(name)}: {_e(fmt(value))}{share}</title>"
                f'<rect class="s{number} segment" x="{x:.2f}%" y="{y + 20}" '
                f'width="{width:.2f}%" height="14" rx="3"/></g>'
            )
            x += width
        parts.append(
            f'<text class="value" x="{x:.2f}%" dx="6" y="{y + 31}">'
            f"{_e(fmt(total) if not percent else '100 %')}</text>"
        )
    rows = []
    for label, values in items:
        total = sum(values)
        cells = [
            f"{fmt(v)} ({v / total * 100:.0f} %)" if percent and total else fmt(v) for v in values
        ]
        rows.append([label, *cells])
    return Chart(title, _svg(title, legend_height + len(items) * ROW, parts), columns, rows)


def gantt_chart(
    title: str,
    rows_label: str,
    items: Sequence[tuple[str, date, date, int, str]],
    *,
    today: date | None = None,
    legend: Sequence[tuple[int, str]] = (),
) -> Chart:
    """Kalendář období: `items` jsou (řádek, od, do, řada, popisek); řádek může mít víc pruhů.

    Překrývající se pruhy jsou poloprůhledné, takže kde běží víc akcí naráz, je pruh tmavší.
    `today` přidá svislou čáru s datem."""
    columns = [rows_label, "Od", "Do", "Položka"]
    if not items:
        return _empty(title, columns)
    first = min(start for _, start, _, _, _ in items)
    last = max(end for _, _, end, _, _ in items)
    if today is not None:
        first, last = min(first, today), max(last, today)
    span = max((last - first).days + 1, 1)
    labels = list(dict.fromkeys(row for row, _, _, _, _ in items))
    parts = []
    offset = 0
    if legend:
        parts.append(_legend([name for _, name in legend], len(legend), [s for s, _ in legend]))
        offset = LEGEND_ROW + 6
    axis = offset + 18
    left = 0.0

    def x(day: date) -> float:
        return left + (day - first).days / span * 100

    for tick in _date_ticks(first, last):
        parts.append(
            f'<line class="grid" x1="{x(tick):.2f}%" x2="{x(tick):.2f}%" y1="{axis}" '
            f'y2="{axis + 6 + len(labels) * ROW}"/>'
            f'<text class="tick" x="{x(tick):.2f}%" y="{axis - 4}" '
            f'text-anchor="{"end" if x(tick) > 85 else "start"}">{tick.day}. {tick.month}.</text>'
        )
    for index, label in enumerate(labels):
        y = axis + 6 + index * ROW
        parts.append(f'<text class="label" x="0" y="{y + 14}">{_e(_short(label))}</text>')
        for row, start, end, series, note in items:
            if row != label:
                continue
            width = max((end - start).days + 1, 1) / span * 100
            parts.append(
                f'<rect class="s{series} period" x="{x(start):.2f}%" y="{y + 20}" '
                f'width="{width:.2f}%" height="14" rx="3"><title>{_e(label)}: {_e(note)}, '
                f"{_e(_date(start))} – {_e(_date(end))}</title></rect>"
            )
    height = axis + 10 + len(labels) * ROW
    if today is not None:
        parts.append(
            f'<line class="marker" x1="{x(today):.2f}%" x2="{x(today):.2f}%" y1="{axis}" '
            f'y2="{height - 4}" stroke-dasharray="4 3"/>'
            f'<text class="label" x="{x(today):.2f}%" dx="4" y="{height + 10}">'
            f"dnes {today.day}. {today.month}.</text>"
        )
        height += 14
    rows = [[row, _date(start), _date(end), note] for row, start, end, _, note in items]
    return Chart(title, _svg(title, height, parts), columns, rows)


def band_chart(
    title: str,
    x_column: str,
    x_labels: Sequence[str],
    middle: Sequence[float | None],
    low: Sequence[float | None],
    high: Sequence[float | None],
    fmt: Callable[[float], str],
    names: tuple[str, str],
) -> Chart:
    """Čára (např. medián) s pásem kolem ní (např. 1.–3. kvartil) po bodech osy x.

    `names` jsou (název čáry, název pásu) pro legendu a tabulku. Bod bez hodnoty čáru
    přeruší."""
    columns = [x_column, names[0], names[1]]
    known = [v for v in [*middle, *high] if v is not None]
    if not known:
        return _empty(title, columns)
    top = _nice_max(max(known))
    plot_top = LEGEND_ROW + 26
    parts = [
        '<g><rect class="s1 band" x="0" y="3" width="10" height="10" rx="2"/>'
        f'<text class="label" x="0" dx="16" y="12">{_e(names[1])}</text>'
        '<rect class="s1" x="50%" y="7" width="12" height="2"/>'
        f'<text class="label" x="50%" dx="18" y="12">{_e(names[0])}</text></g>'
    ]
    parts += _y_grid(top, plot_top, AREA_PLOT, fmt)
    n = len(x_labels)
    xs = [50.0] if n == 1 else [100 * i / (n - 1) for i in range(n)]
    upper = [(x, h) for x, h in zip(xs, high, strict=True) if h is not None]
    lower = [(x, lo) for x, lo in zip(xs, low, strict=True) if lo is not None]
    inner = []
    if upper and lower:
        points = [*upper, *reversed(lower)]
        inner.append(f'<polygon class="s1 band" points="{_points(points, top)}"/>')
    for segment in _segments(xs, middle):
        inner.append(
            f'<polyline class="s1 line" points="{_points(segment, top)}" fill="none" '
            f'stroke-width="2" vector-effect="non-scaling-stroke"/>'
        )
    parts.append(_stretched(plot_top, AREA_PLOT, inner))
    parts += _x_labels(xs, x_labels, plot_top + AREA_PLOT + 16)
    for x, label, value, lo, hi in zip(xs, x_labels, middle, low, high, strict=True):
        if value is None:
            continue
        tip = f"{label}: {names[0]} {fmt(value)}"
        if lo is not None and hi is not None:
            tip += f", {names[1]} {fmt(lo)}–{fmt(hi)}"
        y = plot_top + AREA_PLOT - value / top * AREA_PLOT
        parts.append(
            f'<circle class="hit" cx="{x:.2f}%" cy="{y:.1f}" r="8"><title>{_e(tip)}</title>'
            "</circle>"
        )
    rows = [
        [
            label,
            fmt(value) if value is not None else "—",
            f"{fmt(lo)}–{fmt(hi)}" if lo is not None and hi is not None else "—",
        ]
        for label, value, lo, hi in zip(x_labels, middle, low, high, strict=True)
    ]
    return Chart(title, _svg(title, plot_top + AREA_PLOT + 26, parts), columns, rows)


def area_chart(
    title: str,
    x_column: str,
    x_labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    fmt: Callable[[float], str],
) -> Chart:
    """Skládaná plocha po bodech osy x: `series` jsou (název, hodnota v každém bodu).

    Řady se kladou na sebe, horní hrana je součet; řady jsou v legendě i v tabulce."""
    columns = [x_column, *(name for name, _ in series)]
    totals = [sum(values[i] for _, values in series) for i in range(len(x_labels))]
    if not any(totals):
        return _empty(title, columns)
    top = _nice_max(max(totals))
    per_row = min(len(series), 3)
    plot_top = -(-len(series) // per_row) * LEGEND_ROW + 24
    parts = [_legend([name for name, _ in series], per_row)]
    parts += _y_grid(top, plot_top, AREA_PLOT, fmt)
    n = len(x_labels)
    xs = [0.0, 100.0] if n == 1 else [100 * i / (n - 1) for i in range(n)]
    base = [0.0] * n
    inner = []
    for number, (_, values) in enumerate(series, start=1):
        values = list(values) * (2 if n == 1 else 1)
        bottom = base * (2 if n == 1 else 1)
        upper = [b + v for b, v in zip(bottom, values, strict=True)]
        points = [*zip(xs, upper, strict=True), *reversed(list(zip(xs, bottom, strict=True)))]
        inner.append(
            f'<polygon class="s{number} area" points="{_points(points, top)}" '
            'stroke-width="1.5" vector-effect="non-scaling-stroke"/>'
        )
        base = upper[:n]
    parts.append(_stretched(plot_top, AREA_PLOT, inner))
    label_xs = [50.0] if n == 1 else xs
    parts += _x_labels(label_xs, x_labels, plot_top + AREA_PLOT + 16)
    for x, label, total, i in zip(label_xs, x_labels, totals, range(n), strict=True):
        detail = ", ".join(f"{name} {fmt(values[i])}" for name, values in series if values[i])
        y = plot_top + AREA_PLOT - total / top * AREA_PLOT
        parts.append(
            f'<rect class="hit" x="{x - 2:.2f}%" y="{plot_top}" width="4%" height="{AREA_PLOT}">'
            f"<title>{_e(label)}: celkem {_e(fmt(total))}"
            f"{': ' + _e(detail) if detail else ''}</title></rect>"
            f'<circle class="s0 dot" cx="{x:.2f}%" cy="{y:.1f}" r="2"/>'
        )
    rows = [[label, *(fmt(values[i]) for _, values in series)] for i, label in enumerate(x_labels)]
    return Chart(title, _svg(title, plot_top + AREA_PLOT + 26, parts), columns, rows)


def heatmap_chart(
    title: str,
    corner: str,
    row_labels: Sequence[str],
    col_labels: Sequence[str],
    cells: Sequence[Sequence[float]],
    fmt: Callable[[float], str],
) -> Chart:
    """Heatmapa jako HTML mřížka: řádky × sloupce, tmavší buňka = vyšší hodnota.

    Jeden odstín od světlého po tmavý (sekvenční škála); číslo je v buňce i v tabulce,
    prázdná (nulová) buňka je bez čísla."""
    columns = [corner, *col_labels]
    if not row_labels or not col_labels or not any(v for row in cells for v in row):
        return _empty(title, columns)
    top = max(v for row in cells for v in row)
    html = [
        f'<div class="heatmap" role="img" aria-label="{_e(title)}" '
        f'style="--cols: {len(col_labels)}">',
        '<span class="corner"></span>',
        *(f'<span class="col" title="{_e(c)}">{_e(_short(c, 14))}</span>' for c in col_labels),
    ]
    for label, row in zip(row_labels, cells, strict=True):
        html.append(f'<span class="row" title="{_e(label)}">{_e(_short(label, 22))}</span>')
        for col, value in zip(col_labels, row, strict=True):
            level = 0 if not value else 1 + round((value / top) * (HEAT_LEVELS - 1))
            html.append(
                f'<span class="cell h{level}" title="{_e(label)}, {_e(col)}: {_e(fmt(value))}">'
                f"{_e(fmt(value)) if value else ''}</span>"
            )
    html.append("</div>")
    rows = [[label, *(fmt(v) for v in row)] for label, row in zip(row_labels, cells, strict=True)]
    return Chart(title, "".join(html), columns, rows)


def status_calendar(
    title: str,
    row_labels: Sequence[str],
    days: Sequence[date],
    cells: Sequence[Sequence[str | None]],
    notes: Sequence[Sequence[str]],
) -> Chart:
    """Kalendář stavů: řádek na agenta, buňka na den; stav je „ok“, „fail“ nebo None.

    Stav nese i symbol (✓, ✕, ·) a bublina s poznámkou, ne jen barva."""
    columns = ["Den", *row_labels]
    if not days:
        return _empty(title, columns)
    symbols = {"ok": "", "fail": "✕", None: ""}
    words = {"ok": "v pořádku", "fail": "Selhání", None: "neběželo"}
    html = [
        f'<div class="calendar" role="img" aria-label="{_e(title)}" style="--days: {len(days)}">',
        '<div class="calendar-legend"><span class="cell ok"></span> v pořádku '
        '<span class="cell fail">✕</span> Selhání <span class="cell none"></span> '
        "neběželo</div>",
    ]
    for label, row, row_notes in zip(row_labels, cells, notes, strict=True):
        html.append(
            f'<div class="calendar-row"><span class="row">{_e(label)}</span><span class="days">'
        )
        for day, state, note in zip(days, row, row_notes, strict=True):
            tip = f"{label}, {_date(day)}: {words[state]}" + (f" ({note})" if note else "")
            html.append(
                f'<span class="cell {state or "none"}" title="{_e(tip)}">{symbols[state]}</span>'
            )
        html.append("</span></div>")
    html.append(
        f'<div class="calendar-axis"><span>{_e(_date(days[0]))}</span>'
        f"<span>{_e(_date(days[-1]))}</span></div></div>"
    )
    rows = [
        [_date(day), *(words[cells[r][i]] for r in range(len(row_labels)))]
        for i, day in enumerate(days)
    ]
    return Chart(title, "".join(html), columns, rows)


def sparkline(values: Sequence[float], label: str) -> str:
    """Malá čára vývoje do dlaždice, bez os; poslední bod je zvýrazněný."""
    if len(values) < 2 or not any(values):
        return ""
    top = max(values) or 1
    n = len(values)
    points = [(100 * i / (n - 1), v) for i, v in enumerate(values)]
    area = [(0.0, 0.0), *points, (100.0, 0.0)]
    last_x, last_y = points[-1]
    return (
        f'<svg class="spark" width="100%" height="32" role="img" aria-label="{_e(label)}">'
        f"<title>{_e(label)}</title>"
        '<svg viewBox="0 0 100 100" preserveAspectRatio="none" width="100%" height="100%">'
        f'<polygon class="spark-area" points="{_points(area, top, pad=8)}"/>'
        f'<polyline class="spark-line" points="{_points(points, top, pad=8)}" fill="none" '
        'stroke-width="2" vector-effect="non-scaling-stroke"/></svg>'
        f'<circle class="spark-dot" cx="{last_x:.2f}%" cy="{_spark_y(last_y, top):.1f}" r="3"/>'
        "</svg>"
    )


def _spark_y(value: float, top: float) -> float:
    return 32 - 3 - value / top * (32 - 6)


def _quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    if len(values) == 1:
        return values[0], values[0], values[0]
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, median, q3


def _points(points: Sequence[tuple[float, float]], top: float, pad: float = 0) -> str:
    """Body pro vnořené SVG s viewBox 0 0 100 100: y 0 dole, `top` nahoře."""
    scale = 100 - 2 * pad
    return " ".join(f"{x:.2f},{100 - pad - y / top * scale:.2f}" for x, y in points)


def _stretched(top: float, height: float, inner: Sequence[str]) -> str:
    return (
        f'<svg x="0" y="{top}" width="100%" height="{height}" viewBox="0 0 100 100" '
        f'preserveAspectRatio="none" overflow="visible">{"".join(inner)}</svg>'
    )


def _segments(
    xs: Sequence[float], values: Sequence[float | None]
) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = [[]]
    for x, value in zip(xs, values, strict=True):
        if value is None:
            segments.append([])
        else:
            segments[-1].append((x, value))
    return [s for s in segments if s]


def _y_grid(top: float, plot_top: float, height: float, fmt: Callable[[float], str]) -> list[str]:
    parts = []
    for tick in (0, top / 2, top):
        y = plot_top + height - tick / top * height
        parts.append(
            f'<line class="grid" x1="0" x2="100%" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="tick" x="0" y="{y - 4:.1f}">{_e(fmt(tick))}</text>'
        )
    return parts


def _x_labels(xs: Sequence[float], labels: Sequence[str], y: float) -> list[str]:
    """Popisky osy x: nejvýš MAX_TICKS rovnoměrně, vždy první a poslední."""
    n = len(labels)
    step = max(1, -(-n // MAX_TICKS))
    shown = {*range(0, n, step), n - 1}
    before_last = (n - 1) // step * step
    if before_last != n - 1 and n - 1 - before_last < step / 2:
        shown.discard(before_last)  # předposlední by se s posledním překryl
    shown = sorted(shown | {0})
    parts = []
    for i in shown:
        anchor = "start" if xs[i] < 5 else "end" if xs[i] > 95 else "middle"
        parts.append(
            f'<text class="tick" x="{xs[i]:.2f}%" y="{y}" text-anchor="{anchor}">'
            f"{_e(labels[i])}</text>"
        )
    return parts


def _date_ticks(first: date, last: date) -> list[date]:
    span = (last - first).days
    if span <= 0:
        return [first]
    step = max(1, -(-span // MAX_TICKS))
    ticks = [date.fromordinal(first.toordinal() + d) for d in range(0, span + 1, step)]
    return ticks
