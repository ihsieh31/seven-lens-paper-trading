DROP FUNCTION IF EXISTS public.advance_analysis_stage(UUID, TEXT, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.create_analysis_run(UUID, UUID, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.register_evidence_packet(UUID, TEXT, TIMESTAMPTZ, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.publish_source_object(TEXT);
DROP FUNCTION IF EXISTS public.register_source_object(TEXT, INTEGER);
DROP TABLE IF EXISTS public.analysis_stage_results;
DROP TABLE IF EXISTS public.analysis_runs;
DROP TABLE IF EXISTS public.evidence_packets;
DROP TABLE IF EXISTS public.source_records;
DROP TABLE IF EXISTS public.source_objects;

DELETE FROM public.schema_migrations WHERE version = 10;
