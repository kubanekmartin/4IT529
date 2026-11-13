# Lihoviny MAS

Studijní prototyp multiagentního systému pro konkurenční zpravodajství na trhu lihovin
(4IT529). Pojmy jsou v `GLOSSARY.md`, architektura v `docs/adr/`, podklad pro report
v `KIT-KIQ.md`.

Agenti běží jako samostatné služby se společným Docker image a mluví spolu jen zprávami
přes Postgres (ADR 0001). Web ukazuje log jejich komunikace.

## Spuštění celého systému

```sh
cp .env.example .env                          # doplň hesla, např. openssl rand -hex 24
docker compose up --build                     # Postgres, migrace, web, agenti
```

- Web: <http://127.0.0.1:8510/log> (port mění `WEB_HOST_PORT`)
- Postgres: `127.0.0.1:55431` (port mění `POSTGRES_HOST_PORT`)

Porty jsou jen na `127.0.0.1`. Zadání diagnostickému agentovi Ozvěna:

```sh
docker compose exec ozvena lihoviny zadani ozvena '{"text": "ahoj"}'
docker compose exec ozvena lihoviny zadani ozvena '{"fail": "ukázka Selhání"}'
```

Příkaz vypíše Zadání i odpověď (INFORM, nebo FAILURE s důvodem) a obě zprávy se objeví v logu.

## Řídicí agent a běhy

Řídicí agent (`ridici`) každý den v 6:00 pražského času pošle Zadání sběrným agentům
z `Ridici.collectors` (`src/lihoviny/agents/ridici.py`) a v pondělí v 7:00 Zadání Briefu
analytickému agentovi, jakmile bude zapsaný v `Ridici.analyst`. Za agenta, který do 30 minut
neodpoví, zapíše Selhání. Když v 6:00 neběžel, zmeškaný běh dožene ještě týž den.

Stránka <http://127.0.0.1:8510/behy> ukazuje poslední běhy a varování, když agent v posledním
běhu selhal nebo vrátil 0 nálezů. Ruční běh:

```sh
docker compose exec ridici lihoviny zadani ridici                      # denní sběr
docker compose exec ridici lihoviny zadani ridici '{"run": "brief"}'   # Brief
```

Z GitHub Actions jde totéž přes workflow pro ruční Zadání (ticket Nasazení): příjemce `ridici`,
obsah `{}` nebo `{"run": "brief"}`. Odpověď řídicího agenta jen potvrdí zahájení běhu
(číslo běhu a komu šlo Zadání); výsledky sběrných agentů jsou v logu a na stránce Běhy.

## Vývoj a testy

Potřebuješ [uv](https://docs.astral.sh/uv/) a Docker.

```sh
uv sync                                         # závislosti včetně vývojových
docker compose -f compose.test.yaml up -d       # testovací Postgres na 127.0.0.1:55432
uv run pytest
uv run ruff check && uv run ruff format --check
```

Testovací Postgres je jeden pro všechny worktrees (pevný název projektu `lihoviny-test`,
data jen v paměti). Každý test si v něm založí databázi s náhodným jménem, aplikuje migrace
a po sobě ji smaže, takže testy z více worktrees můžou běžet zároveň. Jiný server nastavíš
přes `TEST_DATABASE_URL` (superuživatel, který smí zakládat databáze a role).

Testy nevolají živé weby ani placené API: stahování nahrazuje `FakeFetcher` s ukázkami
stránek z `tests/fixtures/`, LLM nahrazuje `FakeLlm` s připravenými odpověďmi a čas
`FakeClock`.

## Struktura

| Kde | Co |
| --- | --- |
| `src/lihoviny/bus.py` | sběrnice zpráv (REQUEST = Zadání, INFORM, QUERY = Dotaz, FAILURE = Selhání) |
| `src/lihoviny/agent.py` | společný základ agenta |
| `src/lihoviny/agents/` | jednotliví agenti a jejich registr |
| `src/lihoviny/board.py` | sdílená tabule: snímky a nálezy |
| `src/lihoviny/fetch.py` | adaptér stahování a jeho náhražka |
| `src/lihoviny/llm.py` | adaptér LLM (Claude API) a jeho náhražka |
| `src/lihoviny/migrations/` | číslované migrace `NNNN_popis.sql` |
| `src/lihoviny/web/` | web (FastAPI, šablony Jinja2) |
| `src/lihoviny/cli.py` | příkaz `lihoviny` (migrate, agent, web, zadani) |
