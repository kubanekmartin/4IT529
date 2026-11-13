"""Analytický agent: na Zadání řídicího agenta napíše ze sdílené tabule týdenní Brief.

1. Podklady: nálezy za posledních 7 dní (i starší akční ceny, které snímek z týdne viděl
   znovu), výrobce u každého nálezu a Selhání posledního sběru z obsahu Zadání.
2. Text Briefu (tři části) napíše LLM s vyšším effortem z očíslovaného seznamu nálezů.
3. Agent text zkontroluje: tvrzení, které se neopírá o žádný nález ze seznamu, vypustí;
   tvrzení jen o výrobcích mimo sledované konkurenty přesune do Netypických nálezů;
   tvrzení o sledovaných konkurentech dá v každé KIQ nahoru.
4. Grafy spočítá sám z nálezů (ne z textu LLM) a Brief uloží i s nálezy, o které se opírá.

KIQ bez nálezů („bez dat“) a Selhání sběru uvádí Brief sám, LLM o nich nepíše. Když za
období není jediný nález, LLM se nevolá a Brief vyjde jen s tím, co chybí.

Smyčka Dotazů: když sběr některé KIQ v posledním běhu selhal nebo vrátil prázdný snímek,
pošle agent ještě před psaním řídicímu agentovi Dotaz (nejvýš 3, jeden za KIQ, jedno kolo)
a na Zadání Briefu odpoví až po odpovědích na ně: podklady sestaví znovu, takže Brief
obsahuje i doplněné nálezy, a mezeru, kterou doplnění nezaplnilo, uvede i s důvodem.
Na co agent čeká, čte z konverzace na sběrnici, takže to přežije i restart služby; Brief, na
jehož Dotazy už všechno přišlo, ale agent ho kvůli pádu nedopsal, dopíše `on_idle`.
"""

import json
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from lihoviny.agent import ANSWER_LATER, Agent, AnswerLater
from lihoviny.board import Finding, Product
from lihoviny.bus import Kind, Message
from lihoviny.charts import Chart, bar_chart, line_chart, paired_bar_chart
from lihoviny.clock import PRAGUE
from lihoviny.products import CATEGORIES, plain_text
from lihoviny.zadavatel import PORTFOLIO, WATCHED_COMPETITORS

log = logging.getLogger(__name__)

PERIOD = timedelta(days=7)
# Psaní Briefu je delší úloha než třídění u sběrných agentů: vyšší effort a víc tokenů.
# Jedno volání trvá nejvýš BRIEF_TIMEOUT × (1 + RETRIES) = 4 min × 3 = 12 minut. Řídicí agent
# dává na Zadání Briefu 30 minut; odpovědi na Dotazy přijdou nejpozději v 15. minutě, takže
# i Brief po kole Dotazů je hotový do 27. minuty.
BRIEF_EFFORT = "high"
BRIEF_TIMEOUT = 240.0
BRIEF_MAX_TOKENS = 32000
MAX_DOTAZY = 3

KIQS = {1: "Sortiment", 2: "Promo ceny", 3: "Komunikace", 4: "Regulace"}
# Jeden sběrný agent na jednu KIQ (spec #15); podle toho Brief přiřadí Selhání ke KIQ.
KIQ_OF_AGENT = {"sortimentni": 1, "cenovy": 2, "medialni": 3, "regulatorni": 4}
# Kategorie akční ceny podle výpisu akcniceny.cz, dokud ji párování (#22) nezná z produktu.
LISTING_CATEGORIES = {
    "tuzemák, rum": "rum_tuzemak",
    "vodka": "vodka",
    "whisky": "whisky",
    "gin": "gin",
    "brandy": "brandy",
    "tequila": "ostatni",
    "likéry": "ostatni_likery",
    "bylinné destiláty": "horke_likery",
    "ovocné destiláty": "ovocne_destilaty",
    "míchaný nápoj": "rtd",
}
PSP_SOURCE = "Poslanecká sněmovna PČR, otevřená data psp.cz"
# Strop nálezů jedné KIQ v zadání pro LLM; přednost mají sledovaní konkurenti a hluboké slevy.
PROMPT_LIMIT = 300
TOP_CHAINS = 10

