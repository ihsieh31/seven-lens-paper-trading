-- 0014 down migration: remove the reconciliation scope metadata.
-- Applied only against a disposable/restore-drill database.

ALTER TABLE public.reconciliation_runs
    DROP COLUMN IF EXISTS scope;

DELETE FROM public.schema_migrations WHERE version = 14;
