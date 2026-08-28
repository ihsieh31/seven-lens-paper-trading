-- 0021 up: close the P4-B direct-authority and source-lineage gaps.
--
-- 0020 deliberately established the tables and SECURITY DEFINER seams.  This
-- migration keeps those public signatures stable while adding the missing
-- semantic checks.  It is contiguous and reversible; 0020 remains immutable.

-- A runtime session may append discovery/block/review rows through the stable
-- event seam, but a CONFIRMED row is a security-sensitive result of the
-- application confirmation evaluator.  The trigger below binds that final
-- state transition to the owner session that owns the authoritative table;
-- a runtime caller cannot manufacture the same result by replaying legal
-- lower-level transitions.

ALTER TABLE public.p4_source_records
    ADD CONSTRAINT p4_source_records_wire_contract CHECK (
        jsonb_typeof(wire) = 'object'
        AND wire ?& ARRAY[
            'record_id', 'family', 'endpoint_id', 'schema_version', 'content_hash',
            'retrieved_at', 'role', 'coverage', 'rights', 'producer_version',
            'payload', 'material_claim', 'observation_at', 'published_at',
            'available_at', 'effective_at', 'vintage', 'supersedes_content_hash',
            'coverage_warning'
        ]::text[]
        AND (wire - ARRAY[
            'record_id', 'family', 'endpoint_id', 'schema_version', 'content_hash',
            'retrieved_at', 'role', 'coverage', 'rights', 'producer_version',
            'payload', 'material_claim', 'observation_at', 'published_at',
            'available_at', 'effective_at', 'vintage', 'supersedes_content_hash',
            'coverage_warning'
        ]::text[]) = '{}'::jsonb
        AND jsonb_typeof(wire->'record_id') = 'string'
        AND jsonb_typeof(wire->'family') = 'string'
        AND jsonb_typeof(wire->'endpoint_id') = 'string'
        AND jsonb_typeof(wire->'schema_version') = 'string'
        AND jsonb_typeof(wire->'content_hash') = 'string'
        AND jsonb_typeof(wire->'retrieved_at') = 'string'
        AND jsonb_typeof(wire->'role') = 'string'
        AND jsonb_typeof(wire->'coverage') = 'string'
        AND jsonb_typeof(wire->'rights') = 'string'
        AND jsonb_typeof(wire->'producer_version') = 'string'
        AND jsonb_typeof(wire->'material_claim') = 'boolean'
        AND jsonb_typeof(wire->'observation_at') IN ('string', 'null')
        AND jsonb_typeof(wire->'published_at') IN ('string', 'null')
        AND jsonb_typeof(wire->'available_at') IN ('string', 'null')
        AND jsonb_typeof(wire->'effective_at') IN ('string', 'null')
        AND jsonb_typeof(wire->'vintage') IN ('array', 'null')
        AND jsonb_typeof(wire->'supersedes_content_hash') IN ('string', 'null')
        AND jsonb_typeof(wire->'coverage_warning') IN ('string', 'null')
        AND wire->>'record_id' = record_id
        AND wire->>'family' = family
        AND wire->>'content_hash' = content_hash
        AND wire->>'producer_version' = 'p4a.adapters.v1'
        AND wire->>'schema_version' = '1.0.0'
        AND (wire->>'retrieved_at')::timestamptz = retrieved_at
        AND (
            (
                family IN (
                    'ALPACA_ASSETS', 'ALPACA_HISTORICAL_BARS', 'SEC_EDGAR',
                    'EXCHANGE_OFFICIAL', 'FRED_ALFRED', 'TREASURY', 'BLS',
                    'BEA', 'EIA'
                )
                AND wire->>'role' = 'AUTHORITY'
                AND wire->>'coverage' = 'FULL'
                AND wire->>'rights' = 'ALLOWED'
            )
            OR (
                family = 'ALPACA_IEX_QUOTES'
                AND wire->>'role' = 'AUTHORITY'
                AND wire->>'coverage' = 'LIMITED_MARKET_COVERAGE'
                AND wire->>'rights' = 'ALLOWED'
            )
            OR (
                family IN ('ALPACA_CORPORATE_ACTIONS', 'ISSUER_IR')
                AND wire->>'role' = 'CONFIRMATION'
                AND wire->>'coverage' = 'FULL'
                AND wire->>'rights' = 'ALLOWED'
            )
            OR (
                family IN ('TAVILY', 'GDELT')
                AND wire->>'role' = 'DISCOVERY'
                AND wire->>'coverage' = 'FULL'
                AND wire->>'rights' = 'ALLOWED'
                AND wire->>'material_claim' = 'false'
            )
            OR (
                family = 'YFINANCE'
                AND wire->>'role' = 'RESEARCH_SUPPLEMENT'
                AND wire->>'coverage' = 'FULL'
                AND wire->>'rights' = 'UNKNOWN'
                AND wire->>'material_claim' = 'false'
            )
        )
        AND (
            (family = 'ALPACA_ASSETS' AND wire->>'endpoint_id' IN ('assets_list', 'asset_detail'))
            OR (family = 'ALPACA_HISTORICAL_BARS' AND wire->>'endpoint_id' = 'stock_bars')
            OR (family = 'ALPACA_IEX_QUOTES' AND wire->>'endpoint_id' = 'latest_quote')
            OR (family = 'ALPACA_CORPORATE_ACTIONS' AND wire->>'endpoint_id' = 'corporate_actions')
            OR (family = 'SEC_EDGAR' AND wire->>'endpoint_id' IN ('submissions', 'companyfacts'))
            OR (family = 'ISSUER_IR' AND wire->>'endpoint_id' = 'issuer_press')
            OR (family = 'EXCHANGE_OFFICIAL' AND wire->>'endpoint_id' = 'exchange_notice')
            OR (family = 'FRED_ALFRED' AND wire->>'endpoint_id' IN ('fred_observations', 'alfred_observations'))
            OR (family = 'TREASURY' AND wire->>'endpoint_id' = 'fiscal_dataset')
            OR (family = 'BLS' AND wire->>'endpoint_id' = 'bls_series')
            OR (family = 'BEA' AND wire->>'endpoint_id' = 'bea_data')
            OR (family = 'EIA' AND wire->>'endpoint_id' = 'eia_route')
            OR (family = 'TAVILY' AND wire->>'endpoint_id' = 'tavily_search')
            OR (family = 'GDELT' AND wire->>'endpoint_id' = 'gdelt_doc')
            OR (family = 'YFINANCE' AND wire->>'endpoint_id' = 'yahoo_chart')
        )
        AND (
            (family = 'ALPACA_IEX_QUOTES' AND wire->>'coverage_warning' IS NOT NULL)
            OR (family <> 'ALPACA_IEX_QUOTES' AND wire->>'coverage_warning' IS NULL)
        )
        AND (
            wire->>'observation_at' IS NULL
            OR (wire->>'observation_at')::timestamptz <= retrieved_at
        )
        AND (
            wire->>'published_at' IS NULL
            OR (wire->>'published_at')::timestamptz <= retrieved_at
        )
        AND (
            wire->>'available_at' IS NULL
            OR (wire->>'available_at')::timestamptz <= retrieved_at
        )
        AND (
            wire->>'effective_at' IS NULL
            OR (wire->>'effective_at')::timestamptz <= retrieved_at
        )
        AND (
            wire->'vintage' = 'null'::jsonb
            OR (
                jsonb_array_length(wire->'vintage') = 2
                AND jsonb_typeof(wire->'vintage'->0) = 'string'
                AND jsonb_typeof(wire->'vintage'->1) = 'string'
                AND (wire->'vintage'->>0)::date <= (wire->'vintage'->>1)::date
            )
        )
        AND (
            wire->>'supersedes_content_hash' IS NULL
            OR wire->>'supersedes_content_hash' ~ '^[0-9a-f]{64}$'
        )
    );

