"""Cenový agent (KIQ 2 Akční ceny): akční ceny lihovin a RTD z akcniceny.cz.

Na Zadání projde výpisy akčních cen podle klíčových slov (rum, vodka, gin, likéry, míchaný nápoj…)
i s jejich dalšími stranami. Výpis ukazuje u každého produktu nabídky jednotlivých řetězců:
cenu, případně cenu s věrnostní kartou, o kolik procent je pod běžnou cenou, platnost
a odkaz na leták. Každá nabídka je
jedna akční cena. Snímek se zapíše při každém Zadání (surové stránky jsou na disku), nález jen
u akční ceny, která na tabuli ještě není, takže z řady snímků vzniká historie bez duplicit.

Produkt se ukládá tak, jak ho uvádí zdroj. Objem a obsah alkoholu se čtou z názvu produktu
stejně jako u sortimentního agenta (`lihoviny.products`). Když obsah alkoholu v názvu není
(nebo je nejednoznačný, třeba u „vybraných druhů“), cena za litr etanolu zůstane prázdná.

Akční ceny se párují na produkty ze sortimentu (`lihoviny.pairing`: klíč, LLM jen jako
záloha): nové i dřív nespárované, protože sortimentní agent přidává produkty každý den.
Spárovaný nález dostane `product_id` a co název neuvádí (objem, obsah alkoholu), doplní
produkt; ceny za litr se pak přepočítají.
"""

import html
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urljoin, urlsplit

from lihoviny.agent import Agent, OdpovedSberu
from lihoviny.board import Finding
from lihoviny.bus import Message
from lihoviny.fetch import FetchResult
from lihoviny.pairing import Pairing, pair
from lihoviny.products import parse_abv, parse_volume_ml

log = logging.getLogger(__name__)

SOURCE = "akcniceny.cz"
BASE_URL = "https://www.akcniceny.cz"
SOURCE_HOSTS = {"www.akcniceny.cz", "akcniceny.cz"}
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

        on_board = self._own_findings()
        known = {_offer_key(f.source_url, f.content): f.id for f in on_board}
        unpaired = [f for f in on_board if f.product_id is None]
        pairings, rejected = self._pair(offers, on_board, unpaired)
        new_findings = 0
        # Snímek a jeho nálezy jedna transakce: pád uprostřed nenechá na tabuli půl snímku.
        with self.transaction():
            snapshot = self.board.record_snapshot(
                agent=self.name, source=SOURCE, fetches=pages, item_count=len(offers)
            )
            in_snapshot = []
            for key, (product_url, content) in offers.items():
                if key not in known:
                    pairing = pairings.get(content["product"])
                    known[key] = self.board.record_finding(
                        kiq=2,
                        agent=self.name,
                        type="akcni_cena",
                        snapshot_id=snapshot.id,
                        source_url=product_url,
                        content=_with_product(content, pairing) if pairing else content,
                        product_id=pairing.product.id if pairing else None,
                    ).id
                    new_findings += 1
                in_snapshot.append(known[key])
            # Nález vzniká jen poprvé; snímek si pamatuje i dřívější akční ceny, které obsahuje.
            self.board.record_snapshot_findings(snapshot.id, in_snapshot)
            # Dřívější akční ceny, ke kterým mezitím přibyl produkt ve sortimentu.
            for finding in unpaired:
                pairing = pairings.get(finding.content["product"])
                if pairing:
                    self.board.record_pairing(
                        finding.id,
                        product_id=pairing.product.id,
                        content=_with_product(finding.content, pairing),
                    )
            self.board.record_rejected_pairings(rejected)
        return OdpovedSberu(
            snapshot_id=snapshot.id, new_findings=new_findings, item_count=len(offers)
        )

    def _read_listing(self, url: str, pages: list[FetchResult]) -> list[Offer]:
        offers: list[Offer] = []
        next_url: str | None = url
        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            page = self.fetcher.get(next_url)
            pages.append(page)
            page_offers, next_url = _parse_listing(page)
            offers.extend(page_offers)
        return offers

    def _own_findings(self) -> list[Finding]:
        return [
            f for f in self.board.findings(kiq=2) if f.agent == self.name and f.type == "akcni_cena"
        ]

    def _pair(
        self,
        offers: dict[tuple, tuple[str, dict[str, Any]]],
        on_board: list[Finding],
        unpaired: list[Finding],
    ) -> tuple[dict[str, Pairing], set[tuple[str, int]]]:
        """Párování podle názvu akční ceny pro nové i dříve nespárované akční ceny.

        Název, který už je spárovaný, si párování nechá; ostatní jdou do `lihoviny.pairing`.
        Vrátí párování a dvojice, které LLM nově odmítl.
        """
        products = {product.id: product for product in self.board.products()}
        paired = {
            f.content["product"]: Pairing(products[f.product_id], f.content["paired_by"])
            for f in on_board
            if f.product_id is not None
        }
        names = {content["product"] for _, content in offers.values()}
        names |= {f.content["product"] for f in unpaired}
        result = pair(
            names - paired.keys(),
            list(products.values()),
            rejected=self.board.rejected_pairings(),
            llm=self.llm,
        )
        log.info(
            "párování: %s názvů akčních cen, nově spárováno %s (klíčem %s), nespárováno %s",
            len(names),
            len(result.pairings),
            sum(p.paired_by == "key" for p in result.pairings.values()),
            len(names - paired.keys() - result.pairings.keys()),
        )
        return paired | result.pairings, result.rejected


