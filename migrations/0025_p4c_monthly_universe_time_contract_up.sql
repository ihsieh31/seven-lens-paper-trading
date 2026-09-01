-- 0025 up: repair the monthly universe time contract.
--
-- Universe as_of is the first open NYSE session (REGULAR or HALF_DAY) of its
-- calendar month. Daily screening may reuse it later in that same month only
-- after it is known. This migration also closes exact focus-prefix authority.

-- The calendar proof lives at the append authority boundary because a CHECK
-- cannot inspect the immutable MarketSession wire referenced by the snapshot.
ALTER TABLE public.universe_snapshots
    DROP CONSTRAINT universe_snapshots_as_of_check;
ALTER TABLE public.universe_snapshots
    ADD CONSTRAINT universe_snapshots_as_of_open_session_check
    CHECK (as_of IS NOT NULL);

-- Replace the exact-date universe gate in the feature-vector authority with
-- the (month, availability) gate.
CREATE OR REPLACE FUNCTION public.append_feature_vector(
    p_feature_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing_wire JSONB;
BEGIN
    IF p_feature_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'feature hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire IS NULL
        OR COALESCE(
            pg_catalog.octet_length(
                pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8')
            ),
            0
        ) > 1048576
    THEN
        RAISE EXCEPTION 'feature vector canonical wire exceeds the 1048576-byte limit'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 17
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS k(key_name)
            WHERE k.key_name <> ALL (ARRAY[
                'security_id', 'symbol', 'universe_hash', 'manifest_hash', 'as_of', 'known_at',
                'status', 'raw', 'trend', 'quality', 'value', 'low_risk',
                'composite', 'missing_reason', 'schema_version', 'producer_version',
                'price_session_dates'
            ]::text[])
        )
        OR jsonb_typeof(p_wire->'security_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'symbol') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'universe_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'manifest_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'as_of') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'known_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'status') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'raw') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'price_session_dates') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'trend') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'quality') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'value') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'low_risk') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'composite') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'missing_reason') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'schema_version') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'producer_version') IS DISTINCT FROM 'string'
        OR p_wire->>'security_id' !~ '^[0-9a-f][0-9a-f-]{7,63}$'
        OR p_wire->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
        OR p_wire->>'universe_hash' !~ '^[0-9a-f]{64}$'
        OR p_wire->>'manifest_hash' <> 'a95be51a7468c73a8a6bfdda05fb4fd9076703afdae9f3a9bff7b2d4a8f6fcc7'
        OR p_wire->>'status' NOT IN ('COMPLETE', 'FACTOR_INPUT_MISSING', 'FACTOR_MANIFEST_NOT_APPROVED', 'SECTOR_TAXONOMY_NOT_AUTHORIZED')
        OR p_wire->>'schema_version' <> '1.0.0'
        OR p_wire->>'producer_version' <> 'p4c.screening.v1'
        OR p_wire->>'as_of' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'known_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR jsonb_array_length(p_wire->'raw') <> 9
    THEN
        RAISE EXCEPTION 'feature vector wire shape is outside the P4-C contract'
            USING ERRCODE = '23514';
    END IF;
    IF (
        SELECT pg_catalog.array_agg(item.value->>'name' ORDER BY item.ordinality)
        FROM pg_catalog.jsonb_array_elements(p_wire->'raw') WITH ORDINALITY AS item(value, ordinality)
    ) IS DISTINCT FROM ARRAY[
        'trend_126_21', 'trend_252_21', 'roa', 'cfo_to_assets',
        'accrual_quality', 'earnings_yield', 'fcf_yield', 'vol63',
        'max_drawdown_252'
    ]::text[]
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'raw') AS item(value)
            WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'object'
                OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(item.value)) <> 6
                OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.jsonb_object_keys(item.value) AS k(key_name)
                    WHERE k.key_name <> ALL (ARRAY[
                        'name', 'value', 'formula_version', 'source_refs', 'security_id',
                        'missing_reason'
                    ]::text[])
                )
                OR jsonb_typeof(item.value->'name') IS DISTINCT FROM 'string'
                OR jsonb_typeof(item.value->'value') NOT IN ('null', 'string')
                OR jsonb_typeof(item.value->'formula_version') IS DISTINCT FROM 'string'
                OR jsonb_typeof(item.value->'source_refs') IS DISTINCT FROM 'array'
                OR jsonb_typeof(item.value->'security_id') IS DISTINCT FROM 'string'
                OR jsonb_typeof(item.value->'missing_reason') NOT IN ('null', 'string')
                OR item.value->>'formula_version' <> 'p4-factor-v1.0'
                OR jsonb_array_length(item.value->'source_refs') = 0
                OR jsonb_array_length(item.value->'source_refs') > 64
                OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.jsonb_array_elements(item.value->'source_refs') AS ref(value)
                    WHERE jsonb_typeof(ref.value) IS DISTINCT FROM 'object'
                        OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(ref.value)) <> 3
                        OR EXISTS (
                            SELECT 1
                            FROM pg_catalog.jsonb_object_keys(ref.value) AS k(key_name)
                            WHERE k.key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
                        )
                        OR jsonb_typeof(ref.value->'record_id') IS DISTINCT FROM 'string'
                        OR jsonb_typeof(ref.value->'family') IS DISTINCT FROM 'string'
                        OR jsonb_typeof(ref.value->'record_hash') IS DISTINCT FROM 'string'
                        OR ref.value->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
                        OR ref.value->>'family' NOT IN (
                            'ALPACA_HISTORICAL_BARS', 'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR'
                        )
                        OR ref.value->>'record_hash' !~ '^[0-9a-f]{64}$'
                )
                OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.jsonb_array_elements(item.value->'source_refs') AS ref(value)
                    GROUP BY ref.value->>'record_id'
                    HAVING count(*) > 1
                )
                OR (
                    SELECT pg_catalog.array_agg(ref.value->>'record_id' ORDER BY ref.ordinality)
                    FROM pg_catalog.jsonb_array_elements(item.value->'source_refs')
                        WITH ORDINALITY AS ref(value, ordinality)
                ) IS DISTINCT FROM (
                    SELECT pg_catalog.array_agg(ref.value->>'record_id' ORDER BY ref.value->>'family', ref.value->>'record_id', ref.value->>'record_hash')
                    FROM pg_catalog.jsonb_array_elements(item.value->'source_refs') AS ref(value)
                )
                OR item.value->>'value' IS NOT NULL
                    AND item.value->>'value' !~ '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$'
                OR item.value->>'security_id' IS DISTINCT FROM p_wire->>'security_id'
                OR item.value->>'missing_reason' IS NOT NULL
                    AND item.value->>'missing_reason' = ''
                OR (item.value->>'value' IS NULL AND item.value->>'missing_reason' IS NULL)
                OR (item.value->>'value' IS NOT NULL AND item.value->>'missing_reason' IS NOT NULL)
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'raw') AS item(value)
            GROUP BY item.value->>'name'
            HAVING count(*) > 1
        )
    THEN
        RAISE EXCEPTION 'feature raw subfactors are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'price_session_dates') AS item(value)
        WHERE item.value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'price_session_dates') AS item(value)
            WHERE (item.value)::date IS NULL
        )
        OR (
            SELECT pg_catalog.array_agg(item.value ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'price_session_dates')
                WITH ORDINALITY AS item(value, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.value ORDER BY item.value)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'price_session_dates') AS item(value)
        )
    THEN
        RAISE EXCEPTION 'feature price session dates are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF (
        p_wire->>'status' = 'COMPLETE'
        AND jsonb_array_length(p_wire->'price_session_dates') <> 252
    )
        OR (
            p_wire->>'status' <> 'COMPLETE'
            AND jsonb_array_length(p_wire->'price_session_dates') <> 0
        )
    THEN
        RAISE EXCEPTION 'feature price session coverage does not match its status'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'status' = 'COMPLETE'
        AND (
            SELECT COALESCE(
                pg_catalog.array_agg(expected.trading_date ORDER BY expected.trading_date),
                ARRAY[]::text[]
            )
            FROM (
                SELECT session.value->>'trading_date' AS trading_date
                FROM public.universe_snapshot_entries AS universe_entry
                JOIN public.universe_snapshots AS universe
                  ON universe.universe_hash = universe_entry.universe_hash
                JOIN LATERAL (
                    SELECT candidate_market.*
                    FROM public.market_snapshots AS candidate_market
                    WHERE candidate_market.security_id = p_wire->>'security_id'
                      AND candidate_market.symbol = p_wire->>'symbol'
                      AND candidate_market.as_of::date = (p_wire->>'as_of')::timestamptz::date
                      AND candidate_market.known_at <= (p_wire->>'known_at')::timestamptz
                    ORDER BY candidate_market.known_at DESC, candidate_market.snapshot_hash DESC
                    LIMIT 1
                ) AS market ON TRUE
                CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(market.wire->'sessions')
                    AS session(value)
                WHERE universe_entry.universe_hash = p_wire->>'universe_hash'
                  AND universe_entry.security_id = p_wire->>'security_id'
                  AND universe_entry.symbol = p_wire->>'symbol'
                  AND universe_entry.eligible
                  AND universe.known_at <= (p_wire->>'known_at')::timestamptz
                  AND session.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
                  AND (session.value->>'trading_date')::date
                        < (p_wire->>'as_of')::timestamptz::date
                ORDER BY (session.value->>'trading_date')::date DESC
                LIMIT 252
            ) AS expected
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.value ORDER BY item.value)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'price_session_dates') AS item(value)
        )
    THEN
        RAISE EXCEPTION 'COMPLETE feature vector lacks the latest market-session coverage'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'status' = 'COMPLETE'
        AND EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'price_session_dates') AS expected_date(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(p_wire->'raw') AS raw_item(value)
                CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(raw_item.value->'source_refs')
                    AS ref(value)
                JOIN public.p4_source_records AS source_record
                  ON source_record.record_id = ref.value->>'record_id'
                 AND source_record.record_hash = ref.value->>'record_hash'
                 AND source_record.family = ref.value->>'family'
                CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(
                    source_record.wire->'payload'->'bars'
                ) AS bar(value)
                WHERE ref.value->>'family' = 'ALPACA_HISTORICAL_BARS'
                  AND source_record.wire->'payload'->>'symbol' = p_wire->>'symbol'
                  AND COALESCE(
                        (source_record.wire->>'available_at')::timestamptz,
                        source_record.retrieved_at
                      ) <= (p_wire->>'known_at')::timestamptz
                  AND CASE
                        WHEN bar.value->>'t'
                            ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?Z$'
                        THEN (bar.value->>'t')::timestamptz::date
                        WHEN bar.value->>'t' ~ '^[0-9]{8}T[0-9]{6}Z$'
                        THEN to_timestamp(bar.value->>'t', 'YYYYMMDD"T"HH24MISS"Z"')::date
                        WHEN bar.value->>'t' ~ '^[0-9]{14}$'
                        THEN to_timestamp(bar.value->>'t', 'YYYYMMDDHH24MISS')::date
                        ELSE NULL::date
                      END = expected_date.value::date
            )
        )
    THEN
        RAISE EXCEPTION 'COMPLETE feature vector price dates are not covered by visible historical bars'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'raw') AS item(value)
        CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(item.value->'source_refs') AS ref(value)
        WHERE item.value->>'security_id' IS DISTINCT FROM p_wire->>'security_id'
            OR NOT EXISTS (
            SELECT 1
            FROM public.p4_source_records AS source_record
            WHERE source_record.record_id = ref.value->>'record_id'
              AND source_record.record_hash = ref.value->>'record_hash'
              AND source_record.family = ref.value->>'family'
              AND COALESCE(
                    (source_record.wire->>'available_at')::timestamptz,
                    source_record.retrieved_at
                  ) <= (p_wire->>'known_at')::timestamptz
              AND (
                    (
                        ref.value->>'family' = 'ALPACA_HISTORICAL_BARS'
                        AND source_record.wire->'payload'->>'symbol' = p_wire->>'symbol'
                    )
                    OR (
                        ref.value->>'family' = 'ALPACA_CORPORATE_ACTIONS'
                        AND (
                            source_record.wire->'payload'->>'symbol' = p_wire->>'symbol'
                            OR (
                                source_record.wire->'payload'->>'symbol' IS NULL
                                AND source_record.wire->'payload'->>'cusip' = (
                                    SELECT identity_record.wire->>'cusip'
                                    FROM public.universe_snapshot_entries AS universe_entry
                                    JOIN public.security_identities AS identity_record
                                      ON identity_record.identity_hash = universe_entry.identity_hash
                                    JOIN public.universe_snapshots AS universe
                                      ON universe.universe_hash = universe_entry.universe_hash
                                    WHERE universe_entry.universe_hash = p_wire->>'universe_hash'
                                      AND universe_entry.security_id = p_wire->>'security_id'
                                      AND universe_entry.symbol = p_wire->>'symbol'
                                      AND universe_entry.eligible
                                      AND universe_entry.quarantine_decision_hash IS NOT NULL
                                      AND universe.known_at <= (p_wire->>'known_at')::timestamptz
                                      AND identity_record.available_at <= (p_wire->>'known_at')::timestamptz
                                      AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
                                      AND (
                                            identity_record.valid_to IS NULL
                                            OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
                                      )
                                    ORDER BY identity_record.available_at DESC, identity_record.identity_hash DESC
                                    LIMIT 1
                                )
                            )
                        )
                    )
                    OR (
                        ref.value->>'family' = 'SEC_EDGAR'
                        AND source_record.wire->'payload'->>'cik_padded' = (
                            SELECT identity_record.wire->>'cik'
                            FROM public.universe_snapshot_entries AS universe_entry
                            JOIN public.security_identities AS identity_record
                              ON identity_record.identity_hash = universe_entry.identity_hash
                            JOIN public.universe_snapshots AS universe
                              ON universe.universe_hash = universe_entry.universe_hash
                            WHERE universe_entry.universe_hash = p_wire->>'universe_hash'
                              AND universe_entry.security_id = p_wire->>'security_id'
                              AND universe_entry.symbol = p_wire->>'symbol'
                              AND universe_entry.eligible
                              AND universe_entry.quarantine_decision_hash IS NOT NULL
                              AND universe.known_at <= (p_wire->>'known_at')::timestamptz
                              AND identity_record.available_at <= (p_wire->>'known_at')::timestamptz
                              AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
                              AND (
                                    identity_record.valid_to IS NULL
                                    OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
                              )
                            ORDER BY identity_record.available_at DESC, identity_record.identity_hash DESC
                            LIMIT 1
                        )
                    )
              )
        )
    )
    THEN
        RAISE EXCEPTION 'feature vector source lineage is not present in the P4-A record log'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(
            jsonb_build_array(p_wire->'trend', p_wire->'quality', p_wire->'value', p_wire->'low_risk', p_wire->'composite')
        ) AS item(value)
        WHERE item.value <> 'null'
            AND item.value !~ '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$'
    )
    THEN
        RAISE EXCEPTION 'feature category scores are not finite decimal text'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'status' = 'COMPLETE'
        AND (
            EXISTS (
                SELECT 1 FROM pg_catalog.jsonb_array_elements(p_wire->'raw') AS item(value)
                WHERE item.value->>'value' IS NULL
            )
            OR p_wire->>'trend' IS NULL OR p_wire->>'quality' IS NULL
            OR p_wire->>'value' IS NULL OR p_wire->>'low_risk' IS NULL
            OR p_wire->>'composite' IS NULL
            OR p_wire->>'missing_reason' IS NOT NULL
        )
    THEN
        RAISE EXCEPTION 'COMPLETE feature vector is incomplete'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'status' <> 'COMPLETE'
        AND (
            EXISTS (
                SELECT 1 FROM pg_catalog.jsonb_array_elements(p_wire->'raw') AS item(value)
                WHERE item.value->>'value' IS NOT NULL
            )
            OR p_wire->>'trend' IS NOT NULL OR p_wire->>'quality' IS NOT NULL
            OR p_wire->>'value' IS NOT NULL OR p_wire->>'low_risk' IS NOT NULL
            OR p_wire->>'composite' IS NOT NULL
            OR p_wire->>'missing_reason' IS NULL
            OR p_wire->>'missing_reason' = ''
        )
    THEN
        RAISE EXCEPTION 'non-COMPLETE feature vector carries incomplete missing lineage'
            USING ERRCODE = '23514';
    END IF;
    IF (p_wire->>'known_at')::timestamptz > (p_wire->>'as_of')::timestamptz THEN
        RAISE EXCEPTION 'feature vector timestamps are not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(p_wire->'raw') IS DISTINCT FROM 'array'
        OR p_wire->>'manifest_hash' <> 'a95be51a7468c73a8a6bfdda05fb4fd9076703afdae9f3a9bff7b2d4a8f6fcc7'
    THEN
        RAISE EXCEPTION 'feature vector is not bound to the approved factor manifest'
            USING ERRCODE = '23514';
    END IF;
    -- Same calendar month, universe not in the future, and visible at cutoff.
    IF NOT EXISTS (
        SELECT 1
        FROM public.universe_snapshots AS universe
        WHERE universe.universe_hash = p_wire->>'universe_hash'
          AND EXTRACT(YEAR FROM universe.as_of) = EXTRACT(
                YEAR FROM (p_wire->>'as_of')::timestamptz AT TIME ZONE 'UTC'
              )
          AND EXTRACT(MONTH FROM universe.as_of) = EXTRACT(
                MONTH FROM (p_wire->>'as_of')::timestamptz AT TIME ZONE 'UTC'
              )
          AND universe.as_of <= (p_wire->>'as_of')::timestamptz::date
          AND universe.known_at <= (p_wire->>'known_at')::timestamptz
    )
    THEN
        RAISE EXCEPTION 'feature vector universe lineage is not present or not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'status' = 'COMPLETE'
        AND NOT EXISTS (
            SELECT 1
            FROM public.universe_snapshot_entries AS entry
            WHERE entry.universe_hash = p_wire->>'universe_hash'
              AND entry.security_id = p_wire->>'security_id'
              AND entry.symbol = p_wire->>'symbol'
              AND entry.eligible
              AND entry.quarantine_decision_hash IS NOT NULL
        )
    THEN
        RAISE EXCEPTION 'COMPLETE feature vector is not a member of the eligible universe'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4c.feature-vector.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_feature_hash
    THEN
        RAISE EXCEPTION 'feature hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    SELECT f.wire INTO v_existing_wire
    FROM public.feature_vectors AS f
    WHERE f.feature_hash = p_feature_hash;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'feature hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    INSERT INTO public.feature_vectors (
        feature_hash, security_id, symbol, universe_hash, manifest_hash, as_of, status, wire
    ) VALUES (
        p_feature_hash,
        p_wire->>'security_id',
        p_wire->>'symbol',
        p_wire->>'universe_hash',
        p_wire->>'manifest_hash',
        (p_wire->>'as_of')::timestamptz,
        p_wire->>'status',
        p_wire
    ) ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        SELECT f.wire INTO v_existing_wire
        FROM public.feature_vectors AS f
        WHERE f.feature_hash = p_feature_hash;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'feature vector conflict could not be resolved'
                USING ERRCODE = '23505';
        END IF;
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'feature hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;
    RETURN 'APPENDED';
