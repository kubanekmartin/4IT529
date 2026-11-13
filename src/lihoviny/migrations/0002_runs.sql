-- Běhy řídicího agenta: denní sběr nebo tvorba Briefu, z rozvrhu nebo na ruční pokyn.
-- Web z nich ukazuje varování, když agent v posledním běhu selhal nebo vrátil 0 nálezů.
CREATE TABLE runs (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind          text        NOT NULL CHECK (kind IN ('collection', 'brief')),
    -- Čas z rozvrhu, za který běh proběhl; NULL u ručního běhu. Unikátnost brání tomu,
    -- aby stejný termín rozvrhu spustil běh dvakrát (restart, dvě repliky).
    scheduled_for timestamptz,
    started_at    timestamptz NOT NULL,
    UNIQUE (kind, scheduled_for)
);

-- Jedno Zadání v běhu: komu šlo, v jaké konverzaci a jak dopadlo.
-- outcome NULL = čeká na odpověď; FAILURE i tehdy, když ho po 30 minutách zapsal řídicí agent.
CREATE TABLE run_tasks (
    run_id          bigint      NOT NULL REFERENCES runs (id),
    agent           text        NOT NULL,
    conversation_id uuid        NOT NULL UNIQUE,
    sent_at         timestamptz NOT NULL,
    outcome         text        CHECK (outcome IN ('INFORM', 'FAILURE')),
    -- Z odpovědi sběrného agenta (CollectorResult); u ostatních agentů NULL.
    new_findings    integer,
    reason          text,
    answered_at     timestamptz,
    PRIMARY KEY (run_id, agent)
);

CREATE INDEX run_tasks_waiting_idx ON run_tasks (sent_at) WHERE outcome IS NULL;
