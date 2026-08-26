-- 0014: make reconciliation coverage explicit for the resume safety gate.
--
-- Existing rows are deliberately classified as PARTIAL.  A historical CLEAN
-- result without account-scope evidence must never unlock new entries.

ALTER TABLE public.reconciliation_runs
    ADD COLUMN scope TEXT NOT NULL DEFAULT 'PARTIAL'
    CHECK (scope IN ('PARTIAL', 'FULL'));

COMMENT ON COLUMN public.reconciliation_runs.scope IS
    'Safety scope covered by the run; only FULL CLEAN runs may resume entries.';
