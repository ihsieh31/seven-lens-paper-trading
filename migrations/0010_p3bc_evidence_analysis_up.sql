-- 0010: P3-B+C point-in-time evidence metadata and analysis-stage authority.

CREATE TABLE public.source_objects (
    content_hash TEXT PRIMARY KEY CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    byte_size INTEGER NOT NULL CHECK (byte_size > 0 AND byte_size <= 4000000),
    state TEXT NOT NULL CHECK (state IN ('STAGED', 'AVAILABLE')),
    staged_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    available_at TIMESTAMPTZ,
    CHECK ((state = 'STAGED' AND available_at IS NULL)
        OR (state = 'AVAILABLE' AND available_at IS NOT NULL))
);

CREATE TABLE public.evidence_packets (
    packet_id UUID PRIMARY KEY,
    packet_hash TEXT NOT NULL UNIQUE CHECK (packet_hash ~ '^[0-9a-f]{64}$'),
    as_of TIMESTAMPTZ NOT NULL,
    universe_hash TEXT NOT NULL CHECK (universe_hash ~ '^[0-9a-f]{64}$'),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    producer_version TEXT NOT NULL CHECK (length(producer_version) BETWEEN 1 AND 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (as_of <= created_at)
);

CREATE TABLE public.source_records (
    source_id TEXT PRIMARY KEY CHECK (length(source_id) BETWEEN 1 AND 96),
    canonical_url TEXT NOT NULL CHECK (length(canonical_url) BETWEEN 9 AND 2048),
    publisher TEXT NOT NULL CHECK (length(publisher) BETWEEN 1 AND 256),
    source_family TEXT NOT NULL CHECK (source_family IN (
        'SEC', 'ISSUER', 'EXCHANGE', 'MARKET_VENDOR',
        'NEWS_PUBLISHER', 'PUBLIC_WEB', 'SEARCH'
    )),
    source_kind TEXT NOT NULL CHECK (source_kind IN (
        'FILING', 'ISSUER_RELEASE', 'EXCHANGE_NOTICE', 'ARTICLE',
        'MARKET_DATA', 'SEARCH_RESULT'
    )),
    available_at TIMESTAMPTZ NOT NULL,
    content_hash TEXT NOT NULL REFERENCES public.source_objects(content_hash),
    primary_source BOOLEAN NOT NULL,
    tombstone BOOLEAN NOT NULL DEFAULT FALSE,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (available_at <= recorded_at)
);

CREATE TABLE public.analysis_runs (
    run_id UUID PRIMARY KEY,
    input_id UUID NOT NULL UNIQUE,
    packet_hash TEXT NOT NULL REFERENCES public.evidence_packets(packet_hash),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    current_stage TEXT NOT NULL DEFAULT 'PLANNED' CHECK (current_stage IN (
        'PLANNED', 'ANALYSTS', 'DEBATE', 'RESEARCH', 'TRADER',
        'COMPLETE', 'INVALID', 'EXPIRED'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.analysis_stage_results (
    run_id UUID NOT NULL REFERENCES public.analysis_runs(run_id),
    stage TEXT NOT NULL CHECK (stage IN (
        'ANALYSTS', 'DEBATE', 'RESEARCH', 'TRADER', 'COMPLETE', 'INVALID', 'EXPIRED'
    )),
    result_hash TEXT NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
    payload TEXT NOT NULL CHECK (length(payload) BETWEEN 1 AND 262144),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt BETWEEN 1 AND 8),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (run_id, stage),
    UNIQUE (run_id, stage, result_hash)
);

CREATE OR REPLACE FUNCTION public.register_source_object(
    p_content_hash TEXT, p_byte_size INTEGER
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE existing_size INTEGER;
BEGIN
    INSERT INTO public.source_objects (content_hash, byte_size, state)
    VALUES (p_content_hash, p_byte_size, 'STAGED')
    ON CONFLICT (content_hash) DO NOTHING;
    SELECT byte_size INTO existing_size FROM public.source_objects
      WHERE content_hash = p_content_hash FOR UPDATE;
    IF existing_size IS DISTINCT FROM p_byte_size THEN
        RAISE EXCEPTION 'source object identity collision' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.publish_source_object(p_content_hash TEXT)
RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    UPDATE public.source_objects
    SET state = 'AVAILABLE', available_at = statement_timestamp()
    WHERE content_hash = p_content_hash AND state = 'STAGED';
    IF NOT FOUND AND NOT EXISTS (
        SELECT 1 FROM public.source_objects
        WHERE content_hash = p_content_hash AND state = 'AVAILABLE'
    ) THEN
        RAISE EXCEPTION 'source object is not staged' USING ERRCODE = '55000';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_analysis_run(
    p_run_id UUID, p_input_id UUID, p_packet_hash TEXT, p_snapshot_hash TEXT
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    existing public.analysis_runs%ROWTYPE;
    packet_snapshot_hash TEXT;
BEGIN
    SELECT snapshot_hash INTO packet_snapshot_hash
    FROM public.evidence_packets
    WHERE packet_hash = p_packet_hash
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'evidence packet is unavailable' USING ERRCODE = '23503';
    END IF;
    IF packet_snapshot_hash IS DISTINCT FROM p_snapshot_hash THEN
        RAISE EXCEPTION 'analysis snapshot does not match evidence packet'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.analysis_runs (run_id, input_id, packet_hash, snapshot_hash)
    VALUES (p_run_id, p_input_id, p_packet_hash, p_snapshot_hash)
    ON CONFLICT (run_id) DO NOTHING;
    SELECT * INTO existing FROM public.analysis_runs WHERE run_id = p_run_id;
    IF existing.input_id IS DISTINCT FROM p_input_id
       OR existing.packet_hash IS DISTINCT FROM p_packet_hash
       OR existing.snapshot_hash IS DISTINCT FROM p_snapshot_hash THEN
        RAISE EXCEPTION 'analysis run identity collision' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.register_evidence_packet(
    p_packet_id UUID, p_packet_hash TEXT, p_as_of TIMESTAMPTZ,
    p_universe_hash TEXT, p_snapshot_hash TEXT, p_producer_version TEXT
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE existing public.evidence_packets%ROWTYPE;
BEGIN
    INSERT INTO public.evidence_packets (
        packet_id, packet_hash, as_of, universe_hash, snapshot_hash, producer_version
    ) VALUES (
        p_packet_id, p_packet_hash, p_as_of, p_universe_hash, p_snapshot_hash,
        p_producer_version
    ) ON CONFLICT (packet_id) DO NOTHING;
    SELECT * INTO existing FROM public.evidence_packets WHERE packet_id = p_packet_id;
    IF existing.packet_hash IS DISTINCT FROM p_packet_hash
       OR existing.as_of IS DISTINCT FROM p_as_of
       OR existing.universe_hash IS DISTINCT FROM p_universe_hash
       OR existing.snapshot_hash IS DISTINCT FROM p_snapshot_hash
       OR existing.producer_version IS DISTINCT FROM p_producer_version THEN
        RAISE EXCEPTION 'evidence packet identity collision' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.register_source_record(
    p_source_id TEXT, p_canonical_url TEXT, p_publisher TEXT,
    p_source_family TEXT, p_source_kind TEXT, p_available_at TIMESTAMPTZ,
    p_content_hash TEXT, p_primary_source BOOLEAN, p_tombstone BOOLEAN
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE existing public.source_records%ROWTYPE;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.source_objects
        WHERE content_hash = p_content_hash AND state = 'AVAILABLE'
    ) THEN
        RAISE EXCEPTION 'source object is not available' USING ERRCODE = '55000';
    END IF;
    INSERT INTO public.source_records (
        source_id, canonical_url, publisher, source_family, source_kind,
        available_at, content_hash, primary_source, tombstone
    ) VALUES (
        p_source_id, p_canonical_url, p_publisher, p_source_family, p_source_kind,
        p_available_at, p_content_hash, p_primary_source, p_tombstone
    ) ON CONFLICT (source_id) DO NOTHING;
    SELECT * INTO existing FROM public.source_records WHERE source_id = p_source_id;
    IF existing.canonical_url IS DISTINCT FROM p_canonical_url
       OR existing.publisher IS DISTINCT FROM p_publisher
       OR existing.source_family IS DISTINCT FROM p_source_family
       OR existing.source_kind IS DISTINCT FROM p_source_kind
       OR existing.available_at IS DISTINCT FROM p_available_at
       OR existing.content_hash IS DISTINCT FROM p_content_hash
       OR existing.primary_source IS DISTINCT FROM p_primary_source
       OR existing.tombstone IS DISTINCT FROM p_tombstone THEN
        RAISE EXCEPTION 'source record identity collision' USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.advance_analysis_stage(
    p_run_id UUID, p_expected TEXT, p_stage TEXT, p_result_hash TEXT, p_payload TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE existing_hash TEXT; inserted_count INTEGER;
BEGIN
    IF (p_expected, p_stage) NOT IN (
        ('PLANNED', 'ANALYSTS'), ('ANALYSTS', 'DEBATE'), ('DEBATE', 'RESEARCH'),
        ('RESEARCH', 'TRADER'), ('TRADER', 'COMPLETE'),
        ('PLANNED', 'INVALID'), ('ANALYSTS', 'INVALID'), ('DEBATE', 'INVALID'),
        ('RESEARCH', 'INVALID'), ('TRADER', 'INVALID'),
        ('PLANNED', 'EXPIRED'), ('ANALYSTS', 'EXPIRED'), ('DEBATE', 'EXPIRED'),
        ('RESEARCH', 'EXPIRED'), ('TRADER', 'EXPIRED')
    ) THEN
        RAISE EXCEPTION 'analysis stage transition is not legal' USING ERRCODE = '55000';
    END IF;
    PERFORM 1 FROM public.analysis_runs WHERE run_id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'analysis run does not exist' USING ERRCODE = '23503';
    END IF;
    SELECT result_hash INTO existing_hash FROM public.analysis_stage_results
      WHERE run_id = p_run_id AND stage = p_stage;
    IF existing_hash IS NOT NULL THEN
        IF existing_hash <> p_result_hash THEN
            RAISE EXCEPTION 'analysis stage immutable result changed' USING ERRCODE = '23514';
        END IF;
        UPDATE public.analysis_stage_results SET attempt = attempt + 1
          WHERE run_id = p_run_id AND stage = p_stage;
        RETURN FALSE;
    END IF;
    UPDATE public.analysis_runs SET current_stage = p_stage, updated_at = statement_timestamp()
      WHERE run_id = p_run_id AND current_stage = p_expected;
    GET DIAGNOSTICS inserted_count = ROW_COUNT;
    IF inserted_count <> 1 THEN
        RAISE EXCEPTION 'analysis stage transition is out of order' USING ERRCODE = '55000';
    END IF;
    INSERT INTO public.analysis_stage_results (run_id, stage, result_hash, payload)
      VALUES (p_run_id, p_stage, p_result_hash, p_payload);
    RETURN TRUE;
END;
$$;

REVOKE ALL ON TABLE public.source_objects, public.source_records, public.evidence_packets,
    public.analysis_runs, public.analysis_stage_results FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_source_object(TEXT, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.publish_source_object(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_source_record(TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, BOOLEAN, BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_evidence_packet(UUID, TEXT, TIMESTAMPTZ, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.create_analysis_run(UUID, UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.advance_analysis_stage(UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
