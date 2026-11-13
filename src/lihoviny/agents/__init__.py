"""Registr agentů: jméno na sběrnici → třída. `lihoviny agent <jméno>` spouští odsud.

Nový agent = modul v tomto balíčku, řádek v `AGENTS` a služba v `compose.yaml`.
Sběrný agent navíc patří do `Ridici.sberni_agenti` (agents/ridici.py), jinak nedostane Zadání.
"""

from lihoviny.agent import Agent
from lihoviny.agents.cenovy import Cenovy
from lihoviny.agents.ozvena import Ozvena
from lihoviny.agents.regulatorni import Regulatorni
from lihoviny.agents.ridici import Ridici
from lihoviny.agents.sortimentni import Sortimentni

AGENTS: dict[str, type[Agent]] = {
    agent.name: agent
    for agent in [
        Ozvena,
        Ridici,
        Sortimentni,
        Cenovy,
        Regulatorni,
    ]
}
