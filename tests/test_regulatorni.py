"""Regulatorní agent (KIQ 4) přes sběrnici a tabuli nad ukázkou otevřených dat Sněmovny.

`fixtures/psp/` jsou zmenšené tabulky ze skutečných archivů `tisky.zip` a `poslanci.zip`
z 13. 11. 2026 (formát UNL, kódování cp1250). Jména poslanců jsou v nich nahrazená
smyšlenými (Vzorový, Ukázka, Pokusný, Smyšlený, Testová, Vymyšlený), aby test mohl ukázat,
že se žádné jméno nedostane na tabuli, do dotazu LLM ani na web. Písemná interpelace
má ručně změněné téma (prodej alkoholu), aby prošla filtrem klíčových slov.
Archiv „prvního dne“ je ukázka bez tisku 321, který přibude „druhý den“.
"""

import hashlib
import io
import zipfile
from pathlib import Path

import psycopg

from lihoviny.agents import regulatorni as regulatorni_module
from lihoviny.agents.regulatorni import Regulatorni
from lihoviny.bus import Kind
from lihoviny.fetch import FakeFetcher, FetchError, read_raw
from lihoviny.llm import FakeLlm, LlmError

FIXTURES = Path(__file__).parent / "fixtures" / "psp"
TISKY_URL = "https://www.psp.cz/eknih/cdrom/opendata/tisky.zip"
POSLANCI_URL = "https://www.psp.cz/eknih/cdrom/opendata/poslanci.zip"
# Smyšlená jména z ukázky (kmeny, aby pokryly i skloňované tvary v názvech tisků).
NAMES = ["Vzorov", "Ukázk", "Pokusn", "Smyšlen", "Testov", "Vymyšlen"]
# ID tisků v ukázce (tisky.id_tisk) podle čísla tisku.
TISK_133, TISK_153, TISK_193, TISK_208, TISK_243 = 43256, 43274, 43314, 43330, 43365
TISK_321, TISK_363 = 43443, 43486


