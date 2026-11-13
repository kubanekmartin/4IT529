-- Nálezy obsažené ve snímku. Nález vzniká jen při prvním výskytu (snapshot_id u nálezu),
-- každý další snímek, který ho u zdroje vidí znovu, na něj odkazuje tady. Web podle toho
-- ukáže akční cenu bez data konce, dokud ji zdroj uvádí, a z řady snímků je vidět historie.
CREATE TABLE snapshot_findings (
    snapshot_id bigint NOT NULL REFERENCES snapshots (id),
    finding_id  bigint NOT NULL REFERENCES findings (id),
    PRIMARY KEY (snapshot_id, finding_id)
);

CREATE INDEX snapshot_findings_finding_idx ON snapshot_findings (finding_id);
