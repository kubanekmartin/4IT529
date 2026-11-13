"""Regulatorní agent (KIQ 4 Regulace): sněmovní tisky z otevřených dat Poslanecké sněmovny.

Na Zadání stáhne dva archivy otevřených dat psp.cz: sněmovní tisky (`tisky.zip`, asi 4 MB)
a poslance s orgány (`poslanci.zip`, kvůli poslaneckým klubům navrhovatelů). Zdroj je obnovuje
jednou denně v noci a vždy s úplným stavem, takže agent stáhne každý archiv jednou na Zadání,
při denním sběru tedy dvě stažení denně.

Bere tisky aktuálního volebního období jen těch druhů, které navrhují pravidla (návrhy zákonů,
rozpočet, smlouvy, akty EU); interpelace, žádosti o vydání poslance ke stíhání a podobné tisky
regulací nejsou a jména nesou už v názvu. Nový je tisk, který agent ještě neviděl v žádném
dřívějším snímku; v prvním snímku jsou nové všechny tisky období (nález pak má
`baseline: true`). Nové tisky projdou filtrem klíčových slov v názvu a teprve ty, které jím
projdou, posoudí LLM: zda se týkají výrobců lihovin, jaký mají dopad a odkdy mají platit.
Nález vznikne jen u relevantního. Posudky se zapisují po dávkách: tisk, který LLM neposoudil
(chyba volání nebo vynechání), zůstane nový a posoudí se při příštím Zadání.

Jména osob se neukládají ani neposílají LLM: navrhovatele agent skládá z číselníků
(„vláda (min. financí)“, „skupina poslanců (ODS)“, „Senát“), jména poslanců z úplného názvu
vypustí a tabulku osob vůbec nečte. Ani na disk nejdou archivy, jen to, co z nich agent přečetl
(tisky bez jmen, kluby a ID osob v nich) a otisk SHA-256 původního archivu.
"""

import io
import itertools
import json
import logging
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from lihoviny.agent import Agent, OdpovedSberu
from lihoviny.board import Snapshot
from lihoviny.bus import Message
from lihoviny.clock import PRAGUE
from lihoviny.fetch import FetchResult

log = logging.getLogger(__name__)

SOURCE = "psp.cz"
TISKY_URL = "https://www.psp.cz/eknih/cdrom/opendata/tisky.zip"
POSLANCI_URL = "https://www.psp.cz/eknih/cdrom/opendata/poslanci.zip"
TISK_URL = "https://www.psp.cz/sqw/historie.sqw?o={period}&t={number}"

# Filtr před voláním LLM: označení → vzor v názvu tisku (malými písmeny). Názvy tisků bývají
# obecné („Novela z. o spotřebních daních“), o lihovinách rozhodne až LLM.
KEYWORDS = {
    "líh": r"\bl[ií]h",
    "alkohol": r"alkohol",
    "spotřební daň": r"spotřební\w*\s+da[nň]",
    "reklama": r"reklam",
    "prodej": r"prodej",
    "značení": r"značen|označov|etiket",
    "nápoje": r"nápoj",
    "destiláty": r"destilát|\bpálen",
    "návykové látky": r"návyk",
    "obaly": r"\bobal",
    "potraviny": r"potravin",
    "DPH": r"přidané\s+hodnot",
}
# Druhy tisků, které navrhují nebo mění pravidla (druh_tisku.id_druh): návrhy zákonů, rozpočet,
# mezinárodní smlouvy, akty EU a zákonná opatření Senátu. Ostatní druhy (interpelace, „Zpráva
# a jiné“ se žádostmi o vydání poslance ke stíhání nebo návrhy na volbu) regulací nejsou
# a jména osob nesou už ve zkráceném názvu, proto je agent vůbec nečte.
_REGULATION_KINDS = frozenset({"1", "2", "3", "4", "41", "45", "46", "47", "49"})
# Kolik tisků posoudí LLM jedním voláním (výchozí snímek jich má kolem deseti).
BATCH = 20
# Pojistka proti nečekaně velkému souboru v archivu (tisky.unl má dnes asi 12 MB).
MAX_TABLE_BYTES = 100_000_000

