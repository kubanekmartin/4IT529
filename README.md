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

## Nasazení

Web běží na <https://lihoviny.kubanekmartin.cz/log> (Oracle VM, hostitelský nginx s Let's Encrypt
a `X-Robots-Tag: noindex`; kontejnery jen na `127.0.0.1`).

- Po zeleném CI na `main` workflow **Nasazení** (`.github/workflows/deploy.yml`) pošle
  `git archive` commitu přes SSH na server. Skript `deploy/lihoviny-deploy` ho rozbalí do
  `/srv/lihoviny/releases/<sha>`, spustí `docker compose up --build` a ověří web. Server
  nepotřebuje přístup na GitHub.
- Klíč v secrets `DEPLOY_SSH_KEY` patří uživateli `lihoviny-deploy` a smí spustit jen tento skript
  (forced command, bez shellu a přesměrování portů). Uživatel `deploy` na serveru už patří
  osobnímu webu, proto vlastní uživatel.
- Ruční Zadání: workflow **Ruční Zadání** (příjemce, obsah jako JSON, kolik sekund čekat).
  Odpověď je ve výstupu workflow i v logu komunikace.
- Tajemství jsou jen v `/srv/lihoviny/.env` (práva 600). API klíč vloží Martin sám, klíč se
  čte skrytě z terminálu: `ssh -t ubuntu@130.61.26.171 sudo lihoviny-set-api-key`.
  Ověření jedním voláním LLM: `ssh ubuntu@130.61.26.171 sudo -u lihoviny-deploy lihoviny-deploy overit-llm`.
- Příprava serveru je v `deploy/setup-server.sh` (`web`, `app`; dá se spustit znovu). Změna
  `deploy/lihoviny-deploy` se na server dostane jen novým spuštěním `setup-server.sh app`.

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
| `src/lihoviny/cli.py` | příkaz `lihoviny` (migrate, agent, web, zadani, overit-llm) |
| `deploy/` | nasazení: skript pro SSH, nginx, příprava serveru |
