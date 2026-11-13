"""Párování akčních cen na produkty ze sortimentu; používá ho cenový agent.

Akční cena je spárovaná, když se ví, kterého produktu ze sortimentu (tabulka produktů,
kterou plní sortimentní agent z rohlik.cz) se týká. Párují se názvy, jak je píše zdroj
akčních cen: všechny akční ceny se stejným názvem patří k témuž produktu.

1. Normalizovaný klíč (`lihoviny.products.product_key`): značka a název, objem a obsah
   alkoholu. Akční ceny mají objem i v názvu, proto se z názvu nejdřív vynechá. Když název
   akční ceny obsah alkoholu neuvádí (u akcniceny.cz většina), stačí shoda názvu a objemu
   s jediným produktem. Dárkové balení stejného objemu má týž klíč, takže je týž produkt.
2. Zbytek posoudí LLM, ale jen u akčních cen, které mají kandidáty: produkty stejné značky
   (některé její slovo je v názvu akční ceny), se stejným objemem a obsahem alkoholu, pokud
   je název uvádí. Dvojici, kterou LLM jednou odmítl, už znovu neposuzuje.

LLM je jen záloha: když selže, akční ceny zůstanou nespárované a zkusí se při dalším Zadání.
"""

import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Literal

from pydantic import BaseModel, Field

from lihoviny.board import Product
from lihoviny.llm import Llm
from lihoviny.products import name_key, parse_abv, parse_volume_ml, plain_text, remove_volume

log = logging.getLogger(__name__)

PairedBy = Literal["key", "llm"]
# Akčních cen v jednom dotazu na LLM, aby odpověď stihla časový limit jednoho volání
# (`lihoviny.llm.TIMEOUT`) i při prvním párování stovek akčních cen.
LLM_BATCH = 25


@dataclass(frozen=True)
class Pairing:
    product: Product
    paired_by: PairedBy


@dataclass(frozen=True)
class PairingResult:
    pairings: dict[str, Pairing]  # název akční ceny → párování
    # Dvojice (název akční ceny, ID produktu), které LLM v tomto párování odmítl.
    rejected: set[tuple[str, int]]


class OfferMatch(BaseModel):
    offer: str = Field(description="ID akční ceny ze vstupu")
    product: str | None = Field(
        description="ID kandidáta, který je týž produkt; null, když žádný není"
    )


class OfferMatches(BaseModel):
    offers: list[OfferMatch]


def pair(
    names: Collection[str],
    products: Sequence[Product],
    *,
    rejected: set[tuple[str, int]],
    llm: Llm,
) -> PairingResult:
    """Spáruje názvy akčních cen s produkty: nejdřív klíčem, zbytek přes LLM.

    `rejected` jsou dvojice, které LLM odmítl dřív; na akční cenu, která nemá žádného jiného
    kandidáta, se LLM neptá.
    """
    pairings = _pair_by_key(names, products)
    questions = []
    for name in sorted(set(names) - pairings.keys()):
        candidates = _candidates(name, products)
        if any((name, product.id) not in rejected for product in candidates):
            questions.append((name, candidates))
    newly_rejected: set[tuple[str, int]] = set()
    for batch in batched(questions, LLM_BATCH, strict=False):
        prompt = _prompt(batch)
        try:
            answer = llm.ask(prompt, OfferMatches, effort="low", system=_SYSTEM)
        except Exception:
            # Jakákoli chyba LLM (odmítnutí, výpadek API, chybějící klíč) nesmí zastavit sběr
            # akčních cen: zbytek zůstane nespárovaný a zkusí se při dalším Zadání.
            log.exception("párování přes LLM selhalo, %s názvů zůstává nespárovaných", len(batch))
            break
        chosen = {match.offer: match.product for match in answer.offers}
        for offer_id, (name, candidates) in enumerate(batch, start=1):
            if str(offer_id) not in chosen:
                continue  # LLM akční cenu vynechal: zkusí se příště
            by_id = {str(product.id): product for product in candidates}
            product_id = chosen[str(offer_id)]
            if product_id is None:
                newly_rejected |= {(name, product.id) for product in candidates}
            elif product_id in by_id:
                pairings[name] = Pairing(by_id[product_id], "llm")
    return PairingResult(pairings, newly_rejected)


def _pair_by_key(names: Collection[str], products: Sequence[Product]) -> dict[str, Pairing]:
    """Názvy akčních cen, ke kterým normalizovaný klíč najde jediný produkt → párování."""
    by_name_volume: dict[tuple[str, int], list[Product]] = {}
    for product in products:
        key = (name_key(remove_volume(product.name)), product.volume_ml)
        by_name_volume.setdefault(key, []).append(product)

    pairings = {}
    for name in names:
        volume_ml = parse_volume_ml(name)
        if volume_ml is None:
            continue
        same_name = by_name_volume.get((name_key(remove_volume(name)), volume_ml), [])
        abv = parse_abv(name)
        matches = [p for p in same_name if abv is None or p.abv == abv]
        if len(matches) == 1:
            pairings[name] = Pairing(matches[0], "key")
    return pairings


def _candidates(name: str, products: Sequence[Product]) -> list[Product]:
    """Produkty, které by mohly být touž lihovinou: značka v názvu, objem a obsah alkoholu."""
    volume_ml = parse_volume_ml(name)
    abv = parse_abv(name)
    words = set(name_key(remove_volume(name)).split())
    return [
        product
        for product in products
        if (volume_ml is None or product.volume_ml == volume_ml)
        and (abv is None or product.abv == abv)
        and _brand_words(product.brand) & words
    ]


def _brand_words(brand: str) -> set[str]:
    """Slova značky, podle kterých ji poznat v názvu: „R. Jelínek“ → {„jelinek“}."""
    words = plain_text(brand).split()
    return {word for word in words if len(word) >= 3} or set(words)


def _prompt(batch: Sequence[tuple[str, list[Product]]]) -> str:
    lines = [
        json.dumps(
            {
                "id": str(offer_id),
                "nazev": name,
                "objem_ml": parse_volume_ml(name),
                "alkohol_obj_procent": _float(parse_abv(name)),
                "kandidati": [
                    {
                        "id": str(product.id),
                        "nazev": product.name,
                        "znacka": product.brand,
                        "objem_ml": product.volume_ml,
                        "alkohol_obj_procent": float(product.abv),
                    }
                    for product in candidates
                ],
            },
            ensure_ascii=False,
        )
        for offer_id, (name, candidates) in enumerate(batch, start=1)
    ]
    return (
        "Spáruj akční ceny ze zdroje akcniceny.cz s produkty ze sortimentu rohlik.cz. Produkt "
        "je konkrétní balení lihoviny: značka, název, objem a obsah alkoholu; dárkové balení "
        "stejného objemu je týž produkt. U každé akční ceny vyber z jejích kandidátů ten, který "
        "je týž produkt. Vrať null, když žádný kandidát není týž produkt, když akční cena platí "
        "pro více druhů najednou (například „vybrané druhy“), nebo když si nejsi jistý.\n\n"
        "Akční ceny s kandidáty (JSON, jedna na řádek):\n" + "\n".join(lines)
    )


def _float(value) -> float | None:
    return float(value) if value is not None else None


_SYSTEM = (
    "Pomáháš s konkurenčním zpravodajstvím pro výrobce lihovin v ČR. Odpovídáš jen podle "
    "zadaného schématu."
)
