-- 0023 down: remove the scalar contract and restore the exact 0021 function.
-- The function patch is reversed through the same anchored text operation so
-- a missing or drifted 0021 body fails closed instead of weakening authority.

ALTER TABLE public.p4_source_records
    DROP CONSTRAINT IF EXISTS p4_source_records_payload_scalar_contract;

DO $p4b_patch$
DECLARE
    v_definition TEXT;
    v_original TEXT;
    v_marker TEXT;
    v_insert TEXT;
BEGIN
    SELECT pg_catalog.pg_get_functiondef(
               'public.record_quarantine_decision(text,jsonb)'::pg_catalog.regprocedure
           )
      INTO v_definition;
    IF v_definition IS NULL THEN
        RAISE EXCEPTION 'record_quarantine_decision function is missing'
            USING ERRCODE = '23514';
    END IF;
    v_original := v_definition;

    -- Remove the canonical-reason-order block added by 0023.
    v_original := v_definition;
    v_definition := pg_catalog.regexp_replace(
        v_definition,
        $p4b_regex$(?s)\n    IF COALESCE\(.*?\n    END IF;\n\n$p4b_regex$,
        '',
        1,
        1
    );
    IF v_definition = v_original THEN
        RAISE EXCEPTION '0023 reason-order rollback anchor is missing'
            USING ERRCODE = '23514';
    END IF;
    -- Remove the duplicate-event check added by 0023.
    v_original := v_definition;
    v_definition := pg_catalog.regexp_replace(
        v_definition,
        $p4b_regex$(?s)\n    IF \(\n        SELECT count\(\*\)\n        FROM pg_catalog\.jsonb_array_elements\(p_wire->'event_ids'\) AS item\(value\).*?RAISE EXCEPTION 'quarantine event ids must be unique'.*?\n    END IF;\n\n$p4b_regex$,
        '',
        1,
        1
    );
    IF v_definition = v_original THEN
        RAISE EXCEPTION '0023 event-id rollback anchor is missing'
            USING ERRCODE = '23514';
    END IF;

    -- Restore the one-source existence predicate from 0021.
    v_marker := $p4b_marker$
              AND (
                  (
                      SELECT count(*)
                      FROM public.security_identity_sources AS identity_source
                      WHERE identity_source.identity_hash = identity_row.identity_hash
                  ) = pg_catalog.jsonb_array_length(p_wire->'source_refs')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.security_identity_sources AS identity_source
                      WHERE identity_source.identity_hash = identity_row.identity_hash
                        AND NOT EXISTS (
                            SELECT 1
                            FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
                            WHERE ref.value->>'record_id' = identity_source.record_id
                              AND ref.value->>'record_hash' = identity_source.record_hash
                              AND ref.value->>'family' = identity_source.family
                        )
                  )
              )
$p4b_marker$;
    v_insert := $p4b_insert$
              AND EXISTS (
                  SELECT 1
                  FROM public.security_identity_sources AS identity_source
                  JOIN pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
                    ON ref.value->>'record_id' = identity_source.record_id
                   AND ref.value->>'record_hash' = identity_source.record_hash
                   AND ref.value->>'family' = identity_source.family
                  WHERE identity_source.identity_hash = identity_row.identity_hash
              )
$p4b_insert$;
    IF pg_catalog.strpos(v_definition, v_marker) = 0 THEN
        RAISE EXCEPTION '0023 source-closure rollback anchor is missing'
            USING ERRCODE = '23514';
    END IF;
    v_definition := pg_catalog.replace(v_definition, v_marker, v_insert);

    IF v_definition = v_original THEN
        RAISE EXCEPTION '0023 rollback made no authority-function changes'
            USING ERRCODE = '23514';
    END IF;
    EXECUTE v_definition;
END;
$p4b_patch$;

DELETE FROM public.schema_migrations WHERE version = 23;
