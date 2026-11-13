"""Produkty: deset kategorií a normalizovaný klíč, podle kterého se pozná týž produkt.

Klíč tvoří značka a název (tak, jak je zdroj uvádí za sebou), objem a obsah alkoholu.
Z názvu se vynechá obsah alkoholu, slova o dárkovém balení a obalu, diakritika a velikost
písmen, takže „Jägermeister 35 % obj. dárkové balení se 2 panáky“ 0,7 l je týž produkt
jako „Jägermeister 35% obj.“ 0,7 l. Klíč používá sortimentní agent i párování akčních cen.
"""

import re
import unicodedata
from decimal import Decimal
from typing import Literal

Category = Literal[
    "rum_tuzemak",
    "vodka",
    "horke_likery",
    "ostatni_likery",
    "gin",
    "whisky",
    "brandy",
    "ovocne_destilaty",
    "rtd",
    "ostatni",
]

# Pořadí i popisky podle GLOSSARY.md (Kategorie).
CATEGORIES: dict[Category, str] = {
    "rum_tuzemak": "Rum a tuzemák",
    "vodka": "Vodka",
    "horke_likery": "Hořké likéry",
    "ostatni_likery": "Ostatní likéry",
    "gin": "Gin",
    "whisky": "Whisky",
    "brandy": "Brandy a vinné destiláty",
    "ovocne_destilaty": "Ovocné destiláty",
    "rtd": "RTD",
    "ostatni": "Ostatní",
}

_ABV = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*(?:obj|vol)?\.?,?", re.IGNORECASE)
_VOLUME = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(ml|cl|l)\s*$", re.IGNORECASE)
_UNIT_ML = {"ml": 1, "cl": 10, "l": 1000}
# Dárkové balení a obal nemění produkt: „dárkové balení se 2 panáky“, „dárkový box“, „Tuba“…
_PACKAGING = re.compile(
    r"""
    \b(?:v\s+)?dárkov\w*\s+(?:balení|box\w*|kazet\w*|krabi\w*|set\w*|tub\w*)
    | (?:\bse?|\+)\s+(?:\d+\s+)?(?:panák\w*|skleni\w*|sklenk\w*|pohár\w*|hrn\w*)
    | \btub[aě]\b
    | \bplech\w*\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_abv(text: str) -> Decimal | None:
    """Obsah alkoholu v % obj. z názvu („Božkov Originál 37,5% obj.“ → 37.5)."""
    match = _ABV.search(text)
    return Decimal(match.group(1).replace(",", ".")) if match else None


def parse_volume_ml(text: str) -> int | None:
    """Objem v ml z údaje o množství („0,7 l“ → 700, „500 ml“ → 500, „50 ks“ → None)."""
    match = _VOLUME.match(text)
    if not match:
        return None
    return round(Decimal(match.group(1).replace(",", ".")) * _UNIT_ML[match.group(2).lower()])


def product_key(name: str, volume_ml: int, abv: Decimal) -> str:
    """Normalizovaný klíč produktu: „značka a název|objem v ml|obsah alkoholu“."""
    words = _PACKAGING.sub(" ", _ABV.sub(" ", name))
    return f"{plain_text(words)}|{volume_ml}|{abv.normalize():f}"


def plain_text(text: str) -> str:
    """Text bez diakritiky, malými písmeny, jen písmena a číslice oddělené mezerou."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", ascii_text.lower()).split())
