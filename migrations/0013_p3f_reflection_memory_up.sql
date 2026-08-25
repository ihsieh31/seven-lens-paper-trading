-- 0013: P3-F immutable reflection lineage and independently curated memory.

CREATE OR REPLACE FUNCTION public.p3f_text_is_safe(p_value TEXT, p_max_bytes INTEGER)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH normalized AS (
        SELECT normalize(p_value, NFKC) AS value
    ), label AS (
        SELECT value, pg_catalog.regexp_replace(
                   pg_catalog.replace(pg_catalog.lower(value), 'ß', 'ss'),
                   '[_-]+', ' ', 'g'
               ) AS label_value
        FROM normalized
    )
    SELECT p_value IS NOT NULL
       AND p_max_bytes BETWEEN 1 AND 8192
       AND pg_catalog.octet_length(p_value) BETWEEN 1 AND p_max_bytes
       AND p_value !~ E'[\n\r]'
       AND public.p3d_text_is_safe(p_value)
       AND value !~* '([a-z][a-z0-9+.-]{1,31}://|(^|[^a-z0-9])(data|file|javascript|mailto|postgres(ql)?|ssh|tel|urn):)'
       AND value !~* '(^|[^:])//[^/[:space:]]+'
       AND value !~* '[a-z0-9.!#$%&''*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}'
       AND value !~* '(^|[^a-z0-9])([a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?\.)+(ai|app|cn|co|com|dev|gov|info|invalid|io|net|org)(:[0-9]{1,5})?(/[^[:space:]]*)?'
       AND value !~* '(^|[^0-9])([0-9]{1,3}\.){3}[0-9]{1,3}(:[0-9]{1,5})?/[^[:space:]]*'
       AND value !~* '\[[0-9a-f:.]+\](:[0-9]{1,5})?/[^[:space:]]*'
       AND value !~ '(^|[[:space:]])(/[A-Za-z0-9._-]+/|[A-Za-z]:\\)'
       AND value !~ '(^|[[:space:]([{''"])\.\.?/[^[:space:])\]}''"]+'
       AND label_value !~* '(^|[^a-z0-9])(api[[:space:]]+key|secret([[:space:]]+key|[[:space:]]+ref)?|credential|password|token|dsn|bearer|authorization([[:space:]]+header)?|(request[[:space:]]+)?header|account[[:space:]]+(id|number|name)|broker[[:space:]]+order[[:space:]]+id|client[[:space:]]+order[[:space:]]+id|(customer|client|user|portfolio[[:space:]]+owner)[[:space:]]+(id|name)|email|phone|address)([^a-z0-9]|$)'
       AND NOT EXISTS (
           SELECT 1
           FROM (
               SELECT pg_catalog.unnest(ARRAY[
                   173, 1564, 1757, 1807, 2274, 6158, 65279, 65529, 65530, 65531,
                   69821, 69837, 917505
               ]) AS value
               UNION ALL SELECT pg_catalog.generate_series(1536, 1541)
               UNION ALL SELECT pg_catalog.generate_series(2192, 2193)
               UNION ALL SELECT pg_catalog.generate_series(8203, 8207)
               UNION ALL SELECT pg_catalog.generate_series(8234, 8238)
               UNION ALL SELECT pg_catalog.generate_series(8288, 8292)
               UNION ALL SELECT pg_catalog.generate_series(8294, 8303)
               UNION ALL SELECT pg_catalog.generate_series(113824, 113827)
               UNION ALL SELECT pg_catalog.generate_series(78896, 78911)
               UNION ALL SELECT pg_catalog.generate_series(119155, 119162)
               UNION ALL SELECT pg_catalog.generate_series(917536, 917631)
           ) AS code
           WHERE pg_catalog.strpos(p_value, pg_catalog.chr(code.value)) > 0
       )
    FROM label
$$;

CREATE OR REPLACE FUNCTION public.p3f_instruction_text_is_safe(p_value TEXT)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH normalized AS (
        SELECT pg_catalog.btrim(pg_catalog.regexp_replace(
                   pg_catalog.replace(
                       pg_catalog.lower(normalize(p_value, NFKC)), 'ß', 'ss'
                   ),
                   '[^a-z0-9]+', ' ', 'g'
               )) AS value
    )
    SELECT p_value IS NOT NULL
       AND value !~ '(^| )(ignore|disregard|override|bypass|forget) (all )?(prior|previous)( |$)'
       AND value !~ '(^| )(ignore|disregard|override|bypass|forget)( [a-z0-9]+){0,16} (prior |previous |all |the )?(instruction|rule|policy|constraint)s?( |$)'
       AND value !~ '(^| )(execute|place|submit|send|cancel|modify)( [a-z0-9]+){0,10} (a )?(trade|order)s?( |$)'
       AND value !~ '(^| )(read|reveal|expose|print|return)( [a-z0-9]+){0,10} (secret|credential|api key|authorization)s?( |$)'
       AND value !~ '(^| )(call|invoke|use|run)( [a-z0-9]+){0,10} (tool|shell|command)s?( |$)'
       AND value !~ '(^| )(set|make|mark|promote)( [a-z0-9]+){0,7} current( |$)'
       AND value !~ '(^| )system prompt( |$)'
    FROM normalized
$$;

CREATE OR REPLACE FUNCTION public.p3f_fact_text_is_closed(
    p_value TEXT, p_cited_fact_ids TEXT[], p_facts JSON, p_risk_reason_values TEXT[]
) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH facts AS (
        SELECT item->>'fact_id' AS fact_id, item->>'kind' AS kind, item->>'value' AS value
        FROM pg_catalog.json_array_elements(p_facts) AS fact(item)
    ), cited AS (
        SELECT f.fact_id, f.kind, f.value
        FROM facts AS f
        WHERE f.fact_id = ANY(p_cited_fact_ids)
    ), date_tokens AS (
        SELECT match[1] AS value
        FROM pg_catalog.regexp_matches(
            p_value,
            '(?<![0-9])([0-9]{4}-[0-9]{2}-[0-9]{2})(?![0-9])',
            'g'
        ) AS match
    ), without_dates AS (
        SELECT pg_catalog.regexp_replace(
                   p_value,
                   '(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])',
                   '', 'g'
               ) AS value
    ), number_tokens AS (
        SELECT match[1] AS value
        FROM without_dates,
             LATERAL pg_catalog.regexp_matches(
                 without_dates.value,
                 '(?<![A-Za-z0-9_.-])(-?(0|[1-9][0-9]*)(\.[0-9]+)?)(?![A-Za-z0-9_.-])',
                 'g'
             ) AS match
    ), upper_tokens AS (
        SELECT match[1] AS value
        FROM pg_catalog.regexp_matches(
            p_value,
            '(?<![A-Za-z0-9.-])([A-Z][A-Z0-9.-]{0,9})(?![A-Za-z0-9.-])',
            'g'
        ) AS match
    )
    SELECT p_value IS NOT NULL
       AND p_cited_fact_ids IS NOT NULL
       AND p_facts IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_catalog.unnest(p_cited_fact_ids) AS requested(fact_id)
           WHERE NOT EXISTS (SELECT 1 FROM facts WHERE facts.fact_id = requested.fact_id)
       )
       AND (
           p_risk_reason_values IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM pg_catalog.unnest(p_risk_reason_values) AS requested(value)
               WHERE NOT EXISTS (
                   SELECT 1 FROM cited
                   WHERE cited.kind = 'RISK_REASON' AND cited.value = requested.value
               )
           )
       )
       AND p_value !~ '(?<![A-Za-z0-9_.-])-?(0|[1-9][0-9]*)(\.[0-9]+)?[eE][+-]?[0-9]+(?![A-Za-z0-9_.-])'
       AND NOT EXISTS (
           SELECT 1 FROM date_tokens
           WHERE NOT EXISTS (
               SELECT 1 FROM cited
               WHERE cited.kind = 'DATE' AND cited.value = date_tokens.value
           )
       )
       AND NOT EXISTS (
           SELECT 1 FROM number_tokens
           WHERE NOT EXISTS (
               SELECT 1 FROM cited
               WHERE cited.kind = 'NUMBER' AND cited.value = number_tokens.value
           )
       )
       AND NOT EXISTS (
           SELECT 1 FROM upper_tokens
           WHERE NOT EXISTS (
               SELECT 1 FROM cited
               WHERE cited.kind = 'SYMBOL' AND cited.value = upper_tokens.value
                  OR cited.kind = 'RISK_REASON'
                     AND cited.value = upper_tokens.value
                     AND (p_risk_reason_values IS NULL
                          OR cited.value = ANY(p_risk_reason_values))
           )
       )
       AND NOT EXISTS (
           SELECT 1 FROM cited
           WHERE cited.kind IN ('SYMBOL', 'RISK_REASON')
             AND pg_catalog.strpos(pg_catalog.lower(p_value), pg_catalog.lower(cited.value)) > 0
             AND pg_catalog.strpos(p_value, cited.value) = 0
       )
$$;

