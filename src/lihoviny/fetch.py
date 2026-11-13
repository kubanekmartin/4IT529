"""Adaptér stahování ze zdrojů."""

from pathlib import Path
from typing import Protocol

from lihoviny.clock import Clock


class Fetcher(Protocol):
    pass


class FakeFetcher:
    def __init__(self, routes: dict, *, raw_dir: Path, clock: Clock):
        self._routes = routes
