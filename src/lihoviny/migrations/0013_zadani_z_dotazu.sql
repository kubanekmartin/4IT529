-- Smyčka Dotazů (#26). Dotaz analytického agenta převede řídicí agent na Zadání z Dotazu pro
-- sběrného agenta. Celá výměna kolem Briefu (Zadání Briefu, Dotazy, Zadání z Dotazu a všechny
-- odpovědi) má jedno ID konverzace, aby ji log komunikace ukázal pohromadě. V jedné konverzaci
-- tak může být víc Zadání, každé ale pro jiného agenta.
ALTER TABLE zadani DROP CONSTRAINT zadani_conversation_id_key;
ALTER TABLE zadani ADD CONSTRAINT zadani_conversation_id_agent_key UNIQUE (conversation_id, agent);

-- Dotaz (zpráva QUERY), ze kterého Zadání vzniklo; NULL u Zadání z rozvrhu nebo ručního běhu.
ALTER TABLE zadani ADD COLUMN dotaz_id bigint REFERENCES messages (id);

-- Termín odpovědi: po něm řídicí agent zapíše Selhání. Běžné Zadání má 30 minut od odeslání,
-- Zadání z Dotazu musí skončit dřív, aby analytickému agentovi zbyl čas Brief dopsat.
ALTER TABLE zadani ADD COLUMN deadline timestamptz;
UPDATE zadani SET deadline = sent_at + interval '30 minutes';
ALTER TABLE zadani ALTER COLUMN deadline SET NOT NULL;

DROP INDEX zadani_waiting_idx;
CREATE INDEX zadani_waiting_idx ON zadani (deadline) WHERE outcome IS NULL;
