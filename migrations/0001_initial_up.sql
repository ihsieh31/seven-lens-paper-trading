-- Seven Lens P1-B initial authoritative-state schema.
-- All timestamps are PostgreSQL TIMESTAMPTZ and are generated from the database clock.

CREATE TABLE schema_metadata (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    schema_contract_version TEXT NOT NULL,
    initialized_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

INSERT INTO schema_metadata (singleton, schema_contract_version)
VALUES (TRUE, '0.1.0');

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    filename TEXT NOT NULL UNIQUE CHECK (length(btrim(filename)) > 0),
    checksum TEXT NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE domain_events (
    event_id UUID PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (length(btrim(event_type)) > 0),
    schema_version TEXT NOT NULL CHECK (length(btrim(schema_version)) > 0),
    aggregate_type TEXT NOT NULL CHECK (length(btrim(aggregate_type)) > 0),
    aggregate_id TEXT NOT NULL CHECK (length(btrim(aggregate_id)) > 0),
    aggregate_sequence BIGINT NOT NULL CHECK (aggregate_sequence >= 1),
    run_id UUID NOT NULL,
    correlation_id UUID NOT NULL,
    causation_id UUID,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    producer_version TEXT NOT NULL CHECK (length(btrim(producer_version)) > 0),
    UNIQUE (aggregate_type, aggregate_id, aggregate_sequence)
);

CREATE OR REPLACE FUNCTION enforce_domain_event_sequence_and_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    expected_sequence BIGINT;
BEGIN
    -- A transaction-scoped advisory lock makes max(sequence) + 1 safe under concurrency.
    -- A hash collision only serializes unrelated aggregates; it cannot violate correctness.
    PERFORM pg_advisory_xact_lock(
        hashtextextended(NEW.aggregate_type || E'\\x1f' || NEW.aggregate_id, 0)
    );

    SELECT COALESCE(MAX(aggregate_sequence), 0) + 1
    INTO expected_sequence
    FROM domain_events
    WHERE aggregate_type = NEW.aggregate_type
      AND aggregate_id = NEW.aggregate_id;

    IF NEW.aggregate_sequence <> expected_sequence THEN
        RAISE EXCEPTION
            'aggregate sequence must be contiguous from 1 for %.%; expected %, received %',
            NEW.aggregate_type,
            NEW.aggregate_id,
            expected_sequence,
            NEW.aggregate_sequence
            USING ERRCODE = '23514';
    END IF;

    -- recorded_at is authoritative database time even if a client supplied a value.
    NEW.recorded_at := statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER domain_events_enforce_sequence
BEFORE INSERT ON domain_events
FOR EACH ROW
EXECUTE FUNCTION enforce_domain_event_sequence_and_timestamp();

CREATE OR REPLACE FUNCTION audit_payload_contains_secret(payload JSONB)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    item RECORD;
    text_value TEXT;
BEGIN
    CASE jsonb_typeof(payload)
        WHEN 'object' THEN
            FOR item IN SELECT key, value FROM jsonb_each(payload) LOOP
                IF lower(item.key) ~
                    '(^|[_-])(api[_-]?key|authorization|credential|password|passwd|secret|token|private[_-]?key|access[_-]?key)([_-]|$)'
                THEN
                    RETURN TRUE;
                END IF;
                IF audit_payload_contains_secret(item.value) THEN
                    RETURN TRUE;
                END IF;
            END LOOP;
        WHEN 'array' THEN
            FOR item IN SELECT value FROM jsonb_array_elements(payload) LOOP
                IF audit_payload_contains_secret(item.value) THEN
                    RETURN TRUE;
                END IF;
            END LOOP;
        WHEN 'string' THEN
            text_value := payload #>> '{}';
            IF text_value ~*
                '(^|[[:space:]])(basic|bearer)[[:space:]]+[^[:space:]]+'
                OR text_value ~*
                '(api[_-]?key|authorization|credential|password|passwd|secret|token|private[_-]?key)[[:space:]]*[:=]'
                OR text_value ~ 'AKIA[0-9A-Z]{16}'
                OR text_value ~ '(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'
                OR text_value ~ '(xox[baprs]-[A-Za-z0-9-]{10,}|sk[-_](live|test)[-_][A-Za-z0-9_-]{8,})'
                OR text_value ~ '(^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}'
                OR text_value ~ '-----BEGIN [A-Z ]*PRIVATE KEY-----'
            THEN
                RETURN TRUE;
            END IF;
        ELSE
            -- JSON null, boolean, and number scalars cannot contain credential text.
            NULL;
    END CASE;
    RETURN FALSE;
END;
$$;

CREATE TABLE audit_events (
    audit_id UUID PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (length(btrim(event_type)) > 0),
    run_id UUID,
    correlation_id UUID NOT NULL,
    causation_id UUID,
    occurred_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    producer_version TEXT NOT NULL CHECK (length(btrim(producer_version)) > 0),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (NOT audit_payload_contains_secret(payload))
);

CREATE OR REPLACE FUNCTION validate_and_stamp_audit_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF audit_payload_contains_secret(NEW.payload) THEN
        RAISE EXCEPTION 'audit payload contains secret-bearing material' USING ERRCODE = '23514';
    END IF;
    NEW.recorded_at := statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER audit_events_validate_and_stamp
BEFORE INSERT ON audit_events
FOR EACH ROW
EXECUTE FUNCTION validate_and_stamp_audit_event();

CREATE OR REPLACE FUNCTION prevent_append_only_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; UPDATE and DELETE are forbidden', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER audit_events_append_only
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW
EXECUTE FUNCTION prevent_append_only_mutation();

CREATE TRIGGER domain_events_append_only
BEFORE UPDATE OR DELETE ON domain_events
FOR EACH ROW
EXECUTE FUNCTION prevent_append_only_mutation();

CREATE TABLE job_instances (
    job_key TEXT PRIMARY KEY CHECK (length(btrim(job_key)) > 0),
    trading_date DATE NOT NULL,
    job_type TEXT NOT NULL CHECK (job_type ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    window_name TEXT NOT NULL CHECK (window_name ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    status TEXT NOT NULL CHECK (status IN ('PLANNED', 'RUNNING', 'COMPLETE', 'FAILED', 'EXPIRED')),
    lease_owner TEXT CHECK (lease_owner ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$'),
    leased_until TIMESTAMPTZ,
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (trading_date, job_type, window_name),
    CHECK (job_key = trading_date::TEXT || '/' || job_type || '/' || window_name),
    CHECK (
        (lease_owner IS NULL AND leased_until IS NULL)
        OR (lease_owner IS NOT NULL AND length(btrim(lease_owner)) > 0 AND leased_until IS NOT NULL)
    )
);

CREATE TABLE job_leases (
    lease_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_key TEXT NOT NULL REFERENCES job_instances(job_key),
    owner TEXT NOT NULL CHECK (owner ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$'),
    fencing_token BIGINT NOT NULL CHECK (fencing_token > 0),
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    last_renewed_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    leased_until TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ,
    release_reason TEXT,
    CHECK (leased_until > acquired_at),
    CHECK ((released_at IS NULL AND release_reason IS NULL) OR released_at IS NOT NULL),
    UNIQUE (job_key, fencing_token)
);

CREATE INDEX job_instances_lease_lookup_idx
ON job_instances (leased_until, job_key);

CREATE UNIQUE INDEX job_leases_one_open_per_job_idx
ON job_leases (job_key)
WHERE released_at IS NULL;

CREATE OR REPLACE FUNCTION guard_job_instance_status_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
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
        asserted_owner := current_setting('seven_lens.lease_owner', TRUE);
        BEGIN
            asserted_token := current_setting('seven_lens.fencing_token', TRUE)::BIGINT;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'job status write requires a valid lease fencing token'
                USING ERRCODE = '55000';
        END;

        IF asserted_owner IS NULL
            OR asserted_token IS NULL
            OR OLD.lease_owner IS NULL
            OR OLD.leased_until IS NULL
            OR OLD.leased_until <= statement_timestamp()
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
        IF current_setting('seven_lens.lease_mutation', TRUE) IS DISTINCT FROM 'authorized' THEN
            RAISE EXCEPTION 'job lease fields may only be changed by lease functions'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER job_instances_guard_status_write
BEFORE UPDATE ON job_instances
FOR EACH ROW
EXECUTE FUNCTION guard_job_instance_status_write();

CREATE OR REPLACE FUNCTION acquire_job_lease(
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
SET search_path = pg_catalog, public
AS $$
DECLARE
    candidate job_instances%ROWTYPE;
    claimed job_instances%ROWTYPE;
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
    PERFORM set_config('seven_lens.lease_mutation', 'authorized', TRUE);

    SELECT job_instances.* INTO candidate
    FROM job_instances
    WHERE job_instances.job_key = p_job_key
    FOR UPDATE;

    IF NOT FOUND
        OR (candidate.leased_until IS NOT NULL
            AND candidate.leased_until > statement_timestamp())
    THEN
        PERFORM set_config('seven_lens.lease_mutation', '', TRUE);
        RETURN;
    END IF;

    UPDATE job_instances
    SET lease_owner = p_owner,
        leased_until = statement_timestamp() + p_lease_for,
        fencing_token = job_instances.fencing_token + 1,
        attempt_count = job_instances.attempt_count + 1
    WHERE job_instances.job_key = candidate.job_key
    RETURNING * INTO claimed;

    IF candidate.lease_owner IS NOT NULL THEN
        UPDATE job_leases
        SET released_at = statement_timestamp(),
            release_reason = 'EXPIRED_TAKEOVER'
        WHERE job_leases.job_key = candidate.job_key
          AND job_leases.owner = candidate.lease_owner
          AND job_leases.fencing_token = candidate.fencing_token
          AND job_leases.released_at IS NULL
        ;
        GET DIAGNOSTICS closed_history_count = ROW_COUNT;
        IF closed_history_count <> 1 THEN
            RAISE EXCEPTION 'expired job lease history is missing or inconsistent'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    INSERT INTO job_leases (job_key, owner, fencing_token, leased_until)
    VALUES (claimed.job_key, claimed.lease_owner, claimed.fencing_token, claimed.leased_until);

    RETURN QUERY
    SELECT claimed.job_key,
           claimed.lease_owner,
           claimed.leased_until,
           claimed.fencing_token,
           claimed.attempt_count,
           statement_timestamp();
    PERFORM set_config('seven_lens.lease_mutation', '', TRUE);
END;
$$;

CREATE OR REPLACE FUNCTION renew_job_lease(
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
SET search_path = pg_catalog, public
AS $$
DECLARE
    renewed job_instances%ROWTYPE;
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
    PERFORM set_config('seven_lens.lease_mutation', 'authorized', TRUE);

    UPDATE job_instances
    SET leased_until = statement_timestamp() + p_lease_for
    WHERE job_instances.job_key = p_job_key
      AND job_instances.lease_owner = p_owner
      AND job_instances.fencing_token = p_fencing_token
      AND job_instances.leased_until > statement_timestamp()
    RETURNING * INTO renewed;

    IF NOT FOUND THEN
        PERFORM set_config('seven_lens.lease_mutation', '', TRUE);
        RETURN;
    END IF;

    UPDATE job_leases
    SET leased_until = renewed.leased_until,
        last_renewed_at = statement_timestamp()
    WHERE job_leases.job_key = renewed.job_key
      AND job_leases.owner = renewed.lease_owner
      AND job_leases.fencing_token = renewed.fencing_token
      AND job_leases.released_at IS NULL;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'current job lease history is missing' USING ERRCODE = '55000';
    END IF;

    RETURN QUERY
    SELECT renewed.job_key,
           renewed.lease_owner,
           renewed.leased_until,
           renewed.fencing_token,
           renewed.attempt_count,
           statement_timestamp();
    PERFORM set_config('seven_lens.lease_mutation', '', TRUE);
END;
$$;

CREATE OR REPLACE FUNCTION release_job_lease(
    p_job_key TEXT,
    p_owner TEXT,
    p_fencing_token BIGINT,
    p_reason TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    released job_instances%ROWTYPE;
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
    PERFORM set_config('seven_lens.lease_mutation', 'authorized', TRUE);

    UPDATE job_instances
    SET lease_owner = NULL,
        leased_until = NULL
    WHERE job_instances.job_key = p_job_key
      AND job_instances.lease_owner = p_owner
      AND job_instances.fencing_token = p_fencing_token
      AND job_instances.leased_until > statement_timestamp()
    RETURNING * INTO released;

    IF NOT FOUND THEN
        PERFORM set_config('seven_lens.lease_mutation', '', TRUE);
        RETURN FALSE;
    END IF;

    UPDATE job_leases
    SET released_at = statement_timestamp(),
        release_reason = p_reason
    WHERE job_leases.job_key = released.job_key
      AND job_leases.owner = p_owner
      AND job_leases.fencing_token = p_fencing_token
      AND job_leases.released_at IS NULL;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'current job lease history is missing' USING ERRCODE = '55000';
    END IF;
    PERFORM set_config('seven_lens.lease_mutation', '', TRUE);
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION transition_job_status(
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
SET search_path = pg_catalog, public
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
    PERFORM set_config('seven_lens.lease_owner', p_owner, TRUE);
    PERFORM set_config('seven_lens.fencing_token', p_fencing_token::TEXT, TRUE);

    RETURN QUERY
    UPDATE job_instances
    SET status = p_status
    WHERE job_instances.job_key = p_job_key
      AND job_instances.lease_owner = p_owner
      AND job_instances.fencing_token = p_fencing_token
      AND job_instances.leased_until > statement_timestamp()
    RETURNING job_instances.job_key,
              job_instances.trading_date,
              job_instances.job_type,
              job_instances.window_name,
              job_instances.status,
              job_instances.lease_owner,
              job_instances.leased_until,
              job_instances.fencing_token,
              job_instances.attempt_count,
              job_instances.created_at,
              job_instances.updated_at;
    PERFORM set_config('seven_lens.lease_owner', '', TRUE);
    PERFORM set_config('seven_lens.fencing_token', '', TRUE);
END;
$$;

COMMENT ON TABLE domain_events IS
    'Append-only ledger. Runtime role must not own this table; grant INSERT only, never UPDATE/DELETE.';
COMMENT ON TABLE audit_events IS
    'Append-only audit ledger. Runtime role must not own this table; grant INSERT only, never UPDATE/DELETE.';
COMMENT ON TABLE job_instances IS
    'Runtime role must not own this table; lease fields are changed only through lease functions.';
COMMENT ON FUNCTION acquire_job_lease(TEXT, TEXT, INTERVAL) IS
    'Atomic DB-clock lease acquisition/takeover. Grant EXECUTE to the runtime role instead of direct lease writes.';
COMMENT ON FUNCTION renew_job_lease(TEXT, TEXT, BIGINT, INTERVAL) IS
    'Atomic DB-clock lease renewal. Grant EXECUTE to the runtime role instead of direct lease writes.';
COMMENT ON FUNCTION release_job_lease(TEXT, TEXT, BIGINT, TEXT) IS
    'Atomic DB-clock lease release. Grant EXECUTE to the runtime role instead of direct lease writes.';
COMMENT ON FUNCTION transition_job_status(TEXT, TEXT, BIGINT, TEXT) IS
    'Fenced status transition. Grant EXECUTE to the runtime role instead of direct job updates.';

REVOKE UPDATE, DELETE ON TABLE domain_events, audit_events FROM PUBLIC;
REVOKE UPDATE (lease_owner, leased_until, fencing_token, attempt_count)
ON TABLE job_instances FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE ON TABLE job_leases FROM PUBLIC;
REVOKE DELETE ON TABLE job_instances FROM PUBLIC;
REVOKE ALL ON FUNCTION acquire_job_lease(TEXT, TEXT, INTERVAL) FROM PUBLIC;
REVOKE ALL ON FUNCTION renew_job_lease(TEXT, TEXT, BIGINT, INTERVAL) FROM PUBLIC;
REVOKE ALL ON FUNCTION release_job_lease(TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION transition_job_status(TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC;
