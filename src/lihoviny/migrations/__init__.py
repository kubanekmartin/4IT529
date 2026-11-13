"""Číslované migrace schématu: `NNNN_popis.sql` v tomto adresáři, aplikované podle čísla.

Novou migraci přidáš jen novým souborem s dalším (přiděleným) číslem. Už aplikovaný soubor
se nikdy nemění; oprava je vždy další migrace.
"""

from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS_DIR = Path(__file__).parent

# Zámek, aby dvě současně spuštěné migrace (např. dva `docker compose up`) nešly přes sebe.
_LOCK_ID = 4_529_017


def migrate(database_url: str, *, web_user: str = "", web_password: str = "") -> list[str]:
    """Aplikuje chybějící migrace a dá roli webu právo jen ke čtení. Vrátí nově aplikované."""
    applied_now = []
    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_ID])
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            if path.stem in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", [path.stem])
            applied_now.append(path.stem)
        if web_user:
            _grant_read_only(conn, web_user, web_password)
    return applied_now


def _grant_read_only(conn: psycopg.Connection, user: str, password: str) -> None:
    role = sql.Identifier(user)
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [user]).fetchone()
    verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
    conn.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(verb, role, sql.Literal(password)))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role))
