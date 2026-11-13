from typing import Any

from lihoviny.agent import Agent
from lihoviny.bus import Message


class OzvenaError(Exception):
    pass


class Ozvena(Agent):
    """Diagnostický agent: ověřuje sběrnici tím, že vrátí obsah Zadání beze změny.

    Zadání s klíčem `fail` naopak selže s uvedeným důvodem, aby šla předvést i cesta FAILURE.
    """

    name = "ozvena"
    role = "diagnostika: odpoví INFORM se stejným obsahem"

    def handle_request(self, request: Message) -> dict[str, Any]:
        if "fail" in request.content:
            raise OzvenaError(request.content["fail"])
        return request.content
