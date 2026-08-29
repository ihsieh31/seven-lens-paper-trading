-- 0022: P3-E generic analysis route identity.
--
-- Widens the P3-E model-call claim/audit route identity from the single Agnes
-- literal to a bounded union: the exact historical Agnes route, or a generic
-- OpenAI-compatible route whose model id, hash-bound policy id, and explicit
-- route_config_hash column must all agree.  Historical rows keep their exact
-- bytes and append-only history; the new column is backfilled from the known
-- canonical legacy route material (never from any operator configuration).
--
-- Legacy route material (canonical): base_url = https://apihub.agnes-ai.com/v1,
-- model_id = agnes-2.5-flash, package-owned analysis-route policy v1 material.

-- The append-only guard only legalises CLAIMED->CLOSED transitions, so the
-- one-time provenance backfill below must run with the row-write guard
-- disabled.  This migration executes as the table owner inside a single
-- transaction that holds ACCESS EXCLUSIVE on each table for the whole
-- backfill, so no concurrent session can observe or exploit the disabled
-- window; the guard is re-enabled before any other statement can commit.
ALTER TABLE public.model_call_claims DISABLE TRIGGER model_call_claims_guard_write;
ALTER TABLE public.model_call_claims ADD COLUMN route_config_hash TEXT;
UPDATE public.model_call_claims
   SET route_config_hash = 'f9a3ff8737626e29f4e42e053a37046051a3ee813d4740064870daf14dd41a60';
ALTER TABLE public.model_call_claims ALTER COLUMN route_config_hash SET NOT NULL;
ALTER TABLE public.model_call_claims ENABLE TRIGGER model_call_claims_guard_write;

ALTER TABLE public.model_call_claims DROP CONSTRAINT model_call_claims_provider_check;
ALTER TABLE public.model_call_claims DROP CONSTRAINT model_call_claims_model_check;
ALTER TABLE public.model_call_claims DROP CONSTRAINT model_call_claims_endpoint_policy_id_check;
ALTER TABLE public.model_call_claims ADD CONSTRAINT model_call_claims_route_identity_check CHECK (
    (provider = 'AGNES'
     AND model = 'agnes-2.5-flash'
     AND endpoint_policy_id = 'p3e-agnes-2.5-flash-only-v1'
     AND route_config_hash = 'f9a3ff8737626e29f4e42e053a37046051a3ee813d4740064870daf14dd41a60')
    OR (provider = 'OPENAI_COMPATIBLE'
        AND length(model) BETWEEN 1 AND 128
        AND model ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}(/[A-Za-z0-9][A-Za-z0-9._:-]{0,126})?$'
        AND model !~ '(^|/)\.\.?(/|$)'
        AND endpoint_policy_id ~ '^analysis-route-v1:[0-9a-f]{64}$'
        AND route_config_hash = substring(endpoint_policy_id from 19))
);

ALTER TABLE public.model_call_audits DISABLE TRIGGER model_call_audits_guard_write;
ALTER TABLE public.model_call_audits ADD COLUMN route_config_hash TEXT;
UPDATE public.model_call_audits
   SET route_config_hash = 'f9a3ff8737626e29f4e42e053a37046051a3ee813d4740064870daf14dd41a60';
ALTER TABLE public.model_call_audits ALTER COLUMN route_config_hash SET NOT NULL;
ALTER TABLE public.model_call_audits ENABLE TRIGGER model_call_audits_guard_write;

ALTER TABLE public.model_call_audits DROP CONSTRAINT model_call_audits_provider_check;
ALTER TABLE public.model_call_audits DROP CONSTRAINT model_call_audits_model_check;
ALTER TABLE public.model_call_audits DROP CONSTRAINT model_call_audits_endpoint_policy_id_check;
ALTER TABLE public.model_call_audits ADD CONSTRAINT model_call_audits_route_identity_check CHECK (
    (provider = 'AGNES'
     AND model = 'agnes-2.5-flash'
     AND endpoint_policy_id = 'p3e-agnes-2.5-flash-only-v1'
     AND route_config_hash = 'f9a3ff8737626e29f4e42e053a37046051a3ee813d4740064870daf14dd41a60')
    OR (provider = 'OPENAI_COMPATIBLE'
        AND length(model) BETWEEN 1 AND 128
        AND model ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}(/[A-Za-z0-9][A-Za-z0-9._:-]{0,126})?$'
        AND model !~ '(^|/)\.\.?(/|$)'
        AND endpoint_policy_id ~ '^analysis-route-v1:[0-9a-f]{64}$'
        AND route_config_hash = substring(endpoint_policy_id from 19))
);

