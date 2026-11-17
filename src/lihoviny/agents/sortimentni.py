"""Sortimentní agent (KIQ 1): produkty a Novinky z kategorie Lihoviny na rohlik.cz a alkohol.cz.

Alkohol.cz (specializovaný e-shop) má vlastní výpis Novinky (`ALKOHOL_CZ_NOVINKY`, asi 37
nejnovějších produktů). Lihovina z něj, která ještě není mezi produkty, je Novinka „podle
prodejce alkohol.cz“, i mimo výchozí snímek. Výpadek alkohol.cz Zadání neshodí.

Na Zadání stáhne stránku kategorie Lihoviny a z ní stránky podkategorií. Data čte
z `__NEXT_DATA__`, které Rohlík vkládá do stránky: seznam podkategorií, výpis produktů
podkategorie a seznam značek z filtru. Stránka nese jen první výpis podkategorie (14 produktů
podle doporučeného řazení), zbytek Rohlík dočítá až v prohlížeči. Produktů proto přibývá
s každým snímkem, jak se výpis mění.

Novinka je produkt, který se u zdroje objevil poprvé od začátku sledování:
- výchozí (první) snímek hlásí jen produkty se štítkem prodejce „Novinka“ (poznámka
  „podle prodejce“), ostatní jsou výchozí stav;
- později je novinkou produkt, který není v žádném předchozím snímku (tedy ani v tabulce
  produktů) a má ID u zdroje vyšší než všechny produkty výchozího snímku. Rohlík přiděluje
  ID postupně, takže starší produkt, který se do zkráceného výpisu dostal až teď, u zdroje
  byl už před začátkem sledování a novinkou není (ani se štítkem prodejce). Vyprodaný
  a vrácený produkt už v tabulce je.

Bere jen lihoviny s aspoň 15 % obj.; RTD, nealko a slabší nápoje (medoviny, vinné aperitivy)
vynechá. Kategorie se řídí podkategorií u zdroje; LLM rozhoduje jen nejasné případy (dárková balení,
likéry hořké vs. ostatní, aperitivy) a doplní značku, když název
nezačíná žádnou značkou z filtru zdroje. Výrobce u zdroje není, takže ho LLM určí jednou
pro každou novou značku.
"""

import html
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from lihoviny.agent import Agent, OdpovedSberu
from lihoviny.board import Product
from lihoviny.bus import Message
from lihoviny.fetch import FetchError, FetchResult
from lihoviny.llm import LlmError
from lihoviny.products import (
    CATEGORIES,
    SPIRIT_MIN_ABV,
    Category,
    parse_abv,
    parse_volume_ml,
    plain_text,
    product_key,
)
from lihoviny.zadavatel import WATCHED_COMPETITORS

log = logging.getLogger(__name__)

SOURCE = "rohlik.cz"
BASE_URL = "https://www.rohlik.cz"
ROOT_URL = f"{BASE_URL}/c300108072-lihoviny"

ALKOHOL_CZ = "alkohol.cz"
ALKOHOL_CZ_URL = "https://www.alkohol.cz"
ALKOHOL_CZ_NOVINKY = f"{ALKOHOL_CZ_URL}/produkty/novinky/"
# Karty z alkohol.cz nemají podkategorii Rohlíku: kategorii i značku určí LLM.
ALKOHOL_CZ_CATEGORY = -1
ALKOHOL_CZ_NOTE = "podle prodejce alkohol.cz"
RTD_SOURCE_CATEGORY = 300108082
# Podkategorie Lihovin u zdroje → Kategorie. Chybějící (Dárková balení, Likéry a medoviny,
# Aperitivy a nově přidané) jsou nejasné a rozhodne LLM.
SOURCE_CATEGORIES: dict[int, Category] = {
    300108075: "rum_tuzemak",  # Rum, rumové likéry a rumové elixíry
    300108080: "vodka",  # Vodka
    300108079: "gin",  # Gin a ginové likéry a nealkoholické varianty
    300108081: "whisky",  # Whisky, whiskey a bourbon
    300108074: "brandy",  # Brandy, koňak a armaňak
    300108076: "ovocne_destilaty",  # Ovocné pálenky, konzumní líh a jiné aromatizované lihoviny
    300123817: "ostatni",  # Sake
    300121089: "ostatni",  # Absinth, Tequila
}
# Nealkoholické nápoje a RTD (Ready to drink, Hard seltzer, koktejly) jsou mimo rozsah KIQ 1.
SKIPPED_SOURCE_CATEGORIES = {300122088, RTD_SOURCE_CATEGORY}
SELLER_NOTE = "podle prodejce"

