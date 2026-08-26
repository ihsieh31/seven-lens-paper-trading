-- 0016 down: remove the single-head authority constraint on a disposable DB.

ALTER TABLE public.reflection_corrections
    DROP CONSTRAINT IF EXISTS reflection_corrections_single_head_key;

DELETE FROM public.schema_migrations WHERE version = 16;