_COLUMNS = {
    "tisky": (
        "id_tisk id_druh id_stav ct cislo_za id_navrh id_org id_org_obd id_osoba navrhovatel"
        " nazev_tisku predlozeno rozeslano dal tech_nos_dat uplny_nazev_tisku zm_lhuty lhuta rj"
        " t_url is_eu roz is_sdv status"
    ),
    "druh_tisku": "id_druh druh_t nazev_druh",
    "typ_zakon": "id_navrh druh_navrhovatele",
    "stavy": "id_stav id_typ id_druh popis lhuta lhuta_where",
    "typ_stavu": "id_typ popis_stavu",
    "predkladatel": "id_tisk id_osoba poradi typ",
    "organy": (
        "id_organ organ_id_organ id_typ_organu zkratka nazev_organu_cz nazev_organu_en od_organ"
        " do_organ priorita cl_organ_base"
    ),
    "zarazeni": "id_osoba id_of cl_funkce od_o do_o od_f do_f",
}
_CHAMBER, _CLUB = "11", "1"  # typ_organu: Poslanecká sněmovna (volební období), poslanecký klub
_MP, _MP_GROUP = "2", "3"  # typ_zakon: navrhovatel poslanec, skupina poslanců


class RegulatorniError(Exception):
    pass


@dataclass(frozen=True)
class Tisk:
    """Sněmovní tisk bez jmen osob, jak ho agent zapíše do nálezu."""

    source_id: int
    number: str
    period: int
    title: str
    full_title: str | None
    kind: str
    proposer: str | None
    submitted_on: date | None
    state: str | None
    fast_track: bool

    @property
    def url(self) -> str:
        return TISK_URL.format(period=self.period, number=self.number)

    def keywords(self) -> list[str]:
        text = f"{self.title} {self.full_title or ''}".casefold()
        return [label for label, pattern in KEYWORDS.items() if re.search(pattern, text)]


class Assessment(BaseModel):
    id: int = Field(description="ID tisku, jak je uvedené ve vstupu")
    relevant: bool = Field(description="může tisk změnit pravidla pro výrobce lihovin?")
    impact: str = Field(description="jedna až dvě věty česky: co se může pro výrobce změnit")
    impact_level: Literal["vysoký", "střední", "nízký"]
    effective_date: date | None = Field(
        description="odhad data účinnosti; null, když se z údajů odhadnout nedá"
    )
    effective_note: str = Field(description="z čeho odhad účinnosti vychází, jednou větou")


class Assessments(BaseModel):
    tisky: list[Assessment]


SYSTEM = """\
Jsi analytik regulace pro českého středně velkého výrobce lihovin. Dostaneš sněmovní tisky
(návrhy zákonů a další dokumenty Poslanecké sněmovny), které prošly filtrem klíčových slov.
U každého rozhodni, zda může změnit pravidla pro výrobce lihovin a RTD v ČR: spotřební daň
z lihu, DPH, reklama, prodej a jeho omezení, značení a etikety, obaly a zálohy, ochrana
zdraví před alkoholem, evidence lihu. Názvy tisků bývají obecné: když z nich nejde poznat,
zda se tisk lihovin týká, posuď, jak pravděpodobné to je, a nejistotu řekni v dopadu.
Dopad piš česky, nejvýš dvě věty, konkrétně pro výrobce lihovin. Datum účinnosti odhadni
z názvu, stavu projednávání a obvyklé praxe (daňové novely často od 1. ledna), jinak null.
Nic si nevymýšlej; o tiscích víš jen to, co je ve vstupu.
"""


