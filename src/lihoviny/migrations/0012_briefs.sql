-- Briefy analytického agenta: týdenní souhrn ve třech částech (Co se stalo podle KIQ,
-- Netypické nálezy, Doporučení) s grafy a se Selháními sběru. Brief se po zapsání nemění,
-- archiv je řada Briefů. Části i grafy jsou uložené celé (text, odkazy na zdroje, SVG),
-- takže starý Brief vypadá stejně i po změně kódu nebo konfigurace portfolia.
CREATE TABLE briefs (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Běh řídicího agenta, ve kterém Brief vznikl; NULL u Zadání mimo řídicího agenta.
    beh_id          bigint      REFERENCES behy (id),
    created_at      timestamptz NOT NULL,
    -- Brief pokrývá nálezy z období (period_start, created_at].
    period_start    timestamptz NOT NULL,
    what_happened   jsonb       NOT NULL CHECK (jsonb_typeof(what_happened) = 'array'),
    unusual         jsonb       NOT NULL CHECK (jsonb_typeof(unusual) = 'array'),
    recommendations jsonb       NOT NULL CHECK (jsonb_typeof(recommendations) = 'array'),
    charts          jsonb       NOT NULL CHECK (jsonb_typeof(charts) = 'array'),
    failures        jsonb       NOT NULL CHECK (jsonb_typeof(failures) = 'array')
);

CREATE INDEX briefs_created_at_idx ON briefs (created_at DESC);

-- Nálezy, o které se tvrzení Briefu opírají (každé tvrzení má aspoň jeden).
CREATE TABLE brief_findings (
    brief_id   bigint NOT NULL REFERENCES briefs (id),
    finding_id bigint NOT NULL REFERENCES findings (id),
    PRIMARY KEY (brief_id, finding_id)
);

CREATE INDEX brief_findings_finding_idx ON brief_findings (finding_id);
