-- 0024 up: P4-C market snapshots, universe snapshots, feature vectors, and candidate sets.
--
-- Every relation is append-only and guarded by the shared
-- prevent_append_only_mutation() trigger.  SECURITY DEFINER functions handle
-- every write so the runtime role needs only USAGE on the schema and EXECUTE
-- on the functions, never direct table INSERT/UPDATE/DELETE.

-- Legacy preflight: fail closed if any P4-C object name already exists.
DO $$
DECLARE
    legacy_table TEXT;
BEGIN
    FOREACH legacy_table IN ARRAY ARRAY[
        'market_snapshots',
        'universe_snapshots',
        'universe_snapshot_entries',
        'feature_vectors',
        'sector_assignments',
        'candidate_sets',
        'candidate_set_entries',
        'cluster_results'
    ]::text[]
    LOOP
        IF pg_catalog.to_regclass('public.' || legacy_table) IS NOT NULL THEN
            RAISE EXCEPTION 'legacy object blocks the P4-C storage: %', legacy_table
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    IF pg_catalog.to_regprocedure('public.append_market_snapshot(text,jsonb)') IS NOT NULL
        OR pg_catalog.to_regprocedure('public.append_universe_snapshot(text,jsonb)') IS NOT NULL
        OR pg_catalog.to_regprocedure('public.append_feature_vector(text,jsonb)') IS NOT NULL
        OR pg_catalog.to_regprocedure('public.append_sector_assignment(text,jsonb)') IS NOT NULL
        OR pg_catalog.to_regprocedure('public.append_candidate_set(text,jsonb)') IS NOT NULL
        OR pg_catalog.to_regprocedure('public.append_cluster_result(text,jsonb)') IS NOT NULL
    THEN
        RAISE EXCEPTION 'legacy function blocks the P4-C storage'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

-- ****************************************************************
-- Market snapshots: one immutable, hash-bound observation per row.
-- ****************************************************************
CREATE TABLE public.market_snapshots (
    snapshot_hash TEXT PRIMARY KEY CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    as_of TIMESTAMPTZ NOT NULL,
    known_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    feed TEXT NOT NULL CHECK (feed IN ('iex', 'sip_delayed')),
    coverage TEXT NOT NULL CHECK (coverage IN ('COMPLETE', 'LIMITED_MARKET_COVERAGE')),
    freshness TEXT NOT NULL CHECK (freshness IN ('FRESH', 'STALE', 'MISSING', 'CONFLICT')),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    -- Single authority per as-of instant for one security: a second snapshot
    -- for the same (security_id, as_of) with a different hash is a conflict.
    UNIQUE (security_id, as_of)
);

CREATE INDEX market_snapshots_security_idx
    ON public.market_snapshots (security_id, appended_at DESC);

-- ****************************************************************
-- Universe snapshots: one per as-of month, carrying ordered entries.
-- ****************************************************************
CREATE TABLE public.universe_snapshots (
    universe_hash TEXT PRIMARY KEY CHECK (universe_hash ~ '^[0-9a-f]{64}$'),
    as_of DATE NOT NULL CHECK (EXTRACT(DAY FROM as_of) = 1),
    known_at TIMESTAMPTZ NOT NULL,
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    -- Single authoritative universe per as-of date.
    UNIQUE (as_of)
);

CREATE INDEX universe_snapshots_as_of_idx
    ON public.universe_snapshots (as_of DESC);

CREATE TABLE public.universe_snapshot_entries (
    universe_hash TEXT NOT NULL REFERENCES public.universe_snapshots(universe_hash),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    eligible BOOLEAN NOT NULL,
    reason TEXT NULL,
    identity_hash TEXT NULL CHECK (identity_hash IS NULL OR identity_hash ~ '^[0-9a-f]{64}$'),
    master_version TEXT NULL,
    market_snapshot_hash TEXT NULL CHECK (market_snapshot_hash IS NULL OR market_snapshot_hash ~ '^[0-9a-f]{64}$')
        REFERENCES public.market_snapshots(snapshot_hash),
    whole_share_feasibility TEXT NOT NULL CHECK (whole_share_feasibility = 'NOT_EVALUATED'),
    quarantine_decision_hash TEXT NULL CHECK (
        quarantine_decision_hash IS NULL OR quarantine_decision_hash ~ '^[0-9a-f]{64}$'
    ) REFERENCES public.security_quarantine_decisions(decision_hash),
    quarantine_event_ids TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    FOREIGN KEY (identity_hash) REFERENCES public.security_identities(identity_hash),
    PRIMARY KEY (universe_hash, ordinal)
);

CREATE INDEX universe_entries_security_idx
    ON public.universe_snapshot_entries (security_id, universe_hash);

-- ****************************************************************
-- Feature vectors (P4 factor evaluation output).
-- ****************************************************************
CREATE TABLE public.feature_vectors (
    feature_hash TEXT PRIMARY KEY CHECK (feature_hash ~ '^[0-9a-f]{64}$'),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    universe_hash TEXT NOT NULL CHECK (universe_hash ~ '^[0-9a-f]{64}$')
        REFERENCES public.universe_snapshots(universe_hash),
    manifest_hash TEXT NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    as_of TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('COMPLETE', 'FACTOR_INPUT_MISSING', 'FACTOR_MANIFEST_NOT_APPROVED', 'SECTOR_TAXONOMY_NOT_AUTHORIZED')),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    -- Single authoritative feature vector per (security, as_of).
    UNIQUE (security_id, as_of)
);

CREATE INDEX feature_vectors_as_of_idx
    ON public.feature_vectors (as_of DESC, security_id);