class Regulatorni(Agent):
    name = "regulatorni"
    role = "sběrný agent KIQ 4: sněmovní tisky o alkoholu z otevřených dat Sněmovny"
    fetch_delay = 2.0  # robots.txt psp.cz crawl-delay neuvádí; za Zadání jsou jen dvě stažení

    def handle_request(self, request: Message) -> dict[str, Any]:
        poslanci, chamber = self._read(POSLANCI_URL, read_chamber)
        archive, tisky = self._read(TISKY_URL, lambda body: read_tisky(body, chamber))
        if not tisky:
            raise RegulatorniError(f"{SOURCE}: v otevřených datech není žádný tisk období")

        baseline = not self.board.snapshots(agent=self.name)
        known = self.board.known_tisky()
        new = [t for t in tisky if t.source_id not in known]
        candidates = [t for t in new if t.keywords()]
        # Tisky bez klíčového slova jsou vyřízené hned; kandidáty zapíše až jejich posudek.
        pending = [t for t in new if not t.keywords()]
        snapshot = None
        new_findings = 0
        errors: list[Exception] = []
        # Každá dávka posudků se zapíše hned ve vlastní transakci: chyba LLM u jedné dávky
        # nezahodí ostatní a tisky bez posudku zůstanou nové pro příští Zadání. Snímek
        # vznikne s prvním zápisem, takže když LLM selže u všech, tabule zůstane beze změny.
        for batch in itertools.batched(candidates, BATCH, strict=False):
            try:
                assessments = self._assess(list(batch))
            except Exception as error:
                log.warning("dávka %s tisků: posouzení LLM selhalo (%s)", len(batch), error)
                errors.append(error)
                continue
            if not assessments:
                errors.append(RegulatorniError("LLM neposoudil žádný tisk z dávky"))
                continue
            with self.transaction():
                snapshot = snapshot or self._record_snapshot([archive, poslanci], len(tisky))
                assessed = [t for t in batch if t.source_id in assessments]
                self.board.record_tisky(snapshot.id, [t.source_id for t in [*pending, *assessed]])
                pending = []
                for tisk in assessed:
                    answer = assessments[tisk.source_id]
                    if answer.relevant:
                        self.board.record_finding(
                            kiq=4,
                            agent=self.name,
                            type="snemovni_tisk",
                            snapshot_id=snapshot.id,
                            source_url=tisk.url,
                            content=_finding_content(tisk, answer, baseline=baseline),
                        )
                        new_findings += 1
        if candidates and snapshot is None:
            raise errors[0]
        with self.transaction():
            snapshot = snapshot or self._record_snapshot([archive, poslanci], len(tisky))
            self.board.record_tisky(snapshot.id, [t.source_id for t in pending])
            # Snímek obsahuje i tisky dřívějších nálezů: data jsou vždy úplný stav.
            in_data = {t.source_id for t in tisky}
            self.board.record_snapshot_findings(
                snapshot.id,
                [
                    f.id
                    for f in self.board.findings(kiq=4)
                    if f.agent == self.name and f.content.get("source_id") in in_data
                ],
            )
        return OdpovedSberu(
            snapshot_id=snapshot.id, new_findings=new_findings, item_count=len(tisky)
        )

    def _record_snapshot(self, archives: list[FetchResult], item_count: int) -> Snapshot:
        return self.board.record_snapshot(
            agent=self.name, source=SOURCE, fetches=archives, item_count=item_count
        )

    def _read[T](self, url: str, reader: Callable[[bytes], T]) -> tuple[FetchResult, T]:
        """Stáhne archiv a přečte ho. Na disk jde jen přečtená podoba bez jmen (JSON) a otisk
        původního archivu: archivy psp.cz obsahují jména poslanců."""
        read: list[T] = []

        def minimize(body: bytes) -> bytes:
            read.append(reader(body))
            return _to_json(read[0])

        return self.fetcher.get(url, minimize=minimize), read[0]

    def _assess(self, batch: list[Tisk]) -> dict[int, Assessment]:
        """Posudky LLM k dávce tisků podle ID; tisk, který LLM vynechal, v nich chybí."""
        today = self.clock.now().astimezone(PRAGUE).date()
        answer = self.llm.ask(_prompt(batch, today), Assessments, effort="low", system=SYSTEM)
        ids = {t.source_id for t in batch}
        assessments = {a.id: a for a in answer.tisky if a.id in ids}
        if missing := [t.number for t in batch if t.source_id not in assessments]:
            log.warning("LLM neposoudil tisky %s, zůstávají nové", ", ".join(missing))
        return assessments


