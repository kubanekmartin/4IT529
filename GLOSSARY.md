# CI pro výrobce lihovin (MAS)

Multiagentní systém, který pro zadavatele průběžně sbírá a vyhodnocuje informace o konkurenci a trhu lihovin v ČR.

## Language

### Zadavatel a otázky

**Zadavatel**:
Fiktivní středně velký český výrobce lihovin, pro jehož obchodního a marketingového ředitele systém pracuje.
_Avoid_: klient, zákazník, firma

**Portfolio zadavatele**:
Fiktivní sada produktů zadavatele (značka Valnov) s jejich běžnými cenami, ke které Brief vztahuje doporučení. Slouží jen ke srovnání se skutečnými akčními cenami a Novinkami konkurentů.
_Avoid_: naše produkty, sortiment zadavatele

**Výrobce**:
Firma, pod jejíž značkami se lihovina prodává; zadavatel je také výrobce.
_Avoid_: značka, dodavatel

**Sledovaný konkurent**:
Výrobce ze seznamu, kterému Brief dává přednost před ostatními (Stock, Rudolf Jelínek, Fruko-Schulz, Jan Becher).
_Avoid_: hlavní konkurent, watchlist

**KIT**:
Klíčové zpravodajské téma: produktové, cenové a komunikační aktivity konkurence a změny trhu lihovin v ČR jako podklad pro plán promo akcí a portfolia zadavatele na 12 měsíců.
_Avoid_: zadání, cíl

**KIQ**:
Jedna ze čtyř klíčových otázek, na které systém odpovídá: 1 Sortiment, 2 Akční ceny, 3 Komunikace, 4 Regulace.
_Avoid_: oblast, téma

### Trh

**Produkt**:
Konkrétní balení lihoviny: značka, název, objem a obsah alkoholu; dárkové balení stejného objemu je týž produkt.
_Avoid_: SKU, položka, výrobek

**Značka**:
Obchodní jméno, pod kterým výrobce prodává více produktů (například Božkov).
_Avoid_: brand

**Kategorie**:
Jedna z devíti skupin, do kterých systém řadí každý produkt: rum a tuzemák, vodka, hořké likéry, ostatní likéry, gin, whisky, brandy a vinné destiláty, ovocné destiláty, ostatní.
_Avoid_: druh, typ, segment

**Lihovina**:
Tvrdý alkohol s aspoň 15 % obj. (nařízení (EU) 2019/787), včetně likérů. Jen lihoviny systém sleduje; pivo, víno, nealko nápoje a RTD jsou mimo rozsah KIQ.
_Avoid_: alkohol, nápoj

**RTD**:
Hotový míchaný nápoj s lihovinou (ready to drink), obvykle v plechovce a s nízkým obsahem alkoholu. Mimo rozsah systému (od 17. 11. 2026).
_Avoid_: koktejl, míchaný nápoj, plechovka

**Novinka**:
Produkt, který se u zdroje objevil poprvé od začátku sledování; výchozí snímek novinky neobsahuje, kromě produktů, které jako novinku označil sám prodejce.
_Avoid_: nový produkt, launch

### Agenti

**Sběrný agent**:
Agent, který pro jednu KIQ sbírá a čistí data z vnějšího zdroje: Sortimentní, Cenový, Mediální a Regulatorní agent.
_Avoid_: scraper, crawler, collector

**Řídicí agent**:
Agent fáze Planning, který drží KIT, KIQ a zadání pro sběrné agenty.
_Avoid_: orchestrátor, plánovač

**Analytický agent**:
Agent fází Analysis a Dissemination, který spojuje výstupy sběrných agentů a píše Brief.
_Avoid_: reportovací agent

### Komunikace agentů

**Zadání**:
Zpráva řídicího agenta sběrnému agentovi o tom, co a kde má zjistit.
_Avoid_: úkol, task, job

**Nález**:
Jedna zjištěná skutečnost od sběrného agenta s KIQ, zdrojem a datem, například akční cena produktu nebo nový sněmovní tisk.
_Avoid_: záznam, výsledek, data

**Dotaz**:
Zpráva analytického agenta řídicímu agentovi o mezeře nebo nejasnosti v nálezech; řídicí agent z ní udělá nové zadání.
_Avoid_: požadavek, feedback

**Selhání**:
Zpráva, že zadání se nepodařilo splnit, například protože zdroj nebyl dostupný; Brief pak mezeru výslovně uvede.
_Avoid_: chyba, error, timeout

### Sběr

**Běh**:
Jedno spuštění systému: buď denní sběr, nebo týdenní tvorba Briefu.
_Avoid_: job, run, iterace

**Snímek**:
Stav zdroje, jak ho zachytil jeden sběrný agent při jednom běhu; snímky se nepřepisují, takže z nich vzniká historie.
_Avoid_: export, dump, záloha

### Výstupy

**Brief**:
Týdenní souhrn pro zadavatele ve třech částech: Co se stalo (podle KIQ), Netypické nálezy a Doporučení.
_Avoid_: report, zpráva, souhrn

**Report**:
Semestrální práce týmu odevzdávaná do Moodlu, ne výstup systému.
_Avoid_: zpráva, brief

### Ceny

**Akční cena**:
Cena produktu platná jen po omezené období, které je u ní uvedené.
_Avoid_: promo cena, sleva, akce

**Párování**:
Přiřazení akční ceny k produktu ze sortimentu, kterého se týká.
_Avoid_: matching, mapování, propojení

**Spárovaná akční cena**:
Akční cena, u které je známý její produkt ze sortimentu; nespárovaná akční cena ho zatím nemá (například akční cena na „vybrané druhy“).
_Avoid_: přiřazená, napárovaná, neznámý produkt

**Cena za litr**:
Cena přepočtená na jeden litr výrobku.
_Avoid_: jednotková cena

**Cena za litr etanolu**:
Cena přepočtená na jeden litr čistého alkoholu (objem × obsah alkoholu); hlavní měřítko pro srovnání napříč kategoriemi.
_Avoid_: cena za litr čistého lihu, cena za l. a.
