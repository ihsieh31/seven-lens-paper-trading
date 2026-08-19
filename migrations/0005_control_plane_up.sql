-- P2-D control plane: audited operator commands and the pause singleton.

CREATE TABLE public.control_commands (
    command_id UUID PRIMARY KEY,
    command TEXT NOT NULL CHECK (command IN (
        'PAUSE_ENTRIES', 'RESUME_ENTRIES', 'CANCEL_OPEN_ORDERS',
        'FLATTEN_PAPER', 'SHUTDOWN_AFTER_RECONCILE'
    )),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) > 0 AND length(reason) <= 200),
    actor TEXT NOT NULL CHECK (length(btrim(actor)) > 0 AND length(actor) <= 100),
    run_id UUID,
    requested_at TIMESTAMPTZ NOT NULL,
    applied_at TIMESTAMPTZ,
    CHECK (applied_at IS NULL OR applied_at >= requested_at)
);

CREATE TRIGGER control_commands_append_only
BEFORE UPDATE OR DELETE ON public.control_commands
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TABLE public.control_state (
    singleton BOOLEAN PRIMARY KEY CHECK (singleton),
    entries_paused BOOLEAN NOT NULL DEFAULT FALSE,
    paused_reason TEXT CHECK (
        paused_reason IS NULL
        OR (length(btrim(paused_reason)) > 0 AND length(paused_reason) <= 200)
    ),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (
        (entries_paused AND paused_reason IS NOT NULL)
        OR (NOT entries_paused AND paused_reason IS NULL)
    )
);

INSERT INTO public.control_state (singleton, entries_paused, paused_reason)
VALUES (TRUE, FALSE, NULL);

CREATE OR REPLACE FUNCTION public.guard_control_state_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.singleton <> OLD.singleton THEN
        RAISE EXCEPTION 'control state singleton identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    NEW.updated_at := pg_catalog.statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER control_state_guard_write
BEFORE UPDATE ON public.control_state
FOR EACH ROW
EXECUTE FUNCTION public.guard_control_state_write();

REVOKE INSERT, UPDATE, DELETE ON TABLE public.control_commands FROM PUBLIC;
REVOKE INSERT, DELETE ON TABLE public.control_state FROM PUBLIC;

COMMENT ON TABLE public.control_commands IS
    'Append-only operator command audit log.';
COMMENT ON TABLE public.control_state IS
    'Singleton execution pause flag; a pause always carries a reason.';