@dataclass(frozen=True)
class Chamber:
    """Probíhající volební období a poslanecké kluby; osoby jen jako ID z otevřených dat."""

    organ_id: str  # id orgánu období, např. "174"
    period: int  # číslo období, např. 10
    clubs: dict[str, str]  # id orgánu klubu → název bez „Poslanecký klub“, např. "ODS"
    # id osoby → členství v klubech: (id klubu, od, do); do None = dosud
    memberships: dict[str, list[tuple[str, date, date | None]]]

    def club(self, person: str, day: date | None) -> str | None:
        """Klub, ve kterém byla osoba v daný den (bez data: v kterémkoli)."""
        for club, since, until in self.memberships.get(person, []):
            if day is None or (since <= day and (until is None or day < until)):
                return self.clubs[club]
        return None


def read_chamber(archive: bytes) -> Chamber:
    """Volební období a kluby z archivu poslanci.zip; tabulku osob (jména) nečte."""
    tables = _tables(archive, "poslanci.zip", "organy zarazeni")
    organ_id, period = _current_period(tables["organy"])
    clubs = {
        row["id_organ"]: row["nazev_organu_cz"].removeprefix("Poslanecký klub ")
        for row in tables["organy"]
        if row["id_typ_organu"] == _CLUB and row["organ_id_organ"] == organ_id
    }
    memberships: dict[str, list[tuple[str, date, date | None]]] = defaultdict(list)
    for row in tables["zarazeni"]:
        if row["cl_funkce"] == "0" and row["id_of"] in clubs:
            since, until = _parse_hour_date(row["od_o"]), _parse_hour_date(row["do_o"])
            if since is not None:
                memberships[row["id_osoba"]].append((row["id_of"], since, until))
    return Chamber(organ_id, period, clubs, dict(memberships))


def read_tisky(archive: bytes, chamber: Chamber) -> list[Tisk]:
    """Tisky probíhajícího období z archivu tisky.zip, jen druhy regulace (`_REGULATION_KINDS`),
    bez jmen."""
    tables = _tables(
        archive, "tisky.zip", "tisky druh_tisku typ_zakon stavy typ_stavu predkladatel"
    )
    kinds = {row["id_druh"]: row["nazev_druh"] for row in tables["druh_tisku"]}
    state_types = {row["id_typ"]: row["popis_stavu"] for row in tables["typ_stavu"]}
    states = {row["id_stav"]: state_types.get(row["id_typ"]) for row in tables["stavy"]}
    proposer = _Proposers(tables, chamber)
    return [
        Tisk(
            source_id=int(row["id_tisk"]),
            number=row["ct"] if row["cislo_za"] in ("", "0") else f"{row['ct']}/{row['cislo_za']}",
            period=chamber.period,
            title=row["nazev_tisku"],
            full_title=_full_title(row),
            kind=kinds.get(row["id_druh"], "sněmovní tisk"),
            proposer=proposer(row),
            submitted_on=_parse_date(row["predlozeno"]),
            state=_state(states.get(row["id_stav"])),
            fast_track=row["rj"] == "1",
        )
        for row in tables["tisky"]
        if row["id_org_obd"] == chamber.organ_id and row["id_druh"] in _REGULATION_KINDS
    ]