-- ****************************************************************
-- SEC SIC Division assignments: durable parent lineage for non-quant stages.
-- ****************************************************************
CREATE TABLE public.sector_assignments (
    assignment_hash TEXT PRIMARY KEY CHECK (assignment_hash ~ '^[0-9a-f]{64}$'),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    cik TEXT NOT NULL CHECK (cik ~ '^[0-9]{10}$'),
    sic TEXT NOT NULL CHECK (sic ~ '^[0-9]{4}$'),
    division TEXT NOT NULL CHECK (division IN (
        'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'SECTOR_UNKNOWN'
    )),
    source_record_id TEXT NOT NULL CHECK (source_record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'),
    source_record_hash TEXT NOT NULL CHECK (source_record_hash ~ '^[0-9a-f]{64}$'),
    source_family TEXT NOT NULL CHECK (source_family = 'SEC_EDGAR'),
    accession TEXT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    taxonomy_version TEXT NOT NULL CHECK (taxonomy_version = 'sec-sic-division-v1'),
    taxonomy_hash TEXT NOT NULL CHECK (taxonomy_hash ~ '^[0-9a-f]{64}$'),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (security_id, available_at),
    FOREIGN KEY (source_record_id, source_record_hash, source_family)
        REFERENCES public.p4_source_records(record_id, record_hash, family)
);

CREATE INDEX sector_assignments_security_idx
    ON public.sector_assignments (security_id, available_at DESC);

-- ****************************************************************
-- Candidate sets: quant → evidence → focus with ordered entries.
-- ****************************************************************
CREATE TABLE public.candidate_sets (
    candidate_hash TEXT PRIMARY KEY CHECK (candidate_hash ~ '^[0-9a-f]{64}$'),
    as_of TIMESTAMPTZ NOT NULL,
    known_at TIMESTAMPTZ NOT NULL,
    factor_manifest_hash TEXT NOT NULL CHECK (factor_manifest_hash ~ '^[0-9a-f]{64}$'),
    cluster_manifest_hash TEXT NOT NULL CHECK (cluster_manifest_hash ~ '^[0-9a-f]{64}$'),
    universe_hash TEXT NOT NULL CHECK (universe_hash ~ '^[0-9a-f]{64}$')
        REFERENCES public.universe_snapshots(universe_hash),
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    -- Single authoritative candidate set per as-of/window.
    UNIQUE (as_of)
);

CREATE INDEX candidate_sets_as_of_idx
    ON public.candidate_sets (as_of DESC);

CREATE TABLE public.candidate_set_entries (
    candidate_hash TEXT NOT NULL REFERENCES public.candidate_sets(candidate_hash),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    stage TEXT NOT NULL CHECK (stage IN ('QUANT', 'EVIDENCE', 'FOCUS_OPEN', 'FOCUS_CLOSE')),
    composite TEXT NOT NULL,
    feature_hash TEXT NOT NULL CHECK (feature_hash ~ '^[0-9a-f]{64}$')
        REFERENCES public.feature_vectors(feature_hash),
    universe_hash TEXT NOT NULL CHECK (universe_hash ~ '^[0-9a-f]{64}$')
        REFERENCES public.universe_snapshots(universe_hash),
    quarantine_decision_hash TEXT NOT NULL CHECK (quarantine_decision_hash ~ '^[0-9a-f]{64}$')
        REFERENCES public.security_quarantine_decisions(decision_hash),
    sector_assignment_hash TEXT NULL CHECK (
        sector_assignment_hash IS NULL OR sector_assignment_hash ~ '^[0-9a-f]{64}$'
    ) REFERENCES public.sector_assignments(assignment_hash),
    evidence_source_refs JSONB NOT NULL CHECK (jsonb_typeof(evidence_source_refs) = 'array'),
    PRIMARY KEY (candidate_hash, ordinal, stage)
);

-- ****************************************************************
-- Cluster results per as-of.
-- ****************************************************************
CREATE TABLE public.cluster_results (
    cluster_id TEXT NOT NULL CHECK (cluster_id ~ '^[0-9a-f]{64}$'),
    as_of TIMESTAMPTZ NOT NULL,
    security_id TEXT NOT NULL CHECK (security_id ~ '^[0-9a-f][0-9a-f-]{7,63}$'),
    status TEXT NOT NULL CHECK (status IN ('ASSIGNED', 'UNKNOWN')),
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    manifest_hash TEXT NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    wire JSONB NOT NULL CHECK (jsonb_typeof(wire) = 'object'),
    appended_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (cluster_id, security_id),
    UNIQUE (as_of, security_id),
    UNIQUE (cluster_id, ordinal)
);

-- ****************************************************************
-- SECURITY DEFINER functions: narrow write authority.
-- ****************************************************************

-- Resource bounds are enforced again at the database authority boundary.  The
-- application constructors use these same canonical UTF-8 byte limits, but a
-- caller can invoke a public append function without using those constructors.
-- Measure the canonical representation (rather than jsonb::text) so key
-- ordering/whitespace differences cannot reject a valid producer wire.
-- Market 1 MiB; universe 16 MiB; feature 1 MiB; candidate 4 MiB; sector
-- 64 KiB; cluster 1 MiB.  Array item bounds are checked beside each shape.

CREATE OR REPLACE FUNCTION public.append_market_snapshot(
    p_snapshot_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing_wire JSONB;
    v_bar_count INTEGER;
    v_expected_adv NUMERIC;
BEGIN
    IF p_snapshot_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'snapshot hash must be a SHA-256 digest'
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
        RAISE EXCEPTION 'market snapshot canonical wire exceeds the 1048576-byte limit'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'wire form must be a JSON object'
            USING ERRCODE = '22023';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 27
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS k(key_name)
            WHERE k.key_name <> ALL (ARRAY[
                'security_id', 'symbol', 'as_of', 'known_at', 'observed_at',
                'received_at', 'feed', 'entitlement', 'bid', 'ask', 'mid',
                'spread_bps', 'last', 'adv20_usd', 'bar_feed', 'bar_refs',
                'bar_dates', 'sessions', 'split_adjustment_refs', 'split_adjustments',
                'quote_source_ref', 'coverage', 'freshness', 'coverage_warning',
                'reasons', 'producer_version', 'schema_version'
            ]::text[])
        )
        OR jsonb_typeof(p_wire->'security_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'symbol') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'as_of') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'known_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'observed_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'received_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'feed') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'entitlement') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'bid') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'ask') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'mid') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'spread_bps') IS DISTINCT FROM 'number'
        OR jsonb_typeof(p_wire->'last') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'adv20_usd') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'bar_feed') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'bar_refs') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'bar_dates') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'sessions') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'split_adjustment_refs') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'split_adjustments') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'quote_source_ref') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_wire->'coverage') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'freshness') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'coverage_warning') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'reasons') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'producer_version') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'schema_version') IS DISTINCT FROM 'string'
        OR jsonb_array_length(p_wire->'sessions') > 1024
        OR jsonb_array_length(p_wire->'bar_refs') > 1024
        OR jsonb_array_length(p_wire->'bar_dates') > 1024
        OR jsonb_array_length(p_wire->'split_adjustment_refs') > 64
        OR jsonb_array_length(p_wire->'split_adjustments') > 64
    THEN
        RAISE EXCEPTION 'market snapshot wire shape is outside the P4-C contract'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'security_id' !~ '^[0-9a-f][0-9a-f-]{7,63}$'
        OR p_wire->>'symbol' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
        OR p_wire->>'feed' <> 'iex'
        OR p_wire->>'entitlement' <> 'iex'
        OR p_wire->>'coverage' <> 'LIMITED_MARKET_COVERAGE'
        OR p_wire->>'coverage_warning' <> 'IEX limited market coverage'
        OR p_wire->>'bar_feed' IS NOT NULL AND p_wire->>'bar_feed' <> 'sip_delayed'
        OR p_wire->>'freshness' NOT IN ('FRESH', 'STALE')
        OR p_wire->>'producer_version' <> 'p4c.market.v1'
        OR p_wire->>'schema_version' <> '1.0.0'
        OR p_wire->>'as_of' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'known_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'observed_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'received_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'bid' !~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
        OR p_wire->>'ask' !~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
        OR p_wire->>'mid' !~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
        OR p_wire->>'spread_bps' !~ '^[0-9]+$'
        OR p_wire->>'last' IS NULL
        OR p_wire->>'last' IS NOT NULL AND p_wire->>'last' !~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
        OR p_wire->>'adv20_usd' IS NOT NULL AND p_wire->>'adv20_usd' !~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
        OR (p_wire->>'bid')::numeric <= 0
        OR (p_wire->>'ask')::numeric <= 0
        OR (p_wire->>'mid')::numeric <= 0
        OR p_wire->>'last' IS NOT NULL AND (p_wire->>'last')::numeric <= 0
        OR p_wire->>'adv20_usd' IS NOT NULL AND (p_wire->>'adv20_usd')::numeric <= 0
        OR (p_wire->>'bid')::numeric > (p_wire->>'ask')::numeric
        -- Numeric addition/multiplication are exact; only division rounds.
        -- mid/last/spread_bps are therefore verified by cross-multiplied
        -- exact identities so no floor/rounding boundary can drift:
        --   mid = (bid+ask)/2  <=>  mid*2 = bid+ask
        --   last = mid         <=>  last*2 = bid+ask (with mid*2 = bid+ask)
        --   k = floor((ask-bid)*20000/(bid+ask))
        --                      <=>  k*(bid+ask) <= (ask-bid)*20000
        --                            AND (ask-bid)*20000 < (k+1)*(bid+ask)
        OR (p_wire->>'mid')::numeric * 2
            <> (p_wire->>'bid')::numeric + (p_wire->>'ask')::numeric
        OR (p_wire->>'last')::numeric * 2
            <> (p_wire->>'bid')::numeric + (p_wire->>'ask')::numeric
        OR NOT (
            (p_wire->>'spread_bps')::numeric
                * ((p_wire->>'bid')::numeric + (p_wire->>'ask')::numeric)
                <= ((p_wire->>'ask')::numeric - (p_wire->>'bid')::numeric) * 20000
            AND ((p_wire->>'ask')::numeric - (p_wire->>'bid')::numeric) * 20000
                < ((p_wire->>'spread_bps')::numeric + 1)
                    * ((p_wire->>'bid')::numeric + (p_wire->>'ask')::numeric)
        )
    THEN
        RAISE EXCEPTION 'market snapshot scalar values are invalid or forged'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'object'
            OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(item.value)) <> 4
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_object_keys(item.value) AS k(key_name)
                WHERE k.key_name <> ALL (ARRAY[
                    'trading_date', 'day_kind', 'opens_at', 'closes_at'
                ]::text[])
            )
            OR jsonb_typeof(item.value->'trading_date') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'day_kind') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'opens_at') NOT IN ('string', 'null')
            OR jsonb_typeof(item.value->'closes_at') NOT IN ('string', 'null')
            OR item.value->>'trading_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            OR item.value->>'day_kind' NOT IN ('REGULAR', 'HALF_DAY', 'CLOSED')
            OR (
                item.value->>'day_kind' = 'CLOSED'
                AND (
                    jsonb_typeof(item.value->'opens_at') IS DISTINCT FROM 'null'
                    OR jsonb_typeof(item.value->'closes_at') IS DISTINCT FROM 'null'
                )
            )
            OR (
                item.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
                AND (
                    jsonb_typeof(item.value->'opens_at') IS DISTINCT FROM 'string'
                    OR jsonb_typeof(item.value->'closes_at') IS DISTINCT FROM 'string'
                    OR item.value->>'opens_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
                    OR item.value->>'closes_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
                )
            )
    )
    THEN
        RAISE EXCEPTION 'market sessions have an invalid shape'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
        WHERE (item.value->>'trading_date')::date IS NULL
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
            GROUP BY item.value->>'trading_date'
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(item.value->>'trading_date' ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') WITH ORDINALITY AS item(value, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.value->>'trading_date' ORDER BY item.value->>'trading_date')
            FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
            WHERE (
                EXTRACT(ISODOW FROM (item.value->>'trading_date')::date) >= 6
                AND item.value->>'day_kind' <> 'CLOSED'
            )
                OR (
                    item.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
                    AND (item.value->>'opens_at')::timestamptz
                        >= (item.value->>'closes_at')::timestamptz
                )
        )
    THEN
        RAISE EXCEPTION 'market sessions are not canonical or contain an open weekend'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
        WHERE item.value->>'trading_date' = (p_wire->>'as_of')::timestamptz::date::text
          AND item.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
          AND (item.value->>'opens_at')::timestamptz <= (p_wire->>'as_of')::timestamptz
          AND (p_wire->>'as_of')::timestamptz < (item.value->>'closes_at')::timestamptz
    )
        OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
            WHERE item.value->>'trading_date' = (p_wire->>'observed_at')::timestamptz::date::text
              AND item.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
              AND (item.value->>'opens_at')::timestamptz <= (p_wire->>'observed_at')::timestamptz
              AND (p_wire->>'observed_at')::timestamptz < (item.value->>'closes_at')::timestamptz
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates') AS bar(trading_date)
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
                WHERE item.value->>'trading_date' = bar.trading_date
                  AND item.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
            )
        )
    THEN
        RAISE EXCEPTION 'market timestamps and bar dates must bind to open exchange sessions'
            USING ERRCODE = '23514';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire->'quote_source_ref')) <> 3
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire->'quote_source_ref') AS k(key_name)
            WHERE k.key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
        )
        OR p_wire->'quote_source_ref'->>'family' <> 'ALPACA_IEX_QUOTES'
        OR jsonb_typeof(p_wire->'quote_source_ref'->'record_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'quote_source_ref'->'family') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'quote_source_ref'->'record_hash') IS DISTINCT FROM 'string'
        OR p_wire->'quote_source_ref'->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
        OR p_wire->'quote_source_ref'->>'record_hash' !~ '^[0-9a-f]{64}$'
    THEN
        RAISE EXCEPTION 'quote source reference is outside the IEX authority'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'bar_refs') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'object'
            OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(item.value)) <> 3
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_object_keys(item.value) AS k(key_name)
                WHERE k.key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
            )
            OR jsonb_typeof(item.value->'record_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'family') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'record_hash') IS DISTINCT FROM 'string'
            OR item.value->>'family' <> 'ALPACA_HISTORICAL_BARS'
            OR item.value->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
            OR item.value->>'record_hash' !~ '^[0-9a-f]{64}$'
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'bar_refs') AS item(value)
            GROUP BY item.value->>'record_id'
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(item.value->>'record_id' ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements(p_wire->'bar_refs') WITH ORDINALITY AS item(value, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.value->>'record_id' ORDER BY item.value->>'record_id')
            FROM pg_catalog.jsonb_array_elements(p_wire->'bar_refs') AS item(value)
        )
    THEN
        RAISE EXCEPTION 'bar source references are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates') WITH ORDINALITY AS item(trading_date, ordinality)
        WHERE item.trading_date !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates') AS item(trading_date)
            GROUP BY item.trading_date
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(item.trading_date ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates') WITH ORDINALITY AS item(trading_date, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.trading_date ORDER BY item.trading_date)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates') AS item(trading_date)
        )
        OR (jsonb_array_length(p_wire->'bar_refs') = 0) <> (jsonb_array_length(p_wire->'bar_dates') = 0)
    THEN
        RAISE EXCEPTION 'bar dates are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates') AS item(trading_date)
        WHERE item.trading_date::date >= (p_wire->>'as_of')::timestamptz::date
    )
    THEN
        RAISE EXCEPTION 'bar dates must precede the market snapshot as-of date'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustment_refs') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'object'
            OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(item.value)) <> 3
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_object_keys(item.value) AS k(key_name)
                WHERE k.key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
            )
            OR jsonb_typeof(item.value->'record_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'family') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'record_hash') IS DISTINCT FROM 'string'
            OR item.value->>'family' <> 'ALPACA_CORPORATE_ACTIONS'
            OR item.value->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
            OR item.value->>'record_hash' !~ '^[0-9a-f]{64}$'
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustment_refs') AS item(value)
            GROUP BY item.value->>'record_id'
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(item.value->>'record_id' ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustment_refs') WITH ORDINALITY AS item(value, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.value->>'record_id' ORDER BY item.value->>'record_id')
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustment_refs') AS item(value)
        )
    THEN
        RAISE EXCEPTION 'split-adjustment source references are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'object'
            OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(item.value)) <> 13
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_object_keys(item.value) AS k(key_name)
                WHERE k.key_name <> ALL (ARRAY[
                    'security_id', 'ex_date', 'numerator', 'denominator',
                    'event_id', 'event_record_hash', 'security_identity_hash',
                    'action_type', 'effective_date',
                    'source_ref', 'source_refs', 'available_at', 'confirmed'
                ]::text[])
            )
            OR jsonb_typeof(item.value->'security_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'ex_date') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'numerator') IS DISTINCT FROM 'number'
            OR jsonb_typeof(item.value->'denominator') IS DISTINCT FROM 'number'
            OR jsonb_typeof(item.value->'event_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'event_record_hash') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'security_identity_hash') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'action_type') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'effective_date') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'source_ref') IS DISTINCT FROM 'object'
            OR jsonb_typeof(item.value->'source_refs') IS DISTINCT FROM 'array'
            OR jsonb_typeof(item.value->'available_at') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'confirmed') IS DISTINCT FROM 'boolean'
            OR item.value->>'security_id' !~ '^[0-9a-f][0-9a-f-]{7,63}$'
            OR item.value->>'ex_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            OR item.value->>'numerator' !~ '^[0-9]+$'
            OR item.value->>'denominator' !~ '^[0-9]+$'
            OR (item.value->>'numerator')::bigint <= 0
            OR (item.value->>'denominator')::bigint <= 0
            OR item.value->>'event_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
            OR item.value->>'event_record_hash' !~ '^[0-9a-f]{64}$'
            OR item.value->>'security_identity_hash' !~ '^[0-9a-f]{64}$'
            OR item.value->>'action_type' NOT IN ('forward_split', 'reverse_split')
            OR item.value->>'effective_date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            OR (item.value->>'effective_date')::date < (item.value->>'ex_date')::date
            OR jsonb_array_length(item.value->'source_refs') = 0
            OR jsonb_array_length(item.value->'source_refs') > 64
            OR item.value->>'available_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
            OR (item.value->>'confirmed')::boolean IS NOT TRUE
            OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(item.value->'source_ref')) <> 3
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_object_keys(item.value->'source_ref') AS k(key_name)
                WHERE k.key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
            )
            OR jsonb_typeof(item.value->'source_ref'->'record_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'source_ref'->'family') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item.value->'source_ref'->'record_hash') IS DISTINCT FROM 'string'
            OR item.value->'source_ref'->>'family' <> 'ALPACA_CORPORATE_ACTIONS'
            OR item.value->'source_ref'->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
            OR item.value->'source_ref'->>'record_hash' !~ '^[0-9a-f]{64}$'
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(item.value->'source_refs') AS full_ref(value)
                WHERE jsonb_typeof(full_ref.value) IS DISTINCT FROM 'object'
                   OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(full_ref.value)) <> 3
                   OR EXISTS (
                        SELECT 1
                        FROM pg_catalog.jsonb_object_keys(full_ref.value) AS k(key_name)
                        WHERE k.key_name <> ALL (
                            ARRAY['record_id', 'family', 'record_hash']::text[]
                        )
                   )
                   OR jsonb_typeof(full_ref.value->'record_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(full_ref.value->'family') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(full_ref.value->'record_hash') IS DISTINCT FROM 'string'
                   OR full_ref.value->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
                   OR full_ref.value->>'record_hash' !~ '^[0-9a-f]{64}$'
            )
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(item.value->'source_refs') AS full_ref(value)
                GROUP BY full_ref.value->>'record_id'
                HAVING count(*) > 1
            )
            OR NOT (item.value->'source_refs' @> jsonb_build_array(item.value->'source_ref'))
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments') AS item(value)
            WHERE item.value->>'security_id' <> p_wire->>'security_id'
                OR (item.value->>'available_at')::timestamptz > (p_wire->>'known_at')::timestamptz
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments') AS item(value)
            GROUP BY item.value->>'ex_date'
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(item.value->>'ex_date' ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments')
                WITH ORDINALITY AS item(value, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.value->>'ex_date' ORDER BY item.value->>'ex_date')
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments') AS item(value)
        )
        OR jsonb_array_length(p_wire->'split_adjustments') <> jsonb_array_length(p_wire->'split_adjustment_refs')
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments') AS item(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustment_refs') AS ref(value)
                WHERE ref.value->>'record_id' = item.value->'source_ref'->>'record_id'
                  AND ref.value->>'family' = item.value->'source_ref'->>'family'
                  AND ref.value->>'record_hash' = item.value->'source_ref'->>'record_hash'
            )
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustment_refs') AS ref(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments') AS item(value)
                WHERE item.value->'source_ref'->>'record_id' = ref.value->>'record_id'
                  AND item.value->'source_ref'->>'family' = ref.value->>'family'
                  AND item.value->'source_ref'->>'record_hash' = ref.value->>'record_hash'
            )
        )
    THEN
        RAISE EXCEPTION 'split-adjustment details are invalid or not bound to their references'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustments') AS item(value)
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.corporate_action_event_head AS event_head
            JOIN public.corporate_action_events AS event_row
              ON event_row.record_hash = event_head.record_hash
            WHERE event_head.event_id = item.value->>'event_id'
              AND event_head.record_hash = item.value->>'event_record_hash'
              AND event_head.security_id = p_wire->>'security_id'
              AND event_head.state = 'confirmed'
              AND event_row.event_id = item.value->>'event_id'
              AND event_row.record_hash = item.value->>'event_record_hash'
              AND event_row.security_id = p_wire->>'security_id'
              AND event_row.security_identity_hash = item.value->>'security_identity_hash'
              AND event_row.action_type = item.value->>'action_type'
              AND event_row.ex_date = (item.value->>'ex_date')::date
              AND event_row.effective_date = (item.value->>'effective_date')::date
              AND event_row.ratio_numerator = (item.value->>'numerator')::bigint
              AND event_row.ratio_denominator = (item.value->>'denominator')::bigint
              -- P4-C consumes only the immutable confirmed head.  An
              -- effective_pending_reconciliation event is operationally
              -- unresolved and must never authorize historical adjustment.
              AND event_row.state = 'confirmed'
              AND event_row.available_at = (item.value->>'available_at')::timestamptz
              AND event_row.wire->'source_refs' = item.value->'source_refs'
              AND event_row.available_at <= (p_wire->>'known_at')::timestamptz
        )
    )
    THEN
        RAISE EXCEPTION 'split-adjustment details require a visible confirmed P4-B event lineage'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.p4_source_records AS source_record
        WHERE source_record.record_id = p_wire->'quote_source_ref'->>'record_id'
          AND source_record.record_hash = p_wire->'quote_source_ref'->>'record_hash'
          AND source_record.family = p_wire->'quote_source_ref'->>'family'
          AND COALESCE(
                (source_record.wire->>'available_at')::timestamptz,
                source_record.retrieved_at
              ) <= (p_wire->>'known_at')::timestamptz
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'bar_refs') AS item(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM public.p4_source_records AS source_record
                WHERE source_record.record_id = item.value->>'record_id'
                  AND source_record.record_hash = item.value->>'record_hash'
                  AND source_record.family = item.value->>'family'
                  AND COALESCE(
                        (source_record.wire->>'available_at')::timestamptz,
                        source_record.retrieved_at
                      ) <= (p_wire->>'known_at')::timestamptz
            )
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'split_adjustment_refs') AS item(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM public.p4_source_records AS source_record
                WHERE source_record.record_id = item.value->>'record_id'
                  AND source_record.record_hash = item.value->>'record_hash'
                  AND source_record.family = item.value->>'family'
                  AND COALESCE(
                        (source_record.wire->>'available_at')::timestamptz,
                        source_record.retrieved_at
                      ) <= (p_wire->>'known_at')::timestamptz
            )
        )
    THEN
        RAISE EXCEPTION 'market snapshot source lineage is not present in the P4-A record log'
            USING ERRCODE = '23514';
    END IF;
    -- A symbol-only provider record is safe only when the point-in-time
    -- security master resolves that symbol to exactly one active identity.
    -- Without this closure, a valid bar/quote ref for a reused symbol could
    -- be attached to another security_id while retaining a valid snapshot
    -- hash.
    IF (
        SELECT count(*)
        FROM public.security_identity_heads AS identity_head
        JOIN public.security_identities AS identity_record
          ON identity_record.identity_hash = identity_head.identity_hash
        WHERE identity_record.symbol = p_wire->>'symbol'
          AND identity_record.status = 'active'
          AND identity_record.available_at <= (p_wire->>'known_at')::timestamptz
          AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
          AND (
                identity_record.valid_to IS NULL
                OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
          )
    ) <> 1
        OR NOT EXISTS (
            SELECT 1
            FROM public.security_identity_heads AS identity_head
            JOIN public.security_identities AS identity_record
              ON identity_record.identity_hash = identity_head.identity_hash
            WHERE identity_record.security_id = p_wire->>'security_id'
              AND identity_record.symbol = p_wire->>'symbol'
              AND identity_record.status = 'active'
              AND identity_record.available_at <= (p_wire->>'known_at')::timestamptz
              AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
              AND (
                    identity_record.valid_to IS NULL
                    OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
              )
        )
    THEN
        RAISE EXCEPTION 'market snapshot security identity is not uniquely point-in-time bound'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.p4_source_records AS source_record
        WHERE source_record.record_id = p_wire->'quote_source_ref'->>'record_id'
          AND source_record.record_hash = p_wire->'quote_source_ref'->>'record_hash'
          AND source_record.family = 'ALPACA_IEX_QUOTES'
          AND source_record.wire->>'endpoint_id' = 'latest_quote'
          AND source_record.wire->>'observation_at' = p_wire->>'observed_at'
          AND source_record.wire->'payload'->>'symbol' = p_wire->>'symbol'
          AND source_record.wire->'payload'->>'feed' = p_wire->>'feed'
          AND source_record.wire->'payload'->>'bid_price' ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
          AND source_record.wire->'payload'->>'ask_price' ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
          AND (
              CASE
                  WHEN source_record.wire->'payload'->>'bid_price'
                      ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
                  THEN (source_record.wire->'payload'->>'bid_price')::numeric
                  ELSE NULL::numeric
              END
          ) = (p_wire->>'bid')::numeric
          AND (
              CASE
                  WHEN source_record.wire->'payload'->>'ask_price'
                      ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
                  THEN (source_record.wire->'payload'->>'ask_price')::numeric
                  ELSE NULL::numeric
              END
          ) = (p_wire->>'ask')::numeric
          AND (
              CASE
                  WHEN source_record.wire->'payload'->>'timestamp'
                      ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?Z$'
                  THEN (source_record.wire->'payload'->>'timestamp')::timestamptz
                  WHEN source_record.wire->'payload'->>'timestamp'
                      ~ '^[0-9]{8}T[0-9]{6}Z$'
                  THEN to_timestamp(
                      source_record.wire->'payload'->>'timestamp', 'YYYYMMDD"T"HH24MISS"Z"'
                  )
                  WHEN source_record.wire->'payload'->>'timestamp' ~ '^[0-9]{14}$'
                  THEN to_timestamp(source_record.wire->'payload'->>'timestamp', 'YYYYMMDDHH24MISS')
                  ELSE NULL::timestamptz
              END
          ) = (p_wire->>'observed_at')::timestamptz
    )
    THEN
        RAISE EXCEPTION 'market quote values are not bound to the P4-A quote payload'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        WITH source_bars AS (
            SELECT
                CASE
                    WHEN bar.value->>'t'
                        ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?Z$'
                    THEN (bar.value->>'t')::timestamptz
                    WHEN bar.value->>'t' ~ '^[0-9]{8}T[0-9]{6}Z$'
                    THEN to_timestamp(bar.value->>'t', 'YYYYMMDD"T"HH24MISS"Z"')
                    WHEN bar.value->>'t' ~ '^[0-9]{14}$'
                    THEN to_timestamp(bar.value->>'t', 'YYYYMMDDHH24MISS')
                    ELSE NULL::timestamptz
                END::date AS trading_date,
                CASE
                    WHEN bar.value->>'c' ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
                    THEN (bar.value->>'c')::numeric
                    ELSE NULL::numeric
                END AS close_value,
                CASE
                    WHEN bar.value->>'v' ~ '^[0-9]+$'
                    THEN (bar.value->>'v')::numeric
                    ELSE NULL::numeric
                END AS volume_value
            FROM pg_catalog.jsonb_array_elements(p_wire->'bar_refs') AS ref(value)
            JOIN public.p4_source_records AS source_record
              ON source_record.record_id = ref.value->>'record_id'
             AND source_record.record_hash = ref.value->>'record_hash'
             AND source_record.family = ref.value->>'family'
            CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(
                source_record.wire->'payload'->'bars'
            ) AS bar(value)
            WHERE source_record.wire->>'endpoint_id' = 'stock_bars'
              AND source_record.wire->'payload'->>'symbol' = p_wire->>'symbol'
              AND source_record.wire->'payload'->>'feed' = 'sip'
              AND source_record.wire->'payload'->>'timeframe' = '1Day'
              AND p_wire->>'bar_feed' = 'sip_delayed'
              AND COALESCE(
                    (source_record.wire->>'available_at')::timestamptz,
                    source_record.retrieved_at
                  ) <= (p_wire->>'known_at')::timestamptz
        )
        SELECT requested.trading_date
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates') AS requested(trading_date)
        LEFT JOIN source_bars AS source_bar
          ON source_bar.trading_date = requested.trading_date::date
         AND source_bar.close_value IS NOT NULL
         AND source_bar.volume_value IS NOT NULL
        GROUP BY requested.trading_date
        HAVING count(source_bar.trading_date) <> 1
    )
    THEN
        RAISE EXCEPTION 'market bar dates are not bound one-to-one to the P4-A bar payload'
            USING ERRCODE = '23514';
    END IF;
    WITH source_bars AS (
        SELECT
            CASE
                WHEN bar.value->>'t'
                    ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?Z$'
                THEN (bar.value->>'t')::timestamptz
                WHEN bar.value->>'t' ~ '^[0-9]{8}T[0-9]{6}Z$'
                THEN to_timestamp(bar.value->>'t', 'YYYYMMDD"T"HH24MISS"Z"')
                WHEN bar.value->>'t' ~ '^[0-9]{14}$'
                THEN to_timestamp(bar.value->>'t', 'YYYYMMDDHH24MISS')
                ELSE NULL::timestamptz
            END::date AS trading_date,
            CASE
                WHEN bar.value->>'c' ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'
                THEN (bar.value->>'c')::numeric
                ELSE NULL::numeric
            END AS close_value,
            CASE
                WHEN bar.value->>'v' ~ '^[0-9]+$'
                THEN (bar.value->>'v')::numeric
                ELSE NULL::numeric
            END AS volume_value
        FROM pg_catalog.jsonb_array_elements(p_wire->'bar_refs') AS ref(value)
        JOIN public.p4_source_records AS source_record
          ON source_record.record_id = ref.value->>'record_id'
         AND source_record.record_hash = ref.value->>'record_hash'
         AND source_record.family = ref.value->>'family'
        CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(
            source_record.wire->'payload'->'bars'
        ) AS bar(value)
        WHERE source_record.wire->>'endpoint_id' = 'stock_bars'
          AND source_record.wire->'payload'->>'symbol' = p_wire->>'symbol'
          AND source_record.wire->'payload'->>'feed' = 'sip'
          AND source_record.wire->'payload'->>'timeframe' = '1Day'
          AND p_wire->>'bar_feed' = 'sip_delayed'
          AND COALESCE(
                (source_record.wire->>'available_at')::timestamptz,
                source_record.retrieved_at
              ) <= (p_wire->>'known_at')::timestamptz
    ), requested_dates AS (
        SELECT requested.trading_date::date AS trading_date
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'bar_dates')
            AS requested(trading_date)
    ), expected_sessions AS (
        SELECT (item.value->>'trading_date')::date AS trading_date
        FROM pg_catalog.jsonb_array_elements(p_wire->'sessions') AS item(value)
        WHERE item.value->>'day_kind' IN ('REGULAR', 'HALF_DAY')
          AND (item.value->>'trading_date')::date < (p_wire->>'as_of')::timestamptz::date
        ORDER BY (item.value->>'trading_date')::date DESC
        LIMIT 20
    ), latest_bars AS (
        SELECT source_bar.close_value * source_bar.volume_value AS dollar_volume
        FROM source_bars AS source_bar
        JOIN requested_dates AS requested
          ON requested.trading_date = source_bar.trading_date
        JOIN expected_sessions AS expected
          ON expected.trading_date = source_bar.trading_date
        AND source_bar.close_value IS NOT NULL
        AND source_bar.volume_value IS NOT NULL
    )
    SELECT count(*)::integer, avg(dollar_volume)
      INTO v_bar_count, v_expected_adv
      FROM latest_bars;
    IF (
        v_bar_count >= 20
        AND (
            p_wire->>'adv20_usd' IS NULL
            OR (p_wire->>'adv20_usd')::numeric <> v_expected_adv
        )
    )
        OR (v_bar_count < 20 AND p_wire->>'adv20_usd' IS NOT NULL)
        OR (
            (v_bar_count < 20)
            <> (p_wire->'reasons' @> '["ADV_BELOW_MINIMUM"]'::jsonb)
        )
    THEN
        RAISE EXCEPTION 'market ADV is not derived from the visible P4-A bar payload'
            USING ERRCODE = '23514';
    END IF;
    IF (p_wire->>'known_at')::timestamptz > (p_wire->>'as_of')::timestamptz
        OR (p_wire->>'observed_at')::timestamptz > (p_wire->>'known_at')::timestamptz
        OR (p_wire->>'received_at')::timestamptz > (p_wire->>'known_at')::timestamptz
        OR (p_wire->>'observed_at')::timestamptz > (p_wire->>'as_of')::timestamptz
        OR (p_wire->>'received_at')::timestamptz > (p_wire->>'as_of')::timestamptz
        OR (p_wire->>'received_at')::timestamptz < (p_wire->>'observed_at')::timestamptz
        OR (
            p_wire->>'freshness' = 'FRESH'
            AND (p_wire->>'as_of')::timestamptz - (p_wire->>'observed_at')::timestamptz
                > INTERVAL '5 seconds'
        )
        OR (
            p_wire->>'freshness' = 'STALE'
            AND (p_wire->>'as_of')::timestamptz - (p_wire->>'observed_at')::timestamptz
                <= INTERVAL '5 seconds'
        )
        OR (
            p_wire->>'freshness' = 'FRESH'
            AND p_wire->'reasons' @> '["QUOTE_MISSING_OR_STALE"]'::jsonb
        )
        OR (
            p_wire->>'freshness' = 'STALE'
            AND NOT (p_wire->'reasons' @> '["QUOTE_MISSING_OR_STALE"]'::jsonb)
        )
        OR (
            ((p_wire->>'ask')::numeric - (p_wire->>'bid')::numeric) * 10000
            / ((p_wire->>'bid')::numeric + (p_wire->>'ask')::numeric) * 2
                > 30
            AND NOT (p_wire->'reasons' @> '["SPREAD_TOO_WIDE"]'::jsonb)
        )
        OR (
            ((p_wire->>'ask')::numeric - (p_wire->>'bid')::numeric) * 10000
            / ((p_wire->>'bid')::numeric + (p_wire->>'ask')::numeric) * 2
                <= 30
            AND p_wire->'reasons' @> '["SPREAD_TOO_WIDE"]'::jsonb
        )
    THEN
        RAISE EXCEPTION 'market snapshot timestamps are not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'reasons') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
    )
        OR EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'reasons') WITH ORDINALITY AS item(reason, ordinality)
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
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'reasons') AS item(reason)
            GROUP BY item.reason
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(item.reason ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'reasons') WITH ORDINALITY AS item(reason, ordinality)
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
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'reasons') AS item(reason)
        )
    THEN
        RAISE EXCEPTION 'market snapshot reasons are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4c.market-snapshot.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_snapshot_hash
    THEN
        RAISE EXCEPTION 'snapshot hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    SELECT s.wire INTO v_existing_wire
    FROM public.market_snapshots AS s
    WHERE s.snapshot_hash = p_snapshot_hash;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'snapshot hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;

    -- Single authority per (security_id, as_of): a different hash for the
    -- same as-of instant is a conflict, never an overwrite.
    IF EXISTS (
        SELECT 1
        FROM public.market_snapshots AS s
        WHERE s.security_id = p_wire->>'security_id'
          AND s.as_of = (p_wire->>'as_of')::timestamptz
          AND s.snapshot_hash IS DISTINCT FROM p_snapshot_hash
    ) THEN
        RAISE EXCEPTION 'market snapshot conflicts with the existing as-of authority'
            USING ERRCODE = '23505';
    END IF;

    INSERT INTO public.market_snapshots (
        snapshot_hash, security_id, symbol, as_of, known_at, observed_at, received_at,
        feed, coverage, freshness, wire
    ) VALUES (
        p_snapshot_hash,
        p_wire->>'security_id',
        p_wire->>'symbol',
        (p_wire->>'as_of')::timestamptz,
        (p_wire->>'known_at')::timestamptz,
        (p_wire->>'observed_at')::timestamptz,
        (p_wire->>'received_at')::timestamptz,
        p_wire->>'feed',
        p_wire->>'coverage',
        p_wire->>'freshness',
        p_wire
    ) ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        SELECT s.wire INTO v_existing_wire
        FROM public.market_snapshots AS s
        WHERE s.snapshot_hash = p_snapshot_hash;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'market snapshot conflict could not be resolved'
                USING ERRCODE = '23505';
        END IF;
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'snapshot hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;
    RETURN 'APPENDED';
