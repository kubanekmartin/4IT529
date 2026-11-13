-- Produkty (KIQ 1): konkrétní balení lihoviny, jak ho poprvé viděl sortimentní agent.
-- Normalizovaný klíč spojuje značku a název (bez obsahu alkoholu a slov o dárkovém balení),
-- objem a obsah alkoholu, takže dárkové balení stejného objemu je týž produkt
-- (`lihoviny.products.product_key`). Podle klíče páruje akce i cenový agent.
CREATE TABLE products (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    normalized_key         text          NOT NULL UNIQUE,
    manufacturer           text,
    brand                  text          NOT NULL,
    name                   text          NOT NULL,
    volume_ml              integer       NOT NULL CHECK (volume_ml > 0),
    abv                    numeric(4, 1) NOT NULL CHECK (abv > 0 AND abv <= 100),
    category               text          NOT NULL CHECK (category IN (
                               'rum_tuzemak', 'vodka', 'horke_likery', 'ostatni_likery', 'gin',
                               'whisky', 'brandy', 'ovocne_destilaty', 'rtd', 'ostatni')),
    -- Kdy a kde byl produkt poprvé viděn: snímek, stránka produktu a jeho ID u zdroje.
    first_seen_at          timestamptz   NOT NULL,
    first_seen_url         text          NOT NULL,
    first_seen_snapshot_id bigint        NOT NULL REFERENCES snapshots (id),
    source_product_id      text          NOT NULL
);

CREATE INDEX products_category_idx ON products (category, brand, name);

ALTER TABLE findings
    ADD CONSTRAINT findings_product_id_fkey FOREIGN KEY (product_id) REFERENCES products (id);

CREATE INDEX findings_product_idx ON findings (product_id);