-- The append-only guard must also freeze the derived route hash column.
CREATE OR REPLACE FUNCTION public.guard_model_call_claim_write()
RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR OLD.status <> 'CLAIMED' OR NEW.status <> 'CLOSED'
       OR NEW.closed_at IS NULL OR NEW.closed_at < OLD.claimed_at
       OR NEW.call_id IS DISTINCT FROM OLD.call_id
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.input_id IS DISTINCT FROM OLD.input_id
       OR NEW.context_id IS DISTINCT FROM OLD.context_id
       OR NEW.stage IS DISTINCT FROM OLD.stage
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.round_number IS DISTINCT FROM OLD.round_number
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.model IS DISTINCT FROM OLD.model
       OR NEW.api_flavor IS DISTINCT FROM OLD.api_flavor
       OR NEW.endpoint_policy_id IS DISTINCT FROM OLD.endpoint_policy_id
       OR NEW.route_config_hash IS DISTINCT FROM OLD.route_config_hash
       OR NEW.route_ordinal IS DISTINCT FROM OLD.route_ordinal
       OR NEW.prompt_template_hash IS DISTINCT FROM OLD.prompt_template_hash
       OR NEW.request_envelope_hash IS DISTINCT FROM OLD.request_envelope_hash
       OR NEW.reasoning_requested IS DISTINCT FROM OLD.reasoning_requested
       OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at THEN
        RAISE EXCEPTION 'model-call claim mutation is not legal' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

-- The SECURITY DEFINER claim function derives the route hash from the exact
-- provider/policy pair; signatures are unchanged so callers stay compatible.
CREATE OR REPLACE FUNCTION public.claim_model_call_attempt(
    p_call_id UUID, p_run_id UUID, p_input_id UUID, p_context_id UUID,
    p_stage TEXT, p_role TEXT, p_round_number INTEGER,
    p_provider TEXT, p_model TEXT, p_api_flavor TEXT, p_endpoint_policy_id TEXT,
    p_route_ordinal INTEGER, p_prompt_template_hash TEXT,
    p_request_envelope_hash TEXT, p_reasoning_requested TEXT
) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing public.model_call_claims%ROWTYPE;
    v_route_config_hash TEXT;
