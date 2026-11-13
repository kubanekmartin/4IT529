"""Společné fixtures: každý test dostane vlastní čerstvě zmigrovanou databázi.

Testy běží proti skutečnému Postgresu z `compose.test.yaml` (nebo z `TEST_DATABASE_URL`).
Názvy databází i role pro web jsou náhodné, takže testy z více worktrees si nepřekážejí.
"""

import os
import threading
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from lihoviny.board import Board
from lihoviny.bus import Bus
from lihoviny.clock import FakeClock
from lihoviny.fetch import FakeFetcher
from lihoviny.llm import FakeLlm
from lihoviny.migrations import migrate

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/postgres")
WEB_PASSWORD = "jen-pro-testy"


@pytest.fixture(scope="session")
def web_user():
    name = f"web_{uuid.uuid4().hex[:12]}"
    yield name
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.fixture
def database_url(web_user):
    name = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(ADMIN_URL, dbname=name)
    migrate(url, web_user=web_user, web_password=WEB_PASSWORD)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def web_database_url(database_url, web_user):
    return make_conninfo(database_url, user=web_user, password=WEB_PASSWORD)


@pytest.fixture
def reject_writes(database_url):
    """Databáze přijme do tabulky jen `after` řádků a další zápis odmítne, jako by agent spadl
    uprostřed práce nebo došlo místo na disku. Vrací funkci, která odmítání zase vypne."""

    def reject(table, *, after):
        with psycopg.connect(database_url) as conn:
            conn.execute(
                sql.SQL(
                    """
                    CREATE FUNCTION reject_write() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                        IF (SELECT count(*) FROM {table}) >= {after} THEN
                            RAISE EXCEPTION 'zápis odmítnut (test)';
                        END IF;
                        RETURN NEW;
                    END $$;
                    CREATE TRIGGER reject_write BEFORE INSERT ON {table}
                    FOR EACH ROW EXECUTE FUNCTION reject_write();
                    """
                ).format(table=sql.Identifier(table), after=sql.Literal(after))
            )

        def allow():
            with psycopg.connect(database_url) as conn:
                conn.execute(
                    sql.SQL("DROP TRIGGER reject_write ON {}").format(sql.Identifier(table))
                )

        return allow

    return reject


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 11, 16, 6, 0, tzinfo=UTC))


@pytest.fixture
def bus(database_url, clock):
    return Bus(database_url, clock)


@pytest.fixture
def board(database_url, clock):
    return Board(database_url, clock)


@pytest.fixture
def raw_dir(tmp_path):
    return tmp_path / "raw"


@pytest.fixture
def make_agent(bus, board, clock, raw_dir):
    """Postaví agenta s náhražkami vnějšího světa; přepiš `fetcher` nebo `llm` podle testu."""

    def make(agent_class, *, fetcher=None, llm=None):
        return agent_class(
            bus=bus,
            board=board,
            fetcher=fetcher or FakeFetcher({}, raw_dir=raw_dir, clock=clock),
            llm=llm or FakeLlm(),
            clock=clock,
        )

    return make


@pytest.fixture
def start_agent(database_url):
    """Spustí smyčku agenta ve vlákně, jako běží služba v compose; po testu ji zastaví."""
    running = []

    def start(agent, *, poll_interval=30.0):
        stop = threading.Event()
        thread = threading.Thread(
            target=agent.run, kwargs={"poll_interval": poll_interval, "stop": stop}
        )
        thread.start()
        running.append((stop, thread))

    yield start
    for stop, thread in running:
        stop.set()
        thread.join(timeout=5)
