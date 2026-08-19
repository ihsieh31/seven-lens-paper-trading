-- P2-D down migration: remove the control plane added by 0005.
-- Applied only against a disposable/restore-drill database.

DROP TABLE IF EXISTS public.control_state;
DROP TABLE IF EXISTS public.control_commands;
DROP FUNCTION IF EXISTS public.guard_control_state_write();

DELETE FROM public.schema_migrations WHERE version = 5;
