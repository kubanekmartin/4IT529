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

# Procento v názvu („37,5%“, „35 % obj.“), ne však „40/37,5%“ (dva údaje v jednom zápisu).
_ABV = re.compile(
    r"(?<![\d.,/])(\d+(?:[.,]\d+)?)\s*%(?P<marked>\s*(?:obj|vol|alk)\b\.?)?", re.IGNORECASE
)
# „100% Agave“ je surovina tequily, ne obsah alkoholu.
_NOT_ABV = re.compile(r"\s*ag[aá]ve\b", re.IGNORECASE)
# Objem: „0,7 l“, „500 ml“, „70cl“, „4x0,33l“ (balení více kusů).
_VOLUME = re.compile(r"(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(ml|cl|l)\b", re.IGNORECASE)
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
    """Obsah alkoholu v % obj. z názvu produktu, nebo None, když ho název jednoznačně neuvádí.

    Přednost má údaj s „obj.“, „vol.“ nebo „alk.“: „Olmeca 100% Agave 38% obj.“ → 38. Jinak
    platí hodnota do 100 % bez „agave“ za ní. Několik různých hodnot („35% … 38%“ u více druhů
    v jedné akční ceně) nebo zápis „40/37,5%“ znamená, že obsah alkoholu láhve neznáme.
    """
    marked: set[Decimal] = set()
    unmarked: set[Decimal] = set()
    for match in _ABV.finditer(text):
        value = Decimal(match.group(1).replace(",", "."))
        if not 0 < value <= 100 or _NOT_ABV.match(text, match.end()):
            continue
        (marked if match.group("marked") else unmarked).add(value)
    values = marked or unmarked
    return values.pop() if len(values) == 1 else None


def parse_volume_ml(text: str) -> int | None:
    """Objem v ml z názvu nebo údaje o množství („0,7 l“ → 700, „4x0,33l“ → 1320, „50 ks“ →
    None)."""
    match = _VOLUME.search(text)
    if not match:
        return None
    count, amount, unit = match.groups()
    ml = Decimal(amount.replace(",", ".")) * _UNIT_ML[unit.lower()] * int(count or 1)
    return round(ml)


def product_key(name: str, volume_ml: int, abv: Decimal) -> str:
    """Normalizovaný klíč produktu: „značka a název|objem v ml|obsah alkoholu“."""
    words = _PACKAGING.sub(" ", _ABV.sub(" ", name))
    return f"{plain_text(words)}|{volume_ml}|{abv.normalize():f}"


def plain_text(text: str) -> str:
    """Text bez diakritiky, malými písmeny, jen písmena a číslice oddělené mezerou."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", ascii_text.lower()).split())