END;
$$;

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
    IF (p_wire->>'known_at')::timestamptz::date > (p_wire->>'as_of')::date
        OR EXTRACT(DAY FROM (p_wire->>'as_of')::date) <> 1
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
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'market_snapshot_refs') AS item(ref)
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.market_snapshots AS market
            WHERE market.snapshot_hash = item.ref
              AND market.as_of::date = (p_wire->>'as_of')::date
              AND market.known_at <= (p_wire->>'known_at')::timestamptz
        )
    )
    THEN
        RAISE EXCEPTION 'universe market snapshot lineage is not present or not point-in-time visible'
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
                  AND market.as_of::date = (p_wire->>'as_of')::date
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
                    FROM pg_catalog.jsonb_array_elements(item.value->'source_refs') WITH ORDINALITY AS ref(value, ordinality)
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
                JOIN public.market_snapshots AS market
                  ON market.snapshot_hash = universe_entry.market_snapshot_hash
                CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(market.wire->'sessions')
                    AS session(value)
                WHERE universe_entry.universe_hash = p_wire->>'universe_hash'
                  AND universe_entry.security_id = p_wire->>'security_id'
                  AND universe_entry.symbol = p_wire->>'symbol'
                  AND universe_entry.eligible
                  AND universe.known_at <= (p_wire->>'known_at')::timestamptz
                  AND market.security_id = p_wire->>'security_id'
                  AND market.symbol = p_wire->>'symbol'
                  AND market.as_of::date = (p_wire->>'as_of')::timestamptz::date
                  AND market.known_at <= (p_wire->>'known_at')::timestamptz
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
    IF NOT EXISTS (
        SELECT 1
        FROM public.universe_snapshots AS universe
        WHERE universe.universe_hash = p_wire->>'universe_hash'
          AND universe.as_of = (p_wire->>'as_of')::timestamptz::date
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

CREATE OR REPLACE FUNCTION public.append_sector_assignment(
    p_assignment_hash TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing_wire JSONB;
    v_available_at TIMESTAMPTZ;
    v_sic_number INTEGER;
    v_expected_division TEXT;
BEGIN
    IF p_assignment_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'sector assignment hash must be a SHA-256 digest'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire IS NULL
        OR COALESCE(
            pg_catalog.octet_length(
                pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8')
            ),
            0
        ) > 65536
    THEN
        RAISE EXCEPTION 'sector assignment canonical wire exceeds the 65536-byte limit'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 9
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS k(key_name)
            WHERE k.key_name <> ALL (ARRAY[
                'security_id', 'cik', 'sic', 'division', 'source_ref', 'accession',
                'available_at', 'taxonomy_version', 'taxonomy_hash'
            ]::text[])
        )
        OR jsonb_typeof(p_wire->'security_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'cik') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'sic') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'division') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'source_ref') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_wire->'accession') NOT IN ('null', 'string')
        OR jsonb_typeof(p_wire->'available_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'taxonomy_version') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'taxonomy_hash') IS DISTINCT FROM 'string'
        OR p_wire->>'security_id' !~ '^[0-9a-f][0-9a-f-]{7,63}$'
        OR p_wire->>'cik' !~ '^[0-9]{10}$'
        OR p_wire->>'sic' !~ '^[0-9]{4}$'
        OR p_wire->>'division' NOT IN (
            'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'SECTOR_UNKNOWN'
        )
        OR p_wire->>'available_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'taxonomy_version' <> 'sec-sic-division-v1'
        OR p_wire->>'taxonomy_hash' <> '816dad7c0d8daa45dcb0fef0b18b27552f5f471fbb7ab725328bd9562b1e2136'
        OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire->'source_ref')) <> 3
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire->'source_ref') AS k(key_name)
            WHERE k.key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
        )
        OR jsonb_typeof(p_wire->'source_ref'->'record_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'source_ref'->'family') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'source_ref'->'record_hash') IS DISTINCT FROM 'string'
        OR p_wire->'source_ref'->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
        OR p_wire->'source_ref'->>'family' <> 'SEC_EDGAR'
        OR p_wire->'source_ref'->>'record_hash' !~ '^[0-9a-f]{64}$'
    THEN
        RAISE EXCEPTION 'sector assignment wire shape is outside the P4-C contract'
            USING ERRCODE = '23514';
    END IF;

    v_available_at := (p_wire->>'available_at')::timestamptz;
    v_sic_number := pg_catalog.left(p_wire->>'sic', 2)::integer;
    v_expected_division := CASE
        WHEN v_sic_number BETWEEN 1 AND 9 THEN 'A'
        WHEN v_sic_number BETWEEN 10 AND 14 THEN 'B'
        WHEN v_sic_number BETWEEN 15 AND 17 THEN 'C'
        WHEN v_sic_number BETWEEN 20 AND 39 THEN 'D'
        WHEN v_sic_number BETWEEN 40 AND 49 THEN 'E'
        WHEN v_sic_number BETWEEN 50 AND 51 THEN 'F'
        WHEN v_sic_number BETWEEN 52 AND 59 THEN 'G'
        WHEN v_sic_number BETWEEN 60 AND 67 THEN 'H'
        WHEN v_sic_number BETWEEN 70 AND 89 THEN 'I'
        WHEN v_sic_number BETWEEN 91 AND 97 THEN 'J'
        ELSE 'SECTOR_UNKNOWN'
    END;
    IF p_wire->>'division' <> v_expected_division THEN
        RAISE EXCEPTION 'sector assignment division does not match the approved SIC taxonomy'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.p4_source_records AS source_record
        WHERE source_record.record_id = p_wire->'source_ref'->>'record_id'
          AND source_record.record_hash = p_wire->'source_ref'->>'record_hash'
          AND source_record.family = 'SEC_EDGAR'
          AND COALESCE(
                (source_record.wire->>'available_at')::timestamptz,
                source_record.retrieved_at
              ) <= v_available_at
          AND source_record.wire->'payload'->>'cik_padded' = p_wire->>'cik'
          AND source_record.wire->'payload'->>'sic' = p_wire->>'sic'
          AND (
                p_wire->>'accession' IS NULL
                OR source_record.wire->'payload'->>'accession_number' = p_wire->>'accession'
          )
    )
    THEN
        RAISE EXCEPTION 'sector assignment source lineage is not present or not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.security_identities AS identity_record
        WHERE identity_record.security_id = p_wire->>'security_id'
          AND identity_record.status = 'active'
          AND identity_record.valid_from <= v_available_at
          AND (
                identity_record.valid_to IS NULL
                OR identity_record.valid_to > v_available_at
          )
          AND identity_record.available_at <= v_available_at
          AND identity_record.wire->>'cik' = p_wire->>'cik'
    )
    THEN
        RAISE EXCEPTION 'sector assignment CIK is not bound to an active security identity'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4c.sector-assignment.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(public.p3d_canonical_json(p_wire::json), 'UTF8'),
                'sha256'
            ),
            'hex'
        ) <> p_assignment_hash
    THEN
        RAISE EXCEPTION 'sector assignment hash does not match the canonical wire'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('p4c.sector-assignment:' || (p_wire->>'security_id'))
    );
    SELECT assignment.wire INTO v_existing_wire
    FROM public.sector_assignments AS assignment
    WHERE assignment.assignment_hash = p_assignment_hash;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire THEN
            RAISE EXCEPTION 'sector assignment hash collision carries different wire'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.sector_assignments AS assignment
        WHERE assignment.security_id = p_wire->>'security_id'
          AND assignment.available_at = v_available_at
          AND assignment.assignment_hash <> p_assignment_hash
    )
    THEN
        RAISE EXCEPTION 'sector assignment conflicts at the same security and availability time'
            USING ERRCODE = '23505';
    END IF;

    INSERT INTO public.sector_assignments (
        assignment_hash, security_id, cik, sic, division, source_record_id,
        source_record_hash, source_family, accession, available_at,
        taxonomy_version, taxonomy_hash, wire
    ) VALUES (
        p_assignment_hash,
        p_wire->>'security_id',
        p_wire->>'cik',
        p_wire->>'sic',
        p_wire->>'division',
        p_wire->'source_ref'->>'record_id',
        p_wire->'source_ref'->>'record_hash',
        'SEC_EDGAR',
        p_wire->>'accession',
        v_available_at,
        p_wire->>'taxonomy_version',
        p_wire->>'taxonomy_hash',
        p_wire
    );
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
    IF (p_wire->>'known_at')::timestamptz > (p_wire->>'as_of')::timestamptz THEN
        RAISE EXCEPTION 'candidate set timestamps are not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.universe_snapshots AS universe
        WHERE universe.universe_hash = p_wire->>'universe_hash'
          AND universe.as_of = (p_wire->>'as_of')::timestamptz::date
          AND universe.known_at <= (p_wire->>'known_at')::timestamptz
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