BEGIN
    IF p_call_id IS NULL OR p_run_id IS NULL OR p_input_id IS NULL OR p_context_id IS NULL
       OR p_stage IS NULL OR p_role IS NULL OR p_round_number IS NULL
       OR p_provider IS NULL OR p_model IS NULL OR p_api_flavor IS NULL
       OR p_endpoint_policy_id IS NULL OR p_route_ordinal IS NULL
       OR p_prompt_template_hash IS NULL OR p_request_envelope_hash IS NULL
       OR p_reasoning_requested IS NULL THEN
        RAISE EXCEPTION 'model-call claim is incomplete' USING ERRCODE = '23514';
    END IF;
    IF p_provider = 'AGNES'
       AND p_model = 'agnes-2.5-flash'
       AND p_endpoint_policy_id = 'p3e-agnes-2.5-flash-only-v1' THEN
        v_route_config_hash := 'f9a3ff8737626e29f4e42e053a37046051a3ee813d4740064870daf14dd41a60';
    ELSIF p_provider = 'OPENAI_COMPATIBLE'
       AND length(p_model) BETWEEN 1 AND 128
       AND p_model ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}(/[A-Za-z0-9][A-Za-z0-9._:-]{0,126})?$'
       AND p_model !~ '(^|/)\.\.?(/|$)'
       AND p_endpoint_policy_id ~ '^analysis-route-v1:[0-9a-f]{64}$' THEN
        v_route_config_hash := substring(p_endpoint_policy_id from 19);
    ELSE
        RAISE EXCEPTION 'model-call claim route identity is invalid' USING ERRCODE = '23514';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_call_id::text, 0)
    );
    SELECT * INTO v_existing FROM public.model_call_claims WHERE call_id = p_call_id;
    IF FOUND THEN
        IF ROW(
            v_existing.run_id, v_existing.input_id, v_existing.context_id,
            v_existing.stage, v_existing.role, v_existing.round_number,
            v_existing.provider, v_existing.model, v_existing.api_flavor,
            v_existing.endpoint_policy_id, v_existing.route_ordinal,
            v_existing.prompt_template_hash, v_existing.request_envelope_hash,
            v_existing.reasoning_requested
        ) IS DISTINCT FROM ROW(
            p_run_id, p_input_id, p_context_id, p_stage, p_role, p_round_number,
            p_provider, p_model, p_api_flavor, p_endpoint_policy_id, p_route_ordinal,
            p_prompt_template_hash, p_request_envelope_hash, p_reasoning_requested
        ) THEN
            RAISE EXCEPTION 'model-call claim identity collision' USING ERRCODE = '23505';
        END IF;
        RETURN CASE WHEN v_existing.status = 'CLOSED' THEN 'REPLAY' ELSE 'IN_PROGRESS' END;
    END IF;
    INSERT INTO public.model_call_claims (
        call_id, run_id, input_id, context_id, stage, role, round_number,
        provider, model, api_flavor, endpoint_policy_id, route_ordinal,
        prompt_template_hash, request_envelope_hash, reasoning_requested,
        route_config_hash
    ) VALUES (
        p_call_id, p_run_id, p_input_id, p_context_id, p_stage, p_role, p_round_number,
        p_provider, p_model, p_api_flavor, p_endpoint_policy_id, p_route_ordinal,
        p_prompt_template_hash, p_request_envelope_hash, p_reasoning_requested,
        v_route_config_hash
    );
    RETURN 'CLAIMED';
END;
$$;

