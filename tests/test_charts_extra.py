"""Další druhy grafů (lihoviny.charts_extra): co nakreslí, co dá do tabulky dat a prázdný stav."""

from datetime import date

from lihoviny.charts_extra import (
    area_chart,
    band_chart,
    box_chart,
    column_chart,
    gantt_chart,
    heatmap_chart,
    scatter_chart,
    sparkline,
    stacked_bar_chart,
    status_calendar,
)
from lihoviny.web.trendy import stage_of


def pct(value):
    return f"{value:.0f} %"


def count(value):
    return f"{value:.0f}"


def test_column_chart_draws_a_column_per_bucket_and_the_median_marker():
    chart = column_chart(
        "Slevy",
        ("Sleva", "Počet"),
        [("0–10 %", 2), ("10–20 %", 0), ("20–30 %", 5)],
        count,
        marker=(2.5, "medián 25 %"),
    )

    assert chart.svg.count('<rect class="s1"') == 3
    assert "medián 25 %" in chart.svg and 'stroke-dasharray="4 3"' in chart.svg
    assert chart.rows == [["0–10 %", "2"], ["10–20 %", "0"], ["20–30 %", "5"]]


def test_box_chart_summarises_each_row_with_quartiles_and_shows_single_values():
    chart = box_chart("Slevy", "Řetězec", [("BILLA", [10, 20, 30, 40, 50]), ("Lidl", [30])], pct)

    assert chart.columns == ["Řetězec", "Počet", "Min", "1. kvartil", "Medián", "3. kvartil", "Max"]
    assert chart.rows[0] == ["BILLA", "5", "10 %", "20 %", "30 %", "40 %", "50 %"]
    assert chart.rows[1] == ["Lidl", "1", "30 %", "30 %", "30 %", "30 %", "30 %"]
    assert chart.svg.count('class="s1 dot"') == 6


def test_scatter_chart_has_points_legend_and_reference_lines():
    chart = scatter_chart(
        "Cena proti slevě",
        ("Sleva", "Kč/l"),
        [(30, 240, 1, "Božkov, Albert"), (10, 400, 0, "<b>Jiný</b>, Lidl")],
        pct,
        count,
        legend=[(1, "sledovaný"), (0, "ostatní")],
        references=[(360, "Valnovský tuzemák")],
    )

    assert chart.svg.count('class="s1 point"') == 1 and chart.svg.count('class="s0 point"') == 1
    assert 'class="reference"' in chart.svg and "Valnovský tuzemák" in chart.svg
    assert "&lt;b&gt;Jiný&lt;/b&gt;" in chart.svg and "<b>" not in chart.svg
    assert chart.rows[0] == ["Božkov, Albert", "30 %", "240"]


def test_stacked_bar_chart_in_percent_says_the_share_in_the_table():
    chart = stacked_bar_chart(
        "Podíl",
        "Období",
        ["Stock", "ostatní"],
        [("Teď", [3, 1])],
        count,
        percent=True,
        series=[1, 0],
    )

    assert chart.rows == [["Teď", "3 (75 %)", "1 (25 %)"]]
    assert 'class="s1 segment"' in chart.svg and 'class="s0 segment"' in chart.svg
    assert "100 %" in chart.svg


def test_gantt_chart_draws_periods_per_row_and_marks_today():
    chart = gantt_chart(
        "Kalendář",
        "Řetězec",
        [
            ("BILLA", date(2026, 11, 10), date(2026, 11, 16), 1, "Božkov (Stock)"),
            ("BILLA", date(2026, 11, 14), date(2026, 11, 20), 0, "Jiný"),
            ("Lidl", date(2026, 11, 12), date(2026, 11, 18), 0, "Jiný"),
        ],
        today=date(2026, 11, 16),
    )

    assert chart.svg.count('class="s1 period"') == 1 and chart.svg.count('class="s0 period"') == 2
    assert "dnes 16. 11." in chart.svg
    assert chart.rows[0] == ["BILLA", "10. 11. 2026", "16. 11. 2026", "Božkov (Stock)"]


def test_band_chart_draws_band_and_line_and_skips_missing_points():
    chart = band_chart(
        "Sleva",
        "Týden",
        ["1. 11.", "8. 11.", "15. 11."],
        [20, None, 30],
        [10, None, 25],
        [25, None, 40],
        pct,
        ("medián", "kvartily"),
    )

    assert 'class="s1 band"' in chart.svg and chart.svg.count('class="s1 line"') == 2
    assert chart.rows[1] == ["8. 11.", "—", "—"]
    assert chart.rows[2] == ["15. 11.", "30 %", "25 %–40 %"]


def test_area_chart_stacks_series_and_keeps_values_in_the_table():
    chart = area_chart(
        "Témata", "Týden", ["1. 11.", "8. 11."], [("Promo", [1, 3]), ("Trh", [2, 0])], count
    )

    assert 'class="s1 area"' in chart.svg and 'class="s2 area"' in chart.svg
    assert "celkem 3" in chart.svg
    assert chart.rows == [["1. 11.", "1", "2"], ["8. 11.", "3", "0"]]


def test_heatmap_shades_cells_and_leaves_zero_cells_empty():
    chart = heatmap_chart(
        "Řetězce × kategorie",
        "Řetězec",
        ["BILLA", "Lidl"],
        ["Vodka", "Gin"],
        [[4, 0], [1, 2]],
        count,
    )

    assert 'class="cell h5"' in chart.svg and 'class="cell h0"' in chart.svg
    assert chart.rows == [["BILLA", "4", "0"], ["Lidl", "1", "2"]]


def test_status_calendar_marks_failures_with_a_symbol_and_reason():
    days = [date(2026, 11, 14), date(2026, 11, 15)]
    chart = status_calendar(
        "Sběr", ["Akční ceny"], days, [["ok", "fail"]], [["", "akcniceny.cz vrátil 503"]]
    )

    assert 'class="cell fail"' in chart.svg and "✕" in chart.svg
    assert "Selhání (akcniceny.cz vrátil 503)" in chart.svg
    assert chart.rows == [["14. 11. 2026", "v pořádku"], ["15. 11. 2026", "Selhání"]]


def test_sparkline_needs_at_least_two_values():
    assert sparkline([3], "x") == ""
    assert sparkline([0, 0], "x") == ""
    assert 'class="spark"' in sparkline([1, 3, 2], "Vývoj")


def test_every_chart_says_bez_dat_without_data():
    for chart in [
        column_chart("a", ("x", "y"), [], count),
        box_chart("b", "x", [], pct),
        scatter_chart("c", ("x", "y"), [], pct, pct),
        stacked_bar_chart("d", "x", ["s"], [], count),
        gantt_chart("e", "x", []),
        band_chart("f", "x", ["1"], [None], [None], [None], pct, ("m", "b")),
        area_chart("g", "x", ["1"], [("s", [0])], count),
        heatmap_chart("h", "x", [], [], [], count),
        status_calendar("i", ["a"], [], [[]], [[]]),
    ]:
        assert "bez dat" in chart.svg and chart.rows == []


def test_tisk_stage_follows_the_state_text_from_psp():
    assert stage_of("1. čtení (§ 90, odst. 2) - ob. rozprava") == "1. čtení"
    assert stage_of("2. čtení - obecná rozprava") == "2. čtení"
    assert stage_of("Projednáno ve výboru") == "Ve výboru"
    assert stage_of(None) == "Předloženo"
