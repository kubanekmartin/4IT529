"""Grafy Briefu jako SVG: vznikají při psaní Briefu, ukládají se s ním a web je jen vloží.

SVG je psané ručně, bez knihovny: Brief potřebuje jen pruhy, dvojice pruhů a čáry po dnech,
a knihovna jako matplotlib by do image přidala desítky MB kvůli pár obdélníkům. Vodorovné
souřadnice jsou v procentech šířky, svislé v pixelech, takže graf vyplní šířku stránky na
mobilu i na počítači a písmo zůstane stejně velké.

Barvy určují třídy (`s1`–`s4` pro řady, `grid`, `tick`, `label`, `value`); jejich hodnoty pro
světlý i tmavý režim má stránka Briefu. Text ze zdrojů prochází `escape`, protože SVG jde do
stránky přímo. Každý graf nese i tabulku dat: pro čtečky obrazovky a pro barvy řad, které
mají na světlém pozadí nízký kontrast. Najetí myší na značku ukáže hodnotu (`<title>`).
"""

import html
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"
ROW = 44  # výška řádku vodorovného pruhu: popisek nad pruhem, aby se vešel i na mobil
PAIR_ROW = 56
BAR = 12
PAIR_BAR = 10
BAR_MAX = 78  # nejdelší pruh v % šířky; zbytek patří hodnotě za pruhem
LEGEND_ROW = 18
PLOT = 140  # výška plochy čárového grafu
LABEL_LIMIT = 42


