"""Grafy jako SVG (lihoviny.charts): zvýrazněné pruhy a časová osa."""

from datetime import date

from lihoviny.charts import bar_chart, timeline_chart


def count(value):
    return f"{value:.0f}"


def test_bar_chart_without_highlight_has_one_series_and_no_legend():
    chart = bar_chart("Novinky", ("Výrobce", "Novinky"), [("Stock", 3), ("Metelka", 1)], count)

    assert 'class="s0"' not in chart.svg
    assert chart.svg.count('class="s1"') == 4  # dva pruhy po dvou obdélnících
    assert chart.rows == [["Stock", "3"], ["Metelka", "1"]]


def test_highlighted_bars_are_coloured_others_grey_and_the_table_says_which():
    chart = bar_chart(
        "Novinky",
        ("Výrobce", "Novinky"),
        [("Stock", 3), ("Metelka", 1)],
        count,
        highlight=["Stock"],
        legend=("sledovaný", "ostatní"),
    )

    assert ">sledovaný</text>" in chart.svg and ">ostatní</text>" in chart.svg
    stock, metelka = chart.svg.split("<g><title>")[1:]
    assert 'class="s1"' in stock and 'class="s0"' not in stock
    assert 'class="s0"' in metelka and 'class="s1"' not in metelka
    assert chart.rows == [["Stock (sledovaný)", "3"], ["Metelka", "1"]]


def test_timeline_places_items_between_first_and_last_date_with_note_and_missing_date():
    chart = timeline_chart(
        "Účinnost",
        ("Tisk", "Od", "Dopad"),
        [
            ("Tisk 1", date(2027, 1, 1), 1, "dopad vysoký"),
            ("Tisk 2", date(2028, 1, 1), 2, "dopad střední"),
            ("Tisk 3", None, 3, "dopad nízký"),
        ],
        ["vysoký", "střední", "nízký"],
        "neuvedeno",
    )

    assert ">1. 1. 2027</text>" in chart.svg  # osa od nejdřívějšího
    assert ">1. 1. 2028</text>" in chart.svg  # po nejpozdější datum
    first, second = chart.svg.split("<g><title>")[1:3]
    assert 'cx="0.00%"' in first and 'class="s1"' in first and "dopad vysoký" in first
    assert 'cx="78.00%"' in second and 'class="s2"' in second
    assert 'text-anchor="end"' in second  # text u pravého okraje je před tečkou
    assert ">neuvedeno</text>" in chart.svg
    assert chart.rows == [
        ["Tisk 1", "1. 1. 2027", "dopad vysoký"],
        ["Tisk 2", "1. 1. 2028", "dopad střední"],
        ["Tisk 3", "neuvedeno", "dopad nízký"],
    ]


def test_timeline_escapes_source_text():
    chart = timeline_chart(
        "Účinnost", ("Tisk", "Od", "Dopad"), [("<b>tisk</b>", date(2027, 1, 1), 1, "")], ["x"], "-"
    )

    assert "<b>" not in chart.svg
    assert "&lt;b&gt;tisk&lt;/b&gt;" in chart.svg


def test_empty_charts_say_bez_dat():
    assert "bez dat" in bar_chart("A", ("a", "b"), [], count).svg
    assert "bez dat" in timeline_chart("A", ("a", "b", "c"), [], ["x"], "-").svg