_SYSTEM = """\
Jsi analytický agent systému konkurenčního zpravodajství pro zadavatele, středně velkého
českého výrobce lihovin. Píšeš týdenní Brief pro obchodního a marketingového ředitele, který
podle něj plánuje promo akce a portfolio na 12 měsíců dopředu.

Pravidla:
- Piš česky a věcně, každé tvrzení nejvýš dvě věty. Říkej „akční cena“, ne „akce“ ani „sleva“.
- Každé tvrzení se opírá o nálezy ze seznamu: do `sources` dej jejich čísla (u [F12] je to 12).
  Netvrď nic, co v nálezech není, a nevymýšlej čísla.
- what_happened: pro každou KIQ s nálezy 1–5 nejdůležitějších tvrzení. Přednost mají sledovaní
  konkurenti; o ostatních výrobcích piš jen v unusual. Regulace a zprávy o celém trhu patří sem.
- unusual: 0–5 netypických nálezů, například výrazná akční cena nebo novinka výrobce mimo
  sledované konkurenty, nebo souvislost mezi KIQ.
- recommendations: 2–6 doporučení, každé ke konkrétnímu produktu z portfolia zadavatele
  (`product` = jeho kód), se zdůvodněním z nálezů.
- Neuváděj jména osob. KIQ bez nálezů a selhání sběru Brief uvede sám, nepiš o nich."""


class Claim(BaseModel):
    text: str = Field(description="Jedno tvrzení česky, nejvýš dvě věty.")
    sources: list[int] = Field(description="Čísla nálezů, o které se tvrzení opírá (u [F12] 12).")


class KiqClaims(BaseModel):
    kiq: int = Field(description="Číslo KIQ 1–4.")
    claims: list[Claim]


class Recommendation(BaseModel):
    product: str = Field(description="Kód produktu z portfolia zadavatele.")
    text: str = Field(description="Co s produktem udělat a proč, nejvýš dvě věty.")
    sources: list[int] = Field(description="Čísla nálezů, o které se doporučení opírá.")


class BriefText(BaseModel):
    what_happened: list[KiqClaims]
    unusual: list[Claim]
    recommendations: list[Recommendation]


@dataclass(frozen=True)
class Podklady:
    """Co analytický agent ví při psaní Briefu; z toho vzniká text i grafy."""

    beh_id: int | None
    since: datetime
    until: datetime
    findings: dict[int, Finding]
    products: dict[int, Product]
    manufacturers: dict[int, list[str]]  # ID nálezu → výrobci (prázdný seznam = neznámí)
    failures: list[dict[str, Any]]
    kiqs_with_snapshot: set[int]  # KIQ, jejíž sběrný agent už zapsal aspoň jeden snímek

    def kiq_findings(self, kiq: int) -> list[Finding]:
        return [f for f in self.findings.values() if f.kiq == kiq]

    def competitors(self, finding_ids: Iterable[int]) -> list[str]:
        found = {maker for i in finding_ids for maker in self.manufacturers[i]}
        return [name for name in WATCHED_COMPETITORS if name in found]

    def is_watched(self, finding: Finding) -> bool:
        return any(maker in WATCHED_COMPETITORS for maker in self.manufacturers[finding.id])


@dataclass(frozen=True)
class WaitingBrief:
    """Zadání Briefu, na které agent ještě neodpověděl, a jeho Dotazy s odpověďmi."""

    request: Message
    dotazy: dict[int, int]  # ID Dotazu → KIQ
    answers: dict[int, Message]  # KIQ → odpověď řídicího agenta na Dotaz

    @property
    def all_answered(self) -> bool:
        return len(self.answers) == len(self.dotazy)


