# Lihoviny MAS

Studijní prototyp multiagentního systému pro konkurenční zpravodajství na trhu lihovin
(4IT529). Pojmy jsou v `GLOSSARY.md`, architektura v `docs/adr/`, podklad pro report
v `KIT-KIQ.md`.

Agenti běží jako samostatné služby se společným Docker image a mluví spolu jen zprávami
přes Postgres (ADR 0001). Nálezy zapisují na sdílenou tabuli ve stejném Postgresu. Web jen
čte (role bez práva zápisu) a ukazuje:

| Stránka | Co |
| --- | --- |
| `/prehled` | Přehled (na něj vede `/`): hlavní čísla s vývojem za 30 dní, grafy po KIQ s vysvětlením (bez grafů, které má Brief) a stav sběru |
| `/brief` | aktuální Brief, `/brief/archiv` starší Briefy |
| `/sortiment` | KIQ 1: Novinky a produkty z rohlik.cz (Novinky i z alkohol.cz) podle devíti kategorií lihovin, u produktu jeho akční ceny; Novinky sledovaných konkurentů v čase |
| `/akcni-ceny` | KIQ 2 (akcniceny.cz a kupi.cz bez Tesca): cenový tlak na produkty zadavatele („Co to znamená pro vás“), statistiky a vývoj akčních cen (slevy, řetězce, kalendář akcí) a tabulka akčních cen z akcniceny.cz s kategorií a cenou za litr, filtrem a řazením podle sloupců |
| `/akcni-ceny/nesparovane` | akční ceny, ke kterým párování zatím nenašlo produkt ze sortimentu |
| `/komunikace` | KIQ 3: články o lihovinách ze zboziaprodej.cz, mediar.cz a mediaguru.cz s výrobcem, značkou, kanálem, tématem a shrnutím; témata po týdnech |
| `/regulace` | KIQ 4: nové sněmovní tisky o alkoholu s odhadem dopadu a termínem účinnosti, řazené podle data předložení nebo dopadu; tisky podle fáze projednávání |
| `/log` | log komunikace agentů (tabulka zpráv sběrnice) po konverzacích |
| `/behy` | běhy řídicího agenta, kalendář spolehlivosti sběru po dnech a varování při Selhání nebo prázdném snímku |
| `/architektura` | agenti, jejich role a zdroje, diagram komunikace, druhy zpráv a sdílená tabule |

## Spuštění celého systému

```sh
cp .env.example .env                          # doplň hesla, např. openssl rand -hex 24
docker compose up --build                     # Postgres, migrace, web, agenti
```

- Web: <http://127.0.0.1:8510/> (port mění `WEB_HOST_PORT`, jen na `127.0.0.1`)
- Postgres nemá port na hostiteli: `docker compose exec db psql -U lihoviny`

Zadání diagnostickému agentovi Ozvěna:

```sh
docker compose exec ozvena lihoviny zadani ozvena '{"text": "ahoj"}'
docker compose exec ozvena lihoviny zadani ozvena '{"fail": "ukázka Selhání"}'
```

Příkaz vypíše Zadání i odpověď (INFORM, nebo FAILURE s důvodem) a obě zprávy se objeví v logu.

## Sběrní agenti

Systém sleduje jen lihoviny (tvrdý alkohol s aspoň 15 % obj., včetně likérů). Pivo, víno,
nealko nápoje a hotové míchané nápoje (RTD) jsou mimo rozsah KIQ: sortimentní agent stránku
RTD nestahuje a produkt pod 15 % vynechá, cenový agent nečte výpis míchaných nápojů a vynechá
akční cenu s obsahem alkoholu pod 15 % v názvu. RTD sebrané do 17. 11. 2026 zůstávají
v databázi, web je v Sortimentu neukazuje.

Sběrný agent na Zadání stáhne zdroj, zapíše na tabuli jeden snímek (odkazy na surové stránky
uložené na disku) a k němu nálezy, vše v jedné transakci. Odpoví INFORM jen s přehledem
`{"snapshot_id", "new_findings", "item_count"}`; samotné nálezy jsou na tabuli. Chyba zdroje
(po třech pokusech) nebo stránka, ze které nic nevyčte, je Selhání a tabule zůstane beze změny.