CREATE OR REPLACE FUNCTION public.append_cluster_result(
    p_cluster_id TEXT,
    p_wire JSONB
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_existing_wire JSONB;
    v_member TEXT;
    v_ordinal INTEGER := 1;
BEGIN
    IF p_cluster_id !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'cluster id must be a SHA-256 digest'
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
        RAISE EXCEPTION 'cluster result canonical wire exceeds the 1048576-byte limit'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(p_wire)) <> 7
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_object_keys(p_wire) AS key_name
            WHERE key_name <> ALL (ARRAY[
                'cluster_id', 'as_of', 'policy_hash', 'manifest_hash', 'members', 'status',
                'source_refs'
            ]::text[])
        )
        OR jsonb_typeof(p_wire->'cluster_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'as_of') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'policy_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'manifest_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'members') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'status') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_wire->'source_refs') IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_wire->'members') > 256
        OR jsonb_array_length(p_wire->'source_refs') > 1024
        OR p_wire->>'cluster_id' <> p_cluster_id
        OR p_wire->>'cluster_id' !~ '^[0-9a-f]{64}$'
        OR p_wire->>'as_of' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        OR p_wire->>'policy_hash' !~ '^[0-9a-f]{64}$'
        OR p_wire->>'manifest_hash' <> '34aa2e2e2056cb21495ed398ab2d816ee90b9fd257c632a878466989ef3cfa0e'
        OR p_wire->>'status' NOT IN ('ASSIGNED', 'UNKNOWN')
        OR jsonb_array_length(p_wire->'members') = 0
        OR p_wire->>'status' = 'UNKNOWN' AND jsonb_array_length(p_wire->'members') <> 1
        OR p_wire->>'status' = 'ASSIGNED' AND jsonb_array_length(p_wire->'source_refs') = 0
    THEN
        RAISE EXCEPTION 'cluster result wire shape is outside the P4-C contract'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
        WHERE jsonb_typeof(ref.value) IS DISTINCT FROM 'object'
            OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(ref.value)) <> 3
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_object_keys(ref.value) AS key_name
                WHERE key_name <> ALL (ARRAY['record_id', 'family', 'record_hash']::text[])
            )
            OR jsonb_typeof(ref.value->'record_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(ref.value->'family') IS DISTINCT FROM 'string'
            OR jsonb_typeof(ref.value->'record_hash') IS DISTINCT FROM 'string'
            OR ref.value->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
            OR ref.value->>'family' <> 'ALPACA_HISTORICAL_BARS'
            OR ref.value->>'record_hash' !~ '^[0-9a-f]{64}$'
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
            GROUP BY ref.value->>'record_id'
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(ref.value ORDER BY ref.ordinality)
            FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs')
                WITH ORDINALITY AS ref(value, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(ref.value ORDER BY
                ref.value->>'family', ref.value->>'record_id', ref.value->>'record_hash')
            FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
        )
    THEN
        RAISE EXCEPTION 'cluster source references are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'members') AS item(member)
        WHERE item.member !~ '^[0-9a-f][0-9a-f-]{7,63}$'
    )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'members') AS item(member)
            GROUP BY item.member
            HAVING count(*) > 1
        )
        OR (
            SELECT pg_catalog.array_agg(item.member ORDER BY item.ordinality)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'members')
                WITH ORDINALITY AS item(member, ordinality)
        ) IS DISTINCT FROM (
            SELECT pg_catalog.array_agg(item.member ORDER BY item.member)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'members') AS item(member)
        )
    THEN
        RAISE EXCEPTION 'cluster members are invalid or unordered'
            USING ERRCODE = '23514';
    END IF;
    IF pg_catalog.encode(
            public.digest(
                pg_catalog.convert_to('seven-lens.p4c.cluster-id.v1', 'UTF8')
                || pg_catalog.decode('00', 'hex')
                || pg_catalog.convert_to(
                    public.p3d_canonical_json(
                        json_build_object(
                            'policy_hash', p_wire->>'policy_hash',
                            'as_of', p_wire->>'as_of',
                            'members', (p_wire->'members')::json
                        )
                    ),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        ) <> p_cluster_id
    THEN
        RAISE EXCEPTION 'cluster id does not match the canonical cluster content'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.p4_source_records AS source_record
            WHERE source_record.record_id = ref.value->>'record_id'
              AND source_record.record_hash = ref.value->>'record_hash'
              AND source_record.family = 'ALPACA_HISTORICAL_BARS'
              AND COALESCE(
                    (source_record.wire->>'available_at')::timestamptz,
                    source_record.retrieved_at
                  ) <= (p_wire->>'as_of')::timestamptz
        )
    )
    THEN
        RAISE EXCEPTION 'cluster source lineage is not point-in-time visible'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
        JOIN public.p4_source_records AS source_record
          ON source_record.record_id = ref.value->>'record_id'
         AND source_record.record_hash = ref.value->>'record_hash'
         AND source_record.family = 'ALPACA_HISTORICAL_BARS'
        WHERE NOT EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'members') AS member(security_id)
            JOIN public.security_identities AS identity_record
              ON identity_record.security_id = member.security_id
            WHERE identity_record.status = 'active'
              AND identity_record.available_at <= (p_wire->>'as_of')::timestamptz
              AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
              AND (
                    identity_record.valid_to IS NULL
                    OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
              )
              AND identity_record.wire->>'symbol' = source_record.wire->'payload'->>'symbol'
        )
    )
    THEN
        RAISE EXCEPTION 'cluster source lineage is not bound to a cluster member'
            USING ERRCODE = '23514';
    END IF;
    -- ``source_refs`` is a union of member histories in the P4-C wire, so a
    -- single symbol match is insufficient: every member must contribute at
    -- least one visible bar source, and a source symbol must resolve to one
    -- (not zero or several) active member identities at the cluster cutoff.
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'members') AS member(security_id)
        WHERE jsonb_array_length(p_wire->'source_refs') > 0
          AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
            JOIN public.p4_source_records AS source_record
              ON source_record.record_id = ref.value->>'record_id'
             AND source_record.record_hash = ref.value->>'record_hash'
             AND source_record.family = 'ALPACA_HISTORICAL_BARS'
            JOIN public.security_identities AS identity_record
              ON identity_record.security_id = member.security_id
             AND identity_record.status = 'active'
             AND identity_record.available_at <= (p_wire->>'as_of')::timestamptz
             AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
             AND (
                    identity_record.valid_to IS NULL
                    OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
                 )
             AND identity_record.symbol = source_record.wire->'payload'->>'symbol'
        )
    )
    THEN
        RAISE EXCEPTION 'cluster source lineage does not cover every member'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
        JOIN public.p4_source_records AS source_record
          ON source_record.record_id = ref.value->>'record_id'
         AND source_record.record_hash = ref.value->>'record_hash'
         AND source_record.family = 'ALPACA_HISTORICAL_BARS'
        WHERE (
            SELECT count(*)
            FROM pg_catalog.jsonb_array_elements_text(p_wire->'members') AS member(security_id)
            JOIN public.security_identities AS identity_record
              ON identity_record.security_id = member.security_id
             AND identity_record.status = 'active'
             AND identity_record.available_at <= (p_wire->>'as_of')::timestamptz
             AND identity_record.valid_from <= (p_wire->>'as_of')::timestamptz
             AND (
                    identity_record.valid_to IS NULL
                    OR identity_record.valid_to > (p_wire->>'as_of')::timestamptz
                 )
             AND identity_record.symbol = source_record.wire->'payload'->>'symbol'
        ) <> 1
    )
    THEN
        RAISE EXCEPTION 'cluster source symbol is not uniquely bound to one member'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext('p4c.cluster-result:' || p_cluster_id)
    );
    SELECT c.wire INTO v_existing_wire
    FROM public.cluster_results AS c
    WHERE c.cluster_id = p_cluster_id
    LIMIT 1;
    IF FOUND THEN
        IF v_existing_wire IS DISTINCT FROM p_wire
            OR (SELECT count(*) FROM public.cluster_results AS c
                WHERE c.cluster_id = p_cluster_id)
                <> jsonb_array_length(p_wire->'members')
            OR EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements_text(p_wire->'members')
                    WITH ORDINALITY AS item(member, ordinality)
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM public.cluster_results AS c
                    WHERE c.cluster_id = p_cluster_id
                      AND c.ordinal = item.ordinality
                      AND c.security_id = item.member
                )
            )
        THEN
            RAISE EXCEPTION 'cluster id collision or incomplete member rows'
                USING ERRCODE = '23514';
        END IF;
        RETURN 'IDEMPOTENT_DUPLICATE';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements_text(p_wire->'members') AS item(member)
        JOIN public.cluster_results AS existing
          ON existing.as_of = (p_wire->>'as_of')::timestamptz
         AND existing.security_id = item.member
         AND existing.cluster_id <> p_cluster_id
    )
    THEN
        RAISE EXCEPTION 'security already has a different cluster for this as-of'
            USING ERRCODE = '23505';
    END IF;

    FOR v_member IN SELECT jsonb_array_elements_text(p_wire->'members')
    LOOP
        INSERT INTO public.cluster_results (
            cluster_id, as_of, security_id, status, policy_hash, manifest_hash,
            ordinal, wire
        ) VALUES (
            p_cluster_id,
            (p_wire->>'as_of')::timestamptz,
            v_member,
            p_wire->>'status',
            p_wire->>'policy_hash',
            p_wire->>'manifest_hash',
            v_ordinal,
            p_wire
        );
        v_ordinal := v_ordinal + 1;
    END LOOP;
    RETURN 'APPENDED';
