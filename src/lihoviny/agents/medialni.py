"""Mediální agent (KIQ 3 Komunikace): články o lihovinách z oborového magazínu zboziaprodej.cz.

Na Zadání načte přes WordPress REST API posledních 30 článků i s textem. Každý článek, který
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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from lihoviny.agent import Agent, OdpovedSberu
from lihoviny.board import Snapshot
from lihoviny.bus import Message
from lihoviny.fetch import FetchResult
from lihoviny.zadavatel import WATCHED_COMPETITORS

log = logging.getLogger(__name__)

SOURCE = "zboziaprodej.cz"
BASE_URL = "https://www.zboziaprodej.cz"
# Posledních 30 článků (zhruba dva týdny) jen s poli, která agent čte.
POSTS_URL = f"{BASE_URL}/wp-json/wp/v2/posts?per_page=30&_fields=id,date_gmt,title,content"
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
class Article:
    id: int
    published_at: datetime
    title: str
    text: str


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
    role = "sběrný agent KIQ 3: články o lihovinách z oborového magazínu zboziaprodej.cz"
    # robots.txt zboziaprodej.cz crawl-delay neuvádí; na Zadání jde jen jeden požadavek.
    fetch_delay = 1.0

    def handle_request(self, request: Message) -> dict[str, Any]:
        page = self.fetcher.get(POSTS_URL, minimize=_without_texts)
        articles = _parse_articles(page)
        seen = self.board.article_ids(source=SOURCE)
        new = [article for article in articles if article.id not in seen]

        # Každý posouzený článek se zapíše hned ve vlastní transakci: chyba LLM u jednoho
        # článku nezahodí ostatní a příští Zadání posoudí jen články, které zůstaly.
        # Snímek vznikne s prvním zápisem, takže když LLM selže u všech, tabule zůstane beze
        # změny a odpovědí je Selhání.
        snapshot = None
        new_findings = 0
        errors: list[Exception] = []
        for article in new:
            try:
                assessment = self._assess(article)
            except Exception as error:
                log.warning("článek %s: posouzení LLM selhalo (%s)", article.id, error)
                errors.append(error)
                continue
            with self.transaction():
                snapshot = snapshot or self._record_snapshot(page, len(articles))
                new_findings += self._record_article(snapshot.id, article, assessment)
        if errors and len(errors) == len(new):
            raise errors[0]
        if snapshot is None:
            with self.transaction():
                snapshot = self._record_snapshot(page, len(articles))
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

    def _record_snapshot(self, page: FetchResult, item_count: int) -> Snapshot:
        return self.board.record_snapshot(
            agent=self.name, source=SOURCE, fetches=[page], item_count=item_count
        )

    def _record_article(self, snapshot_id: int, article: Article, assessment: Assessment) -> int:
        """Zapíše posouzený článek (a z relevantního nález); vrátí počet nových nálezů."""
        finding_id = None
        if assessment.relevant:
            finding_id = self.board.record_finding(
                kiq=3,
                agent=self.name,
                type="clanek",
                snapshot_id=snapshot_id,
                source_url=f"{BASE_URL}/?p={article.id}",
                content=_finding_content(article, assessment),
            ).id
        self.board.record_article(
            source=SOURCE,
            source_article_id=article.id,
            first_seen_snapshot_id=snapshot_id,
            finding_id=finding_id,
        )
        return 0 if finding_id is None else 1

    def _assess(self, article: Article) -> Assessment:
        return self.llm.ask(
            _PROMPT.format(
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
Posuď článek z oborového magazínu zboziaprodej.cz pro přehled komunikace výrobců lihovin.

Relevantní je článek o lihovinách (včetně likérů a RTD, tedy hotových míchaných nápojů
s lihovinou): o jejich výrobcích, značkách, produktech, prodeji, propagaci nebo o trhu
lihovin a alkoholu. Článek jen o pivu, víně, nealkoholických nápojích nebo o maloobchodě
obecně relevantní není.

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


def _parse_articles(page: FetchResult) -> list[Article]:
    """Články z odpovědi API od nejstaršího. Prázdný výpis znamená rozbitý zdroj: Selhání."""
    try:
        articles = [
            Article(
                id=int(post["id"]),
                published_at=datetime.fromisoformat(post["date_gmt"]).replace(tzinfo=UTC),
                title=_plain_text(post["title"]["rendered"]),
                text=_plain_text(post["content"]["rendered"]),
            )
            for post in json.loads(page.text)
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise SourceFormatError(f"{page.url}: nečekaná odpověď API ({error!r})") from error
    if not articles:
        raise SourceFormatError(f"{SOURCE}: API nevrátilo žádný článek (změnil se web?)")
    return sorted(articles, key=lambda article: article.published_at)


def _without_texts(body: bytes) -> bytes:
    """Podoba odpovědi API pro disk: titulky a texty nesou jména osob, zůstane ID, čas vydání
    a odkaz na článek. Z odpovědi, která není výpisem článků, se neuloží nic (jen otisk)."""
    try:
        posts = json.loads(body)
        kept = [
            {"id": post["id"], "date_gmt": post["date_gmt"], "link": f"{BASE_URL}/?p={post['id']}"}
            for post in posts
        ]
    except (KeyError, TypeError, ValueError):
        return b""
    return json.dumps(kept).encode()


def _plain_text(markup: str) -> str:
    """Text z HTML článku: odstavce na řádcích, bez značek a entit."""
    text = re.sub(r"</(?:p|h\d|li|figcaption|blockquote)>|<br\s*/?>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _finding_content(article: Article, assessment: Assessment) -> dict[str, Any]:
    companies = [*assessment.manufacturers, *assessment.brands, *WATCHED_COMPETITORS]
    return {
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