### Sortimentní agent (KIQ 1, rohlik.cz a alkohol.cz)

Kromě Rohlíku čte výpis Novinky specializovaného e-shopu alkohol.cz (`/produkty/novinky/`,
asi 37 nejnovějších produktů; název nese objem i obsah alkoholu). Lihovina z něj, která ještě
není mezi produkty, je Novinka s poznámkou „podle prodejce alkohol.cz“, i po výchozím snímku
(ID alkohol.cz s Rohlíkem nesouvisí, hranice ID pro ni neplatí). Kategorii, značku a výrobce
určí LLM jako u nejasných produktů Rohlíku. Víno, pivo a doplňky vypadnou podle obsahu
alkoholu nebo objemu. Výpadek alkohol.cz Zadání neshodí.

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

### Cenový agent (KIQ 2, akcniceny.cz a kupi.cz)

Kromě akcniceny.cz čte i kupi.cz: kategorie lihovin (rum, vodka, whisky, gin a tequila, brandy,
pálenky, likéry) se všemi stranami výpisu, u každého produktu nabídky jednotlivých řetězců.
Tesco se z Kupi nebere (rozhodnutí týmu). Kupi u nabídky obvykle neuvádí obsah alkoholu,
slabé nápoje z kategorie likérů a míchaných nápojů (Aperol, spritz, plechovky…) agent pozná
podle názvu. Cena s věrnostní kartou je u Kupi jediná cena s podmínkou (`card_label`). Rok
platnosti Kupi neuvádí: datum víc než měsíc v minulosti je příští rok. Nabídka, kterou má
ve stejném Zadání i akcniceny.cz (stejný řetězec, cena a konec platnosti), se nezapíše
podruhé. Výpadek Kupi Zadání neshodí. Nález z Kupi má `source: kupi.cz`.

Projde výpisy nápojů alkoholických podle klíčových slov (rum, vodka, gin, likéry, bylinné
a ovocné destiláty…) i s dalšími stranami, s pauzou 1 s podle `Crawl-delay`. Každá nabídka řetězce je jedna
akční cena: cena, případně cena s věrnostní kartou, sleva, platnost a odkaz na leták. Objem
a obsah alkoholu čte z názvu produktu stejně jako sortimentní agent; z nich počítá cenu za
litr a za litr etanolu (bez známého obsahu alkoholu zůstane prázdná). LLM potřebuje jen jako
zálohu při párování (viz níže); bez `ANTHROPIC_API_KEY` akční ceny sbírá dál, jen jich víc
zůstane nespárovaných:

```sh
docker compose exec cenovy lihoviny zadani cenovy --cekat 180
```

Nález vznikne jen u akční ceny, která na tabuli ještě není (stejný produkt, řetězec, ceny
a konec platnosti); každý snímek si pamatuje, které akční ceny obsahoval. Stránka
<http://127.0.0.1:8510/akcni-ceny> ukazuje akční ceny platné k poslednímu snímku: s datem
konce do jeho data, bez data konce, dokud je poslední snímek obsahuje. Odkaz na produkt se
bere jen přes https z akcniceny.cz, jinak vede na stranu výpisu.

Párování akčních cen na produkty ze sortimentu (`src/lihoviny/pairing.py`) jde podle názvu
u zdroje, takže všechny akční ceny se stejným názvem patří k jednomu produktu:

- Normalizovaný klíč jako u sortimentního agenta (značka a název, objem, obsah alkoholu),
  z názvu se jen vynechá objem. Když název obsah alkoholu neuvádí, stačí shoda názvu
  a objemu s jediným produktem. Dárkové balení stejného objemu je týž produkt.
- Zbytek posoudí LLM, ale jen u akčních cen s kandidáty: produkt téže značky se stejným
  objemem a obsahem alkoholu (pokud je název uvádí). Akční cena na „vybrané druhy“ není
  jeden produkt a zůstane nespárovaná. Odmítnuté dvojice si agent pamatuje (tabulka
  `rejected_pairings`) a stejnou otázku LLM nepoloží znovu.
- Při každém Zadání se párují nové i dřív nespárované akční ceny, takže akční cena se
  spáruje i dodatečně, jakmile sortimentní agent produkt zapíše (obvykle o den později:
  oba agenti běží v 6:00 současně). Rohlík ve výpisu ukazuje jen asi 14 produktů na
  podkategorii, nespárovaných je proto zatím většina.