class Analyticky(Agent):
    name = "analyticky"
    role = "analýza a šíření: týdenní Brief ze sdílené tabule pro ředitele zadavatele"
    llm_timeout = BRIEF_TIMEOUT
    llm_max_tokens = BRIEF_MAX_TOKENS
    # Komu posílá Dotazy. Zadání od někoho jiného (ruční pokus z CLI) vyjde bez Dotazů.
    ridici_agent: ClassVar[str] = "ridici"

    def handle_request(self, request: Message) -> dict[str, Any] | AnswerLater:
        dotazy = _dotazy(request.content) if request.sender == self.ridici_agent else []
        if not dotazy:
            return self._brief(request.content, answers={})
        # Všechny Dotazy najednou: řídicí agent je tak dostane dřív než první odpověď
        # sběrného agenta a kolo Dotazů je opravdu jedno.
        with self.transaction():
            for dotaz in dotazy:
                self.bus.send(
                    kind=Kind.QUERY,
                    sender=self.name,
                    recipient=request.sender,
                    content=dotaz,
                    conversation_id=request.conversation_id,
                )
        return ANSWER_LATER

    def handle_message(self, message: Message) -> None:
        if message.kind in (Kind.INFORM, Kind.FAILURE) and "dotaz_id" in message.content:
            self._dotaz_answered(message)
        else:
            super().handle_message(message)

    def on_idle(self) -> None:
        """Dopíše Brief, na jehož Dotazy už přišly všechny odpovědi, ale agent ho nenapsal:
        spadl hned po vyzvednutí poslední odpovědi (zpráva je vyzvednutá, `handle_message`
        ji už nedostane)."""
        for zadani in self.board.unanswered_zadani(agent=self.name):
            conversation = self.bus.conversation(zadani.conversation_id)
            waiting = _waiting_brief(conversation, self.name)
            if waiting is not None and waiting.dotazy and waiting.all_answered:
                log.info("%s: dopisuje Brief po restartu (%s)", self.name, zadani.conversation_id)
                self._finish_brief(waiting)

    def _dotaz_answered(self, answer: Message) -> None:
        """Po odpovědi na poslední Dotaz agent Brief dopíše a odpoví na Zadání Briefu."""
        waiting = _waiting_brief(self.bus.conversation(answer.conversation_id), self.name)
        if waiting is None:
            log.info("%s: odpověď %s na Dotaz, Brief už je hotový", self.name, answer.id)
        elif waiting.all_answered:
            self._finish_brief(waiting)

    def _finish_brief(self, waiting: WaitingBrief) -> None:
        self.answer(waiting.request, lambda: self._brief(waiting.request.content, waiting.answers))

    def _brief(self, content: dict[str, Any], answers: dict[int, Message]) -> dict[str, Any]:
        """Napíše a uloží Brief; `answers` jsou odpovědi na Dotazy podle KIQ."""
        podklady = self._podklady(content, answers)
        text = (
            self._write(podklady)
            if podklady.findings
            else BriefText(what_happened=[], unusual=[], recommendations=[])
        )
        return self._publish(podklady, text)

    def _podklady(self, content: dict[str, Any], answers: dict[int, Message]) -> Podklady:
        until = self.clock.now()
        since = until - PERIOD
        findings = {f.id: f for f in self.board.findings_seen(since=since, until=until)}
        products = {p.id: p for p in self.board.products()}
        makers = _brand_makers(products.values())
        beh_id = content.get("beh_id")
        return Podklady(
            beh_id=beh_id if type(beh_id) is int else None,  # bool je také int, ale ID ne
            since=since,
            until=until,
            findings=findings,
            products=products,
            manufacturers={i: _manufacturers(f, products, makers) for i, f in findings.items()},
            failures=_failures(content, answers),
            kiqs_with_snapshot={
                kiq for agent, kiq in KIQ_OF_AGENT.items() if self.board.snapshots(agent=agent)
            },
        )

    def _write(self, podklady: Podklady) -> BriefText:
        return self.llm.ask(_prompt(podklady), BriefText, effort=BRIEF_EFFORT, system=_SYSTEM)

    def _publish(self, podklady: Podklady, text: BriefText) -> dict[str, Any]:
        claims_by_kiq, unusual = _sorted_claims(podklady, text)
        what_happened = [_kiq_section(podklady, kiq, claims_by_kiq[kiq]) for kiq in KIQS]
        recommendations = _recommendations(podklady, text.recommendations)
        all_claims = [*(c for kiq in what_happened for c in kiq["claims"]), *unusual]
        cited = [s["finding_id"] for c in [*all_claims, *recommendations] for s in c["sources"]]
        charts = [chart.as_dict() for chart in self._charts(podklady)]
        with self.transaction():
            brief = self.board.record_brief(
                beh_id=podklady.beh_id,
                period_start=podklady.since,
                what_happened=what_happened,
                unusual=unusual,
                recommendations=recommendations,
                charts=charts,
                failures=podklady.failures,
                finding_ids=cited,
            )
        return {"brief_id": brief.id}

    def _charts(self, podklady: Podklady) -> list[Chart]:
        akcni_ceny = [f for f in podklady.findings.values() if f.type == "akcni_cena"]
        return [
            _chains_chart(akcni_ceny),
            self._competitors_chart(podklady),
            _portfolio_chart(podklady, akcni_ceny),
            _novinky_chart(podklady),
        ]

    def _competitors_chart(self, podklady: Podklady) -> Chart:
        """Kolik akčních cen sledovaných konkurentů ukazoval zdroj v denních snímcích."""
        by_day = {}
        for snapshot in self.board.snapshots(agent="cenovy"):
            if podklady.since < snapshot.taken_at <= podklady.until:
                # Za den platí poslední snímek (ruční běh týž den přepíše ranní).
                by_day[snapshot.taken_at.astimezone(PRAGUE).date()] = snapshot.id
        days = sorted(by_day)
        counts = [
            Counter(
                maker
                for i in self.board.snapshot_finding_ids(by_day[day])
                for maker in podklady.manufacturers.get(i, [])
            )
            for day in days
        ]
        series = [(name, [c[name] for c in counts]) for name in WATCHED_COMPETITORS]
        return line_chart(
            "Akční ceny sledovaných konkurentů podle dne",
            "Den",
            [f"{day.day}. {day.month}." for day in days],
            series,
            _count,
        )


