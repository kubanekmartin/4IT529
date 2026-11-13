"""Sdílená tabule (blackboard): snímky zdrojů a nálezy, ze kterých čte analytický agent."""

from lihoviny.clock import Clock


class Board:
    def __init__(self, database_url: str, clock: Clock):
        self._database_url = database_url
        self._clock = clock