CREATE OR REPLACE FUNCTION public.register_model_call_attempt(
    p_call_id UUID, p_run_id UUID, p_input_id UUID, p_context_id UUID,
    p_stage TEXT, p_role TEXT, p_round_number INTEGER,
    p_provider TEXT, p_model TEXT, p_api_flavor TEXT, p_endpoint_policy_id TEXT,
    p_route_ordinal INTEGER, p_prompt_template_hash TEXT,
    p_request_envelope_hash TEXT, p_response_hash TEXT,
    p_reasoning_requested TEXT, p_reasoning_effective TEXT,
    p_token_counts_trusted BOOLEAN, p_input_tokens INTEGER, p_output_tokens INTEGER,
    p_latency_ms INTEGER, p_started_at TIMESTAMPTZ, p_completed_at TIMESTAMPTZ,
    p_outcome TEXT, p_error_code TEXT, p_authority_kind TEXT,
    p_authority_hash TEXT, p_authority_payload TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing public.model_call_audits%ROWTYPE;
    v_claim public.model_call_claims%ROWTYPE;
    v_audit_hash TEXT;
    v_route_config_hash TEXT;
BEGIN
    IF p_call_id IS NULL OR p_run_id IS NULL OR p_input_id IS NULL OR p_context_id IS NULL
       OR p_stage IS NULL OR p_role IS NULL OR p_round_number IS NULL
       OR p_provider IS NULL OR p_model IS NULL OR p_api_flavor IS NULL
       OR p_endpoint_policy_id IS NULL OR p_route_ordinal IS NULL
       OR p_prompt_template_hash IS NULL OR p_request_envelope_hash IS NULL
       OR p_reasoning_requested IS NULL OR p_reasoning_effective IS NULL
       OR p_token_counts_trusted IS NULL OR p_latency_ms IS NULL
       OR p_started_at IS NULL OR p_completed_at IS NULL
       OR p_outcome IS NULL OR p_error_code IS NULL THEN
        RAISE EXCEPTION 'model-call audit metadata is incomplete' USING ERRCODE = '23514';
    END IF;
    IF p_call_id <> public.p3d_derive_run_id(
        'seven-lens.p3e.model-call.v1', p_input_id::text, p_context_id::text,
        p_stage, p_role, p_round_number::text, p_route_ordinal::text
    ) THEN
        RAISE EXCEPTION 'model-call audit identity is invalid' USING ERRCODE = '23514';
    END IF;
    IF p_provider = 'AGNES'
       AND p_model = 'agnes-2.5-flash'
       AND p_endpoint_policy_id = 'p3e-agnes-2.5-flash-only-v1' THEN
        v_route_config_hash := 'f9a3ff8737626e29f4e42e053a37046051a3ee813d4740064870daf14dd41a60';
    ELSIF p_provider = 'OPENAI_COMPATIBLE'
       AND length(p_model) BETWEEN 1 AND 128
       AND p_model ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}(/[A-Za-z0-9][A-Za-z0-9._:-]{0,126})?$'
       AND p_model !~ '(^|/)\.\.?(/|$)'
       AND p_endpoint_policy_id ~ '^analysis-route-v1:[0-9a-f]{64}$' THEN
        v_route_config_hash := substring(p_endpoint_policy_id from 19);
    ELSE
        RAISE EXCEPTION 'model-call audit route identity is invalid' USING ERRCODE = '23514';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_call_id::text, 0)
    );
    SELECT * INTO v_claim FROM public.model_call_claims WHERE call_id = p_call_id FOR UPDATE;
    IF NOT FOUND OR ROW(
            v_claim.run_id, v_claim.input_id, v_claim.context_id,
            v_claim.stage, v_claim.role, v_claim.round_number,
            v_claim.provider, v_claim.model, v_claim.api_flavor,
            v_claim.endpoint_policy_id, v_claim.route_ordinal,
            v_claim.prompt_template_hash, v_claim.request_envelope_hash,
            v_claim.reasoning_requested
       ) IS DISTINCT FROM ROW(
            p_run_id, p_input_id, p_context_id, p_stage, p_role, p_round_number,
            p_provider, p_model, p_api_flavor, p_endpoint_policy_id, p_route_ordinal,
            p_prompt_template_hash, p_request_envelope_hash, p_reasoning_requested
       ) THEN
        RAISE EXCEPTION 'model-call attempt has no exact open claim' USING ERRCODE = '55000';
    END IF;
    IF p_completed_at > clock_timestamp()
       OR abs(
            extract(epoch FROM (p_completed_at - p_started_at)) * 1000 - p_latency_ms
       ) > 1 THEN
        RAISE EXCEPTION 'model-call audit timing is invalid' USING ERRCODE = '23514';
    END IF;
    IF p_authority_payload IS NOT NULL THEN
        IF octet_length(p_authority_payload) NOT BETWEEN 2 AND 65536
           OR public.p3d_canonical_json(p_authority_payload::json) <> p_authority_payload
           OR encode(public.digest(convert_to(p_authority_payload, 'UTF8'), 'sha256'), 'hex')
              <> p_authority_hash
           OR NOT public.p3d_text_is_safe(p_authority_payload) THEN
            RAISE EXCEPTION 'model-call result authority is invalid' USING ERRCODE = '23514';
        END IF;
    END IF;
    v_audit_hash := encode(public.digest(convert_to(
        jsonb_build_object(
            'call_id', p_call_id::text, 'run_id', p_run_id::text,
            'input_id', p_input_id::text, 'context_id', p_context_id::text,
            'stage', p_stage, 'role', p_role, 'round_number', p_round_number,
            'provider', p_provider, 'model', p_model, 'api_flavor', p_api_flavor,
            'endpoint_policy_id', p_endpoint_policy_id, 'route_ordinal', p_route_ordinal,
            'prompt_template_hash', p_prompt_template_hash,
            'request_envelope_hash', p_request_envelope_hash,
            'response_hash', p_response_hash,
            'reasoning_requested', p_reasoning_requested,
            'reasoning_effective', p_reasoning_effective,
            'token_counts_trusted', p_token_counts_trusted,
            'input_tokens', p_input_tokens, 'output_tokens', p_output_tokens,
            'latency_ms', p_latency_ms,
            'started_at', extract(epoch FROM p_started_at)::text,
            'completed_at', extract(epoch FROM p_completed_at)::text,
            'outcome', p_outcome, 'error_code', p_error_code,
            'authority_kind', p_authority_kind, 'authority_hash', p_authority_hash
        )::text, 'UTF8'), 'sha256'), 'hex');

    SELECT * INTO v_existing
      FROM public.model_call_audits WHERE call_id = p_call_id;
    IF FOUND THEN
        IF ROW(
            v_existing.run_id, v_existing.input_id, v_existing.context_id,
            v_existing.stage, v_existing.role, v_existing.round_number,
            v_existing.provider, v_existing.model, v_existing.api_flavor,
            v_existing.endpoint_policy_id, v_existing.route_ordinal,
            v_existing.prompt_template_hash, v_existing.request_envelope_hash,
            v_existing.response_hash, v_existing.reasoning_requested,
            v_existing.reasoning_effective, v_existing.token_counts_trusted,
            v_existing.input_tokens, v_existing.output_tokens, v_existing.latency_ms,
            v_existing.started_at, v_existing.completed_at, v_existing.outcome,
            v_existing.error_code, v_existing.authority_kind,
            v_existing.authority_hash, v_existing.authority_payload
        ) IS NOT DISTINCT FROM ROW(
            p_run_id, p_input_id, p_context_id, p_stage, p_role, p_round_number,
            p_provider, p_model, p_api_flavor, p_endpoint_policy_id, p_route_ordinal,
            p_prompt_template_hash, p_request_envelope_hash, p_response_hash,
            p_reasoning_requested, p_reasoning_effective, p_token_counts_trusted,
            p_input_tokens, p_output_tokens, p_latency_ms, p_started_at, p_completed_at,
            p_outcome, p_error_code, p_authority_kind, p_authority_hash,
            p_authority_payload
        ) THEN
            RETURN FALSE;
        END IF;
        RAISE EXCEPTION 'model-call audit identity collision' USING ERRCODE = '23505';
    END IF;
    IF v_claim.status <> 'CLAIMED' THEN
        RAISE EXCEPTION 'closed model-call claim has no audit authority'
            USING ERRCODE = '55000';
    END IF;

    INSERT INTO public.model_call_audits (
        call_id, run_id, input_id, context_id, stage, role, round_number,
        provider, model, api_flavor, endpoint_policy_id, route_ordinal,
        prompt_template_hash, request_envelope_hash, response_hash,
        reasoning_requested, reasoning_effective, token_counts_trusted,
        input_tokens, output_tokens, latency_ms, started_at, completed_at,
        outcome, error_code, authority_kind, authority_hash, authority_payload,
        audit_hash, route_config_hash
    ) VALUES (
        p_call_id, p_run_id, p_input_id, p_context_id, p_stage, p_role, p_round_number,
        p_provider, p_model, p_api_flavor, p_endpoint_policy_id, p_route_ordinal,
        p_prompt_template_hash, p_request_envelope_hash, p_response_hash,
        p_reasoning_requested, p_reasoning_effective, p_token_counts_trusted,
        p_input_tokens, p_output_tokens, p_latency_ms, p_started_at, p_completed_at,
        p_outcome, p_error_code, p_authority_kind, p_authority_hash,
        p_authority_payload, v_audit_hash, v_route_config_hash
    );
    UPDATE public.model_call_claims
       SET status = 'CLOSED', closed_at = statement_timestamp()
     WHERE call_id = p_call_id AND status = 'CLAIMED';
    RETURN TRUE;