ALTER TABLE public.p4_source_records
    ADD CONSTRAINT p4_source_records_payload_contract CHECK (
        jsonb_typeof(wire->'payload') = 'object'
        AND (
            (
                family = 'ALPACA_ASSETS'
                AND wire->'payload' ?& ARRAY[
                    'id', 'symbol', 'exchange', 'asset_class', 'status', 'tradable'
                ]::text[]
                AND ((wire->'payload') - ARRAY[
                    'id', 'symbol', 'exchange', 'asset_class', 'status', 'tradable'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'ALPACA_HISTORICAL_BARS'
                AND wire->'payload' ?& ARRAY['symbol', 'feed', 'bars', 'next_page_token']::text[]
                AND ((wire->'payload') - ARRAY[
                    'symbol', 'feed', 'bars', 'next_page_token'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'ALPACA_IEX_QUOTES'
                AND wire->'payload' ?& ARRAY[
                    'symbol', 'bid_price', 'ask_price', 'timestamp', 'feed'
                ]::text[]
                AND ((wire->'payload') - ARRAY[
                    'symbol', 'bid_price', 'ask_price', 'timestamp', 'feed',
                    'bid_size', 'ask_size'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'ALPACA_CORPORATE_ACTIONS'
                AND wire->'payload' ?& ARRAY[
                    'type', 'split_type', 'cusip', 'symbol', 'ex_date', 'record_date',
                    'payment_date', 'ratio', 'supported', 'complete', 'detection_only'
                ]::text[]
                AND ((wire->'payload') - ARRAY[
                    'type', 'split_type', 'cusip', 'symbol', 'ex_date', 'record_date',
                    'payment_date', 'ratio', 'supported', 'complete', 'detection_only'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'SEC_EDGAR'
                AND (
                    (
                        wire->>'endpoint_id' = 'submissions'
                        AND (
                            (
                                wire->'payload' ?& ARRAY['cik_padded', 'sic']::text[]
                                AND ((wire->'payload') - ARRAY[
                                    'cik_padded', 'sic'
                                ]::text[]) = '{}'::jsonb
                            )
                            OR (
                                wire->'payload' ?& ARRAY[
                                    'cik_padded', 'accession_number', 'form',
                                    'primary_document', 'filing_date'
                                ]::text[]
                                AND ((wire->'payload') - ARRAY[
                                    'cik_padded', 'accession_number', 'form',
                                    'primary_document', 'filing_date'
                                ]::text[]) = '{}'::jsonb
                            )
                        )
                    )
                    OR (
                        wire->>'endpoint_id' = 'companyfacts'
                        AND (
                            wire->'payload'->>'taxonomy', wire->'payload'->>'concept'
                        ) IN (
                            ('us-gaap', 'NetIncomeLoss'),
                            ('us-gaap', 'NetCashProvidedByUsedInOperatingActivities'),
                            ('us-gaap', 'Assets'),
                            ('us-gaap', 'PaymentsToAcquirePropertyPlantAndEquipment'),
                            ('dei', 'EntityCommonStockSharesOutstanding')
                        )
                        AND (
                            (
                                wire->'payload'->>'concept'
                                    = 'PaymentsToAcquirePropertyPlantAndEquipment'
                                AND wire->'payload' ?& ARRAY[
                                    'cik_padded', 'taxonomy', 'concept', 'unit', 'value', 'start',
                                    'end', 'fiscal_year', 'fiscal_period', 'form', 'accession',
                                    'filed', 'sign_convention'
                                ]::text[]
                                AND wire->'payload'->>'sign_convention'
                                    = 'provider_value_preserved_no_abs'
                                AND ((wire->'payload') - ARRAY[
                                    'cik_padded', 'taxonomy', 'concept', 'unit', 'value', 'start',
                                    'end', 'fiscal_year', 'fiscal_period', 'form', 'accession',
                                    'filed', 'sign_convention'
                                ]::text[]) = '{}'::jsonb
                            )
                            OR (
                                wire->'payload'->>'concept'
                                    <> 'PaymentsToAcquirePropertyPlantAndEquipment'
                                AND wire->'payload' ?& ARRAY[
                                    'cik_padded', 'taxonomy', 'concept', 'unit', 'value', 'start',
                                    'end', 'fiscal_year', 'fiscal_period', 'form', 'accession', 'filed'
                                ]::text[]
                                AND ((wire->'payload') - ARRAY[
                                    'cik_padded', 'taxonomy', 'concept', 'unit', 'value', 'start',
                                    'end', 'fiscal_year', 'fiscal_period', 'form', 'accession', 'filed'
                                ]::text[]) = '{}'::jsonb
                            )
                        )
                    )
                )
            )
            OR (
                family = 'ISSUER_IR'
                AND wire->'payload' ?& ARRAY['issuer_id', 'title', 'url']::text[]
                AND ((wire->'payload') - ARRAY['issuer_id', 'title', 'url']::text[]) = '{}'::jsonb
            )
            OR (
                family = 'EXCHANGE_OFFICIAL'
                AND wire->'payload' ?& ARRAY['exchange', 'title', 'url']::text[]
                AND ((wire->'payload') - ARRAY['exchange', 'title', 'url']::text[]) = '{}'::jsonb
            )
            OR (
                family = 'FRED_ALFRED'
                AND wire->'payload' ?& ARRAY[
                    'series_id', 'date', 'value', 'realtime_start', 'realtime_end'
                ]::text[]
                AND ((wire->'payload') - ARRAY[
                    'series_id', 'date', 'value', 'realtime_start', 'realtime_end'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'TREASURY'
                AND wire->'payload' ?& ARRAY['dataset', 'record_date']::text[]
                AND ((wire->'payload') - ARRAY[
                    'dataset', 'record_date', 'security_type', 'avg_interest_rate'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'BLS'
                AND wire->'payload' ?& ARRAY['series_id', 'year', 'period', 'value']::text[]
                AND ((wire->'payload') - ARRAY[
                    'series_id', 'year', 'period', 'value'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'BEA'
                AND wire->'payload' ?& ARRAY[
                    'dataset', 'table_name', 'year', 'period', 'value', 'series_code'
                ]::text[]
                AND ((wire->'payload') - ARRAY[
                    'dataset', 'table_name', 'year', 'period', 'value', 'series_code'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'EIA'
                AND wire->'payload' ?& ARRAY['route', 'period', 'value']::text[]
                AND ((wire->'payload') - ARRAY[
                    'route', 'period', 'value', 'units', 'unit', 'duoarea', 'area-name',
                    'product', 'product-name', 'process', 'process-name', 'seriesId',
                    'seriesDescription', 'stateId', 'stateDescription', 'fuelId', 'fuelName',
                    'sectorId', 'sectorName'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'TAVILY'
                AND wire->'payload' ?& ARRAY['query', 'title', 'url', 'snippet', 'score']::text[]
                AND ((wire->'payload') - ARRAY[
                    'query', 'title', 'url', 'snippet', 'score'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'GDELT'
                AND wire->'payload' ?& ARRAY['query', 'url', 'title', 'domain', 'seendate']::text[]
                AND ((wire->'payload') - ARRAY[
                    'query', 'url', 'title', 'domain', 'seendate'
                ]::text[]) = '{}'::jsonb
            )
            OR (
                family = 'YFINANCE'
                AND wire->'payload' ?& ARRAY[
                    'symbol', 'regular_market_price', 'regular_market_time',
                    'exchange_name', 'currency', 'supplement_only'
                ]::text[]
                AND ((wire->'payload') - ARRAY[
                    'symbol', 'regular_market_price', 'regular_market_time',
                    'exchange_name', 'currency', 'supplement_only'
                ]::text[]) = '{}'::jsonb
            )
        )
    );

ALTER TABLE public.security_quarantine_decisions
    ADD CONSTRAINT security_quarantine_master_version_contract CHECK (
        master_version ~ '^p4b[.]securities[.]v1:[0-9a-f]{64}$'
    );

CREATE FUNCTION public.guard_confirmed_corporate_action_event()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_table_owner TEXT;
BEGIN
    IF NEW.state = 'confirmed' THEN
        SELECT pg_catalog.pg_get_userbyid(c.relowner)
          INTO v_table_owner
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relname = 'corporate_action_events';
        IF v_table_owner IS NULL OR session_user IS DISTINCT FROM v_table_owner THEN
            RAISE EXCEPTION
                'confirmed corporate-action transitions require the authority-owner session'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.guard_confirmed_corporate_action_event() FROM PUBLIC;

CREATE TRIGGER corporate_action_confirmed_owner_guard
BEFORE INSERT ON public.corporate_action_events
FOR EACH ROW
EXECUTE FUNCTION public.guard_confirmed_corporate_action_event();

ALTER TABLE public.security_quarantine_decisions
    ADD CONSTRAINT security_quarantine_wire_contract CHECK (
        jsonb_typeof(wire) = 'object'
        AND wire ?& ARRAY[
            'security_id', 'symbol_as_of', 'master_version', 'decision_at', 'outcome',
            'reasons', 'event_ids', 'source_refs', 'producer_version'
        ]::text[]
        AND (wire - ARRAY[
            'security_id', 'symbol_as_of', 'master_version', 'decision_at', 'outcome',
            'reasons', 'event_ids', 'source_refs', 'producer_version'
        ]::text[]) = '{}'::jsonb
        AND jsonb_typeof(wire->'security_id') = 'string'
        AND jsonb_typeof(wire->'symbol_as_of') = 'string'
        AND jsonb_typeof(wire->'master_version') = 'string'
        AND jsonb_typeof(wire->'decision_at') = 'string'
        AND jsonb_typeof(wire->'outcome') = 'string'
        AND jsonb_typeof(wire->'reasons') = 'array'
        AND jsonb_typeof(wire->'event_ids') = 'array'
        AND jsonb_typeof(wire->'source_refs') = 'array'
        AND jsonb_typeof(wire->'producer_version') = 'string'
        AND wire->>'security_id' = security_id
        AND wire->>'symbol_as_of' = symbol_as_of
        AND wire->>'master_version' = master_version
        AND (wire->>'decision_at')::timestamptz = decision_at
        AND wire->>'outcome' = outcome
        AND (
            (outcome = 'ELIGIBLE'
                AND jsonb_array_length(wire->'reasons') = 0
                AND jsonb_array_length(wire->'event_ids') = 0
                AND jsonb_array_length(wire->'source_refs') > 0)
            OR (outcome <> 'ELIGIBLE' AND jsonb_array_length(wire->'reasons') > 0)
        )
    );

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
    v_incoming_available TIMESTAMPTZ;
    v_current_available TIMESTAMPTZ;
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
    IF p_wire->>'retrieved_at' IS NULL THEN
        RAISE EXCEPTION 'source record wire carries no retrieved_at'
            USING ERRCODE = '22023';
    END IF;
    v_incoming_available := COALESCE(
        (p_wire->>'available_at')::timestamptz,
        (p_wire->>'retrieved_at')::timestamptz
    );

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
                'same provider identity with different content requires explicit supersession'
                USING ERRCODE = '23514';
        END IF;
        v_current_available := COALESCE(
            (v_current.wire->>'available_at')::timestamptz,
            (v_current.wire->>'retrieved_at')::timestamptz
        );
        IF v_incoming_available < v_current_available THEN
            RAISE EXCEPTION 'source supersession availability cannot move backwards'
                USING ERRCODE = '23514';
        END IF;
        IF v_incoming_available = v_current_available
            AND p_content_hash <> v_current.content_hash
        THEN
            RAISE EXCEPTION 'source supersession at equal availability is unorderable'
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
    v_event_id TEXT;
    v_existing_wire JSONB;
    v_canonical_refs JSONB;
    v_canonical_events JSONB;
    v_master_identity_hash TEXT;
    v_decision_at TIMESTAMPTZ;
BEGIN
    IF p_decision_hash IS NULL OR p_decision_hash !~ '^[0-9a-f]{64}$' THEN
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
    IF jsonb_typeof(p_wire->'security_id') IS DISTINCT FROM 'string'
        OR v_security_id IS NULL
        OR v_security_id !~ '^[0-9a-f][0-9a-f-]{7,63}$'
    THEN
        RAISE EXCEPTION 'wire form carries no canonical security id'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire->'symbol_as_of') IS DISTINCT FROM 'string'
        OR p_wire->>'symbol_as_of' IS NULL
        OR p_wire->>'symbol_as_of' !~ '^[A-Z][A-Z0-9.-]{0,9}$'
    THEN
        RAISE EXCEPTION 'wire form carries no canonical symbol'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire->'master_version') IS DISTINCT FROM 'string'
        OR p_wire->>'master_version' !~ '^p4b[.]securities[.]v1:[0-9a-f]{64}$'
    THEN
        RAISE EXCEPTION 'wire form carries no canonical master version'
            USING ERRCODE = '22023';
    END IF;
    v_master_identity_hash := pg_catalog.split_part(p_wire->>'master_version', ':', 2);
    IF jsonb_typeof(p_wire->'decision_at') IS DISTINCT FROM 'string'
        OR p_wire->>'decision_at' IS NULL
    THEN
        RAISE EXCEPTION 'wire form carries no decision_at'
            USING ERRCODE = '22023';
    END IF;
    v_decision_at := (p_wire->>'decision_at')::timestamptz;
    IF p_wire->>'outcome' NOT IN ('ELIGIBLE', 'ENTRY_BLOCKED', 'REVIEW_REQUIRED') THEN
        RAISE EXCEPTION 'wire form carries no closed quarantine outcome'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(p_wire->'reasons') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'event_ids') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_wire->'source_refs') IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_wire->'source_refs') > 256
    THEN
        RAISE EXCEPTION 'wire form must carry canonical reason, event, and source arrays'
            USING ERRCODE = '22023';
    END IF;
    IF p_wire->>'outcome' = 'ELIGIBLE'
        AND (
            jsonb_array_length(p_wire->'reasons') <> 0
            OR jsonb_array_length(p_wire->'event_ids') <> 0
            OR jsonb_array_length(p_wire->'source_refs') = 0
        )
    THEN
        RAISE EXCEPTION 'eligible quarantine decisions require source evidence and no findings'
            USING ERRCODE = '23514';
    END IF;
    IF p_wire->>'outcome' <> 'ELIGIBLE'
        AND jsonb_array_length(p_wire->'reasons') = 0
    THEN
        RAISE EXCEPTION 'non-eligible quarantine decisions require a closed reason'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'reasons') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
            OR item.value #>> '{}' NOT IN (
                'UNKNOWN_SECURITY', 'AMBIGUOUS_IDENTITY', 'SYMBOL_AS_OF_MISMATCH',
                'IDENTITY_INTERVAL_CONFLICT', 'SOURCE_NOT_YET_AVAILABLE',
                'STALE_SECURITY_MASTER', 'SPLIT_DETECTED', 'FORMAL_CONFIRMATION_MISSING',
                'SPLIT_RATIO_CONFLICT', 'SPLIT_DATE_CONFLICT', 'SPLIT_IDENTITY_CONFLICT',
                'SOURCE_WITHDRAWN_OR_CORRECTED', 'UNSUPPORTED_CORPORATE_ACTION',
                'EFFECTIVE_OR_LATE_EVENT_REVIEW', 'SPLIT_TYPE_CONFLICT'
            )
    ) THEN
        RAISE EXCEPTION 'quarantine reasons are not closed canonical codes'
            USING ERRCODE = '22023';
    END IF;
    IF (
        SELECT count(*) FROM pg_catalog.jsonb_array_elements(p_wire->'reasons') AS item(value)
    ) <> (
        SELECT count(DISTINCT item.value #>> '{}')
        FROM pg_catalog.jsonb_array_elements(p_wire->'reasons') AS item(value)
    ) THEN
        RAISE EXCEPTION 'quarantine reasons must be unique'
            USING ERRCODE = '22023';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_wire->'event_ids') AS item(value)
        WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'string'
            OR item.value #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
    ) THEN
        RAISE EXCEPTION 'quarantine event ids are not canonical'
            USING ERRCODE = '22023';
    END IF;
    SELECT pg_catalog.jsonb_agg(item.value ORDER BY item.value #>> '{}')
      INTO v_canonical_events
      FROM pg_catalog.jsonb_array_elements(p_wire->'event_ids') AS item(value);
    IF COALESCE(v_canonical_events, '[]'::jsonb) IS DISTINCT FROM p_wire->'event_ids' THEN
        RAISE EXCEPTION 'quarantine event ids must be unique and sorted'
            USING ERRCODE = '22023';
    END IF;

    FOR v_ref IN
        SELECT item.value
        FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS item(value)
    LOOP
        IF jsonb_typeof(v_ref) IS DISTINCT FROM 'object'
            OR (v_ref - ARRAY['record_id', 'record_hash', 'family']::text[]) <> '{}'::jsonb
            OR NOT (v_ref ?& ARRAY['record_id', 'record_hash', 'family']::text[])
            OR v_ref->>'record_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$'
            OR v_ref->>'record_hash' !~ '^[0-9a-f]{64}$'
            OR v_ref->>'family' NOT IN (
                'ALPACA_ASSETS', 'ALPACA_HISTORICAL_BARS', 'ALPACA_IEX_QUOTES',
                'ALPACA_CORPORATE_ACTIONS', 'SEC_EDGAR', 'ISSUER_IR',
                'EXCHANGE_OFFICIAL', 'FRED_ALFRED', 'TREASURY', 'BLS', 'BEA',
                'EIA', 'TAVILY', 'GDELT', 'YFINANCE'
            )
        THEN
            RAISE EXCEPTION 'quarantine source refs are not canonical'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;
    IF (
        SELECT count(*) FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS item(value)
    ) <> (
        SELECT count(DISTINCT item.value->>'record_id')
        FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS item(value)
    ) THEN
        RAISE EXCEPTION 'quarantine source refs must use unique record identifiers'
            USING ERRCODE = '22023';
    END IF;
    SELECT pg_catalog.jsonb_agg(
               item.value
               ORDER BY item.value->>'record_id', item.value->>'family', item.value->>'record_hash'
           )
      INTO v_canonical_refs
      FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS item(value);
    IF COALESCE(v_canonical_refs, '[]'::jsonb) IS DISTINCT FROM p_wire->'source_refs' THEN
        RAISE EXCEPTION 'quarantine source refs must be unique and sorted'
            USING ERRCODE = '22023';
    END IF;

    IF p_wire->>'outcome' = 'ELIGIBLE'
        AND NOT EXISTS (
            SELECT 1
            FROM public.security_identities AS identity_row
            WHERE identity_row.identity_hash = v_master_identity_hash
              AND identity_row.security_id = v_security_id
              AND identity_row.symbol = p_wire->>'symbol_as_of'
              AND identity_row.available_at <= v_decision_at
              AND v_decision_at >= identity_row.valid_from
              AND (
                  identity_row.valid_to IS NULL
                  OR v_decision_at < identity_row.valid_to
              )
              AND EXISTS (
                  SELECT 1
                  FROM public.security_identity_sources AS identity_source
                  JOIN pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS ref(value)
                    ON ref.value->>'record_id' = identity_source.record_id
                   AND ref.value->>'record_hash' = identity_source.record_hash
                   AND ref.value->>'family' = identity_source.family
                  WHERE identity_source.identity_hash = identity_row.identity_hash
              )
        )
    THEN
        RAISE EXCEPTION
            'eligible quarantine decision requires an available matching identity and source closure'
            USING ERRCODE = '23514';
    END IF;

    -- P4-B has no EXITED state: every corporate-action observation that was
    -- available at the decision cutoff remains a quarantine finding until the
    -- application evaluator records an allowed confirmation transition.  This
    -- check is deliberately independent of p_wire.event_ids because ELIGIBLE
    -- requires that array to be empty; otherwise the public SECURITY DEFINER
    -- seam could mint a safe decision while the event authority is blocked.
    IF p_wire->>'outcome' = 'ELIGIBLE'
        AND EXISTS (
            SELECT 1
            FROM public.corporate_action_events AS event_row
            WHERE event_row.security_id = v_security_id
              AND event_row.available_at <= v_decision_at
        )
    THEN
        RAISE EXCEPTION
            'eligible quarantine decision cannot bypass a visible corporate-action event'
            USING ERRCODE = '23514';
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
        v_decision_at,
        p_wire->>'outcome',
        ARRAY(SELECT jsonb_array_elements_text(p_wire->'reasons')),
        ARRAY(SELECT jsonb_array_elements_text(p_wire->'event_ids')),
        p_wire
    );
    FOR v_ref IN SELECT item.value
                 FROM pg_catalog.jsonb_array_elements(p_wire->'source_refs') AS item(value)
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
