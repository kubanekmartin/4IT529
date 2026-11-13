# Lihoviny MAS

Studijní prototyp multiagentního systému pro konkurenční zpravodajství na trhu lihovin
(4IT529). Pojmy jsou v `GLOSSARY.md`, architektura v `docs/adr/`, podklad pro report
v `KIT-KIQ.md`.

Agenti běží jako samostatné služby se společným Docker image a mluví spolu jen zprávami
přes Postgres (ADR 0001). Nálezy zapisují na sdílenou tabuli ve stejném Postgresu. Web jen
čte (role bez práva zápisu) a ukazuje:

| Stránka | Co |
| --- | --- |
| `/sortiment` | KIQ 1: Novinky a produkty z rohlik.cz podle deseti kategorií |
| `/akcni-ceny` | KIQ 2: akční ceny lihovin a RTD z akcniceny.cz s cenou za litr a za litr etanolu |
| `/log` | log komunikace agentů (tabulka zpráv sběrnice) |
| `/behy` | běhy řídicího agenta a varování při Selhání nebo prázdném snímku |
| `/architektura` | agenti, jejich role a zdroje, diagram komunikace, druhy zpráv a sdílená tabule |

## Spuštění celého systému

```sh
cp .env.example .env                          # doplň hesla, např. openssl rand -hex 24
docker compose up --build                     # Postgres, migrace, web, agenti
```

- Web: <http://127.0.0.1:8510/log> (port mění `WEB_HOST_PORT`, jen na `127.0.0.1`)
- Postgres nemá port na hostiteli: `docker compose exec db psql -U lihoviny`

Zadání diagnostickému agentovi Ozvěna:

```sh
docker compose exec ozvena lihoviny zadani ozvena '{"text": "ahoj"}'
docker compose exec ozvena lihoviny zadani ozvena '{"fail": "ukázka Selhání"}'
```

Příkaz vypíše Zadání i odpověď (INFORM, nebo FAILURE s důvodem) a obě zprávy se objeví v logu.

## Sběrní agenti

Sběrný agent na Zadání stáhne zdroj, zapíše na tabuli jeden snímek (odkazy na surové stránky
uložené na disku) a k němu nálezy, vše v jedné transakci. Odpoví INFORM jen s přehledem
`{"snapshot_id", "new_findings", "item_count"}`; samotné nálezy jsou na tabuli. Chyba zdroje
(po třech pokusech) nebo stránka, ze které nic nevyčte, je Selhání a tabule zůstane beze změny.

### Sortimentní agent (KIQ 1, rohlik.cz)

Stáhne kategorii Lihoviny a její podkategorie (asi 13 stránek po 5 s) a data čte
z `__NEXT_DATA__` stránky. Kategorii bere z podkategorie u zdroje; LLM rozhoduje jen nejasné
případy a určí výrobce nové značky, takže agent potřebuje `ANTHROPIC_API_KEY` v `.env`:

```sh
docker compose exec sortimentni lihoviny zadani sortimentni --cekat 600
```

Produkty a Novinky pak ukáže stránka <http://127.0.0.1:8510/sortiment>.

Jak agent pozná Novinku (pojem v `GLOSSARY.md`: produkt, který se u zdroje objevil poprvé od
začátku sledování):

- Výchozí (první) snímek hlásí jen produkty se štítkem prodejce „Novinka“, s poznámkou
  „podle prodejce“. Ostatní produkty jsou výchozí stav.
- V dalších snímcích je Novinkou produkt, který ještě není v tabulce produktů **a** má ID
  u Rohlíku vyšší než nejvyšší ID mezi produkty výchozího snímku.
- Proč ta hranice ID: stránka podkategorie nese jen prvních asi 14 produktů (zbytek Rohlík
  dočítá až v prohlížeči), takže se do výpisu může kdykoli dostat produkt, který u zdroje
  je dávno. Rohlík přiděluje ID postupně, nízké ID tedy prozradí produkt, který byl u zdroje
  už před začátkem sledování; Novinkou není, ani se štítkem prodejce.
- Vyprodaný a vrácený produkt už v tabulce je, takže Novinkou není. Dárkové balení stejného
  objemu má stejný klíč (`lihoviny.products.product_key`), je to týž produkt.

### Cenový agent (KIQ 2, akcniceny.cz)

Projde výpisy nápojů alkoholických podle klíčových slov (rum, vodka, gin, likéry, míchaný
nápoj…) i s dalšími stranami, s pauzou 1 s podle `Crawl-delay`. Každá nabídka řetězce je jedna
akční cena: cena, případně cena s věrnostní kartou, sleva, platnost a odkaz na leták. Objem
a obsah alkoholu čte z názvu produktu stejně jako sortimentní agent; z nich počítá cenu za
litr a za litr etanolu (bez známého obsahu alkoholu zůstane prázdná). LLM nepotřebuje:

```sh
docker compose exec cenovy lihoviny zadani cenovy --cekat 180
```

