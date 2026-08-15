-- P1 foundation hardening.  This migration intentionally leaves 0001 immutable.

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

DO $$
BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END;
$$;

CREATE OR REPLACE FUNCTION public.guard_job_instance_status_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    asserted_owner TEXT;
    asserted_token BIGINT;
BEGIN
    IF NEW.job_key <> OLD.job_key
        OR NEW.trading_date <> OLD.trading_date
        OR NEW.job_type <> OLD.job_type
        OR NEW.window_name <> OLD.window_name
    THEN
        RAISE EXCEPTION 'job identity fields are immutable' USING ERRCODE = '55000';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        asserted_owner := pg_catalog.current_setting('seven_lens.lease_owner', TRUE);
        BEGIN
            asserted_token := pg_catalog.current_setting(
                'seven_lens.fencing_token', TRUE
            )::BIGINT;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'job status write requires a valid lease fencing token'
                USING ERRCODE = '55000';
        END;

        IF asserted_owner IS NULL
            OR asserted_token IS NULL
            OR OLD.lease_owner IS NULL
            OR OLD.leased_until IS NULL
            OR OLD.leased_until <= pg_catalog.statement_timestamp()
            OR OLD.lease_owner <> asserted_owner
            OR OLD.fencing_token <> asserted_token
        THEN
            RAISE EXCEPTION 'job status write requires the current unexpired owner and fencing token'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    IF NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
        OR NEW.leased_until IS DISTINCT FROM OLD.leased_until
        OR NEW.fencing_token IS DISTINCT FROM OLD.fencing_token
        OR NEW.attempt_count IS DISTINCT FROM OLD.attempt_count
    THEN
        IF pg_catalog.current_setting(
            'seven_lens.lease_mutation', TRUE
        ) IS DISTINCT FROM 'authorized' THEN
            RAISE EXCEPTION 'job lease fields may only be changed by lease functions'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    NEW.updated_at := pg_catalog.statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.acquire_job_lease(
    p_job_key TEXT,
    p_owner TEXT,
    p_lease_for INTERVAL
)
RETURNS TABLE (
    job_key TEXT,
    lease_owner TEXT,
    leased_until TIMESTAMPTZ,
    fencing_token BIGINT,
    attempt_count INTEGER,
    database_time TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    candidate public.job_instances%ROWTYPE;
    claimed public.job_instances%ROWTYPE;
    closed_history_count INTEGER;
BEGIN
    IF p_lease_for IS NULL
        OR p_lease_for <= INTERVAL '0 seconds'
        OR p_lease_for > INTERVAL '1 day'
    THEN
        RAISE EXCEPTION 'lease duration must be greater than zero and at most one day'
            USING ERRCODE = '22023';
    END IF;
    IF p_owner IS NULL
        OR p_owner !~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$'
    THEN
        RAISE EXCEPTION 'lease owner must use the canonical bounded owner format'
            USING ERRCODE = '22023';
    END IF;
    IF p_job_key IS NULL OR length(btrim(p_job_key)) = 0 THEN
        RAISE EXCEPTION 'job key must be non-empty text' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_catalog.set_config('seven_lens.lease_mutation', 'authorized', TRUE);

    SELECT ji.* INTO candidate
    FROM public.job_instances AS ji
    WHERE ji.job_key = p_job_key
    FOR UPDATE;

    IF NOT FOUND
        OR (candidate.leased_until IS NOT NULL
            AND candidate.leased_until > pg_catalog.statement_timestamp())
    THEN
        PERFORM pg_catalog.set_config('seven_lens.lease_mutation', '', TRUE);
        RETURN;
    END IF;

    UPDATE public.job_instances AS ji
    SET lease_owner = p_owner,
        leased_until = pg_catalog.statement_timestamp() + p_lease_for,
        fencing_token = ji.fencing_token + 1,
        attempt_count = ji.attempt_count + 1
    WHERE ji.job_key = candidate.job_key
    RETURNING ji.* INTO claimed;

    IF candidate.lease_owner IS NOT NULL THEN
        UPDATE public.job_leases AS jl
        SET released_at = pg_catalog.statement_timestamp(),
            release_reason = 'EXPIRED_TAKEOVER'
        WHERE jl.job_key = candidate.job_key
          AND jl.owner = candidate.lease_owner
          AND jl.fencing_token = candidate.fencing_token
          AND jl.released_at IS NULL;
        GET DIAGNOSTICS closed_history_count = ROW_COUNT;
        IF closed_history_count <> 1 THEN
            RAISE EXCEPTION 'expired job lease history is missing or inconsistent'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    INSERT INTO public.job_leases (job_key, owner, fencing_token, leased_until)
    VALUES (claimed.job_key, claimed.lease_owner, claimed.fencing_token, claimed.leased_until);

    RETURN QUERY
    SELECT claimed.job_key,
           claimed.lease_owner,
           claimed.leased_until,
           claimed.fencing_token,
           claimed.attempt_count,
           pg_catalog.statement_timestamp();
    PERFORM pg_catalog.set_config('seven_lens.lease_mutation', '', TRUE);
END;
$$;

CREATE OR REPLACE FUNCTION public.renew_job_lease(
    p_job_key TEXT,
    p_owner TEXT,
    p_fencing_token BIGINT,
    p_lease_for INTERVAL
)
RETURNS TABLE (
    job_key TEXT,
    lease_owner TEXT,
    leased_until TIMESTAMPTZ,
    fencing_token BIGINT,
    attempt_count INTEGER,
    database_time TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    renewed public.job_instances%ROWTYPE;
BEGIN
    IF p_lease_for IS NULL
        OR p_lease_for <= INTERVAL '0 seconds'
        OR p_lease_for > INTERVAL '1 day'
    THEN
        RAISE EXCEPTION 'lease duration must be greater than zero and at most one day'
            USING ERRCODE = '22023';
    END IF;
    IF p_owner IS NULL
        OR p_owner !~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$'
    THEN
        RAISE EXCEPTION 'lease owner must use the canonical bounded owner format'
            USING ERRCODE = '22023';
    END IF;
    IF p_fencing_token IS NULL OR p_fencing_token < 1 THEN
        RAISE EXCEPTION 'fencing token must be positive' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_catalog.set_config('seven_lens.lease_mutation', 'authorized', TRUE);

    UPDATE public.job_instances AS ji
    SET leased_until = pg_catalog.statement_timestamp() + p_lease_for
    WHERE ji.job_key = p_job_key
      AND ji.lease_owner = p_owner
      AND ji.fencing_token = p_fencing_token
      AND ji.leased_until > pg_catalog.statement_timestamp()
    RETURNING ji.* INTO renewed;

    IF NOT FOUND THEN
        PERFORM pg_catalog.set_config('seven_lens.lease_mutation', '', TRUE);
        RETURN;
    END IF;

    UPDATE public.job_leases AS jl
    SET leased_until = renewed.leased_until,
        last_renewed_at = pg_catalog.statement_timestamp()
    WHERE jl.job_key = renewed.job_key
      AND jl.owner = renewed.lease_owner
      AND jl.fencing_token = renewed.fencing_token
      AND jl.released_at IS NULL;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'current job lease history is missing' USING ERRCODE = '55000';
    END IF;

    RETURN QUERY
    SELECT renewed.job_key,
           renewed.lease_owner,
           renewed.leased_until,
           renewed.fencing_token,
           renewed.attempt_count,
           pg_catalog.statement_timestamp();
    PERFORM pg_catalog.set_config('seven_lens.lease_mutation', '', TRUE);
END;
$$;

CREATE OR REPLACE FUNCTION public.release_job_lease(
    p_job_key TEXT,
    p_owner TEXT,
    p_fencing_token BIGINT,
    p_reason TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    released public.job_instances%ROWTYPE;
BEGIN
    IF p_owner IS NULL
        OR p_owner !~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$'
    THEN
        RAISE EXCEPTION 'lease owner must use the canonical bounded owner format'
            USING ERRCODE = '22023';
    END IF;
    IF p_fencing_token IS NULL OR p_fencing_token < 1 THEN
        RAISE EXCEPTION 'fencing token must be positive' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_catalog.set_config('seven_lens.lease_mutation', 'authorized', TRUE);

    UPDATE public.job_instances AS ji
    SET lease_owner = NULL,
        leased_until = NULL
    WHERE ji.job_key = p_job_key
      AND ji.lease_owner = p_owner
      AND ji.fencing_token = p_fencing_token
      AND ji.leased_until > pg_catalog.statement_timestamp()
    RETURNING ji.* INTO released;

    IF NOT FOUND THEN
        PERFORM pg_catalog.set_config('seven_lens.lease_mutation', '', TRUE);
        RETURN FALSE;
    END IF;

    UPDATE public.job_leases AS jl
    SET released_at = pg_catalog.statement_timestamp(),
        release_reason = p_reason
    WHERE jl.job_key = released.job_key
      AND jl.owner = p_owner
      AND jl.fencing_token = p_fencing_token
      AND jl.released_at IS NULL;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'current job lease history is missing' USING ERRCODE = '55000';
    END IF;
    PERFORM pg_catalog.set_config('seven_lens.lease_mutation', '', TRUE);
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.transition_job_status(
    p_job_key TEXT,
    p_owner TEXT,
    p_fencing_token BIGINT,
    p_status TEXT
)
RETURNS TABLE (
    job_key TEXT,
    trading_date DATE,
    job_type TEXT,
    window_name TEXT,
    status TEXT,
    lease_owner TEXT,
    leased_until TIMESTAMPTZ,
    fencing_token BIGINT,
    attempt_count INTEGER,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF p_owner IS NULL
        OR p_owner !~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$'
    THEN
        RAISE EXCEPTION 'lease owner must use the canonical bounded owner format'
            USING ERRCODE = '22023';
    END IF;
    IF p_fencing_token IS NULL OR p_fencing_token < 1 THEN
        RAISE EXCEPTION 'fencing token must be positive' USING ERRCODE = '22023';
    END IF;
    IF p_status IS NULL
        OR p_status NOT IN ('PLANNED', 'RUNNING', 'COMPLETE', 'FAILED', 'EXPIRED')
    THEN
        RAISE EXCEPTION 'job status is invalid' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_catalog.set_config('seven_lens.lease_owner', p_owner, TRUE);
    PERFORM pg_catalog.set_config('seven_lens.fencing_token', p_fencing_token::TEXT, TRUE);

    RETURN QUERY
    UPDATE public.job_instances AS ji
    SET status = p_status
    WHERE ji.job_key = p_job_key
      AND ji.lease_owner = p_owner
      AND ji.fencing_token = p_fencing_token
      AND ji.leased_until > pg_catalog.statement_timestamp()
    RETURNING ji.job_key,
              ji.trading_date,
              ji.job_type,
              ji.window_name,
              ji.status,
              ji.lease_owner,
              ji.leased_until,
              ji.fencing_token,
              ji.attempt_count,
              ji.created_at,
              ji.updated_at;
    PERFORM pg_catalog.set_config('seven_lens.lease_owner', '', TRUE);
    PERFORM pg_catalog.set_config('seven_lens.fencing_token', '', TRUE);
END;
$$;

CREATE OR REPLACE FUNCTION public.domain_event_payload_is_valid(
    p_event_type TEXT,
    p_payload JSONB
)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT p_event_type = 'job.created'
       AND p_payload = '{"attempt_count": 0, "status": "PLANNED"}'::JSONB;
$$;

CREATE OR REPLACE FUNCTION public.audit_event_payload_is_valid(
    p_event_type TEXT,
    p_payload JSONB
)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT p_event_type = 'job.status_changed'
       AND pg_catalog.jsonb_typeof(p_payload) = 'object'
       AND p_payload - 'target_status' - 'reason_code' = '{}'::JSONB
       AND p_payload->>'target_status' IN (
           'PLANNED', 'RUNNING', 'COMPLETE', 'FAILED', 'EXPIRED'
       )
       AND p_payload->>'reason_code' IN (
           'SCHEDULED', 'RECOVERY', 'FAILURE', 'EXPIRY'
       );
$$;

ALTER TABLE public.domain_events
ADD CONSTRAINT domain_events_typed_payload_check
CHECK (public.domain_event_payload_is_valid(event_type, payload)) NOT VALID;

ALTER TABLE public.audit_events
ADD CONSTRAINT audit_events_typed_payload_check
CHECK (public.audit_event_payload_is_valid(event_type, payload)) NOT VALID;

ALTER TABLE public.domain_events VALIDATE CONSTRAINT domain_events_typed_payload_check;
ALTER TABLE public.audit_events VALIDATE CONSTRAINT audit_events_typed_payload_check;

REVOKE ALL ON FUNCTION public.guard_job_instance_status_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.acquire_job_lease(TEXT, TEXT, INTERVAL) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.renew_job_lease(TEXT, TEXT, BIGINT, INTERVAL) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_job_lease(TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.transition_job_status(TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.domain_event_payload_is_valid(TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.audit_event_payload_is_valid(TEXT, JSONB) FROM PUBLIC;

COMMENT ON FUNCTION public.domain_event_payload_is_valid(TEXT, JSONB) IS
    'Closed P1 authoritative domain-event payload registry; raw evidence belongs outside the ledger.';
COMMENT ON FUNCTION public.audit_event_payload_is_valid(TEXT, JSONB) IS
    'Closed P1 audit payload registry; regex secret detection remains defense in depth only.';
