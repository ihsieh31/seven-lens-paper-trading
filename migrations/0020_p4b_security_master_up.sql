-- 0020 up: P4-B point-in-time security master and split quarantine authority.
--
-- Durable home of the P4-A typed source record log, the event-sourced security
-- identity master, forward/reverse split event lineages, and the unified entry
-- quarantine decisions.  Every history relation is append-only and guarded by
-- the shared prevent_append_only_mutation() trigger.  The two head relations
-- are projections maintained exclusively by the narrow SECURITY DEFINER
-- functions below, mirroring the memory_current_pointer pattern: they carry no
-- guard trigger and the runtime role receives no direct write grant on them.

-- Legacy preflight: fail closed if any P4-B object name already exists.
DO $$
DECLARE
    legacy_table TEXT;
BEGIN
    FOREACH legacy_table IN ARRAY ARRAY[
        'p4_source_records',
        'security_identities',
        'security_identity_sources',
        'security_identity_heads',
        'corporate_action_events',
        'corporate_action_event_sources',
        'corporate_action_event_head',
        'security_quarantine_decisions',
        'security_quarantine_decision_sources'
    ]::text[]
    LOOP
        IF pg_catalog.to_regclass('public.' || legacy_table) IS NOT NULL THEN
            RAISE EXCEPTION 'legacy object blocks the P4-B security master: %', legacy_table
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    IF pg_catalog.to_regprocedure(
            'public.append_p4_source_record(text,text,text,jsonb)'
        ) IS NOT NULL
        OR pg_catalog.to_regprocedure('public.append_security_identity(text,jsonb)') IS NOT NULL
        OR pg_catalog.to_regprocedure(
            'public.append_corporate_action_event(text,text,jsonb)'
        ) IS NOT NULL
        OR pg_catalog.to_regprocedure('public.record_quarantine_decision(text,jsonb)') IS NOT NULL
    THEN
        RAISE EXCEPTION 'legacy function blocks the P4-B security master'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

-- btree_gist lives in pg_catalog, not public: its ~190 support functions would
-- otherwise pollute the exact public-schema function inventory enforced by the
-- role provisioning guards.  pg_catalog is always on every search_path, so the
-- GiST exclusion constraint below resolves its operator classes unchanged.
CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA pg_catalog;

