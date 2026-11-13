-- Sběrnice zpráv (ADR 0001). Každý řádek je jedna zpráva mezi agenty a zároveň záznam
-- v logu komunikace na webu. Odeslání = INSERT, vyzvednutí = nastavení claimed_at.
CREATE TABLE messages (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid        NOT NULL,
    kind            text        NOT NULL CHECK (kind IN ('REQUEST', 'INFORM', 'QUERY', 'FAILURE')),
    sender          text        NOT NULL,
    recipient       text        NOT NULL,
    content         jsonb       NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    sent_at         timestamptz NOT NULL,
    claimed_at      timestamptz
);

CREATE INDEX messages_unclaimed_idx ON messages (recipient, id) WHERE claimed_at IS NULL;
CREATE INDEX messages_conversation_idx ON messages (conversation_id, id);

-- Probudí agenta, který poslouchá na kanálu `messages`; payload je jméno příjemce.
CREATE FUNCTION notify_message_recipient() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('messages', NEW.recipient);
    RETURN NULL;
END;
$$;

CREATE TRIGGER messages_notify_recipient
AFTER INSERT ON messages
FOR EACH ROW EXECUTE FUNCTION notify_message_recipient();

-- Sdílená tabule (blackboard): snímky zdrojů a nálezy sběrných agentů, ze kterých čte
-- analytický agent. Snímky se nepřepisují, historie vzniká z jejich řady.
CREATE TABLE snapshots (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent      text        NOT NULL,
    source     text        NOT NULL,
    taken_at   timestamptz NOT NULL,
    raw_paths  text[]      NOT NULL,
    item_count integer     NOT NULL CHECK (item_count >= 0)
);

CREATE INDEX snapshots_agent_taken_at_idx ON snapshots (agent, taken_at DESC);

CREATE TABLE findings (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kiq         smallint    NOT NULL CHECK (kiq BETWEEN 1 AND 4),
    agent       text        NOT NULL,
    type        text        NOT NULL
                CHECK (type IN ('novinka', 'akcni_cena', 'clanek', 'snemovni_tisk')),
    -- Cizí klíč na produkty přidá migrace sortimentního agenta, až tabulka produktů vznikne.
    product_id  bigint,
    snapshot_id bigint      REFERENCES snapshots (id),
    source_url  text        NOT NULL,
    content     jsonb       NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    found_at    timestamptz NOT NULL
);

CREATE INDEX findings_kiq_found_at_idx ON findings (kiq, found_at DESC);
CREATE INDEX findings_snapshot_idx ON findings (snapshot_id);
