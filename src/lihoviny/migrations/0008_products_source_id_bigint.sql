-- ID produktu u zdroje je číslo (Rohlík ho přiděluje postupně a pravidlo Novinky ID porovnává),
-- takže ho ukládáme jako číslo, ne jako text převáděný při každém čtení.
ALTER TABLE products ALTER COLUMN source_product_id TYPE bigint USING source_product_id::bigint;
