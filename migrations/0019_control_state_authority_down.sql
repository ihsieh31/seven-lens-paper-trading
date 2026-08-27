-- 0019 down: remove the narrow control-state authority functions.

DROP FUNCTION IF EXISTS public.bump_flatten_generation();
DROP FUNCTION IF EXISTS public.resume_entries();
DROP FUNCTION IF EXISTS public.pause_entries(TEXT);
DROP FUNCTION IF EXISTS public.lock_control_state_for_submission();
DELETE FROM public.schema_migrations WHERE version = 19;