-- The durable P4-A source record log: one append-only row per accepted record
-- version.  Supersession chains keep every withdrawn version readable while
-- UNIQUE (record_id, record_hash) pins idempotent same-hash appends.
CREATE TABLE public.p4_source_records (
    append_sequence BIGINT GENERATED ALWAYS AS IDENTITY,
    record_id TEXT NOT NULL CHECK (record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'),
    record_hash TEXT NOT NULL CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    family TEXT NOT NULL CHECK (family IN (
        'ALPACA_ASSETS', 'ALPACA_HISTORICAL_BARS', 'ALPACA_IEX_QUOTES',
        'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR', 'ISSUER_IR', 'EXCHANGE_OFFICIAL',
        'FRED_ALFRED', 'TREASURY', 'BLS', 'BEA', 'EIA', 'TAVILY', 'GDELT',
        'YFINANCE'
    )),
    retrieved_at TIMESTAMPTZ NOT NULL,
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (record_id, record_hash),
    UNIQUE (record_id, record_hash, family)
);

CREATE INDEX p4_source_records_head_idx
    ON public.p4_source_records (record_id, append_sequence DESC);

-- Append-only point-in-time identity observations; the wire JSONB is the sole
-- content authority and the extracted columns exist for constraints and reads.
CREATE TABLE public.security_identities (
    identity_hash TEXT PRIMARY KEY CHECK (identity_hash ~ '^[0-9a-f]{64}$'),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    exchange TEXT NOT NULL CHECK (exchange IN ('AMEX', 'ARCA', 'BATS', 'NASDAQ', 'NYSE')),
    asset_class TEXT NOT NULL CHECK (asset_class = 'us_equity'),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ NULL,
    available_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
    schema_version TEXT NOT NULL CHECK (schema_version <> ''),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX security_identities_security_idx
    ON public.security_identities (security_id, appended_at);
CREATE INDEX security_identities_symbol_idx
    ON public.security_identities (symbol, appended_at);

CREATE TABLE public.security_identity_sources (
    identity_hash TEXT NOT NULL REFERENCES public.security_identities(identity_hash),
    record_id TEXT NOT NULL CHECK (record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'),
    record_hash TEXT NOT NULL CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    family TEXT NOT NULL CHECK (family IN (
        'ALPACA_ASSETS', 'ALPACA_HISTORICAL_BARS', 'ALPACA_IEX_QUOTES',
        'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR', 'ISSUER_IR', 'EXCHANGE_OFFICIAL',
        'FRED_ALFRED', 'TREASURY', 'BLS', 'BEA', 'EIA', 'TAVILY', 'GDELT',
        'YFINANCE'
    )),
    PRIMARY KEY (identity_hash, record_id),
    FOREIGN KEY (record_id, record_hash, family)
        REFERENCES public.p4_source_records(record_id, record_hash, family)
);

-- Mutable projection: one current head per security and exact validity
-- interval.  Maintained only inside append_security_identity(); the exclusion
-- constraint is the durable backstop against overlapping intervals.
CREATE TABLE public.security_identity_heads (
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ NULL,
    identity_hash TEXT NOT NULL UNIQUE REFERENCES public.security_identities(identity_hash),
    available_at TIMESTAMPTZ NOT NULL,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE NULLS NOT DISTINCT (security_id, valid_from, valid_to),
    EXCLUDE USING gist (
        security_id WITH =,
        pg_catalog.tstzrange(valid_from, valid_to, '[)') WITH &&
    )
);

-- Append-only forward/reverse split event lineages.  Each row repeats every
-- immutable event fact; previous_record_hash is the compare-and-swap pointer.
CREATE TABLE public.corporate_action_events (
    record_hash TEXT PRIMARY KEY CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    previous_record_hash TEXT NULL
        REFERENCES public.corporate_action_events(record_hash),
    event_id TEXT NOT NULL CHECK (event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    security_identity_hash TEXT NOT NULL CHECK (security_identity_hash ~ '^[0-9a-f]{64}$')
        REFERENCES public.security_identities(identity_hash),
    action_type TEXT NOT NULL CHECK (action_type IN ('forward_split', 'reverse_split')),
    ratio_numerator BIGINT NOT NULL CHECK (ratio_numerator > 0),
    ratio_denominator BIGINT NOT NULL CHECK (ratio_denominator > 0),
    declared_at TIMESTAMPTZ NOT NULL,
    ex_date DATE NOT NULL,
    effective_date DATE NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'detected', 'entry_blocked', 'confirmed', 'review_required',
        'effective_pending_reconciliation'
    )),
    schema_version TEXT NOT NULL CHECK (schema_version <> ''),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (effective_date >= ex_date),
    CHECK (available_at >= declared_at)
);

CREATE INDEX corporate_action_events_event_idx
    ON public.corporate_action_events (event_id);

CREATE TABLE public.corporate_action_event_sources (
    record_hash TEXT NOT NULL REFERENCES public.corporate_action_events(record_hash),
    source_record_id TEXT NOT NULL
        CHECK (source_record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'),
    source_record_hash TEXT NOT NULL CHECK (source_record_hash ~ '^[0-9a-f]{64}$'),
    family TEXT NOT NULL CHECK (family IN (
        'ALPACA_ASSETS', 'ALPACA_HISTORICAL_BARS', 'ALPACA_IEX_QUOTES',
        'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR', 'ISSUER_IR', 'EXCHANGE_OFFICIAL',
        'FRED_ALFRED', 'TREASURY', 'BLS', 'BEA', 'EIA', 'TAVILY', 'GDELT',
        'YFINANCE'
    )),
    PRIMARY KEY (record_hash, source_record_id),
    FOREIGN KEY (source_record_id, source_record_hash, family)
        REFERENCES public.p4_source_records(record_id, record_hash, family)
);

-- Mutable projection: one head per event lineage, advanced only by the closed
-- transition table inside append_corporate_action_event().
CREATE TABLE public.corporate_action_event_head (
    event_id TEXT PRIMARY KEY CHECK (event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'),
    record_hash TEXT NOT NULL UNIQUE
        REFERENCES public.corporate_action_events(record_hash),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    state TEXT NOT NULL CHECK (state IN (
        'detected', 'entry_blocked', 'confirmed', 'review_required',
        'effective_pending_reconciliation'
    )),
    available_at TIMESTAMPTZ NOT NULL
);

-- Append-only unified entry-quarantine decisions; the purpose marker of the
-- calling seam is deliberately never part of the stored decision content.
CREATE TABLE public.security_quarantine_decisions (
    decision_hash TEXT PRIMARY KEY CHECK (decision_hash ~ '^[0-9a-f]{64}$'),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    symbol_as_of TEXT NOT NULL CHECK (symbol_as_of ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    master_version TEXT NOT NULL CHECK (length(master_version) BETWEEN 1 AND 128),
    decision_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ELIGIBLE', 'ENTRY_BLOCKED', 'REVIEW_REQUIRED')),
    reasons TEXT[] NOT NULL CHECK (reasons <@ ARRAY[
        'UNKNOWN_SECURITY', 'AMBIGUOUS_IDENTITY', 'SYMBOL_AS_OF_MISMATCH',
        'IDENTITY_INTERVAL_CONFLICT', 'SOURCE_NOT_YET_AVAILABLE',
        'STALE_SECURITY_MASTER', 'SPLIT_DETECTED', 'FORMAL_CONFIRMATION_MISSING',
        'SPLIT_RATIO_CONFLICT', 'SPLIT_DATE_CONFLICT', 'SPLIT_IDENTITY_CONFLICT',
        'SOURCE_WITHDRAWN_OR_CORRECTED', 'UNSUPPORTED_CORPORATE_ACTION',
        'EFFECTIVE_OR_LATE_EVENT_REVIEW', 'SPLIT_TYPE_CONFLICT'
    ]::text[]),
    event_ids TEXT[] NOT NULL,
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE INDEX security_quarantine_decisions_security_idx
    ON public.security_quarantine_decisions (security_id, decision_at DESC);

CREATE TABLE public.security_quarantine_decision_sources (
    decision_hash TEXT NOT NULL
        REFERENCES public.security_quarantine_decisions(decision_hash),
    record_id TEXT NOT NULL CHECK (record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'),
    record_hash TEXT NOT NULL CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    family TEXT NOT NULL CHECK (family IN (
        'ALPACA_ASSETS', 'ALPACA_HISTORICAL_BARS', 'ALPACA_IEX_QUOTES',
        'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR', 'ISSUER_IR', 'EXCHANGE_OFFICIAL',
        'FRED_ALFRED', 'TREASURY', 'BLS', 'BEA', 'EIA', 'TAVILY', 'GDELT',
        'YFINANCE'
    )),
    PRIMARY KEY (decision_hash, record_id),
    FOREIGN KEY (record_id, record_hash, family)
        REFERENCES public.p4_source_records(record_id, record_hash, family)
);

-- Append one P4-A source record version.  Same-hash appends are idempotent;
-- a different hash for the same provider identity must explicitly supersede
-- the current head content hash or the append fails closed.
CREATE OR REPLACE FUNCTION public.append_p4_source_record(
    p_record_id TEXT,
    p_record_hash TEXT,
    p_content_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing RECORD;
    v_current RECORD;
BEGIN
    IF p_record_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$' THEN
        RAISE EXCEPTION 'record id must be a canonical record identifier'
            USING ERRCODE = '22023';
    END IF;
    IF p_record_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'record hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF p_content_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'content hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 19
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS key_name
            WHERE key_name NOT IN (
                'record_id', 'family', 'endpoint_id', 'schema_version', 'content_hash',
                'retrieved_at', 'role', 'coverage', 'rights', 'producer_version',
                'payload', 'material_claim', 'observation_at', 'published_at',
                'available_at', 'effective_at', 'vintage', 'supersedes_content_hash',
                'coverage_warning'
            )
        )
    THEN
        RAISE EXCEPTION 'source record wire keys do not match the P4-A contract'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'producer_version' IS DISTINCT FROM 'p4a.adapters.v1' THEN
        RAISE EXCEPTION 'source record wire carries an unsupported producer version'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'record_id' IS DISTINCT FROM p_record_id THEN
        RAISE EXCEPTION 'wire form does not match the supplied record identity'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'content_hash' IS DISTINCT FROM p_content_hash THEN
        RAISE EXCEPTION 'wire form does not match the supplied content hash'
            USING ERRCODE = '22023';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4.source-record.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_record_hash
    THEN
        RAISE EXCEPTION 'source record hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('p4b.source-record:' || p_record_id)
    );

    SELECT r.record_hash, r.content_hash, r.wire
      INTO v_existing
      FROM public.p4_source_records AS r
     WHERE r.record_id = p_record_id
       AND r.record_hash = p_record_hash;
    IF FOUND THEN
        IF v_existing.wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'source record hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    SELECT r.record_hash, r.content_hash, r.wire
      INTO v_current
      FROM public.p4_source_records AS r
     WHERE r.record_id = p_record_id
     ORDER BY r.append_sequence DESC
     LIMIT 1;
    IF FOUND THEN
        IF v_current.record_hash = p_record_hash THEN
            IF v_current.wire IS DISTINCT FROM p_wire THEN
                RAISE EXCEPTION 'source record hash collision carries different wire'
                    USING ERRCODE = '23514';
            END IF;
            RETURN 'IDEMPOTENT_DUPLICATE';
        END IF;
        IF p_wire->>'supersedes_content_hash' IS DISTINCT FROM v_current.content_hash THEN
            RAISE EXCEPTION
                'same provider identity with different content requires '
                'explicit supersession'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    INSERT INTO public.p4_source_records (
        record_id, record_hash, content_hash, family, retrieved_at, wire
    ) VALUES (
        p_record_id,
        p_record_hash,
        p_content_hash,
        p_wire->>'family',
        (p_wire->>'retrieved_at')::timestamptz,
        p_wire
    );
    RETURN 'APPENDED';
END;
$$;

-- Append one point-in-time identity observation and maintain the interval
-- head.  A later available_at corrects the head; an earlier one is kept as
-- history only; an equal available_at with different content is unorderable
-- and fails closed.  Overlapping distinct intervals fail closed.
CREATE OR REPLACE FUNCTION public.append_security_identity(
    p_identity_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_security_id TEXT;
    v_valid_from TIMESTAMPTZ;
    v_valid_to TIMESTAMPTZ;
    v_available_at TIMESTAMPTZ;
    v_head RECORD;
    v_ref JSONB;
    v_existing_wire JSONB;
BEGIN
    IF p_identity_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 14
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS key_name
            WHERE key_name NOT IN (
                'security_id', 'symbol', 'exchange', 'asset_class', 'cik', 'cusip', 'isin',
                'valid_from', 'valid_to', 'available_at', 'status', 'source_refs',
                'schema_version', 'producer_version'
            )
        )
    THEN
        RAISE EXCEPTION 'identity wire keys do not match the P4-B contract'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'producer_version' IS DISTINCT FROM 'p4b.securities.v1' THEN
        RAISE EXCEPTION 'identity wire carries an unsupported producer version'
            USING ERRCODE = '22023';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4b.security-identity.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_identity_hash
    THEN
        RAISE EXCEPTION 'identity hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    v_security_id := p_wire->>'security_id';
    IF v_security_id IS NULL OR v_security_id !~ '^[0-9a-f][0-9a-f-]{7,63}$' THEN
        RAISE EXCEPTION 'wire form carries no canonical security id'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'valid_from' IS NULL THEN
        RAISE EXCEPTION 'wire form carries no valid_from'
            USING ERRCODE = '22023';
    END IF;
    v_valid_from := (p_wire->>'valid_from')::timestamptz;
    IF p_wire->>'valid_to' IS NULL THEN
        v_valid_to := NULL;
    ELSE
        v_valid_to := (p_wire->>'valid_to')::timestamptz;
        IF v_valid_to <= v_valid_from THEN
            RAISE EXCEPTION 'validity interval must start strictly before it ends'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF p_wire->>'available_at' IS NULL THEN
        RAISE EXCEPTION 'wire form carries no available_at'
            USING ERRCODE = '22023';
    END IF;
    v_available_at := (p_wire->>'available_at')::timestamptz;
    IF jsonb_typeof(p_wire->'source_refs') IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_wire->'source_refs') < 1
        OR jsonb_array_length(p_wire->'source_refs') > 16
    THEN
        RAISE EXCEPTION 'wire form must carry between 1 and 16 source refs'
            USING ERRCODE = '22023';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('p4b.security-identity:' || v_security_id)
    );

    SELECT i.wire
      INTO v_existing_wire
      FROM public.security_identities AS i
     WHERE i.identity_hash = p_identity_hash;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'identity hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    INSERT INTO public.security_identities (
        identity_hash, security_id, symbol, exchange, asset_class,
        valid_from, valid_to, available_at, status, schema_version, wire
    ) VALUES (
        p_identity_hash,
        v_security_id,
        p_wire->>'symbol',
        p_wire->>'exchange',
        p_wire->>'asset_class',
        v_valid_from,
        v_valid_to,
        v_available_at,
        p_wire->>'status',
        p_wire->>'schema_version',
        p_wire
    );

    FOR v_ref IN SELECT * FROM jsonb_array_elements(p_wire->'source_refs')
    LOOP
        INSERT INTO public.security_identity_sources (
            identity_hash, record_id, record_hash, family
        ) VALUES (
            p_identity_hash,
            v_ref->>'record_id',
            v_ref->>'record_hash',
            v_ref->>'family'
        );
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM public.security_identity_heads
        WHERE security_id = v_security_id
          AND pg_catalog.tstzrange(valid_from, valid_to, '[)')
              && pg_catalog.tstzrange(v_valid_from, v_valid_to, '[)')
          AND (valid_from IS DISTINCT FROM v_valid_from
               OR valid_to IS DISTINCT FROM v_valid_to)
    ) THEN
        RAISE EXCEPTION
            'identity interval overlaps an existing head for the same security'
            USING ERRCODE = '23514';
    END IF;

    SELECT h.identity_hash, h.available_at
      INTO v_head
      FROM public.security_identity_heads AS h
     WHERE h.security_id = v_security_id
       AND h.valid_from = v_valid_from
       AND h.valid_to IS NOT DISTINCT FROM v_valid_to;
    IF NOT FOUND THEN
        INSERT INTO public.security_identity_heads (
            security_id, valid_from, valid_to, identity_hash, available_at
        ) VALUES (
            v_security_id, v_valid_from, v_valid_to, p_identity_hash, v_available_at
        );
    ELSIF v_head.identity_hash <> p_identity_hash
        AND v_available_at > v_head.available_at
    THEN
        UPDATE public.security_identity_heads
           SET identity_hash = p_identity_hash,
               available_at = v_available_at
         WHERE security_id = v_security_id
           AND valid_from = v_valid_from
           AND valid_to IS NOT DISTINCT FROM v_valid_to;
    ELSIF v_head.identity_hash <> p_identity_hash
        AND v_available_at = v_head.available_at
    THEN
        RAISE EXCEPTION 'unorderable identity correction at the same available time'
            USING ERRCODE = '23514';
    END IF;
    -- A stale append (earlier available_at) keeps history and leaves the head.

    RETURN 'APPENDED';
END;
$$;

-- Append one corporate-action lineage row.  The DETECTED root starts a
-- lineage; every later row must name the current head record hash (CAS),
-- repeat every immutable event fact, follow the closed transition table, and
-- never regress the decision time.  A moved head fails closed as a typed
-- concurrency loss.
CREATE OR REPLACE FUNCTION public.append_corporate_action_event(
    p_record_hash TEXT,
    p_previous_record_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_event_id TEXT;
    v_security_id TEXT;
    v_identity_hash TEXT;
    v_action_type TEXT;
    v_ratio JSONB;
    v_ratio_n BIGINT;
    v_ratio_d BIGINT;
    v_declared_at TIMESTAMPTZ;
    v_ex_date DATE;
    v_effective_date DATE;
    v_available_at TIMESTAMPTZ;
    v_state TEXT;
    v_head RECORD;
    v_prev RECORD;
    v_existing_event_id TEXT;
    v_existing_previous_record_hash TEXT;
    v_existing_wire JSONB;
    v_identity_security_id TEXT;
    v_ref JSONB;
    v_a BIGINT;
    v_b BIGINT;
    v_t BIGINT;
BEGIN
    IF p_record_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'record hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF p_previous_record_hash IS NOT NULL
        AND p_previous_record_hash !~ '^[0-9a-f]{64}$'
    THEN
        RAISE EXCEPTION 'previous record hash must be a SHA-256 digest or null'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 13
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS key_name
            WHERE key_name NOT IN (
                'event_id', 'security_id', 'security_identity_hash', 'action_type', 'ratio',
                'declared_at', 'ex_date', 'effective_date', 'available_at', 'state',
                'source_refs', 'schema_version', 'producer_version'
            )
        )
    THEN
        RAISE EXCEPTION 'corporate-action wire keys do not match the P4-B contract'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'producer_version' IS DISTINCT FROM 'p4b.corporate-actions.v1' THEN
        RAISE EXCEPTION 'corporate-action wire carries an unsupported producer version'
            USING ERRCODE = '22023';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4b.corporate-action.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_record_hash
    THEN
        RAISE EXCEPTION 'corporate-action hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    v_event_id := p_wire->>'event_id';
    IF v_event_id IS NULL OR v_event_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$' THEN
        RAISE EXCEPTION 'wire form carries no canonical event id'
            USING ERRCODE = '22023';
    END IF;
    v_security_id := p_wire->>'security_id';
    IF v_security_id IS NULL OR v_security_id !~ '^[0-9a-f][0-9a-f-]{7,63}$' THEN
        RAISE EXCEPTION 'wire form carries no canonical security id'
            USING ERRCODE = '22023';
    END IF;
    v_identity_hash := p_wire->>'security_identity_hash';
    IF v_identity_hash IS NULL OR v_identity_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'wire form carries no canonical security identity hash'
            USING ERRCODE = '22023';
    END IF;
    SELECT i.security_id
      INTO v_identity_security_id
      FROM public.security_identities AS i
     WHERE i.identity_hash = v_identity_hash;
    IF NOT FOUND OR v_identity_security_id <> v_security_id THEN
        RAISE EXCEPTION 'corporate-action identity does not match its security'
            USING ERRCODE = '23514';
    END IF;
    v_action_type := p_wire->>'action_type';
    IF v_action_type IS NULL
        OR v_action_type NOT IN ('forward_split', 'reverse_split')
    THEN
        RAISE EXCEPTION 'wire form carries no supported corporate action type'
            USING ERRCODE = '22023';
    END IF;

    v_ratio := p_wire->'ratio';
    IF jsonb_typeof(v_ratio) IS DISTINCT FROM 'object'
        OR v_ratio->>'numerator' !~ '^[0-9]+$'
        OR v_ratio->>'denominator' !~ '^[0-9]+$'
    THEN
        RAISE EXCEPTION 'split ratio must be an exact positive rational'
            USING ERRCODE = '22023';
    END IF;
    v_ratio_n := (v_ratio->>'numerator')::bigint;
    v_ratio_d := (v_ratio->>'denominator')::bigint;
    IF v_ratio_n <= 0 OR v_ratio_d <= 0 THEN
        RAISE EXCEPTION 'split ratio must be strictly positive'
            USING ERRCODE = '23514';
    END IF;
    v_a := v_ratio_n;
    v_b := v_ratio_d;
    WHILE v_b <> 0 LOOP
        v_t := v_b;
        v_b := v_a % v_b;
        v_a := v_t;
    END LOOP;
    IF v_a <> 1 THEN
        RAISE EXCEPTION 'split ratio must be normalized'
            USING ERRCODE = '23514';
    END IF;

    IF p_wire->>'declared_at' IS NULL THEN
        RAISE EXCEPTION 'wire form carries no declared_at'
            USING ERRCODE = '22023';
    END IF;
    v_declared_at := (p_wire->>'declared_at')::timestamptz;
    IF p_wire->>'ex_date' IS NULL THEN
        RAISE EXCEPTION 'wire form carries no ex_date'
            USING ERRCODE = '22023';
    END IF;
    v_ex_date := (p_wire->>'ex_date')::date;
    IF p_wire->>'effective_date' IS NULL THEN
        RAISE EXCEPTION 'wire form carries no effective_date'
            USING ERRCODE = '22023';
    END IF;
    v_effective_date := (p_wire->>'effective_date')::date;
    IF p_wire->>'available_at' IS NULL THEN
        RAISE EXCEPTION 'wire form carries no available_at'
            USING ERRCODE = '22023';
    END IF;
    v_available_at := (p_wire->>'available_at')::timestamptz;
    v_state := p_wire->>'state';
    IF v_state IS NULL OR v_state NOT IN (
        'detected', 'entry_blocked', 'confirmed', 'review_required',
        'effective_pending_reconciliation'
    ) THEN
        RAISE EXCEPTION 'wire form carries no closed corporate-action state'
            USING ERRCODE = '22023';
    END IF;
    IF v_ex_date < (v_declared_at AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'ex date cannot precede the declaration date'
            USING ERRCODE = '23514';
    END IF;
    IF v_effective_date < v_ex_date THEN
        RAISE EXCEPTION 'effective date cannot precede the ex date'
            USING ERRCODE = '23514';
    END IF;
    IF v_available_at < v_declared_at THEN
        RAISE EXCEPTION 'available_at cannot precede the declaration time'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(p_wire->'source_refs') IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_wire->'source_refs') < 1
        OR jsonb_array_length(p_wire->'source_refs') > 16
    THEN
        RAISE EXCEPTION 'wire form must carry between 1 and 16 source refs'
            USING ERRCODE = '22023';
    END IF;
    IF v_state = 'confirmed'
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS source_ref
            WHERE source_ref->>'family' IN ('SEC_EDGAR', 'ISSUER_IR', 'EXCHANGE_OFFICIAL')
        )
    THEN
        RAISE EXCEPTION 'confirmed corporate-action row requires an official source'
            USING ERRCODE = '23514';
    END IF;

    -- Serialize source correction against a confirmation that is about to
    -- consume the source lineage.  Sorting the locks makes reordered source
    -- arrays use one deterministic lock order.
    FOR v_ref IN
        SELECT source_ref
        FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS source(source_ref)
        ORDER BY source_ref->>'record_id', source_ref->>'family', source_ref->>'record_hash'
    LOOP
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtext('p4b.source-record:' || (v_ref->>'record_id'))
        );
    END LOOP;

    IF v_state = 'confirmed'
        AND EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS source(source_ref)
            WHERE NOT EXISTS (
                SELECT 1
                FROM (
                    SELECT r.record_hash, r.family, r.wire
                    FROM public.p4_source_records AS r
                    WHERE r.record_id = source.source_ref->>'record_id'
                    ORDER BY r.append_sequence DESC
                    LIMIT 1
                ) AS current_source
                WHERE current_source.record_hash = source.source_ref->>'record_hash'
                  AND current_source.family = source.source_ref->>'family'
                  AND COALESCE(
                        (current_source.wire->>'available_at')::timestamptz,
                        (current_source.wire->>'retrieved_at')::timestamptz
                  ) <= v_available_at
            )
        )
    THEN
        RAISE EXCEPTION
            'confirmed corporate-action row requires current source versions available by decision time'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('p4b.corporate-action-event:' || v_event_id)
    );

    SELECT e.event_id, e.previous_record_hash, e.wire
      INTO v_existing_event_id, v_existing_previous_record_hash, v_existing_wire
      FROM public.corporate_action_events AS e
     WHERE e.record_hash = p_record_hash;
    IF FOUND THEN
        IF v_existing_event_id = v_event_id THEN
            IF v_existing_wire IS DISTINCT FROM p_wire THEN
                RAISE EXCEPTION 'corporate-action hash collision carries different wire'
                    USING ERRCODE = '23514';
            END IF;
            IF v_existing_previous_record_hash IS DISTINCT FROM p_previous_record_hash THEN
                RAISE EXCEPTION
                    'corporate-action record hash was reused with a different predecessor'
                    USING ERRCODE = '23514';
            END IF;
            RETURN 'IDEMPOTENT_DUPLICATE';
        END IF;
        RAISE EXCEPTION 'corporate-action record hash collision across events'
            USING ERRCODE = '23514';
    END IF;

    IF v_state = 'detected' THEN
        IF p_previous_record_hash IS NOT NULL THEN
            RAISE EXCEPTION 'detected root cannot reference a previous head'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.corporate_action_event_head
            WHERE event_id = v_event_id
        ) THEN
            RAISE EXCEPTION 'corporate-action event lineage already started'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF p_previous_record_hash IS NULL THEN
            RAISE EXCEPTION 'transition requires the previous head record hash'
                USING ERRCODE = '23514';
        END IF;
        SELECT h.record_hash, h.state
          INTO v_head
          FROM public.corporate_action_event_head AS h
         WHERE h.event_id = v_event_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'corporate-action event lineage not found'
                USING ERRCODE = '23514';
        END IF;
        IF v_head.record_hash <> p_previous_record_hash THEN
            RAISE EXCEPTION 'corporate-action head moved; concurrent transition lost'
                USING ERRCODE = '40001';
        END IF;
        SELECT e.security_id, e.security_identity_hash, e.action_type,
               e.ratio_numerator, e.ratio_denominator, e.declared_at,
               e.ex_date, e.effective_date, e.available_at
          INTO v_prev
          FROM public.corporate_action_events AS e
         WHERE e.record_hash = p_previous_record_hash;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'previous corporate-action head is not in the event log'
                USING ERRCODE = '23514';
        END IF;
        IF NOT (
            (v_head.state = 'detected'
                AND v_state IN ('entry_blocked', 'review_required'))
            OR (v_head.state = 'entry_blocked'
                AND v_state IN ('confirmed', 'review_required'))
            OR (v_head.state = 'confirmed'
                AND v_state IN ('review_required', 'effective_pending_reconciliation'))
        ) THEN
            RAISE EXCEPTION 'illegal corporate-action transition: % -> %',
                v_head.state, v_state
                USING ERRCODE = '23514';
        END IF;
        IF v_prev.security_id <> v_security_id
            OR v_prev.security_identity_hash <> v_identity_hash
            OR v_prev.action_type <> v_action_type
            OR v_prev.ratio_numerator <> v_ratio_n
            OR v_prev.ratio_denominator <> v_ratio_d
            OR v_prev.declared_at <> v_declared_at
            OR v_prev.ex_date <> v_ex_date
            OR v_prev.effective_date <> v_effective_date
        THEN
            RAISE EXCEPTION 'transition cannot change immutable event facts'
                USING ERRCODE = '23514';
        END IF;
        IF v_available_at < v_prev.available_at THEN
            RAISE EXCEPTION 'transition decision time cannot precede the previous head'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM public.corporate_action_event_sources AS previous_source
            WHERE previous_source.record_hash = p_previous_record_hash
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS source(source_ref)
                  WHERE source.source_ref->>'record_id' = previous_source.source_record_id
                    AND source.source_ref->>'record_hash' = previous_source.source_record_hash
                    AND source.source_ref->>'family' = previous_source.family
              )
        )
        THEN
            RAISE EXCEPTION 'corporate-action transition cannot drop source lineage'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    INSERT INTO public.corporate_action_events (
        record_hash, previous_record_hash, event_id, security_id,
        security_identity_hash, action_type, ratio_numerator, ratio_denominator,
        declared_at, ex_date, effective_date, available_at, state,
        schema_version, wire
    ) VALUES (
        p_record_hash,
        p_previous_record_hash,
        v_event_id,
        v_security_id,
        v_identity_hash,
        v_action_type,
        v_ratio_n,
        v_ratio_d,
        v_declared_at,
        v_ex_date,
        v_effective_date,
        v_available_at,
        v_state,
        p_wire->>'schema_version',
        p_wire
    );

    IF v_state = 'detected' THEN
        INSERT INTO public.corporate_action_event_head (
            event_id, record_hash, security_id, state, available_at
        ) VALUES (
            v_event_id, p_record_hash, v_security_id, v_state, v_available_at
        );
    ELSE
        UPDATE public.corporate_action_event_head
           SET record_hash = p_record_hash,
               state = v_state,
               available_at = v_available_at
         WHERE event_id = v_event_id;
    END IF;

    FOR v_ref IN SELECT * FROM jsonb_array_elements(p_wire->'source_refs')
    LOOP
        INSERT INTO public.corporate_action_event_sources (
            record_hash, source_record_id, source_record_hash, family
        ) VALUES (
            p_record_hash,
            v_ref->>'record_id',
            v_ref->>'record_hash',
            v_ref->>'family'
        );
    END LOOP;

    RETURN 'APPENDED';