def _failures(content: dict[str, Any], answers: dict[int, Message]) -> list[dict[str, Any]]:
    """Mezery ve sběru z výsledků posledního sběru, které řídicí agent přikládá k Zadání.

    `answers` jsou odpovědi na Dotazy podle KIQ: mezeru, kterou Zadání z Dotazu zaplnilo,
    Brief neuvádí; u nezaplněné připíše, proč doplnění nevyšlo."""
    if "collection" not in content:
        return [_gap(None, "Zadání nepřišlo od řídicího agenta, výsledky sběru nejsou známé")]
    collection = content["collection"]
    if not isinstance(collection, dict) or not isinstance(collection.get("results"), dict):
        return [_gap(None, "zatím neproběhl žádný sběr")]
    failures = []
    for agent, reason, _ in _collection_gaps(collection["results"]):
        answer = answers.get(KIQ_OF_AGENT.get(agent))
        if answer is not None:
            if (doplneni := _doplneni_gap(answer)) is None:
                continue
            reason = f"{reason}; {doplneni}"
        failures.append(_gap(agent, reason))
    return failures


def _collection_gaps(results: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """Mezery posledního sběru po agentech: (agent, důvod, může je zaplnit nový sběr?).

    Sběr, který selhal nebo vrátil prázdný snímek, může nové Zadání zopakovat; sběr, který
    ještě neodpověděl, pořád běží a nové Zadání by ho jen zdvojilo."""
    gaps = []
    for agent, result in sorted(results.items()):
        if not isinstance(result, dict):
            continue
        match result.get("outcome"):
            case "FAILURE":
                gaps.append((agent, str(result.get("reason") or "bez důvodu"), True))
            case None:
                gaps.append((agent, "v době psaní Briefu sběr ještě neodpověděl", False))
            case _ if result.get("item_count") == 0:
                gaps.append((agent, "prázdný snímek (0 položek), zdroj je asi rozbitý", True))
    return gaps


def _doplneni_gap(answer: Message) -> str | None:
    """Co z mezery zůstalo po odpovědi na Dotaz; None = doplňující sběr ji zaplnil."""
    if answer.kind is Kind.FAILURE:
        return f"doplňující sběr na Dotaz selhal: {answer.content.get('reason') or 'bez důvodu'}"
    if answer.content.get("item_count") == 0:
        return "doplňující sběr na Dotaz vrátil prázdný snímek"
    return None


def _dotazy(content: dict[str, Any]) -> list[dict[str, Any]]:
    """Dotazy řídicímu agentovi: KIQ, co chybí a návrh doplňujícího sběru, za každou KIQ,
    jejíž mezeru může nový sběr zaplnit; nejvýš MAX_DOTAZY podle čísla KIQ."""
    collection = content.get("collection")
    if not isinstance(collection, dict) or not isinstance(collection.get("results"), dict):
        return []
    dotazy = []
    for agent, reason, recollect in _collection_gaps(collection["results"]):
        kiq = KIQ_OF_AGENT.get(agent)
        if kiq is None or not recollect:
            continue
        dotazy.append(
            {
                "kiq": kiq,
                "missing": f"KIQ {kiq} {KIQS[kiq]}: chybí data z posledního sběru"
                f" ({agent}: {reason})",
                "proposal": f"Zopakovat sběr agenta {agent}, aby Brief měl pro KIQ {kiq}"
                " aktuální snímek.",
            }
        )
    return sorted(dotazy, key=lambda dotaz: dotaz["kiq"])[:MAX_DOTAZY]


def _waiting_brief(conversation: list[Message], name: str) -> WaitingBrief | None:
    """Zadání Briefu z konverzace, na které agent `name` ještě neodpověděl, s jeho Dotazy
    a odpověďmi na ně. Stav se nikam neukládá: celý je na sběrnici a přežije restart."""
    request = next(
        (m for m in conversation if m.kind is Kind.REQUEST and m.recipient == name), None
    )
    replied = any(m.sender == name and m.kind in (Kind.INFORM, Kind.FAILURE) for m in conversation)
    if request is None or replied:
        return None
    dotazy = {
        m.id: m.content["kiq"] for m in conversation if m.kind is Kind.QUERY and m.sender == name
    }
    answers = {}
    for m in conversation:
        dotaz_id = m.content.get("dotaz_id")
        if m.recipient == name and type(dotaz_id) is int and dotaz_id in dotazy:
            answers[dotazy[dotaz_id]] = m
    return WaitingBrief(request, dotazy, answers)


def _gap(agent: str | None, reason: str) -> dict[str, Any]:
    return {"agent": agent, "kiq": KIQ_OF_AGENT.get(agent or ""), "reason": reason}


def _kiq_section(podklady: Podklady, kiq: int, claims: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(podklady.kiq_findings(kiq))
    no_data = None
    if count == 0 and kiq in podklady.kiqs_with_snapshot:
        no_data = "za období žádný nový nález"
    elif count == 0:
        no_data = "sběr pro tuto KIQ zatím nezachytil žádný snímek"
    failures = [f["reason"] for f in podklady.failures if f["kiq"] == kiq]
    return {
        "kiq": kiq,
        "name": KIQS[kiq],
        "finding_count": count,
        "no_data": no_data,
        "failure": "; ".join(failures) or None,
        # Sledovaní konkurenti nahoře, jinak v pořadí, které zvolil LLM.
        "claims": sorted(claims, key=lambda claim: not claim["competitors"]),
    }


def _sorted_claims(
    podklady: Podklady, text: BriefText
) -> tuple[dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Ověřená tvrzení podle KIQ a Netypické nálezy. Tvrzení z Co se stalo, které mluví jen
    o výrobcích mimo sledované konkurenty, patří podle specu mezi Netypické nálezy."""
    claims_by_kiq: dict[int, list[dict[str, Any]]] = {kiq: [] for kiq in KIQS}
    unusual = []
    for section in text.what_happened:
        for claim in section.claims:
            checked = _checked_claim(podklady, claim.text, claim.sources)
            if checked is None or section.kiq not in KIQS:
                continue
            if _only_other_manufacturers(podklady, checked):
                unusual.append(checked)
            else:
                claims_by_kiq[section.kiq].append(checked)
    for claim in text.unusual:
        if checked := _checked_claim(podklady, claim.text, claim.sources):
            unusual.append(checked)
    return claims_by_kiq, unusual


def _recommendations(
    podklady: Podklady, recommendations: list[Recommendation]
) -> list[dict[str, Any]]:
    """Doporučení k produktům z portfolia zadavatele; jiný kód produktu se vypustí."""
    portfolio = {product.code: product for product in PORTFOLIO}
    checked_items = []
    for item in recommendations:
        checked = _checked_claim(podklady, item.text, item.sources)
        if checked and item.product in portfolio:
            product = portfolio[item.product]
            checked_items.append({"product": product.code, "product_name": product.name, **checked})
    return checked_items


def _checked_claim(podklady: Podklady, text: str, sources: list[int]) -> dict[str, Any] | None:
    """Tvrzení s odkazy na zdroje, nebo None, když se neopírá o žádný nález z podkladů."""
    known = [i for i in dict.fromkeys(sources) if i in podklady.findings]
    if len(known) < len(sources):
        log.warning("analyticky: LLM citoval neznámé nálezy %s", sorted(set(sources) - set(known)))
    if not known or not text.strip():
        return None
    return {
        "text": text.strip(),
        "sources": [_source(podklady.findings[i]) for i in known],
        "competitors": podklady.competitors(known),
    }


def _only_other_manufacturers(podklady: Podklady, claim: dict[str, Any]) -> bool:
    """Tvrzení, jehož nálezy mají všechny známého výrobce a žádný není sledovaný konkurent."""
    makers = [podklady.manufacturers[s["finding_id"]] for s in claim["sources"]]
    return not claim["competitors"] and all(makers)


def _source(finding: Finding) -> dict[str, Any]:
    url = finding.source_url
    # Odkaz jde na web do `href`; jiný než http(s) (třeba `javascript:`) se nevypíše.
    safe = urlsplit(url).scheme in ("http", "https")
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return {"finding_id": finding.id, "url": url if safe else None, "label": _label(finding, host)}


def _label(finding: Finding, host: str) -> str:
    content = finding.content
    match finding.type:
        case "akcni_cena":
            what = f"{content.get('product')}, {content.get('chain')}"
        case "novinka":
            what = f"Novinka {content.get('name')}"
        case "snemovni_tisk":
            # Licence otevřených dat Sněmovny žádá uvést zdroj (viz stránka Regulace).
            return f"Tisk {content.get('number')}: {content.get('title')} ({PSP_SOURCE})"
        case _:
            what = str(content.get("title") or content.get("name") or finding.type)
    return f"{what} ({host})" if host else what


def _brand_makers(products: Iterable[Product]) -> dict[str, str]:
    """Značka (bez diakritiky) → výrobce, jak ho u produktů zapsal sortimentní agent."""
    return {plain_text(p.brand): p.manufacturer for p in products if p.manufacturer}


def _manufacturers(
    finding: Finding, products: dict[int, Product], brand_makers: dict[str, str]
) -> list[str]:
    """Výrobci nálezu: ze spárovaného produktu, z obsahu nálezu (Novinka jednoho, článek
    i několik), nebo podle značky na začátku názvu nespárované akční ceny. Jméno sledovaného
    konkurenta se sjednotí („Stock Plzeň-Božkov“ → „Stock“)."""
    content = finding.content
    product = products.get(finding.product_id)
    names: list[str] = []
    if product is not None and product.manufacturer:
        names = [product.manufacturer]
    elif isinstance(content.get("manufacturer"), str):
        names = [content["manufacturer"]]
    elif isinstance(content.get("manufacturers"), list):
        names = [name for name in content["manufacturers"] if isinstance(name, str)]
    elif isinstance(content.get("product"), str):
        name = plain_text(content["product"])
        brands = [b for b in brand_makers if name == b or name.startswith(b + " ")]
        names = [brand_makers[max(brands, key=len)]] if brands else []
    return list(dict.fromkeys(_watched_name(name) for name in names))


def _watched_name(maker: str) -> str:
    for competitor in WATCHED_COMPETITORS:
        if plain_text(competitor) in plain_text(maker):
            return competitor
    return maker


def _prompt(podklady: Podklady) -> str:
    lines = [
        f"Období Briefu: {_when(podklady.since)} – {_when(podklady.until)}",
        "",
        "Portfolio zadavatele (kód: produkt, kategorie, běžná cena, Kč za litr etanolu):",
        *(
            f"- {p.code}: {p.name}, {CATEGORIES[p.category]}, {_kc(p.regular_price)}, "
            f"{_whole(p.price_per_l_ethanol)} Kč/l etanolu"
            for p in PORTFOLIO
        ),
        "",
        f"Sledovaní konkurenti: {', '.join(WATCHED_COMPETITORS)}",
        "",
        "Mezery ve sběru (Brief je uvede sám): "
        + ("; ".join(_gap_text(f) for f in podklady.failures) or "žádné"),
    ]
    for kiq, name in KIQS.items():
        findings = podklady.kiq_findings(kiq)
        lines += ["", f"KIQ {kiq} {name}: {len(findings) or 'bez dat'} nálezů"]
        ordered = sorted(findings, key=lambda f: _priority(podklady, f))
        lines += [_digest(podklady, f) for f in ordered[:PROMPT_LIMIT]]
        if len(ordered) > PROMPT_LIMIT:
            lines.append(f"(dalších {len(ordered) - PROMPT_LIMIT} nálezů vynecháno)")
    return "\n".join(lines)


def _gap_text(failure: dict[str, Any]) -> str:
    where = f"KIQ {failure['kiq']} ({failure['agent']})" if failure["kiq"] else "sběr"
    return f"{where}: {failure['reason']}"


def _priority(podklady: Podklady, finding: Finding) -> tuple[bool, int, int]:
    """Pořadí v zadání pro LLM: sledovaní konkurenti, pak hlubší sleva, pak novější nález."""
    discount = finding.content.get("discount_percent")
    return (
        not podklady.is_watched(finding),
        -discount if type(discount) is int else 0,
        -finding.id,
    )


def _digest(podklady: Podklady, finding: Finding) -> str:
    """Jeden řádek o nálezu pro LLM; neznámé typy nálezů jako zkrácený JSON obsahu."""
    match finding.type:
        case "akcni_cena":
            parts = _akcni_cena_parts(podklady, finding)
        case "novinka":
            parts = _novinka_parts(finding.content)
        case "clanek":
            parts = _clanek_parts(finding.content)
        case "snemovni_tisk":
            parts = _tisk_parts(finding.content)
        case _:
            parts = [f"{finding.type}: {json.dumps(finding.content, ensure_ascii=False)[:600]}"]
    if makers := podklady.manufacturers[finding.id]:
        named = [f"{m} (sledovaný konkurent)" if m in WATCHED_COMPETITORS else m for m in makers]
        parts.append("výrobce " + ", ".join(named))
    return f"[F{finding.id}] " + ", ".join(parts)


def _akcni_cena_parts(podklady: Podklady, finding: Finding) -> list[str]:
    content = finding.content
    parts = [f"akční cena: {content.get('product')}", str(content.get("chain"))]
    parts.append(_kc(content.get("price")))
    if content.get("discount_percent"):
        parts.append(f"sleva {content['discount_percent']} %")
    if content.get("card_price") is not None:
        parts.append(f"s kartou {content.get('card_label')} {_kc(content['card_price'])}")
    if content.get("valid_until"):
        parts.append(f"platí do {content['valid_until']}")
    if (ethanol := _ethanol_price(podklady, finding)) is not None:
        parts.append(f"{_whole(ethanol)} Kč/l etanolu")
    return parts


def _novinka_parts(content: dict[str, Any]) -> list[str]:
    category = CATEGORIES.get(content.get("category"), content.get("category"))
    parts = [f"novinka: {content.get('name')}", f"značka {content.get('brand')}", str(category)]
    if content.get("note"):
        parts.append(str(content["note"]))
    return parts


def _clanek_parts(content: dict[str, Any]) -> list[str]:
    """Článek mediálního agenta (#23); `published_at` je datum vydání, ne zjištění."""
    parts = [f"článek: {content.get('title')}"]
    if isinstance(content.get("published_at"), str):
        published = datetime.fromisoformat(content["published_at"]).astimezone(PRAGUE)
        parts.append(f"vyšel {published.day}. {published.month}. {published.year}")
    parts.append(str(content.get("summary")))
    return parts


def _tisk_parts(content: dict[str, Any]) -> list[str]:
    """Sněmovní tisk regulatorního agenta (#24); tisk z výchozího snímku není nový."""
    parts = [
        f"sněmovní tisk {content.get('number')}: {content.get('title')}",
        str(content.get("kind")),
        str(content.get("proposer")),
        f"stav {content.get('state')}",
        f"dopad {content.get('impact_level')}: {content.get('impact')}",
    ]
    if content.get("effective_date"):
        parts.append(f"účinnost asi od {content['effective_date']}")
    if content.get("baseline"):
        parts.append("projednává se už od začátku sledování")
    return parts


def _ethanol_price(podklady: Podklady, finding: Finding) -> float | None:
    """Cena za litr etanolu: od cenového agenta, nebo ze spárovaného produktu (objem, alkohol)."""
    ethanol = finding.content.get("price_per_l_ethanol")
    if isinstance(ethanol, int | float):
        return float(ethanol)
    product = podklady.products.get(finding.product_id)
    price = finding.content.get("price")
    if product is None or not isinstance(price, int | float):
        return None
    return price / (product.volume_ml / 1000 * float(product.abv) / 100)


def _category(podklady: Podklady, finding: Finding) -> str | None:
    product = podklady.products.get(finding.product_id)
    if product is not None:
        return product.category
    return LISTING_CATEGORIES.get(finding.content.get("source_category"))


def _chains_chart(akcni_ceny: list[Finding]) -> Chart:
    chains = Counter(str(f.content.get("chain")) for f in akcni_ceny)
    items = sorted(chains.items(), key=lambda item: (-item[1], item[0]))[:TOP_CHAINS]
    return bar_chart("Akční ceny podle řetězce", ("Řetězec", "Akční ceny"), items, _count)


def _portfolio_chart(podklady: Podklady, akcni_ceny: list[Finding]) -> Chart:
    """Běžná cena produktu z portfolia za litr etanolu proti nejnižší akční ceně v kategorii."""
    cheapest: dict[str, tuple[float, Finding]] = {}
    for finding in akcni_ceny:
        category, ethanol = _category(podklady, finding), _ethanol_price(podklady, finding)
        if category is None or ethanol is None:
            continue
        if category not in cheapest or ethanol < cheapest[category][0]:
            cheapest[category] = (ethanol, finding)
    items = []
    for product in PORTFOLIO:
        own = float(product.price_per_l_ethanol)
        if product.category in cheapest:
            ethanol, finding = cheapest[product.category]
            note = f"{finding.content.get('product')}, {finding.content.get('chain')}"
            items.append((product.name, own, ethanol, note))
        else:
            items.append((product.name, own, None, ""))
    return paired_bar_chart(
        "Cena za litr etanolu: portfolio zadavatele a nejnižší akční cena v kategorii",
        ("Produkt", "Běžná cena portfolia", "Nejnižší akční cena v kategorii"),
        items,
        lambda value: f"{_whole(value)} Kč",
        "bez akční ceny se známým obsahem alkoholu",
    )


def _novinky_chart(podklady: Podklady) -> Chart:
    categories = Counter(
        str(f.content.get("category")) for f in podklady.findings.values() if f.type == "novinka"
    )
    items = [
        (CATEGORIES.get(code, code), count)
        for code, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    ]
    return bar_chart("Novinky podle kategorie", ("Kategorie", "Novinky"), items, _count)


def _when(value: datetime) -> str:
    local = value.astimezone(PRAGUE)
    return f"{local.day}. {local.month}. {local.year} {local.hour}:{local:%M}"


def _kc(value: Any) -> str:
    if not isinstance(value, int | float | Decimal):
        return "cena neuvedena"
    return f"{value:,.2f}".replace(",", "\xa0").replace(".", ",") + " Kč"


def _whole(value: float | Decimal) -> str:
    return f"{value:,.0f}".replace(",", "\xa0")


def _count(value: float) -> str:
    return f"{value:.0f}"
