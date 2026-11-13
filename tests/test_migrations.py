"""Migrace běží při každém `docker compose up`, takže musí jít spustit opakovaně."""

import psycopg
import pytest

from lihoviny.migrations import migrate


def test_migrate_twice_applies_nothing_the_second_time(database_url, web_user):
    assert migrate(database_url, web_user=web_user, web_password="jen-pro-testy") == []


def test_finding_can_point_only_to_an_existing_product(board):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        board.record_finding(
            kiq=1,
            agent="sortimentni",
            type="novinka",
            source_url="https://www.rohlik.cz/1-neexistuje",
            content={},
            product_id=999,
        )


def test_web_role_can_read_messages_but_not_write_them(web_database_url):
    with psycopg.connect(web_database_url) as conn:
        assert conn.execute("SELECT count(*) FROM messages").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                """
                INSERT INTO messages (conversation_id, kind, sender, recipient, content, sent_at)
                VALUES (gen_random_uuid(), 'REQUEST', 'web', 'ozvena', '{}', now())
                """
            )
