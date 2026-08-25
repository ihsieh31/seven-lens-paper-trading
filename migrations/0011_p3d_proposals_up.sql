-- 0011: P3-D research bundles, risk debates, proposal authority and feedback lineage.

ALTER TABLE public.analysis_runs
    ADD CONSTRAINT analysis_runs_run_input_unique UNIQUE (run_id, input_id);

CREATE TABLE public.research_bundles (
    bundle_id UUID PRIMARY KEY,
    parent_input_id UUID NOT NULL UNIQUE,
    bundle_hash TEXT NOT NULL UNIQUE CHECK (bundle_hash ~ '^[0-9a-f]{64}$'),
    as_of TIMESTAMPTZ NOT NULL,
    analysis_window TEXT NOT NULL
        CHECK (analysis_window IN ('PRIMARY', 'SECONDARY', 'EMERGENCY')),
    deadline TIMESTAMPTZ NOT NULL,
    universe_hash TEXT NOT NULL CHECK (universe_hash ~ '^[0-9a-f]{64}$'),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    payload_hash TEXT NOT NULL UNIQUE CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 262144),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (deadline > as_of)
);

CREATE TABLE public.research_bundle_items (
    bundle_id UUID NOT NULL REFERENCES public.research_bundles(bundle_id),
    item_ordinal INTEGER NOT NULL CHECK (item_ordinal BETWEEN 1 AND 27),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    analysis_run_id UUID NOT NULL UNIQUE,
    analysis_input_id UUID NOT NULL UNIQUE,
    packet_hash TEXT NOT NULL CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    trader_plan_id UUID NOT NULL UNIQUE,
    trader_plan_hash TEXT NOT NULL CHECK (trader_plan_hash ~ '^[0-9a-f]{64}$'),
    item_payload JSONB NOT NULL CHECK (jsonb_typeof(item_payload) = 'object'),
    PRIMARY KEY (bundle_id, symbol),
    UNIQUE (bundle_id, item_ordinal),
    FOREIGN KEY (analysis_run_id, analysis_input_id)
        REFERENCES public.analysis_runs(run_id, input_id)
);

