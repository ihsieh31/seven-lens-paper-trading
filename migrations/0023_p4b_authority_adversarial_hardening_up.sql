-- 0023: close the P4-B authority gaps found by runtime-role adversarial tests.
--
-- 0021 remains immutable.  This migration tightens its existing
-- record_quarantine_decision seam and adds semantic scalar checks to the
-- existing source-payload contract.  No new public function or write path is
-- introduced, so the runtime privilege inventory remains unchanged.

-- Patch the 0021 function in place rather than copying a second implementation
-- of the authority seam.  Each replacement is anchored to the exact 0021 body
-- and fails closed if that body is not the function being upgraded.
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

    -- Python QuarantineReason order is enum order, not lexical order.
    v_marker := $p4b_marker$
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'event_ids') AS item(value)
$p4b_marker$;
    v_insert := $p4b_insert$
    IF COALESCE(
        (
            SELECT pg_catalog.jsonb_agg(
                       item.value
                       ORDER BY pg_catalog.array_position(
                           ARRAY[
                               'UNKNOWN_SECURITY', 'AMBIGUOUS_IDENTITY',
                               'SYMBOL_AS_OF_MISMATCH', 'IDENTITY_INTERVAL_CONFLICT',
                               'SOURCE_NOT_YET_AVAILABLE', 'STALE_SECURITY_MASTER',
                               'SPLIT_DETECTED', 'FORMAL_CONFIRMATION_MISSING',
                               'SPLIT_RATIO_CONFLICT', 'SPLIT_DATE_CONFLICT',
                               'SPLIT_IDENTITY_CONFLICT', 'SOURCE_WITHDRAWN_OR_CORRECTED',
                               'UNSUPPORTED_CORPORATE_ACTION',
                               'EFFECTIVE_OR_LATE_EVENT_REVIEW', 'SPLIT_TYPE_CONFLICT'
                           ]::TEXT[],
                           item.value #>> '{}'
                       )
                   )
            FROM pg_catalog.jsonb_array_elements(p_wire->'reasons') AS item(value)
        ),
        '[]'::JSONB
    ) IS DISTINCT FROM p_wire->'reasons'
    THEN
        RAISE EXCEPTION 'quarantine reasons must use canonical enum order'
            USING ERRCODE = '23514';
    END IF;

