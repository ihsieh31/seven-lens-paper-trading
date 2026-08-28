-- 0021 down: restore the 0020 P4-B authority behavior.

DROP TRIGGER IF EXISTS corporate_action_confirmed_owner_guard
    ON public.corporate_action_events;
DROP FUNCTION IF EXISTS public.guard_confirmed_corporate_action_event();

ALTER TABLE public.security_quarantine_decisions
    DROP CONSTRAINT IF EXISTS security_quarantine_wire_contract;
ALTER TABLE public.security_quarantine_decisions
    DROP CONSTRAINT IF EXISTS security_quarantine_master_version_contract;
ALTER TABLE public.p4_source_records
    DROP CONSTRAINT IF EXISTS p4_source_records_payload_contract;
ALTER TABLE public.p4_source_records
    DROP CONSTRAINT IF EXISTS p4_source_records_wire_contract;

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

DELETE FROM public.schema_migrations WHERE version = 21;
