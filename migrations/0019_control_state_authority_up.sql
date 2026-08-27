-- 0019: expose only narrow database authorities for protected control state.

CREATE OR REPLACE FUNCTION public.lock_control_state_for_submission()
RETURNS TABLE (
    entries_paused BOOLEAN,
    paused_reason TEXT,
    updated_at TIMESTAMPTZ,
    flatten_generation BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    SELECT state.entries_paused,
           state.paused_reason,
           state.updated_at,
           state.flatten_generation
    FROM public.control_state AS state
    WHERE state.singleton
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'control state row is missing' USING ERRCODE = '55000';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.pause_entries(p_reason TEXT)
RETURNS TABLE (
    entries_paused BOOLEAN,
    paused_reason TEXT,
    updated_at TIMESTAMPTZ,
    flatten_generation BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_entries_paused BOOLEAN;
    v_paused_reason TEXT;
    v_updated_at TIMESTAMPTZ;
    v_flatten_generation BIGINT;
BEGIN
    IF p_reason IS NULL
       OR pg_catalog.length(pg_catalog.btrim(p_reason)) = 0
       OR pg_catalog.length(p_reason) > 200 THEN
        RAISE EXCEPTION 'control pause reason is invalid' USING ERRCODE = '22023';
    END IF;

    UPDATE public.control_state AS state
    SET entries_paused = TRUE,
        paused_reason = p_reason
    WHERE state.singleton
    RETURNING state.entries_paused,
              state.paused_reason,
              state.updated_at,
              state.flatten_generation
    INTO v_entries_paused, v_paused_reason, v_updated_at, v_flatten_generation;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'control state row is missing' USING ERRCODE = '55000';
    END IF;

    RETURN QUERY
    SELECT v_entries_paused, v_paused_reason, v_updated_at, v_flatten_generation;
END;
$$;

CREATE OR REPLACE FUNCTION public.resume_entries()
RETURNS TABLE (
    entries_paused BOOLEAN,
    paused_reason TEXT,
    updated_at TIMESTAMPTZ,
    flatten_generation BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_latest_status TEXT;
    v_latest_scope TEXT;
    v_entries_paused BOOLEAN;
    v_paused_reason TEXT;
    v_updated_at TIMESTAMPTZ;
    v_flatten_generation BIGINT;
BEGIN
    PERFORM 1
    FROM public.control_state AS state
    WHERE state.singleton
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'control state row is missing' USING ERRCODE = '55000';
    END IF;

    SELECT reconciliation.status, reconciliation.scope
    INTO v_latest_status, v_latest_scope
    FROM public.reconciliation_runs AS reconciliation
    ORDER BY reconciliation.recorded_at DESC, reconciliation.run_id DESC
    LIMIT 1;

    IF v_latest_status IS DISTINCT FROM 'CLEAN'
       OR v_latest_scope IS DISTINCT FROM 'FULL' THEN
        RAISE EXCEPTION 'entries cannot resume without a latest FULL CLEAN reconciliation'
            USING ERRCODE = '55000';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.order_intents AS intent
        WHERE intent.status IN ('UNKNOWN', 'REVIEW_REQUIRED')
    ) THEN
        RAISE EXCEPTION 'entries cannot resume while unresolved order intents remain'
            USING ERRCODE = '55000';
    END IF;

    UPDATE public.control_state AS state
    SET entries_paused = FALSE,
        paused_reason = NULL
    WHERE state.singleton
    RETURNING state.entries_paused,
              state.paused_reason,
              state.updated_at,
              state.flatten_generation
    INTO v_entries_paused, v_paused_reason, v_updated_at, v_flatten_generation;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'control state row is missing' USING ERRCODE = '55000';
    END IF;

    RETURN QUERY
    SELECT v_entries_paused, v_paused_reason, v_updated_at, v_flatten_generation;
END;
$$;

CREATE OR REPLACE FUNCTION public.bump_flatten_generation()
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_flatten_generation BIGINT;
BEGIN
    UPDATE public.control_state AS state
    SET flatten_generation = state.flatten_generation + 1
    WHERE state.singleton
    RETURNING state.flatten_generation INTO v_flatten_generation;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'control state row is missing' USING ERRCODE = '55000';
    END IF;

    RETURN v_flatten_generation;
END;
$$;

REVOKE ALL ON FUNCTION public.lock_control_state_for_submission() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.pause_entries(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.resume_entries() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.bump_flatten_generation() FROM PUBLIC;

COMMENT ON FUNCTION public.lock_control_state_for_submission() IS
    'Narrow authority to lock and read the control singleton for submission serialization.';
COMMENT ON FUNCTION public.pause_entries(TEXT) IS
    'Narrow authority to set the execution pause flag with a bounded reason.';
COMMENT ON FUNCTION public.resume_entries() IS
    'Narrow authority to resume only after a latest FULL CLEAN reconciliation with no unresolved intents.';
COMMENT ON FUNCTION public.bump_flatten_generation() IS
    'Narrow authority to advance the protected flatten-generation counter.';
