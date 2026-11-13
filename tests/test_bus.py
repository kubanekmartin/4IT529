"""Doručování zpráv přes sběrnici: nejvýš jednou, probuzení přes NOTIFY a pojistka v intervalu."""

import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.types.json import Jsonb

from lihoviny.agents.ozvena import Ozvena
from lihoviny.bus import Kind


def send_zadani(bus, content):
    return bus.send(kind=Kind.REQUEST, sender="clen-tymu", recipient="ozvena", content=content)


def wait_for_reply(bus, zadani, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        messages = bus.conversation(zadani.conversation_id)
        if len(messages) > 1:
            return messages[-1]
        time.sleep(0.02)
    return None


def test_each_zadani_is_claimed_at_most_once_by_concurrent_replicas(bus, make_agent):
    sent = [send_zadani(bus, {"n": n}) for n in range(30)]
    replicas = [make_agent(Ozvena) for _ in range(3)]

    with ThreadPoolExecutor(len(replicas)) as pool:
        handled = sum(pool.map(lambda agent: agent.process_pending(), replicas))

    assert handled == 30
    for zadani in sent:
        replies = [m for m in bus.conversation(zadani.conversation_id) if m.kind is Kind.INFORM]
        assert len(replies) == 1


def test_running_agent_is_woken_by_notify_long_before_its_poll_interval(
    bus, make_agent, start_agent
):
    start_agent(make_agent(Ozvena), poll_interval=60)
    assert wait_for_reply(bus, send_zadani(bus, {"text": "rozehřátí"})) is not None

    reply = wait_for_reply(bus, send_zadani(bus, {"text": "ahoj"}), timeout=5)

    assert reply is not None
    assert reply.content == {"text": "ahoj"}


def test_running_agent_finds_message_without_notify_at_next_poll(
    bus, make_agent, start_agent, database_url
):
    start_agent(make_agent(Ozvena), poll_interval=0.2)
    assert wait_for_reply(bus, send_zadani(bus, {"text": "rozehřátí"})) is not None

    zadani = insert_without_notify(database_url, bus, {"text": "ztracené oznámení"})
    reply = wait_for_reply(bus, zadani, timeout=5)

    assert reply is not None
    assert reply.content == {"text": "ztracené oznámení"}


def insert_without_notify(database_url, bus, content):
    """Simuluje ztracené oznámení: v režimu replica se triggery (a tedy NOTIFY) nespustí."""
    with psycopg.connect(database_url) as conn:
        conn.execute("SET session_replication_role = replica")
        conversation_id = conn.execute(
            """
            INSERT INTO messages (conversation_id, kind, sender, recipient, content, sent_at)
            VALUES (gen_random_uuid(), 'REQUEST', 'clen-tymu', 'ozvena', %s, now())
            RETURNING conversation_id
            """,
            [Jsonb(content)],
        ).fetchone()[0]
    return bus.conversation(conversation_id)[0]
