"""Cenový agent (KIQ 2 Promo ceny): akční ceny lihovin a RTD z akcniceny.cz.

Na Zadání projde výpisy akčních cen podle klíčových slov (rum, vodka, gin, likéry, míchaný nápoj…)
i s jejich dalšími stranami. Výpis ukazuje u každého produktu nabídky jednotlivých řetězců:
cenu, případně cenu s věrnostní kartou, slevu, platnost a odkaz na leták. Každá nabídka je
jedna akční cena. Snímek se zapíše při každém Zadání (surové stránky jsou na disku), nález jen
u akční ceny, která na tabuli ještě není, takže z řady snímků vzniká historie bez duplicit.

Produkt se ukládá tak, jak ho uvádí zdroj; párování na produkty je samostatný krok.
Objem a obsah alkoholu se čtou z názvu produktu stejně jako u sortimentního agenta
(`lihoviny.products`). Když obsah alkoholu v názvu není (nebo je nejednoznačný, třeba
u „vybraných druhů“), cena za litr etanolu zůstane prázdná.
"""

import html
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urljoin

from lihoviny.agent import Agent, OdpovedSberu
from lihoviny.bus import Message
from lihoviny.fetch import FetchResult
from lihoviny.products import parse_abv, parse_volume_ml

SOURCE = "akcniceny.cz"
BASE_URL = "https://www.akcniceny.cz"
LISTING_URL = BASE_URL + "/zbozi/napoje-alkoholicke/{}/"

# Výpisy nápojů alkoholických podle klíčového slova: (slovo v adrese, jak ho uvádí zdroj).
# Zdroj nemá samostatnou kategorii lihovin ani RTD; pivo, víno a sekt tak zůstanou stranou.
LISTINGS = [
    ("tuzemak-rum", "tuzemák, rum"),
    ("vodky", "vodka"),
    ("whisky", "whisky"),
    ("gin", "gin"),
    ("brandy", "brandy"),
    ("tequily", "tequila"),
    ("likery", "likéry"),
    ("bylinne-destilaty", "bylinné destiláty"),
    ("ovocne-destilaty", "ovocné destiláty"),
    ("michany-napoj", "míchaný nápoj"),
]
# Pojistka proti zacyklenému stránkování; výpisy mají běžně 1–3 strany.
MAX_PAGES = 10


class CenovyError(Exception):
    pass


@dataclass(frozen=True)
class Offer:
    """Nabídka jednoho řetězce u jednoho produktu, jak ji ukazuje výpis."""

    product: str
    product_url: str
    chain: str
    price: Decimal
    card_price: Decimal | None
    card_label: str | None
    discount_percent: int | None
    valid_from: date | None
    valid_until: date | None
    flyer_url: str | None


class Cenovy(Agent):
    name = "cenovy"
    role = "sběrný agent KIQ 2: akční ceny lihovin a RTD z akcniceny.cz"
    fetch_delay = 1.0  # robots.txt akcniceny.cz: Crawl-delay: 1

    def handle_request(self, request: Message) -> dict[str, Any]:
        pages: list[FetchResult] = []
        # Tatáž akční cena bývá ve více výpisech (Jägermeister v likérech i bylinných destilátech).
        offers: dict[tuple, tuple[str, dict[str, Any]]] = {}
        for slug, category in LISTINGS:
            for offer in self._read_listing(LISTING_URL.format(slug), pages):
                content = _finding_content(offer, category)
                key = _offer_key(offer.product_url, content)
                offers.setdefault(key, (offer.product_url, content))
        if not offers:
            raise CenovyError(f"{SOURCE}: ve výpisech není žádná akční cena (změnila se stránka?)")

        known = self._known_offer_keys()
        new_findings = 0
        # Snímek a jeho nálezy jedna transakce: pád uprostřed nenechá na tabuli půl snímku.
        with self.transaction():
            snapshot = self.board.record_snapshot(
                agent=self.name, source=SOURCE, fetches=pages, item_count=len(offers)
            )
            for key, (product_url, content) in offers.items():
                if key in known:
                    continue
                self.board.record_finding(
                    kiq=2,
                    agent=self.name,
                    type="akcni_cena",
                    snapshot_id=snapshot.id,
                    source_url=product_url,
                    content=content,
                )
                new_findings += 1
        return OdpovedSberu(snapshot_id=snapshot.id, new_findings=new_findings)

    def _read_listing(self, url: str, pages: list[FetchResult]) -> list[Offer]:
        offers: list[Offer] = []
        next_url: str | None = url
        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            page = self.fetcher.get(next_url)
            pages.append(page)
            page_offers, next_url = _parse_listing(page.text)
            offers.extend(page_offers)
        return offers

    def _known_offer_keys(self) -> set[tuple]:
        return {
            _offer_key(f.source_url, f.content)
            for f in self.board.findings(kiq=2)
            if f.agent == self.name and f.type == "akcni_cena"
        }