class _Proposers:
    """Navrhovatel tisku bez jmen: z druhu navrhovatele a poslaneckých klubů předkladatelů."""

    def __init__(self, tables: dict[str, list[dict[str, str]]], chamber: Chamber):
        self._kinds = {
            row["id_navrh"]: row["druh_navrhovatele"].partition(" - ")[2]
            for row in tables["typ_zakon"]
        }
        self._people: dict[str, list[str]] = defaultdict(list)
        for row in sorted(tables["predkladatel"], key=lambda r: int(r["poradi"] or 0)):
            self._people[row["id_tisk"]].append(row["id_osoba"])
        self._chamber = chamber

    def __call__(self, tisk: dict[str, str]) -> str | None:
        match tisk["id_navrh"]:
            case "2" | "3" as kind:
                label = "poslanec" if kind == _MP else "skupina poslanců"
                people = self._people.get(tisk["id_tisk"]) or [tisk["id_osoba"]]
                parties = Counter(
                    club
                    for person in people
                    if (club := self._chamber.club(person, _parse_date(tisk["predlozeno"])))
                )
                if not parties:
                    return label
                return f"{label} ({', '.join(name for name, _ in parties.most_common())})"
            case "1":
                role = tisk["navrhovatel"]
                return f"vláda ({role})" if _ROLE.match(role) else "vláda"
            case "4":
                return "Senát"
            case kind if kind in self._kinds and self._kinds[kind]:
                return f"zastupitelstvo ({self._kinds[kind]})"
        # Zprávy a další dokumenty: jen funkce nebo zkratka instituce, nikdy jméno.
        text = tisk["navrhovatel"]
        return text if _ROLE.match(text) or _INSTITUTION.fullmatch(text) else None


def _tables(body: bytes, archive_name: str, names: str) -> dict[str, list[dict[str, str]]]:
    """Tabulky UNL z archivu ZIP jako řádky {sloupec: hodnota}."""
    source = f"{SOURCE} {archive_name}"
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as error:
        raise RegulatorniError(f"{source}: není archiv ZIP ({error})") from error
    tables = {}
    with archive:
        for name in names.split():
            try:
                info = archive.getinfo(f"{name}.unl")
            except KeyError:
                raise RegulatorniError(
                    f"{source}: v archivu chybí {name}.unl (změnila se data?)"
                ) from None
            if info.file_size > MAX_TABLE_BYTES:
                raise RegulatorniError(f"{source}: {name}.unl je nečekaně velký")
            columns = _COLUMNS[name].split()
            text = archive.read(info).decode("cp1250")
            tables[name] = [dict(zip(columns, row, strict=False)) for row in _unl_rows(text)]
    return tables


def _unl_rows(text: str) -> list[list[str]]:
    """Řádky formátu UNL: každý sloupec končí znakem |, zpětné lomítko ruší význam znaku za ním
    (i konce řádku uvnitř hodnoty)."""
    rows: list[list[str]] = []
    row: list[str] = []
    for match in _UNL_TOKEN.finditer(text):
        value, end = match.groups()
        if end == "|":
            row.append(_UNL_ESCAPE.sub(r"\1", value))
        elif row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _current_period(organy: list[dict[str, str]]) -> tuple[str, int]:
    """Probíhající volební období: (id orgánu, číslo období), např. ("174", 10)."""
    chambers = [
        row
        for row in organy
        if row["id_typ_organu"] == _CHAMBER and row["zkratka"].startswith("PSP")
    ]
    current = [row for row in chambers if not row["do_organ"]]
    if not current:
        raise RegulatorniError(f"{SOURCE}: v datech orgánů není probíhající volební období")
    latest = max(current, key=lambda row: _parse_date(row["od_organ"]) or date.min)
    return latest["id_organ"], int(latest["zkratka"].removeprefix("PSP"))