Nález vznikne jen u akční ceny, která na tabuli ještě není (stejný produkt, řetězec, ceny
a konec platnosti); každý snímek si pamatuje, které akční ceny obsahoval. Stránka
<http://127.0.0.1:8510/akcni-ceny> ukazuje akční ceny platné k poslednímu snímku: s datem
konce do jeho data, bez data konce, dokud je poslední snímek obsahuje. Odkaz na produkt se
bere jen přes https z akcniceny.cz, jinak vede na stranu výpisu.

## Řídicí agent a běhy

Řídicí agent (`ridici`) každý den v 6:00 pražského času pošle Zadání sběrným agentům
z `Ridici.sberni_agenti` (`src/lihoviny/agents/ridici.py`) a v pondělí v 7:00 Zadání Briefu
analytickému agentovi, jakmile bude zapsaný v `Ridici.analyst`. Za agenta, který do 30 minut
neodpoví, zapíše Selhání. Když v 6:00 neběžel, zmeškaný běh dožene ještě týž den.

Stránka <http://127.0.0.1:8510/behy> ukazuje poslední běhy a varování, když agent v posledním
běhu selhal nebo vrátil prázdný snímek (0 položek). Počet nových nálezů je jen informace:
den bez Novinky nebo nové akční ceny je běžný, rozbitý zdroj poznáš podle prázdného snímku
nebo Selhání. Ruční běh:

```sh
docker compose exec ridici lihoviny zadani ridici                      # denní sběr
docker compose exec ridici lihoviny zadani ridici '{"beh": "brief"}'   # Brief
```

Z GitHub Actions jde totéž přes workflow pro ruční Zadání (ticket Nasazení): příjemce `ridici`,
obsah `{}` nebo `{"beh": "brief"}`. Odpověď řídicího agenta jen potvrdí zahájení běhu
(číslo běhu a komu šlo Zadání); výsledky sběrných agentů jsou v logu a na stránce Běhy.

## Nasazení

Web běží na <https://lihoviny.kubanekmartin.cz/log> (Oracle VM, hostitelský nginx s Let's Encrypt
a `X-Robots-Tag: noindex`; kontejnery jen na `127.0.0.1`).

- Po zeleném CI na `main` workflow **Nasazení** (`.github/workflows/deploy.yml`) pošle
  `git archive` commitu přes SSH na server. Skript `deploy/lihoviny-deploy` ho rozbalí do
  `/srv/lihoviny/releases/<sha>` (nejvýš 100 MB komprimovaný a 256 MB rozbalený, aby gzip
  bomba nezaplnila disk sdílený s ostatními weby), spustí `docker compose up --build` a ověří
  web. Když nová verze nenaběhne, skript znovu spustí předchozí release; migrace se
  nevracejí, schéma zůstane novější. Server nepotřebuje přístup na GitHub.
- Klíč `DEPLOY_SSH_KEY` patří uživateli `lihoviny-deploy` a smí spustit jen tento skript
  (forced command, bez shellu a přesměrování portů). Uživatel `deploy` na serveru už patří
  osobnímu webu, proto vlastní uživatel.
- `lihoviny-deploy` je ve skupině `docker` (bez ní `docker compose` nejde) a skript sestaví
  a spustí, co přijde v archivu. **Kdo má klíč, je tedy na VM root.** Proto patří
  `DEPLOY_SSH_KEY` a `DEPLOY_KNOWN_HOSTS` do GitHub prostředí `produkce` omezeného na větev
  `main` (oba workflow mají `environment: produkce`), ne do secrets celého repa, ke kterým se
  dostane workflow z jakékoli větve.
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

CI (`.github/workflows/ci.yml`) sestaví Docker image jen pro amd64 (arm64 by na runneru
šel jen přes pomalou emulaci). Server je arm64 (Oracle VM, aarch64) a image si při nasazení
sestavuje sám; před změnou `Dockerfile` nebo závislostí to ověř lokálně:

```sh
docker buildx build --platform linux/arm64 .
```

## Struktura

| Kde | Co |
| --- | --- |
| `src/lihoviny/bus.py` | sběrnice zpráv (REQUEST = Zadání, INFORM, QUERY = Dotaz, FAILURE = Selhání) |
| `src/lihoviny/agent.py` | společný základ agenta, obsah odpovědi sběrného agenta |
| `src/lihoviny/agents/` | jednotliví agenti a jejich registr |
| `src/lihoviny/board.py` | sdílená tabule: snímky, nálezy, produkty a běhy řídicího agenta se Zadáními |
| `src/lihoviny/db.py` | připojení k Postgresu a transakce přes sběrnici i tabuli |
| `src/lihoviny/products.py` | kategorie, normalizovaný klíč produktu, objem a obsah alkoholu z názvu |
| `src/lihoviny/clock.py` | hodiny (skutečné a pro testy), pražský čas |
| `src/lihoviny/fetch.py` | adaptér stahování a jeho náhražka |
| `src/lihoviny/llm.py` | adaptér LLM (Claude API) a jeho náhražka |
| `src/lihoviny/migrations/` | číslované migrace `NNNN_popis.sql` |
| `src/lihoviny/web/` | web (FastAPI, šablony Jinja2) |
| `src/lihoviny/cli.py` | příkaz `lihoviny` (migrate, agent, web, zadani, overit-llm) |
| `deploy/` | nasazení: skript pro SSH, nginx, příprava serveru |