END;
$$;

-- Replace the pinned known_at invariant in the universe authority: the
-- monthly universe may become known on any day of its own month, not only
-- on the 1st.
CREATE OR REPLACE FUNCTION public.append_universe_snapshot(
    p_universe_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing_wire JSONB;
    v_entry JSONB;
    v_ordinal INTEGER := 1;
BEGIN
    IF p_universe_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'universe hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire IS NULL
        OR COALESCE(
            pg_catalog.octet_length(
                pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8')
            ),
            0
        ) > 16777216
    THEN
        RAISE EXCEPTION 'universe snapshot canonical wire exceeds the 16777216-byte limit'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 8
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS k(key_name)
            WHERE k.key_name <> ALL (ARRAY[
                'as_of', 'known_at', 'security_master_version', 'market_snapshot_refs',
                'entries', 'policy_hash', 'schema_version', 'producer_version'
            ]::text[])
        )
        OR jsonb_typeof(p_wire->'as_of') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'known_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'security_master_version') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'market_snapshot_refs') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'entries') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'policy_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'schema_version') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'producer_version') IS DISTINCT FROM 'string'
        OR p_wire->>'as_of' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
        OR p_wire->>'security_master_version' !~ '^p4b\.securities\.v1:[0-9a-f]{64}$'
        OR p_wire->>'policy_hash' !~ '^[0-9a-f]{64}$'
        OR p_wire->>'schema_version' <> '1.0.0'
        OR p_wire->>'producer_version' <> 'p4c.universe.v1'
        OR p_wire->>'known_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR jsonb_array_length(p_wire->'entries') > 10000
        OR jsonb_array_length(p_wire->'market_snapshot_refs') > 10000
    THEN
        RAISE EXCEPTION 'universe snapshot wire shape is outside the P4-C contract'
            USING ERRCODE = '23514';
    END IF;
    IF EXTRACT(YEAR FROM (p_wire->>'known_at')::timestamptz AT TIME ZONE 'UTC')
            <> EXTRACT(YEAR FROM (p_wire->>'as_of')::date)
        OR EXTRACT(MONTH FROM (p_wire->>'known_at')::timestamptz AT TIME ZONE 'UTC')
            <> EXTRACT(MONTH FROM (p_wire->>'as_of')::date)
        OR ((p_wire->>'known_at')::timestamptz AT TIME ZONE 'UTC')::date
            < (p_wire->>'as_of')::date
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'market_snapshot_refs') AS item(value)
            WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') AS item(ref)
            WHERE item.ref !~ '^[0-9a-f]{64}$'
        )
        OR (
            SELECT pg_catalog.array_agg(item.ref ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') WITH ORDINALITY AS item(ref, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.ref ORDER BY item.ref)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') AS item(ref)
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') AS item(ref)
            GROUP BY item.ref
            HAVING count(*) > 1
        )
    THEN
        RAISE EXCEPTION 'universe snapshot references are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    -- Market snapshot lineage is from the same first open session.
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') AS item(ref)
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.market_snapshots AS market
            WHERE market.snapshot_hash = item.ref
              AND (market.as_of AT TIME ZONE 'UTC')::date = (p_wire->>'as_of')::date
              AND market.known_at <= (p_wire->>'known_at')::timestamptz
        )
    )
    THEN
        RAISE EXCEPTION 'universe market snapshot lineage is not present or not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    -- At least one referenced immutable market snapshot must carry an explicit
    -- MarketSession record for every calendar date from month start through
    -- as_of, with every earlier date CLOSED and as_of REGULAR/HALF_DAY.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') AS ref(value)
        JOIN public.market_snapshots AS market ON market.snapshot_hash = ref.value
        WHERE EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(market.wire->'sessions') AS session(value)
            WHERE (session.value->>'trading_date')::date = (p_wire->>'as_of')::date
              AND session.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
        )
          AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.generate_series(
                date_trunc('month', (p_wire->>'as_of')::date)::date,
                (p_wire->>'as_of')::date,
                interval '1 day'
            ) AS expected(calendar_date)
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(market.wire->'sessions') AS session(value)
                WHERE (session.value->>'trading_date')::date = expected.calendar_date::date
            )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(market.wire->'sessions') AS session(value)
            WHERE (session.value->>'trading_date')::date
                    >= date_trunc('month', (p_wire->>'as_of')::date)::date
              AND (session.value->>'trading_date')::date < (p_wire->>'as_of')::date
              AND session.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
          )
    )
    THEN
        RAISE EXCEPTION 'universe as_of is not proven as the first open NYSE session'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(p_wire->'entries') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'universe entries must be a JSON array'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(p_wire->'market_snapshot_refs') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'market snapshot refs must be a JSON array'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4c.universe-snapshot.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_universe_hash
    THEN
        RAISE EXCEPTION 'universe hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    SELECT u.wire INTO v_existing_wire
    FROM public.universe_snapshots AS u
    WHERE u.universe_hash = p_universe_hash;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'universe hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    -- Single authoritative universe per as-of date.
    IF EXISTS (
        SELECT 1
        FROM public.universe_snapshots AS u
        WHERE u.as_of = (p_wire->>'as_of')::date
          AND u.universe_hash IS DISTINCT FROM p_universe_hash
    ) THEN
        RAISE EXCEPTION 'universe snapshot conflicts with the existing as-of authority'
            USING ERRCODE = '23505';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'entries') AS item(value)
        GROUP BY item.value->>'security_id'
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'universe entries must contain one row per security'
            USING ERRCODE = '23514';
    END IF;

    FOR v_entry IN SELECT * FROM pg_catalog.jsonb_array_elements(p_wire->'entries')
    LOOP
        IF pg_catalog.jsonb_typeof(v_entry) IS DISTINCT FROM 'object'
            OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(v_entry)) <> 10
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_object_keys(v_entry) AS k(key_name)
                WHERE k.key_name <> ALL (ARRAY[
                    'security_id', 'symbol', 'eligible', 'reason', 'identity_hash',
                    'master_version', 'market_snapshot_hash', 'whole_share_feasibility',
                    'quarantine_decision_hash', 'quarantine_event_ids'
                ]::text[])
            )
            OR pg_catalog.jsonb_typeof(v_entry->'security_id') IS DISTINCT FROM 'string'
            OR pg_catalog.jsonb_typeof(v_entry->'symbol') IS DISTINCT FROM 'string'
            OR pg_catalog.jsonb_typeof(v_entry->'eligible') IS DISTINCT FROM 'boolean'
            OR pg_catalog.jsonb_typeof(v_entry->'reason') NOT IN ('null', 'string')
            OR pg_catalog.jsonb_typeof(v_entry->'identity_hash') NOT IN ('null', 'string')
            OR pg_catalog.jsonb_typeof(v_entry->'master_version') NOT IN ('null', 'string')
            OR pg_catalog.jsonb_typeof(v_entry->'market_snapshot_hash') NOT IN ('null', 'string')
            OR pg_catalog.jsonb_typeof(v_entry->'whole_share_feasibility') IS DISTINCT FROM 'string'
            OR pg_catalog.jsonb_typeof(v_entry->'quarantine_decision_hash') NOT IN ('null', 'string')
            OR pg_catalog.jsonb_typeof(v_entry->'quarantine_event_ids') IS DISTINCT FROM 'array'
        THEN
            RAISE EXCEPTION 'universe entry has an invalid shape'
                USING ERRCODE = '23514';
        END IF;
        IF v_entry->>'security_id' !~ '^[0-9a-f][0-9a-f-]{7,63}$'
            OR v_entry->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
            OR v_entry->>'whole_share_feasibility' <> 'NOT_EVALUATED'
            OR v_entry->>'identity_hash' IS NOT NULL
                AND v_entry->>'identity_hash' !~ '^[0-9a-f]{64}$'

            OR v_entry->>'market_snapshot_hash' IS NOT NULL
                AND v_entry->>'market_snapshot_hash' !~ '^[0-9a-f]{64}$'
            OR v_entry->>'quarantine_decision_hash' IS NOT NULL
                AND v_entry->>'quarantine_decision_hash' !~ '^[0-9a-f]{64}$'
            OR v_entry->>'master_version' IS NOT NULL
                AND v_entry->>'master_version' = ''
            OR v_entry->>'master_version' IS NOT NULL
                AND v_entry->>'master_version' !~ '^p4b\.securities\.v1:[0-9a-f]{64}$'
        THEN
            RAISE EXCEPTION 'universe entry scalar values are invalid'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(v_entry->'quarantine_event_ids') AS event_id(value)
            WHERE jsonb_typeof(event_id.value) IS DISTINCT FROM 'string'
                OR event_id.value #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
        )
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements_text(v_entry->'quarantine_event_ids') AS event_id(value)
                GROUP BY event_id.value
                HAVING count(*) > 1
            )
            OR (
                SELECT pg_catalog.array_agg(event_id.value ORDER BY event_id.ordinality)
                FROM pg_catalog.jsonb_array_elements_text(v_entry->'quarantine_event_ids')
                    WITH ORDINALITY AS event_id(value, ordinality)
            ) IS DISTINCT FROM (
                SELECT pg_catalog.array_agg(event_id.value ORDER BY event_id.value)
                FROM pg_catalog.jsonb_array_elements_text(v_entry->'quarantine_event_ids') AS event_id(value)
            )
        THEN
            RAISE EXCEPTION 'quarantine event ids are invalid or unordered'
                USING ERRCODE = '23514';
        END IF;
        IF (v_entry->>'eligible')::boolean
            AND pg_catalog.jsonb_typeof(v_entry->'reason') IS DISTINCT FROM 'null'
        THEN
            RAISE EXCEPTION 'eligible universe entries require a null reason'
                USING ERRCODE = '23514';
        END IF;
        IF NOT (v_entry->>'eligible')::boolean
            AND pg_catalog.jsonb_typeof(v_entry->'reason') IS DISTINCT FROM 'string'
        THEN
            RAISE EXCEPTION 'ineligible universe entries require a reason'
                USING ERRCODE = '23514';
        END IF;
        IF (v_entry->>'eligible')::boolean
            AND (
                v_entry->>'identity_hash' IS NULL
                OR v_entry->>'master_version' IS NULL
                OR v_entry->>'market_snapshot_hash' IS NULL
                OR v_entry->>'quarantine_decision_hash' IS NULL
            )
        THEN
            RAISE EXCEPTION 'eligible universe entries require closed references'
                USING ERRCODE = '23514';
        END IF;
        IF v_entry->>'quarantine_decision_hash' IS NULL
            AND jsonb_array_length(v_entry->'quarantine_event_ids') <> 0
        THEN
            RAISE EXCEPTION 'quarantine event ids require a decision hash'
                USING ERRCODE = '23514';
        END IF;
        IF v_entry->>'identity_hash' IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM public.security_identities AS identity_record
                WHERE identity_record.identity_hash = v_entry->>'identity_hash'
                  AND identity_record.security_id = v_entry->>'security_id'
                  AND identity_record.symbol = v_entry->>'symbol'
                  AND identity_record.status = 'active'
                  AND identity_record.available_at <= (p_wire->>'known_at')::timestamptz
                  AND identity_record.valid_from <= (p_wire->>'as_of')::date::timestamptz
                  AND (
                      identity_record.valid_to IS NULL
                      OR identity_record.valid_to > (p_wire->>'as_of')::date::timestamptz
                  )
                  AND v_entry->>'master_version' = 'p4b.securities.v1:' || identity_record.identity_hash
            )
        THEN
            RAISE EXCEPTION 'universe identity lineage is not point-in-time authoritative'
                USING ERRCODE = '23514';
        END IF;
        IF v_entry->>'market_snapshot_hash' IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') AS ref(value)
                WHERE ref.value = v_entry->>'market_snapshot_hash'
            )
        THEN
            RAISE EXCEPTION 'universe entry market reference is absent from the parent reference set'
                USING ERRCODE = '23514';
        END IF;
        IF v_entry->>'market_snapshot_hash' IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM public.market_snapshots AS market
                WHERE market.snapshot_hash = v_entry->>'market_snapshot_hash'
                  AND market.security_id = v_entry->>'security_id'
                  AND market.symbol = v_entry->>'symbol'
                  AND (market.as_of AT TIME ZONE 'UTC')::date = (p_wire->>'as_of')::date
                  AND market.known_at <= (p_wire->>'known_at')::timestamptz
            )
        THEN
            RAISE EXCEPTION 'universe market reference is not bound to the entry security'
                USING ERRCODE = '23514';
        END IF;
        IF (v_entry->>'eligible')::boolean
            AND NOT EXISTS (
                SELECT 1
                FROM public.market_snapshots AS market
                WHERE market.snapshot_hash = v_entry->>'market_snapshot_hash'
                  AND market.wire->>'freshness' = 'FRESH'
                  AND market.wire->>'last' IS NOT NULL
                  AND (market.wire->>'last')::numeric >= 5
                  AND market.wire->>'adv20_usd' IS NOT NULL
                  AND (market.wire->>'adv20_usd')::numeric >= 20000000
                  AND (market.wire->>'spread_bps') IS NOT NULL
                  AND (market.wire->>'spread_bps')::integer <= 30
                  AND jsonb_array_length(market.wire->'bar_dates') >= 252
                  AND market.wire->'reasons' = '[]'::jsonb
            )
        THEN
            RAISE EXCEPTION 'eligible universe entry is not backed by an eligible market snapshot'
                USING ERRCODE = '23514';
        END IF;
        IF v_entry->>'quarantine_decision_hash' IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM public.security_quarantine_decisions AS decision
                WHERE decision.decision_hash = v_entry->>'quarantine_decision_hash'
                  AND decision.security_id = v_entry->>'security_id'
                  AND decision.symbol_as_of = v_entry->>'symbol'
                  AND decision.master_version = v_entry->>'master_version'
                  AND (
                      NOT (v_entry->>'eligible')::boolean
                      OR decision.outcome = 'ELIGIBLE'
                  )
                  AND decision.decision_at <= (p_wire->>'known_at')::timestamptz
                  AND ARRAY(
                      SELECT jsonb_array_elements_text(v_entry->'quarantine_event_ids')
                  ) IS NOT DISTINCT FROM decision.event_ids
            )
        THEN
            RAISE EXCEPTION 'universe quarantine decision is not bound to the entry'
                USING ERRCODE = '23514';
        END IF;
        IF v_entry->>'reason' IS NOT NULL
            AND v_entry->>'reason' <> ALL (ARRAY[
                'UNSUPPORTED_ASSET_CLASS', 'OTC_OR_EXCLUDED_INSTRUMENT',
                'NOT_ACTIVE_OR_TRADABLE', 'PRICE_BELOW_MINIMUM', 'ADV_BELOW_MINIMUM',
                'INSUFFICIENT_TRADING_HISTORY', 'IDENTITY_NOT_CLOSED',
                'CORPORATE_ACTION_QUARANTINE', 'QUOTE_MISSING_OR_STALE',
                'SPREAD_TOO_WIDE', 'MARKET_DATA_CONFLICT', 'FACTOR_INPUT_MISSING',
                'FACTOR_MANIFEST_NOT_APPROVED', 'SECTOR_TAXONOMY_NOT_AUTHORIZED',
                'CLUSTER_POLICY_NOT_APPROVED', 'EVIDENCE_INSUFFICIENT_OR_CONFLICTING',
                'WINDOW_OR_DEADLINE_INVALID'
            ]::text[])
        THEN
            RAISE EXCEPTION 'universe entry reason is not a closed P4-C reason'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT item.value->>'security_id' AS security_id,
                   item.ordinality,
                   row_number() OVER (ORDER BY item.value->>'security_id') AS expected_ordinal
            FROM pg_catalog.jsonb_array_elements(p_wire->'entries') WITH ORDINALITY AS item(value, ordinality)
        ) AS ordered
        WHERE ordered.ordinality <> ordered.expected_ordinal
    )
    THEN
        RAISE EXCEPTION 'universe entries must be ordered by security id'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.universe_snapshots (
        universe_hash, as_of, known_at, policy_hash, wire
    ) VALUES (
        p_universe_hash,
        (p_wire->>'as_of')::date,
        (p_wire->>'known_at')::timestamptz,
        p_wire->>'policy_hash',
        p_wire
    ) ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        SELECT u.wire INTO v_existing_wire
        FROM public.universe_snapshots AS u
        WHERE u.universe_hash = p_universe_hash;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'universe snapshot conflict could not be resolved'
                USING ERRCODE = '23505';
        END IF;
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'universe hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    FOR v_entry IN SELECT * FROM jsonb_array_elements(p_wire->'entries')
    LOOP
        INSERT INTO public.universe_snapshot_entries (
            universe_hash, ordinal, security_id, symbol, eligible,
            reason, identity_hash, master_version, market_snapshot_hash,
            whole_share_feasibility, quarantine_decision_hash, quarantine_event_ids
        ) VALUES (
            p_universe_hash,
            v_ordinal,
            v_entry->>'security_id',
            v_entry->>'symbol',
            (v_entry->>'eligible')::boolean,
            v_entry->>'reason',
            v_entry->>'identity_hash',
            v_entry->>'master_version',
            v_entry->>'market_snapshot_hash',
            v_entry->>'whole_share_feasibility',
            v_entry->>'quarantine_decision_hash',
            ARRAY(SELECT jsonb_array_elements_text(v_entry->'quarantine_event_ids'))
        );
        v_ordinal := v_ordinal + 1;
    END LOOP;

    RETURN 'APPENDED';
