-- P2-C down migration: remove reconciliation run evidence added by 0004.
-- Applied only against a disposable/restore-drill database.

DROP TABLE IF EXISTS public.reconciliation_runs;

DELETE FROM public.schema_migrations WHERE version = 4;
