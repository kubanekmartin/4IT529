-- Zprávy úřadů (KIQ 4): tiskové zprávy MF a aktuality Celní správy, které regulatorní agent
-- vyhodnotil jako relevantní. Jen rozšíří povolené typy nálezů; žádná data se nemění.
ALTER TABLE findings DROP CONSTRAINT findings_type_check;
ALTER TABLE findings ADD CONSTRAINT findings_type_check
    CHECK (type IN ('novinka', 'akcni_cena', 'clanek', 'snemovni_tisk', 'uredni_zprava'));