_NEXT_DATA = re.compile(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


class SourceFormatError(Exception):
    """Stránka zdroje nemá očekávanou strukturu (Rohlík ji změnil); agent ohlásí Selhání."""


@dataclass(frozen=True)
class Listing:
    """Jedna karta produktu ve výpisu podkategorie."""

    source_id: int
    name: str
    url: str
    volume_ml: int
    abv: Decimal
    labelled_new: bool
    source_category_id: int
    source_category_name: str

    @property
    def key(self) -> str:
        return product_key(self.name, self.volume_ml, self.abv)


@dataclass
class UnrecordedProduct:
    """Produkt ze snímku, který na tabuli ještě není; všechny karty se stejným klíčem.

    Novinkou je jen někdy, podle pravidla v popisu modulu.
    """

    listings: list[Listing]
    category: Category | None = None
    brand: str | None = None
    manufacturer: str | None = None

    @property
    def oldest_listing(self) -> Listing:
        # Nejnižší ID u zdroje je nejstarší karta: obvykle samotný produkt, ne dárkové balení.
        return min(self.listings, key=lambda listing: listing.source_id)

    @property
    def labelled_new(self) -> bool:
        return any(listing.labelled_new for listing in self.listings)


class ProductClassification(BaseModel):
    id: str = Field(description="ID produktu ze vstupu")
    category: Category = Field(description="kód kategorie")
    brand: str = Field(description="značka, pod kterou výrobce produkt prodává, např. Božkov")


class Classification(BaseModel):
    products: list[ProductClassification]


class BrandManufacturer(BaseModel):
    brand: str = Field(description="značka ze vstupu")
    manufacturer: str | None = Field(description="výrobce značky, null když ho neznáš")


class Manufacturers(BaseModel):
    brands: list[BrandManufacturer]


class Sortimentni(Agent):
    name = "sortimentni"
    role = "sběrný agent KIQ 1: produkty a Novinky lihovin z rohlik.cz a alkohol.cz"
    # robots.txt rohlik.cz crawl-delay pro obecné klienty neuvádí; 5 s je ohleduplná rezerva.
    fetch_delay = 5.0

    def handle_request(self, request: Message) -> dict[str, Any]:
        root = self.fetcher.get(ROOT_URL)
        subcategories = _subcategories(root)
        pages = [self.fetcher.get(url) for url in subcategories]
        listings, source_brands = _parse_subcategories(pages, subcategories)
        fetches = [root, *pages]
        try:
            novinky = self.fetcher.get(ALKOHOL_CZ_NOVINKY)
            listings += _parse_alkohol_cz(novinky)
            fetches.append(novinky)
        except FetchError as error:
            log.warning("%s: zdroj vynechán (%s)", ALKOHOL_CZ, error)

        # Celé stahování a třídění proběhne dřív, než se cokoli zapíše na tabuli.
        known = {product.normalized_key: product for product in self.board.products()}
        in_snapshot: dict[str, list[Listing]] = {}
        for listing in listings:
            in_snapshot.setdefault(listing.key, []).append(listing)
        unrecorded = [
            UnrecordedProduct(group) for key, group in in_snapshot.items() if key not in known
        ]
        self._add_category_brand_manufacturer(unrecorded, source_brands, list(known.values()))

        previous = self.board.snapshots(agent=self.name)
        baseline = not previous
        watermark = _baseline_watermark(previous, known.values())
        new_findings = 0
        # Snímek, produkty a Novinky jedna transakce: neúplný výchozí snímek by později
        # udělal z běžných produktů falešné Novinky.
        with self.transaction():
            snapshot = self.board.record_snapshot(
                agent=self.name,
                source=", ".join(
                    [SOURCE, *([ALKOHOL_CZ] if len(fetches) > len(pages) + 1 else [])]
                ),
                fetches=fetches,
                item_count=len(in_snapshot),
            )
            for product in unrecorded:
                stored = self._record_product(product, snapshot.id)
                # Výchozí snímek: jen štítek prodejce. Později: ID u zdroje vyšší než výchozí stav.
                # Alkohol.cz: vždy podle štítku prodejce (jeho ID s Rohlíkem nesouvisí).
                from_alkohol_cz = product.oldest_listing.source_category_id == ALKOHOL_CZ_CATEGORY
                is_novinka = (
                    product.labelled_new
                    if baseline or from_alkohol_cz
                    else product.oldest_listing.source_id > watermark
                )
                if is_novinka:
                    note = ALKOHOL_CZ_NOTE if from_alkohol_cz else SELLER_NOTE if baseline else None
                    self._record_novinka(stored, snapshot.id, product.labelled_new, note)
                    new_findings += 1
        log.info(
            "snímek %s: %s produktů, z toho %s nových, %s novinek",
            snapshot.id,
            len(in_snapshot),
            len(unrecorded),
            new_findings,
        )
        return OdpovedSberu(
            snapshot_id=snapshot.id, new_findings=new_findings, item_count=len(in_snapshot)
        )

    def _add_category_brand_manufacturer(
        self, unrecorded: list[UnrecordedProduct], source_brands: set[str], known
    ) -> None:
        """Doplní novým produktům kategorii, značku a výrobce; LLM jen tam, kde zdroj nestačí."""
        brands = source_brands | {product.brand for product in known}
        unclear = []
        for product in unrecorded:
            product.category = _source_category(product.listings)
            product.brand = _match_brand(product.oldest_listing.name, brands)
            if product.category is None or product.brand is None:
                unclear.append(product)
        if unclear:
            self._classify(unclear)

        manufacturers = {product.brand: product.manufacturer for product in known}
        unknown_brands = sorted({p.brand for p in unrecorded if p.brand not in manufacturers})
        if unknown_brands:
            manufacturers |= self._ask_manufacturers(unknown_brands)
        for product in unrecorded:
            product.manufacturer = manufacturers[product.brand]

    def _classify(self, unclear: list[UnrecordedProduct]) -> None:
        items = [
            {
                "id": str(product.oldest_listing.source_id),
                "nazev": product.oldest_listing.name,
                "kategorie_u_zdroje": sorted({x.source_category_name for x in product.listings}),
                "objem_ml": product.oldest_listing.volume_ml,
                "alkohol_obj_procent": float(product.oldest_listing.abv),
            }
            for product in unclear
        ]
        categories = "\n".join(f"- {code}: {label}" for code, label in CATEGORIES.items())
        answer = self.llm.ask(
            "Zařaď každý produkt do jedné kategorie a urči jeho značku.\n\n"
            f"Kategorie (kód: název):\n{categories}\n\n"
            "Všechny produkty jsou lihoviny s aspoň 15 % obj. Hořké likéry jsou "
            "bylinné a hořké (Becherovka, Fernet, Jägermeister), ostatní likéry ty sladké.\n\n"
            "Produkty (JSON, jeden na řádek):\n" + _json_lines(items),
            Classification,
            effort="low",
            system=_SYSTEM,
        )
        by_id = {item.id: item for item in answer.products}
        for product in unclear:
            item = by_id.get(str(product.oldest_listing.source_id))
            if item is None:
                raise LlmError(f"LLM nezařadil produkt {product.oldest_listing.name!r}")
            product.category = product.category or item.category
            product.brand = product.brand or item.brand

    def _ask_manufacturers(self, brands: list[str]) -> dict[str, str | None]:
        answer = self.llm.ask(
            "U každé značky lihoviny urči výrobce, tedy firmu, pod jejímž jménem se značka "
            "v ČR prodává. Tyto výrobce piš přesně takto: "
            f"{', '.join(WATCHED_COMPETITORS)}. Když výrobce neznáš, vrať null.\n\n"
            "Značky (JSON):\n" + json.dumps(brands, ensure_ascii=False),
            Manufacturers,
            effort="low",
            system=_SYSTEM,
        )
        found = {item.brand: item.manufacturer for item in answer.brands}
        missing = [brand for brand in brands if brand not in found]
        if missing:
            raise LlmError(f"LLM neurčil výrobce značek {', '.join(missing)}")
        return found

    def _record_product(self, product: UnrecordedProduct, snapshot_id: int) -> Product:
        listing = product.oldest_listing
        return self.board.record_product(
            normalized_key=listing.key,
            manufacturer=product.manufacturer,
            brand=product.brand,
            name=listing.name,
            volume_ml=listing.volume_ml,
            abv=listing.abv,
            category=product.category,
            first_seen_url=listing.url,
            first_seen_snapshot_id=snapshot_id,
            source_product_id=listing.source_id,
        )

    def _record_novinka(
        self, product: Product, snapshot_id: int, seller_label: bool, note: str | None
    ) -> None:
        content = {
            "name": product.name,
            "brand": product.brand,
            "manufacturer": product.manufacturer,
            "category": product.category,
            "volume_ml": product.volume_ml,
            "abv": float(product.abv),
            "seller_label": seller_label,
        }
        if note:
            content["note"] = note
        self.board.record_finding(
            kiq=1,
            agent=self.name,
            type="novinka",
            source_url=product.first_seen_url,
            content=content,
            snapshot_id=snapshot_id,
            product_id=product.id,
        )


_SYSTEM = (
    "Pomáháš s konkurenčním zpravodajstvím pro výrobce lihovin v ČR. Odpovídáš jen podle "
    "zadaného schématu."
)


def _subcategories(root: FetchResult) -> dict[str, str]:
    """Adresy stránek podkategorií Lihovin → jejich název u zdroje (bez nealko nápojů)."""
    try:
        categories = [
            query["state"]["data"]
            for query in _queries(_page_props(root))
            if query["queryKey"][0] == "category"
        ]
        if len(categories) != 1:
            raise SourceFormatError(
                f"{root.url}: čekám jeden seznam podkategorií, stránka jich má {len(categories)}"
            )
        return {
            f"{BASE_URL}/c{sub['categoryId']}-{sub['slug']}": sub["name"]
            for sub in categories[0]["categories"]
            if sub["categoryId"] not in SKIPPED_SOURCE_CATEGORIES
        }
    except (KeyError, TypeError, ValueError) as error:
        raise SourceFormatError(f"{root.url}: chybí seznam podkategorií ({error!r})") from error


def _parse_subcategories(
    pages: list[FetchResult], names: dict[str, str]
) -> tuple[list[Listing], set[str]]:
    """Stránka bez karet, nebo výpis, ve kterém není žádná lihovina, znamená, že Rohlík změnil
    stránku: prázdný snímek by vypadal jako zdravý den bez Novinek, proto Selhání."""
    listings: list[Listing] = []
    brands: set[str] = set()
    for page in pages:
        try:
            props = _page_props(page)
            cards = _page_cards(props)
            for card in cards:
                listing = _listing(card, props["categoryId"], names[page.url])
                if listing is not None:
                    listings.append(listing)
            brands |= _page_brands(props)
        except (KeyError, TypeError, ValueError) as error:
            raise SourceFormatError(
                f"{page.url}: nečekaná struktura stránky ({error!r})"
            ) from error
        if not cards:
            raise SourceFormatError(f"{page.url}: ve výpisu podkategorie nejsou žádné produkty")
    if not listings:
        raise SourceFormatError(f"{SOURCE}: ve výpisech není žádná lihovina (změnila se stránka?)")
    return listings, brands


def _page_cards(props: dict[str, Any]) -> list[dict[str, Any]]:
    """Karty z výpisu podkategorie, které stránka patří (ne z jiných výpisů na stránce)."""
    category_id = props["categoryId"]
    cards = []
    for query in _queries(props):
        key = query["queryKey"]
        if key[0] == "categoryProductCards" and key[2]["id"] == category_id:
            for card_page in query["state"]["data"]["pages"]:
                cards += card_page["cardsData"]
    return cards


def _listing(card: dict[str, Any], category_id: int, category_name: str) -> Listing | None:
    """Karta jako lihovina; karta bez objemu nebo obsahu alkoholu (příslušenství, nealko
    nápoje) nebo s méně než 15 % obj. (RTD v jiné podkategorii, medovina) vrátí None."""
    volume_ml = parse_volume_ml(card["textualAmount"])
    abv = parse_abv(card["name"])
    if volume_ml is None or abv is None or abv < SPIRIT_MIN_ABV:
        return None
    return Listing(
        source_id=card["productId"],
        name=" ".join(card["name"].split()),
        url=f"{BASE_URL}/{card['productId']}-{card['slug']}",
        volume_ml=volume_ml,
        abv=abv,
        labelled_new=any(badge["type"] == "NEW" for badge in card["badges"]),
        source_category_id=category_id,
        source_category_name=category_name,
    )


def _page_brands(props: dict[str, Any]) -> set[str]:
    brands: set[str] = set()
    for query in _queries(props):
        if query["queryKey"][0] != "getCategoryFilters":
            continue
        for group in query["state"]["data"]["filterGroups"]:
            if group["tag"] == "znacka":  # filtr „Značka“
                brands.update(option["title"] for option in group["options"])
    return brands


def _source_category(listings: list[Listing]) -> Category | None:
    """Kategorie podle podkategorie u zdroje, nebo None, když je případ nejasný."""
    for listing in listings:
        category = SOURCE_CATEGORIES.get(listing.source_category_id)
        if category:
            return category
    return None


def _match_brand(name: str, brands: set[str]) -> str | None:
    """Nejdelší značka, kterou název začíná (bez ohledu na diakritiku a velikost písmen)."""
    plain_name = plain_text(name) + " "
    for brand in sorted(brands, key=len, reverse=True):
        if plain_name.startswith(plain_text(brand) + " "):
            return brand
    return None


def _baseline_watermark(previous, known) -> int:
    """Nejvyšší ID u zdroje mezi produkty výchozího snímku (0 před výchozím snímkem)."""
    if not previous:
        return 0
    baseline_id = previous[0].id
    return max(
        (p.source_product_id for p in known if p.first_seen_snapshot_id == baseline_id),
        default=0,
    )


def _page_props(page: FetchResult) -> dict[str, Any]:
    """Data stránky, která Rohlík vkládá do `__NEXT_DATA__` (Next.js `pageProps`)."""
    match = _NEXT_DATA.search(page.text)
    if match is None:
        raise SourceFormatError(f"{page.url}: stránka nemá __NEXT_DATA__")
    return json.loads(match.group(1))["props"]["pageProps"]


def _queries(props: dict[str, Any]) -> list[dict[str, Any]]:
    """Dotazy, jejichž výsledky stránka předává prohlížeči (stav React Query)."""
    return props["dehydratedState"]["queries"]


def _json_lines(items: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items)


_ALKOHOL_CZ_CARD = re.compile(
    r'<div class="h4-title">\s*<a[^>]*href="(/produkty/[^"]*-(\d+)/)"[^>]*>\s*([^<]+?)\s*</a>'
)


def _parse_alkohol_cz(page: FetchResult) -> list[Listing]:
    """Lihoviny z výpisu Novinky na alkohol.cz: název nese objem i obsah alkoholu
    („Bache Gabrielsen Cognac VS 0,7l 40%“). Víno, pivo a tašky vypadnou podle obsahu
    alkoholu nebo objemu."""
    listings = []
    for href, source_id, raw_name in _ALKOHOL_CZ_CARD.findall(page.text):
        name = " ".join(html.unescape(raw_name).split())
        volume_ml, abv = parse_volume_ml(name), parse_abv(name)
        if volume_ml is None or abv is None or abv < SPIRIT_MIN_ABV:
            continue
        listings.append(
            Listing(
                source_id=int(source_id),
                name=name,
                url=f"{ALKOHOL_CZ_URL}{href}",
                volume_ml=volume_ml,
                abv=abv,
                labelled_new=True,
                source_category_id=ALKOHOL_CZ_CATEGORY,
                source_category_name="alkohol.cz: Novinky",
            )
        )
    if not listings:
        log.warning("%s: ve výpisu Novinky není žádná lihovina (změnila se stránka?)", ALKOHOL_CZ)
    return listings