CREATE TABLE public.reflection_records (
    reflection_id TEXT PRIMARY KEY CHECK (
        reflection_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    schema_version TEXT NOT NULL CHECK (length(schema_version) BETWEEN 1 AND 64),
    record_kind TEXT NOT NULL CHECK (record_kind IN ('DAILY', 'CORRECTION')),
    created_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    as_of_at TIMESTAMPTZ NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    proposal_id TEXT NOT NULL CHECK (proposal_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    decision_id TEXT NOT NULL CHECK (decision_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    research_bundle_hash TEXT NOT NULL CHECK (research_bundle_hash ~ '^[0-9a-f]{64}$'),
    portfolio_snapshot_hash TEXT NOT NULL CHECK (portfolio_snapshot_hash ~ '^[0-9a-f]{64}$'),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    content_bytes BYTEA NOT NULL CHECK (octet_length(content_bytes) BETWEEN 1 AND 524288),
    prompt_version TEXT NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 128),
    model_version TEXT NOT NULL CHECK (length(model_version) BETWEEN 1 AND 128),
    provider_version TEXT NOT NULL CHECK (length(provider_version) BETWEEN 1 AND 128),
    data_version TEXT NOT NULL CHECK (length(data_version) BETWEEN 1 AND 128),
    memory_version TEXT NOT NULL CHECK (length(memory_version) BETWEEN 1 AND 128),
    source_count INTEGER NOT NULL CHECK (source_count BETWEEN 1 AND 32),
    CHECK (cutoff_at <= as_of_at),
    CHECK (as_of_at <= created_at),
    CHECK (created_at <= available_at),
    CHECK (
        encode(digest(
            convert_to('seven-lens.p3f.reflection.v1', 'UTF8') || decode('00', 'hex') ||
            content_bytes,
            'sha256'
        ), 'hex') = content_hash
    )
);

CREATE TABLE public.reflection_sources (
    reflection_id TEXT NOT NULL REFERENCES public.reflection_records(reflection_id),
    source_fact_id TEXT NOT NULL CHECK (
        source_fact_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    source_kind TEXT NOT NULL CHECK (
        source_kind ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    source_content_hash TEXT NOT NULL CHECK (source_content_hash ~ '^[0-9a-f]{64}$'),
    source_available_at TIMESTAMPTZ NOT NULL,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal BETWEEN 1 AND 32),
    PRIMARY KEY (reflection_id, source_fact_id),
    UNIQUE (reflection_id, source_ordinal)
);

CREATE TABLE public.reflection_corrections (
    correction_reflection_id TEXT PRIMARY KEY
        REFERENCES public.reflection_records(reflection_id),
    superseded_reflection_id TEXT NOT NULL
        REFERENCES public.reflection_records(reflection_id),
    reason_code TEXT NOT NULL CHECK (
        reason_code IN ('SOURCE_CORRECTION', 'FACTUAL_ERROR', 'LINEAGE_REPAIR')
    ),
    linked_at TIMESTAMPTZ NOT NULL,
    CHECK (correction_reflection_id <> superseded_reflection_id),
    UNIQUE (correction_reflection_id, superseded_reflection_id)
);

CREATE VIEW public.approved_reflection_records
WITH (security_barrier = true)
AS
SELECT r.reflection_id, r.schema_version, r.record_kind, r.created_at,
       r.available_at, r.as_of_at, r.cutoff_at, r.proposal_id, r.decision_id,
       r.research_bundle_hash, r.portfolio_snapshot_hash, r.content_hash, r.content_bytes,
       r.prompt_version, r.model_version, r.provider_version, r.data_version,
       r.memory_version, r.source_count,
       c.superseded_reflection_id, c.reason_code AS correction_reason_code
FROM public.reflection_records AS r
LEFT JOIN public.reflection_corrections AS c
  ON c.correction_reflection_id = r.reflection_id;

CREATE VIEW public.approved_reflection_sources
WITH (security_barrier = true)
AS
SELECT s.reflection_id, s.source_fact_id, s.source_kind,
       s.source_content_hash, s.source_available_at, s.source_ordinal
FROM public.reflection_sources AS s
JOIN public.reflection_records AS r ON r.reflection_id = s.reflection_id
WHERE s.source_available_at <= r.cutoff_at;

CREATE TABLE public.memory_artifacts (
    artifact_id TEXT PRIMARY KEY CHECK (
        artifact_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    schema_version TEXT NOT NULL CHECK (length(schema_version) BETWEEN 1 AND 64),
    created_at TIMESTAMPTZ NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    previous_artifact_id TEXT REFERENCES public.memory_artifacts(artifact_id),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    cas_hash TEXT NOT NULL CHECK (cas_hash ~ '^[0-9a-f]{64}$'),
    content_bytes BYTEA NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count BETWEEN 1 AND 524288),
    line_count INTEGER NOT NULL CHECK (line_count BETWEEN 1 AND 4000),
    entry_count INTEGER NOT NULL CHECK (entry_count BETWEEN 1 AND 512),
    source_record_count INTEGER NOT NULL CHECK (source_record_count BETWEEN 1 AND 4096),
    prompt_version TEXT NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 128),
    model_version TEXT NOT NULL CHECK (length(model_version) BETWEEN 1 AND 128),
    provider_version TEXT NOT NULL CHECK (length(provider_version) BETWEEN 1 AND 128),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (cutoff_at <= created_at),
    CHECK (registered_at >= cutoff_at),
    CHECK (previous_artifact_id IS NULL OR previous_artifact_id <> artifact_id),
    CHECK (content_hash = cas_hash),
    CHECK (octet_length(content_bytes) = byte_count),
    CHECK (encode(digest(content_bytes, 'sha256'), 'hex') = content_hash)
);

CREATE TABLE public.memory_artifact_sources (
    artifact_id TEXT NOT NULL REFERENCES public.memory_artifacts(artifact_id),
    reflection_id TEXT NOT NULL REFERENCES public.reflection_records(reflection_id),
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal BETWEEN 1 AND 4096),
    PRIMARY KEY (artifact_id, reflection_id),
    UNIQUE (artifact_id, source_ordinal)
);

CREATE TABLE public.memory_artifact_state_events (
    state_event_id UUID PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES public.memory_artifacts(artifact_id),
    state TEXT NOT NULL CHECK (state IN ('CANDIDATE', 'VALIDATED', 'CURRENT', 'INVALID')),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    validator_version TEXT CHECK (
        validator_version IS NULL OR length(validator_version) BETWEEN 1 AND 128
    ),
    validation_report_hash TEXT CHECK (
        validation_report_hash IS NULL OR validation_report_hash ~ '^[0-9a-f]{64}$'
    ),
    reason_code TEXT CHECK (
        reason_code IS NULL OR reason_code IN (
            'REGISTERED', 'DETERMINISTIC_VALIDATION', 'PROMOTED',
            'SCHEMA', 'BOUNDS', 'LINEAGE', 'FUTURE_LEAKAGE',
            'PROMPT_INJECTION', 'FACT_CLOSURE', 'INTEGRITY'
        )
    ),
    UNIQUE (artifact_id, state),
    CHECK (
        (state = 'VALIDATED' AND validator_version IS NOT NULL
             AND validation_report_hash IS NOT NULL
             AND reason_code = 'DETERMINISTIC_VALIDATION')
        OR (state = 'CANDIDATE' AND validator_version IS NULL
             AND validation_report_hash IS NULL AND reason_code = 'REGISTERED')
        OR (state = 'CURRENT' AND validator_version IS NULL
             AND validation_report_hash IS NULL AND reason_code = 'PROMOTED')
        OR (state = 'INVALID' AND reason_code NOT IN (
             'REGISTERED', 'DETERMINISTIC_VALIDATION', 'PROMOTED'
        ))
    )
);

CREATE TABLE public.memory_promotion_history (
    promotion_id UUID PRIMARY KEY,
    promotion_order BIGINT NOT NULL UNIQUE CHECK (promotion_order > 0),
    artifact_id TEXT NOT NULL UNIQUE REFERENCES public.memory_artifacts(artifact_id),
    previous_artifact_id TEXT REFERENCES public.memory_artifacts(artifact_id),
    requested_as_of TIMESTAMPTZ NOT NULL,
    effective_as_of TIMESTAMPTZ NOT NULL,
    promoted_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (effective_as_of = promoted_at),
    CHECK (previous_artifact_id IS NULL OR previous_artifact_id <> artifact_id)
);

ALTER TABLE public.memory_promotion_history
    ADD CONSTRAINT memory_promotion_history_pair_unique
    UNIQUE (promotion_id, artifact_id);

CREATE TABLE public.memory_current_pointer (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    artifact_id TEXT REFERENCES public.memory_artifacts(artifact_id),
    promotion_id UUID REFERENCES public.memory_promotion_history(promotion_id),
    promoted_at TIMESTAMPTZ,
    CHECK (
        (artifact_id IS NULL AND promotion_id IS NULL AND promoted_at IS NULL)
        OR (artifact_id IS NOT NULL AND promotion_id IS NOT NULL AND promoted_at IS NOT NULL)
    ),
    FOREIGN KEY (promotion_id, artifact_id)
        REFERENCES public.memory_promotion_history(promotion_id, artifact_id)
);

INSERT INTO public.memory_current_pointer(singleton) VALUES (TRUE);

CREATE TABLE public.memory_curation_audits (
    audit_id UUID PRIMARY KEY,
    artifact_id TEXT REFERENCES public.memory_artifacts(artifact_id),
    audit_kind TEXT NOT NULL CHECK (audit_kind IN ('MODEL', 'EVAL')),
    route_id TEXT NOT NULL CHECK (
        route_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    provider_id TEXT NOT NULL CHECK (
        provider_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    model_id TEXT NOT NULL CHECK (
        model_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    policy_id TEXT NOT NULL CHECK (
        policy_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    ),
    template_hash TEXT NOT NULL CHECK (template_hash ~ '^[0-9a-f]{64}$'),
    reasoning_requested TEXT NOT NULL CHECK (
        reasoning_requested IN ('NONE', 'LOW', 'MEDIUM', 'HIGH', 'MAX')
    ),
    reasoning_effective TEXT NOT NULL CHECK (
        reasoning_effective IN ('UNKNOWN', 'NONE', 'LOW', 'MEDIUM', 'HIGH', 'MAX')
    ),
    attempt_count INTEGER NOT NULL CHECK (attempt_count BETWEEN 1 AND 100),
    fallback_count INTEGER NOT NULL CHECK (
        fallback_count BETWEEN 0 AND attempt_count - 1
    ),
    input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    output_hash TEXT CHECK (output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'),
    report_hash TEXT CHECK (report_hash IS NULL OR report_hash ~ '^[0-9a-f]{64}$'),
    case_count INTEGER NOT NULL CHECK (case_count BETWEEN 0 AND 100000),
    accepted_count INTEGER NOT NULL CHECK (accepted_count BETWEEN 0 AND case_count),
    latency_ms INTEGER NOT NULL CHECK (latency_ms BETWEEN 0 AND 900000),
    outcome TEXT NOT NULL CHECK (outcome IN ('SUCCESS', 'FAILURE', 'TIMEOUT', 'ABSTAIN')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (
        (report_hash IS NOT NULL)
        = (audit_kind = 'EVAL' OR outcome IN ('SUCCESS', 'ABSTAIN'))
    ),
    CHECK (outcome <> 'SUCCESS' OR output_hash IS NOT NULL)
);

-- Owner writes are also denied for immutable history. The current pointer is the
-- sole mutable relation and only its SECURITY DEFINER promotion function is granted.
CREATE TRIGGER reflection_records_guard_write
BEFORE UPDATE OR DELETE ON public.reflection_records
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER reflection_records_guard_truncate
BEFORE TRUNCATE ON public.reflection_records
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER reflection_sources_guard_write
BEFORE UPDATE OR DELETE ON public.reflection_sources
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER reflection_sources_guard_truncate
BEFORE TRUNCATE ON public.reflection_sources
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER reflection_corrections_guard_write
BEFORE UPDATE OR DELETE ON public.reflection_corrections
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER reflection_corrections_guard_truncate
BEFORE TRUNCATE ON public.reflection_corrections
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_artifacts_guard_write
BEFORE UPDATE OR DELETE ON public.memory_artifacts
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_artifacts_guard_truncate
BEFORE TRUNCATE ON public.memory_artifacts
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_artifact_sources_guard_write
BEFORE UPDATE OR DELETE ON public.memory_artifact_sources
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_artifact_sources_guard_truncate
BEFORE TRUNCATE ON public.memory_artifact_sources
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_artifact_state_events_guard_write
BEFORE UPDATE OR DELETE ON public.memory_artifact_state_events
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_artifact_state_events_guard_truncate
BEFORE TRUNCATE ON public.memory_artifact_state_events
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_promotion_history_guard_write
BEFORE UPDATE OR DELETE ON public.memory_promotion_history
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_promotion_history_guard_truncate
BEFORE TRUNCATE ON public.memory_promotion_history
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_curation_audits_guard_write
BEFORE UPDATE OR DELETE ON public.memory_curation_audits
FOR EACH ROW EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER memory_curation_audits_guard_truncate
BEFORE TRUNCATE ON public.memory_curation_audits
FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

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
       OR p_model_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
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
        IF FOUND AND ROW(
            v_existing.artifact_id, v_existing.audit_kind, v_existing.route_id,
            v_existing.provider_id, v_existing.model_id, v_existing.policy_id,
            v_existing.template_hash, v_existing.reasoning_requested,
            v_existing.reasoning_effective, v_existing.attempt_count,
            v_existing.fallback_count, v_existing.input_hash,
            v_existing.output_hash, v_existing.report_hash,
            v_existing.case_count, v_existing.accepted_count,
            v_existing.latency_ms, v_existing.outcome
        ) IS NOT DISTINCT FROM ROW(
            p_artifact_id, p_audit_kind, p_route_id,
            p_provider_id, p_model_id, p_policy_id,
            p_template_hash, p_reasoning_requested,
            p_reasoning_effective, p_attempt_count,
            p_fallback_count, p_input_hash,
            p_output_hash, p_report_hash,
            p_case_count, p_accepted_count,
            p_latency_ms, p_outcome
        ) THEN
            RETURN FALSE;
        END IF;
        RAISE EXCEPTION 'memory curation audit identity collision'
            USING ERRCODE = '23505';
END;
$$;

CREATE OR REPLACE FUNCTION public.register_reflection_record(
    p_reflection_id TEXT, p_schema_version TEXT, p_record_kind TEXT,
    p_created_at TIMESTAMPTZ, p_available_at TIMESTAMPTZ, p_as_of_at TIMESTAMPTZ,
    p_cutoff_at TIMESTAMPTZ, p_proposal_id TEXT, p_decision_id TEXT,
    p_research_bundle_hash TEXT, p_portfolio_snapshot_hash TEXT, p_content_hash TEXT,
    p_content_bytes BYTEA,
    p_prompt_version TEXT, p_model_version TEXT, p_provider_version TEXT,
    p_data_version TEXT, p_memory_version TEXT, p_source_fact_ids TEXT[],
    p_source_kinds TEXT[], p_source_hashes TEXT[], p_source_available_ats TIMESTAMPTZ[],
    p_superseded_reflection_id TEXT, p_correction_reason_code TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_count INTEGER;
    v_existing public.reflection_records%ROWTYPE;
    v_existing_correction public.reflection_corrections%ROWTYPE;
    v_superseded public.reflection_records%ROWTYPE;
    v_payload JSON;
    v_payload_ids TEXT[];
    v_payload_kinds TEXT[];
    v_payload_hashes TEXT[];
    v_payload_available TIMESTAMPTZ[];
BEGIN
    BEGIN
        v_payload := pg_catalog.convert_from(p_content_bytes, 'UTF8')::json;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'reflection canonical bytes are not valid UTF-8 JSON'
            USING ERRCODE = '23514';
    END;
    IF pg_catalog.convert_to(public.p3d_canonical_json(v_payload), 'UTF8') <> p_content_bytes
       OR pg_catalog.json_typeof(v_payload) <> 'object'
       OR (SELECT pg_catalog.count(*) FROM pg_catalog.json_object_keys(v_payload)) <> 17
       OR pg_catalog.json_typeof(v_payload->'record_id') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'schema_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'created_at') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'available_at') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'as_of') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'cutoff_at') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'proposal_id') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'decision_id') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'research_bundle_hash') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'portfolio_snapshot_hash') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'prompt_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'model_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'provider_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'data_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'memory_version') <> 'string'
       OR NOT public.p3f_text_is_safe(v_payload->>'record_id', 128)
       OR NOT public.p3f_text_is_safe(v_payload->>'proposal_id', 128)
       OR NOT public.p3f_text_is_safe(v_payload->>'decision_id', 128)
       OR NOT public.p3f_text_is_safe(v_payload->>'schema_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'prompt_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'model_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'provider_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'data_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'memory_version', 64)
       OR v_payload->>'schema_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'prompt_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'model_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'provider_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'data_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'memory_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'created_at'
          !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_payload->>'available_at'
          !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_payload->>'as_of'
          !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_payload->>'cutoff_at'
          !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_payload->>'record_id' IS DISTINCT FROM p_reflection_id
       OR v_payload->>'schema_version' IS DISTINCT FROM p_schema_version
       OR v_payload->>'proposal_id' IS DISTINCT FROM p_proposal_id
       OR v_payload->>'decision_id' IS DISTINCT FROM p_decision_id
       OR v_payload->>'research_bundle_hash' IS DISTINCT FROM p_research_bundle_hash
       OR v_payload->>'portfolio_snapshot_hash' IS DISTINCT FROM p_portfolio_snapshot_hash
       OR v_payload->>'prompt_version' IS DISTINCT FROM p_prompt_version
       OR v_payload->>'model_version' IS DISTINCT FROM p_model_version
       OR v_payload->>'provider_version' IS DISTINCT FROM p_provider_version
       OR v_payload->>'data_version' IS DISTINCT FROM p_data_version
       OR v_payload->>'memory_version' IS DISTINCT FROM p_memory_version
       OR (v_payload->>'created_at')::timestamptz IS DISTINCT FROM p_created_at
       OR (v_payload->>'available_at')::timestamptz IS DISTINCT FROM p_available_at
       OR (v_payload->>'as_of')::timestamptz IS DISTINCT FROM p_as_of_at
       OR (v_payload->>'cutoff_at')::timestamptz IS DISTINCT FROM p_cutoff_at
       OR pg_catalog.json_typeof(v_payload->'sources') <> 'array'
       OR pg_catalog.json_typeof(v_payload->'observations') <> 'array'
       OR pg_catalog.json_array_length(v_payload->'observations') NOT BETWEEN 1 AND 64 THEN
        RAISE EXCEPTION 'reflection canonical bytes do not close over metadata'
            USING ERRCODE = '23514';
    END IF;
    IF p_source_fact_ids IS NULL OR p_source_kinds IS NULL OR p_source_hashes IS NULL
       OR p_source_available_ats IS NULL THEN
        RAISE EXCEPTION 'reflection source lineage is required' USING ERRCODE = '23514';
    END IF;
    v_count := pg_catalog.array_length(p_source_fact_ids, 1);
    IF v_count IS NULL OR v_count < 1 OR v_count > 32
       OR pg_catalog.array_length(p_source_kinds, 1) IS DISTINCT FROM v_count
       OR pg_catalog.array_length(p_source_hashes, 1) IS DISTINCT FROM v_count
       OR pg_catalog.array_length(p_source_available_ats, 1) IS DISTINCT FROM v_count THEN
        RAISE EXCEPTION 'reflection source lineage is invalid' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.unnest(p_source_available_ats) AS t(value)
        WHERE value > p_cutoff_at
    ) THEN
        RAISE EXCEPTION 'reflection source is not available by cutoff' USING ERRCODE = '23514';
    END IF;
    SELECT pg_catalog.array_agg(item->>'source_id' ORDER BY ordinal),
           pg_catalog.array_agg(item->>'source_type' ORDER BY ordinal),
           pg_catalog.array_agg(item->>'content_hash' ORDER BY ordinal),
           pg_catalog.array_agg((item->>'available_at')::timestamptz ORDER BY ordinal)
      INTO v_payload_ids, v_payload_kinds, v_payload_hashes, v_payload_available
      FROM pg_catalog.json_array_elements(v_payload->'sources')
           WITH ORDINALITY AS source(item, ordinal);
    IF v_payload_ids IS DISTINCT FROM p_source_fact_ids
       OR v_payload_kinds IS DISTINCT FROM p_source_kinds
       OR v_payload_hashes IS DISTINCT FROM p_source_hashes
       OR v_payload_available IS DISTINCT FROM p_source_available_ats
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'sources') AS source(item)
           WHERE pg_catalog.json_typeof(item) <> 'object'
              OR (SELECT pg_catalog.count(*) FROM pg_catalog.json_object_keys(item)) <> 6
              OR pg_catalog.json_typeof(item->'source_id') <> 'string'
              OR pg_catalog.json_typeof(item->'source_type') <> 'string'
              OR pg_catalog.json_typeof(item->'content_hash') <> 'string'
              OR pg_catalog.json_typeof(item->'available_at') <> 'string'
              OR NOT public.p3f_text_is_safe(item->>'source_id', 128)
              OR NOT public.p3f_text_is_safe(item->>'source_type', 128)
              OR item->>'source_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              OR item->>'source_type' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              OR item->>'available_at'
                 !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
              OR pg_catalog.json_typeof(item->'facts') <> 'array'
              OR pg_catalog.json_array_length(item->'facts') NOT BETWEEN 1 AND 256
              OR pg_catalog.json_typeof(item->'prompt_injection_flags') <> 'array'
              OR pg_catalog.json_array_length(item->'prompt_injection_flags') <> 0
              OR EXISTS (
                  SELECT 1
                  FROM pg_catalog.json_array_elements(item->'facts') AS fact(value)
                  WHERE pg_catalog.json_typeof(fact.value) <> 'object'
                     OR (SELECT pg_catalog.count(*)
                         FROM pg_catalog.json_object_keys(fact.value)) <> 3
                     OR pg_catalog.json_typeof(fact.value->'fact_id') <> 'string'
                     OR fact.value->>'fact_id'
                        !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                     OR pg_catalog.json_typeof(fact.value->'kind') <> 'string'
                     OR fact.value->>'kind'
                        NOT IN ('TEXT', 'NUMBER', 'DATE', 'SYMBOL', 'RISK_REASON')
                     OR pg_catalog.json_typeof(fact.value->'value') <> 'string'
                     OR pg_catalog.octet_length(fact.value->>'value') NOT BETWEEN 1 AND 256
                     OR (fact.value->>'value') ~ E'[\\n\\r]'
                     OR NOT public.p3f_text_is_safe(fact.value->>'value', 256)
                     OR (fact.value->>'kind' = 'NUMBER'
                         AND fact.value->>'value'
                             !~ '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$')
                     OR (fact.value->>'kind' = 'DATE'
                         AND fact.value->>'value'
                             !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
                     OR (fact.value->>'kind' = 'DATE'
                         AND NOT pg_catalog.pg_input_is_valid(
                             fact.value->>'value', 'date'
                         ))
                     OR (fact.value->>'kind' = 'SYMBOL'
                         AND fact.value->>'value' !~ '^[A-Z][A-Z0-9.-]{0,9}$')
                     OR (fact.value->>'kind' = 'RISK_REASON'
                         AND fact.value->>'value'
                             !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
              )
              OR EXISTS (
                  SELECT 1
                  FROM pg_catalog.json_array_elements(item->'prompt_injection_flags')
                       AS flag(value)
                  WHERE pg_catalog.json_typeof(flag.value) <> 'string'
                     OR flag.value #>> '{}'
                        !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                     OR NOT public.p3f_text_is_safe(flag.value #>> '{}', 128)
              )
              OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.json_array_elements(item->'prompt_injection_flags'))
                 <> (SELECT pg_catalog.count(DISTINCT flag.value #>> '{}')
                     FROM pg_catalog.json_array_elements(item->'prompt_injection_flags')
                          AS flag(value))
       ) THEN
        RAISE EXCEPTION 'reflection bytes and source lineage differ' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.json_array_elements(v_payload->'observations') AS observation(item)
        WHERE pg_catalog.json_typeof(item) <> 'object'
           OR (SELECT pg_catalog.count(*) FROM pg_catalog.json_object_keys(item)) <> 7
           OR item->>'kind' NOT IN (
               'FORECAST', 'OUTCOME', 'RISK_REJECTION', 'OPEN_POSITION', 'CORRECTION'
           )
           OR pg_catalog.json_typeof(item->'applies_when') <> 'array'
           OR pg_catalog.json_array_length(item->'applies_when') > 16
           OR pg_catalog.json_typeof(item->'invalid_when') <> 'array'
           OR pg_catalog.json_array_length(item->'invalid_when') > 16
           OR pg_catalog.json_typeof(item->'fact_ids') <> 'array'
           OR pg_catalog.json_array_length(item->'fact_ids') NOT BETWEEN 1 AND 64
           OR ((item->>'kind') = 'CORRECTION')
              IS DISTINCT FROM ((item->>'supersedes_record_id') IS NOT NULL)
           OR pg_catalog.json_typeof(item->'kind') <> 'string'
           OR pg_catalog.json_typeof(item->'observation') <> 'string'
           OR pg_catalog.octet_length(item->>'observation') NOT BETWEEN 1 AND 2048
           OR (item->>'observation') ~ E'[\\n\\r]'
           OR NOT public.p3f_text_is_safe(item->>'observation', 2048)
           OR NOT public.p3f_instruction_text_is_safe(item->>'observation')
           OR pg_catalog.json_typeof(item->'reusable_lesson') <> 'string'
           OR pg_catalog.octet_length(item->>'reusable_lesson') NOT BETWEEN 1 AND 2048
           OR (item->>'reusable_lesson') ~ E'[\\n\\r]'
           OR NOT public.p3f_text_is_safe(item->>'reusable_lesson', 2048)
           OR NOT public.p3f_instruction_text_is_safe(item->>'reusable_lesson')
           OR pg_catalog.json_typeof(item->'supersedes_record_id')
              NOT IN ('string', 'null')
           OR (item->>'supersedes_record_id' IS NOT NULL
               AND item->>'supersedes_record_id'
                   !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
           OR EXISTS (
               SELECT 1
               FROM pg_catalog.json_array_elements(item->'applies_when') AS value(item)
               WHERE pg_catalog.json_typeof(value.item) <> 'string'
                  OR pg_catalog.octet_length(value.item #>> '{}') NOT BETWEEN 1 AND 2048
                  OR (value.item #>> '{}') ~ E'[\\n\\r]'
                  OR NOT public.p3f_text_is_safe(value.item #>> '{}', 2048)
                  OR NOT public.p3f_instruction_text_is_safe(value.item #>> '{}')
           )
           OR (SELECT pg_catalog.count(*)
               FROM pg_catalog.json_array_elements(item->'applies_when'))
              <> (SELECT pg_catalog.count(DISTINCT value.item #>> '{}')
                  FROM pg_catalog.json_array_elements(item->'applies_when') AS value(item))
           OR EXISTS (
               SELECT 1
               FROM pg_catalog.json_array_elements(item->'invalid_when') AS value(item)
               WHERE pg_catalog.json_typeof(value.item) <> 'string'
                  OR pg_catalog.octet_length(value.item #>> '{}') NOT BETWEEN 1 AND 2048
                  OR (value.item #>> '{}') ~ E'[\\n\\r]'
                  OR NOT public.p3f_text_is_safe(value.item #>> '{}', 2048)
                  OR NOT public.p3f_instruction_text_is_safe(value.item #>> '{}')
           )
           OR (SELECT pg_catalog.count(*)
               FROM pg_catalog.json_array_elements(item->'invalid_when'))
              <> (SELECT pg_catalog.count(DISTINCT value.item #>> '{}')
                  FROM pg_catalog.json_array_elements(item->'invalid_when') AS value(item))
           OR EXISTS (
               SELECT 1
               FROM pg_catalog.json_array_elements(item->'fact_ids') AS value(item)
               WHERE pg_catalog.json_typeof(value.item) <> 'string'
                  OR value.item #>> '{}'
                     !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                  OR NOT public.p3f_text_is_safe(value.item #>> '{}', 128)
           )
           OR (SELECT pg_catalog.count(*)
               FROM pg_catalog.json_array_elements(item->'fact_ids'))
              <> (SELECT pg_catalog.count(DISTINCT value.item #>> '{}')
                  FROM pg_catalog.json_array_elements(item->'fact_ids') AS value(item))
    ) THEN
        RAISE EXCEPTION 'reflection observation contract is invalid' USING ERRCODE = '23514';
    END IF;
    IF (
        SELECT pg_catalog.count(*)
        FROM pg_catalog.json_array_elements(v_payload->'sources') AS source(item)
        CROSS JOIN LATERAL pg_catalog.json_array_elements(source.item->'facts') AS fact(value)
    ) <> (
        SELECT pg_catalog.count(DISTINCT fact.value->>'fact_id')
        FROM pg_catalog.json_array_elements(v_payload->'sources') AS source(item)
        CROSS JOIN LATERAL pg_catalog.json_array_elements(source.item->'facts') AS fact(value)
    ) THEN
        RAISE EXCEPTION 'reflection fact ids must be globally unique'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.json_array_elements(v_payload->'observations') AS observation(item)
        WHERE NOT public.p3f_fact_text_is_closed(
            pg_catalog.concat_ws(
                E'\n',
                observation.item->>'observation',
                observation.item->>'reusable_lesson',
                (SELECT pg_catalog.string_agg(value #>> '{}', E'\n' ORDER BY ordinal)
                 FROM pg_catalog.json_array_elements(observation.item->'applies_when')
                      WITH ORDINALITY AS applies(value, ordinal)),
                (SELECT pg_catalog.string_agg(value #>> '{}', E'\n' ORDER BY ordinal)
                 FROM pg_catalog.json_array_elements(observation.item->'invalid_when')
                      WITH ORDINALITY AS invalid(value, ordinal))
            ),
            ARRAY(
                SELECT value #>> '{}'
                FROM pg_catalog.json_array_elements(observation.item->'fact_ids') AS fact(value)
            ),
            (SELECT pg_catalog.json_agg(fact.value)
             FROM pg_catalog.json_array_elements(v_payload->'sources') AS source(item)
             CROSS JOIN LATERAL pg_catalog.json_array_elements(source.item->'facts') AS fact(value)),
            NULL
        )
    ) THEN
        RAISE EXCEPTION 'reflection observation fact closure is invalid'
            USING ERRCODE = '23514';
    END IF;
    IF p_record_kind = 'CORRECTION' THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.json_array_elements(v_payload->'observations') AS o(item)
            WHERE item->>'kind' = 'CORRECTION'
        ) OR EXISTS (
            SELECT 1 FROM pg_catalog.json_array_elements(v_payload->'observations') AS o(item)
            WHERE item->>'kind' <> 'CORRECTION'
               OR item->>'supersedes_record_id' IS DISTINCT FROM p_superseded_reflection_id
        ) THEN
            RAISE EXCEPTION 'canonical correction link differs from relational lineage'
                USING ERRCODE = '23514';
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM pg_catalog.json_array_elements(v_payload->'observations') AS o(item)
        WHERE item->>'kind' = 'CORRECTION'
           OR item->>'supersedes_record_id' IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'daily reflection bytes contain a hidden correction link'
            USING ERRCODE = '23514';
    END IF;
    IF p_record_kind = 'CORRECTION' THEN
        IF p_superseded_reflection_id IS NULL OR p_correction_reason_code IS NULL THEN
            RAISE EXCEPTION 'correction lineage is required' USING ERRCODE = '23514';
        END IF;
        SELECT * INTO v_superseded FROM public.reflection_records
        WHERE reflection_id = p_superseded_reflection_id;
        IF NOT FOUND OR v_superseded.cutoff_at > p_cutoff_at
           OR v_superseded.available_at > p_cutoff_at THEN
            RAISE EXCEPTION 'superseded reflection is missing or from the future'
                USING ERRCODE = '23514';
        END IF;
    ELSIF p_superseded_reflection_id IS NOT NULL OR p_correction_reason_code IS NOT NULL THEN
        RAISE EXCEPTION 'daily reflection cannot carry correction lineage' USING ERRCODE = '23514';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_reflection_id::text, 0)
    );
    SELECT * INTO v_existing FROM public.reflection_records
    WHERE reflection_id = p_reflection_id;
    IF FOUND THEN
        IF ROW(v_existing.schema_version, v_existing.record_kind, v_existing.created_at,
               v_existing.available_at, v_existing.as_of_at, v_existing.cutoff_at,
               v_existing.proposal_id, v_existing.decision_id, v_existing.research_bundle_hash,
               v_existing.portfolio_snapshot_hash, v_existing.content_hash,
               v_existing.content_bytes,
               v_existing.prompt_version, v_existing.model_version,
               v_existing.provider_version, v_existing.data_version,
               v_existing.memory_version, v_existing.source_count)
           IS DISTINCT FROM
           ROW(p_schema_version, p_record_kind, p_created_at, p_available_at, p_as_of_at,
               p_cutoff_at, p_proposal_id, p_decision_id, p_research_bundle_hash,
               p_portfolio_snapshot_hash, p_content_hash, p_content_bytes, p_prompt_version,
               p_model_version, p_provider_version, p_data_version, p_memory_version,
               v_count)
           OR (SELECT pg_catalog.array_agg(source_fact_id ORDER BY source_ordinal)
               FROM public.reflection_sources WHERE reflection_id = p_reflection_id)
              IS DISTINCT FROM p_source_fact_ids
           OR (SELECT pg_catalog.array_agg(source_kind ORDER BY source_ordinal)
               FROM public.reflection_sources WHERE reflection_id = p_reflection_id)
              IS DISTINCT FROM p_source_kinds
           OR (SELECT pg_catalog.array_agg(source_content_hash ORDER BY source_ordinal)
               FROM public.reflection_sources WHERE reflection_id = p_reflection_id)
              IS DISTINCT FROM p_source_hashes
           OR (SELECT pg_catalog.array_agg(source_available_at ORDER BY source_ordinal)
               FROM public.reflection_sources WHERE reflection_id = p_reflection_id)
              IS DISTINCT FROM p_source_available_ats THEN
            RAISE EXCEPTION 'reflection identity collision' USING ERRCODE = '23505';
        END IF;
        SELECT * INTO v_existing_correction
        FROM public.reflection_corrections
        WHERE correction_reflection_id = p_reflection_id;
        IF p_record_kind = 'CORRECTION' THEN
            IF NOT FOUND
               OR v_existing_correction.superseded_reflection_id
                  IS DISTINCT FROM p_superseded_reflection_id
               OR v_existing_correction.reason_code IS DISTINCT FROM p_correction_reason_code
               OR v_existing_correction.linked_at IS DISTINCT FROM p_created_at THEN
                RAISE EXCEPTION 'reflection correction identity collision'
                    USING ERRCODE = '23505';
            END IF;
        ELSIF FOUND THEN
            RAISE EXCEPTION 'daily reflection has unexpected correction lineage'
                USING ERRCODE = '23505';
        END IF;
        RETURN FALSE;
    END IF;
    INSERT INTO public.reflection_records(
        reflection_id, schema_version, record_kind, created_at, available_at, as_of_at,
        cutoff_at, proposal_id, decision_id, research_bundle_hash, portfolio_snapshot_hash,
        content_hash, content_bytes, prompt_version, model_version, provider_version, data_version,
        memory_version, source_count
    ) VALUES (
        p_reflection_id, p_schema_version, p_record_kind, p_created_at, p_available_at,
        p_as_of_at, p_cutoff_at, p_proposal_id, p_decision_id, p_research_bundle_hash,
        p_portfolio_snapshot_hash, p_content_hash, p_content_bytes, p_prompt_version,
        p_model_version,
        p_provider_version, p_data_version, p_memory_version, v_count
    );
    INSERT INTO public.reflection_sources(
        reflection_id, source_fact_id, source_kind, source_content_hash, source_available_at,
        source_ordinal
    )
    SELECT p_reflection_id, ids.value, kinds.value, hashes.value, ats.value,
           ids.ordinal::integer
    FROM pg_catalog.unnest(p_source_fact_ids) WITH ORDINALITY AS ids(value, ordinal)
    JOIN pg_catalog.unnest(p_source_kinds) WITH ORDINALITY AS kinds(value, ordinal)
      USING (ordinal)
    JOIN pg_catalog.unnest(p_source_hashes) WITH ORDINALITY AS hashes(value, ordinal)
      USING (ordinal)
    JOIN pg_catalog.unnest(p_source_available_ats) WITH ORDINALITY AS ats(value, ordinal)
      USING (ordinal);
    IF p_record_kind = 'CORRECTION' THEN
        INSERT INTO public.reflection_corrections(
            correction_reflection_id, superseded_reflection_id, reason_code, linked_at
        ) VALUES (
            p_reflection_id, p_superseded_reflection_id, p_correction_reason_code, p_created_at
        );
    END IF;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.register_memory_candidate(
    p_artifact_id TEXT, p_schema_version TEXT, p_created_at TIMESTAMPTZ,
    p_cutoff_at TIMESTAMPTZ, p_previous_artifact_id TEXT, p_content_hash TEXT,
    p_cas_hash TEXT, p_content_bytes BYTEA, p_byte_count INTEGER,
    p_line_count INTEGER, p_entry_count INTEGER,
    p_prompt_version TEXT, p_model_version TEXT, p_provider_version TEXT,
    p_source_record_ids TEXT[]
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_count INTEGER;
    v_existing public.memory_artifacts%ROWTYPE;
    v_payload JSON;
    v_payload_source_ids TEXT[];
BEGIN
    BEGIN
        v_payload := pg_catalog.convert_from(p_content_bytes, 'UTF8')::json;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'artifact canonical bytes are not valid UTF-8 JSON'
            USING ERRCODE = '23514';
    END;
    SELECT pg_catalog.array_agg(value #>> '{}' ORDER BY ordinal)
      INTO v_payload_source_ids
      FROM pg_catalog.json_array_elements(v_payload->'source_record_ids')
           WITH ORDINALITY AS source(value, ordinal);
    IF pg_catalog.convert_to(public.p3d_canonical_json(v_payload), 'UTF8') <> p_content_bytes
       OR pg_catalog.json_typeof(v_payload) <> 'object'
       OR (SELECT pg_catalog.count(*) FROM pg_catalog.json_object_keys(v_payload)) <> 11
       OR pg_catalog.json_typeof(v_payload->'artifact_id') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'schema_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'created_at') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'cutoff_at') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'previous_artifact_id')
          NOT IN ('string', 'null')
       OR pg_catalog.json_typeof(v_payload->'line_count') <> 'number'
       OR pg_catalog.json_typeof(v_payload->'prompt_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'model_version') <> 'string'
       OR pg_catalog.json_typeof(v_payload->'provider_version') <> 'string'
       OR NOT public.p3f_text_is_safe(v_payload->>'artifact_id', 128)
       OR NOT public.p3f_text_is_safe(v_payload->>'schema_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'prompt_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'model_version', 64)
       OR NOT public.p3f_text_is_safe(v_payload->>'provider_version', 64)
       OR (v_payload->>'previous_artifact_id' IS NOT NULL
           AND NOT public.p3f_text_is_safe(v_payload->>'previous_artifact_id', 128))
       OR v_payload->>'schema_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'prompt_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'model_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'provider_version' !~ '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$'
       OR v_payload->>'created_at'
          !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_payload->>'cutoff_at'
          !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
       OR v_payload->>'artifact_id' IS DISTINCT FROM p_artifact_id
       OR v_payload->>'schema_version' IS DISTINCT FROM p_schema_version
       OR (v_payload->>'created_at')::timestamptz IS DISTINCT FROM p_created_at
       OR (v_payload->>'cutoff_at')::timestamptz IS DISTINCT FROM p_cutoff_at
       OR v_payload->>'previous_artifact_id' IS DISTINCT FROM p_previous_artifact_id
       OR v_payload->>'prompt_version' IS DISTINCT FROM p_prompt_version
       OR v_payload->>'model_version' IS DISTINCT FROM p_model_version
       OR v_payload->>'provider_version' IS DISTINCT FROM p_provider_version
       OR (v_payload->>'line_count')::integer IS DISTINCT FROM p_line_count
       OR pg_catalog.json_typeof(v_payload->'entries') <> 'array'
       OR pg_catalog.json_array_length(v_payload->'entries') IS DISTINCT FROM p_entry_count
       OR p_line_count IS DISTINCT FROM 5 + 9 * p_entry_count
       OR pg_catalog.json_typeof(v_payload->'source_record_ids') <> 'array'
       OR v_payload_source_ids IS DISTINCT FROM p_source_record_ids
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'source_record_ids') AS source(value)
           WHERE pg_catalog.json_typeof(source.value) <> 'string'
       )
       OR EXISTS (
           SELECT 1 FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           WHERE pg_catalog.json_typeof(value) <> 'object'
              OR (SELECT pg_catalog.count(*) FROM pg_catalog.json_object_keys(value)) <> 9
              OR pg_catalog.json_typeof(value->'category') <> 'string'
              OR value->>'category' NOT IN (
                  'RISK_REJECTION', 'FORECAST_CALIBRATION', 'POSITION_MANAGEMENT',
                  'SAME_DAY_LOSS', 'BORROW_LIQUIDITY', 'MARKET_REGIME',
                  'UNRESOLVED_RISK', 'GENERAL'
              )
              OR pg_catalog.json_typeof(value->'importance') <> 'number'
              OR value->>'importance' !~ '^(0|[1-9][0-9]{0,2})$'
              OR (value->>'importance')::integer NOT BETWEEN 0 AND 100
              OR pg_catalog.json_typeof(value->'observation') <> 'string'
              OR NOT public.p3f_text_is_safe(value->>'observation', 2048)
              OR NOT public.p3f_instruction_text_is_safe(value->>'observation')
              OR pg_catalog.json_typeof(value->'reusable_lesson') <> 'string'
              OR NOT public.p3f_text_is_safe(value->>'reusable_lesson', 2048)
              OR NOT public.p3f_instruction_text_is_safe(value->>'reusable_lesson')
              OR pg_catalog.json_typeof(value->'evidence_ids') <> 'array'
              OR pg_catalog.json_array_length(value->'evidence_ids') NOT BETWEEN 1 AND 32
              OR pg_catalog.json_typeof(value->'source_record_ids') <> 'array'
              OR pg_catalog.json_array_length(value->'source_record_ids') NOT BETWEEN 1 AND 16
              OR pg_catalog.json_typeof(value->'risk_reason_codes') <> 'array'
              OR pg_catalog.json_array_length(value->'risk_reason_codes') > 16
              OR pg_catalog.json_typeof(value->'applies_when') <> 'array'
              OR pg_catalog.json_array_length(value->'applies_when') > 16
              OR pg_catalog.json_typeof(value->'invalid_when') <> 'array'
              OR pg_catalog.json_array_length(value->'invalid_when') > 16
              OR EXISTS (
                  SELECT 1
                  FROM pg_catalog.json_array_elements(value->'applies_when') AS item(value)
                  WHERE pg_catalog.json_typeof(item.value) <> 'string'
                     OR NOT public.p3f_text_is_safe(item.value #>> '{}', 2048)
                     OR NOT public.p3f_instruction_text_is_safe(item.value #>> '{}')
              )
              OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.json_array_elements(value->'applies_when'))
                 <> (SELECT pg_catalog.count(DISTINCT item.value #>> '{}')
                     FROM pg_catalog.json_array_elements(value->'applies_when') AS item(value))
              OR EXISTS (
                  SELECT 1
                  FROM pg_catalog.json_array_elements(value->'invalid_when') AS item(value)
                  WHERE pg_catalog.json_typeof(item.value) <> 'string'
                     OR NOT public.p3f_text_is_safe(item.value #>> '{}', 2048)
                     OR NOT public.p3f_instruction_text_is_safe(item.value #>> '{}')
              )
              OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.json_array_elements(value->'invalid_when'))
                 <> (SELECT pg_catalog.count(DISTINCT item.value #>> '{}')
                     FROM pg_catalog.json_array_elements(value->'invalid_when') AS item(value))
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           CROSS JOIN LATERAL pg_catalog.json_array_elements_text(
               entry.value->'source_record_ids'
           ) AS source(record_id)
           WHERE NOT (source.record_id = ANY(p_source_record_ids))
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           CROSS JOIN LATERAL pg_catalog.json_array_elements(
               entry.value->'evidence_ids'
           ) AS evidence(item)
           WHERE pg_catalog.json_typeof(evidence.item) <> 'string'
              OR pg_catalog.octet_length(evidence.item #>> '{}') NOT BETWEEN 1 AND 128
              OR evidence.item #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              OR NOT public.p3f_text_is_safe(evidence.item #>> '{}', 128)
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           WHERE (SELECT pg_catalog.count(*)
                  FROM pg_catalog.json_array_elements(entry.value->'evidence_ids'))
                 <> (SELECT pg_catalog.count(DISTINCT evidence.item #>> '{}')
                     FROM pg_catalog.json_array_elements(entry.value->'evidence_ids')
                          AS evidence(item))
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           CROSS JOIN LATERAL pg_catalog.json_array_elements(
               entry.value->'risk_reason_codes'
           ) AS risk(item)
           WHERE pg_catalog.json_typeof(risk.item) <> 'string'
              OR pg_catalog.octet_length(risk.item #>> '{}') NOT BETWEEN 1 AND 128
              OR risk.item #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              OR NOT public.p3f_text_is_safe(risk.item #>> '{}', 128)
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           WHERE (SELECT pg_catalog.count(*)
                  FROM pg_catalog.json_array_elements(entry.value->'risk_reason_codes'))
                 <> (SELECT pg_catalog.count(DISTINCT risk.item #>> '{}')
                     FROM pg_catalog.json_array_elements(entry.value->'risk_reason_codes')
                          AS risk(item))
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           CROSS JOIN LATERAL pg_catalog.json_array_elements(
               entry.value->'source_record_ids'
           ) AS source(item)
           WHERE pg_catalog.json_typeof(source.item) <> 'string'
              OR pg_catalog.octet_length(source.item #>> '{}') NOT BETWEEN 1 AND 128
              OR source.item #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              OR NOT public.p3f_text_is_safe(source.item #>> '{}', 128)
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           WHERE (SELECT pg_catalog.count(*)
                  FROM pg_catalog.json_array_elements(entry.value->'source_record_ids'))
                 <> (SELECT pg_catalog.count(DISTINCT source.item #>> '{}')
                     FROM pg_catalog.json_array_elements(entry.value->'source_record_ids')
                          AS source(item))
       )
       OR EXISTS (
           SELECT 1
           FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
           WHERE pg_catalog.octet_length(
                     'applies_when: ' || COALESCE((
                         SELECT pg_catalog.string_agg(item.value #>> '{}', ' | ' ORDER BY ordinal)
                         FROM pg_catalog.json_array_elements(entry.value->'applies_when')
                              WITH ORDINALITY AS item(value, ordinal)
                     ), '')
                 ) > 2048
              OR pg_catalog.octet_length(
                     'invalid_when: ' || COALESCE((
                         SELECT pg_catalog.string_agg(item.value #>> '{}', ' | ' ORDER BY ordinal)
                         FROM pg_catalog.json_array_elements(entry.value->'invalid_when')
                              WITH ORDINALITY AS item(value, ordinal)
                     ), '')
                 ) > 2048
              OR pg_catalog.octet_length(
                     'evidence_ids: ' || COALESCE((
                         SELECT pg_catalog.string_agg(item.value #>> '{}', ' | ' ORDER BY ordinal)
                         FROM pg_catalog.json_array_elements(entry.value->'evidence_ids')
                              WITH ORDINALITY AS item(value, ordinal)
                     ), '')
                 ) > 2048
              OR pg_catalog.octet_length(
                     'source_record_ids: ' || COALESCE((
                         SELECT pg_catalog.string_agg(item.value #>> '{}', ' | ' ORDER BY ordinal)
                         FROM pg_catalog.json_array_elements(entry.value->'source_record_ids')
                              WITH ORDINALITY AS item(value, ordinal)
                     ), '')
                 ) > 2048
              OR pg_catalog.octet_length(
                     'risk_reason_codes: ' || COALESCE((
                         SELECT pg_catalog.string_agg(item.value #>> '{}', ' | ' ORDER BY ordinal)
                         FROM pg_catalog.json_array_elements(entry.value->'risk_reason_codes')
                              WITH ORDINALITY AS item(value, ordinal)
                     ), '')
                 ) > 2048
       ) THEN
        RAISE EXCEPTION 'artifact canonical bytes do not close over metadata and counts'
            USING ERRCODE = '23514';
    END IF;
    v_count := pg_catalog.array_length(p_source_record_ids, 1);
    IF v_count IS NULL OR v_count < 1 OR v_count > 4096
       OR (SELECT pg_catalog.count(DISTINCT value)
           FROM pg_catalog.unnest(p_source_record_ids) AS t(value)) <> v_count THEN
        RAISE EXCEPTION 'artifact source lineage is invalid' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.unnest(p_source_record_ids) AS t(reflection_id)
        LEFT JOIN public.reflection_records AS r USING (reflection_id)
        WHERE r.reflection_id IS NULL OR r.available_at > p_cutoff_at OR r.cutoff_at > p_cutoff_at
    ) THEN
        RAISE EXCEPTION 'artifact source lineage is missing or future-dated'
            USING ERRCODE = '23514';
    END IF;
    IF p_previous_artifact_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.memory_artifacts
        WHERE artifact_id = p_previous_artifact_id AND cutoff_at <= p_cutoff_at
    ) THEN
        RAISE EXCEPTION 'previous artifact is missing or newer than candidate'
            USING ERRCODE = '23514';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_artifact_id::text, 0)
    );
    SELECT * INTO v_existing FROM public.memory_artifacts WHERE artifact_id = p_artifact_id;
    IF FOUND THEN
        IF ROW(v_existing.schema_version, v_existing.created_at, v_existing.cutoff_at,
               v_existing.previous_artifact_id, v_existing.content_hash,
               v_existing.cas_hash, v_existing.content_bytes, v_existing.byte_count,
               v_existing.line_count,
               v_existing.entry_count, v_existing.source_record_count,
               v_existing.prompt_version, v_existing.model_version,
               v_existing.provider_version)
           IS DISTINCT FROM
           ROW(p_schema_version, p_created_at, p_cutoff_at, p_previous_artifact_id,
               p_content_hash, p_cas_hash, p_content_bytes, p_byte_count,
               p_line_count, p_entry_count,
               v_count, p_prompt_version, p_model_version, p_provider_version)
           OR (SELECT pg_catalog.array_agg(reflection_id ORDER BY source_ordinal)
               FROM public.memory_artifact_sources WHERE artifact_id = p_artifact_id)
              IS DISTINCT FROM p_source_record_ids THEN
            RAISE EXCEPTION 'memory artifact identity collision' USING ERRCODE = '23505';
        END IF;
        RETURN FALSE;
    END IF;
    INSERT INTO public.memory_artifacts(
        artifact_id, schema_version, created_at, cutoff_at, previous_artifact_id,
        content_hash, cas_hash, content_bytes, byte_count, line_count, entry_count,
        source_record_count, prompt_version, model_version, provider_version
    ) VALUES (
        p_artifact_id, p_schema_version, p_created_at, p_cutoff_at, p_previous_artifact_id,
        p_content_hash, p_cas_hash, p_content_bytes, p_byte_count, p_line_count,
        p_entry_count, v_count, p_prompt_version, p_model_version, p_provider_version
    );
    INSERT INTO public.memory_artifact_sources(artifact_id, reflection_id, source_ordinal)
    SELECT p_artifact_id, value, ordinal::integer
    FROM pg_catalog.unnest(p_source_record_ids) WITH ORDINALITY AS t(value, ordinal);
    INSERT INTO public.memory_artifact_state_events(
        state_event_id, artifact_id, state, reason_code
    ) VALUES (
        public.p3d_derive_run_id('seven-lens.p3f.memory-state.v1',
            p_artifact_id::text, 'CANDIDATE'),
        p_artifact_id, 'CANDIDATE', 'REGISTERED'
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.validate_memory_artifact(
    p_artifact_id TEXT, p_content_hash TEXT,
    p_validator_version TEXT, p_validation_report_hash TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_artifact public.memory_artifacts%ROWTYPE;
    v_existing_validation public.memory_artifact_state_events%ROWTYPE;
    v_payload JSON;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_artifact_id::text, 0)
    );
    SELECT * INTO v_artifact FROM public.memory_artifacts
    WHERE artifact_id = p_artifact_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'memory artifact does not exist' USING ERRCODE = '23503';
    END IF;
    IF v_artifact.content_hash IS DISTINCT FROM v_artifact.cas_hash
       OR v_artifact.content_hash IS DISTINCT FROM p_content_hash
       OR pg_catalog.octet_length(v_artifact.content_bytes) <> v_artifact.byte_count
       OR pg_catalog.encode(public.digest(v_artifact.content_bytes, 'sha256'), 'hex')
          <> v_artifact.content_hash THEN
        RAISE EXCEPTION 'memory artifact integrity metadata mismatch' USING ERRCODE = '23514';
    END IF;
    v_payload := pg_catalog.convert_from(v_artifact.content_bytes, 'UTF8')::json;
    IF (SELECT pg_catalog.count(*) FROM public.memory_artifact_sources
        WHERE artifact_id = p_artifact_id) <> v_artifact.source_record_count
       OR EXISTS (
           SELECT 1 FROM public.memory_artifact_sources AS s
           JOIN public.reflection_records AS r USING (reflection_id)
           WHERE s.artifact_id = p_artifact_id
             AND (r.available_at > v_artifact.cutoff_at OR r.cutoff_at > v_artifact.cutoff_at)
       ) THEN
        RAISE EXCEPTION 'memory artifact lineage is incomplete or future-dated'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.memory_artifact_state_events
               WHERE artifact_id = p_artifact_id AND state = 'INVALID') THEN
        RAISE EXCEPTION 'invalid memory artifact cannot be validated' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        WITH entries AS (
            SELECT entry.value,
                   ARRAY(
                       SELECT value #>> '{}'
                       FROM pg_catalog.json_array_elements(entry.value->'evidence_ids') AS e(value)
                   ) AS evidence_ids,
                   ARRAY(
                       SELECT value #>> '{}'
                       FROM pg_catalog.json_array_elements(entry.value->'risk_reason_codes') AS r(value)
                   ) AS risk_reasons,
                   ARRAY(
                       SELECT value #>> '{}'
                       FROM pg_catalog.json_array_elements(entry.value->'source_record_ids') AS s(value)
                   ) AS source_record_ids
            FROM pg_catalog.json_array_elements(v_payload->'entries') AS entry(value)
        )
        SELECT 1
        FROM entries AS e
        CROSS JOIN LATERAL (
            SELECT pg_catalog.json_agg(fact.value) AS facts,
                   pg_catalog.count(*) AS fact_count,
                   pg_catalog.count(DISTINCT fact.value->>'fact_id') AS distinct_fact_count
            FROM public.reflection_records AS record
            CROSS JOIN LATERAL pg_catalog.json_array_elements(
                (pg_catalog.convert_from(record.content_bytes, 'UTF8')::json)->'sources'
            ) AS source(item)
            CROSS JOIN LATERAL pg_catalog.json_array_elements(source.item->'facts') AS fact(value)
            WHERE record.reflection_id = ANY(e.source_record_ids)
        ) AS fact_set
        CROSS JOIN LATERAL (
            SELECT pg_catalog.count(DISTINCT (
                       record.reflection_id, observation.ordinal
                   )) FILTER (WHERE EXISTS (
                       SELECT 1
                       FROM pg_catalog.json_array_elements(observation.item->'fact_ids') f(value)
                       WHERE f.value #>> '{}' = ANY(e.evidence_ids)
                   ))::integer AS recurrence_count,
                   pg_catalog.bool_or(observation.item->>'kind' = 'RISK_REJECTION')
                       FILTER (WHERE EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(
                               observation.item->'fact_ids'
                           ) f(value) WHERE f.value #>> '{}' = ANY(e.evidence_ids)
                       )) AS has_risk_rejection,
                   pg_catalog.bool_or(observation.item->>'kind' = 'OPEN_POSITION')
                       FILTER (WHERE EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(
                               observation.item->'fact_ids'
                           ) f(value) WHERE f.value #>> '{}' = ANY(e.evidence_ids)
                       )) AS has_open_position,
                   pg_catalog.bool_or(observation.item->>'kind' IN ('FORECAST', 'OUTCOME'))
                       FILTER (WHERE EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(
                               observation.item->'fact_ids'
                           ) f(value) WHERE f.value #>> '{}' = ANY(e.evidence_ids)
                       )) AS has_forecast,
                   pg_catalog.bool_or(source.item->>'source_type' = 'unresolved_risk'
                       AND EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(source.item->'facts') f(value)
                           WHERE f.value->>'fact_id' = ANY(e.evidence_ids)
                       )) AS marker_unresolved,
                   pg_catalog.bool_or(source.item->>'source_type' = 'same_day_loss'
                       AND EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(source.item->'facts') f(value)
                           WHERE f.value->>'fact_id' = ANY(e.evidence_ids)
                       )) AS marker_same_day,
                   pg_catalog.bool_or(source.item->>'source_type' = 'borrow_liquidity'
                       AND EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(source.item->'facts') f(value)
                           WHERE f.value->>'fact_id' = ANY(e.evidence_ids)
                       )) AS marker_borrow,
                   pg_catalog.bool_or(source.item->>'source_type' = 'market_regime'
                       AND EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(source.item->'facts') f(value)
                           WHERE f.value->>'fact_id' = ANY(e.evidence_ids)
                       )) AS marker_regime,
                   pg_catalog.bool_or(source.item->>'source_type' = 'open_position'
                       AND EXISTS (
                           SELECT 1 FROM pg_catalog.json_array_elements(source.item->'facts') f(value)
                           WHERE f.value->>'fact_id' = ANY(e.evidence_ids)
                       )) AS marker_position
            FROM public.reflection_records AS record
            CROSS JOIN LATERAL pg_catalog.json_array_elements(
                (pg_catalog.convert_from(record.content_bytes, 'UTF8')::json)->'sources'
            ) AS source(item)
            CROSS JOIN LATERAL pg_catalog.json_array_elements(
                (pg_catalog.convert_from(record.content_bytes, 'UTF8')::json)->'observations'
            ) WITH ORDINALITY AS observation(item, ordinal)
            WHERE record.reflection_id = ANY(e.source_record_ids)
        ) AS policy
        CROSS JOIN LATERAL (
            SELECT CASE
                       WHEN COALESCE(policy.marker_unresolved, FALSE)
                           THEN 'UNRESOLVED_RISK'
                       WHEN COALESCE(policy.marker_same_day, FALSE)
                           THEN 'SAME_DAY_LOSS'
                       WHEN COALESCE(policy.marker_borrow, FALSE)
                           THEN 'BORROW_LIQUIDITY'
                       WHEN COALESCE(policy.marker_regime, FALSE)
                           THEN 'MARKET_REGIME'
                       WHEN COALESCE(policy.marker_position, FALSE)
                           THEN 'POSITION_MANAGEMENT'
                       WHEN COALESCE(policy.has_risk_rejection, FALSE)
                           THEN 'RISK_REJECTION'
                       WHEN COALESCE(policy.has_open_position, FALSE)
                           THEN 'POSITION_MANAGEMENT'
                       WHEN COALESCE(policy.has_forecast, FALSE)
                           THEN 'FORECAST_CALIBRATION'
                       ELSE 'GENERAL'
                   END AS category
        ) AS derived
        CROSS JOIN LATERAL (
            SELECT CASE derived.category
                       WHEN 'RISK_REJECTION' THEN 78
                       WHEN 'FORECAST_CALIBRATION' THEN 72
                       WHEN 'POSITION_MANAGEMENT' THEN 68
                       WHEN 'SAME_DAY_LOSS' THEN 82
                       WHEN 'BORROW_LIQUIDITY' THEN 82
                       WHEN 'MARKET_REGIME' THEN 66
                       WHEN 'UNRESOLVED_RISK' THEN 86
                       ELSE 40
                   END
                   + LEAST(GREATEST(policy.recurrence_count, 1) - 1, 8) * 2
                   + LEAST(pg_catalog.cardinality(e.risk_reasons), 3) * 2
                   + CASE WHEN COALESCE(policy.marker_unresolved, FALSE)
                                  OR COALESCE(policy.has_open_position, FALSE)
                               THEN 6 ELSE 0 END AS importance
        ) AS score
        WHERE fact_set.fact_count <> fact_set.distinct_fact_count
           OR NOT public.p3f_fact_text_is_closed(
                  pg_catalog.concat_ws(
                      E'\n', e.value->>'observation', e.value->>'reusable_lesson',
                      (SELECT pg_catalog.string_agg(value #>> '{}', E'\n' ORDER BY ordinal)
                       FROM pg_catalog.json_array_elements(e.value->'applies_when')
                            WITH ORDINALITY AS applies(value, ordinal)),
                      (SELECT pg_catalog.string_agg(value #>> '{}', E'\n' ORDER BY ordinal)
                       FROM pg_catalog.json_array_elements(e.value->'invalid_when')
                            WITH ORDINALITY AS invalid(value, ordinal))
                  ),
                  e.evidence_ids, fact_set.facts, e.risk_reasons
              )
           OR e.value->>'category' IS DISTINCT FROM derived.category
           OR (e.value->>'importance')::integer
              IS DISTINCT FROM LEAST(score.importance, 100)
    ) THEN
        RAISE EXCEPTION 'memory artifact deterministic validation failed'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO v_existing_validation
    FROM public.memory_artifact_state_events
    WHERE artifact_id = p_artifact_id AND state = 'VALIDATED';
    IF FOUND THEN
        IF v_existing_validation.validator_version IS DISTINCT FROM p_validator_version
           OR v_existing_validation.validation_report_hash
              IS DISTINCT FROM p_validation_report_hash THEN
            RAISE EXCEPTION 'memory validation identity collision'
                USING ERRCODE = '23505';
        END IF;
        RETURN FALSE;
    END IF;
    INSERT INTO public.memory_artifact_state_events(
        state_event_id, artifact_id, state, validator_version,
        validation_report_hash, reason_code
    ) VALUES (
        public.p3d_derive_run_id('seven-lens.p3f.memory-state.v1',
            p_artifact_id::text, 'VALIDATED'),
        p_artifact_id, 'VALIDATED', p_validator_version,
        p_validation_report_hash,
        'DETERMINISTIC_VALIDATION'
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.promote_memory_artifact(
    p_artifact_id TEXT, p_expected_current_artifact_id TEXT,
    p_requested_as_of TIMESTAMPTZ
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_current TEXT;
    v_artifact public.memory_artifacts%ROWTYPE;
    v_promotion_id UUID;
    v_promotion_order BIGINT;
BEGIN
    SELECT artifact_id INTO v_current FROM public.memory_current_pointer
    WHERE singleton FOR UPDATE;
    IF v_current IS NOT DISTINCT FROM p_artifact_id THEN
        RETURN FALSE;
    END IF;
    IF v_current IS DISTINCT FROM p_expected_current_artifact_id THEN
        RAISE EXCEPTION 'memory current changed during promotion' USING ERRCODE = '40001';
    END IF;
    SELECT * INTO v_artifact FROM public.memory_artifacts
    WHERE artifact_id = p_artifact_id FOR SHARE;
    IF NOT FOUND OR v_artifact.cutoff_at > p_requested_as_of
       OR v_artifact.created_at > p_requested_as_of
       OR v_artifact.previous_artifact_id IS DISTINCT FROM v_current THEN
        RAISE EXCEPTION 'memory artifact is not safe for requested promotion'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.memory_artifact_state_events
                   WHERE artifact_id = p_artifact_id AND state = 'VALIDATED')
       OR EXISTS (SELECT 1 FROM public.memory_artifact_state_events
                  WHERE artifact_id = p_artifact_id AND state = 'INVALID') THEN
        RAISE EXCEPTION 'only a validated memory artifact can be promoted'
            USING ERRCODE = '23514';
    END IF;
    v_promotion_id := public.p3d_derive_run_id(
        'seven-lens.p3f.memory-promotion.v1', p_artifact_id::text
    );
    SELECT COALESCE(pg_catalog.max(promotion_order), 0::bigint) + 1
      INTO v_promotion_order
      FROM public.memory_promotion_history;
    INSERT INTO public.memory_promotion_history(
        promotion_id, promotion_order, artifact_id, previous_artifact_id,
        requested_as_of, effective_as_of, promoted_at
    ) VALUES (
        v_promotion_id, v_promotion_order, p_artifact_id, v_current,
        p_requested_as_of, statement_timestamp(), statement_timestamp()
    );
    INSERT INTO public.memory_artifact_state_events(
        state_event_id, artifact_id, state, reason_code
    ) VALUES (
        public.p3d_derive_run_id('seven-lens.p3f.memory-state.v1',
            p_artifact_id::text, 'CURRENT'),
        p_artifact_id, 'CURRENT', 'PROMOTED'
    );
    UPDATE public.memory_current_pointer AS p
    SET artifact_id = p_artifact_id,
        promotion_id = v_promotion_id,
        promoted_at = statement_timestamp()
    WHERE p.singleton;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.current_memory_artifact(p_as_of TIMESTAMPTZ)
RETURNS TABLE (
    artifact_id TEXT, schema_version TEXT, created_at TIMESTAMPTZ,
    cutoff_at TIMESTAMPTZ, previous_artifact_id TEXT, content_hash TEXT,
    cas_hash TEXT, byte_count INTEGER, line_count INTEGER, entry_count INTEGER,
    prompt_version TEXT, model_version TEXT, provider_version TEXT,
    content_bytes BYTEA, promoted_at TIMESTAMPTZ
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT a.artifact_id, a.schema_version, a.created_at, a.cutoff_at,
           a.previous_artifact_id, a.content_hash, a.cas_hash, a.byte_count,
           a.line_count, a.entry_count, a.prompt_version, a.model_version,
           a.provider_version, a.content_bytes, h.promoted_at
    FROM public.memory_promotion_history AS h
    JOIN public.memory_artifacts AS a ON a.artifact_id = h.artifact_id
    WHERE h.promoted_at <= p_as_of
      AND h.requested_as_of <= p_as_of
      AND h.effective_as_of <= p_as_of
      AND a.cutoff_at <= p_as_of
      AND a.created_at <= p_as_of
      AND a.registered_at <= p_as_of
      AND a.content_hash = a.cas_hash
      AND pg_catalog.octet_length(a.content_bytes) = a.byte_count
      AND pg_catalog.encode(public.digest(a.content_bytes, 'sha256'), 'hex') = a.content_hash
      AND EXISTS (
          SELECT 1 FROM public.memory_artifact_state_events AS state
          WHERE state.artifact_id = a.artifact_id AND state.state = 'VALIDATED'
      )
      AND NOT EXISTS (
          SELECT 1 FROM public.memory_artifact_state_events AS state
          WHERE state.artifact_id = a.artifact_id AND state.state = 'INVALID'
      )
      AND (
          SELECT pg_catalog.count(*) FROM public.memory_artifact_sources AS source
          WHERE source.artifact_id = a.artifact_id
      ) = a.source_record_count
      AND NOT EXISTS (
          SELECT 1
          FROM public.memory_artifact_sources AS source
          JOIN public.reflection_records AS reflection USING (reflection_id)
          WHERE source.artifact_id = a.artifact_id
            AND (reflection.available_at > a.cutoff_at OR reflection.cutoff_at > a.cutoff_at)
      )
    ORDER BY h.promotion_order DESC
    LIMIT 1
$$;

CREATE OR REPLACE FUNCTION public.current_memory_pointer_artifact()
RETURNS TABLE (
    artifact_id TEXT, schema_version TEXT, created_at TIMESTAMPTZ,
    cutoff_at TIMESTAMPTZ, previous_artifact_id TEXT, content_hash TEXT,
    cas_hash TEXT, byte_count INTEGER, line_count INTEGER, entry_count INTEGER,
    prompt_version TEXT, model_version TEXT, provider_version TEXT,
    content_bytes BYTEA, promoted_at TIMESTAMPTZ
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT a.artifact_id, a.schema_version, a.created_at, a.cutoff_at,
           a.previous_artifact_id, a.content_hash, a.cas_hash, a.byte_count,
           a.line_count, a.entry_count, a.prompt_version, a.model_version,
           a.provider_version, a.content_bytes, h.promoted_at
    FROM public.memory_current_pointer AS pointer
    JOIN public.memory_promotion_history AS h
      ON h.promotion_id = pointer.promotion_id AND h.artifact_id = pointer.artifact_id
    JOIN public.memory_artifacts AS a ON a.artifact_id = pointer.artifact_id
    WHERE pointer.singleton
      AND pointer.promoted_at = h.promoted_at
      AND a.content_hash = a.cas_hash
      AND pg_catalog.octet_length(a.content_bytes) = a.byte_count
      AND pg_catalog.encode(public.digest(a.content_bytes, 'sha256'), 'hex') = a.content_hash
      AND EXISTS (
          SELECT 1 FROM public.memory_artifact_state_events AS state
          WHERE state.artifact_id = a.artifact_id AND state.state = 'VALIDATED'
      )
      AND NOT EXISTS (
          SELECT 1 FROM public.memory_artifact_state_events AS state
          WHERE state.artifact_id = a.artifact_id AND state.state = 'INVALID'
      )
      AND (
          SELECT pg_catalog.count(*) FROM public.memory_artifact_sources AS source
          WHERE source.artifact_id = a.artifact_id
      ) = a.source_record_count
      AND NOT EXISTS (
          SELECT 1
          FROM public.memory_artifact_sources AS source
          JOIN public.reflection_records AS reflection USING (reflection_id)
          WHERE source.artifact_id = a.artifact_id
            AND (reflection.available_at > a.cutoff_at OR reflection.cutoff_at > a.cutoff_at)
      )
$$;

REVOKE ALL ON TABLE public.reflection_records, public.reflection_sources,
    public.reflection_corrections, public.memory_artifacts,
    public.memory_artifact_sources, public.memory_artifact_state_events,
    public.memory_promotion_history, public.memory_current_pointer,
    public.memory_curation_audits FROM PUBLIC;
REVOKE ALL ON TABLE public.approved_reflection_records,
    public.approved_reflection_sources FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_reflection_record(
    TEXT, TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TIMESTAMPTZ, TIMESTAMPTZ,
    TEXT, TEXT, TEXT, TEXT, TEXT, BYTEA, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT[], TEXT[], TEXT[], TIMESTAMPTZ[], TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_memory_candidate(
    TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT, TEXT, BYTEA,
    INTEGER, INTEGER, INTEGER, TEXT, TEXT, TEXT, TEXT[]
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_memory_curation_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    INTEGER, INTEGER, TEXT, TEXT, TEXT, INTEGER, INTEGER, INTEGER, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.validate_memory_artifact(
    TEXT, TEXT, TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.promote_memory_artifact(
    TEXT, TEXT, TIMESTAMPTZ
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.current_memory_artifact(TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.current_memory_pointer_artifact() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p3f_text_is_safe(TEXT, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p3f_instruction_text_is_safe(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p3f_fact_text_is_closed(TEXT, TEXT[], JSON, TEXT[]) FROM PUBLIC;