END;
$$;

CREATE OR REPLACE FUNCTION public.append_candidate_set(
    p_candidate_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing_wire JSONB;
    v_entry JSONB;
    v_ordinal INTEGER := 1;
    v_stage TEXT;
    v_stages TEXT[] := ARRAY['quant', 'evidence', 'focus_open', 'focus_close'];
    v_stage_name TEXT;
BEGIN
    IF p_candidate_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'candidate hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire IS NULL
        OR COALESCE(
            pg_catalog.octet_length(
                pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8')
            ),
            0
        ) > 4194304
    THEN
        RAISE EXCEPTION 'candidate set canonical wire exceeds the 4194304-byte limit'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 12
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS k(key_name)
            WHERE k.key_name <> ALL (ARRAY[
                'as_of', 'known_at', 'factor_manifest_hash', 'cluster_manifest_hash',
                'universe_hash', 'quant', 'evidence', 'focus_open', 'focus_close',
                'policy_hash', 'producer_version', 'schema_version'
            ]::text[])
        )
        OR jsonb_typeof(p_wire->'as_of') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'known_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'factor_manifest_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'cluster_manifest_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'universe_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'quant') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'evidence') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'focus_open') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'focus_close') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'policy_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'producer_version') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'schema_version') IS DISTINCT FROM 'string'
        OR p_wire->>'factor_manifest_hash' <> 'a95be51a7468c73a8a6bfdda05fb4fd9076703afdae9f3a9bff7b2d4a8f6fcc7'
        OR p_wire->>'cluster_manifest_hash' <> '34aa2e2e2056cb21495ed398ab2d816ee90b9fd257c632a878466989ef3cfa0e'
        OR p_wire->>'universe_hash' !~ '^[0-9a-f]{64}$'
        OR p_wire->>'policy_hash' !~ '^[0-9a-f]{64}$'
        OR p_wire->>'producer_version' <> 'p4c.screening.v1'
        OR p_wire->>'schema_version' <> '1.0.0'
        OR p_wire->>'as_of' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'known_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR jsonb_array_length(p_wire->'quant') > 100
        OR jsonb_array_length(p_wire->'evidence') > 30
        OR jsonb_array_length(p_wire->'focus_open') > 12
        OR jsonb_array_length(p_wire->'focus_close') > 5
    THEN
        RAISE EXCEPTION 'candidate set wire shape is outside the P4-C contract'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.jsonb_array_length(p_wire->'focus_open') <> LEAST(
            pg_catalog.jsonb_array_length(p_wire->'evidence'), 12
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open')
                WITH ORDINALITY AS child(value, ordinal)
            JOIN pg_catalog.jsonb_array_elements(p_wire->'evidence')
                WITH ORDINALITY AS parent(value, ordinal)
                ON child.ordinal = parent.ordinal
            WHERE (child.value - 'stage') IS DISTINCT FROM (parent.value - 'stage')
               OR child.value->>'stage' IS DISTINCT FROM 'FOCUS_OPEN'
               OR parent.value->>'stage' IS DISTINCT FROM 'EVIDENCE'
        )
    THEN
        RAISE EXCEPTION 'candidate focus_open must equal the canonical evidence prefix'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.jsonb_array_length(p_wire->'focus_close') <> LEAST(
            pg_catalog.jsonb_array_length(p_wire->'evidence'), 5
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close')
                WITH ORDINALITY AS child(value, ordinal)
            JOIN pg_catalog.jsonb_array_elements(p_wire->'evidence')
                WITH ORDINALITY AS parent(value, ordinal)
                ON child.ordinal = parent.ordinal
            WHERE (child.value - 'stage') IS DISTINCT FROM (parent.value - 'stage')
               OR child.value->>'stage' IS DISTINCT FROM 'FOCUS_CLOSE'
               OR parent.value->>'stage' IS DISTINCT FROM 'EVIDENCE'
        )
    THEN
        RAISE EXCEPTION 'candidate focus_close must equal the canonical evidence prefix'
            USING ERRCODE = '23514';
    END IF;
    IF (p_wire->>'known_at')::timestamptz > (p_wire->>'as_of')::timestamptz THEN
        RAISE EXCEPTION 'candidate set timestamps are not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    -- Same calendar month, universe not in the future, and visible at cutoff.
    IF NOT EXISTS (
        SELECT 1
        FROM public.universe_snapshots AS universe
        WHERE universe.universe_hash = p_wire->>'universe_hash'
          AND EXTRACT(YEAR FROM universe.as_of) = EXTRACT(
                YEAR FROM (p_wire->>'as_of')::timestamptz AT TIME ZONE 'UTC'
              )
          AND EXTRACT(MONTH FROM universe.as_of) = EXTRACT(
                MONTH FROM (p_wire->>'as_of')::timestamptz AT TIME ZONE 'UTC'
              )
          AND universe.as_of <= (p_wire->>'as_of')::timestamptz::date
          AND universe.known_at <= (p_wire->>'as_of')::timestamptz
    )
    THEN
        RAISE EXCEPTION 'candidate set universe lineage is not present or not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'factor_manifest_hash' <> 'a95be51a7468c73a8a6bfdda05fb4fd9076703afdae9f3a9bff7b2d4a8f6fcc7'
        OR p_wire->>'cluster_manifest_hash' <> '34aa2e2e2056cb21495ed398ab2d816ee90b9fd257c632a878466989ef3cfa0e'
        OR jsonb_typeof(p_wire->'quant') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'evidence') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'focus_open') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'focus_close') IS DISTINCT FROM 'array'
    THEN
        RAISE EXCEPTION 'candidate set has an unapproved manifest or incomplete stages'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4c.candidate-set.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_candidate_hash
    THEN
        RAISE EXCEPTION 'candidate hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'quant') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close') AS item(value)
        ) AS entry
        WHERE entry.value->>'universe_hash' IS DISTINCT FROM p_wire->>'universe_hash'
            OR NOT EXISTS (
                SELECT 1
                FROM public.feature_vectors AS feature
                WHERE feature.feature_hash = entry.value->>'feature_hash'
                  AND feature.security_id = entry.value->>'security_id'
                  AND feature.symbol = entry.value->>'symbol'
                  AND feature.universe_hash = p_wire->>'universe_hash'
                  AND feature.as_of = (p_wire->>'as_of')::timestamptz
                  AND (feature.wire->>'known_at')::timestamptz <= (p_wire->>'known_at')::timestamptz
                  AND feature.status = 'COMPLETE'
                  AND feature.wire->>'composite' = entry.value->>'composite'
                  AND feature.wire->>'trend' = entry.value->>'trend'
                  AND feature.wire->>'quality' = entry.value->>'quality'
                  AND feature.wire->>'value' = entry.value->>'value'
                  AND feature.wire->>'low_risk' = entry.value->>'low_risk'
            )
    )
    THEN
        RAISE EXCEPTION 'candidate feature lineage is not complete or not bound to the candidate universe'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'quant') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close') AS item(value)
        ) AS entry
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.security_quarantine_decisions AS decision
            WHERE decision.decision_hash = entry.value->>'quarantine_decision_hash'
              AND decision.security_id = entry.value->>'security_id'
              AND decision.symbol_as_of = entry.value->>'symbol'
              AND decision.outcome = 'ELIGIBLE'
              AND decision.decision_at <= (p_wire->>'known_at')::timestamptz
        )
    )
    THEN
        RAISE EXCEPTION 'candidate quarantine lineage is not an eligible point-in-time decision'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'quant') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open') AS item(value)
            UNION ALL
            SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close') AS item(value)
        ) AS entry
        WHERE entry.value->>'stage' <> 'QUANT'
          AND NOT EXISTS (
              SELECT 1
              FROM public.sector_assignments AS assignment
              JOIN public.universe_snapshots AS universe
                ON universe.universe_hash = p_wire->>'universe_hash'
              JOIN public.universe_snapshot_entries AS universe_entry
                ON universe_entry.universe_hash = universe.universe_hash
               AND universe_entry.security_id = entry.value->>'security_id'
               AND universe_entry.symbol = entry.value->>'symbol'
              JOIN public.security_identities AS identity_record
                ON identity_record.identity_hash = universe_entry.identity_hash
              WHERE assignment.assignment_hash = entry.value->>'sector_assignment_hash'
                AND assignment.security_id = entry.value->>'security_id'
                AND assignment.available_at <= universe.known_at
                AND assignment.taxonomy_version = 'sec-sic-division-v1'
                AND assignment.taxonomy_hash = '816dad7c0d8daa45dcb0fef0b18b27552f5f471fbb7ab725328bd9562b1e2136'
                AND assignment.division <> 'SECTOR_UNKNOWN'
                AND universe_entry.eligible
                AND identity_record.status = 'active'
                AND identity_record.available_at <= universe.known_at
                AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
                AND (
                    identity_record.valid_to IS NULL
                    OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
                )
                AND identity_record.wire->>'cik' = assignment.cik
          )
    )
    THEN
        RAISE EXCEPTION 'candidate sector lineage is not complete or not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'quant') AS item(value)
        GROUP BY item.value->>'security_id'
        HAVING count(*) > 1
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS item(value)
            GROUP BY item.value->>'security_id'
            HAVING count(*) > 1
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open') AS item(value)
            GROUP BY item.value->>'security_id'
            HAVING count(*) > 1
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close') AS item(value)
            GROUP BY item.value->>'security_id'
            HAVING count(*) > 1
        )
    THEN
        RAISE EXCEPTION 'candidate stages must contain one row per security'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS child(value)
        WHERE NOT EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'quant') AS parent(value)
            WHERE parent.value->>'security_id' = child.value->>'security_id'
        )
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open') AS child(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS parent(value)
                WHERE parent.value->>'security_id' = child.value->>'security_id'
            )
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close') AS child(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS parent(value)
                WHERE parent.value->>'security_id' = child.value->>'security_id'
            )
        )
    THEN
        RAISE EXCEPTION 'candidate stages must be parent subsets'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'evidence') AS child(value)
        JOIN pg_catalog.jsonb_array_elements(p_wire->'quant') AS parent(value)
            ON parent.value->>'security_id' = child.value->>'security_id'
            WHERE child.value->>'symbol' IS DISTINCT FROM parent.value->>'symbol'
            OR child.value->>'composite' IS DISTINCT FROM parent.value->>'composite'
            OR child.value->>'trend' IS DISTINCT FROM parent.value->>'trend'
            OR child.value->>'quality' IS DISTINCT FROM parent.value->>'quality'
            OR child.value->>'value' IS DISTINCT FROM parent.value->>'value'
            OR child.value->>'low_risk' IS DISTINCT FROM parent.value->>'low_risk'
            OR child.value->>'feature_hash' IS DISTINCT FROM parent.value->>'feature_hash'
            OR child.value->>'universe_hash' IS DISTINCT FROM parent.value->>'universe_hash'
            OR child.value->>'quarantine_decision_hash' IS DISTINCT FROM parent.value->>'quarantine_decision_hash'
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open') AS child(value)
            JOIN pg_catalog.jsonb_array_elements(p_wire->'evidence') AS parent(value)
                ON parent.value->>'security_id' = child.value->>'security_id'
            WHERE child.value->>'symbol' IS DISTINCT FROM parent.value->>'symbol'
            OR child.value->>'composite' IS DISTINCT FROM parent.value->>'composite'
            OR child.value->>'trend' IS DISTINCT FROM parent.value->>'trend'
            OR child.value->>'quality' IS DISTINCT FROM parent.value->>'quality'
            OR child.value->>'value' IS DISTINCT FROM parent.value->>'value'
            OR child.value->>'low_risk' IS DISTINCT FROM parent.value->>'low_risk'
            OR child.value->>'feature_hash' IS DISTINCT FROM parent.value->>'feature_hash'
            OR child.value->>'universe_hash' IS DISTINCT FROM parent.value->>'universe_hash'
            OR child.value->>'quarantine_decision_hash' IS DISTINCT FROM parent.value->>'quarantine_decision_hash'
            OR child.value->>'sector_assignment_hash' IS DISTINCT FROM parent.value->>'sector_assignment_hash'
            OR child.value->'evidence_source_refs' IS DISTINCT FROM parent.value->'evidence_source_refs'
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close') AS child(value)
            JOIN pg_catalog.jsonb_array_elements(p_wire->'evidence') AS parent(value)
                ON parent.value->>'security_id' = child.value->>'security_id'
            WHERE child.value->>'symbol' IS DISTINCT FROM parent.value->>'symbol'
            OR child.value->>'composite' IS DISTINCT FROM parent.value->>'composite'
            OR child.value->>'trend' IS DISTINCT FROM parent.value->>'trend'
            OR child.value->>'quality' IS DISTINCT FROM parent.value->>'quality'
            OR child.value->>'value' IS DISTINCT FROM parent.value->>'value'
            OR child.value->>'low_risk' IS DISTINCT FROM parent.value->>'low_risk'
            OR child.value->>'feature_hash' IS DISTINCT FROM parent.value->>'feature_hash'
            OR child.value->>'universe_hash' IS DISTINCT FROM parent.value->>'universe_hash'
            OR child.value->>'quarantine_decision_hash' IS DISTINCT FROM parent.value->>'quarantine_decision_hash'
            OR child.value->>'sector_assignment_hash' IS DISTINCT FROM parent.value->>'sector_assignment_hash'
            OR child.value->'evidence_source_refs' IS DISTINCT FROM parent.value->'evidence_source_refs'
    )
    THEN
        RAISE EXCEPTION 'candidate stages must preserve parent identity and score lineage'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT child.ordinality AS child_ordinal,
                   parent.ordinality AS parent_ordinal,
                   row_number() OVER (ORDER BY child.ordinality) AS child_position,
                   row_number() OVER (ORDER BY parent.ordinality) AS parent_position
            FROM pg_catalog.jsonb_array_elements(p_wire->'evidence')
                WITH ORDINALITY AS child(value, ordinality)
            JOIN pg_catalog.jsonb_array_elements(p_wire->'quant')
                WITH ORDINALITY AS parent(value, ordinality)
                ON parent.value->>'security_id' = child.value->>'security_id'
        ) AS ordered
        WHERE ordered.child_position <> ordered.parent_position
    )
        OR EXISTS (
            SELECT 1
            FROM (
                SELECT child.ordinality AS child_ordinal,
                       parent.ordinality AS parent_ordinal,
                       row_number() OVER (ORDER BY child.ordinality) AS child_position,
                       row_number() OVER (ORDER BY parent.ordinality) AS parent_position
                FROM pg_catalog.jsonb_array_elements(p_wire->'focus_open')
                    WITH ORDINALITY AS child(value, ordinality)
                JOIN pg_catalog.jsonb_array_elements(p_wire->'evidence')
                    WITH ORDINALITY AS parent(value, ordinality)
                    ON parent.value->>'security_id' = child.value->>'security_id'
            ) AS ordered
            WHERE ordered.child_position <> ordered.parent_position
        )
        OR EXISTS (
            SELECT 1
            FROM (
                SELECT child.ordinality AS child_ordinal,
                       parent.ordinality AS parent_ordinal,
                       row_number() OVER (ORDER BY child.ordinality) AS child_position,
                       row_number() OVER (ORDER BY parent.ordinality) AS parent_position
                FROM pg_catalog.jsonb_array_elements(p_wire->'focus_close')
                    WITH ORDINALITY AS child(value, ordinality)
                JOIN pg_catalog.jsonb_array_elements(p_wire->'evidence')
                    WITH ORDINALITY AS parent(value, ordinality)
                    ON parent.value->>'security_id' = child.value->>'security_id'
            ) AS ordered
            WHERE ordered.child_position <> ordered.parent_position
        )
    THEN
        RAISE EXCEPTION 'candidate stages must preserve parent order'
            USING ERRCODE = '23514';
    END IF;

    FOREACH v_stage_name IN ARRAY v_stages
    LOOP
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->v_stage_name) AS item(value)
            WHERE jsonb_typeof(item.value->'reasons') IS DISTINCT FROM 'array'
                OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.jsonb_array_elements(item.value->'reasons') AS reason(value)
                    WHERE jsonb_typeof(reason.value) IS DISTINCT FROM 'string'
                )
        )
        THEN
            RAISE EXCEPTION 'candidate reasons must be an array of strings'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM (
                SELECT item.ordinality,
                       row_number() OVER (
                           ORDER BY
                               (item.value->>'composite')::numeric DESC,
                               (item.value->>'trend')::numeric DESC,
                               (item.value->>'quality')::numeric DESC,
                               (item.value->>'value')::numeric DESC,
                               (item.value->>'low_risk')::numeric DESC,
                               item.value->>'security_id'
                       ) AS expected_ordinal
                FROM pg_catalog.jsonb_array_elements(p_wire->v_stage_name)
                    WITH ORDINALITY AS item(value, ordinality)
            ) AS ordered
            WHERE ordered.ordinality <> ordered.expected_ordinal
        )
        THEN
            RAISE EXCEPTION 'candidate stage order is not canonical'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    SELECT c.wire INTO v_existing_wire
    FROM public.candidate_sets AS c
    WHERE c.candidate_hash = p_candidate_hash;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'candidate hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    INSERT INTO public.candidate_sets (
        candidate_hash, as_of, known_at,
        factor_manifest_hash, cluster_manifest_hash,
        universe_hash, policy_hash, wire
    ) VALUES (
        p_candidate_hash,
        (p_wire->>'as_of')::timestamptz,
        (p_wire->>'known_at')::timestamptz,
        p_wire->>'factor_manifest_hash',
        p_wire->>'cluster_manifest_hash',
        p_wire->>'universe_hash',
        p_wire->>'policy_hash',
        p_wire
    ) ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        SELECT c.wire INTO v_existing_wire
        FROM public.candidate_sets AS c
        WHERE c.candidate_hash = p_candidate_hash;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'candidate set conflict could not be resolved'
                USING ERRCODE = '23505';
        END IF;
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'candidate hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    FOREACH v_stage_name IN ARRAY v_stages
    LOOP
        v_ordinal := 1;
        FOR v_entry IN SELECT * FROM jsonb_array_elements(p_wire->v_stage_name)
        LOOP
            IF pg_catalog.jsonb_typeof(v_entry) IS DISTINCT FROM 'object'
                OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(v_entry)) <> 14
                OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.jsonb_object_keys(v_entry) AS k(key_name)
                    WHERE k.key_name <> ALL (ARRAY[
                        'security_id', 'symbol', 'composite', 'trend', 'quality',
                        'value', 'low_risk', 'stage', 'feature_hash', 'universe_hash',
                        'quarantine_decision_hash', 'sector_assignment_hash',
                        'evidence_source_refs', 'reasons'
                    ]::text[])
                )
                OR pg_catalog.jsonb_typeof(v_entry->'security_id') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'symbol') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'composite') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'trend') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'quality') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'value') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'low_risk') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'stage') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'feature_hash') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'universe_hash') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'quarantine_decision_hash') IS DISTINCT FROM 'string'
                OR pg_catalog.jsonb_typeof(v_entry->'sector_assignment_hash') NOT IN ('null', 'string')
                OR pg_catalog.jsonb_typeof(v_entry->'evidence_source_refs') IS DISTINCT FROM 'array'
                OR pg_catalog.jsonb_typeof(v_entry->'reasons') IS DISTINCT FROM 'array'
                OR v_entry->>'security_id' !~ '^[0-9a-f][0-9a-f-]{7,63}$'
                OR v_entry->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
                OR v_entry->>'composite' !~ '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$'
                OR v_entry->>'trend' !~ '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$'
                OR v_entry->>'quality' !~ '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$'
                OR v_entry->>'value' !~ '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$'
                OR v_entry->>'low_risk' !~ '^-?([0-9]+([.][0-9]+)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$'
                OR v_entry->>'feature_hash' !~ '^[0-9a-f]{64}$'
                OR v_entry->>'universe_hash' !~ '^[0-9a-f]{64}$'
                OR v_entry->>'quarantine_decision_hash' !~ '^[0-9a-f]{64}$'
                OR v_entry->>'sector_assignment_hash' IS NOT NULL
                    AND v_entry->>'sector_assignment_hash' !~ '^[0-9a-f]{64}$'
            THEN
                RAISE EXCEPTION 'candidate entry has an invalid shape'
                    USING ERRCODE = '23514';
            END IF;
            IF v_entry->>'stage' <> upper(v_stage_name)
                AND NOT (v_stage_name = 'focus_open' AND v_entry->>'stage' = 'FOCUS_OPEN')
                AND NOT (v_stage_name = 'focus_close' AND v_entry->>'stage' = 'FOCUS_CLOSE')
            THEN
                RAISE EXCEPTION 'candidate entry stage does not match its array'
                    USING ERRCODE = '23514';
            END IF;
            IF (v_stage_name = 'quant' AND v_entry->>'sector_assignment_hash' IS NOT NULL)
                OR (v_stage_name <> 'quant' AND v_entry->>'sector_assignment_hash' IS NULL)
            THEN
                RAISE EXCEPTION 'candidate sector lineage does not match its stage'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(v_entry->'evidence_source_refs') AS ref(value)
                WHERE jsonb_typeof(ref.value) IS DISTINCT FROM 'object'
                    OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(ref.value)) <> 3
                    OR EXISTS (
                        SELECT 1
                        FROM pg_catalog.jsonb_object_keys(ref.value) AS k(key_name)
                        WHERE k.key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
                    )
                    OR jsonb_typeof(ref.value->'record_id') IS DISTINCT FROM 'string'
                    OR jsonb_typeof(ref.value->'family') IS DISTINCT FROM 'string'
                    OR jsonb_typeof(ref.value->'record_hash') IS DISTINCT FROM 'string'
                    OR ref.value->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
                    OR ref.value->>'family' NOT IN (
                        'ALPACA_ASSETS', 'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR',
                        'ISSUER_IR', 'EXCHANGE_OFFICIAL'
                    )
                    OR ref.value->>'record_hash' !~ '^[0-9a-f]{64}$'
            )
                OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.jsonb_array_elements(v_entry->'evidence_source_refs') AS ref(value)
                    GROUP BY ref.value->>'record_id'
                    HAVING count(*) > 1
                )
                OR (
                    SELECT pg_catalog.array_agg(ref.value ORDER BY ref.ordinality)
                    FROM pg_catalog.jsonb_array_elements(v_entry->'evidence_source_refs')
                        WITH ORDINALITY AS ref(value, ordinality)
                ) IS DISTINCT FROM (
                    SELECT pg_catalog.array_agg(ref.value ORDER BY
                        ref.value->>'family', ref.value->>'record_id', ref.value->>'record_hash')
                    FROM pg_catalog.jsonb_array_elements(v_entry->'evidence_source_refs') AS ref(value)
                )
            THEN
                RAISE EXCEPTION 'candidate evidence source references are invalid or unordered'
                    USING ERRCODE = '23514';
            END IF;
            IF (v_stage_name = 'quant' AND jsonb_array_length(v_entry->'evidence_source_refs') <> 0)
                OR (v_stage_name <> 'quant' AND jsonb_array_length(v_entry->'evidence_source_refs') = 0)
            THEN
                RAISE EXCEPTION 'candidate evidence source lineage does not match its stage'
                    USING ERRCODE = '23514';
            END IF;
            IF v_stage_name <> 'quant' AND EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(v_entry->'evidence_source_refs') AS ref(value)
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM public.p4_source_records AS source_record
                    WHERE source_record.record_id = ref.value->>'record_id'
                      AND source_record.record_hash = ref.value->>'record_hash'
                      AND source_record.family = ref.value->>'family'
                      AND ref.value->>'family' IN (
                          'ALPACA_ASSETS', 'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR',
                          'ISSUER_IR', 'EXCHANGE_OFFICIAL'
                      )
                      AND COALESCE(
                            (source_record.wire->>'available_at')::timestamptz,
                            source_record.retrieved_at
                          ) <= (p_wire->>'known_at')::timestamptz
                )
            ) THEN
                RAISE EXCEPTION 'candidate evidence source lineage is not point-in-time visible'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements_text(v_entry->'reasons') AS item(reason)
                WHERE item.reason <> ALL (ARRAY[
                    'UNSUPPORTED_ASSET_CLASS', 'OTC_OR_EXCLUDED_INSTRUMENT',
                    'NOT_ACTIVE_OR_TRADABLE', 'PRICE_BELOW_MINIMUM', 'ADV_BELOW_MINIMUM',
                    'INSUFFICIENT_TRADING_HISTORY', 'IDENTITY_NOT_CLOSED',
                    'CORPORATE_ACTION_QUARANTINE', 'QUOTE_MISSING_OR_STALE',
                    'SPREAD_TOO_WIDE', 'MARKET_DATA_CONFLICT', 'FACTOR_INPUT_MISSING',
                    'FACTOR_MANIFEST_NOT_APPROVED', 'SECTOR_TAXONOMY_NOT_AUTHORIZED',
                    'CLUSTER_POLICY_NOT_APPROVED', 'EVIDENCE_INSUFFICIENT_OR_CONFLICTING',
                    'WINDOW_OR_DEADLINE_INVALID'
                ]::text[])
            )
                OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.jsonb_array_elements_text(v_entry->'reasons') AS item(reason)
                    GROUP BY item.reason
                    HAVING count(*) > 1
                )
                OR (
                    SELECT pg_catalog.array_agg(item.reason ORDER BY item.ordinality)
                    FROM pg_catalog.jsonb_array_elements_text(v_entry->'reasons') WITH ORDINALITY AS item(reason, ordinality)
                ) IS DISTINCT FROM (
                    SELECT pg_catalog.array_agg(item.reason ORDER BY pg_catalog.array_position(ARRAY[
                        'UNSUPPORTED_ASSET_CLASS', 'OTC_OR_EXCLUDED_INSTRUMENT',
                        'NOT_ACTIVE_OR_TRADABLE', 'PRICE_BELOW_MINIMUM', 'ADV_BELOW_MINIMUM',
                        'INSUFFICIENT_TRADING_HISTORY', 'IDENTITY_NOT_CLOSED',
                        'CORPORATE_ACTION_QUARANTINE', 'QUOTE_MISSING_OR_STALE',
                        'SPREAD_TOO_WIDE', 'MARKET_DATA_CONFLICT', 'FACTOR_INPUT_MISSING',
                        'FACTOR_MANIFEST_NOT_APPROVED', 'SECTOR_TAXONOMY_NOT_AUTHORIZED',
                        'CLUSTER_POLICY_NOT_APPROVED', 'EVIDENCE_INSUFFICIENT_OR_CONFLICTING',
                        'WINDOW_OR_DEADLINE_INVALID'
                    ]::text[], item.reason))
                    FROM pg_catalog.jsonb_array_elements_text(v_entry->'reasons') AS item(reason)
                )
            THEN
                RAISE EXCEPTION 'candidate entry reasons are invalid or unordered'
                    USING ERRCODE = '23514';
            END IF;
            v_stage := CASE v_stage_name
                WHEN 'quant' THEN 'QUANT'
                WHEN 'evidence' THEN 'EVIDENCE'
                WHEN 'focus_open' THEN 'FOCUS_OPEN'
                WHEN 'focus_close' THEN 'FOCUS_CLOSE'
            END;
            INSERT INTO public.candidate_set_entries (
                candidate_hash, ordinal, security_id, symbol, stage, composite,
                feature_hash, universe_hash, quarantine_decision_hash,
                sector_assignment_hash, evidence_source_refs
            ) VALUES (
                p_candidate_hash,
                v_ordinal,
                v_entry->>'security_id',
                v_entry->>'symbol',
                v_stage,
                v_entry->>'composite',
                v_entry->>'feature_hash',
                v_entry->>'universe_hash',
                v_entry->>'quarantine_decision_hash',
                v_entry->>'sector_assignment_hash',
                v_entry->'evidence_source_refs'
            );
            v_ordinal := v_ordinal + 1;
        END LOOP;
    END LOOP;

    RETURN 'APPENDED';
END;
$$;
