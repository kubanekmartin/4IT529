-- Párování akčních cen na produkty (cenový agent): dvojice, které LLM posoudil jako různé
-- produkty. Párují se názvy akčních cen, jak je píše zdroj (`findings.content->>'product'`).
-- Cenový agent se na stejnou dvojici LLM znovu neptá; akční cena jde na LLM znovu, až
-- ke značce, objemu a obsahu alkoholu přibude v sortimentu nový produkt.
CREATE TABLE rejected_pairings (
    offer_name text   NOT NULL,
    product_id bigint NOT NULL REFERENCES products (id),
    PRIMARY KEY (offer_name, product_id)
);
