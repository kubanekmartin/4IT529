"""Mediální agent (KIQ 3 Komunikace): články o lihovinách z oborových a mediálních webů.

Zdroje (`SOURCES`): zboziaprodej.cz a mediar.cz přes WordPress REST API (článek i s textem),
mediaguru.cz přes RSS (titulek a perex). Hlavní je zboziaprodej.cz: když selže, je to
Selhání; výpadek ostatních agent jen zaloguje a snímek vznikne bez nich. Mediář a MediaGuru
píšou o celém mediálním trhu, proto LLM dostane jen jejich články s lihovinami v textu
(`_SPIRITS`); ostatní agent zapíše jako viděné a nerelevantní.

Na Zadání načte ze zboziaprodej.cz posledních 30 článků i s textem. Každý článek, který
ještě nebyl v žádném snímku, posoudí LLM: jestli je o lihovinách, a u relevantního výrobce,
značky, kanál, téma a shrnutí ve dvou větách. Nález vznikne jen z relevantního článku.

REST API místo RSS: RSS nese jen 10 posledních článků (pár dní), API vrátí libovolný počet
a v JSON dává ID článku a čas vydání v UTC.

Jména osob se neukládají. Na disk jde jen ID, čas vydání a odkaz každého článku (s otiskem
celé odpovědi), ne titulky a texty. Nález odkazuje na článek krátkým odkazem s ID (adresa se slugem
opakuje titulek, a s ním i jména), titulek a shrnutí píše LLM bez jmen a jména, která LLM
v článku našel, agent v jeho textech pro jistotu ještě nahradí.
"""

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import partial
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field

from lihoviny.agent import Agent, OdpovedSberu
from lihoviny.board import Snapshot
from lihoviny.bus import Message
from lihoviny.fetch import FetchError, FetchResult
from lihoviny.products import plain_text
from lihoviny.zadavatel import WATCHED_COMPETITORS

log = logging.getLogger(__name__)

SOURCE = "zboziaprodej.cz"
BASE_URL = "https://www.zboziaprodej.cz"
# Posledních 30 článků (zhruba dva týdny) jen s poli, která agent čte.
POSTS_URL = f"{BASE_URL}/wp-json/wp/v2/posts?per_page=30&_fields=id,date_gmt,title,content"
MEDIAR_URL = "https://www.mediar.cz"
# Mediář vydá kolem deseti článků denně: 50 je zhruba pět dní.
MEDIAR_POSTS_URL = f"{MEDIAR_URL}/wp-json/wp/v2/posts?per_page=50&_fields=id,date_gmt,title,content"
MEDIAGURU_URL = "https://www.mediaguru.cz"
# shortcut: RSS nese jen 15 nejnovějších článků (asi den), při denním sběru část uteče;
# kdyby bylo potřeba víc, číst rubriky MediaGuru (Marketing, Reklama) zvlášť.
MEDIAGURU_RSS_URL = f"{MEDIAGURU_URL}/rss/nejnovejsi"
REDACTED = "[osoba]"

Channel = Literal["letak", "prodejna", "e_shop", "socialni_site", "reklama", "eventy", "pr"]
# Kanály, kterými výrobce podporuje prodej (KIQ 3); oborový tisk sám je kanál PR.
CHANNELS: dict[Channel, str] = {
    "letak": "Leták řetězce",
    "prodejna": "V prodejně",
    "e_shop": "E-shop",
    "socialni_site": "Sociální sítě",
    "reklama": "Reklama v médiích",
    "eventy": "Eventy a sponzoring",
    "pr": "PR a oborový tisk",
}

Topic = Literal["portfolio", "kampan", "promo", "sezona", "firma", "trh", "regulace", "jine"]
TOPICS: dict[Topic, str] = {
    "portfolio": "Produkty a portfolio",
    "kampan": "Reklamní kampaň",
    "promo": "Promo a soutěže",
    "sezona": "Sezónní nabídka",
    "firma": "Firma",
    "trh": "Trh a spotřebitelé",
    "regulace": "Regulace a daně",
    "jine": "Jiné",
}

# Popis kódů pro LLM: o co jde, aby kódy volil stejně článek od článku.
_CHANNEL_HINTS: dict[Channel, str] = {
    "letak": "leták řetězce",
    "prodejna": "v prodejně: vystavení, ochutnávky, materiály v místě prodeje",
    "e_shop": "e-shop a online prodej",
    "socialni_site": "sociální sítě a influenceři",
    "reklama": "reklama v TV, rádiu, tisku nebo venku",
    "eventy": "eventy pro veřejnost, festivaly a sponzoring",
    "pr": "PR a oborový tisk: tisková zpráva, rozhovor, článek o firmě (když nic jiného nesedí)",
}
_TOPIC_HINTS: dict[Topic, str] = {
    "portfolio": "produkty a portfolio: další produkty, příchutě, balení, inovace",
    "kampan": "reklamní nebo značková kampaň",
    "promo": "spotřebitelská soutěž, promo v řetězci, akční cena",
    "sezona": "sezónní nabídka a kampaně (Vánoce, Velikonoce, léto)",
    "firma": "firma: vedení, akvizice, investice, výroba, výsledky",
    "trh": "trh a spotřebitelé: data, trendy, chování nakupujících",
    "regulace": "regulace a daně",
    "jine": "jiné",
}