def _full_title(tisk: dict[str, str]) -> str | None:
    """Úplný název bez jmen: „Návrh poslanců Jana X a Petra Y na vydání…“ → „Návrh poslanců na
    vydání…“. Návrh poslanců, ve kterém jména vypustit nejde, úplný název nemá."""
    title = tisk["uplny_nazev_tisku"]
    if tisk["id_navrh"] in (_MP, _MP_GROUP) or title.startswith("Návrh poslan"):
        match = _MP_PROPOSAL.match(title)
        return f"{match.group(1)} {title[match.start(2) :]}" if match else None
    return title or None


def _to_json(value: Chamber | list[Tisk]) -> bytes:
    data = [asdict(t) for t in value] if isinstance(value, list) else asdict(value)
    return json.dumps(data, ensure_ascii=False, default=str).encode()


def _finding_content(tisk: Tisk, answer: Assessment, *, baseline: bool) -> dict[str, Any]:
    return {
        "source_id": tisk.source_id,
        "number": tisk.number,
        "period": tisk.period,
        "title": tisk.title,
        "full_title": tisk.full_title,
        "kind": tisk.kind,
        "proposer": tisk.proposer,
        "submitted_on": tisk.submitted_on.isoformat() if tisk.submitted_on else None,
        "state": tisk.state,
        "fast_track": tisk.fast_track,
        "keywords": tisk.keywords(),
        "impact": answer.impact,
        "impact_level": answer.impact_level,
        "effective_date": answer.effective_date.isoformat() if answer.effective_date else None,
        "effective_note": answer.effective_note,
        "baseline": baseline,
    }


def _prompt(tisky: list[Tisk], today: date) -> str:
    parts = [f"Dnes je {_czech(today)}. Posuď tyto sněmovní tisky:"]
    for tisk in tisky:
        details = [
            tisk.kind,
            f"předložen {_czech(tisk.submitted_on)}" if tisk.submitted_on else None,
            f"navrhovatel: {tisk.proposer}" if tisk.proposer else None,
            f"stav: {tisk.state}" if tisk.state else None,
            "navrženo schválit už v prvním čtení" if tisk.fast_track else None,
        ]
        parts.append(
            f"\nID {tisk.source_id}, tisk {tisk.number}: "
            + ", ".join(d for d in details if d)
            + f"\nNázev: {tisk.title}"
            + (f"\nÚplný název: {tisk.full_title}" if tisk.full_title else "")
            + f"\nKlíčová slova: {', '.join(tisk.keywords())}"
        )
    return "\n".join(parts)


def _czech(day: date) -> str:
    return f"{day.day}. {day.month}. {day.year}"


def _state(state: str | None) -> str | None:
    if not state:
        return None
    return "projednávání skončilo" if state == "KONEC" else state


def _parse_date(value: str) -> date | None:
    """„10.03.2026“ → date; prázdná hodnota → None."""
    day, month, year = (value.strip().split(".") + ["", "", ""])[:3]
    return date(int(year), int(month), int(day)) if year else None


def _parse_hour_date(value: str) -> date | None:
    """„2025-10-08 00“ (datum s hodinou) → date; prázdná hodnota → None."""
    return date.fromisoformat(value[:10]) if value.strip() else None


_UNL_TOKEN = re.compile(r"((?:[^|\\\r\n]|\\.)*)(\||\r?\n)", re.S)
_UNL_ESCAPE = re.compile(r"\\(.)", re.S)
_MP_PROPOSAL = re.compile(
    r"^(Návrh\s+poslan\w*(?:\s+a\s+poslan\w*)?)\s.*?,?\s+(na\s+(?:vydání|přijetí)\b)", re.S
)
# Funkce člena vlády nebo vláda sama („min. financí“, „ministr vnitra“, „předseda vlády“).
_ROLE = re.compile(r"(?:min\.|ministr|předsed\w* vlády|vlád)")
# Zkratka instituce velkými písmeny („ČNB“, „NKÚ“, „GA ČR“).
_INSTITUTION = re.compile(r"[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]{2,}(?: [A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]{2,})*")
