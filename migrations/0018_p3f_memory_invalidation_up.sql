-- 0018: make deterministic memory invalidation an append-only PostgreSQL transition.

CREATE OR REPLACE FUNCTION public.invalidate_memory_artifact(
    p_artifact_id TEXT, p_content_hash TEXT, p_reason_code TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_artifact public.memory_artifacts%ROWTYPE;
    v_existing_invalid public.memory_artifact_state_events%ROWTYPE;
BEGIN
    IF p_reason_code IS NULL OR p_reason_code NOT IN (
        'SCHEMA', 'BOUNDS', 'LINEAGE', 'FUTURE_LEAKAGE',
        'PROMPT_INJECTION', 'FACT_CLOSURE', 'INTEGRITY'
    ) THEN
        RAISE EXCEPTION 'memory invalidation reason is invalid' USING ERRCODE = '23514';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_artifact_id::text, 0)
    );
    SELECT * INTO v_artifact
    FROM public.memory_artifacts
    WHERE artifact_id = p_artifact_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'memory artifact does not exist' USING ERRCODE = '23503';
    END IF;
    IF v_artifact.content_hash IS DISTINCT FROM v_artifact.cas_hash
       OR v_artifact.content_hash IS DISTINCT FROM p_content_hash
       OR pg_catalog.octet_length(v_artifact.content_bytes) <> v_artifact.byte_count
       OR pg_catalog.encode(public.digest(v_artifact.content_bytes, 'sha256'), 'hex')
          IS DISTINCT FROM v_artifact.content_hash THEN
        RAISE EXCEPTION 'memory artifact integrity metadata mismatch' USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.memory_artifact_state_events
        WHERE artifact_id = p_artifact_id AND state = 'CURRENT'
    ) THEN
        RAISE EXCEPTION 'current memory artifact cannot be invalidated' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO v_existing_invalid
    FROM public.memory_artifact_state_events
    WHERE artifact_id = p_artifact_id AND state = 'INVALID';
    IF FOUND THEN
        IF v_existing_invalid.reason_code IS DISTINCT FROM p_reason_code THEN
            RAISE EXCEPTION 'memory invalidation identity collision' USING ERRCODE = '23505';
        END IF;
        RETURN FALSE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.memory_artifact_state_events
        WHERE artifact_id = p_artifact_id AND state IN ('CANDIDATE', 'VALIDATED')
    ) THEN
        RAISE EXCEPTION 'only a candidate or validated memory artifact can be invalidated'
            USING ERRCODE = '23514';
    END IF;

    INSERT INTO public.memory_artifact_state_events(
        state_event_id, artifact_id, state, reason_code
    ) VALUES (
        public.p3d_derive_run_id(
            'seven-lens.p3f.memory-state.v1', p_artifact_id::text, 'INVALID'
        ),
        p_artifact_id, 'INVALID', p_reason_code
    );
    RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.invalidate_memory_artifact(TEXT, TEXT, TEXT) FROM PUBLIC;