class SourceFormatError(Exception):
    """Odpověď API nemá očekávaný tvar (web ho změnil nebo API vypnul); agent ohlásí Selhání."""


@dataclass(frozen=True)
class Source:
    name: str
    home: str
    url: str
    parse: Callable[[FetchResult, "Source"], list["Article"]]
    minimize: Callable[[bytes], bytes]
    # Obecný mediální web: LLM dostane jen článek, ve kterém jsou lihoviny (`_SPIRITS`).
    prefilter: bool


@dataclass(frozen=True)
class Article:
    id: int
    published_at: datetime
    title: str
    text: str
    source: Source
    url: str


class Assessment(BaseModel):
    relevant: bool = Field(description="true, když je článek o lihovinách podle pokynů")
    people: list[str] = Field(
        description="jména a příjmení všech fyzických osob z článku, jen pro kontrolu"
    )
    title: str = Field(description="titulek článku bez jmen osob")
    manufacturers: list[str] = Field(description="výrobci lihovin, o kterých článek píše")
    brands: list[str] = Field(description="značky lihovin, o kterých článek píše")
    channel: Channel = Field(description="kód kanálu komunikace")
    topic: Topic = Field(description="kód tématu")
    summary: str = Field(description="shrnutí článku ve dvou větách, bez jmen osob")


class Medialni(Agent):
    name = "medialni"
    role = "sběrný agent KIQ 3: články o lihovinách ze zboziaprodej.cz, mediar.cz a mediaguru.cz"
    # robots.txt zdrojů crawl-delay neuvádí; na Zadání jde jeden požadavek na zdroj.
    fetch_delay = 1.0

    def handle_request(self, request: Message) -> dict[str, Any]:
        pages: list[FetchResult] = []
        articles: list[Article] = []
        for source in SOURCES:
            try:
                page = self.fetcher.get(source.url, minimize=source.minimize)
                articles += source.parse(page, source)
            except (FetchError, SourceFormatError) as error:
                if source is SOURCES[0]:
                    raise
                log.warning("%s: zdroj vynechán (%s)", source.name, error)
                continue
            pages.append(page)
        seen = {s.name: self.board.article_ids(source=s.name) for s in SOURCES}
        new = [article for article in articles if article.id not in seen[article.source.name]]

        # Každý posouzený článek se zapíše hned ve vlastní transakci: chyba LLM u jednoho
        # článku nezahodí ostatní a příští Zadání posoudí jen články, které zůstaly.
        # Snímek vznikne s prvním zápisem, takže když LLM selže u všech, tabule zůstane beze
        # změny a odpovědí je Selhání.
        snapshot = None
        new_findings = 0
        errors: list[Exception] = []
        for article in new:
            try:
                assessment = self._assess(article) if _worth_assessing(article) else None
            except Exception as error:
                log.warning("článek %s: posouzení LLM selhalo (%s)", article.id, error)
                errors.append(error)
                continue
            with self.transaction():
                snapshot = snapshot or self._record_snapshot(pages, len(articles))
                new_findings += self._record_article(snapshot.id, article, assessment)
        if errors and len(errors) == len(new):
            raise errors[0]
        if snapshot is None:
            with self.transaction():
                snapshot = self._record_snapshot(pages, len(articles))
        log.info(
            "snímek %s: %s článků, %s posouzeno, %s nepodařilo posoudit, %s o lihovinách",
            snapshot.id,
            len(articles),
            len(new) - len(errors),
            len(errors),
            new_findings,
        )
        return OdpovedSberu(
            snapshot_id=snapshot.id, new_findings=new_findings, item_count=len(articles)
        )

    def _record_snapshot(self, pages: list[FetchResult], item_count: int) -> Snapshot:
        names = [s.name for s in SOURCES if any(p.url == s.url for p in pages)]
        return self.board.record_snapshot(
            agent=self.name, source=", ".join(names), fetches=pages, item_count=item_count
        )

    def _record_article(
        self, snapshot_id: int, article: Article, assessment: Assessment | None
    ) -> int:
        """Zapíše článek (a z relevantního nález); vrátí počet nových nálezů.
        `assessment` None = článek bez lihovin, který LLM neposuzoval."""
        finding_id = None
        if assessment is not None and assessment.relevant:
            finding_id = self.board.record_finding(
                kiq=3,
                agent=self.name,
                type="clanek",
                snapshot_id=snapshot_id,
                source_url=_link(article, assessment.people),
                content=_finding_content(article, assessment),
            ).id
        self.board.record_article(
            source=article.source.name,
            source_article_id=article.id,
            first_seen_snapshot_id=snapshot_id,
            finding_id=finding_id,
        )
        return 0 if finding_id is None else 1

    def _assess(self, article: Article) -> Assessment:
        return self.llm.ask(
            _PROMPT.format(
                source=article.source.name,
                competitors=", ".join(WATCHED_COMPETITORS),
                channels=_code_list(_CHANNEL_HINTS),
                topics=_code_list(_TOPIC_HINTS),
                title=article.title,
                published=f"{article.published_at:%Y-%m-%d}",
                text=article.text,
            ),
            Assessment,
            effort="low",
            system=_SYSTEM,
        )