def archive(table_dir, *, skip_tisky=(), drop=(), extra_tisky=()):
    """Archiv ZIP jako od zdroje, složený z tabulek UNL v `table_dir`; `extra_tisky` jsou
    další řádky tabulky tisků."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        for table in sorted((FIXTURES / table_dir).glob("*.unl")):
            if table.name in drop:
                continue
            body = table.read_bytes()
            if table.name == "tisky.unl":
                body = b"".join(
                    line
                    for line in body.splitlines(keepends=True)
                    if line.split(b"|")[0].decode() not in {str(i) for i in skip_tisky}
                ) + b"".join(row.encode("cp1250") + b"\n" for row in extra_tisky)
            zip_file.writestr(table.name, body)
    return buffer.getvalue()


def source_routes(*, day=2, **archive_options):
    skip = [TISK_321] if day == 1 else []
    return {
        TISKY_URL: archive("tisky", skip_tisky=skip, **archive_options),
        POSLANCI_URL: archive("poslanci"),
    }


# Smyšlená žádost o souhlas s trestním stíháním poslance (druh 5 „Zpráva a jiné“): jméno je
# už ve zkráceném názvu a klíčová slova (prodej, alkohol) ji pustí filtrem.
STIHANI = (
    "43500|5|251|400|0|0||174|||Žádost o souhlas s trestním stíháním posl. J. Pokusného"
    " pro prodej alkoholu|01.09.2026|02.09.2026|||Žádost o vydání souhlasu k trestnímu stíhání"
    " poslance Jana Pokusného pro prodej alkoholu nezletilým||||||2026-09-02 10:00|||"
)


def assessment(tisk_id, *, relevant=True, level="střední", effective="2027-01-01"):
    return {
        "id": tisk_id,
        "relevant": relevant,
        "impact": f"Odhad dopadu tisku {tisk_id} na výrobce lihovin.",
        "impact_level": level,
        "effective_date": effective,
        "effective_note": "obvyklý termín u daňových novel",
    }


# Odpověď LLM k výchozímu snímku: kandidáti z filtru klíčových slov; prodejní doba lihoviny
# netrápí.
BASELINE_ANSWER = {
    "tisky": [
        assessment(TISK_133, level="vysoký"),
        assessment(TISK_153),
        assessment(TISK_193, level="nízký", effective=None),
        assessment(TISK_208, level="vysoký"),
        assessment(TISK_243, relevant=False),
    ]
}


def make_regulatorni(make_agent, clock, raw_dir, llm, routes=None):
    fetcher = FakeFetcher(routes or source_routes(day=1), raw_dir=raw_dir, clock=clock)
    return make_agent(Regulatorni, fetcher=fetcher, llm=llm)


def send_zadani(bus):
    return bus.send(kind=Kind.REQUEST, sender="ridici", recipient="regulatorni", content={})


def tisky_on_board(board):
    return {f.content["number"]: f for f in board.findings(kiq=4)}


def test_first_zadani_puts_relevant_snemovni_tisky_of_current_period_on_board(
    bus, board, clock, raw_dir, make_agent
):
    regulatorni = make_regulatorni(make_agent, clock, raw_dir, FakeLlm(BASELINE_ANSWER))
    zadani = send_zadani(bus)

    regulatorni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    snapshot = board.snapshot(reply.content["snapshot_id"])
    # Šest tisků 10. období; interpelace a tisk z 9. období se nepočítají.
    assert reply.content == {"snapshot_id": snapshot.id, "new_findings": 4, "item_count": 6}
    assert (snapshot.agent, snapshot.source, len(snapshot.raw_paths)) == (
        "regulatorni",
        "psp.cz",
        2,
    )
    findings = tisky_on_board(board)
    assert sorted(findings) == ["133", "153", "193", "208"]
    spotrebni_dane = findings["133"]
    assert (spotrebni_dane.kiq, spotrebni_dane.type, spotrebni_dane.snapshot_id) == (
        4,
        "snemovni_tisk",
        snapshot.id,
    )
    assert spotrebni_dane.source_url == "https://www.psp.cz/sqw/historie.sqw?o=10&t=133"
    assert spotrebni_dane.content == {
        "source_id": TISK_133,
        "number": "133",
        "period": 10,
        "title": "Novela z. o spotřebních daních",
        "full_title": "Návrh poslanců na vydání zákona, kterým se mění zákon č. 353/2003 Sb.,"
        " o spotřebních daních, ve znění pozdějších předpisů",
        "kind": "Návrh zákona",
        "proposer": "skupina poslanců (ODS)",
        "submitted_on": "2026-03-10",
        "state": "1. čtení (§ 90, odst. 2) - ob. rozprava",
        "fast_track": True,
        "keywords": ["spotřební daň"],
        "impact": f"Odhad dopadu tisku {TISK_133} na výrobce lihovin.",
        "impact_level": "vysoký",
        "effective_date": "2027-01-01",
        "effective_note": "obvyklý termín u daňových novel",
        "baseline": True,
    }
    assert findings["153"].content["proposer"] == "vláda (min. prům. a obchodu)"
    assert findings["193"].content["proposer"] == "poslanec (ANO 2011)"
    assert findings["193"].content["effective_date"] is None


def test_only_new_tisky_matching_keywords_go_to_llm_with_low_effort(
    bus, clock, raw_dir, make_agent
):
    llm = FakeLlm(BASELINE_ANSWER)
    regulatorni = make_regulatorni(make_agent, clock, raw_dir, llm)
    send_zadani(bus)

    regulatorni.process_pending()

    (call,) = llm.calls
    assert call.effort == "low"
    for tisk_id in [TISK_133, TISK_153, TISK_193, TISK_208, TISK_243]:
        assert f"ID {tisk_id}," in call.prompt
    assert f"ID {TISK_363}," not in call.prompt  # trestní řád: žádné klíčové slovo
    assert "trestní řád" not in call.prompt
    assert "interpelace" not in call.prompt  # interpelace o prodeji alkoholu se neposuzuje
    assert "43287" not in call.prompt
    assert "42831" not in call.prompt  # spotřební daně z 9. období


def test_names_of_mps_never_reach_board_or_llm(bus, clock, raw_dir, make_agent, database_url):
    llm = FakeLlm(BASELINE_ANSWER, {"tisky": [assessment(TISK_321)]})
    regulatorni = make_regulatorni(make_agent, clock, raw_dir, llm)
    send_zadani(bus)
    regulatorni.process_pending()
    send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm, source_routes(day=2)).process_pending()

    with psycopg.connect(database_url) as conn:
        stored = " ".join(
            conn.execute(
                f"SELECT coalesce(string_agg(t::text, ' '), '') FROM {table} t"
            ).fetchone()[0]
            for table in ["findings", "snapshots", "messages", "snemovni_tisky"]
        )
    sent = " ".join(call.prompt + call.system for call in llm.calls)
    for name in NAMES:
        assert name not in stored
        assert name not in sent
    assert "skupina poslanců (KDU-ČSL)" in sent  # místo jmen navrhovatelů tisku 243


def test_tisky_whose_kind_names_a_person_are_left_out_of_raw_data_board_and_llm(
    bus, board, clock, raw_dir, make_agent, database_url
):
    routes = source_routes(day=1, extra_tisky=[STIHANI])
    llm = FakeLlm(BASELINE_ANSWER)
    regulatorni = make_regulatorni(make_agent, clock, raw_dir, llm, routes)
    zadani = send_zadani(bus)

    regulatorni.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.content["item_count"] == 6  # žádost mezi tisky nepočítá
    on_disk = b" ".join(read_raw(path)[1] for path in raw_dir.rglob("*.gz")).decode()
    with psycopg.connect(database_url) as conn:
        stored = conn.execute(
            "SELECT string_agg(t::text, ' ') FROM (SELECT * FROM findings) t"
        ).fetchone()[0]
    assert "Novela z. o spotřebních daních" in on_disk
    for text in [on_disk, stored, llm.calls[0].prompt]:
        assert "Pokusn" not in text
        assert "43500" not in text


def test_raw_data_on_disk_is_minimized_without_names_and_keeps_hash_of_original(
    bus, board, clock, raw_dir, make_agent
):
    routes = source_routes(day=1)
    regulatorni = make_regulatorni(make_agent, clock, raw_dir, FakeLlm(BASELINE_ANSWER), routes)
    zadani = send_zadani(bus)

    regulatorni.process_pending()

    snapshot = board.snapshot(bus.conversation(zadani.conversation_id)[-1].content["snapshot_id"])
    saved = [read_raw(path) for path in snapshot.raw_paths]
    assert sorted((meta["url"], meta["original_sha256"]) for meta, _ in saved) == sorted(
        (url, hashlib.sha256(body).hexdigest()) for url, body in routes.items()
    )
    stored = b" ".join(body for _, body in saved).decode()
    for name in NAMES:
        assert name not in stored
    # Uložené je to, z čeho agent pracuje: tisky období bez jmen a poslanecké kluby.
    assert "Novela z. o spotřebních daních" in stored
    assert "skupina poslanců (ODS)" in stored
    assert "ODS" in stored and "Poslanecký klub" not in stored


def test_later_snapshot_turns_only_tisky_new_since_last_snapshot_into_findings(
    bus, board, clock, raw_dir, make_agent
):
    llm = FakeLlm(BASELINE_ANSWER, {"tisky": [assessment(TISK_321, level="vysoký")]})
    send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm).process_pending()
    clock.advance(days=1)
    zadani = send_zadani(bus)

    make_regulatorni(make_agent, clock, raw_dir, llm, source_routes(day=2)).process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert (reply.content["new_findings"], reply.content["item_count"]) == (1, 7)
    # Druhé volání LLM dostalo jen nový tisk 321, ne znovu kandidáty z výchozího snímku.
    second_call = llm.calls[1].prompt
    assert f"ID {TISK_321}," in second_call
    assert f"ID {TISK_243}," not in second_call
    navykove_latky = tisky_on_board(board)["321"]
    assert navykove_latky.snapshot_id == reply.content["snapshot_id"]
    assert navykove_latky.found_at == clock.now()
    assert navykove_latky.content["baseline"] is False
    assert navykove_latky.content["proposer"] == "vláda (min. zdrav., min. fin.)"
    assert navykove_latky.content["keywords"] == ["návykové látky"]


def test_zadani_without_new_tisky_writes_snapshot_and_does_not_call_llm(
    bus, board, clock, raw_dir, make_agent
):
    llm = FakeLlm(BASELINE_ANSWER)  # druhá odpověď chybí: další volání by test shodilo
    send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm).process_pending()
    clock.advance(days=1)
    zadani = send_zadani(bus)

    make_regulatorni(make_agent, clock, raw_dir, llm).process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert (reply.content["new_findings"], reply.content["item_count"]) == (0, 6)
    assert len(llm.calls) == 1
    assert len(board.findings(kiq=4)) == 4


def failure_reason(bus, board, zadani):
    """Důvod Selhání; tabule musí zůstat beze změny."""
    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.FAILURE
    assert board.snapshots(agent="regulatorni") == []
    assert board.findings(kiq=4) == []
    assert board.known_tisky() == set()
    return reply.content["reason"]


def test_source_failure_after_retries_is_failure_and_board_stays_untouched(
    bus, board, clock, raw_dir, make_agent
):
    routes = source_routes(day=1)
    routes[TISKY_URL] = FetchError(f"{TISKY_URL}: 3 pokusy selhaly, naposledy HTTP 429")
    regulatorni = make_regulatorni(make_agent, clock, raw_dir, FakeLlm(), routes)
    zadani = send_zadani(bus)

    regulatorni.process_pending()

    assert "HTTP 429" in failure_reason(bus, board, zadani)


def test_archive_without_tisky_table_is_failure(bus, board, clock, raw_dir, make_agent):
    routes = source_routes(day=1, drop={"tisky.unl"})
    regulatorni = make_regulatorni(make_agent, clock, raw_dir, FakeLlm(), routes)
    zadani = send_zadani(bus)

    regulatorni.process_pending()

    assert "chybí tisky.unl" in failure_reason(bus, board, zadani)


def test_tisk_missing_in_llm_answer_stays_new_for_next_zadani_and_the_rest_is_kept(
    bus, board, clock, raw_dir, make_agent
):
    incomplete = {"tisky": BASELINE_ANSWER["tisky"][:-1]}  # bez tisku 243
    llm = FakeLlm(incomplete, {"tisky": [assessment(TISK_243, relevant=False)]})
    zadani = send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm).process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert reply.content["new_findings"] == 4
    assert TISK_243 not in board.known_tisky()

    send_zadani(bus)
    make_regulatorni(make_agent, clock, raw_dir, llm).process_pending()

    second_call = llm.calls[1].prompt
    assert f"ID {TISK_243}," in second_call
    assert f"ID {TISK_133}," not in second_call
    assert TISK_243 in board.known_tisky()
    assert sorted(tisky_on_board(board)) == ["133", "153", "193", "208"]


class RefusesFirstBatch(FakeLlm):
    refused = False

    def ask(self, prompt, schema, *, effort, system=""):
        if not self.refused:
            self.refused = True
            raise LlmError("model odmítl odpovědět (refusal, kategorie None)")
        return super().ask(prompt, schema, effort=effort, system=system)


def test_llm_failure_on_one_batch_keeps_the_other_batches(
    bus, board, clock, raw_dir, make_agent, monkeypatch
):
    monkeypatch.setattr(regulatorni_module, "BATCH", 3)  # kandidáti 133, 153, 193 | 208, 243
    second_batch = {"tisky": [assessment(TISK_208), assessment(TISK_243, relevant=False)]}
    zadani = send_zadani(bus)

    make_regulatorni(make_agent, clock, raw_dir, RefusesFirstBatch(second_batch)).process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert reply.kind is Kind.INFORM
    assert sorted(tisky_on_board(board)) == ["208"]
    assert {TISK_133, TISK_153, TISK_193}.isdisjoint(board.known_tisky())


def test_llm_failure_on_every_batch_is_failure_and_board_stays_untouched(
    bus, board, clock, raw_dir, make_agent
):
    zadani = send_zadani(bus)

    make_regulatorni(make_agent, clock, raw_dir, RefusesFirstBatch()).process_pending()

    assert "refusal" in failure_reason(bus, board, zadani)