CREATE TABLE public.risk_rejection_feedback (
    feedback_id UUID PRIMARY KEY,
    rejected_proposal_id UUID NOT NULL,
    feedback_hash TEXT NOT NULL UNIQUE CHECK (feedback_hash ~ '^[0-9a-f]{64}$'),
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 65536),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.proposal_contexts (
    context_id UUID PRIMARY KEY,
    bundle_id UUID NOT NULL REFERENCES public.research_bundles(bundle_id),
    attempt INTEGER NOT NULL CHECK (attempt IN (1, 2)),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    previous_context_id UUID REFERENCES public.proposal_contexts(context_id),
    superseded_proposal_id UUID,
    superseded_proposal_hash TEXT CHECK (
        superseded_proposal_hash IS NULL OR superseded_proposal_hash ~ '^[0-9a-f]{64}$'
    ),
    feedback_id UUID REFERENCES public.risk_rejection_feedback(feedback_id),
    context_hash TEXT NOT NULL CHECK (context_hash ~ '^[0-9a-f]{64}$'),
    payload_hash TEXT NOT NULL UNIQUE CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 262144),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (
        (attempt = 1 AND previous_context_id IS NULL
                   AND superseded_proposal_id IS NULL
                   AND superseded_proposal_hash IS NULL AND feedback_id IS NULL)
        OR (attempt = 2 AND previous_context_id IS NOT NULL
                      AND superseded_proposal_id IS NOT NULL
                      AND superseded_proposal_hash IS NOT NULL AND feedback_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX proposal_contexts_one_attempt_one
    ON public.proposal_contexts (bundle_id) WHERE attempt = 1;

CREATE UNIQUE INDEX proposal_contexts_one_attempt_two
    ON public.proposal_contexts (bundle_id) WHERE attempt = 2;

CREATE TABLE public.proposal_runs (
    run_id UUID PRIMARY KEY,
    context_id UUID NOT NULL UNIQUE REFERENCES public.proposal_contexts(context_id),
    bundle_id UUID NOT NULL REFERENCES public.research_bundles(bundle_id),
    bundle_hash TEXT NOT NULL CHECK (bundle_hash ~ '^[0-9a-f]{64}$'),
    current_stage TEXT NOT NULL DEFAULT 'PLANNED' CHECK (current_stage IN (
        'PLANNED', 'RISK_DEBATE', 'PROPOSAL', 'COMPLETE', 'INVALID', 'EXPIRED'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.risk_debates (
    debate_id UUID PRIMARY KEY,
    context_id UUID NOT NULL REFERENCES public.proposal_contexts(context_id),
    bundle_id UUID NOT NULL REFERENCES public.research_bundles(bundle_id),
    debate_hash TEXT NOT NULL UNIQUE CHECK (debate_hash ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.portfolio_proposals (
    proposal_id UUID PRIMARY KEY,
    context_id UUID NOT NULL UNIQUE REFERENCES public.proposal_contexts(context_id),
    bundle_id UUID NOT NULL REFERENCES public.research_bundles(bundle_id),
    attempt INTEGER NOT NULL CHECK (attempt IN (1, 2)),
    superseded_proposal_id UUID UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('VALID', 'INVALID', 'ABSTAIN')),
    proposal_hash TEXT NOT NULL UNIQUE CHECK (proposal_hash ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK ((attempt = 1) = (superseded_proposal_id IS NULL)),
    FOREIGN KEY (superseded_proposal_id)
        REFERENCES public.portfolio_proposals(proposal_id)
);

CREATE UNIQUE INDEX portfolio_proposals_one_attempt_one
    ON public.portfolio_proposals (bundle_id) WHERE attempt = 1;

ALTER TABLE public.proposal_contexts
    ADD CONSTRAINT proposal_contexts_superseded_proposal_fkey
    FOREIGN KEY (superseded_proposal_id)
    REFERENCES public.portfolio_proposals(proposal_id);

ALTER TABLE public.risk_rejection_feedback
    ADD CONSTRAINT risk_rejection_feedback_proposal_fkey
    FOREIGN KEY (rejected_proposal_id)
    REFERENCES public.portfolio_proposals(proposal_id);

CREATE TABLE public.proposal_stage_results (
    run_id UUID NOT NULL REFERENCES public.proposal_runs(run_id),
    stage TEXT NOT NULL CHECK (stage IN (
        'RISK_DEBATE', 'PROPOSAL', 'COMPLETE', 'INVALID', 'EXPIRED'
    )),
    result_hash TEXT NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 262144),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt BETWEEN 1 AND 8),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (run_id, stage),
    UNIQUE (run_id, stage, result_hash)
);

CREATE OR REPLACE FUNCTION public.guard_proposal_run_write()
RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'proposal run mutation is not legal' USING ERRCODE = '55000';
    END IF;
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.context_id IS DISTINCT FROM OLD.context_id
       OR NEW.bundle_id IS DISTINCT FROM OLD.bundle_id
       OR NEW.bundle_hash IS DISTINCT FROM OLD.bundle_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.updated_at < OLD.updated_at
       OR (OLD.current_stage, NEW.current_stage) NOT IN (
           ('PLANNED', 'RISK_DEBATE'), ('RISK_DEBATE', 'PROPOSAL'),
           ('PROPOSAL', 'COMPLETE'),
           ('PLANNED', 'INVALID'), ('RISK_DEBATE', 'INVALID'), ('PROPOSAL', 'INVALID'),
           ('PLANNED', 'EXPIRED'), ('RISK_DEBATE', 'EXPIRED'), ('PROPOSAL', 'EXPIRED')
       ) THEN
        RAISE EXCEPTION 'proposal run mutation is not legal' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.guard_proposal_stage_result_write()
RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'proposal stage result mutation is not legal'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.stage IS DISTINCT FROM OLD.stage
       OR NEW.result_hash IS DISTINCT FROM OLD.result_hash
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at
       OR NEW.attempt IS DISTINCT FROM OLD.attempt + 1 THEN
        RAISE EXCEPTION 'proposal stage result mutation is not legal'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER research_bundles_guard_write
BEFORE UPDATE OR DELETE ON public.research_bundles
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER research_bundle_items_guard_write
BEFORE UPDATE OR DELETE ON public.research_bundle_items
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER risk_rejection_feedback_guard_write
BEFORE UPDATE OR DELETE ON public.risk_rejection_feedback
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER proposal_contexts_guard_write
BEFORE UPDATE OR DELETE ON public.proposal_contexts
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER proposal_runs_guard_write
BEFORE UPDATE OR DELETE ON public.proposal_runs
FOR EACH ROW EXECUTE FUNCTION public.guard_proposal_run_write();

CREATE TRIGGER risk_debates_guard_write
BEFORE UPDATE OR DELETE ON public.risk_debates
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER portfolio_proposals_guard_write
BEFORE UPDATE OR DELETE ON public.portfolio_proposals
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER proposal_stage_results_guard_write
BEFORE UPDATE OR DELETE ON public.proposal_stage_results
FOR EACH ROW EXECUTE FUNCTION public.guard_proposal_stage_result_write();

CREATE OR REPLACE FUNCTION public.p3d_canonical_json(p_value JSON)
RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_type TEXT;
    v_total BIGINT;
    v_distinct BIGINT;
    v_result TEXT;
BEGIN
    IF p_value IS NULL THEN
        RAISE EXCEPTION 'P3-D JSON value is missing' USING ERRCODE = '23514';
    END IF;
    v_type := json_typeof(p_value);
    IF v_type = 'object' THEN
        SELECT count(*), count(DISTINCT key)
          INTO v_total, v_distinct
          FROM json_each(p_value);
        IF v_total <> v_distinct THEN
            RAISE EXCEPTION 'P3-D JSON contains duplicate object keys'
                USING ERRCODE = '23514';
        END IF;
        SELECT '{' || COALESCE(
                   string_agg(
                       to_json(key)::text || ':' || public.p3d_canonical_json(value),
                       ',' ORDER BY key COLLATE "C"
                   ),
                   ''
               ) || '}'
          INTO v_result
          FROM json_each(p_value);
        RETURN v_result;
    ELSIF v_type = 'array' THEN
        SELECT '[' || COALESCE(
                   string_agg(public.p3d_canonical_json(value), ',' ORDER BY ordinality),
                   ''
               ) || ']'
          INTO v_result
          FROM json_array_elements(p_value) WITH ORDINALITY;
        RETURN v_result;
    ELSIF v_type IN ('string', 'number', 'boolean', 'null') THEN
        RETURN p_value::jsonb::text;
    END IF;
    RAISE EXCEPTION 'P3-D JSON type is invalid' USING ERRCODE = '23514';
END;
$$;

CREATE OR REPLACE FUNCTION public.p3d_derive_run_id(p_domain TEXT, VARIADIC p_parts TEXT[])
RETURNS UUID
LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_material BYTEA;
    v_part TEXT;
    v_bytes BYTEA;
    v_hex TEXT;
BEGIN
    IF p_domain IS NULL OR p_parts IS NULL OR cardinality(p_parts) NOT BETWEEN 1 AND 8
       OR octet_length(p_domain) NOT BETWEEN 1 AND 96 THEN
        RAISE EXCEPTION 'P3-D identity material is invalid' USING ERRCODE = '23514';
    END IF;
    v_material := convert_to(p_domain, 'UTF8');
    FOREACH v_part IN ARRAY p_parts LOOP
        IF v_part IS NULL OR octet_length(v_part) > 512 THEN
            RAISE EXCEPTION 'P3-D identity material is invalid' USING ERRCODE = '23514';
        END IF;
        v_material := v_material || decode('00', 'hex') || convert_to(v_part, 'UTF8');
    END LOOP;
    v_bytes := substring(public.digest(v_material, 'sha256') FROM 1 FOR 16);
    v_bytes := set_byte(v_bytes, 6, (get_byte(v_bytes, 6) & 15) | 64);
    v_bytes := set_byte(v_bytes, 8, (get_byte(v_bytes, 8) & 63) | 128);
    v_hex := encode(v_bytes, 'hex');
    RETURN (
        substring(v_hex FROM 1 FOR 8) || '-' ||
        substring(v_hex FROM 9 FOR 4) || '-' ||
        substring(v_hex FROM 13 FOR 4) || '-' ||
        substring(v_hex FROM 17 FOR 4) || '-' ||
        substring(v_hex FROM 21 FOR 12)
    )::uuid;
END;
$$;

CREATE OR REPLACE FUNCTION public.p3d_text_is_safe(p_value TEXT)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    SELECT p_value IS NOT NULL
       AND position('account_id' IN lower(p_value)) = 0
       AND position('broker_order_id' IN lower(p_value)) = 0
       AND position('raw_broker_payload' IN lower(p_value)) = 0
       AND position('authorization:' IN lower(p_value)) = 0
       AND position('bearer ' IN lower(p_value)) = 0
       AND position('api_key' IN lower(p_value)) = 0
       AND position('secret_key' IN lower(p_value)) = 0
       AND position('credential' IN lower(p_value)) = 0
$$;

CREATE OR REPLACE FUNCTION public.register_research_bundle(
    p_bundle_id UUID, p_parent_input_id UUID, p_bundle_hash TEXT,
    p_as_of TIMESTAMPTZ, p_window TEXT, p_deadline TIMESTAMPTZ,
    p_universe_hash TEXT, p_snapshot_hash TEXT, p_items JSONB,
    p_payload_hash TEXT, p_payload TEXT
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing public.research_bundles%ROWTYPE;
    v_item JSONB;
    v_item_count INTEGER;
    v_bundle_payload JSONB;
    v_analysis public.analysis_runs%ROWTYPE;
    v_packet public.evidence_packets%ROWTYPE;
    v_trader_hash TEXT;
    v_trader_payload TEXT;
    v_complete_hash TEXT;
    v_complete_payload TEXT;
    v_trader_plan JSONB;
    v_ordinal INTEGER;
    v_derived_citations JSONB;
BEGIN
    IF p_window NOT IN ('PRIMARY', 'SECONDARY', 'EMERGENCY') OR p_deadline <= p_as_of THEN
        RAISE EXCEPTION 'research bundle boundary is invalid' USING ERRCODE = '23514';
    END IF;
    IF clock_timestamp() > p_deadline THEN
        RAISE EXCEPTION 'research bundle deadline expired' USING ERRCODE = '57014';
    END IF;
    IF p_bundle_id <> public.p3d_derive_run_id(
        'seven-lens.p3d.bundle.v1', p_parent_input_id::text
    ) THEN
        RAISE EXCEPTION 'research bundle identity is invalid' USING ERRCODE = '23514';
    END IF;
    -- A bundle has several independent UNIQUE identities.  Serialize the one
    -- deterministic parent authority so concurrent identical registrations
    -- cannot lose speculative insertion on a non-arbiter unique index.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_parent_input_id::text, 0)
    );
    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' THEN
        RAISE EXCEPTION 'research bundle items are malformed' USING ERRCODE = '23514';
    END IF;
    IF octet_length(p_items::text) > 262144 THEN
        RAISE EXCEPTION 'research bundle items are outside their bound' USING ERRCODE = '23514';
    END IF;
    v_item_count := jsonb_array_length(p_items);
    IF v_item_count < 1 OR v_item_count > 27 THEN
        RAISE EXCEPTION 'research bundle item count is outside its bound'
            USING ERRCODE = '23514';
    END IF;
    IF length(p_payload) NOT BETWEEN 1 AND 262144
       OR encode(digest(convert_to(p_payload, 'UTF8'), 'sha256'), 'hex') <> p_payload_hash THEN
        RAISE EXCEPTION 'research bundle payload hash is invalid' USING ERRCODE = '23514';
    END IF;
    IF NOT (p_payload IS JSON) THEN
        RAISE EXCEPTION 'research bundle payload is not valid JSON' USING ERRCODE = '23514';
    END IF;
    IF p_payload IS DISTINCT FROM public.p3d_canonical_json(p_payload::json) THEN
        RAISE EXCEPTION 'research bundle payload is not canonical JSON'
            USING ERRCODE = '23514';
    END IF;
    v_bundle_payload := p_payload::jsonb;
    IF jsonb_typeof(v_bundle_payload) <> 'object'
       OR NOT (v_bundle_payload ?& ARRAY[
           'meta', 'bundle_id', 'parent_input_id', 'as_of', 'window', 'deadline',
           'universe_hash', 'portfolio_snapshot_hash', 'data_snapshot_refs',
           'holding_symbols', 'candidate_symbols', 'focus_symbols', 'items',
           'citation_ids', 'bundle_hash'
       ])
       OR (v_bundle_payload - ARRAY[
           'meta', 'bundle_id', 'parent_input_id', 'as_of', 'window', 'deadline',
           'universe_hash', 'portfolio_snapshot_hash', 'data_snapshot_refs',
           'holding_symbols', 'candidate_symbols', 'focus_symbols', 'items',
           'citation_ids', 'bundle_hash'
       ]) <> '{}'::jsonb
       OR jsonb_typeof(v_bundle_payload->'items') <> 'array' THEN
        RAISE EXCEPTION 'research bundle payload shape is invalid' USING ERRCODE = '23514';
    END IF;
    IF v_bundle_payload->>'bundle_id' IS DISTINCT FROM p_bundle_id::text
       OR v_bundle_payload->>'parent_input_id' IS DISTINCT FROM p_parent_input_id::text
       OR v_bundle_payload->>'bundle_hash' IS DISTINCT FROM p_bundle_hash
       OR encode(
              digest(
                  convert_to(
                      public.p3d_canonical_json((v_bundle_payload - 'bundle_hash')::json),
                      'UTF8'
                  ),
                  'sha256'
              ),
              'hex'
          ) IS DISTINCT FROM p_bundle_hash
       OR v_bundle_payload->>'as_of' IS DISTINCT FROM
          to_char(p_as_of AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
       OR v_bundle_payload->>'window' IS DISTINCT FROM p_window
       OR v_bundle_payload->>'deadline' IS DISTINCT FROM
          to_char(p_deadline AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
       OR v_bundle_payload->>'universe_hash' IS DISTINCT FROM p_universe_hash
       OR v_bundle_payload->>'portfolio_snapshot_hash' IS DISTINCT FROM p_snapshot_hash
       OR jsonb_array_length(v_bundle_payload->'items') <> v_item_count THEN
        RAISE EXCEPTION 'research bundle payload identity is invalid' USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(v_bundle_payload->'meta') <> 'object'
       OR NOT ((v_bundle_payload->'meta') ?& ARRAY[
           'schema_version', 'run_id', 'created_at', 'producer_version'
       ])
       OR ((v_bundle_payload->'meta') - ARRAY[
           'schema_version', 'run_id', 'created_at', 'producer_version'
       ]) <> '{}'::jsonb
       OR EXISTS (
           SELECT 1 FROM jsonb_each(v_bundle_payload->'meta') AS meta_field
           WHERE jsonb_typeof(meta_field.value) <> 'string'
       )
       OR v_bundle_payload->'meta'->>'schema_version' IS DISTINCT FROM '1.0.0'
       OR v_bundle_payload->'meta'->>'run_id' IS DISTINCT FROM p_bundle_id::text
       OR v_bundle_payload->'meta'->>'created_at' !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_bundle_payload->'meta'->>'producer_version' !~
          '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR NOT public.p3d_text_is_safe(v_bundle_payload->'meta'->>'producer_version')
       OR jsonb_typeof(v_bundle_payload->'focus_symbols') <> 'array'
       OR jsonb_array_length(v_bundle_payload->'focus_symbols') <> v_item_count
       OR jsonb_typeof(v_bundle_payload->'holding_symbols') <> 'array'
       OR jsonb_array_length(v_bundle_payload->'holding_symbols') > 15
       OR jsonb_typeof(v_bundle_payload->'candidate_symbols') <> 'array'
       OR jsonb_array_length(v_bundle_payload->'candidate_symbols') > 12
       OR jsonb_typeof(v_bundle_payload->'citation_ids') <> 'array'
       OR jsonb_array_length(v_bundle_payload->'citation_ids') NOT BETWEEN 1 AND 864
       OR jsonb_typeof(v_bundle_payload->'data_snapshot_refs') <> 'array'
       OR jsonb_array_length(v_bundle_payload->'data_snapshot_refs') > 32 THEN
        RAISE EXCEPTION 'research bundle nested payload is invalid' USING ERRCODE = '23514';
    END IF;
    PERFORM (v_bundle_payload->'meta'->>'created_at')::timestamptz;
    IF encode(
           digest(
               convert_to(
                   public.p3d_canonical_json(
                       jsonb_build_object(
                           'holdings', v_bundle_payload->'holding_symbols',
                           'candidates', v_bundle_payload->'candidate_symbols'
                       )::json
                   ),
                   'UTF8'
               ),
               'sha256'
           ),
           'hex'
       ) IS DISTINCT FROM p_universe_hash
       OR (SELECT count(*) FROM jsonb_array_elements(v_bundle_payload->'holding_symbols')) <>
          (SELECT count(DISTINCT symbol) FROM jsonb_array_elements_text(
              v_bundle_payload->'holding_symbols'
          ) AS holding(symbol))
       OR (SELECT count(*) FROM jsonb_array_elements(v_bundle_payload->'candidate_symbols')) <>
          (SELECT count(DISTINCT symbol) FROM jsonb_array_elements_text(
              v_bundle_payload->'candidate_symbols'
          ) AS candidate(symbol))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements_text(v_bundle_payload->'holding_symbols') AS holding(symbol)
           JOIN jsonb_array_elements_text(v_bundle_payload->'candidate_symbols') AS candidate(symbol)
             USING (symbol)
       )
       OR EXISTS (
           SELECT 1 FROM jsonb_array_elements_text(v_bundle_payload->'focus_symbols') AS focus(symbol)
           WHERE NOT (
               (v_bundle_payload->'holding_symbols') ? focus.symbol
               OR (v_bundle_payload->'candidate_symbols') ? focus.symbol
           )
       )
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_bundle_payload->'holding_symbols') AS entry(value)
           WHERE jsonb_typeof(entry.value) <> 'string'
              OR entry.value #>> '{}' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
       )
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_bundle_payload->'candidate_symbols') AS entry(value)
           WHERE jsonb_typeof(entry.value) <> 'string'
              OR entry.value #>> '{}' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
       )
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_bundle_payload->'focus_symbols') AS entry(value)
           WHERE jsonb_typeof(entry.value) <> 'string'
              OR entry.value #>> '{}' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
       )
       OR (SELECT count(*) FROM jsonb_array_elements(v_bundle_payload->'data_snapshot_refs')) <>
          (SELECT count(DISTINCT ref)
           FROM jsonb_array_elements_text(v_bundle_payload->'data_snapshot_refs') AS refs(ref))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_bundle_payload->'data_snapshot_refs') AS entry(value)
           WHERE jsonb_typeof(entry.value) <> 'string'
              OR entry.value #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
              OR NOT public.p3d_text_is_safe(entry.value #>> '{}')
       ) THEN
        RAISE EXCEPTION 'research bundle universe is invalid' USING ERRCODE = '23514';
    END IF;
    FOR v_item IN SELECT element FROM jsonb_array_elements(p_items) AS element LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR jsonb_typeof(v_item->'ordinal') <> 'number'
           OR v_item->>'ordinal' !~ '^([1-9]|1[0-9]|2[0-7])$' THEN
            RAISE EXCEPTION 'research bundle item ordinal is invalid'
                USING ERRCODE = '23514';
        END IF;
        v_ordinal := (v_item->>'ordinal')::integer;
        IF v_item->>'analysis_run_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_item->>'analysis_input_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_item->>'trader_plan_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_item->>'trader_plan_id' = '00000000-0000-0000-0000-000000000000' THEN
            RAISE EXCEPTION 'research bundle item UUID text is invalid'
                USING ERRCODE = '23514';
        END IF;
        IF jsonb_typeof(v_item) <> 'object'
           OR NOT (v_item ?& ARRAY[
               'ordinal', 'symbol', 'analysis_run_id', 'analysis_input_id', 'packet_hash',
               'snapshot_hash', 'trader_plan_id', 'trader_plan_hash', 'trader_plan',
               'evidence_refs',
               'producer_version', 'graph_version', 'prompt_version', 'data_version', 'status'
           ])
           OR (v_item - ARRAY[
               'ordinal', 'symbol', 'analysis_run_id', 'analysis_input_id', 'packet_hash',
               'snapshot_hash', 'trader_plan_id', 'trader_plan_hash', 'trader_plan',
               'evidence_refs',
               'producer_version', 'graph_version', 'prompt_version', 'data_version', 'status'
           ]) <> '{}'::jsonb
           OR jsonb_typeof(v_item->'ordinal') <> 'number'
           OR v_item->>'ordinal' !~ '^([1-9]|1[0-9]|2[0-7])$'
           OR (v_item->>'ordinal')::integer NOT BETWEEN 1 AND 27
           OR v_item->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
           OR v_item->>'packet_hash' !~ '^[0-9a-f]{64}$'
           OR v_item->>'snapshot_hash' !~ '^[0-9a-f]{64}$'
           OR v_item->>'trader_plan_hash' !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(v_item->'trader_plan') <> 'object'
           OR v_item->>'status' <> 'VALID'
           OR v_item->>'snapshot_hash' <> p_snapshot_hash
           OR jsonb_typeof(v_item->'evidence_refs') <> 'array'
           OR jsonb_array_length(v_item->'evidence_refs') NOT BETWEEN 1 AND 32
           OR (SELECT count(*) FROM jsonb_array_elements(v_item->'evidence_refs')) <>
              (SELECT count(DISTINCT ref)
               FROM jsonb_array_elements_text(v_item->'evidence_refs') AS refs(ref))
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(v_item->'evidence_refs') AS evidence(value)
               WHERE jsonb_typeof(evidence.value) <> 'string'
                  OR evidence.value #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
                  OR NOT public.p3d_text_is_safe(evidence.value #>> '{}')
           )
           OR v_item->>'producer_version' IS DISTINCT FROM
              v_bundle_payload->'meta'->>'producer_version'
           OR v_item->>'graph_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
           OR v_item->>'prompt_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
           OR v_item->>'data_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
           OR NOT public.p3d_text_is_safe(v_item->>'producer_version')
           OR NOT public.p3d_text_is_safe(v_item->>'graph_version')
           OR NOT public.p3d_text_is_safe(v_item->>'prompt_version')
           OR NOT public.p3d_text_is_safe(v_item->>'data_version')
           OR v_item->>'packet_hash' IS DISTINCT FROM
              v_bundle_payload->'items'->0->>'packet_hash'
           OR v_item->>'producer_version' IS DISTINCT FROM
              v_bundle_payload->'items'->0->>'producer_version'
           OR v_item->>'graph_version' IS DISTINCT FROM
              v_bundle_payload->'items'->0->>'graph_version'
           OR v_item->>'prompt_version' IS DISTINCT FROM
              v_bundle_payload->'items'->0->>'prompt_version'
           OR v_item->>'data_version' IS DISTINCT FROM
              v_bundle_payload->'items'->0->>'data_version'
           OR v_item->>'symbol' IS DISTINCT FROM
              v_bundle_payload->'focus_symbols'->>(v_ordinal - 1)
           OR v_item->>'analysis_run_id' IS DISTINCT FROM
              ((v_item->>'analysis_run_id')::uuid)::text
           OR v_item->>'analysis_input_id' IS DISTINCT FROM
              ((v_item->>'analysis_input_id')::uuid)::text
           OR v_item->>'trader_plan_id' IS DISTINCT FROM
              ((v_item->>'trader_plan_id')::uuid)::text
           OR (v_item->>'analysis_run_id')::uuid <> public.p3d_derive_run_id(
               'seven-lens.p3d.child-run.v1',
               p_parent_input_id::text, v_item->>'symbol'
           )
           OR (v_item->>'analysis_input_id')::uuid <> public.p3d_derive_run_id(
               'seven-lens.p3d.child-input.v1',
               p_parent_input_id::text, v_item->>'symbol'
           ) THEN
            RAISE EXCEPTION 'research bundle item is malformed' USING ERRCODE = '23514';
        END IF;
        IF (v_bundle_payload->'items'->(v_ordinal - 1)) IS DISTINCT FROM
           (v_item - 'ordinal') THEN
            RAISE EXCEPTION 'research bundle item payload is not canonical'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO v_analysis FROM public.analysis_runs
          WHERE run_id = (v_item->>'analysis_run_id')::uuid FOR SHARE;
        IF NOT FOUND OR v_analysis.input_id <> (v_item->>'analysis_input_id')::uuid
           OR v_analysis.packet_hash <> v_item->>'packet_hash'
           OR v_analysis.snapshot_hash <> v_item->>'snapshot_hash'
           OR v_analysis.current_stage <> 'COMPLETE' THEN
            RAISE EXCEPTION 'research bundle child analysis authority is invalid'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO v_packet FROM public.evidence_packets
          WHERE packet_hash = v_analysis.packet_hash FOR SHARE;
        IF NOT FOUND OR v_packet.as_of IS DISTINCT FROM p_as_of
           OR v_packet.universe_hash IS DISTINCT FROM p_universe_hash
           OR v_packet.snapshot_hash IS DISTINCT FROM p_snapshot_hash
           OR v_packet.producer_version IS DISTINCT FROM v_item->>'producer_version' THEN
            RAISE EXCEPTION 'research bundle child evidence authority is invalid'
                USING ERRCODE = '23514';
        END IF;
        SELECT result_hash, payload INTO v_complete_hash, v_complete_payload
          FROM public.analysis_stage_results
          WHERE run_id = v_analysis.run_id AND stage = 'COMPLETE';
        IF NOT FOUND OR v_complete_payload IS DISTINCT FROM 'complete'
           OR encode(digest(convert_to(v_complete_payload, 'UTF8'), 'sha256'), 'hex') <>
              v_complete_hash THEN
            RAISE EXCEPTION 'research bundle child COMPLETE authority is invalid'
                USING ERRCODE = '23514';
        END IF;
        SELECT result_hash, payload INTO v_trader_hash, v_trader_payload
          FROM public.analysis_stage_results
          WHERE run_id = v_analysis.run_id AND stage = 'TRADER';
        IF NOT FOUND OR v_trader_hash <> v_item->>'trader_plan_hash'
           OR encode(digest(convert_to(v_trader_payload, 'UTF8'), 'sha256'), 'hex') <>
              v_trader_hash THEN
            RAISE EXCEPTION 'research bundle child TraderPlan authority is invalid'
                USING ERRCODE = '23514';
        END IF;
        IF NOT (v_trader_payload IS JSON) THEN
            RAISE EXCEPTION 'research bundle child TraderPlan is not valid JSON'
                USING ERRCODE = '23514';
        END IF;
        IF v_trader_payload IS DISTINCT FROM public.p3d_canonical_json(v_trader_payload::json) THEN
            RAISE EXCEPTION 'research bundle child TraderPlan is not canonical JSON'
                USING ERRCODE = '23514';
        END IF;
        v_trader_plan := v_trader_payload::jsonb;
        IF jsonb_typeof(v_trader_plan) <> 'object'
           OR v_item->'trader_plan' IS DISTINCT FROM v_trader_plan
           OR NOT (v_trader_plan ?& ARRAY[
               'meta', 'plan_id', 'input_id', 'symbol', 'rating', 'reason_codes',
               'evidence_refs', 'entry_band_low', 'entry_band_high', 'downside_band', 'status'
           ])
           OR (v_trader_plan - ARRAY[
               'meta', 'plan_id', 'input_id', 'symbol', 'rating', 'reason_codes',
               'evidence_refs', 'entry_band_low', 'entry_band_high', 'downside_band', 'status'
           ]) <> '{}'::jsonb
           OR jsonb_typeof(v_trader_plan->'meta') <> 'object'
           OR NOT ((v_trader_plan->'meta') ?& ARRAY[
               'schema_version', 'run_id', 'created_at', 'producer_version'
           ])
           OR ((v_trader_plan->'meta') - ARRAY[
               'schema_version', 'run_id', 'created_at', 'producer_version'
           ]) <> '{}'::jsonb
           OR v_trader_plan->'meta'->>'schema_version' IS DISTINCT FROM '1.0.0'
           OR v_trader_plan->'meta'->>'run_id' IS DISTINCT FROM v_analysis.run_id::text
           OR v_trader_plan->'meta'->>'created_at' IS DISTINCT FROM
              v_bundle_payload->'meta'->>'created_at'
           OR v_trader_plan->'meta'->>'producer_version' IS DISTINCT FROM
              v_item->>'producer_version'
           OR v_trader_plan->>'plan_id' IS DISTINCT FROM v_item->>'trader_plan_id'
           OR v_trader_plan->>'input_id' IS DISTINCT FROM v_analysis.input_id::text
           OR v_trader_plan->>'symbol' IS DISTINCT FROM v_item->>'symbol'
           OR v_trader_plan->>'status' IS DISTINCT FROM 'VALID'
           OR jsonb_typeof(v_trader_plan->'rating') <> 'string'
           OR v_trader_plan->>'rating' NOT IN (
               'BUY', 'OVERWEIGHT', 'HOLD', 'UNDERWEIGHT', 'SELL'
           )
           OR jsonb_typeof(v_trader_plan->'reason_codes') <> 'array'
           OR jsonb_array_length(v_trader_plan->'reason_codes') NOT BETWEEN 1 AND 6
           OR (SELECT count(*) FROM jsonb_array_elements(v_trader_plan->'reason_codes')) <>
              (SELECT count(DISTINCT reason)
               FROM jsonb_array_elements_text(v_trader_plan->'reason_codes') AS reasons(reason))
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(v_trader_plan->'reason_codes') AS reason
               WHERE reason NOT IN (
                   'TECHNICAL', 'FUNDAMENTAL', 'NEWS', 'SENTIMENT',
                   'VALUATION', 'REBALANCE'
               )
           )
           OR v_trader_plan->'evidence_refs' IS DISTINCT FROM v_item->'evidence_refs'
           OR (v_trader_plan->'entry_band_low' = 'null'::jsonb) <>
              (v_trader_plan->'entry_band_high' = 'null'::jsonb)
           OR EXISTS (
               SELECT 1
               FROM jsonb_each(v_trader_plan) AS field
               WHERE field.key IN ('entry_band_low', 'entry_band_high', 'downside_band')
                 AND field.value <> 'null'::jsonb
                 AND (jsonb_typeof(field.value) <> 'string'
                      OR field.value #>> '{}' !~ '^(0|[1-9][0-9]{0,7})\.[0-9]{2}$'
                      OR (field.value #>> '{}')::numeric <= 0
                      OR (field.value #>> '{}')::numeric > 10000000)
           )
           OR (
               v_trader_plan->'entry_band_low' <> 'null'::jsonb
               AND (v_trader_plan->>'entry_band_low')::numeric >
                   (v_trader_plan->>'entry_band_high')::numeric
           ) THEN
            RAISE EXCEPTION 'research bundle child TraderPlan payload is invalid'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    SELECT COALESCE(jsonb_agg(ref ORDER BY ref), '[]'::jsonb)
      INTO v_derived_citations
      FROM (
          SELECT DISTINCT jsonb_array_elements_text(item->'evidence_refs') AS ref
          FROM jsonb_array_elements(p_items) AS item
      ) AS citations;
    IF v_derived_citations IS DISTINCT FROM v_bundle_payload->'citation_ids' THEN
        RAISE EXCEPTION 'research bundle citation union is invalid' USING ERRCODE = '23514';
    END IF;
    IF clock_timestamp() > p_deadline THEN
        RAISE EXCEPTION 'research bundle deadline expired' USING ERRCODE = '57014';
    END IF;
    INSERT INTO public.research_bundles (
        bundle_id, parent_input_id, bundle_hash, as_of, analysis_window, deadline,
        universe_hash, snapshot_hash, payload_hash, payload
    ) VALUES (
        p_bundle_id, p_parent_input_id, p_bundle_hash, p_as_of, p_window, p_deadline,
        p_universe_hash, p_snapshot_hash, p_payload_hash, p_payload
    ) ON CONFLICT (bundle_id) DO NOTHING;
    SELECT * INTO v_existing FROM public.research_bundles WHERE bundle_id = p_bundle_id;
    IF v_existing.parent_input_id IS DISTINCT FROM p_parent_input_id
       OR v_existing.bundle_hash IS DISTINCT FROM p_bundle_hash
       OR v_existing.as_of IS DISTINCT FROM p_as_of
       OR v_existing.analysis_window IS DISTINCT FROM p_window
       OR v_existing.deadline IS DISTINCT FROM p_deadline
       OR v_existing.universe_hash IS DISTINCT FROM p_universe_hash
       OR v_existing.snapshot_hash IS DISTINCT FROM p_snapshot_hash
       OR v_existing.payload_hash IS DISTINCT FROM p_payload_hash
       OR v_existing.payload IS DISTINCT FROM p_payload THEN
        RAISE EXCEPTION 'research bundle identity collision' USING ERRCODE = '23514';
    END IF;
    FOR v_item IN SELECT element FROM jsonb_array_elements(p_items) AS element LOOP
        INSERT INTO public.research_bundle_items (
            bundle_id, item_ordinal, symbol, analysis_run_id, analysis_input_id,
            packet_hash, snapshot_hash, trader_plan_id, trader_plan_hash, item_payload
        ) VALUES (
            p_bundle_id, (v_item->>'ordinal')::integer, v_item->>'symbol',
            (v_item->>'analysis_run_id')::uuid,
            (v_item->>'analysis_input_id')::uuid, v_item->>'packet_hash',
            v_item->>'snapshot_hash', (v_item->>'trader_plan_id')::uuid,
            v_item->>'trader_plan_hash', v_item - 'ordinal'
        ) ON CONFLICT (bundle_id, symbol) DO NOTHING;
        IF NOT EXISTS (
            SELECT 1 FROM public.research_bundle_items
            WHERE bundle_id = p_bundle_id
              AND item_ordinal = (v_item->>'ordinal')::integer
              AND symbol = v_item->>'symbol'
              AND analysis_run_id = (v_item->>'analysis_run_id')::uuid
              AND analysis_input_id = (v_item->>'analysis_input_id')::uuid
              AND packet_hash = v_item->>'packet_hash'
              AND snapshot_hash = v_item->>'snapshot_hash'
              AND trader_plan_id = (v_item->>'trader_plan_id')::uuid
              AND trader_plan_hash = v_item->>'trader_plan_hash'
              AND item_payload = v_item - 'ordinal'
        ) THEN
            RAISE EXCEPTION 'research bundle item identity collision'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM public.research_bundle_items WHERE bundle_id = p_bundle_id)
       <> v_item_count THEN
        RAISE EXCEPTION 'research bundle item set is not exact' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.register_risk_feedback(
    p_feedback_id UUID, p_rejected_proposal_id UUID, p_feedback_hash TEXT, p_payload TEXT
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing public.risk_rejection_feedback%ROWTYPE;
    v_payload JSONB;
    v_proposal public.portfolio_proposals%ROWTYPE;
    v_proposal_payload JSONB;
    v_context_payload JSONB;
    v_bundle_deadline TIMESTAMPTZ;
    v_run_stage TEXT;
BEGIN
    IF length(p_payload) NOT BETWEEN 1 AND 65536 THEN
        RAISE EXCEPTION 'risk feedback payload is outside its bound' USING ERRCODE = '23514';
    END IF;
    IF encode(digest(convert_to(p_payload, 'UTF8'), 'sha256'), 'hex') <> p_feedback_hash THEN
        RAISE EXCEPTION 'risk feedback payload hash is invalid' USING ERRCODE = '23514';
    END IF;
    IF NOT (p_payload IS JSON) THEN
        RAISE EXCEPTION 'risk feedback payload is not valid JSON' USING ERRCODE = '23514';
    END IF;
    IF p_payload IS DISTINCT FROM public.p3d_canonical_json(p_payload::json) THEN
        RAISE EXCEPTION 'risk feedback payload is not canonical JSON'
            USING ERRCODE = '23514';
    END IF;
    v_payload := p_payload::jsonb;
    IF jsonb_typeof(v_payload) <> 'object'
       OR NOT (v_payload ?& ARRAY[
           'meta', 'rejected_proposal_id', 'review_round', 'rejection_codes',
           'rejected_symbols', 'remaining_limits', 'constraints_snapshot_hash', 'reviewed_at'
       ])
       OR (v_payload - ARRAY[
           'meta', 'rejected_proposal_id', 'review_round', 'rejection_codes',
           'rejected_symbols', 'remaining_limits', 'constraints_snapshot_hash', 'reviewed_at'
       ]) <> '{}'::jsonb
       OR jsonb_typeof(v_payload->'meta') <> 'object'
       OR jsonb_typeof(v_payload->'rejection_codes') <> 'array'
       OR jsonb_array_length(v_payload->'rejection_codes') NOT BETWEEN 1 AND 15
       OR jsonb_typeof(v_payload->'rejected_symbols') <> 'array'
       OR jsonb_array_length(v_payload->'rejected_symbols') > 27
       OR jsonb_typeof(v_payload->'remaining_limits') <> 'object'
       OR NOT ((v_payload->'meta') ?& ARRAY[
           'schema_version', 'run_id', 'created_at', 'producer_version'
       ])
       OR ((v_payload->'meta') - ARRAY[
           'schema_version', 'run_id', 'created_at', 'producer_version'
       ]) <> '{}'::jsonb
       OR EXISTS (
           SELECT 1 FROM jsonb_each(v_payload->'meta') AS meta_field
           WHERE jsonb_typeof(meta_field.value) <> 'string'
       )
       OR NOT ((v_payload->'remaining_limits') ?& ARRAY[
           'remaining_slots', 'long_gross_room', 'short_gross_room', 'total_gross_room',
           'net_lower_room', 'net_upper_room', 'single_name_room', 'turnover_room'
       ])
       OR ((v_payload->'remaining_limits') - ARRAY[
           'remaining_slots', 'long_gross_room', 'short_gross_room', 'total_gross_room',
           'net_lower_room', 'net_upper_room', 'single_name_room', 'turnover_room'
       ]) <> '{}'::jsonb
       OR jsonb_typeof(v_payload->'remaining_limits'->'remaining_slots') <> 'number'
       OR v_payload->'remaining_limits'->>'remaining_slots' !~
          '^(0|[1-9]|1[0-5])$'
       OR EXISTS (
           SELECT 1
           FROM jsonb_each(v_payload->'remaining_limits') AS limit_value
           WHERE limit_value.key <> 'remaining_slots'
             AND (
                 jsonb_typeof(limit_value.value) <> 'string'
                 OR limit_value.value #>> '{}' !~ '^-?(0|1|2)\.[0-9]{6}$'
                 OR abs((limit_value.value #>> '{}')::numeric) > 2
                 OR limit_value.value #>> '{}' = '-0.000000'
             )
       )
       OR v_payload->'meta'->>'schema_version' IS DISTINCT FROM '1.0.0'
       OR v_payload->'meta'->>'run_id' IS DISTINCT FROM p_feedback_id::text
       OR v_payload->'meta'->>'created_at' !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_payload->'meta'->>'producer_version' !~
          '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR NOT public.p3d_text_is_safe(v_payload->'meta'->>'producer_version')
       OR v_payload->>'rejected_proposal_id' IS DISTINCT FROM p_rejected_proposal_id::text
       OR jsonb_typeof(v_payload->'review_round') <> 'number'
       OR v_payload->>'review_round' !~ '^1$'
       OR (v_payload->>'review_round')::integer IS DISTINCT FROM 1
       OR v_payload->>'constraints_snapshot_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(v_payload->'constraints_snapshot_hash') <> 'string'
       OR jsonb_typeof(v_payload->'reviewed_at') <> 'string'
       OR v_payload->>'reviewed_at' !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'rejection_codes')) <>
          (SELECT count(DISTINCT code)
           FROM jsonb_array_elements_text(v_payload->'rejection_codes') AS codes(code))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements_text(v_payload->'rejection_codes') AS codes(code)
           WHERE codes.code NOT IN (
               'CASH', 'BUYING_POWER', 'MAX_SYMBOLS', 'SINGLE_NAME', 'LONG_GROSS',
               'SHORT_GROSS', 'TOTAL_GROSS', 'NET_EXPOSURE', 'TURNOVER', 'BORROW',
               'OPEN_ORDER_CONFLICT', 'STALE_SNAPSHOT', 'SAME_DAY_EXIT', 'DATA_CONFLICT',
               'SCHEMA_INVALID'
           )
       )
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'rejected_symbols')) <>
          (SELECT count(DISTINCT symbol)
           FROM jsonb_array_elements_text(v_payload->'rejected_symbols') AS symbols(symbol))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_payload->'rejected_symbols') AS rejected(value)
           WHERE jsonb_typeof(rejected.value) <> 'string'
              OR rejected.value #>> '{}' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
       ) THEN
        RAISE EXCEPTION 'risk feedback payload identity is invalid' USING ERRCODE = '23514';
    END IF;
    PERFORM (v_payload->'meta'->>'created_at')::timestamptz;
    PERFORM (v_payload->>'reviewed_at')::timestamptz;
    SELECT * INTO v_proposal FROM public.portfolio_proposals
      WHERE proposal_id = p_rejected_proposal_id FOR SHARE;
    IF NOT FOUND OR v_proposal.attempt <> 1 OR v_proposal.status <> 'VALID' THEN
        RAISE EXCEPTION 'risk feedback targets unknown proposal authority'
            USING ERRCODE = '23514';
    END IF;
    SELECT current_stage INTO v_run_stage FROM public.proposal_runs
      WHERE context_id = v_proposal.context_id FOR SHARE;
    SELECT payload::jsonb INTO v_proposal_payload FROM public.proposal_stage_results
      WHERE run_id = (
          SELECT run_id FROM public.proposal_runs WHERE context_id = v_proposal.context_id
      ) AND stage = 'PROPOSAL';
    SELECT context.payload::jsonb, bundle.deadline
      INTO v_context_payload, v_bundle_deadline
      FROM public.proposal_contexts AS context
      JOIN public.research_bundles AS bundle ON bundle.bundle_id = context.bundle_id
      WHERE context.context_id = v_proposal.context_id;
    IF v_run_stage IS DISTINCT FROM 'COMPLETE'
       OR v_proposal_payload IS NULL
       OR v_context_payload IS NULL
       OR (v_payload->>'reviewed_at')::timestamptz <=
          (v_context_payload->'meta'->>'created_at')::timestamptz
       OR (v_payload->>'reviewed_at')::timestamptz > clock_timestamp()
       OR (v_payload->>'reviewed_at')::timestamptz >= v_bundle_deadline
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements_text(v_payload->'rejected_symbols') AS rejected(symbol)
           WHERE NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(v_proposal_payload->'requests') AS request
               WHERE request->>'symbol' = rejected.symbol
           )
       ) THEN
        RAISE EXCEPTION 'risk feedback does not match completed proposal authority'
            USING ERRCODE = '23514';
    END IF;
    IF clock_timestamp() > (
        SELECT bundle.deadline
        FROM public.research_bundles AS bundle
        WHERE bundle.bundle_id = v_proposal.bundle_id
    ) THEN
        RAISE EXCEPTION 'risk feedback deadline expired' USING ERRCODE = '57014';
    END IF;
    IF clock_timestamp() > v_bundle_deadline THEN
        RAISE EXCEPTION 'risk feedback deadline expired' USING ERRCODE = '57014';
    END IF;
    INSERT INTO public.risk_rejection_feedback (
        feedback_id, rejected_proposal_id, feedback_hash, payload
    ) VALUES (p_feedback_id, p_rejected_proposal_id, p_feedback_hash, p_payload)
    ON CONFLICT (feedback_id) DO NOTHING;
    SELECT * INTO v_existing FROM public.risk_rejection_feedback
      WHERE feedback_id = p_feedback_id;
    IF v_existing.rejected_proposal_id IS DISTINCT FROM p_rejected_proposal_id
       OR v_existing.feedback_hash IS DISTINCT FROM p_feedback_hash
       OR v_existing.payload IS DISTINCT FROM p_payload THEN
        RAISE EXCEPTION 'risk feedback identity collision' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.register_proposal_context(
    p_context_id UUID, p_bundle_id UUID, p_attempt INTEGER, p_snapshot_hash TEXT,
    p_previous_context_id UUID, p_superseded_proposal_id UUID,
    p_superseded_proposal_hash TEXT, p_feedback_id UUID, p_context_hash TEXT,
    p_payload_hash TEXT, p_payload TEXT
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing public.proposal_contexts%ROWTYPE;
    v_previous public.proposal_contexts%ROWTYPE;
    v_superseded public.portfolio_proposals%ROWTYPE;
    v_feedback public.risk_rejection_feedback%ROWTYPE;
    v_bundle public.research_bundles%ROWTYPE;
    v_payload JSONB;
    v_feedback_payload JSONB;
BEGIN
    IF p_attempt NOT IN (1, 2)
       OR (p_attempt = 1) <> (p_previous_context_id IS NULL
                              AND p_superseded_proposal_id IS NULL
                              AND p_superseded_proposal_hash IS NULL
                              AND p_feedback_id IS NULL) THEN
        RAISE EXCEPTION 'proposal context attempt lineage is invalid' USING ERRCODE = '23514';
    END IF;
    IF p_context_id <> public.p3d_derive_run_id(
        'seven-lens.p3d.context.v1',
        p_bundle_id::text, p_attempt::text, p_snapshot_hash,
        COALESCE(p_superseded_proposal_id::text, '')
    ) THEN
        RAISE EXCEPTION 'proposal context identity is invalid' USING ERRCODE = '23514';
    END IF;
    IF length(p_payload) NOT BETWEEN 1 AND 262144
       OR encode(digest(convert_to(p_payload, 'UTF8'), 'sha256'), 'hex') <> p_payload_hash THEN
        RAISE EXCEPTION 'proposal context payload hash is invalid' USING ERRCODE = '23514';
    END IF;
    IF NOT (p_payload IS JSON) THEN
        RAISE EXCEPTION 'proposal context payload is not valid JSON' USING ERRCODE = '23514';
    END IF;
    IF p_payload IS DISTINCT FROM public.p3d_canonical_json(p_payload::json) THEN
        RAISE EXCEPTION 'proposal context payload is not canonical JSON'
            USING ERRCODE = '23514';
    END IF;
    v_payload := p_payload::jsonb;
    IF jsonb_typeof(v_payload) <> 'object'
       OR NOT (v_payload ?& ARRAY[
           'meta', 'context_id', 'attempt', 'bundle_id', 'bundle_hash', 'snapshot',
           'snapshot_hash', 'window', 'deadline', 'universe_hash', 'allowed_symbols',
           'citation_ids', 'graph_version', 'prompt_version', 'model_version',
           'provider_version', 'data_version', 'memory_version', 'previous_context_id',
           'superseded_proposal_id', 'superseded_proposal_hash', 'feedback', 'context_hash'
       ])
       OR (v_payload - ARRAY[
           'meta', 'context_id', 'attempt', 'bundle_id', 'bundle_hash', 'snapshot',
           'snapshot_hash', 'window', 'deadline', 'universe_hash', 'allowed_symbols',
           'citation_ids', 'graph_version', 'prompt_version', 'model_version',
           'provider_version', 'data_version', 'memory_version', 'previous_context_id',
           'superseded_proposal_id', 'superseded_proposal_hash', 'feedback', 'context_hash'
       ]) <> '{}'::jsonb
       OR jsonb_typeof(v_payload->'meta') <> 'object'
       OR jsonb_typeof(v_payload->'snapshot') <> 'object'
       OR jsonb_typeof(v_payload->'allowed_symbols') <> 'array'
       OR jsonb_array_length(v_payload->'allowed_symbols') NOT BETWEEN 1 AND 27
       OR jsonb_typeof(v_payload->'citation_ids') <> 'array'
       OR NOT ((v_payload->'meta') ?& ARRAY[
           'schema_version', 'run_id', 'created_at', 'producer_version'
       ])
       OR ((v_payload->'meta') - ARRAY[
           'schema_version', 'run_id', 'created_at', 'producer_version'
       ]) <> '{}'::jsonb
       OR EXISTS (
           SELECT 1 FROM jsonb_each(v_payload->'meta') AS meta_field
           WHERE jsonb_typeof(meta_field.value) <> 'string'
       )
       OR NOT ((v_payload->'snapshot') ?& ARRAY[
           'as_of', 'nav', 'cash', 'buying_power', 'positions', 'open_orders',
           'same_day_fills', 'borrow_statuses', 'remaining_limits', 'content_hash'
       ])
       OR ((v_payload->'snapshot') - ARRAY[
           'as_of', 'nav', 'cash', 'buying_power', 'positions', 'open_orders',
           'same_day_fills', 'borrow_statuses', 'remaining_limits', 'content_hash'
       ]) <> '{}'::jsonb
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'allowed_symbols')) <>
          (SELECT count(DISTINCT symbol)
           FROM jsonb_array_elements_text(v_payload->'allowed_symbols') AS symbols(symbol))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements_text(v_payload->'allowed_symbols') AS symbols(symbol)
           WHERE symbols.symbol !~ '^[A-Z][A-Z0-9.-]{0,9}$'
       )
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'citation_ids')) <>
          (SELECT count(DISTINCT citation)
           FROM jsonb_array_elements_text(v_payload->'citation_ids') AS citations(citation))
       OR EXISTS (
           SELECT 1 FROM jsonb_each(v_payload) AS version_field
           WHERE version_field.key IN (
               'graph_version', 'prompt_version', 'model_version',
               'provider_version', 'data_version', 'memory_version'
           ) AND (
               jsonb_typeof(version_field.value) <> 'string'
               OR version_field.value #>> '{}' !~
                  '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
               OR NOT public.p3d_text_is_safe(version_field.value #>> '{}')
           )
       ) THEN
        RAISE EXCEPTION 'proposal context payload shape is invalid' USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(v_payload->'attempt') <> 'number'
       OR v_payload->>'attempt' !~ '^[12]$' THEN
        RAISE EXCEPTION 'proposal context attempt is invalid' USING ERRCODE = '23514';
    END IF;
    IF v_payload->>'context_id' IS DISTINCT FROM p_context_id::text
       OR (v_payload->>'attempt')::integer IS DISTINCT FROM p_attempt
       OR v_payload->>'bundle_id' IS DISTINCT FROM p_bundle_id::text
       OR v_payload->>'snapshot_hash' IS DISTINCT FROM p_snapshot_hash
       OR v_payload->>'context_hash' IS DISTINCT FROM p_context_hash
       OR v_payload->>'previous_context_id' IS DISTINCT FROM p_previous_context_id::text
       OR v_payload->>'superseded_proposal_id' IS DISTINCT FROM p_superseded_proposal_id::text
       OR v_payload->>'superseded_proposal_hash' IS DISTINCT FROM p_superseded_proposal_hash
       OR v_payload->>'context_hash' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'proposal context payload identity is invalid' USING ERRCODE = '23514';
    END IF;
    IF encode(
           digest(
               convert_to(
                   public.p3d_canonical_json((v_payload - 'context_hash')::json),
                   'UTF8'
               ),
               'sha256'
           ),
           'hex'
       ) IS DISTINCT FROM p_context_hash
       OR jsonb_typeof(v_payload->'snapshot') <> 'object'
       OR v_payload->'snapshot'->>'content_hash' IS DISTINCT FROM p_snapshot_hash
       OR encode(
              digest(
                  convert_to(
                      public.p3d_canonical_json(
                          ((v_payload->'snapshot') - 'content_hash')::json
                      ),
                      'UTF8'
                  ),
                  'sha256'
              ),
              'hex'
          ) IS DISTINCT FROM p_snapshot_hash THEN
        RAISE EXCEPTION 'proposal context content hashes are invalid'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(v_payload->'snapshot'->'positions') <> 'array'
       OR jsonb_array_length(v_payload->'snapshot'->'positions') > 15
       OR jsonb_typeof(v_payload->'snapshot'->'open_orders') <> 'array'
       OR jsonb_array_length(v_payload->'snapshot'->'open_orders') > 64
       OR jsonb_typeof(v_payload->'snapshot'->'same_day_fills') <> 'array'
       OR jsonb_array_length(v_payload->'snapshot'->'same_day_fills') > 128
       OR jsonb_typeof(v_payload->'snapshot'->'borrow_statuses') <> 'array'
       OR jsonb_array_length(v_payload->'snapshot'->'borrow_statuses') > 64
       OR jsonb_typeof(v_payload->'snapshot'->'remaining_limits') <> 'object'
       OR NOT ((v_payload->'snapshot'->'remaining_limits') ?& ARRAY[
           'remaining_slots', 'long_gross_room', 'short_gross_room', 'total_gross_room',
           'net_lower_room', 'net_upper_room', 'single_name_room', 'turnover_room'
       ])
       OR ((v_payload->'snapshot'->'remaining_limits') - ARRAY[
           'remaining_slots', 'long_gross_room', 'short_gross_room', 'total_gross_room',
           'net_lower_room', 'net_upper_room', 'single_name_room', 'turnover_room'
       ]) <> '{}'::jsonb
       OR jsonb_typeof(v_payload->'snapshot'->'as_of') <> 'string'
       OR v_payload->'snapshot'->>'as_of' !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR jsonb_typeof(v_payload->'snapshot'->'nav') <> 'string'
       OR jsonb_typeof(v_payload->'snapshot'->'cash') <> 'string'
       OR jsonb_typeof(v_payload->'snapshot'->'buying_power') <> 'string'
       OR jsonb_typeof(v_payload->'snapshot'->'remaining_limits'->'remaining_slots') <>
          'number'
       OR v_payload->'snapshot'->'remaining_limits'->>'remaining_slots' !~
          '^(0|[1-9]|1[0-5])$'
       OR (v_payload->'snapshot'->'remaining_limits'->>'remaining_slots')::numeric < 0
       OR (v_payload->'snapshot'->'remaining_limits'->>'remaining_slots')::numeric > 15
       OR trunc(
              (v_payload->'snapshot'->'remaining_limits'->>'remaining_slots')::numeric
          ) <> (v_payload->'snapshot'->'remaining_limits'->>'remaining_slots')::numeric
       OR EXISTS (
           SELECT 1
           FROM jsonb_each(v_payload->'snapshot'->'remaining_limits') AS limit_value
           WHERE limit_value.key <> 'remaining_slots'
             AND (
                 jsonb_typeof(limit_value.value) <> 'string'
                 OR limit_value.value #>> '{}' !~ '^-?(0|1|2)\.[0-9]{6}$'
                 OR abs((limit_value.value #>> '{}')::numeric) > 2
                 OR limit_value.value #>> '{}' = '-0.000000'
             )
       )
       OR v_payload->'snapshot'->>'nav' !~ '^(0|[1-9][0-9]{0,12})\.[0-9]{2}$'
       OR (v_payload->'snapshot'->>'nav')::numeric <= 0
       OR (v_payload->'snapshot'->>'nav')::numeric > 1000000000000
       OR v_payload->'snapshot'->>'cash' !~ '^(0|[1-9][0-9]{0,12})\.[0-9]{2}$'
       OR (v_payload->'snapshot'->>'cash')::numeric > 1000000000000
       OR v_payload->'snapshot'->>'buying_power' !~
          '^(0|[1-9][0-9]{0,12})\.[0-9]{2}$'
       OR (v_payload->'snapshot'->>'buying_power')::numeric > 1000000000000
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'snapshot'->'positions')) <>
          (SELECT count(DISTINCT position->>'symbol')
           FROM jsonb_array_elements(v_payload->'snapshot'->'positions') AS positions(position))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_payload->'snapshot'->'positions') AS positions(position)
           WHERE jsonb_typeof(position) <> 'object'
              OR NOT (position ?& ARRAY[
                  'symbol', 'side', 'quantity', 'signed_weight', 'average_entry_price',
                  'current_price', 'market_value', 'unrealized_pnl', 'realized_pnl_today',
                  'opened_at', 'same_day'
              ])
              OR (position - ARRAY[
                  'symbol', 'side', 'quantity', 'signed_weight', 'average_entry_price',
                  'current_price', 'market_value', 'unrealized_pnl', 'realized_pnl_today',
                  'opened_at', 'same_day'
              ]) <> '{}'::jsonb
              OR EXISTS (
                  SELECT 1 FROM jsonb_each(position) AS field
                  WHERE field.key <> 'same_day' AND jsonb_typeof(field.value) <> 'string'
              )
              OR position->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
              OR position->>'side' NOT IN ('LONG', 'SHORT')
              OR jsonb_typeof(position->'same_day') <> 'boolean'
              OR position->>'quantity' !~ '^(0|[1-9][0-9]{0,9})\.[0-9]{6}$'
              OR (position->>'quantity')::numeric NOT BETWEEN 0.000001 AND 1000000000
              OR position->>'signed_weight' !~ '^-?(0|1)\.[0-9]{6}$'
              OR abs((position->>'signed_weight')::numeric) > 1
              OR (position->>'signed_weight')::numeric = 0
              OR ((position->>'side' = 'LONG') <> ((position->>'signed_weight')::numeric > 0))
              OR position->>'average_entry_price' !~ '^(0|[1-9][0-9]{0,7})\.[0-9]{2}$'
              OR (position->>'average_entry_price')::numeric NOT BETWEEN 0.01 AND 10000000
              OR position->>'current_price' !~ '^(0|[1-9][0-9]{0,7})\.[0-9]{2}$'
              OR (position->>'current_price')::numeric NOT BETWEEN 0.01 AND 10000000
              OR position->>'market_value' !~ '^(0|[1-9][0-9]{0,12})\.[0-9]{2}$'
              OR (position->>'market_value')::numeric > 1000000000000
              OR position->>'unrealized_pnl' !~ '^-?(0|[1-9][0-9]{0,12})\.[0-9]{2}$'
              OR abs((position->>'unrealized_pnl')::numeric) > 1000000000000
              OR position->>'unrealized_pnl' = '-0.00'
              OR position->>'realized_pnl_today' !~
                 '^-?(0|[1-9][0-9]{0,12})\.[0-9]{2}$'
              OR abs((position->>'realized_pnl_today')::numeric) > 1000000000000
              OR position->>'realized_pnl_today' = '-0.00'
              OR position->>'opened_at' !~
                 '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       )
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'snapshot'->'open_orders')) <>
          (SELECT count(DISTINCT order_item->>'reference_id')
           FROM jsonb_array_elements(v_payload->'snapshot'->'open_orders') AS orders(order_item))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_payload->'snapshot'->'open_orders') AS orders(order_item)
           WHERE jsonb_typeof(order_item) <> 'object'
              OR NOT (order_item ?& ARRAY[
                  'reference_id', 'symbol', 'side', 'remaining_quantity'
              ])
              OR (order_item - ARRAY[
                  'reference_id', 'symbol', 'side', 'remaining_quantity'
              ]) <> '{}'::jsonb
              OR EXISTS (
                  SELECT 1 FROM jsonb_each(order_item) AS field
                  WHERE jsonb_typeof(field.value) <> 'string'
              )
              OR order_item->>'reference_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
              OR NOT public.p3d_text_is_safe(order_item->>'reference_id')
              OR order_item->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
              OR order_item->>'side' NOT IN ('LONG', 'SHORT')
              OR order_item->>'remaining_quantity' !~ '^(0|[1-9][0-9]{0,9})\.[0-9]{6}$'
              OR (order_item->>'remaining_quantity')::numeric NOT BETWEEN 0.000001 AND 1000000000
       )
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'snapshot'->'same_day_fills')) <>
          (SELECT count(DISTINCT fill_item->>'reference_id')
           FROM jsonb_array_elements(v_payload->'snapshot'->'same_day_fills') AS fills(fill_item))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_payload->'snapshot'->'same_day_fills') AS fills(fill_item)
           WHERE jsonb_typeof(fill_item) <> 'object'
              OR NOT (fill_item ?& ARRAY[
                  'reference_id', 'symbol', 'side', 'quantity', 'price', 'occurred_at'
              ])
              OR (fill_item - ARRAY[
                  'reference_id', 'symbol', 'side', 'quantity', 'price', 'occurred_at'
              ]) <> '{}'::jsonb
              OR EXISTS (
                  SELECT 1 FROM jsonb_each(fill_item) AS field
                  WHERE jsonb_typeof(field.value) <> 'string'
              )
              OR fill_item->>'reference_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
              OR NOT public.p3d_text_is_safe(fill_item->>'reference_id')
              OR fill_item->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
              OR fill_item->>'side' NOT IN ('LONG', 'SHORT')
              OR fill_item->>'quantity' !~ '^(0|[1-9][0-9]{0,9})\.[0-9]{6}$'
              OR (fill_item->>'quantity')::numeric NOT BETWEEN 0.000001 AND 1000000000
              OR fill_item->>'price' !~ '^(0|[1-9][0-9]{0,7})\.[0-9]{2}$'
              OR (fill_item->>'price')::numeric NOT BETWEEN 0.01 AND 10000000
              OR fill_item->>'occurred_at' !~
                 '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       )
       OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'snapshot'->'borrow_statuses')) <>
          (SELECT count(DISTINCT borrow_item->>'symbol')
           FROM jsonb_array_elements(v_payload->'snapshot'->'borrow_statuses') AS borrows(borrow_item))
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(v_payload->'snapshot'->'borrow_statuses') AS borrows(borrow_item)
           WHERE jsonb_typeof(borrow_item) <> 'object'
              OR NOT (borrow_item ?& ARRAY['symbol', 'availability', 'located_quantity'])
              OR (borrow_item - ARRAY[
                  'symbol', 'availability', 'located_quantity'
              ]) <> '{}'::jsonb
              OR EXISTS (
                  SELECT 1 FROM jsonb_each(borrow_item) AS field
                  WHERE jsonb_typeof(field.value) <> 'string'
              )
              OR borrow_item->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
              OR borrow_item->>'availability' NOT IN ('AVAILABLE', 'UNAVAILABLE', 'UNKNOWN')
              OR borrow_item->>'located_quantity' !~ '^(0|[1-9][0-9]{0,9})\.[0-9]{6}$'
              OR (borrow_item->>'located_quantity')::numeric > 1000000000
              OR (
                  borrow_item->>'availability' <> 'AVAILABLE'
                  AND (borrow_item->>'located_quantity')::numeric <> 0
              )
       ) THEN
        RAISE EXCEPTION 'proposal context snapshot is malformed' USING ERRCODE = '23514';
    END IF;
    PERFORM (position->>'opened_at')::timestamptz
      FROM jsonb_array_elements(v_payload->'snapshot'->'positions') AS positions(position);
    PERFORM (fill_item->>'occurred_at')::timestamptz
      FROM jsonb_array_elements(v_payload->'snapshot'->'same_day_fills') AS fills(fill_item);
    SELECT * INTO v_bundle FROM public.research_bundles
      WHERE bundle_id = p_bundle_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposal context binds an unknown research bundle'
            USING ERRCODE = '23503';
    END IF;
    IF clock_timestamp() > v_bundle.deadline THEN
        RAISE EXCEPTION 'proposal context deadline expired' USING ERRCODE = '57014';
    END IF;
    IF v_payload->>'bundle_hash' <> v_bundle.bundle_hash
       OR v_payload->>'window' <> v_bundle.analysis_window
       OR v_payload->>'deadline' IS DISTINCT FROM
          (v_bundle.payload::jsonb)->>'deadline'
       OR v_payload->>'universe_hash' <> v_bundle.universe_hash
       OR v_payload->'allowed_symbols' IS DISTINCT FROM
          (((v_bundle.payload::jsonb)->'holding_symbols') ||
           ((v_bundle.payload::jsonb)->'candidate_symbols'))
       OR v_payload->'citation_ids' IS DISTINCT FROM (v_bundle.payload::jsonb)->'citation_ids'
       OR (v_payload->'snapshot'->>'content_hash') IS DISTINCT FROM p_snapshot_hash
       OR v_payload->'meta'->>'schema_version' IS DISTINCT FROM '1.0.0'
       OR v_payload->'meta'->>'run_id' IS DISTINCT FROM p_context_id::text
       OR v_payload->'meta'->>'created_at' IS DISTINCT FROM
          (v_bundle.payload::jsonb)->'meta'->>'created_at'
       OR v_payload->'meta'->>'producer_version' IS DISTINCT FROM
          (v_bundle.payload::jsonb)->'meta'->>'producer_version'
       OR v_payload->>'graph_version' IS DISTINCT FROM
          (v_bundle.payload::jsonb)->'items'->0->>'graph_version'
       OR v_payload->>'prompt_version' IS DISTINCT FROM
          (v_bundle.payload::jsonb)->'items'->0->>'prompt_version'
       OR v_payload->>'data_version' IS DISTINCT FROM
          (v_bundle.payload::jsonb)->'items'->0->>'data_version'
       OR (v_payload->'snapshot'->>'as_of')::timestamptz >= v_bundle.deadline
       OR (v_payload->'snapshot'->>'as_of')::timestamptz < v_bundle.as_of
       OR (v_payload->'snapshot'->>'as_of')::timestamptz > clock_timestamp() THEN
        RAISE EXCEPTION 'proposal context does not match the research bundle'
            USING ERRCODE = '23514';
    END IF;
    IF p_attempt = 1 AND p_snapshot_hash IS DISTINCT FROM v_bundle.snapshot_hash THEN
        RAISE EXCEPTION 'attempt 1 context snapshot is not the frozen bundle snapshot'
            USING ERRCODE = '23514';
    END IF;
    IF p_attempt = 2 THEN
        SELECT * INTO v_previous FROM public.proposal_contexts
          WHERE context_id = p_previous_context_id;
        IF NOT FOUND OR v_previous.attempt <> 1 OR v_previous.bundle_id <> p_bundle_id THEN
            RAISE EXCEPTION 'attempt 2 context requires the attempt 1 context'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO v_superseded FROM public.portfolio_proposals
          WHERE proposal_id = p_superseded_proposal_id;
        IF NOT FOUND OR v_superseded.attempt <> 1
           OR v_superseded.status <> 'VALID'
           OR v_superseded.bundle_id <> p_bundle_id
           OR v_superseded.context_id <> v_previous.context_id
           OR NOT EXISTS (
               SELECT 1 FROM public.proposal_runs
               WHERE context_id = v_previous.context_id AND current_stage = 'COMPLETE'
           ) THEN
            RAISE EXCEPTION 'attempt 2 context supersedes an unknown proposal'
                USING ERRCODE = '23514';
        END IF;
        IF v_superseded.proposal_hash <> p_superseded_proposal_hash THEN
            RAISE EXCEPTION 'attempt 2 context superseded proposal hash is invalid'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO v_feedback FROM public.risk_rejection_feedback
          WHERE feedback_id = p_feedback_id;
        IF NOT FOUND OR v_feedback.rejected_proposal_id <> p_superseded_proposal_id THEN
            RAISE EXCEPTION 'attempt 2 context requires registered risk feedback'
                USING ERRCODE = '23514';
        END IF;
        v_feedback_payload := v_feedback.payload::jsonb;
        IF v_payload->'feedback' IS DISTINCT FROM v_feedback_payload
           OR v_payload->'snapshot'->'remaining_limits' IS DISTINCT FROM
           v_feedback_payload->'remaining_limits'
           OR (v_feedback_payload->>'reviewed_at')::timestamptz <=
              (v_payload->'meta'->>'created_at')::timestamptz
           OR (v_feedback_payload->>'reviewed_at')::timestamptz >
              (v_payload->'snapshot'->>'as_of')::timestamptz THEN
            RAISE EXCEPTION 'attempt 2 context feedback or timeline is invalid'
                USING ERRCODE = '23514';
        END IF;
    ELSIF v_payload->'feedback' IS DISTINCT FROM 'null'::jsonb THEN
        RAISE EXCEPTION 'attempt 1 context feedback is invalid' USING ERRCODE = '23514';
    END IF;
    IF clock_timestamp() > v_bundle.deadline THEN
        RAISE EXCEPTION 'proposal context deadline expired' USING ERRCODE = '57014';
    END IF;
    INSERT INTO public.proposal_contexts (
        context_id, bundle_id, attempt, snapshot_hash, previous_context_id,
        superseded_proposal_id, superseded_proposal_hash, feedback_id, context_hash,
        payload_hash, payload
    ) VALUES (
        p_context_id, p_bundle_id, p_attempt, p_snapshot_hash, p_previous_context_id,
        p_superseded_proposal_id, p_superseded_proposal_hash, p_feedback_id, p_context_hash,
        p_payload_hash, p_payload
    ) ON CONFLICT (context_id) DO NOTHING;
    SELECT * INTO v_existing FROM public.proposal_contexts WHERE context_id = p_context_id;
    IF v_existing.bundle_id IS DISTINCT FROM p_bundle_id
       OR v_existing.attempt IS DISTINCT FROM p_attempt
       OR v_existing.snapshot_hash IS DISTINCT FROM p_snapshot_hash
       OR v_existing.previous_context_id IS DISTINCT FROM p_previous_context_id
       OR v_existing.superseded_proposal_id IS DISTINCT FROM p_superseded_proposal_id
       OR v_existing.superseded_proposal_hash IS DISTINCT FROM p_superseded_proposal_hash
       OR v_existing.feedback_id IS DISTINCT FROM p_feedback_id
       OR v_existing.context_hash IS DISTINCT FROM p_context_hash
       OR v_existing.payload_hash IS DISTINCT FROM p_payload_hash
       OR v_existing.payload IS DISTINCT FROM p_payload THEN
        RAISE EXCEPTION 'proposal context identity collision' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_proposal_run(
    p_run_id UUID, p_context_id UUID, p_bundle_id UUID, p_bundle_hash TEXT
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing public.proposal_runs%ROWTYPE;
    v_bound public.proposal_contexts%ROWTYPE;
    v_stored_bundle_hash TEXT;
BEGIN
    SELECT bundle_hash INTO v_stored_bundle_hash FROM public.research_bundles
      WHERE bundle_id = p_bundle_id FOR SHARE;
    IF NOT FOUND OR v_stored_bundle_hash <> p_bundle_hash THEN
        RAISE EXCEPTION 'research bundle is unavailable for the proposal run'
            USING ERRCODE = '23503';
    END IF;
    IF clock_timestamp() > (
        SELECT deadline FROM public.research_bundles WHERE bundle_id = p_bundle_id
    ) THEN
        RAISE EXCEPTION 'proposal run deadline expired' USING ERRCODE = '57014';
    END IF;
    SELECT * INTO v_bound FROM public.proposal_contexts
      WHERE context_id = p_context_id FOR SHARE;
    IF NOT FOUND OR v_bound.bundle_id <> p_bundle_id THEN
        RAISE EXCEPTION 'proposal context is unavailable for the proposal run'
            USING ERRCODE = '23503';
    END IF;
    IF p_run_id <> public.p3d_derive_run_id(
        'seven-lens.p3d.proposal-run.v1', p_context_id::text
    ) THEN
        RAISE EXCEPTION 'proposal run identity is invalid' USING ERRCODE = '23514';
    END IF;
    IF clock_timestamp() > (
        SELECT deadline FROM public.research_bundles WHERE bundle_id = p_bundle_id
    ) THEN
        RAISE EXCEPTION 'proposal run deadline expired' USING ERRCODE = '57014';
    END IF;
    INSERT INTO public.proposal_runs (run_id, context_id, bundle_id, bundle_hash)
    VALUES (p_run_id, p_context_id, p_bundle_id, p_bundle_hash)
    ON CONFLICT (run_id) DO NOTHING;
    SELECT * INTO v_existing FROM public.proposal_runs WHERE run_id = p_run_id;
    IF v_existing.context_id IS DISTINCT FROM p_context_id
       OR v_existing.bundle_id IS DISTINCT FROM p_bundle_id
       OR v_existing.bundle_hash IS DISTINCT FROM p_bundle_hash THEN
        RAISE EXCEPTION 'proposal run identity collision' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.advance_proposal_stage(
    p_run_id UUID, p_expected TEXT, p_stage TEXT, p_result_hash TEXT, p_payload TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing_hash TEXT;
    v_inserted_count INTEGER;
    v_run public.proposal_runs%ROWTYPE;
    v_payload JSONB;
    v_debate_id UUID;
    v_debate_context UUID;
    v_debate_bundle UUID;
    v_existing_debate_hash TEXT;
    v_debate_contract_hash TEXT;
    v_debate_expected_context UUID;
    v_debate_expected_run UUID;
    v_argument JSONB;
    v_argument_ordinal BIGINT;
    v_proposal_id UUID;
    v_proposal_context UUID;
    v_proposal_bundle UUID;
    v_proposal_attempt INTEGER;
    v_proposal_superseded UUID;
    v_proposal_status TEXT;
    v_superseded_row public.portfolio_proposals%ROWTYPE;
    v_context public.proposal_contexts%ROWTYPE;
    v_context_payload JSONB;
    v_request JSONB;
BEGIN
    IF (p_expected, p_stage) NOT IN (
        ('PLANNED', 'RISK_DEBATE'), ('RISK_DEBATE', 'PROPOSAL'),
        ('PROPOSAL', 'COMPLETE'),
        ('PLANNED', 'INVALID'), ('RISK_DEBATE', 'INVALID'), ('PROPOSAL', 'INVALID'),
        ('PLANNED', 'EXPIRED'), ('RISK_DEBATE', 'EXPIRED'), ('PROPOSAL', 'EXPIRED')
    ) THEN
        RAISE EXCEPTION 'proposal stage transition is not legal' USING ERRCODE = '55000';
    END IF;
    IF length(p_payload) NOT BETWEEN 1 AND 262144 THEN
        RAISE EXCEPTION 'proposal stage payload is outside its bound' USING ERRCODE = '23514';
    END IF;
    IF encode(digest(convert_to(p_payload, 'UTF8'), 'sha256'), 'hex') <> p_result_hash THEN
        RAISE EXCEPTION 'proposal stage result hash does not match payload'
            USING ERRCODE = '23514';
    END IF;
    IF p_stage IN ('RISK_DEBATE', 'PROPOSAL') AND NOT (p_payload IS JSON) THEN
        RAISE EXCEPTION 'proposal stage payload is not valid JSON' USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM public.proposal_runs WHERE run_id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposal run does not exist' USING ERRCODE = '23503';
    END IF;
    SELECT * INTO v_run FROM public.proposal_runs WHERE run_id = p_run_id;
    IF p_stage NOT IN ('INVALID', 'EXPIRED')
       AND clock_timestamp() > (
           SELECT deadline FROM public.research_bundles WHERE bundle_id = v_run.bundle_id
       ) THEN
        RAISE EXCEPTION 'proposal stage deadline expired' USING ERRCODE = '57014';
    END IF;
    SELECT result_hash INTO v_existing_hash FROM public.proposal_stage_results
      WHERE run_id = p_run_id AND stage = p_stage;
    IF v_existing_hash IS NOT NULL THEN
        IF v_existing_hash <> p_result_hash THEN
            RAISE EXCEPTION 'proposal stage immutable result changed' USING ERRCODE = '23514';
        END IF;
        UPDATE public.proposal_stage_results SET attempt = attempt + 1
          WHERE run_id = p_run_id AND stage = p_stage;
        RETURN FALSE;
    END IF;
    IF p_stage = 'RISK_DEBATE' THEN
        IF p_payload IS DISTINCT FROM public.p3d_canonical_json(p_payload::json) THEN
            RAISE EXCEPTION 'risk debate payload is not canonical JSON'
                USING ERRCODE = '23514';
        END IF;
        v_payload := p_payload::jsonb;
        IF jsonb_typeof(v_payload) <> 'object'
           OR NOT (v_payload ?& ARRAY[
               'meta', 'debate_id', 'context_id', 'bundle_id', 'bundle_hash',
               'arguments', 'complete', 'debate_hash'
           ])
           OR (v_payload - ARRAY[
               'meta', 'debate_id', 'context_id', 'bundle_id', 'bundle_hash',
               'arguments', 'complete', 'debate_hash'
           ]) <> '{}'::jsonb
           OR jsonb_typeof(v_payload->'meta') <> 'object'
           OR NOT ((v_payload->'meta') ?& ARRAY[
               'schema_version', 'run_id', 'created_at', 'producer_version'
           ])
           OR ((v_payload->'meta') - ARRAY[
               'schema_version', 'run_id', 'created_at', 'producer_version'
           ]) <> '{}'::jsonb
           OR EXISTS (
               SELECT 1 FROM jsonb_each(v_payload->'meta') AS meta_field
               WHERE jsonb_typeof(meta_field.value) <> 'string'
           )
           OR v_payload->>'debate_hash' !~ '^[0-9a-f]{64}$'
           OR v_payload->'complete' <> 'true'::jsonb
           OR jsonb_typeof(v_payload->'arguments') <> 'array'
           OR jsonb_array_length(v_payload->'arguments') <> 6 THEN
            RAISE EXCEPTION 'risk debate payload is malformed' USING ERRCODE = '23514';
        END IF;
        IF v_payload->>'debate_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_payload->>'context_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_payload->>'bundle_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_payload->'meta'->>'run_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
            RAISE EXCEPTION 'risk debate UUID text is invalid' USING ERRCODE = '23514';
        END IF;
        v_debate_id := (v_payload->>'debate_id')::uuid;
        v_debate_context := (v_payload->>'context_id')::uuid;
        v_debate_bundle := (v_payload->>'bundle_id')::uuid;
        v_debate_contract_hash := v_payload->>'debate_hash';
        IF v_payload->>'debate_id' IS DISTINCT FROM v_debate_id::text
           OR v_payload->>'context_id' IS DISTINCT FROM v_debate_context::text
           OR v_payload->>'bundle_id' IS DISTINCT FROM v_debate_bundle::text
           OR v_payload->'meta'->>'run_id' IS DISTINCT FROM
              ((v_payload->'meta'->>'run_id')::uuid)::text THEN
            RAISE EXCEPTION 'risk debate UUID text is not canonical'
                USING ERRCODE = '23514';
        END IF;
        IF encode(
               digest(
                   convert_to(
                       public.p3d_canonical_json((v_payload - 'debate_hash')::json),
                       'UTF8'
                   ),
                   'sha256'
               ),
               'hex'
           ) IS DISTINCT FROM v_debate_contract_hash THEN
            RAISE EXCEPTION 'risk debate content hash is invalid' USING ERRCODE = '23514';
        END IF;
        IF v_debate_id <> public.p3d_derive_run_id(
            'seven-lens.p3d.debate.v1', v_debate_context::text
        ) THEN
            RAISE EXCEPTION 'risk debate identity is invalid' USING ERRCODE = '23514';
        END IF;
        SELECT * INTO v_context FROM public.proposal_contexts
          WHERE context_id = v_run.context_id FOR SHARE;
        v_context_payload := v_context.payload::jsonb;
        v_debate_expected_context := CASE
            WHEN v_context.attempt = 1 THEN v_context.context_id
            ELSE v_context.previous_context_id
        END;
        SELECT run_id INTO v_debate_expected_run FROM public.proposal_runs
          WHERE context_id = v_debate_expected_context;
        IF v_debate_expected_run IS NULL
           OR v_debate_bundle <> v_run.bundle_id
           OR v_payload->>'bundle_hash' <> v_run.bundle_hash
           OR v_debate_context <> v_debate_expected_context
           OR (v_payload->'meta'->>'run_id')::uuid <> v_debate_expected_run
           OR v_payload->'meta'->>'schema_version' IS DISTINCT FROM '1.0.0'
           OR v_payload->'meta'->>'created_at' IS DISTINCT FROM
              v_context_payload->'meta'->>'created_at'
           OR v_payload->'meta'->>'producer_version' IS DISTINCT FROM
              v_context_payload->'meta'->>'producer_version'
           OR (SELECT count(DISTINCT argument->>'argument_id')
               FROM jsonb_array_elements(v_payload->'arguments') AS argument) <> 6 THEN
            RAISE EXCEPTION 'risk debate binds a foreign bundle' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.proposal_contexts WHERE context_id = v_debate_context
        ) THEN
            RAISE EXCEPTION 'risk debate context is unregistered' USING ERRCODE = '23503';
        END IF;
        FOR v_argument, v_argument_ordinal IN
            SELECT value, ordinality
            FROM jsonb_array_elements(v_payload->'arguments') WITH ORDINALITY
        LOOP
            IF v_argument->>'argument_id' !~
                  '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR v_argument->'meta'->>'run_id' !~
                  '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
                RAISE EXCEPTION 'risk debate argument UUID text is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF jsonb_typeof(v_argument) <> 'object'
               OR NOT (v_argument ?& ARRAY[
                   'meta', 'argument_id', 'context_id', 'bundle_id', 'bundle_hash',
                   'viewpoint', 'round_number', 'argument', 'evidence_refs', 'producer_version'
               ])
               OR (v_argument - ARRAY[
                   'meta', 'argument_id', 'context_id', 'bundle_id', 'bundle_hash',
                   'viewpoint', 'round_number', 'argument', 'evidence_refs', 'producer_version'
               ]) <> '{}'::jsonb
               OR jsonb_typeof(v_argument->'meta') <> 'object'
               OR jsonb_typeof(v_argument->'round_number') <> 'number'
               OR v_argument->>'round_number' !~ '^[12]$'
               OR (v_argument->'meta') IS DISTINCT FROM (v_payload->'meta')
               OR jsonb_typeof(v_argument->'argument') <> 'string'
               OR NOT public.p3d_text_is_safe(v_argument->>'argument')
               OR v_argument->>'context_id' <> v_debate_context::text
               OR v_argument->>'bundle_id' <> v_debate_bundle::text
               OR v_argument->>'bundle_hash' <> v_run.bundle_hash
               OR (v_argument->'meta'->>'run_id')::uuid <> v_debate_expected_run
               OR v_argument->'meta'->>'run_id' IS DISTINCT FROM
                  ((v_argument->'meta'->>'run_id')::uuid)::text
               OR octet_length(v_argument->>'argument') NOT BETWEEN 1 AND 2048
               OR jsonb_typeof(v_argument->'evidence_refs') <> 'array'
               OR jsonb_array_length(v_argument->'evidence_refs') NOT BETWEEN 1 AND 32
               OR (SELECT count(*) FROM jsonb_array_elements(v_argument->'evidence_refs')) <>
                  (SELECT count(DISTINCT ref)
                   FROM jsonb_array_elements_text(v_argument->'evidence_refs') AS refs(ref))
               OR EXISTS (
                   SELECT 1
                   FROM jsonb_array_elements(v_argument->'evidence_refs') AS evidence(value)
                   WHERE jsonb_typeof(evidence.value) <> 'string'
                      OR evidence.value #>> '{}' !~
                         '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
                      OR NOT public.p3d_text_is_safe(evidence.value #>> '{}')
               )
               OR v_argument->>'producer_version' IS DISTINCT FROM
                  v_payload->'meta'->>'producer_version'
               OR (v_argument->>'argument_id')::uuid <> public.p3d_derive_run_id(
                   'seven-lens.p3d.risk-argument.v1',
                   v_debate_context::text, v_argument->>'viewpoint',
                   v_argument->>'round_number'
               )
               OR v_argument->>'argument_id' IS DISTINCT FROM
                  ((v_argument->>'argument_id')::uuid)::text
               OR (v_argument->>'round_number')::integer <>
                  (CASE WHEN v_argument_ordinal <= 3 THEN 1 ELSE 2 END)
               OR v_argument->>'viewpoint' <>
                  (ARRAY['AGGRESSIVE','CONSERVATIVE','NEUTRAL'])[
                      (((v_argument_ordinal - 1) % 3) + 1)::integer
                  ]
               OR EXISTS (
                   SELECT 1 FROM jsonb_array_elements_text(v_argument->'evidence_refs') AS ref
                   WHERE NOT ((v_context_payload->'citation_ids') ? ref)
               ) THEN
                RAISE EXCEPTION 'risk debate argument identity is invalid'
                    USING ERRCODE = '23514';
            END IF;
        END LOOP;
        IF clock_timestamp() > (
            SELECT deadline FROM public.research_bundles WHERE bundle_id = v_run.bundle_id
        ) THEN
            RAISE EXCEPTION 'proposal stage deadline expired' USING ERRCODE = '57014';
        END IF;
        UPDATE public.proposal_runs
          SET current_stage = p_stage, updated_at = clock_timestamp()
          WHERE run_id = p_run_id AND current_stage = p_expected;
        GET DIAGNOSTICS v_inserted_count = ROW_COUNT;
        IF v_inserted_count <> 1 THEN
            RAISE EXCEPTION 'proposal stage transition is out of order' USING ERRCODE = '55000';
        END IF;
        INSERT INTO public.risk_debates (debate_id, context_id, bundle_id, debate_hash)
        VALUES (v_debate_id, v_debate_context, v_debate_bundle, v_debate_contract_hash)
        ON CONFLICT (debate_id) DO NOTHING;
        SELECT debate_hash INTO v_existing_debate_hash FROM public.risk_debates
          WHERE debate_id = v_debate_id;
        IF v_existing_debate_hash IS DISTINCT FROM v_debate_contract_hash THEN
            RAISE EXCEPTION 'risk debate identity collision' USING ERRCODE = '23514';
        END IF;
        INSERT INTO public.proposal_stage_results (run_id, stage, result_hash, payload)
        VALUES (p_run_id, p_stage, p_result_hash, p_payload);
        RETURN TRUE;
    ELSIF p_stage = 'PROPOSAL' THEN
        IF p_payload IS DISTINCT FROM public.p3d_canonical_json(p_payload::json) THEN
            RAISE EXCEPTION 'proposal payload is not canonical JSON'
                USING ERRCODE = '23514';
        END IF;
        v_payload := p_payload::jsonb;
        IF jsonb_typeof(v_payload) <> 'object'
           OR NOT (v_payload ?& ARRAY[
               'meta', 'proposal_id', 'attempt', 'context_id', 'context_hash',
               'bundle_id', 'bundle_hash', 'superseded_proposal_id', 'universe_hash',
               'snapshot_hash', 'window', 'requests', 'graph_version', 'prompt_version',
               'model_version', 'provider_version', 'data_version', 'memory_version',
               'expiration_at', 'status'
           ])
           OR (v_payload - ARRAY[
               'meta', 'proposal_id', 'attempt', 'context_id', 'context_hash',
               'bundle_id', 'bundle_hash', 'superseded_proposal_id', 'universe_hash',
               'snapshot_hash', 'window', 'requests', 'graph_version', 'prompt_version',
               'model_version', 'provider_version', 'data_version', 'memory_version',
               'expiration_at', 'status'
           ]) <> '{}'::jsonb
           OR jsonb_typeof(v_payload->'meta') <> 'object'
           OR NOT ((v_payload->'meta') ?& ARRAY[
               'schema_version', 'run_id', 'created_at', 'producer_version'
           ])
           OR ((v_payload->'meta') - ARRAY[
               'schema_version', 'run_id', 'created_at', 'producer_version'
           ]) <> '{}'::jsonb
           OR EXISTS (
               SELECT 1 FROM jsonb_each(v_payload->'meta') AS meta_field
               WHERE jsonb_typeof(meta_field.value) <> 'string'
           )
           OR jsonb_typeof(v_payload->'attempt') <> 'number'
           OR v_payload->>'attempt' !~ '^[12]$'
           OR jsonb_typeof(v_payload->'requests') <> 'array'
           OR jsonb_typeof(v_payload->'expiration_at') <> 'string'
           OR jsonb_typeof(v_payload->'status') <> 'string'
           OR v_payload->>'expiration_at' !~
              '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$' THEN
            RAISE EXCEPTION 'proposal stage payload is malformed' USING ERRCODE = '23514';
        END IF;
        IF v_payload->>'proposal_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_payload->>'context_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR v_payload->>'bundle_id' !~
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR (
               v_payload->'superseded_proposal_id' <> 'null'::jsonb
               AND v_payload->>'superseded_proposal_id' !~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           ) THEN
            RAISE EXCEPTION 'proposal UUID text is invalid' USING ERRCODE = '23514';
        END IF;
        v_proposal_id := (v_payload->>'proposal_id')::uuid;
        v_proposal_context := (v_payload->>'context_id')::uuid;
        v_proposal_bundle := (v_payload->>'bundle_id')::uuid;
        v_proposal_attempt := (v_payload->>'attempt')::integer;
        v_proposal_status := v_payload->>'status';
        IF v_proposal_id <> public.p3d_derive_run_id(
            'seven-lens.p3d.proposal.v1', v_proposal_context::text
        ) THEN
            RAISE EXCEPTION 'proposal identity is invalid' USING ERRCODE = '23514';
        END IF;
        IF (v_payload->>'superseded_proposal_id') IS NULL THEN
            v_proposal_superseded := NULL;
        ELSE
            v_proposal_superseded := (v_payload->>'superseded_proposal_id')::uuid;
        END IF;
        IF v_payload->>'proposal_id' IS DISTINCT FROM v_proposal_id::text
           OR v_payload->>'context_id' IS DISTINCT FROM v_proposal_context::text
           OR v_payload->>'bundle_id' IS DISTINCT FROM v_proposal_bundle::text
           OR (
               v_proposal_superseded IS NOT NULL
               AND v_payload->>'superseded_proposal_id' IS DISTINCT FROM
                   v_proposal_superseded::text
           ) THEN
            RAISE EXCEPTION 'proposal UUID text is not canonical' USING ERRCODE = '23514';
        END IF;
        IF v_proposal_status NOT IN ('VALID', 'INVALID', 'ABSTAIN')
           OR v_proposal_attempt NOT IN (1, 2)
           OR (v_proposal_attempt = 1) <> (v_proposal_superseded IS NULL) THEN
            RAISE EXCEPTION 'proposal stage payload is malformed' USING ERRCODE = '23514';
        END IF;
        SELECT * INTO v_context FROM public.proposal_contexts
          WHERE context_id = v_run.context_id FOR SHARE;
        v_context_payload := v_context.payload::jsonb;
        IF v_proposal_context <> v_run.context_id
           OR v_proposal_bundle <> v_run.bundle_id
           OR v_proposal_attempt <> v_context.attempt
           OR v_proposal_superseded IS DISTINCT FROM v_context.superseded_proposal_id
           OR v_payload->>'context_hash' <> v_context.context_hash
           OR v_payload->>'bundle_hash' <> v_run.bundle_hash
           OR v_payload->>'universe_hash' <> v_context_payload->>'universe_hash'
           OR v_payload->>'snapshot_hash' <> v_context.snapshot_hash
           OR v_payload->>'window' <> v_context_payload->>'window'
           OR v_payload->'meta'->>'run_id' <> v_proposal_id::text
           OR v_payload->'meta'->>'created_at' <> v_context_payload->'meta'->>'created_at'
           OR v_payload->'meta'->>'producer_version' <>
              v_context_payload->'meta'->>'producer_version'
           OR v_payload->'meta'->>'schema_version' IS DISTINCT FROM '1.0.0'
           OR v_payload->>'graph_version' IS DISTINCT FROM
              v_context_payload->>'graph_version'
           OR v_payload->>'prompt_version' IS DISTINCT FROM
              v_context_payload->>'prompt_version'
           OR v_payload->>'model_version' IS DISTINCT FROM
              v_context_payload->>'model_version'
           OR v_payload->>'provider_version' IS DISTINCT FROM
              v_context_payload->>'provider_version'
           OR v_payload->>'data_version' IS DISTINCT FROM
              v_context_payload->>'data_version'
           OR v_payload->>'memory_version' IS DISTINCT FROM
              v_context_payload->>'memory_version'
           OR (v_payload->>'expiration_at')::timestamptz >
              (v_context_payload->>'deadline')::timestamptz
           OR jsonb_typeof(v_payload->'requests') <> 'array'
           OR jsonb_array_length(v_payload->'requests') > 27
           OR (v_proposal_status = 'VALID' AND jsonb_array_length(v_payload->'requests') = 0)
           OR (v_proposal_status <> 'VALID' AND jsonb_array_length(v_payload->'requests') <> 0)
           OR (SELECT count(*) FROM jsonb_array_elements(v_payload->'requests')) <>
              (SELECT count(DISTINCT request->>'symbol')
               FROM jsonb_array_elements(v_payload->'requests') AS request) THEN
            RAISE EXCEPTION 'proposal binds a foreign authority' USING ERRCODE = '23514';
        END IF;
        FOR v_request IN SELECT value FROM jsonb_array_elements(v_payload->'requests') LOOP
            IF jsonb_typeof(v_request) <> 'object'
               OR NOT (v_request ?& ARRAY[
                   'symbol', 'action', 'side', 'target_weight', 'confidence',
                   'evidence_refs', 'reason_codes', 'invalidators', 'same_day_exit_reason'
               ])
               OR (v_request - ARRAY[
                   'symbol', 'action', 'side', 'target_weight', 'confidence',
                   'evidence_refs', 'reason_codes', 'invalidators', 'same_day_exit_reason'
               ]) <> '{}'::jsonb
               OR EXISTS (
                   SELECT 1 FROM jsonb_each(v_request) AS scalar_field
                   WHERE scalar_field.key IN (
                       'symbol', 'action', 'side', 'target_weight', 'confidence'
                   ) AND jsonb_typeof(scalar_field.value) <> 'string'
               )
               OR NOT ((v_context_payload->'allowed_symbols') ? (v_request->>'symbol'))
               OR v_request->>'action' NOT IN ('OPEN','INCREASE','REDUCE','CLOSE','HOLD')
               OR v_request->>'side' NOT IN ('LONG','SHORT','FLAT')
               OR v_request->>'target_weight' !~ '^-?(0|0\.[0-9]{6})$'
               OR abs((v_request->>'target_weight')::numeric) > 0.15
               OR v_request->>'target_weight' = '-0.000000'
               OR v_request->>'confidence' !~ '^(0|1)\.[0-9]{4}$'
               OR (v_request->>'confidence')::numeric > 1
               OR ((v_request->>'confidence')::numeric < 0.6500
                   AND v_request->>'action' <> 'HOLD')
               OR jsonb_typeof(v_request->'evidence_refs') <> 'array'
               OR jsonb_array_length(v_request->'evidence_refs') NOT BETWEEN 1 AND 32
               OR (SELECT count(*) FROM jsonb_array_elements(v_request->'evidence_refs')) <>
                  (SELECT count(DISTINCT ref)
                   FROM jsonb_array_elements_text(v_request->'evidence_refs') AS refs(ref))
               OR EXISTS (
                   SELECT 1
                   FROM jsonb_array_elements(v_request->'evidence_refs') AS evidence(value)
                   WHERE jsonb_typeof(evidence.value) <> 'string'
                      OR evidence.value #>> '{}' !~
                         '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
                      OR NOT public.p3d_text_is_safe(evidence.value #>> '{}')
               )
               OR jsonb_typeof(v_request->'reason_codes') <> 'array'
               OR jsonb_array_length(v_request->'reason_codes') NOT BETWEEN 1 AND 6
               OR (SELECT count(*) FROM jsonb_array_elements(v_request->'reason_codes')) <>
                  (SELECT count(DISTINCT reason)
                   FROM jsonb_array_elements_text(v_request->'reason_codes') AS reasons(reason))
               OR EXISTS (
                   SELECT 1
                   FROM jsonb_array_elements_text(v_request->'reason_codes') AS reasons(reason)
                   WHERE reasons.reason NOT IN (
                       'TECHNICAL', 'FUNDAMENTAL', 'NEWS', 'SENTIMENT',
                       'VALUATION', 'REBALANCE'
                   )
               )
               OR jsonb_typeof(v_request->'invalidators') <> 'array'
               OR jsonb_array_length(v_request->'invalidators') > 32
               OR (SELECT count(*) FROM jsonb_array_elements(v_request->'invalidators')) <>
                  (SELECT count(DISTINCT invalidator)
                   FROM jsonb_array_elements_text(v_request->'invalidators')
                        AS invalidators(invalidator))
               OR EXISTS (
                   SELECT 1
                   FROM jsonb_array_elements(v_request->'invalidators') AS invalidator(value)
                   WHERE jsonb_typeof(invalidator.value) <> 'string'
                      OR octet_length(invalidator.value #>> '{}') NOT BETWEEN 1 AND 2048
                      OR NOT public.p3d_text_is_safe(invalidator.value #>> '{}')
               )
               OR EXISTS (
                   SELECT 1
                   FROM jsonb_array_elements_text(v_request->'invalidators') AS entries(item)
                   WHERE length(entries.item) NOT BETWEEN 1 AND 2048
               )
               OR (v_request->>'side' = 'LONG'
                   AND (v_request->>'target_weight')::numeric <= 0)
               OR (v_request->>'side' = 'SHORT'
                   AND (v_request->>'target_weight')::numeric >= 0)
               OR (v_request->>'side' = 'FLAT'
                   AND (v_request->>'target_weight')::numeric <> 0)
               OR (v_request->>'action' = 'CLOSE'
                   AND (v_request->>'side' <> 'FLAT'
                        OR (v_request->>'target_weight')::numeric <> 0))
               OR (v_request->>'action' IN ('OPEN','INCREASE')
                   AND (v_request->>'side' = 'FLAT'
                        OR (v_request->>'target_weight')::numeric = 0))
               OR (v_context_payload->>'window' = 'EMERGENCY'
                   AND v_request->>'action' IN ('OPEN','INCREASE'))
               OR ((v_request->>'same_day_exit_reason') IS NOT NULL
                   AND v_request->>'same_day_exit_reason' NOT IN (
                       'DOWNSIDE_BAND_EXCEEDED', 'THESIS_INVALIDATED',
                       'MATERIAL_NEW_EVENT', 'BORROW_LIQUIDITY_ANOMALY', 'HARD_RISK_TRIGGER'
                   ))
               OR ((v_request->>'same_day_exit_reason') IS NOT NULL
                   AND v_request->>'action' NOT IN ('REDUCE','CLOSE'))
               OR EXISTS (
                   SELECT 1 FROM jsonb_array_elements_text(v_request->'evidence_refs') AS ref
                   WHERE NOT ((v_context_payload->'citation_ids') ? ref)
               ) THEN
                RAISE EXCEPTION 'proposal request is outside the frozen context'
                    USING ERRCODE = '23514';
            END IF;
        END LOOP;
        IF v_proposal_attempt = 2 THEN
            SELECT * INTO v_superseded_row FROM public.portfolio_proposals
              WHERE proposal_id = v_proposal_superseded;
            IF NOT FOUND OR v_superseded_row.attempt <> 1
               OR v_superseded_row.bundle_id <> v_run.bundle_id THEN
                RAISE EXCEPTION 'attempt 2 proposal supersedes an unknown proposal'
                    USING ERRCODE = '23514';
            END IF;
            IF v_superseded_row.proposal_hash <> v_context.superseded_proposal_hash THEN
                RAISE EXCEPTION 'attempt 2 proposal superseded hash is invalid'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF clock_timestamp() > (
            SELECT deadline FROM public.research_bundles WHERE bundle_id = v_run.bundle_id
        ) THEN
            RAISE EXCEPTION 'proposal stage deadline expired' USING ERRCODE = '57014';
        END IF;
        UPDATE public.proposal_runs
          SET current_stage = p_stage, updated_at = clock_timestamp()
          WHERE run_id = p_run_id AND current_stage = p_expected;
        GET DIAGNOSTICS v_inserted_count = ROW_COUNT;
        IF v_inserted_count <> 1 THEN
            RAISE EXCEPTION 'proposal stage transition is out of order' USING ERRCODE = '55000';
        END IF;
        INSERT INTO public.portfolio_proposals (
            proposal_id, context_id, bundle_id, attempt, superseded_proposal_id,
            status, proposal_hash
        ) VALUES (
            v_proposal_id, v_proposal_context, v_proposal_bundle, v_proposal_attempt,
            v_proposal_superseded, v_proposal_status, p_result_hash
        );
        INSERT INTO public.proposal_stage_results (run_id, stage, result_hash, payload)
        VALUES (p_run_id, p_stage, p_result_hash, p_payload);
        RETURN TRUE;
    END IF;
    IF p_payload IS DISTINCT FROM lower(p_stage) THEN
        RAISE EXCEPTION 'terminal proposal stage payload is not canonical'
            USING ERRCODE = '23514';
    END IF;
    IF p_stage = 'COMPLETE' AND clock_timestamp() > (
        SELECT deadline FROM public.research_bundles WHERE bundle_id = v_run.bundle_id
    ) THEN
        RAISE EXCEPTION 'proposal stage deadline expired' USING ERRCODE = '57014';
    END IF;
    UPDATE public.proposal_runs
      SET current_stage = p_stage, updated_at = clock_timestamp()
      WHERE run_id = p_run_id AND current_stage = p_expected;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;
    IF v_inserted_count <> 1 THEN
        RAISE EXCEPTION 'proposal stage transition is out of order' USING ERRCODE = '55000';
    END IF;
    INSERT INTO public.proposal_stage_results (run_id, stage, result_hash, payload)
    VALUES (p_run_id, p_stage, p_result_hash, p_payload);
    RETURN TRUE;
END;
$$;

REVOKE ALL ON TABLE public.research_bundles, public.research_bundle_items,
    public.risk_rejection_feedback, public.proposal_contexts, public.proposal_runs,
    public.risk_debates, public.portfolio_proposals, public.proposal_stage_results
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_research_bundle(
    UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, TIMESTAMPTZ, TEXT, TEXT, JSONB, TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_risk_feedback(UUID, UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_proposal_context(
    UUID, UUID, INTEGER, TEXT, UUID, UUID, TEXT, UUID, TEXT, TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.create_proposal_run(UUID, UUID, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.advance_proposal_stage(UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guard_proposal_run_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guard_proposal_stage_result_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p3d_canonical_json(JSON) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p3d_derive_run_id(TEXT, TEXT[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p3d_text_is_safe(TEXT) FROM PUBLIC;

-- Runtime authority is an explicit function allowlist.  Remove PostgreSQL's
-- default PUBLIC EXECUTE (including pgcrypto) from the entire authoritative schema.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