- Spárovaná akční cena převezme z produktu obsah alkoholu (a objem, když ho název neuvádí)
  a dopočítá cenu za litr etanolu. Web ukáže kategorii u akční ceny, akční ceny u produktu
  na `/sortiment` a nespárované zvlášť na `/akcni-ceny/nesparovane`.

### Mediální agent (KIQ 3, zboziaprodej.cz, mediar.cz, mediaguru.cz)

Čte tři zdroje: oborový magazín zboziaprodej.cz (hlavní), mediar.cz (WordPress REST API,
posledních 50 článků, asi dva týdny) a mediaguru.cz (RSS, jen 15 nejnovějších, asi dva dny).
Mediář a MediaGuru píšou o celém mediálním trhu, proto LLM dostane jen jejich články, ve
kterých se objeví lihoviny (alkohol, lihovina, likér, rum, vodka, gin, značky a výrobci…);
ostatní si agent zapíše jako viděné. Výpadek Mediáře nebo MediaGuru Zadání neshodí, jen se
zaloguje a snímek vznikne bez něj; výpadek zboziaprodej.cz je Selhání. Nález nese `source`.
U MediaGuru nemá článek číselné ID: agent si pamatuje otisk odkazu a když adresa obsahuje
jméno osoby, nález odkáže jen na web zdroje.

Ze zboziaprodej.cz jedním požadavkem na WordPress REST API načte posledních 30 článků i s textem (RSS by neslo
jen 10). Článek, který ještě nebyl v žádném snímku, posoudí LLM: jestli je o lihovinách,
a u relevantního výrobce, značky, kanál komunikace, téma a shrnutí ve dvou větách. Nález
vznikne jen z relevantního článku; viděné články si agent pamatuje (jen jejich ID), takže
LLM žádný článek neposuzuje dvakrát. První snímek tak udělá nálezy ze všech asi 30 článků
výpisu (zhruba dvou týdnů), další už jen z nových. Posouzený článek se zapíše hned; když LLM u některého
článku selže, ostatní zůstanou zapsané a příští Zadání posoudí jen ty zbylé (Selhání je
odpovědí, jen když LLM selže u všech). Potřebuje `ANTHROPIC_API_KEY`:

```sh
docker compose exec medialni lihoviny zadani medialni --cekat 600
```

Jména osob se neukládají: nález odkazuje na článek krátkým odkazem `?p=<ID>` (adresa se
slugem opakuje titulek i se jmény), titulek a shrnutí píše LLM bez jmen a agent v nich jména,
která LLM v článku našel, ještě nahradí slovem „[osoba]“. Články ukáže stránka
<http://127.0.0.1:8510/komunikace>.

### Regulatorní agent (KIQ 4, otevřená data Sněmovny)

Kromě Sněmovny čte výpis tiskových zpráv Ministerstva financí za rok
(`mf.gov.cz/cs/ministerstvo/media/tiskove-zpravy/<rok>`) a aktuality Celní správy. Nová zpráva
projde stejným filtrem klíčových slov v titulku jako tisky, kandidáty posoudí LLM (jen
z titulku) a relevantní jsou nálezy `uredni_zprava` (migrace 0014 jen přidá typ). Viděné
zprávy si agent pamatuje v tabulce `articles`. Výpadek úřadu Zadání neshodí. Stránka Regulace
je ukazuje v sekci Zprávy úřadů.

