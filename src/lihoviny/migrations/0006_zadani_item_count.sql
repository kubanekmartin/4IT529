-- Počet položek ve snímku z odpovědi sběrného agenta (OdpovedSberu). Varování na webu se řídí
-- prázdným snímkem, ne počtem nových nálezů: den bez Novinky je běžný, prázdný snímek ne.
-- U ostatních agentů a u starších odpovědí NULL.
ALTER TABLE zadani ADD COLUMN item_count integer;
