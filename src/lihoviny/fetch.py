"""Adaptér stahování ze zdrojů.

`HttpFetcher` stahuje ohleduplně: mezi požadavky na tentýž zdroj drží pauzu (crawl-delay),
při 429 a 5xx to zkusí celkem třikrát s rostoucí pauzou a respektuje `Retry-After`.
Každou úspěšnou odpověď uloží zabalenou (gzip) na disk, aby šla zpětně znovu zpracovat.
V testech ho nahradí `FakeFetcher`, který místo webu vrací uložené ukázky stránek.
"""

import email.utils
import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx2

from lihoviny.clock import Clock

# Obecný název projektu, žádná jména ani e-maily (zadání zakazuje osobní údaje).
USER_AGENT = "lihoviny-mas/0.1 (studijni prototyp, nekomercni)"

_RETRYABLE = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """Zdroj se nepodařilo stáhnout; agent z ní udělá Selhání."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    fetched_at: datetime
    raw_path: Path = field(repr=False)

    @property
    def text(self) -> str:
        return self.body.decode(_charset(self.headers.get("content-type", "")))

    def json(self) -> Any:
        return json.loads(self.body)


class Fetcher(Protocol):
    def get(self, url: str) -> FetchResult: ...


class HttpFetcher:
    def __init__(
        self,
        *,
        raw_dir: Path,
        clock: Clock,
        min_delay: float = 1.0,
        attempts: int = 3,
        backoff: float = 2.0,
        timeout: float = 30.0,
        transport: httpx2.BaseTransport | None = None,
    ):
        self._raw_dir = raw_dir
        self._clock = clock
        self._min_delay = min_delay
        self._attempts = attempts
        self._backoff = backoff
        self._last_request_at: dict[str, datetime] = {}
        self._client = httpx2.Client(
            headers={"user-agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def get(self, url: str) -> FetchResult:
        host = urlsplit(url).hostname or ""
        problem = ""
        for attempt in range(1, self._attempts + 1):
            self._keep_delay(host)
            retry_after = 0.0
            try:
                response = self._client.get(url)
            except httpx2.TransportError as error:
                problem = f"{type(error).__name__}: {error}"
            else:
                if response.status_code < 400:
                    return self._result(url, response)
                problem = f"HTTP {response.status_code}"
                if response.status_code not in _RETRYABLE:
                    raise FetchError(f"{url}: {problem}")
                retry_after = self._retry_after(response.headers.get("retry-after"))
            if attempt < self._attempts:
                self._clock.sleep(max(self._backoff * 2 ** (attempt - 1), retry_after))
        raise FetchError(f"{url}: {self._attempts} pokusy selhaly, naposledy {problem}")

    def _keep_delay(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            wait = self._min_delay - (self._clock.now() - last).total_seconds()
            if wait > 0:
                self._clock.sleep(wait)
        self._last_request_at[host] = self._clock.now()

    def _retry_after(self, value: str | None) -> float:
        if not value:
            return 0.0
        if value.strip().isdigit():
            return float(value)
        try:
            until = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 0.0
        return max((until - self._clock.now()).total_seconds(), 0.0)

    def _result(self, url: str, response: httpx2.Response) -> FetchResult:
        fetched_at = self._clock.now()
        headers = dict(response.headers)
        raw_path = save_raw(
            self._raw_dir, url, response.status_code, headers, response.content, fetched_at
        )
        return FetchResult(
            url, response.status_code, headers, response.content, fetched_at, raw_path
        )


class FakeFetcher:
    """Náhražka pro testy: `routes` mapuje URL na uloženou ukázku (Path), bajty nebo výjimku."""

    def __init__(self, routes: dict[str, Path | bytes | Exception], *, raw_dir: Path, clock: Clock):
        self._routes = routes
        self._raw_dir = raw_dir
        self._clock = clock
        self.requested: list[str] = []

    def get(self, url: str) -> FetchResult:
        self.requested.append(url)
        if url not in self._routes:
            raise FetchError(f"{url}: náhražka pro tuto adresu nemá ukázku")
        route = self._routes[url]
        if isinstance(route, Exception):
            raise route
        body = route.read_bytes() if isinstance(route, Path) else route
        fetched_at = self._clock.now()
        raw_path = save_raw(self._raw_dir, url, 200, {}, body, fetched_at)
        return FetchResult(url, 200, {}, body, fetched_at, raw_path)


def save_raw(
    raw_dir: Path, url: str, status: int, headers: dict[str, str], body: bytes, fetched_at: datetime
) -> Path:
    """Uloží odpověď jako gzip: první řádek JSON s metadaty, za ním surové tělo."""
    host = urlsplit(url).hostname or "neznamy-zdroj"
    name = f"{fetched_at:%H%M%S%f}-{hashlib.sha256(body).hexdigest()[:12]}.gz"
    path = raw_dir / host / f"{fetched_at:%Y-%m-%d}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"url": url, "status": status, "fetched_at": fetched_at.isoformat(), "headers": headers}
    with gzip.open(path, "wb") as file:
        file.write(json.dumps(meta, ensure_ascii=False).encode() + b"\n")
        file.write(body)
    return path


def read_raw(path: Path | str) -> tuple[dict[str, Any], bytes]:
    with gzip.open(path, "rb") as file:
        meta = json.loads(file.readline())
        return meta, file.read()


def _charset(content_type: str) -> str:
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip('"')
    return "utf-8"