_SYSTEM = (
    "Pomáháš s konkurenčním zpravodajstvím pro výrobce lihovin v ČR. Odpovídáš jen podle "
    "zadaného schématu. Jména osob nikdy nepíšeš do titulku ani do shrnutí."
)

_PROMPT = """\
Posuď článek z webu {source} pro přehled komunikace výrobců lihovin.

Relevantní je článek o lihovinách, tedy tvrdém alkoholu s aspoň 15 % obj. (včetně likérů):
o jejich výrobcích, značkách, produktech, prodeji, propagaci nebo o trhu lihovin a alkoholu.
Článek jen o pivu, víně, nealkoholických nápojích, hotových míchaných nápojích (RTD) nebo
o maloobchodě obecně relevantní není.

U relevantního článku vyplň:
- manufacturers: výrobce lihovin z článku. Sledované konkurenty piš přesně takto: \
{competitors}.
- brands: značky lihovin z článku.
- channel: kanál, kterým výrobce podle článku komunikuje; jeden kód ze seznamu.
- topic: téma článku; jeden kód ze seznamu.
- title: titulek článku; když obsahuje jméno osoby, přepiš ho bez něj.
- summary: shrnutí ve dvou větách česky.
U nerelevantního článku nech seznamy prázdné a texty prázdné.

Jména osob nepiš do titulku ani do shrnutí: místo jména uveď funkci a firmu (například
„marketingová ředitelka společnosti Stock“). Do people vypiš jména všech fyzických osob
z článku; slouží jen ke kontrole a neukládají se. Firma pojmenovaná po osobě (Jan Becher,
Rudolf Jelínek) osoba není.

Kanály (kód: popis):
{channels}

Témata (kód: popis):
{topics}

Titulek: {title}
Vydáno: {published}

{text}
"""


def _code_list(hints: dict[str, str]) -> str:
    return "\n".join(f"- {code}: {hint}" for code, hint in hints.items())


