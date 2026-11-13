from lihoviny.agents.ozvena import Ozvena
from lihoviny.bus import Kind


def test_ozvena_answers_zadani_with_inform_of_same_content(bus, clock, make_agent):
    ozvena = make_agent(Ozvena)
    zadani = bus.send(
        kind=Kind.REQUEST, sender="clen-tymu", recipient="ozvena", content={"text": "ahoj"}
    )

    ozvena.process_pending()

    request, reply = bus.conversation(zadani.conversation_id)
    assert request.claimed_at == clock.now()
    assert (reply.kind, reply.sender, reply.recipient, reply.content) == (
        Kind.INFORM,
        "ozvena",
        "clen-tymu",
        {"text": "ahoj"},
    )


def test_error_while_handling_zadani_returns_failure_with_reason_to_sender(bus, make_agent):
    ozvena = make_agent(Ozvena)
    zadani = bus.send(
        kind=Kind.REQUEST,
        sender="clen-tymu",
        recipient="ozvena",
        content={"fail": "zdroj nedostupný"},
    )

    ozvena.process_pending()

    reply = bus.conversation(zadani.conversation_id)[-1]
    assert (reply.kind, reply.recipient, reply.content) == (
        Kind.FAILURE,
        "clen-tymu",
        {"reason": "zdroj nedostupný"},
    )
