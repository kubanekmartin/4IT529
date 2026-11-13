"""Adaptér stahování proti simulovanému zdroji (httpx2.MockTransport), čas běží na FakeClock."""

import hashlib

import httpx2
import pytest

from lihoviny.fetch import FetchError, HttpFetcher, read_raw

URL = "https://zdroj.example/akce?strana=1"


def make_fetcher(responses, *, clock, raw_dir, min_delay=1.0):
    """`responses` je seznam odpovědí, které zdroj vrátí postupně; požadavky se zapisují."""
    requests = []

    def handler(request):
        requests.append(request)
        return responses[min(len(requests), len(responses)) - 1]

    fetcher = HttpFetcher(
        raw_dir=raw_dir, clock=clock, min_delay=min_delay, transport=httpx2.MockTransport(handler)
    )
    return fetcher, requests


def test_fetch_saves_raw_response_to_disk_and_identifies_without_personal_data(clock, raw_dir):
    body = "<html>Božkov Original 1 l</html>".encode()
    fetcher, requests = make_fetcher(
        [httpx2.Response(200, content=body, headers={"content-type": "text/html; charset=utf-8"})],
        clock=clock,
        raw_dir=raw_dir,
    )

    page = fetcher.get(URL)

    assert page.text == "<html>Božkov Original 1 l</html>"
    meta, raw_body = read_raw(page.raw_path)
    assert raw_body == body
    assert (meta["url"], meta["status"], meta["fetched_at"]) == (
        URL,
        200,
        "2026-11-16T06:00:00+00:00",
    )
    user_agent = requests[0].headers["user-agent"]
    assert user_agent.startswith("lihoviny-mas/")
    assert "@" not in user_agent


def test_fetch_with_minimize_saves_only_minimized_body_and_hash_of_original(clock, raw_dir):
    body = "id|jméno\n1|Jan Novák\n".encode()
    fetcher, _ = make_fetcher([httpx2.Response(200, content=body)], clock=clock, raw_dir=raw_dir)

    page = fetcher.get(URL, minimize=lambda original: original.split(b"|")[0])

    assert page.body == body  # agent čte celou odpověď, na disk jde jen zmenšená
    meta, raw_body = read_raw(page.raw_path)
    assert raw_body == b"id"
    assert (meta["original_sha256"], meta["original_bytes"]) == (
        hashlib.sha256(body).hexdigest(),
        len(body),
    )


def test_fetch_pauses_between_requests_to_the_same_source(clock, raw_dir):
    fetcher, _ = make_fetcher([httpx2.Response(200)], clock=clock, raw_dir=raw_dir, min_delay=1.5)

    fetcher.get(URL)
    fetcher.get(URL)
    clock.advance(seconds=10)
    fetcher.get(URL)

    assert clock.sleeps == [1.5]


def test_fetch_retries_three_times_with_growing_pause_then_fails(clock, raw_dir):
    fetcher, requests = make_fetcher([httpx2.Response(503)], clock=clock, raw_dir=raw_dir)

    with pytest.raises(FetchError, match="HTTP 503"):
        fetcher.get(URL)

    assert len(requests) == 3
    assert clock.sleeps == [2.0, 4.0]


def test_fetch_waits_as_long_as_retry_after_asks_on_429(clock, raw_dir):
    fetcher, requests = make_fetcher(
        [httpx2.Response(429, headers={"retry-after": "7"}), httpx2.Response(200, text="ok")],
        clock=clock,
        raw_dir=raw_dir,
    )

    page = fetcher.get(URL)

    assert page.text == "ok"
    assert len(requests) == 2
    assert clock.sleeps == [7.0]


def test_fetch_does_not_retry_when_source_says_page_does_not_exist(clock, raw_dir):
    fetcher, requests = make_fetcher([httpx2.Response(404)], clock=clock, raw_dir=raw_dir)

    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher.get(URL)

    assert len(requests) == 1
