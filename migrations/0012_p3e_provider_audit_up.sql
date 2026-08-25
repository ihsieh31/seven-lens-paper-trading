-- 0012: P3-E authoritative, payload-free model-call attempt audit and replay result.

CREATE TABLE public.model_call_claims (
    call_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    input_id UUID NOT NULL,
    context_id UUID NOT NULL,
    stage TEXT NOT NULL,
    role TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'AGNES'),
    model TEXT NOT NULL CHECK (model = 'agnes-2.5-flash'),
    api_flavor TEXT NOT NULL CHECK (api_flavor = 'CHAT_COMPLETIONS'),
    endpoint_policy_id TEXT NOT NULL CHECK (
        endpoint_policy_id = 'p3e-agnes-2.5-flash-only-v1'
    ),
    route_ordinal INTEGER NOT NULL CHECK (route_ordinal = 1),
    prompt_template_hash TEXT NOT NULL CHECK (prompt_template_hash ~ '^[0-9a-f]{64}$'),
    request_envelope_hash TEXT NOT NULL CHECK (request_envelope_hash ~ '^[0-9a-f]{64}$'),
    reasoning_requested TEXT NOT NULL CHECK (reasoning_requested = 'MAX'),
    status TEXT NOT NULL DEFAULT 'CLAIMED' CHECK (status IN ('CLAIMED', 'CLOSED')),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    closed_at TIMESTAMPTZ,
    CHECK (
        (stage = 'ANALYST'
         AND role IN ('TECHNICAL', 'FUNDAMENTALS', 'NEWS', 'SENTIMENT')
         AND round_number = 0)
        OR (stage = 'INVESTMENT_DEBATE'
            AND role IN ('BULL', 'BEAR') AND round_number IN (1, 2))
        OR (stage = 'RESEARCH_MANAGER'
            AND role = 'RESEARCH_MANAGER' AND round_number = 0)
        OR (stage = 'TRADER' AND role = 'TRADER' AND round_number = 0)
        OR (stage = 'RISK_DEBATE'
            AND role IN ('AGGRESSIVE', 'CONSERVATIVE', 'NEUTRAL')
            AND round_number IN (1, 2))
        OR (stage = 'PORTFOLIO_MANAGER'
            AND role = 'PORTFOLIO_MANAGER' AND round_number = 0)
    ),
    CHECK (
        (status = 'CLAIMED' AND closed_at IS NULL)
        OR (status = 'CLOSED' AND closed_at IS NOT NULL AND closed_at >= claimed_at)
    ),
    CHECK (run_id <> '00000000-0000-0000-0000-000000000000'::uuid),
    CHECK (input_id <> '00000000-0000-0000-0000-000000000000'::uuid),
    CHECK (context_id <> '00000000-0000-0000-0000-000000000000'::uuid),
    CHECK (call_id = public.p3d_derive_run_id(
        'seven-lens.p3e.model-call.v1', input_id::text, context_id::text,
        stage, role, round_number::text, route_ordinal::text
    ))
);