EXCEPTION
    WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'model-call result authority is invalid' USING ERRCODE = '23514';
END;
$$;

-- P3-F reflection memory: allow exactly one '/' model id separator while
-- rejecting whitespace, controls, traversal segments, and overlong values.
ALTER TABLE public.memory_curation_audits DROP CONSTRAINT memory_curation_audits_model_id_check;
ALTER TABLE public.memory_curation_audits ADD CONSTRAINT memory_curation_audits_model_id_check CHECK (
    length(model_id) BETWEEN 1 AND 128
    AND model_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}(/[A-Za-z0-9][A-Za-z0-9._:-]{0,126})?$'
    AND model_id !~ '(^|/)\.\.?(/|$)'
);

CREATE OR REPLACE FUNCTION public.register_memory_curation_audit(
    p_audit_id UUID, p_artifact_id TEXT, p_audit_kind TEXT, p_route_id TEXT,
    p_provider_id TEXT, p_model_id TEXT, p_policy_id TEXT, p_template_hash TEXT,
    p_reasoning_requested TEXT, p_reasoning_effective TEXT,
    p_attempt_count INTEGER, p_fallback_count INTEGER,
    p_input_hash TEXT, p_output_hash TEXT, p_report_hash TEXT,
    p_case_count INTEGER, p_accepted_count INTEGER, p_latency_ms INTEGER,
    p_outcome TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_existing public.memory_curation_audits%ROWTYPE;
BEGIN
    IF p_audit_id IS NULL
       OR p_audit_kind IS NULL OR p_route_id IS NULL OR p_provider_id IS NULL
       OR p_model_id IS NULL OR p_policy_id IS NULL OR p_template_hash IS NULL
       OR p_reasoning_requested IS NULL OR p_reasoning_effective IS NULL
       OR p_attempt_count IS NULL OR p_fallback_count IS NULL
       OR p_input_hash IS NULL OR p_case_count IS NULL OR p_accepted_count IS NULL
       OR p_latency_ms IS NULL OR p_outcome IS NULL
       OR p_audit_kind NOT IN ('MODEL', 'EVAL')
       OR p_route_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       OR p_provider_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       OR length(p_model_id) NOT BETWEEN 1 AND 128
       OR p_model_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}(/[A-Za-z0-9][A-Za-z0-9._:-]{0,126})?$'
       OR p_model_id ~ '(^|/)\.\.?(/|$)'
       OR p_policy_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       OR NOT public.p3f_text_is_safe(p_route_id, 128)
       OR NOT public.p3f_text_is_safe(p_provider_id, 128)
       OR NOT public.p3f_text_is_safe(p_model_id, 128)
       OR NOT public.p3f_text_is_safe(p_policy_id, 128)
       OR p_template_hash !~ '^[0-9a-f]{64}$'
       OR p_reasoning_requested NOT IN ('NONE', 'LOW', 'MEDIUM', 'HIGH', 'MAX')
       OR p_reasoning_effective NOT IN (
           'UNKNOWN', 'NONE', 'LOW', 'MEDIUM', 'HIGH', 'MAX'
       )
       OR p_attempt_count NOT BETWEEN 1 AND 100
       OR p_fallback_count NOT BETWEEN 0 AND p_attempt_count - 1
       OR p_input_hash !~ '^[0-9a-f]{64}$'
       OR (p_output_hash IS NOT NULL AND p_output_hash !~ '^[0-9a-f]{64}$')
       OR (p_report_hash IS NOT NULL AND p_report_hash !~ '^[0-9a-f]{64}$')
       OR (p_report_hash IS NOT NULL) IS DISTINCT FROM (
           p_audit_kind = 'EVAL' OR p_outcome IN ('SUCCESS', 'ABSTAIN')
       )
       OR p_case_count NOT BETWEEN 0 AND 100000
       OR p_accepted_count NOT BETWEEN 0 AND p_case_count
       OR p_latency_ms NOT BETWEEN 0 AND 900000
       OR p_outcome NOT IN ('SUCCESS', 'FAILURE', 'TIMEOUT', 'ABSTAIN')
       OR (p_outcome = 'SUCCESS' AND p_output_hash IS NULL) THEN
        RAISE EXCEPTION 'memory curation audit metadata is invalid'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO v_existing
    FROM public.memory_curation_audits
    WHERE audit_id = p_audit_id;
    IF FOUND THEN
        IF ROW(
            v_existing.artifact_id, v_existing.audit_kind, v_existing.route_id,
            v_existing.provider_id, v_existing.model_id, v_existing.policy_id,
            v_existing.template_hash, v_existing.reasoning_requested,
            v_existing.reasoning_effective, v_existing.attempt_count,
            v_existing.fallback_count, v_existing.input_hash,
            v_existing.output_hash, v_existing.report_hash,
            v_existing.case_count, v_existing.accepted_count,
            v_existing.latency_ms, v_existing.outcome
        ) IS DISTINCT FROM ROW(
            p_artifact_id, p_audit_kind, p_route_id,
            p_provider_id, p_model_id, p_policy_id,
            p_template_hash, p_reasoning_requested,
            p_reasoning_effective, p_attempt_count,
            p_fallback_count, p_input_hash,
            p_output_hash, p_report_hash,
            p_case_count, p_accepted_count,
            p_latency_ms, p_outcome
        ) THEN
            RAISE EXCEPTION 'memory curation audit identity collision'
                USING ERRCODE = '23505';
        END IF;
        RETURN FALSE;
    END IF;
    INSERT INTO public.memory_curation_audits(
        audit_id, artifact_id, audit_kind, route_id,
        provider_id, model_id, policy_id, template_hash,
        reasoning_requested, reasoning_effective, attempt_count, fallback_count,
        input_hash, output_hash, report_hash, case_count, accepted_count,
        latency_ms, outcome
    ) VALUES (
        p_audit_id, p_artifact_id, p_audit_kind, p_route_id,
        p_provider_id, p_model_id, p_policy_id, p_template_hash,
        p_reasoning_requested, p_reasoning_effective, p_attempt_count, p_fallback_count,
        p_input_hash, p_output_hash, p_report_hash, p_case_count, p_accepted_count,
        p_latency_ms, p_outcome
    );
    RETURN TRUE;
