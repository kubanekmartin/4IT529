"""Příkaz `lihoviny`: jeden image, služba v compose vybírá jen podpříkaz.

lihoviny migrate                          aplikuje migrace, nastaví roli webu
lihoviny agent ozvena                     spustí agenta (služba)
lihoviny web                              spustí web
lihoviny zadani ozvena '{"text": "ahoj"}' pošle Zadání a vypíše odpověď
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from lihoviny.agent import Agent
from lihoviny.agents import AGENTS
from lihoviny.board import Board
from lihoviny.bus import Bus, Kind, Message
from lihoviny.clock import SystemClock
from lihoviny.fetch import HttpFetcher
from lihoviny.llm import ClaudeLlm
from lihoviny.migrations import migrate

TEAM_MEMBER = "clen-tymu"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lihoviny", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migrate", help="aplikuje migrace a dá webu roli jen pro čtení")

    agent = commands.add_parser("agent", help="spustí agenta jako službu")
    agent.add_argument("name", choices=sorted(AGENTS))
    agent.add_argument("--poll-interval", type=float, default=30.0, help="pojistka v sekundách")

    web = commands.add_parser("web", help="spustí web")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)

    zadani = commands.add_parser("zadani", help="pošle Zadání agentovi a vypíše odpověď")
    zadani.add_argument("recipient", help="jméno agenta, např. ozvena")
    zadani.add_argument("content", nargs="?", default="{}", help="obsah jako objekt JSON")
    zadani.add_argument("--cekat", type=float, default=30.0, help="kolik sekund čekat na odpověď")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    match args.command:
        case "migrate":
            return run_migrate()
        case "agent":
            build_agent(args.name).run(poll_interval=args.poll_interval)
            return 0
        case "web":
            return run_web(args.host, args.port)
        case "zadani":
            return send_zadani(args.recipient, args.content, wait=args.cekat)
    return 2


def run_migrate() -> int:
    applied = migrate(
        _require_env("DATABASE_URL"),
        web_user=os.environ.get("WEB_DB_USER", ""),
        web_password=os.environ.get("WEB_DB_PASSWORD", ""),
    )
    logging.info("aplikované migrace: %s", ", ".join(applied) or "žádné nové")
    return 0


def build_agent(name: str) -> Agent:
    agent_class = AGENTS[name]
    clock = SystemClock()
    database_url = _require_env("DATABASE_URL")
    return agent_class(
        bus=Bus(database_url, clock),
        board=Board(database_url, clock),
        fetcher=HttpFetcher(
            raw_dir=Path(os.environ.get("RAW_DATA_DIR", "data/raw")),
            clock=clock,
            min_delay=agent_class.fetch_delay,
        ),
        llm=ClaudeLlm.for_agent(name),
        clock=clock,
    )


def run_web(host: str, port: int) -> int:
    import uvicorn

    from lihoviny.web.app import create_app

    uvicorn.run(create_app(_require_env("WEB_DATABASE_URL")), host=host, port=port)
    return 0


def send_zadani(recipient: str, content_json: str, *, wait: float) -> int:
    """Exit kód: 0 = INFORM, 1 = FAILURE nebo jiná odpověď, 2 = bez odpovědi."""
    try:
        content = json.loads(content_json)
    except json.JSONDecodeError as error:
        raise SystemExit(f"obsah Zadání není platný JSON: {error}") from error
    if not isinstance(content, dict):
        raise SystemExit('obsah Zadání musí být objekt JSON, např. \'{"text": "ahoj"}\'')
    bus = Bus(_require_env("DATABASE_URL"), SystemClock())
    request = bus.send(kind=Kind.REQUEST, sender=TEAM_MEMBER, recipient=recipient, content=content)
    _print(request)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        replies = bus.conversation(request.conversation_id)[1:]
        if replies:
            _print(replies[0])
            return 0 if replies[0].kind is Kind.INFORM else 1
        time.sleep(0.2)
    print(f"bez odpovědi do {wait:g} s (běží agent {recipient}?)", file=sys.stderr)
    return 2


def _print(message: Message) -> None:
    content = json.dumps(message.content, ensure_ascii=False)
    print(f"{message.kind} {message.sender} → {message.recipient}: {content}")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"chybí proměnná prostředí {name} (viz .env.example)")
    return value


if __name__ == "__main__":
    sys.exit(main())