def _parse_listing(page: str) -> tuple[list[Offer], str | None]:
    """Nabídky ze strany výpisu a adresa další strany (None na poslední straně).

    Sponzorované produkty nahoře výpisu (TOP) se přeskakují: nejsou z dané kategorie.
    """
    offers: list[Offer] = []
    starts = list(_PRODUCT.finditer(page))
    for index, match in enumerate(starts):
        limit = starts[index + 1].start() if index + 1 < len(starts) else len(page)
        end = page.find(f'href="#prodejci{match.group(1)}"', match.end(), limit)
        offers.extend(_parse_product(page[match.end() : end if end != -1 else limit]))
    next_link = _NEXT_PAGE.search(page)
    return offers, urljoin(BASE_URL, html.unescape(next_link.group(1))) if next_link else None


def _parse_product(block: str) -> list[Offer]:
    url, name = _ITEM_URL.search(block), _ITEM_NAME.search(block)
    if not (url and name):
        return []
    product = " ".join(html.unescape(name.group(1)).split())
    product_url = urljoin(BASE_URL, html.unescape(url.group(1)))
    offers = []
    for part in block.split(_OFFER)[1:]:
        chain, price = _SELLER.search(part), _PRICE.search(part)
        if not (chain and price):
            continue
        card = _CARD.search(part)
        discount = _DISCOUNT.search(part)
        flyer = _FLYER.search(part)
        offers.append(
            Offer(
                product=product,
                product_url=product_url,
                chain=" ".join(html.unescape(chain.group(1)).split()),
                price=Decimal(price.group(1)),
                card_price=_czech_number(card.group(2)) if card else None,
                card_label=" ".join(html.unescape(card.group(1)).split()) if card else None,
                discount_percent=int(discount.group(1)) if discount else None,
                valid_from=_date_after("Platí od", part),
                valid_until=_date_after("Platí do", part),
                flyer_url=urljoin(BASE_URL, html.unescape(flyer.group(1))) if flyer else None,
            )
        )
    return offers


def _finding_content(offer: Offer, category: str) -> dict[str, Any]:
    volume_ml = parse_volume_ml(offer.product)
    volume = Decimal(volume_ml) / 1000 if volume_ml else None
    abv = parse_abv(offer.product)
    return {
        "product": offer.product,
        "chain": offer.chain,
        "price": float(offer.price),
        "card_price": float(offer.card_price) if offer.card_price is not None else None,
        "card_label": offer.card_label,
        "discount_percent": offer.discount_percent,
        "valid_from": offer.valid_from.isoformat() if offer.valid_from else None,
        "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
        "volume_l": float(volume) if volume else None,
        "abv_percent": float(abv) if abv else None,
        "price_per_l": _money(offer.price / volume) if volume else None,
        "price_per_l_ethanol": (
            _money(offer.price / (volume * abv / 100)) if volume and abv else None
        ),
        "source_category": category,
        "flyer_url": offer.flyer_url,
    }


def _offer_key(source_url: str, content: dict[str, Any]) -> tuple:
    """Akční cena je táž, když se shoduje produkt, řetězec, ceny a konec platnosti.

    Začátek platnosti do klíče nepatří: zdroj ho uvádí jen u akčních cen, které ještě neplatí.
    """
    return (
        source_url,
        content["chain"],
        content["price"],
        content["card_price"],
        content["valid_until"],
    )


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _czech_number(text: str) -> Decimal | None:
    digits = "".join(text.split()).replace(",", ".")
    return Decimal(digits) if digits else None


def _date_after(label: str, part: str) -> date | None:
    match = re.search(
        rf"{label}:(?:<[^>]+>|[^\d<])*?(\d{{1,2}})\.\s*(\d{{1,2}})\.\s*(\d{{4}})", part
    )
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    return date(year, month, day)


_PRODUCT = re.compile(r'data-collapser="prodejci(\d+)"')
_NEXT_PAGE = re.compile(r'title="Následující" href="([^"]+)"')
_OFFER = 'itemtype="http://schema.org/Offer"'
_ITEM_URL = re.compile(r'itemprop="url" content="([^"]+)"')
_ITEM_NAME = re.compile(r'itemprop="name" content="([^"]+)"')
_SELLER = re.compile(r'itemprop="name"[^>]*?content="([^"]+)"')
_PRICE = re.compile(r'itemprop="price" content="(\d+(?:\.\d+)?)"')
_CARD = re.compile(r"<small[^>]*>([^<]+)</small>\s*([\d\s\xa0,]+)")
_DISCOUNT = re.compile(r"-(\d+)<span>%")
_FLYER = re.compile(r'href="(/letak/[^"]+)"')