END;
$$;

-- Append one unified entry-quarantine decision.  Decisions are content
-- addressed: an identical decision hash is idempotent, and the purpose marker
-- of the calling seam never enters the stored content.
CREATE OR REPLACE FUNCTION public.record_quarantine_decision(
    p_decision_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_security_id TEXT;
    v_ref JSONB;
    v_existing_wire JSONB;
BEGIN
    IF p_decision_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'decision hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 9
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS key_name
            WHERE key_name NOT IN (
                'security_id', 'symbol_as_of', 'master_version', 'decision_at', 'outcome',
                'reasons', 'event_ids', 'source_refs', 'producer_version'
            )
        )
    THEN
        RAISE EXCEPTION 'quarantine decision wire keys do not match the P4-B contract'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'producer_version' IS DISTINCT FROM 'p4b.quarantine.v1' THEN
        RAISE EXCEPTION 'quarantine decision wire carries an unsupported producer version'
            USING ERRCODE = '22023';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4b.quarantine-decision.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_decision_hash
    THEN
        RAISE EXCEPTION 'quarantine decision hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;
    v_security_id := p_wire->>'security_id';
    IF v_security_id IS NULL OR v_security_id !~ '^[0-9a-f][0-9a-f-]{7,63}$' THEN
        RAISE EXCEPTION 'wire form carries no canonical security id'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'symbol_as_of' IS NULL
        OR p_wire->>'symbol_as_of' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
    THEN
        RAISE EXCEPTION 'wire form carries no canonical symbol'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'master_version' IS NULL
        OR length(p_wire->>'master_version') > 128
    THEN
        RAISE EXCEPTION 'wire form carries no bounded master version'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'decision_at' IS NULL THEN
        RAISE EXCEPTION 'wire form carries no decision_at'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'outcome' NOT IN ('ELIGIBLE', 'ENTRY_BLOCKED', 'REVIEW_REQUIRED') THEN
        RAISE EXCEPTION 'wire form carries no closed quarantine outcome'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire->'reasons') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'event_ids') IS DISTINCT FROM 'array'
    THEN
        RAISE EXCEPTION 'wire form must carry reason and event id arrays'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire->'source_refs') IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_wire->'source_refs') > 256
    THEN
        RAISE EXCEPTION 'wire form must carry at most 256 source refs'
            USING ERRCODE = '22023';
    END IF;

    SELECT d.wire
      INTO v_existing_wire
      FROM public.security_quarantine_decisions AS d
     WHERE d.decision_hash = p_decision_hash;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'quarantine decision hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    INSERT INTO public.security_quarantine_decisions (
        decision_hash, security_id, symbol_as_of, master_version, decision_at,
        outcome, reasons, event_ids, wire
    ) VALUES (
        p_decision_hash,
        v_security_id,
        p_wire->>'symbol_as_of',
        p_wire->>'master_version',
        (p_wire->>'decision_at')::timestamptz,
        p_wire->>'outcome',
        ARRAY(SELECT jsonb_array_elements_text(p_wire->'reasons')),
        ARRAY(SELECT jsonb_array_elements_text(p_wire->'event_ids')),
        p_wire
    );
    FOR v_ref IN SELECT * FROM jsonb_array_elements(p_wire->'source_refs')
    LOOP
        INSERT INTO public.security_quarantine_decision_sources (
            decision_hash, record_id, record_hash, family
        ) VALUES (
            p_decision_hash,
            v_ref->>'record_id',
            v_ref->>'record_hash',
            v_ref->>'family'
        );
    END LOOP;

    RETURN 'APPENDED';