def _parse_listing(page: FetchResult) -> tuple[list[Offer], str | None]:
    """Nabídky ze strany výpisu a adresa další strany (None na poslední straně).

    Sponzorované produkty nahoře výpisu (TOP) se přeskakují: nejsou z dané kategorie.
    """
    text = page.text
    offers: list[Offer] = []
    starts = list(_PRODUCT.finditer(text))
    for index, match in enumerate(starts):
        limit = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        end = text.find(f'href="#prodejci{match.group(1)}"', match.end(), limit)
        block = text[match.end() : end if end != -1 else limit]
        offers.extend(_parse_product(block, page.url))
    next_link = _NEXT_PAGE.search(text)
    return offers, urljoin(BASE_URL, html.unescape(next_link.group(1))) if next_link else None


def _parse_product(block: str, page_url: str) -> list[Offer]:
    url, name = _ITEM_URL.search(block), _ITEM_NAME.search(block)
    if not (url and name):
        return []
    product = " ".join(html.unescape(name.group(1)).split())
    # Odkazy na produkt i leták jdou na web do `href`: jiný než https na akcniceny.cz (třeba
    # `javascript:`) by byl díra. Místo odkazu na produkt pak nález dostane stranu výpisu,
    # leták zůstane bez odkazu.
    product_url = _source_link(url.group(1)) or page_url
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
                flyer_url=_source_link(flyer.group(1)) if flyer else None,
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
        **_litre_prices(offer.price, volume, abv),
        "source_category": category,
        "flyer_url": offer.flyer_url,
    }


def _with_product(content: dict[str, Any], pairing: Pairing) -> dict[str, Any]:
    """Obsah spárované akční ceny: objem a obsah alkoholu, které název neuvádí, doplní
    produkt ze sortimentu (`from_product` říká které) a ceny za litr se přepočítají."""
    filled: dict[str, float] = {}
    if content["volume_l"] is None:
        filled["volume_l"] = pairing.product.volume_ml / 1000
    if content["abv_percent"] is None:
        filled["abv_percent"] = float(pairing.product.abv)
    paired = content | filled
    return paired | {
        **_litre_prices(
            Decimal(str(paired["price"])),
            Decimal(str(paired["volume_l"])),
            Decimal(str(paired["abv_percent"])),
        ),
        "paired_by": pairing.paired_by,
        "from_product": sorted(filled),
    }


def _litre_prices(
    price: Decimal, volume: Decimal | None, abv: Decimal | None
) -> dict[str, float | None]:
    """Cena za litr a za litr etanolu (objem × obsah alkoholu); bez údaje zůstane prázdná."""
    return {
        "price_per_l": _money(price / volume) if volume else None,
        "price_per_l_ethanol": _money(price / (volume * abv / 100)) if volume and abv else None,
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


def _source_link(href: str) -> str | None:
    """Odkaz ze zdroje jako celá adresa, jen když vede přes https na akcniceny.cz."""
    url = urljoin(BASE_URL, html.unescape(href))
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in SOURCE_HOSTS else None


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
# Odkaz na leták relativní i celý; `_source_link` pak propustí jen https na akcniceny.cz.
_FLYER = re.compile(r'href="([^"]*/letak/[^"]+)"')
