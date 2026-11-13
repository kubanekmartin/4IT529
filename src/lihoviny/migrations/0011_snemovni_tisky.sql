-- Sněmovní tisky (KIQ 4), které regulatorní agent už viděl v některém snímku. Nový tisk je
-- ten, který tu ještě není; nález vzniká jen u nového tisku, který se týká lihovin.
-- Jen identifikátory z otevřených dat Sněmovny: názvy tisků mohou nést jména poslanců.
CREATE TABLE snemovni_tisky (
    source_id              bigint PRIMARY KEY,  -- tisky.id_tisk v otevřených datech psp.cz
    first_seen_snapshot_id bigint NOT NULL REFERENCES snapshots (id)
);