Stáhne dva archivy [otevřených dat Poslanecké sněmovny](https://www.psp.cz/sqw/hp.sqw?k=1300):
sněmovní tisky (`tisky.zip`, asi 4 MB) a poslance s orgány (`poslanci.zip`, kvůli poslaneckým
klubům). Zdroj je obnovuje jednou denně v noci a vždy s úplným stavem, takže agent stáhne
každý jednou za Zadání. Bere tisky probíhajícího volebního období jen těch druhů, které
navrhují pravidla (návrhy zákonů, rozpočet, mezinárodní smlouvy, akty EU); interpelace, žádosti
o vydání poslance ke stíhání nebo návrhy na volbu regulací nejsou a jména nesou už v názvu.
Nové tisky (které agent v dřívějším snímku neviděl) projdou filtrem klíčových slov v názvu
(líh, alkohol, spotřební daň, reklama, prodej, značení, obaly, návykové látky, DPH…) a jen ty
posoudí LLM: relevance pro výrobce lihovin, dopad a odhad termínu účinnosti. Posudky se
zapisují po dávkách; tisk, který LLM neposoudil, zůstane nový pro příští Zadání. Potřebuje
`ANTHROPIC_API_KEY`:

```sh
docker compose exec regulatorni lihoviny zadani regulatorni --cekat 300
```

Výchozí (první) snímek bere jako nové všechny tisky období, takže stránka
<http://127.0.0.1:8510/regulace> hned ukáže projednávané tisky, které se lihovin týkají
(označené „výchozí snímek“). Jména osob systém neukládá: navrhovatel je „vláda (min. financí)“,
„skupina poslanců (ODS)“ (klub v den předložení) nebo „Senát“, jména z úplného názvu agent
vypustí a tabulku osob nečte. Na disk místo archivů jde jen to, co agent z archivů přečetl
(tisky bez jmen, kluby, ID osob), a otisk SHA-256 původního archivu. Podmínky otevřených dat
chtějí uvést zdroj a datum zpracování; stránka je uvádí pod seznamem.

## Řídicí agent a běhy

Řídicí agent (`ridici`) každý den v 6:00 pražského času pošle Zadání sběrným agentům
z `Ridici.sberni_agenti` (`src/lihoviny/agents/ridici.py`) a v pondělí v 7:00 Zadání Briefu
analytickému agentovi z `Ridici.analyticky_agent` (`analyticky`). Za agenta, který do 30 minut
neodpoví, zapíše Selhání. Když v 6:00 neběžel, zmeškaný běh dožene ještě týž den. Brief
zadá, až je poslední sběr uzavřený (všichni odpověděli nebo jim vypršel termín), takže ani
dohnané pondělí nepíše Brief bez dat ze sběru.

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

### Smyčka Dotazů

Když sběr některé KIQ v posledním běhu selhal nebo vrátil prázdný snímek, pošle analytický
agent před psaním Briefu řídicímu agentovi Dotaz (`QUERY`): KIQ, co chybí a návrh doplňujícího
sběru. Řídicí agent z něj udělá Zadání z Dotazu pro sběrného agenta té KIQ (text Zadání převede
LLM s effortem `low`, model `LLM_MODEL_RIDICI`) a jeho výsledek pošle zpět jako odpověď na Dotaz.
Po poslední odpovědi analytický agent Brief napíše; mezeru, kterou doplnění nezaplnilo, uvede
i s důvodem. Sběrní agenti text instrukce ze Zadání z Dotazu záměrně nečtou a zopakují celý
svůj sběr: mezeru vždy způsobil chybějící nebo prázdný snímek a nový snímek ji zaplní celou.
Instrukce zůstává v logu komunikace jako doklad, o co analytický agent žádal.

- Nejvýš 3 Dotazy na běh (jeden za KIQ, podle čísla KIQ) a jedno kolo: po první odpovědi
  řídicí agent další Dotaz odmítne Selháním. Sběr, který ještě neodpověděl, se nezadává znovu.
- Časový rozpočet (`src/lihoviny/planning.py`): 30 minut na Zadání Briefu platí i s Dotazy.
  Převod Dotazu na Zadání trvá nejvýš 2 minuty (60 s, jedno opakování). Zadání z Dotazu musí
  odpovědět do 15 minut od Zadání Briefu (jinak Selhání „bez odpovědi do 15 minut od Zadání
  Briefu“), takže na napsání Briefu zbude 15 minut a jedno volání LLM trvá nejvýš 12. Termíny
  řídicí agent kontroluje i mezi Dotazy a Dotaz, na který už čas nezbývá, odmítne.
- Sběrný agent obsluhuje zprávy jednu po druhé: když ještě pracuje na dřívějším Zadání (třeba
  na sběru, kterému už vypršel termín), Zadání z Dotazu čeká ve frontě a po 15 minutách
  skončí Selháním. Brief pak mezeru uvede; nic se neblokuje.
- Celá výměna má ID konverzace Zadání Briefu, takže ji <http://127.0.0.1:8510/log> ukáže
  pohromadě; Zadání z Dotazu jsou na stránce Běhy v běhu Briefu s označením „z Dotazu“.
- Ruční Zadání poslané analytickému agentovi přímo (ne přes `ridici`) vyjde bez Dotazů.

## Analytický agent a Brief

Na Zadání od řídicího agenta (pondělí 7:00 nebo ručně) analytický agent (`analyticky`) napíše
ze sdílené tabule Brief za posledních 7 dní: Co se stalo podle KIQ, Netypické nálezy
a Doporučení k portfoliu zadavatele, se čtyřmi grafy. Stránka <http://127.0.0.1:8510/brief>
ukazuje poslední Brief, <http://127.0.0.1:8510/brief/archiv> všechny starší.

- Do období patří nálezy zjištěné za 7 dní a starší akční ceny, které snímek z týdne viděl
  znovu (platí dál). Výrobce nálezu bere ze spárovaného produktu, z obsahu nálezu, jinak podle
  značky na začátku názvu akční ceny (značky a výrobce zná z produktů sortimentního agenta).
- Text píše LLM s effortem `high` (sběrní agenti `low`), jedno volání nejvýš 4 minuty na
  pokus, celkem 12 minut, tedy uvnitř 15 minut, které po kole Dotazů zbudou z 30 minut
  řídicího agenta. Model mění `LLM_MODEL_ANALYTICKY`.
- Každé tvrzení odkazuje na zdroj nálezu. Tvrzení, které necituje žádný nález z promptu
  pro LLM, agent vypustí; doporučení k produktu mimo portfolio také. Tvrzení jen o výrobcích mimo
  sledované konkurenty přesune do Netypických nálezů, o sledovaných konkurentech dá nahoru.
- KIQ bez nálezů Brief uvede jako „bez dat“. Selhání posledního sběru (z výsledků, které
  řídicí agent přikládá k Zadání) zkusí doplnit Dotazem (viz Smyčka Dotazů); co se doplnit
  nepodařilo, uvede v rámečku nahoře i u dotčené KIQ.
- Grafy (akční ceny podle řetězce, akční ceny sledovaných konkurentů podle dne, cena za litr
  portfolia proti nejnižší akční ceně v kategorii, Novinky podle kategorie) počítá
  agent sám z nálezů a ukládá je s Briefem jako SVG; web je vloží bez JavaScriptu. Pod každým
  grafem je tabulka s daty.
- Portfolio zadavatele (fiktivní značka Valnov, jen ke srovnání; odsouhlaseno týmem) a sledovaní konkurenti jsou
  v `src/lihoviny/zadavatel.toml`; sortimentní agent odtud bere jména konkurentů.

```sh
docker compose exec ridici lihoviny zadani ridici '{"beh": "brief"}'
```

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
| `src/lihoviny/board.py` | sdílená tabule: snímky, nálezy, produkty, viděné sněmovní tisky, běhy řídicího agenta se Zadáními a Briefy |
| `src/lihoviny/db.py` | připojení k Postgresu a transakce přes sběrnici i tabuli |
| `src/lihoviny/products.py` | kategorie, normalizovaný klíč produktu, objem a obsah alkoholu z názvu |
| `src/lihoviny/zadavatel.toml` | portfolio zadavatele a sledovaní konkurenti (načítá `zadavatel.py`) |
| `src/lihoviny/charts.py` | grafy Briefu jako SVG |
| `src/lihoviny/clock.py` | hodiny (skutečné a pro testy), pražský čas |
| `src/lihoviny/fetch.py` | adaptér stahování a jeho náhražka (na disk ukládá surová data, případně zmenšená bez jmen) |
| `src/lihoviny/llm.py` | adaptér LLM (Claude API) a jeho náhražka |
| `src/lihoviny/migrations/` | číslované migrace `NNNN_popis.sql` |
| `src/lihoviny/web/` | web (FastAPI, šablony Jinja2) |
| `src/lihoviny/cli.py` | příkaz `lihoviny` (migrate, agent, web, zadani, overit-llm) |
| `deploy/` | nasazení: skript pro SSH, nginx, příprava serveru |