def _parse_articles(page: FetchResult, source: Source) -> list[Article]:
    """Články z odpovědi WordPress API od nejstaršího. Prázdný výpis = rozbitý zdroj."""
    try:
        articles = [
            Article(
                id=int(post["id"]),
                published_at=datetime.fromisoformat(post["date_gmt"]).replace(tzinfo=UTC),
                title=_article_text(post["title"]["rendered"]),
                text=_article_text(post["content"]["rendered"]),
                source=source,
                url=f"{source.home}/?p={int(post['id'])}",
            )
            for post in json.loads(page.text)
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise SourceFormatError(f"{page.url}: nečekaná odpověď API ({error!r})") from error
    if not articles:
        raise SourceFormatError(f"{source.name}: API nevrátilo žádný článek (změnil se web?)")
    return sorted(articles, key=lambda article: article.published_at)


def _parse_rss(page: FetchResult, source: Source) -> list[Article]:
    """Články z RSS od nejstaršího. RSS nemá číselné ID: ID je otisk odkazu."""
    try:
        articles = [
            Article(
                id=_link_id(item.findtext("link", "").strip()),
                published_at=parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(UTC),
                title=_article_text(item.findtext("title", "")),
                text=_article_text(item.findtext("description", "")),
                source=source,
                url=item.findtext("link", "").strip(),
            )
            for item in ET.fromstring(page.body).iter("item")
        ]
    except (ET.ParseError, TypeError, ValueError) as error:
        raise SourceFormatError(f"{page.url}: nečekané RSS ({error!r})") from error
    if not articles or not all(a.url.startswith(source.home + "/") for a in articles):
        raise SourceFormatError(f"{source.name}: RSS bez článků webu (změnil se web?)")
    return sorted(articles, key=lambda article: article.published_at)


def _link_id(link: str) -> int:
    # Prvních 7 bajtů SHA-256: kladné číslo do bigint, kolize u pár tisíc článků nehrozí.
    return int.from_bytes(sha256(link.encode()).digest()[:7], "big")


def _rss_ids_only(body: bytes) -> bytes:
    """Podoba RSS pro disk: jen otisk odkazu a čas vydání (titulky i adresy nesou jména)."""
    try:
        items = ET.fromstring(body).iter("item")
        kept = [
            {"id": _link_id(i.findtext("link", "").strip()), "pubDate": i.findtext("pubDate")}
            for i in items
        ]
    except ET.ParseError:
        return b""
    return json.dumps(kept).encode()


# Lihoviny v textu článku z obecného mediálního webu: jen takový článek posoudí LLM.
_SPIRITS = re.compile(
    r"alkohol|lihovin|likér|\brum(?:y|u|em|ech|ov[éý]\w*)?\b|tuzemák|vodk[ayuo]|\bgin\b|"
    r"whisk|destilát|slivovic|pálenk|becher|jelínek|fruko|božkov|jägermeister|fernet|"
    r"bacardi|diageo|pernod|spirits",
    re.IGNORECASE,
)


def _worth_assessing(article: Article) -> bool:
    return not article.source.prefilter or bool(_SPIRITS.search(f"{article.title}\n{article.text}"))


def _link(article: Article, people: list[str]) -> str:
    """Odkaz nálezu. Adresa se slugem (RSS) opakuje titulek: když v ní je jméno osoby, nález
    odkáže jen na web zdroje."""
    slug = plain_text(article.url)
    surnames = [plain_text(_stem(p.split()[-1])) for p in people if p.split()]
    return article.source.home if any(s and s in slug for s in surnames) else article.url


def _without_texts(body: bytes, home: str = BASE_URL) -> bytes:
    """Podoba odpovědi API pro disk: titulky a texty nesou jména osob, zůstane ID, čas vydání
    a odkaz na článek. Z odpovědi, která není výpisem článků, se neuloží nic (jen otisk)."""
    try:
        posts = json.loads(body)
        kept = [
            {"id": post["id"], "date_gmt": post["date_gmt"], "link": f"{home}/?p={post['id']}"}
            for post in posts
        ]
    except (KeyError, TypeError, ValueError):
        return b""
    return json.dumps(kept).encode()


def _article_text(markup: str) -> str:
    """Text z HTML článku: odstavce na řádcích, bez značek a entit."""
    text = re.sub(r"</(?:p|h\d|li|figcaption|blockquote)>|<br\s*/?>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _finding_content(article: Article, assessment: Assessment) -> dict[str, Any]:
    companies = [*assessment.manufacturers, *assessment.brands, *WATCHED_COMPETITORS]
    return {
        "source": article.source.name,
        "article_id": article.id,
        "published_at": article.published_at.isoformat(),
        "title": _without_people(assessment.title, assessment.people, companies),
        "manufacturers": assessment.manufacturers,
        "brands": assessment.brands,
        "channel": assessment.channel,
        "topic": assessment.topic,
        "summary": _without_people(assessment.summary, assessment.people, companies),
    }


def _without_people(text: str, people: list[str], companies: list[str]) -> str:
    """Pojistka k pokynu v promptu: jméno osoby, které LLM přesto napsal, nahradí `REDACTED`.

    Hledá příjmení i s křestními jmény před ním, také ve skloňovaných tvarech. Osobu, po které
    se jmenuje firma nebo značka (Jan Becher), přeskočí, aby z textu nezmizel výrobce.
    """
    for person in people:
        names = person.split()
        if not names or any(names[-1] in company for company in companies):
            continue
        *given, surname = (re.escape(_stem(name)) for name in names)
        before = rf"(?:\b(?:{'|'.join(given)})\w*\s+)*" if given else ""
        text = re.sub(rf"{before}\b{surname}\w*", REDACTED, text)
    return text


def _stem(name: str) -> str:
    # Bez poslední hlásky pokryje skloňování: Formánková → Formánkové, Kopecký → Kopeckého.
    return name[:-1] if len(name) > 3 else name


SOURCES = [
    Source(SOURCE, BASE_URL, POSTS_URL, _parse_articles, _without_texts, prefilter=False),
    Source(
        "mediar.cz",
        MEDIAR_URL,
        MEDIAR_POSTS_URL,
        _parse_articles,
        partial(_without_texts, home=MEDIAR_URL),
        prefilter=True,
    ),
    Source(
        "mediaguru.cz", MEDIAGURU_URL, MEDIAGURU_RSS_URL, _parse_rss, _rss_ids_only, prefilter=True
    ),
]