END;
$$;

-- ***************************************************************
-- Append-only mutation guards on every P4-C table.
-- ***************************************************************
CREATE TRIGGER market_snapshots_guard_write
    BEFORE UPDATE OR DELETE ON public.market_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER market_snapshots_guard_truncate
    BEFORE TRUNCATE ON public.market_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER universe_snapshots_guard_write
    BEFORE UPDATE OR DELETE ON public.universe_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER universe_snapshots_guard_truncate
    BEFORE TRUNCATE ON public.universe_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER universe_snapshot_entries_guard_write
    BEFORE UPDATE OR DELETE ON public.universe_snapshot_entries
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER universe_snapshot_entries_guard_truncate
    BEFORE TRUNCATE ON public.universe_snapshot_entries
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER feature_vectors_guard_write
    BEFORE UPDATE OR DELETE ON public.feature_vectors
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER feature_vectors_guard_truncate
    BEFORE TRUNCATE ON public.feature_vectors
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER sector_assignments_guard_write
    BEFORE UPDATE OR DELETE ON public.sector_assignments
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER sector_assignments_guard_truncate
    BEFORE TRUNCATE ON public.sector_assignments
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER candidate_sets_guard_write
    BEFORE UPDATE OR DELETE ON public.candidate_sets
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER candidate_sets_guard_truncate
    BEFORE TRUNCATE ON public.candidate_sets
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER candidate_set_entries_guard_write
    BEFORE UPDATE OR DELETE ON public.candidate_set_entries
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER candidate_set_entries_guard_truncate
    BEFORE TRUNCATE ON public.candidate_set_entries
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE TRIGGER cluster_results_guard_write
    BEFORE UPDATE OR DELETE ON public.cluster_results
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();
CREATE TRIGGER cluster_results_guard_truncate
    BEFORE TRUNCATE ON public.cluster_results
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_append_only_mutation();

-- ***************************************************************
-- Revoke all write authority from PUBLIC.
-- ***************************************************************
REVOKE ALL ON TABLE public.market_snapshots, public.universe_snapshots,
    public.universe_snapshot_entries, public.feature_vectors,
    public.sector_assignments, public.candidate_sets, public.candidate_set_entries,
    public.cluster_results
    FROM PUBLIC;

REVOKE ALL ON FUNCTION public.append_market_snapshot(TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_universe_snapshot(TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_feature_vector(TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_sector_assignment(TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_candidate_set(TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_cluster_result(TEXT, JSONB) FROM PUBLIC;
