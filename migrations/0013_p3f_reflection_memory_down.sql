DROP FUNCTION IF EXISTS public.current_memory_pointer_artifact();
DROP FUNCTION IF EXISTS public.current_memory_artifact(TIMESTAMPTZ);
DROP FUNCTION IF EXISTS public.promote_memory_artifact(TEXT, TEXT, TIMESTAMPTZ);
DROP FUNCTION IF EXISTS public.validate_memory_artifact(
    TEXT, TEXT, TEXT, TEXT
);
DROP FUNCTION IF EXISTS public.register_memory_candidate(
    TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT, TEXT, BYTEA,
    INTEGER, INTEGER, INTEGER, TEXT, TEXT, TEXT, TEXT[]
);
DROP FUNCTION IF EXISTS public.register_memory_curation_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    INTEGER, INTEGER, TEXT, TEXT, TEXT, INTEGER, INTEGER, INTEGER, TEXT
);
DROP FUNCTION IF EXISTS public.register_reflection_record(
    TEXT, TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TIMESTAMPTZ, TIMESTAMPTZ,
    TEXT, TEXT, TEXT, TEXT, TEXT, BYTEA, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT[], TEXT[], TEXT[], TIMESTAMPTZ[], TEXT, TEXT
);
DROP FUNCTION IF EXISTS public.p3f_text_is_safe(TEXT, INTEGER);
DROP FUNCTION IF EXISTS public.p3f_instruction_text_is_safe(TEXT);
DROP FUNCTION IF EXISTS public.p3f_fact_text_is_closed(TEXT, TEXT[], JSON, TEXT[]);
DROP VIEW IF EXISTS public.approved_reflection_sources;
DROP VIEW IF EXISTS public.approved_reflection_records;
DROP TABLE IF EXISTS public.memory_curation_audits;
DROP TABLE IF EXISTS public.memory_current_pointer;
DROP TABLE IF EXISTS public.memory_promotion_history;
DROP TABLE IF EXISTS public.memory_artifact_state_events;
DROP TABLE IF EXISTS public.memory_artifact_sources;
DROP TABLE IF EXISTS public.memory_artifacts;
DROP TABLE IF EXISTS public.reflection_corrections;
DROP TABLE IF EXISTS public.reflection_sources;
DROP TABLE IF EXISTS public.reflection_records;
DELETE FROM public.schema_migrations WHERE version = 13;
