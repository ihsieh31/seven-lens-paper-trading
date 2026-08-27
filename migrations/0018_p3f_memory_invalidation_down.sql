DROP FUNCTION IF EXISTS public.invalidate_memory_artifact(TEXT, TEXT, TEXT);
DELETE FROM public.schema_migrations WHERE version = 18;
