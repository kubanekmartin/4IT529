-- Články (KIQ 3), které mediální agent u zdroje viděl. Podle nich pozná článek nový od minulého
-- snímku a nerelevantní článek nedává LLM posoudit znovu. Ukládá se jen ID článku u zdroje:
-- titulek ani adresa se slugem ne, protože mohou obsahovat jména osob.
CREATE TABLE articles (
    source                 text   NOT NULL,
    source_article_id      bigint NOT NULL,
    first_seen_snapshot_id bigint NOT NULL REFERENCES snapshots (id),
    -- Nález z článku; NULL = LLM článek vyhodnotil jako nerelevantní.
    finding_id             bigint REFERENCES findings (id),
    PRIMARY KEY (source, source_article_id)
);