@dataclass(frozen=True)
class Chart:
    title: str
    svg: str
    columns: list[str]
    rows: list[list[str]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def bar_chart(
    title: str,
    columns: tuple[str, str],
    items: Sequence[tuple[str, float]],
    fmt: Callable[[float], str],
) -> Chart:
    """Vodorovné pruhy jedné řady: `items` jsou (popisek, hodnota)."""
    if not items:
        return _empty(title, list(columns))
    top = max(value for _, value in items)
    parts = []
    for index, (label, value) in enumerate(items):
        y = index * ROW
        width = _share(value, top)
        parts.append(
            f"<g><title>{_e(label)}: {_e(fmt(value))}</title>"
            f'<text class="label" x="0" y="{y + 14}">{_e(_short(label))}</text>'
            f"{_bar(1, y + 20, width, BAR)}"
            f'<text class="value" x="{width:.2f}%" dx="6" y="{y + 30}">{_e(fmt(value))}</text>'
            "</g>"
        )
    rows = [[label, fmt(value)] for label, value in items]
    return Chart(title, _svg(title, len(items) * ROW, parts), list(columns), rows)


def paired_bar_chart(
    title: str,
    columns: tuple[str, str, str],
    items: Sequence[tuple[str, float | None, float | None, str]],
    fmt: Callable[[float], str],
    missing: str,
) -> Chart:
    """Dva pruhy na řádek pro srovnání: `items` jsou (popisek, první, druhá, poznámka k druhé).

    Chybějící hodnota se vypíše textem `missing` místo pruhu.
    """
    if not items:
        return _empty(title, list(columns))
    top = max((v for _, *values, _ in items for v in values if v is not None), default=0)
    # Názvy řad jsou dlouhé, legenda má proto každou řadu na vlastním řádku.
    parts = [_legend(columns[1:], per_row=1)]
    legend = 2 * LEGEND_ROW + 8
    for index, (label, first, second, note) in enumerate(items):
        y = legend + index * PAIR_ROW
        parts.append(f'<text class="label" x="0" y="{y + 14}">{_e(_short(label))}</text>')
        for series, value, bar_y, tooltip in [
            (1, first, y + 20, f"{label}, {columns[1]}"),
            (2, second, y + 20 + PAIR_BAR + 2, f"{label}, {columns[2]}: {note}"),
        ]:
            text_y = bar_y + PAIR_BAR - 1
            if value is None:
                parts.append(f'<text class="tick" x="0" y="{text_y}">{_e(missing)}</text>')
                continue
            width = _share(value, top)
            parts.append(
                f"<g><title>{_e(tooltip)} {_e(fmt(value))}</title>"
                f"{_bar(series, bar_y, width, PAIR_BAR)}"
                f'<text class="value" x="{width:.2f}%" dx="6" y="{text_y}">{_e(fmt(value))}</text>'
                "</g>"
            )
    rows = [
        [
            label,
            _or(first, fmt, missing),
            _or(second, fmt, missing) + (f" ({note})" if note else ""),
        ]
        for label, first, second, note in items
    ]
    height = legend + len(items) * PAIR_ROW
    return Chart(title, _svg(title, height, parts), list(columns), rows)


def line_chart(
    title: str,
    x_column: str,
    x_labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    fmt: Callable[[float], str],
) -> Chart:
    """Čáry po bodech osy x (dny): `series` jsou (název řady, hodnota v každém bodu).

    Graf ze samých nul (žádná řada nemá v žádném bodu hodnotu) je „bez dat“ jako prázdný."""
    columns = [x_column, *(name for name, _ in series)]
    if not any(value for _, values in series for value in values):
        return _empty(title, columns)
    legend_rows = math.ceil(len(series) / 2)
    parts = [_legend([name for name, _ in series], per_row=2)]
    plot_top = legend_rows * LEGEND_ROW + 24
    plot_bottom = plot_top + PLOT
    top = _nice_max(max(max(values) for _, values in series))
    for tick in [0, top / 2, top] if top % 2 == 0 else [0, top]:
        y = _y(tick, top, plot_bottom)
        parts.append(
            f'<line class="grid" x1="0" x2="100%" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="tick" x="0" y="{y - 4:.1f}">{_e(fmt(tick))}</text>'
        )
    xs = (
        [50.0]
        if len(x_labels) == 1
        else [10 + 80 * i / (len(x_labels) - 1) for i in range(len(x_labels))]
    )
    for x, label in zip(xs, x_labels, strict=True):
        parts.append(
            f'<text class="tick" x="{x:.2f}%" y="{plot_bottom + 18}" text-anchor="middle">'
            f"{_e(label)}</text>"
        )
    for number, (name, values) in enumerate(series, start=1):
        points = [(x, _y(v, top, plot_bottom)) for x, v in zip(xs, values, strict=True)]
        for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
            parts.append(
                f'<line class="s{number}" x1="{x1:.2f}%" y1="{y1:.1f}" x2="{x2:.2f}%" '
                f'y2="{y2:.1f}" stroke-width="2" stroke-linecap="round"/>'
            )
        for (x, y), label, value in zip(points, x_labels, values, strict=True):
            parts.append(
                f'<circle class="s{number}" cx="{x:.2f}%" cy="{y:.1f}" r="4" stroke-width="2">'
                f"<title>{_e(name)}, {_e(label)}: {_e(fmt(value))}</title></circle>"
            )
    rows = [[label, *(fmt(values[i]) for _, values in series)] for i, label in enumerate(x_labels)]
    return Chart(title, _svg(title, plot_bottom + 26, parts), columns, rows)


def _empty(title: str, columns: list[str]) -> Chart:
    text = '<text class="tick" x="50%" y="24" text-anchor="middle">bez dat</text>'
    return Chart(title, _svg(title, 40, [text]), columns, [])


def _svg(title: str, height: int, parts: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="graf" width="100%" height="{height}" '
        f'role="img" aria-label="{_e(title)}" font-family="{FONT}" font-size="13">'
        + "".join(parts)
        + "</svg>"
    )


def _bar(series: int, y: float, width: float, height: int) -> str:
    """Pruh se zaobleným koncem a rovným začátkem u osy (druhý obdélník srovná začátek)."""
    if width <= 0:
        return ""
    return (
        f'<rect class="s{series}" x="0" y="{y}" width="{width:.2f}%" height="{height}" rx="4"/>'
        f'<rect class="s{series}" x="0" y="{y}" width="4" height="{height}"/>'
    )


def _legend(names: Sequence[str], per_row: int) -> str:
    """Legenda nad grafem: barevný čtverec a název řady, `per_row` řad vedle sebe."""
    items = []
    for index, name in enumerate(names):
        x, y = 100 // per_row * (index % per_row), (index // per_row) * LEGEND_ROW
        items.append(
            f'<rect class="s{index + 1}" x="{x}%" y="{y + 3}" width="10" height="10" rx="2"/>'
            f'<text class="label" x="{x}%" dx="16" y="{y + 12}">{_e(_short(name))}</text>'
        )
    return "<g>" + "".join(items) + "</g>"


def _share(value: float, top: float) -> float:
    return value / top * BAR_MAX if top > 0 else 0.0


def _y(value: float, top: float, bottom: float) -> float:
    return bottom - value / top * PLOT


def _nice_max(value: float) -> float:
    """Nejbližší „kulatá“ horní mez osy: 1, 2, 5 nebo 10 × řád."""
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    return next(m * magnitude for m in (1, 2, 5, 10) if value <= m * magnitude)


def _short(text: str, limit: int = LABEL_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _or(value: float | None, fmt: Callable[[float], str], missing: str) -> str:
    return missing if value is None else fmt(value)


def _e(text: str) -> str:
    return html.escape(text, quote=True)