END;
$$;

REVOKE ALL ON FUNCTION public.append_p4_source_record(TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_security_identity(TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_corporate_action_event(TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_quarantine_decision(TEXT, JSONB) FROM PUBLIC;

-- Append-only guards on every immutable P4-B relation.  The two head
-- relations intentionally carry no guard trigger: like memory_current_pointer
-- they are writable only through the SECURITY DEFINER functions above.
CREATE TRIGGER p4_source_records_guard_write
BEFORE UPDATE OR DELETE ON public.p4_source_records
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER p4_source_records_guard_truncate
BEFORE TRUNCATE ON public.p4_source_records
FOR EACH STATEMENT
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER security_identities_guard_write
BEFORE UPDATE OR DELETE ON public.security_identities
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER security_identities_guard_truncate
BEFORE TRUNCATE ON public.security_identities
FOR EACH STATEMENT
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER security_identity_sources_guard_write
BEFORE UPDATE OR DELETE ON public.security_identity_sources
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER security_identity_sources_guard_truncate
BEFORE TRUNCATE ON public.security_identity_sources
FOR EACH STATEMENT
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER corporate_action_events_guard_write
BEFORE UPDATE OR DELETE ON public.corporate_action_events
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER corporate_action_events_guard_truncate
BEFORE TRUNCATE ON public.corporate_action_events
FOR EACH STATEMENT
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER corporate_action_event_sources_guard_write
BEFORE UPDATE OR DELETE ON public.corporate_action_event_sources
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER corporate_action_event_sources_guard_truncate
BEFORE TRUNCATE ON public.corporate_action_event_sources
FOR EACH STATEMENT
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER security_quarantine_decisions_guard_write
BEFORE UPDATE OR DELETE ON public.security_quarantine_decisions
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER security_quarantine_decisions_guard_truncate
BEFORE TRUNCATE ON public.security_quarantine_decisions
FOR EACH STATEMENT
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER security_quarantine_decision_sources_guard_write
BEFORE UPDATE OR DELETE ON public.security_quarantine_decision_sources
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER security_quarantine_decision_sources_guard_truncate
BEFORE TRUNCATE ON public.security_quarantine_decision_sources
FOR EACH STATEMENT
EXECUTE FUNCTION public.prevent_append_only_mutation();

COMMENT ON TABLE public.p4_source_records IS
    'Durable append-only P4-A typed source record log with supersession chains.';
COMMENT ON TABLE public.security_identities IS
    'Append-only point-in-time security identity observations.';
COMMENT ON TABLE public.security_identity_sources IS
    'Hash-bound source lineage for each identity observation.';
COMMENT ON TABLE public.security_identity_heads IS
    'Current identity head per security and exact validity interval.';
COMMENT ON TABLE public.corporate_action_events IS
    'Append-only forward/reverse split event lineage rows.';
COMMENT ON TABLE public.corporate_action_event_sources IS
    'Hash-bound source lineage for each split event row.';
COMMENT ON TABLE public.corporate_action_event_head IS
    'Current state head per split event lineage.';
COMMENT ON TABLE public.security_quarantine_decisions IS
    'Append-only unified entry-quarantine decisions for all three seams.';
COMMENT ON TABLE public.security_quarantine_decision_sources IS
    'Hash-bound source lineage for each quarantine decision.';
COMMENT ON FUNCTION public.append_p4_source_record(TEXT, TEXT, TEXT, JSONB) IS
    'Append one P4-A source record version with idempotent same-hash lineage.';
COMMENT ON FUNCTION public.append_security_identity(TEXT, JSONB) IS
    'Append one identity observation and maintain its interval head.';
COMMENT ON FUNCTION public.append_corporate_action_event(TEXT, TEXT, JSONB) IS
    'Append one split lineage row under the closed transition table and CAS.';
COMMENT ON FUNCTION public.record_quarantine_decision(TEXT, JSONB) IS
    'Append one content-addressed entry-quarantine decision.';
