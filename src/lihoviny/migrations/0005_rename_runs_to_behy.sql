-- Názvy podle GLOSSARY.md: Běh (ne „run“) a Zadání (ne „task“). Data zůstávají, mění se jen
-- jména tabulek, sloupce s odkazem na běh, omezení, indexů a sekvence.
ALTER TABLE runs RENAME TO behy;
ALTER TABLE behy RENAME CONSTRAINT runs_pkey TO behy_pkey;
ALTER TABLE behy RENAME CONSTRAINT runs_kind_scheduled_for_key TO behy_kind_scheduled_for_key;
ALTER TABLE behy RENAME CONSTRAINT runs_kind_check TO behy_kind_check;
ALTER SEQUENCE runs_id_seq RENAME TO behy_id_seq;

ALTER TABLE run_tasks RENAME TO zadani;
ALTER TABLE zadani RENAME COLUMN run_id TO beh_id;
ALTER TABLE zadani RENAME CONSTRAINT run_tasks_pkey TO zadani_pkey;
ALTER TABLE zadani RENAME CONSTRAINT run_tasks_conversation_id_key TO zadani_conversation_id_key;
ALTER TABLE zadani RENAME CONSTRAINT run_tasks_outcome_check TO zadani_outcome_check;
ALTER TABLE zadani RENAME CONSTRAINT run_tasks_run_id_fkey TO zadani_beh_id_fkey;
ALTER INDEX run_tasks_waiting_idx RENAME TO zadani_waiting_idx;
