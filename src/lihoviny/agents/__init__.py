"""Registr agentů: jméno na sběrnici → třída. `lihoviny agent <jméno>` spouští odsud.

Nový agent = modul v tomto balíčku, řádek v `AGENTS` a služba v `compose.yaml`.
"""

from lihoviny.agent import Agent
from lihoviny.agents.cenovy import Cenovy
from lihoviny.agents.ozvena import Ozvena

AGENTS: dict[str, type[Agent]] = {
    agent.name: agent
    for agent in [
        Ozvena,
        Cenovy,
    ]
}