EXCEPTION
    WHEN unique_violation THEN
        SELECT * INTO v_existing
        FROM public.memory_curation_audits
        WHERE audit_id = p_audit_id;
        IF ROW(
            v_existing.artifact_id, v_existing.audit_kind, v_existing.route_id,
            v_existing.provider_id, v_existing.model_id, v_existing.policy_id,
            v_existing.template_hash, v_existing.reasoning_requested,
            v_existing.reasoning_effective, v_existing.attempt_count,
            v_existing.fallback_count, v_existing.input_hash,
            v_existing.output_hash, v_existing.report_hash,
            v_existing.case_count, v_existing.accepted_count,
            v_existing.latency_ms, v_existing.outcome
        ) IS DISTINCT FROM ROW(
            p_artifact_id, p_audit_kind, p_route_id,
            p_provider_id, p_model_id, p_policy_id,
            p_template_hash, p_reasoning_requested,
            p_reasoning_effective, p_attempt_count,
            p_fallback_count, p_input_hash,
            p_output_hash, p_report_hash,
            p_case_count, p_accepted_count,
            p_latency_ms, p_outcome
        ) THEN
            RAISE EXCEPTION 'memory curation audit identity collision'
                USING ERRCODE = '23505';
        END IF;
        RETURN FALSE;
END;
$$;

REVOKE ALL ON TABLE public.model_call_claims, public.model_call_audits FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guard_model_call_claim_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim_model_call_attempt(
    UUID, UUID, UUID, UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT, TEXT,
    INTEGER, TEXT, TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_model_call_attempt(
    UUID, UUID, UUID, UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT, TEXT,
    INTEGER, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, INTEGER, INTEGER,
    INTEGER, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT, TEXT, TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_memory_curation_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    INTEGER, INTEGER, TEXT, TEXT, TEXT, INTEGER, INTEGER, INTEGER, TEXT
) FROM PUBLIC;