$p4b_insert$;
    IF pg_catalog.strpos(v_definition, v_marker) = 0 THEN
        RAISE EXCEPTION '0021 reason-order patch anchor is missing'
            USING ERRCODE = '23514';
    END IF;
    v_definition := pg_catalog.replace(v_definition, v_marker, v_insert || v_marker);

    -- jsonb_agg preserves duplicate values, so 0021's sortedness comparison
    -- alone did not reject duplicate event IDs.
    v_marker := $p4b_marker$
    SELECT pg_catalog.jsonb_agg(item.value ORDER BY item.value #>> '{}')
$p4b_marker$;
    v_insert := $p4b_insert$
    IF (
        SELECT count(*)
        FROM pg_catalog.jsonb_array_elements(p_wire->'event_ids') AS item(value)
    ) <> (
        SELECT count(DISTINCT item.value #>> '{}')
        FROM pg_catalog.jsonb_array_elements(p_wire->'event_ids') AS item(value)
    )
    THEN
        RAISE EXCEPTION 'quarantine event ids must be unique'
            USING ERRCODE = '23514';
    END IF;

$p4b_insert$;
    IF pg_catalog.strpos(v_definition, v_marker) = 0 THEN
        RAISE EXCEPTION '0021 event-id patch anchor is missing'
            USING ERRCODE = '23514';
    END IF;
    v_definition := pg_catalog.replace(v_definition, v_marker, v_insert || v_marker);

    -- ELIGIBLE requires the exact identity source set, not merely one matching
    -- identity source.  Count equality rejects extra refs; the anti-join
    -- rejects a missing identity source even when the array remains sorted.
    v_marker := $p4b_marker$
              AND EXISTS (
                  SELECT 1
                  FROM public.security_identity_sources AS identity_source
                  JOIN pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
                    ON ref.value->>'record_id' = identity_source.record_id
                   AND ref.value->>'record_hash' = identity_source.record_hash
                   AND ref.value->>'family' = identity_source.family
                  WHERE identity_source.identity_hash = identity_row.identity_hash
              )
$p4b_marker$;
    v_insert := $p4b_insert$
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
$p4b_insert$;
    IF pg_catalog.strpos(v_definition, v_marker) = 0 THEN
        RAISE EXCEPTION '0021 source-closure patch anchor is missing'
            USING ERRCODE = '23514';
    END IF;
    v_definition := pg_catalog.replace(v_definition, v_marker, v_insert);

    IF v_definition = v_original THEN
        RAISE EXCEPTION '0023 made no authority-function changes'
            USING ERRCODE = '23514';
    END IF;
    EXECUTE v_definition;
END;
$p4b_patch$;

-- The Python adapters produce typed JSON scalars.  0021 checked only the
-- branch key sets; these checks keep a recomputed wire hash from turning a
-- malformed scalar into an accepted source record.  Optional provider fields
-- that the Python adapters intentionally preserve without typing remain
-- unconstrained here.
ALTER TABLE public.p4_source_records
    ADD CONSTRAINT p4_source_records_payload_scalar_contract CHECK (
        (
            jsonb_typeof(wire->'payload') = 'object'
            AND (
                (
                    family = 'ALPACA_ASSETS'
                    AND jsonb_typeof(wire->'payload'->'id') = 'string'
                    AND jsonb_typeof(wire->'payload'->'symbol') = 'string'
                    AND jsonb_typeof(wire->'payload'->'exchange') = 'string'
                    AND jsonb_typeof(wire->'payload'->'asset_class') = 'string'
                    AND jsonb_typeof(wire->'payload'->'status') = 'string'
                    AND jsonb_typeof(wire->'payload'->'tradable') = 'boolean'
                )
                OR (
                    family = 'ALPACA_HISTORICAL_BARS'
                    AND jsonb_typeof(wire->'payload'->'symbol') = 'string'
                    AND jsonb_typeof(wire->'payload'->'feed') = 'string'
                    AND wire->'payload'->>'feed' = 'sip'
                    AND jsonb_typeof(wire->'payload'->'timeframe') = 'string'
                    AND wire->'payload'->>'timeframe' = '1Day'
                    AND jsonb_typeof(wire->'payload'->'bars') = 'array'
                    AND jsonb_typeof(wire->'payload'->'next_page_token') IN ('string', 'null')
                )
                OR (
                    family = 'ALPACA_IEX_QUOTES'
                    AND jsonb_typeof(wire->'payload'->'symbol') = 'string'
                    AND jsonb_typeof(wire->'payload'->'bid_price') IN ('string', 'null')
                    AND jsonb_typeof(wire->'payload'->'ask_price') IN ('string', 'null')
                    AND jsonb_typeof(wire->'payload'->'timestamp') = 'string'
                    AND jsonb_typeof(wire->'payload'->'feed') = 'string'
                    AND (
                        NOT (wire->'payload' ? 'bid_size')
                        OR (
                            jsonb_typeof(wire->'payload'->'bid_size') = 'number'
                            AND wire->'payload'->>'bid_size' ~ '^[0-9]+$'
                        )
                    )
                    AND (
                        NOT (wire->'payload' ? 'ask_size')
                        OR (
                            jsonb_typeof(wire->'payload'->'ask_size') = 'number'
                            AND wire->'payload'->>'ask_size' ~ '^[0-9]+$'
                        )
                    )
                )
                OR (
                    family = 'ALPACA_CORPORATE_ACTIONS'
                    AND jsonb_typeof(wire->'payload'->'type') = 'string'
                    AND jsonb_typeof(wire->'payload'->'supported') = 'boolean'
                    AND jsonb_typeof(wire->'payload'->'complete') = 'boolean'
                    AND jsonb_typeof(wire->'payload'->'detection_only') = 'boolean'
                )
                OR (
                    family = 'SEC_EDGAR'
                    AND (
                        (
                            wire->>'endpoint_id' = 'submissions'
                            AND jsonb_typeof(wire->'payload'->'cik_padded') = 'string'
                            AND (
                                (
                                    wire->'payload' ? 'sic'
                                    AND jsonb_typeof(wire->'payload'->'sic') = 'string'
                                )
                                OR (
                                    wire->'payload' ? 'accession_number'
                                    AND jsonb_typeof(wire->'payload'->'accession_number') = 'string'
                                    AND jsonb_typeof(wire->'payload'->'form') = 'string'
                                    AND jsonb_typeof(wire->'payload'->'primary_document') = 'string'
                                    AND jsonb_typeof(wire->'payload'->'filing_date') = 'string'
                                )
                            )
                        )
                        OR (
                            wire->>'endpoint_id' = 'companyfacts'
                            AND jsonb_typeof(wire->'payload'->'cik_padded') = 'string'
                            AND jsonb_typeof(wire->'payload'->'taxonomy') = 'string'
                            AND jsonb_typeof(wire->'payload'->'concept') = 'string'
                            AND jsonb_typeof(wire->'payload'->'unit') = 'string'
                            AND jsonb_typeof(wire->'payload'->'value') = 'string'
                            AND jsonb_typeof(wire->'payload'->'start') IN ('string', 'null')
                            AND jsonb_typeof(wire->'payload'->'end') = 'string'
                            AND jsonb_typeof(wire->'payload'->'fiscal_year') = 'number'
                            AND wire->'payload'->>'fiscal_year' ~ '^[0-9]{4}$'
                            AND jsonb_typeof(wire->'payload'->'fiscal_period') = 'string'
                            AND jsonb_typeof(wire->'payload'->'form') = 'string'
                            AND jsonb_typeof(wire->'payload'->'accession') = 'string'
                            AND jsonb_typeof(wire->'payload'->'filed') = 'string'
                            AND jsonb_typeof(wire->'payload'->'frame') IN ('string', 'null')
                            AND jsonb_typeof(wire->'payload'->'consolidation_scope') = 'string'
                            AND wire->'payload'->>'consolidation_scope'
                                = 'entire_filing_entity'
                            AND (
                                NOT (wire->'payload' ? 'sign_convention')
                                OR jsonb_typeof(wire->'payload'->'sign_convention') = 'string'
                            )
                        )
                    )
                )
                OR (
                    family = 'ISSUER_IR'
                    AND jsonb_typeof(wire->'payload'->'issuer_id') = 'string'
                    AND jsonb_typeof(wire->'payload'->'title') = 'string'
                    AND jsonb_typeof(wire->'payload'->'url') = 'string'
                )
                OR (
                    family = 'EXCHANGE_OFFICIAL'
                    AND jsonb_typeof(wire->'payload'->'exchange') = 'string'
                    AND jsonb_typeof(wire->'payload'->'title') = 'string'
                    AND jsonb_typeof(wire->'payload'->'url') = 'string'
                    AND (
                        NOT (wire->'payload' ? 'symbol')
                        OR (
                            jsonb_typeof(wire->'payload'->'symbol') = 'string'
                            AND jsonb_typeof(wire->'payload'->'instrument_kind') = 'string'
                            AND jsonb_typeof(wire->'payload'->'halted') = 'boolean'
                            AND jsonb_typeof(wire->'payload'->'observed_at') = 'string'
                        )
                    )
                )
                OR (
                    family = 'FRED_ALFRED'
                    AND jsonb_typeof(wire->'payload'->'series_id') = 'string'
                    AND jsonb_typeof(wire->'payload'->'date') = 'string'
                    AND jsonb_typeof(wire->'payload'->'value') = 'string'
                    AND jsonb_typeof(wire->'payload'->'realtime_start') = 'string'
                    AND jsonb_typeof(wire->'payload'->'realtime_end') = 'string'
                )
                OR (
                    family = 'TREASURY'
                    AND jsonb_typeof(wire->'payload'->'dataset') = 'string'
                    AND jsonb_typeof(wire->'payload'->'record_date') = 'string'
                )
                OR (
                    family = 'BLS'
                    AND jsonb_typeof(wire->'payload'->'series_id') = 'string'
                    AND jsonb_typeof(wire->'payload'->'year') = 'string'
                    AND jsonb_typeof(wire->'payload'->'period') = 'string'
                    AND jsonb_typeof(wire->'payload'->'value') = 'string'
                )
                OR (
                    family = 'BEA'
                    AND jsonb_typeof(wire->'payload'->'dataset') = 'string'
                    AND jsonb_typeof(wire->'payload'->'table_name') = 'string'
                    AND jsonb_typeof(wire->'payload'->'year') = 'string'
                    AND jsonb_typeof(wire->'payload'->'period') = 'string'
                    AND jsonb_typeof(wire->'payload'->'value') = 'string'
                    AND jsonb_typeof(wire->'payload'->'series_code') IN ('string', 'null')
                )
                OR (
                    family = 'EIA'
                    AND jsonb_typeof(wire->'payload'->'route') = 'string'
                    AND jsonb_typeof(wire->'payload'->'period') = 'string'
                    AND jsonb_typeof(wire->'payload'->'value') = 'string'
                )
                OR (
                    family = 'TAVILY'
                    AND jsonb_typeof(wire->'payload'->'query') = 'string'
                    AND jsonb_typeof(wire->'payload'->'title') = 'string'
                    AND jsonb_typeof(wire->'payload'->'url') = 'string'
                    AND jsonb_typeof(wire->'payload'->'snippet') = 'string'
                    AND jsonb_typeof(wire->'payload'->'score') = 'number'
                )
                OR (
                    family = 'GDELT'
                    AND jsonb_typeof(wire->'payload'->'query') = 'string'
                    AND jsonb_typeof(wire->'payload'->'url') = 'string'
                    AND jsonb_typeof(wire->'payload'->'title') = 'string'
                    AND jsonb_typeof(wire->'payload'->'domain') = 'string'
                    AND jsonb_typeof(wire->'payload'->'seendate') = 'string'
                )
                OR (
                    family = 'YFINANCE'
                    AND jsonb_typeof(wire->'payload'->'symbol') = 'string'
                    AND jsonb_typeof(wire->'payload'->'regular_market_price') = 'number'
                    AND jsonb_typeof(wire->'payload'->'regular_market_time') = 'number'
                    AND wire->'payload'->>'regular_market_time' ~ '^[0-9]+$'
                    AND jsonb_typeof(wire->'payload'->'exchange_name') IN ('string', 'null')
                    AND jsonb_typeof(wire->'payload'->'currency') IN ('string', 'null')
                    AND jsonb_typeof(wire->'payload'->'supplement_only') = 'boolean'
                )
            )
        ) IS TRUE
    );