CREATE TABLE public.model_call_audits (
    call_id UUID PRIMARY KEY REFERENCES public.model_call_claims(call_id),
    run_id UUID NOT NULL,
    input_id UUID NOT NULL,
    context_id UUID NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN (
        'ANALYST', 'INVESTMENT_DEBATE', 'RESEARCH_MANAGER',
        'TRADER', 'RISK_DEBATE', 'PORTFOLIO_MANAGER'
    )),
    role TEXT NOT NULL CHECK (role IN (
        'TECHNICAL', 'FUNDAMENTALS', 'NEWS', 'SENTIMENT',
        'BULL', 'BEAR', 'RESEARCH_MANAGER', 'TRADER',
        'AGGRESSIVE', 'CONSERVATIVE', 'NEUTRAL', 'PORTFOLIO_MANAGER'
    )),
    round_number INTEGER NOT NULL CHECK (round_number BETWEEN 0 AND 2),
    provider TEXT NOT NULL CHECK (provider = 'AGNES'),
    model TEXT NOT NULL CHECK (model = 'agnes-2.5-flash'),
    api_flavor TEXT NOT NULL CHECK (api_flavor = 'CHAT_COMPLETIONS'),
    endpoint_policy_id TEXT NOT NULL CHECK (
        endpoint_policy_id = 'p3e-agnes-2.5-flash-only-v1'
    ),
    route_ordinal INTEGER NOT NULL CHECK (route_ordinal = 1),
    prompt_template_hash TEXT NOT NULL CHECK (prompt_template_hash ~ '^[0-9a-f]{64}$'),
    request_envelope_hash TEXT NOT NULL CHECK (request_envelope_hash ~ '^[0-9a-f]{64}$'),
    response_hash TEXT CHECK (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'),
    reasoning_requested TEXT NOT NULL CHECK (reasoning_requested = 'MAX'),
    reasoning_effective TEXT NOT NULL CHECK (reasoning_effective = 'UNKNOWN'),
    token_counts_trusted BOOLEAN NOT NULL,
    input_tokens INTEGER CHECK (input_tokens BETWEEN 0 AND 1000000),
    output_tokens INTEGER CHECK (output_tokens BETWEEN 0 AND 1000000),
    latency_ms INTEGER NOT NULL CHECK (latency_ms BETWEEN 0 AND 900000),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('SUCCESS', 'FAILURE')),
    error_code TEXT NOT NULL CHECK (error_code IN (
        'NONE', 'CONFIG', 'AUTH', 'PERMANENT', 'RATE_LIMIT', 'TRANSIENT',
        'TIMEOUT', 'PROTOCOL', 'SCHEMA', 'OVERSIZE', 'DEADLINE'
    )),
    authority_kind TEXT CHECK (authority_kind IS NULL OR authority_kind IN (
        'ANALYST_REPORT', 'DEBATE_ARGUMENT', 'RESEARCH_CONCLUSION', 'TRADER_PLAN',
        'RISK_ARGUMENT', 'PORTFOLIO_PROPOSAL'
    )),
    authority_hash TEXT CHECK (
        authority_hash IS NULL OR authority_hash ~ '^[0-9a-f]{64}$'
    ),
    authority_payload TEXT CHECK (
        authority_payload IS NULL OR length(authority_payload) BETWEEN 2 AND 65536
    ),
    audit_hash TEXT NOT NULL CHECK (audit_hash ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (completed_at > started_at),
    CHECK (run_id <> '00000000-0000-0000-0000-000000000000'::uuid),
    CHECK (input_id <> '00000000-0000-0000-0000-000000000000'::uuid),
    CHECK (context_id <> '00000000-0000-0000-0000-000000000000'::uuid),
    CHECK (
        (stage = 'ANALYST'
         AND role IN ('TECHNICAL', 'FUNDAMENTALS', 'NEWS', 'SENTIMENT')
         AND round_number = 0)
        OR (stage = 'INVESTMENT_DEBATE'
            AND role IN ('BULL', 'BEAR') AND round_number IN (1, 2))
        OR (stage = 'RESEARCH_MANAGER'
            AND role = 'RESEARCH_MANAGER' AND round_number = 0)
        OR (stage = 'TRADER' AND role = 'TRADER' AND round_number = 0
        )
        OR (stage = 'RISK_DEBATE'
            AND role IN ('AGGRESSIVE', 'CONSERVATIVE', 'NEUTRAL')
            AND round_number IN (1, 2))
        OR (stage = 'PORTFOLIO_MANAGER'
            AND role = 'PORTFOLIO_MANAGER' AND round_number = 0)
    ),
    CHECK (
        outcome = 'FAILURE'
        OR (stage = 'ANALYST' AND authority_kind = 'ANALYST_REPORT')
        OR (stage = 'INVESTMENT_DEBATE' AND authority_kind = 'DEBATE_ARGUMENT')
        OR (stage = 'RESEARCH_MANAGER' AND authority_kind = 'RESEARCH_CONCLUSION')
        OR (stage = 'TRADER' AND authority_kind = 'TRADER_PLAN')
        OR (stage = 'RISK_DEBATE' AND authority_kind = 'RISK_ARGUMENT')
        OR (stage = 'PORTFOLIO_MANAGER' AND authority_kind = 'PORTFOLIO_PROPOSAL')
    ),
    CHECK (
        (token_counts_trusted AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL)
        OR (NOT token_counts_trusted AND input_tokens IS NULL AND output_tokens IS NULL)
    ),
    CHECK (
        (outcome = 'SUCCESS' AND error_code = 'NONE' AND response_hash IS NOT NULL
         AND authority_kind IS NOT NULL AND authority_hash IS NOT NULL
         AND authority_payload IS NOT NULL)
        OR (outcome = 'FAILURE' AND error_code <> 'NONE'
            AND authority_kind IS NULL AND authority_hash IS NULL
            AND authority_payload IS NULL)
    ),
    CHECK (call_id = public.p3d_derive_run_id(
        'seven-lens.p3e.model-call.v1', input_id::text, context_id::text,
        stage, role, round_number::text, route_ordinal::text
    ))
);

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

CREATE TRIGGER model_call_claims_guard_write
BEFORE UPDATE OR DELETE ON public.model_call_claims
FOR EACH ROW EXECUTE FUNCTION public.guard_model_call_claim_write();

CREATE TRIGGER model_call_claims_guard_truncate
BEFORE TRUNCATE ON public.model_call_claims
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER model_call_audits_guard_write
BEFORE UPDATE OR DELETE ON public.model_call_audits
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER model_call_audits_guard_truncate
BEFORE TRUNCATE ON public.model_call_audits
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

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
BEGIN
    IF p_call_id IS NULL OR p_run_id IS NULL OR p_input_id IS NULL OR p_context_id IS NULL
       OR p_stage IS NULL OR p_role IS NULL OR p_round_number IS NULL
       OR p_provider IS NULL OR p_model IS NULL OR p_api_flavor IS NULL
       OR p_endpoint_policy_id IS NULL OR p_route_ordinal IS NULL
       OR p_prompt_template_hash IS NULL OR p_request_envelope_hash IS NULL
       OR p_reasoning_requested IS NULL THEN
        RAISE EXCEPTION 'model-call claim is incomplete' USING ERRCODE = '23514';
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
        prompt_template_hash, request_envelope_hash, reasoning_requested
    ) VALUES (
        p_call_id, p_run_id, p_input_id, p_context_id, p_stage, p_role, p_round_number,
        p_provider, p_model, p_api_flavor, p_endpoint_policy_id, p_route_ordinal,
        p_prompt_template_hash, p_request_envelope_hash, p_reasoning_requested
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
        audit_hash
    ) VALUES (
        p_call_id, p_run_id, p_input_id, p_context_id, p_stage, p_role, p_round_number,
        p_provider, p_model, p_api_flavor, p_endpoint_policy_id, p_route_ordinal,
        p_prompt_template_hash, p_request_envelope_hash, p_response_hash,
        p_reasoning_requested, p_reasoning_effective, p_token_counts_trusted,
        p_input_tokens, p_output_tokens, p_latency_ms, p_started_at, p_completed_at,
        p_outcome, p_error_code, p_authority_kind, p_authority_hash,
        p_authority_payload, v_audit_hash
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
