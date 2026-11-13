"""KIQ, jejich sběrní agenti a časový rozpočet Briefu: společná pravidla řídicího
a analytického agenta, aby se nerozešla.

Časový rozpočet Zadání Briefu (`ANSWER_LIMIT`, 30 minut) platí i s kolem Dotazů:
1. Řídicí agent převede každý Dotaz na Zadání z Dotazu voláním LLM, nejvýš
   `MAX_DOTAZY` × `DOTAZ_LLM_TIMEOUT` × (1 + `DOTAZ_LLM_RETRIES`) = 3 × 2 minuty.
2. Zadání z Dotazu musí odpovědět `FINISH_TIME` před termínem Zadání Briefu, tedy do
   15 minut od něj; jinak za něj řídicí agent zapíše Selhání.
3. Analytickému agentovi zbude 15 minut na Brief: jedno volání trvá nejvýš
   `BRIEF_LLM_TIMEOUT` × (1 + `BRIEF_LLM_RETRIES`) = 12 minut.
"""

from datetime import timedelta

KIQS = {1: "Sortiment", 2: "Promo ceny", 3: "Komunikace", 4: "Regulace"}
# Jeden sběrný agent na KIQ (spec #15): jemu jde Zadání z Dotazu, podle něj Brief přiřadí
# Selhání ke KIQ.
SBERNY_AGENT_OF_KIQ = {1: "sortimentni", 2: "cenovy", 3: "medialni", 4: "regulatorni"}
KIQ_OF_AGENT = {agent: kiq for kiq, agent in SBERNY_AGENT_OF_KIQ.items()}

ANSWER_LIMIT = timedelta(minutes=30)  # na odpověď na Zadání; pak Selhání
FINISH_TIME = timedelta(minutes=15)  # rezerva na dopsání Briefu po odpovědích na Dotazy
MAX_DOTAZY = 3  # na jeden běh, v jednom kole

# Převod Dotazu na Zadání je krátký text: krátký limit, aby tři převody nesnědly rezervu.
DOTAZ_LLM_TIMEOUT = 60.0
DOTAZ_LLM_RETRIES = 1
# Psaní Briefu je delší úloha než třídění u sběrných agentů.
BRIEF_LLM_TIMEOUT = 240.0
BRIEF_LLM_RETRIES = 2
